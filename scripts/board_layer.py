#!/usr/bin/env python
"""Build a national visual-board layer: hazard grid cell x 시군구 pieces with per-period values.

Layers are defined in ``assets/libraries/visual_board.json`` (river flood, tropical cyclone): the
CLIMADA Data API hazard for KOR (150 arcsec grid), read from CLIMADA's local download cache only
(offline), cut by the SGIS 시군구 boundaries. Each piece keeps its cell's value; nothing is
averaged (docs/VISUAL_BOARD_KO.md section 2).

Every return-period layer carries a mandatory sub check (``meta.check``): the empirical step rule
(``board.rp_intensity_check``) against the main CLIMADA method, diagnostic only, with a warning
when more than ``rp_check.warn_share`` of the centroids disagree. A change layer is the
future / base ratio of two built layers of the same grid (``change_layers`` in the config).

Runs in the CLIMADA worker environment::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    export CLIMATERISK_CLIMADA_DATA_DIR=~/Data/climaterisk/external/climada
    ./.climada-env/bin/python scripts/board_layer.py --fetch --all   # online, sequential
    ./.climada-env/bin/python scripts/board_layer.py --all           # offline build of everything
    ./.climada-env/bin/python scripts/board_layer.py [--layer river_flood_hist_kor]
    ./.climada-env/bin/python scripts/board_layer.py --change river_flood_change_rcp85_kor

Outputs under ``<DATA_ROOT>/derived/board/``: ``<layer>.geojson`` (columns ``centroid``,
``code``, ``name``, ``area_km2``, ``rp<T>`` per kept return period in the hazard's units)
``<layer>.board.json`` (compact file the API serves; coordinates rounded to 4 decimals, about 10 m)
and ``<layer>.meta.json`` (source, resolution, periods kept, max_supported, record_years,
n_pieces, null vs 0 counts). A null value means no hazard data; 0 is a computed zero.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

_M_PER_DEG_LAT = 111_320.0
log = logging.getLogger("board_layer")


def hazard_properties(spec: dict[str, Any]) -> dict[str, str]:
    """CLIMADA Data API properties of a layer (same names as ``physical.py`` requests).

    River flood: ``climate_scenario`` and ``year_range``. Tropical cyclone: ``event_type``,
    ``model_name``, ``climate_scenario`` and, for a future set, ``ref_year`` (the historical
    set has none; the config's ``historical`` is the API's ``None``). Country coverage for
    both. ``nb_synth_tracks`` of the config is not sent: the current Data API versions (v2.1)
    carry no such property and a request with it matches the stale v2 sets; every KOR set here
    is the ``10synth_tracks`` one by name.
    """
    props: dict[str, str] = {"climate_scenario": str(spec["climate_scenario"])}
    if spec["peril"] == "tropical_cyclone":
        if props["climate_scenario"] == "historical":
            props["climate_scenario"] = "None"  # the Data API names the present TC set "None"
        props["event_type"] = str(spec.get("event_type", "synthetic"))
        props["model_name"] = str(spec.get("model_name", "random_walk"))
        if spec.get("ref_year") is not None:
            props["ref_year"] = str(spec["ref_year"])
    else:
        props["year_range"] = str(spec["year_range"])
    props["spatial_coverage"] = "country"
    props["country_iso3alpha"] = str(spec["country_iso3"])
    return props


def _period_label(spec: dict[str, Any]) -> str:
    """``year_range`` or ``ref_year`` of a layer spec, for the source text."""
    if spec.get("ref_year") is not None:
        return f"ref_year {spec['ref_year']}"
    return str(spec["year_range"])


def period_column(period: float) -> str:
    """GeoJSON column name of a return period: ``rp2``, ``rp2.5``, ``rp100``."""
    return f"rp{period:g}"


COORD_DECIMALS = 4  # 1e-4 degree ~ 10 m: far below the 4.6 km hazard cell


def _ring(coords: Any) -> list[list[float]]:
    """One ring as ``[[lon, lat], ...]`` rounded to ``COORD_DECIMALS``."""
    return [
        [round(float(x), COORD_DECIMALS), round(float(y), COORD_DECIMALS)] for x, y, *_ in coords
    ]


def polygons_of(geom: Any) -> list[list[list[list[float]]]]:
    """Shapely (Multi)Polygon -> ``[[exterior, hole, ...], ...]`` with rounded coordinates."""
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out = []
    for part in parts:
        if getattr(part, "is_empty", False) or not hasattr(part, "exterior"):
            continue
        out.append([_ring(part.exterior.coords)] + [_ring(r.coords) for r in part.interiors])
    return out


def period_key(period: float) -> str:
    """Key of a return period in ``values`` (``"100"`` for 100.0, ``"2.5"``); the store
    parses the request ``rp`` as float and formats it the same way."""
    return f"{period:g}"


def board_json_payload(pieces: Any, meta: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Compact ``<layer>.board.json`` content (pure given a pieces GeoDataFrame).

    Args:
        pieces: GeoDataFrame with ``centroid``, ``code``, ``name``, geometry and one
            ``rp<T>`` column per kept period (NaN = no data -> null).
        meta: Output of ``build_metadata``.
        spec: The layer's block of ``visual_board.json`` (``use_tier``, ``use_tier_reason``).

    Returns:
        ``{"meta", "pieces"}`` as documented in ``tests/test_board_api.py``.
    """
    periods = [float(p) for p in meta["periods_kept"]]
    keep = (
        "layer", "units", "requested_periods", "periods_kept", "max_supported", "record_years",
        "resolution_arcsec", "cell_km_ns", "cell_km_ew", "hazard_source", "boundary_source",
        "boundary_simplify_m",
    )  # fmt: skip
    out_meta = {k: meta[k] for k in keep}
    out_meta["peril"] = spec["peril"]
    out_meta["quantity"] = spec["quantity"]
    out_meta["use_tier"] = spec["use_tier"]
    out_meta["use_tier_reason"] = spec["use_tier_reason"]
    if spec.get("label"):
        out_meta["label"] = spec["label"]
    if "check" in meta:
        out_meta["check"] = meta["check"]
    rows = []
    for i, (geom, cen, code, name) in enumerate(
        zip(pieces.geometry, pieces["centroid"], pieces["code"], pieces["name"], strict=True)
    ):
        values: dict[str, float | None] = {}
        for p in periods:
            v = float(pieces[period_column(p)].iloc[i])
            values[period_key(p)] = None if math.isnan(v) else v
        rows.append(
            {
                "id": i,
                "centroid": int(cen),
                "code": str(code),
                "name": str(name),
                "polygons": polygons_of(geom),
                "values": values,
            }
        )
    return {"meta": out_meta, "pieces": rows}


def build_metadata(
    *,
    layer: str,
    spec: dict[str, Any],
    result: dict[str, Any],
    res_deg: float,
    mean_lat: float,
    n_centroids: int,
    n_pieces: int,
    n_regions: int,
    n_regions_with_pieces: int,
    units: str,
    requested: list[float],
) -> dict[str, Any]:
    """Assemble the layer's metadata record (pure; no I/O).

    Args:
        layer: Layer name.
        spec: The layer's block of ``visual_board.json``.
        result: Output of ``board.rp_intensity``.
        res_deg: Grid cell side in degrees.
        mean_lat: Mean centroid latitude (degrees) for the km conversion.
        n_centroids: Number of hazard centroids.
        n_pieces: Number of pieces written.
        n_regions: Regions in the boundary file.
        n_regions_with_pieces: Regions that received at least one piece.
        units: Hazard intensity units.
        requested: Return periods requested from the config.

    Returns:
        JSON-serialisable dict. ``null_pieces``/``zero_pieces``/``positive_pieces`` are
        per kept period (keyed by period), counted over pieces; the cell size in km is
        ``res_deg`` times 111.32 km (N-S) and times that times cos(lat) (E-W).
    """
    import numpy as np

    values = np.asarray(result["intensity"], dtype=float)
    counts: dict[str, dict[str, int]] = {"null": {}, "zero": {}, "positive": {}}
    for p, row in zip(result["periods"], result.get("piece_values", values), strict=True):
        key = f"{p:g}"
        counts["null"][key] = int(np.isnan(row).sum())
        counts["zero"][key] = int((row == 0).sum())
        counts["positive"][key] = int((row > 0).sum())
    km_ns = res_deg * _M_PER_DEG_LAT / 1000.0
    return {
        "layer": layer,
        "built_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "hazard_source": (
            f"CLIMADA Data API {spec['peril']} {spec['climate_scenario']} "
            f"{_period_label(spec)} {spec['country_iso3']} (local download cache, offline)"
        ),
        "boundary_source": f"SGIS 시군구 {spec['boundary_version']}",
        "units": units,
        "resolution_deg": res_deg,
        "resolution_arcsec": res_deg * 3600.0,
        "cell_km_ns": km_ns,
        "cell_km_ew": km_ns * math.cos(math.radians(mean_lat)),
        "requested_periods": [float(p) for p in requested],
        "periods_kept": [float(p) for p in result["periods"]],
        "max_supported": result["max_supported"],
        "record_years": result["record_years"],
        "n_centroids": n_centroids,
        "n_pieces": n_pieces,
        "n_regions": n_regions,
        "n_regions_with_pieces": n_regions_with_pieces,
        "value_counts_over_pieces": counts,
        "columns": [period_column(p) for p in result["periods"]],
    }


def run(layer: str, out_dir: Path | None) -> dict[str, Any]:
    """Build one layer and write its GeoJSON and metadata; return the metadata."""
    import climaterisk_worker  # noqa: F401  (must precede climada: project-scoped conf)
    import geopandas as gpd
    import numpy as np
    from climaterisk_worker import board
    from climaterisk_worker._dataapi import make_client, resilient_get_hazard

    from climaterisk import paths

    # Offline by construction: read CLIMADA's download cache, never the network.
    os.environ["CLIMATERISK_OFFLINE"] = "1"
    cfg = board.load_board_config()
    spec = cfg["layers"][layer]
    requested = [float(p) for p in cfg["return_periods_years"]]

    t0 = time.time()
    hazard = resilient_get_hazard(
        make_client(),
        spec["peril"],
        properties=hazard_properties(spec),
    )
    lon = np.asarray(hazard.centroids.lon, dtype=float)
    lat = np.asarray(hazard.centroids.lat, dtype=float)
    meta_t = hazard.centroids.get_meta()["transform"]
    res_deg = float(abs(meta_t.a))

    result = board.rp_intensity(hazard, requested)
    check = board.rp_intensity_check(hazard, requested)
    rp_cfg = cfg["rp_check"]
    shares = board.rp_disagreement(result, check, float(rp_cfg["rel_tol"]))
    check_block = {
        "method": "empirical_step",
        "rel_tol": float(rp_cfg["rel_tol"]),
        "warn_share": float(rp_cfg["warn_share"]),
        "disagree_share_by_period": shares,
        "warning": any(v > float(rp_cfg["warn_share"]) for v in shares.values()),
    }
    if check_block["warning"]:
        log.warning(
            "%s: return-period sub check disagrees (> %.0f%% of centroids, tol %.0f%%): %s",
            layer,
            100 * check_block["warn_share"],
            100 * check_block["rel_tol"],
            {k: round(v, 3) for k, v in shares.items()},
        )
    del check  # diagnostic only; never written as a second result

    shp = paths.municipality_sgis_dir() / f"bnd_sigungu_00_{spec['boundary_version']}.shp"
    regions = gpd.read_file(shp, encoding="utf-8")
    simplify_m = float(spec.get("boundary_simplify_m", 0.0))
    if simplify_m > 0:
        # Display-size reduction only: each 시군구 outline is simplified on its own in metres
        # (EPSG:5179), so neighbouring outlines may leave gaps/overlaps up to ~simplify_m.
        # Hazard values are per 4.6 km cell and do not change; only which 시군구 label a
        # point within ~simplify_m of a boundary gets can change. Recorded in the metadata.
        regions = regions.to_crs(5179)
        regions["geometry"] = regions.geometry.simplify(simplify_m, preserve_topology=True)
        regions = regions.to_crs(4326)
    cells = board.grid_cells(lon, lat, res_deg)
    pieces = board.clip_pieces(
        cells, regions, spec["boundary_code_column"], spec["boundary_name_column"]
    )

    centroid_ix = pieces["centroid"].to_numpy(dtype=int)
    piece_values = np.asarray(result["intensity"], dtype=float)[:, centroid_ix]
    for p, row in zip(result["periods"], piece_values, strict=True):
        pieces[period_column(p)] = row
    result["piece_values"] = piece_values

    out = out_dir or paths.derived_board_dir()
    out.mkdir(parents=True, exist_ok=True)
    geojson = out / f"{layer}.geojson"
    pieces.to_file(geojson, driver="GeoJSON")
    meta = build_metadata(
        layer=layer,
        spec=spec,
        result=result,
        res_deg=res_deg,
        mean_lat=float(lat.mean()),
        n_centroids=int(lon.size),
        n_pieces=len(pieces),
        n_regions=len(regions),
        n_regions_with_pieces=int(pieces["code"].nunique()),
        units=str(hazard.units),
        requested=requested,
    )
    meta["boundary_simplify_m"] = simplify_m
    meta["check"] = check_block
    meta["runtime_seconds"] = round(time.time() - t0, 1)
    board_file = out / f"{layer}.board.json"
    board_file.write_text(
        json.dumps(
            board_json_payload(pieces, meta, spec), ensure_ascii=False, separators=(",", ":")
        ),
        encoding="utf-8",
    )
    meta["board_json_bytes"] = board_file.stat().st_size
    (out / f"{layer}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def _read_built(layer: str, out: Path) -> dict[str, Any]:
    """A built layer's ``.board.json`` (raises ``FileNotFoundError`` naming the build step)."""
    path = out / f"{layer}.board.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; build layer {layer!r} first")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def change_payload(
    name: str, ch: dict[str, Any], base: dict[str, Any], fut: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """Change layer content from two built layers (pure; no I/O).

    Values are ``future / base`` (``board.change_values``) per period kept by both layers;
    where no ratio exists the value is ``None`` and ``status`` names why (``base_zero``,
    ``both_zero``, ``null``).

    Args:
        name: Change layer name.
        ch: Its block of ``change_layers`` (``base``, ``future``, ``label``, tier fields).
        base: Parsed base ``.board.json``.
        fut: Parsed future ``.board.json``.
        spec: The future layer's block of ``layers`` (peril, boundary fields).

    Returns:
        ``{"meta", "pieces"}``.

    Raises:
        ValueError: The two layers do not have identical pieces (grid, region, geometry).
    """
    import numpy as np
    from climaterisk_worker import board

    bp, fp = base["pieces"], fut["pieces"]
    if len(bp) != len(fp):
        raise ValueError(f"{name}: piece counts differ ({len(bp)} vs {len(fp)})")
    for i, (a, b) in enumerate(zip(bp, fp, strict=True)):
        same = (a["centroid"], a["code"], a["polygons"]) == (
            b["centroid"],
            b["code"],
            b["polygons"],
        )
        if not same:
            raise ValueError(f"{name}: piece {i} differs between base and future (not one grid)")
    bm, fm = base["meta"], fut["meta"]
    base_keys = {period_key(p) for p in bm["periods_kept"]}
    periods = [p for p in fm["periods_kept"] if period_key(p) in base_keys]
    rows: list[dict[str, Any]] = [
        {k: a[k] for k in ("id", "centroid", "code", "name", "polygons")} | {"values": {}}
        for a in bp
    ]
    status_counts: dict[str, dict[str, int]] = {}
    for p in periods:
        key = period_key(p)
        b = np.array([np.nan if a["values"][key] is None else a["values"][key] for a in bp])
        f = np.array([np.nan if a["values"][key] is None else a["values"][key] for a in fp])
        ratio, status = board.change_values(b, f)
        status_counts[key] = {
            s_: int((status == s_).sum()) for s_ in ("", "both_zero", "base_zero", "null")
        }
        for row, r, st in zip(rows, ratio, status, strict=True):
            row["values"][key] = None if np.isnan(r) else float(r)
            if st:
                row.setdefault("status", {})[key] = str(st)
    keep = (
        "requested_periods", "resolution_arcsec", "cell_km_ns", "cell_km_ew", "boundary_source",
        "boundary_simplify_m",
    )  # fmt: skip
    meta = {k: fm[k] for k in keep if k in fm}
    meta.update(
        layer=name,
        units="ratio",
        quantity="change",
        label=ch["label"],
        peril=spec["peril"],
        base_layer=ch["base"],
        future_layer=ch["future"],
        periods_kept=[float(p) for p in periods],
        max_supported=min(bm["max_supported"], fm["max_supported"]),
        record_years=min(bm["record_years"], fm["record_years"]),
        hazard_source=f"ratio {fm['hazard_source']} / {bm['hazard_source']}",
        use_tier=ch["use_tier"],
        use_tier_reason=ch["use_tier_reason"],
        status_counts_by_period=status_counts,
    )
    return {"meta": meta, "pieces": rows}


def run_change(name: str, out_dir: Path | None) -> dict[str, Any]:
    """Build one change layer from the two built layers it names; return its metadata."""
    from climaterisk_worker import board

    from climaterisk import paths

    cfg = board.load_board_config()
    ch = cfg["change_layers"][name]
    out = out_dir or paths.derived_board_dir()
    payload = change_payload(
        name,
        ch,
        _read_built(ch["base"], out),
        _read_built(ch["future"], out),
        cfg["layers"][ch["future"]],
    )
    board_file = out / f"{name}.board.json"
    board_file.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    meta = dict(payload["meta"], n_pieces=len(payload["pieces"]))
    meta["board_json_bytes"] = board_file.stat().st_size
    (out / f"{name}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def fetch(layers: list[str]) -> None:
    """Download the hazards the layers need that CLIMADA's cache lacks (online, sequential).

    Never run two downloads at once (large files, slow link). Each fetched dataset's name and
    size are logged. A dataset that does not exist in the Data API raises; nothing else is
    substituted for it.
    """
    import climaterisk_worker  # noqa: F401  (must precede climada: project-scoped conf)
    from climaterisk_worker import board
    from climaterisk_worker._dataapi import OfflineError, make_client, resilient_get_hazard

    cfg = board.load_board_config()
    for layer in layers:
        spec = cfg["layers"][layer]
        props = hazard_properties(spec)
        os.environ["CLIMATERISK_OFFLINE"] = "1"
        try:
            resilient_get_hazard(make_client(), spec["peril"], properties=props)
            log.info("%s: already in the CLIMADA cache", layer)
            continue
        except OfflineError:
            pass
        finally:
            os.environ.pop("CLIMATERISK_OFFLINE", None)
        client = make_client()
        info = client.get_dataset_info(spec["peril"], properties=props)
        size = sum(int(f.file_size) for f in info.files)
        log.info("%s: fetching dataset %s (%d bytes)", layer, info.name, size)
        resilient_get_hazard(client, spec["peril"], properties=props)
        log.info("%s: fetched %s (%d bytes)", layer, info.name, size)


def main() -> None:
    """CLI entry point."""
    from climaterisk_worker import board

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--layer", default=None, help="one layer name (default river_flood_hist_kor)")
    ap.add_argument("--change", default=None, help="one change layer name")
    ap.add_argument("--all", action="store_true", help="every layer, then every change layer")
    ap.add_argument(
        "--fetch", action="store_true", help="online: download missing hazards only, then exit"
    )
    ap.add_argument("--out", type=Path, default=None, help="default: <DATA_ROOT>/derived/board")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = board.load_board_config()
    layers = list(cfg["layers"]) if args.all else [args.layer or "river_flood_hist_kor"]
    if args.change:
        layers = []
    if args.fetch:
        fetch(layers)
        return
    summary: dict[str, Any] = {}
    for layer in layers:
        summary[layer] = run(layer, args.out)
    changes = list(cfg["change_layers"]) if args.all else ([args.change] if args.change else [])
    for name in changes:
        summary[name] = run_change(name, args.out)
    print(
        json.dumps(
            summary if (args.all or len(summary) > 1) else next(iter(summary.values())),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
