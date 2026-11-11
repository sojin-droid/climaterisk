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
AQUEDUCT_SOURCE = "aqueduct_inuncoast"
log = logging.getLogger("board_layer")


def hazard_properties(spec: dict[str, Any]) -> dict[str, str]:
    """CLIMADA Data API properties of a layer (same names as ``physical.py`` requests).

    Wildfire: country only. River flood: ``climate_scenario`` and ``year_range``. Tropical
    cyclone: ``event_type``,
    ``model_name``, ``climate_scenario`` and, for a future set, ``ref_year`` (the historical
    set has none; the config's ``historical`` is the API's ``None``). Country coverage for
    both. ``nb_synth_tracks`` of the config is not sent: the current Data API versions (v2.1)
    carry no such property and a request with it matches the stale v2 sets; every KOR set here
    is the ``10synth_tracks`` one by name.
    """
    if spec["peril"] == "wildfire":
        # The Data API wildfire set is the one historical 2001-2020 record; it has no scenario
        # property (``physical._run_wildfire`` requests it with the country only).
        return {"spatial_coverage": "country", "country_iso3alpha": str(spec["country_iso3"])}
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


def hazard_source_text(spec: dict[str, Any]) -> str:
    """Where a layer's hazard comes from (metadata / ``.board.json``)."""
    if spec.get("source") == AQUEDUCT_SOURCE:
        return (
            f"{spec['aqueduct']['note']}; one depth map per return period "
            f"(<DATA_ROOT>/cache/aqueduct, offline); no coastal defences represented"
        )
    return (
        f"CLIMADA Data API {spec['peril']} {spec['climate_scenario']} "
        f"{_period_label(spec)} {spec['country_iso3']} (local download cache, offline)"
    )


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


def board_json_payload(
    pieces: Any,
    meta: dict[str, Any],
    spec: dict[str, Any],
    exposure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact ``<layer>.board.json`` content (pure given a pieces GeoDataFrame).

    Args:
        pieces: GeoDataFrame with ``centroid``, ``code``, ``name``, geometry and one
            ``rp<T>`` column per kept period (NaN = no data -> null).
        meta: Output of ``build_metadata``.
        spec: The layer's block of ``visual_board.json`` (``use_tier``, ``use_tier_reason``).
        exposure: Optional ``{name: array(n_pieces)}`` summable amounts (USD), written per
            piece rounded to 2 decimals (the LitPop weighting of step 7).

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
    if spec.get("notes"):
        out_meta["notes"] = spec["notes"]
    if "check" in meta:
        out_meta["check"] = meta["check"]
    if "litpop" in meta:
        out_meta["litpop"] = meta["litpop"]
    rows = []
    for i, (geom, cen, code, name) in enumerate(
        zip(pieces.geometry, pieces["centroid"], pieces["code"], pieces["name"], strict=True)
    ):
        values: dict[str, float | None] = {}
        for p in periods:
            v = float(pieces[period_column(p)].iloc[i])
            values[period_key(p)] = None if math.isnan(v) else v
        row: dict[str, Any] = {
            "id": i,
            "centroid": int(cen),
            "code": str(code),
            "name": str(name),
            "polygons": polygons_of(geom),
            "values": values,
        }
        if exposure:
            row["exposure"] = {k: round(float(a[i]), 2) for k, a in exposure.items()}
        rows.append(row)
    return {"meta": out_meta, "pieces": rows}


def metric_board_payload(
    pieces: Any,
    *,
    layer: str,
    board_cfg: dict[str, Any],
    values: dict[str, Any],
    status: dict[str, Any] | None,
    exposure: dict[str, Any] | None,
    resolution: dict[str, float],
    boundary_source: str,
    boundary_simplify_m: float,
    hazard_source: str | None = None,
    check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``<layer>.board.json`` of a metric layer whose keys are not return periods or years.

    Every key names its palette ``quantity`` (``assets/libraries/board_palettes.json``); the
    key's units are read from that spec (``change``: a ratio). A NaN value is written as null
    (no data), never as 0. Pieces keep their cell's value; nothing is averaged.

    Args:
        pieces: GeoDataFrame ``centroid, code, name, geometry`` (one row per piece).
        layer: Layer name.
        board_cfg: The config's ``board`` block (label, tier, notes, ``keys`` with ``key``,
            ``label``, ``quantity``).
        values: ``{key: array(n_pieces)}`` (NaN = no data).
        status: ``{key: array(n_pieces) of "" / "base_zero" / "both_zero" / "null"}`` for
            change keys, or ``None``.
        exposure: ``{name: array(n_pieces)}`` summable amounts (persons), or ``None``.
        resolution: ``{"deg", "cell_km_ns", "cell_km_ew"}``.
        boundary_source: Boundary text for the source line.
        boundary_simplify_m: Outline simplification in metres.
        hazard_source: Override for ``board_cfg["hazard_source"]``.
        check: Sub-check record to carry in the meta (diagnostic only).

    Returns:
        ``{"meta", "pieces"}``.
    """
    import numpy as np
    from climaterisk_worker import board

    spec = json.loads((board.board_config_path().parent / "board_palettes.json").read_text("utf-8"))
    keys: list[dict[str, Any]] = []
    for k in board_cfg["keys"]:
        q = k["quantity"]
        if q == "change":
            units, scale = "배(미래 ÷ 기준)", "ratio"
        else:
            units, scale = spec["quantities"][q]["units"], "level"
        keys.append({"key": k["key"], "label": k["label"], "units": units, "scale": scale,
                     "quantity": q})  # fmt: skip
    meta: dict[str, Any] = {
        "layer": layer,
        "peril": board_cfg["peril"],
        "key_kind": "metric",
        "default_key": board_cfg["default_key"],
        "keys": keys,
        "units": keys[0]["units"],
        "label": board_cfg["label"],
        "resolution_arcsec": round(resolution["deg"] * 3600.0, 1),
        "cell_km_ns": resolution["cell_km_ns"],
        "cell_km_ew": resolution["cell_km_ew"],
        "hazard_source": hazard_source or board_cfg["hazard_source"],
        "boundary_source": boundary_source,
        "boundary_simplify_m": boundary_simplify_m,
        "use_tier": board_cfg["use_tier"],
        "use_tier_reason": board_cfg["use_tier_reason"],
        "notes": board_cfg["notes"],
    }
    if check is not None:
        meta["check"] = check
    arrays = {k: np.asarray(v, dtype=float) for k, v in values.items()}
    exp = {k: np.asarray(v, dtype=float) for k, v in (exposure or {}).items()}
    rows = []
    for i, (geom, cen, code, name) in enumerate(
        zip(pieces.geometry, pieces["centroid"], pieces["code"], pieces["name"], strict=True)
    ):
        vals = {
            k: None if math.isnan(float(a[i])) else round(float(a[i]), 4) for k, a in arrays.items()
        }
        row: dict[str, Any] = {
            "id": i,
            "centroid": int(cen),
            "code": str(code),
            "name": str(name),
            "polygons": polygons_of(geom),
            "values": vals,
        }
        if status:
            st = {k: str(a[i]) for k, a in status.items() if str(a[i])}
            if st:
                row["status"] = st
        if exp:
            row["exposure"] = {k: round(float(a[i]), 2) for k, a in exp.items()}
        rows.append(row)
    return {"meta": meta, "pieces": rows}


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
        "hazard_source": hazard_source_text(spec),
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


def aqueduct_cache_file(spec: dict[str, Any]) -> Path:
    """Cached Aqueduct stack of a layer (same name rule as the Models-tab adapter)."""
    from climaterisk import paths

    box = "_".join(f"{float(v):g}" for v in spec["aqueduct"]["box"])
    return paths.cache_dir("aqueduct", f"{spec['aqueduct']['dataset']}__{box}.npz")


def load_aqueduct(layer: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Hazard of an Aqueduct coastal layer on the union grid of every coastal layer.

    Every layer with ``source == "aqueduct_inuncoast"`` is read from its cache so they share
    one wet-cell set (hence identical pieces, as ``change_payload`` requires); a cell outside
    this layer's own box is null. Offline: a missing cache raises, nothing is downloaded.

    Returns:
        ``hazard``, ``lon``, ``lat``, ``res_deg``, ``rps`` (maps' periods), ``depth`` (this
        layer's raw maps, NaN outside its box) and ``null_centroid`` (bool per centroid).
    """
    import numpy as np
    from climaterisk_worker import board

    stacks: dict[str, Any] = {}
    for name, sp in cfg["layers"].items():
        if sp.get("source") != AQUEDUCT_SOURCE:
            continue
        cache = aqueduct_cache_file(sp)
        if not cache.exists():
            raise FileNotFoundError(f"{cache} missing; Aqueduct maps are read from cache only")
        with np.load(cache) as z:
            stacks[name] = {k: z[k] for k in z.files}
    union = board.aqueduct_union(stacks)
    depth = union["depth"][layer]
    log.info(
        "%s: aqueduct union grid %d wet cells, %d periods %s",
        layer, depth.shape[1], len(union["rps"]), union["rps"],
    )  # fmt: skip
    return {
        "hazard": board.aqueduct_hazard(union["lat"], union["lon"], union["rps"], depth),
        "lon": union["lon"],
        "lat": union["lat"],
        "res_deg": union["res_deg"],
        "rps": union["rps"],
        "depth": depth,
        "null_centroid": np.isnan(depth).all(axis=0),
    }


def aqueduct_check(aq: dict[str, Any], result: dict[str, Any], rp_cfg: dict[str, Any]) -> dict:
    """Extra sub-check entries for a one-map-per-period product (diagnostic only).

    Main: CLIMADA ``local_exceedance_intensity`` on the nine-event hazard built from the maps
    (incremental frequencies). Checks: (1) the empirical step rule (already in the block);
    (2) the raw maps must be monotone in the return period; (3) the CLIMADA reading must equal
    the raw map at each kept period (it does wherever (2) holds).
    """
    import numpy as np
    from climaterisk_worker import board

    keep = ~aq["null_centroid"]
    mono = board.rp_monotone_check(aq["rps"], aq["depth"][:, keep])
    mono["warning"] = mono["share_violating"] > float(rp_cfg["warn_share"])
    idx = [aq["rps"].index(int(p)) for p in result["periods"]]
    raw = aq["depth"][idx][:, keep]
    main = np.asarray(result["intensity"], dtype=float)[:, keep]
    diff = np.abs(main - raw)
    tol = float(rp_cfg["rel_tol"])
    differ = diff > tol * np.maximum(main, raw)
    return {
        "map_rule": "one depth map per return period; event i = map i, frequency 1/T_i - 1/T_i+1",
        "monotone": mono,
        "raw_map_vs_main": {
            "max_abs_diff_m": float(diff.max()) if diff.size else 0.0,
            "share_differ": float(differ.sum() / max(int((raw > 0).sum()), 1)),
        },
    }


LITPOP_EXPOSURE_KEYS = ("litpop_value_usd", "litpop_eal_usd")
_LITPOP_CACHE: dict[str, Any] = {}


def litpop_impact_function(spec: dict[str, Any], litpop_cfg: dict[str, Any]) -> tuple[Any, str]:
    """Impact function and its text for a LitPop layer (same choices as the asset engine).

    River and coastal flood: the JRC curve of ``litpop_cfg["flood_region"]`` and
    ``litpop_cfg["sector"]`` (``registry.flood_impact_function`` /
    ``coastal_flood_impact_function``, as ``physical_risk.engine`` does). Tropical cyclone:
    the Eberenz regional function CLIMADA assigns to the layer's country
    (``registry.tc_region_for_country`` + ``tc_impact_function``).

    Returns:
        ``(function, text naming the function and id)``.
    """
    from climaterisk_worker.physical_risk import registry

    peril = spec["peril"]
    if peril in ("river_flood", "coastal_flood"):
        region, sector = str(litpop_cfg["flood_region"]), str(litpop_cfg["sector"])
        make = (
            registry.flood_impact_function
            if peril == "river_flood"
            else registry.coastal_flood_impact_function
        )
        func = make(region, sector)
        text = (
            f"JRC depth-damage curve ImpfRiverFlood.from_jrc_region_sector({region!r}, "
            f"{sector!r}), id {int(func.id)} ({func.name})"
            + (" — same curve tagged CF" if peril == "coastal_flood" else "")
        )
        return func, text
    if peril == "tropical_cyclone":
        iso3 = str(spec["country_iso3"])
        assigned = registry.tc_region_for_country(iso3)
        if assigned is None:
            raise ValueError(f"{iso3}: no CLIMADA tropical-cyclone region; LitPop not computed")
        impf_id, code, name = assigned
        func = registry.tc_impact_function(impf_id)
        text = (
            f"Eberenz et al. 2021 regional TC function {code} {name!r}, id {int(func.id)} "
            f"({func.name}), assigned to {iso3} by ImpfSetTropCyclone"
        )
        return func, text
    raise ValueError(f"{peril}: no LitPop weighting for this peril")


def load_litpop(litpop_cfg: dict[str, Any], country_iso3: str) -> Any:
    """The LitPop ``Exposures`` (CLIMADA Data API, offline cache), read once per process."""
    from climaterisk_worker._dataapi import make_client

    key = f"{litpop_cfg['dataset']}|{country_iso3}"
    if key not in _LITPOP_CACHE:
        _LITPOP_CACHE[key] = make_client().get_exposures(
            "litpop",
            properties={
                "country_iso3alpha": country_iso3,
                "exponents": str(litpop_cfg["exponents"]),
                "fin_mode": str(litpop_cfg["fin_mode"]),
            },
        )
    return _LITPOP_CACHE[key]


def litpop_layer(
    layer: str,
    spec: dict[str, Any],
    litpop_cfg: dict[str, Any],
    hazard: Any,
    pieces: Any,
    result: dict[str, Any],
    res_deg: float,
) -> dict[str, Any]:
    """LitPop exposure weighting of one built layer: per-piece value, EAL, ``meta.litpop``.

    Main method: CLIMADA ``ImpactCalc(exposures, impfset, hazard).impact()`` per-point
    ``eai_exp`` (centroids assigned the CLIMADA way), summed per piece. Sub check
    (diagnostic only): per piece, loss at each kept return period =
    value x MDR(piece intensity) x mean wet-event affected fraction of the cell, then
    ``board.eal_from_rp_losses``; ``board.litpop_check`` compares it with the main EAL.
    LitPop points are assigned to the piece that contains them (``board.assign_points``);
    a point outside every piece is counted, not moved.

    Algorithm:
        $$EAL_p = \\sum_{k \\in p} \\sum_e f_e\\, V_k\\, \\mathrm{MDR}(I_e(c_k))$$
        with $V_k$ the LitPop value (USD) of point $k$, $f_e$ the annual frequency of event
        $e$, $I_e(c_k)$ its intensity at the centroid assigned to $k$ and MDR the impact
        function's mean damage ratio (CLIMADA ``eai_exp``). Sums only; no mean.
        ASCII: EAL_piece = sum over points in the piece of sum over events of
        f_e * V_k * MDR(I_e(c_k)).

    Args:
        layer: Layer name.
        spec: The layer's block of ``visual_board.json``.
        litpop_cfg: The config's ``litpop`` block.
        hazard: The layer's CLIMADA hazard (the one the return periods came from).
        pieces: GeoDataFrame of the layer's pieces with ``centroid`` and geometry.
        result: ``board.rp_intensity`` output with ``piece_values`` ``(n_periods, n_pieces)``.
        res_deg: Hazard grid cell side in degrees (centroid matching threshold).

    Returns:
        ``{"meta": meta.litpop, "exposure": {key: array(n_pieces)}}``.
    """
    import numpy as np
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet
    from climaterisk_worker import board

    func, impf_text = litpop_impact_function(spec, litpop_cfg)
    haz_type = str(hazard.haz_type)
    if str(func.haz_type) != haz_type:
        raise ValueError(f"{layer}: impact function tag {func.haz_type} != hazard {haz_type}")
    exp = load_litpop(litpop_cfg, str(spec["country_iso3"]))
    gdf = exp.gdf
    lon = gdf.geometry.x.to_numpy(dtype=float)
    lat = gdf.geometry.y.to_numpy(dtype=float)
    value = gdf["value"].to_numpy(dtype=float)
    n_points = int(value.size)
    log.debug("%s: litpop points %s %s, impf %s", layer, lon.shape, lon.dtype, impf_text)

    frame = pd.DataFrame(
        {"latitude": lat, "longitude": lon, "value": value, f"impf_{haz_type}": int(func.id)}
    )
    exposures = Exposures(frame, value_unit=str(exp.value_unit))
    # Nearest centroid, but only within ``max_centroid_distance_cells`` hazard cells: every
    # LitPop point lies on a corner shared by four cells, so a point with none of its four
    # cells in the hazard grid (e.g. inland of the wet-cell-only coastal grid) is not
    # exposed instead of being tied to a far-away wet cell.
    threshold_deg = res_deg * float(litpop_cfg["max_centroid_distance_cells"])
    exposures.assign_centroids(hazard, threshold=threshold_deg)
    impact = ImpactCalc(exposures, ImpactFuncSet([func]), hazard).impact(
        save_mat=False, assign_centroids=False
    )
    eai_point = np.asarray(impact.eai_exp, dtype=float)
    centr_point = exposures.gdf[f"centr_{haz_type}"].to_numpy(dtype=int)

    # The LitPop points sit on the corners of the hazard cells (the two 150 arcsec grids are
    # registered half a cell apart), so "the cell containing the point" is a floating-point
    # tie. The point is read in the cell of the centroid CLIMADA assigned to it (the same one
    # the main method used), by moving only the *query* location a fraction of the way to
    # that centroid; the 시군구 is read there. The LitPop value itself is not moved.
    nudge = float(litpop_cfg["centroid_nudge_fraction"])
    h_lon = np.asarray(hazard.centroids.lon, dtype=float)
    h_lat = np.asarray(hazard.centroids.lat, dtype=float)
    q_lon, q_lat = lon.copy(), lat.copy()
    has_c = centr_point >= 0
    q_lon[has_c] += nudge * (h_lon[centr_point[has_c]] - lon[has_c])
    q_lat[has_c] += nudge * (h_lat[centr_point[has_c]] - lat[has_c])
    rows = [{"polygons": polygons_of(g)} for g in pieces.geometry]
    idx = board.assign_points(q_lon, q_lat, rows)
    ok = idx >= 0
    n_pieces = len(rows)
    centr_piece = pieces["centroid"].to_numpy(dtype=int)
    n_centroid_mismatch = int((centr_point[ok] != centr_piece[idx[ok]]).sum())

    value_piece = np.bincount(idx[ok], weights=value[ok], minlength=n_pieces)
    eal_piece = np.bincount(idx[ok], weights=eai_point[ok], minlength=n_pieces)

    # sub check: loss at each kept return period = value x MDR(piece intensity)
    periods = [float(p) for p in result["periods"]]
    inten = np.nan_to_num(np.asarray(result["piece_values"], dtype=float), nan=0.0)
    # ImpactCalc multiplies each event's loss by the hazard's affected fraction of the cell
    # (the ISIMIP flooded fraction); an intensity at a return period carries no fraction, so
    # the check uses the frequency-weighted mean fraction of the cell's wet events.
    wet = (hazard.intensity > 0).astype(float)
    freq = np.asarray(hazard.frequency, dtype=float)
    den = np.asarray(wet.T @ freq).ravel()
    frac = hazard.fraction if hazard.fraction.nnz > 0 else wet  # no fraction stored = 1 where wet
    num = np.asarray(frac.multiply(wet).T @ freq).ravel()
    fbar = np.divide(num, den, out=np.zeros_like(den), where=den > 0)[centr_piece]
    losses = np.stack(
        [value_piece * np.asarray(func.calc_mdr(row), dtype=float) * fbar for row in inten]
    )
    eal_check = np.asarray(board.eal_from_rp_losses(periods, losses), dtype=float)
    ck_cfg = litpop_cfg["check"]
    check = board.litpop_check(
        eal_piece,
        eal_check,
        float(ck_cfg["rel_tol"]),
        float(ck_cfg["warn_share"]),
        float(ck_cfg["min_eal"]),
    )
    check.update(
        rel_tol=float(ck_cfg["rel_tol"]),
        warn_share=float(ck_cfg["warn_share"]),
        method="return_period_trapezoid",
    )
    if check["warning"]:
        log.warning(
            "%s: LitPop EAL sub check disagrees on %.0f%% of %d pieces (tol %.0f%%, warn > %.0f%%)",
            layer, 100 * check["disagree_share"], check["n_compared"],
            100 * check["rel_tol"], 100 * check["warn_share"],
        )  # fmt: skip
    log.info(
        "%s: LitPop %d/%d points assigned, EAL total %.3e USD, %d centroid mismatches",
        layer, int(ok.sum()), n_points, float(eal_piece.sum()), n_centroid_mismatch,
    )  # fmt: skip

    lp_meta = {
        "dataset": str(litpop_cfg["dataset"]),
        "res_arcsec": int(litpop_cfg["res_arcsec"]),
        "exponents": str(litpop_cfg["exponents"]),
        "fin_mode": str(litpop_cfg["fin_mode"]),
        "value_unit": str(exp.value_unit),
        "banner_ko": str(litpop_cfg["banner_ko"]),
        "size_legend_usd": [float(v) for v in litpop_cfg["size_legend_usd"]],
        "impf": impf_text,
        "assumptions": list(litpop_cfg["assumptions_ko"]),
        "n_points": n_points,
        "n_assigned": int(ok.sum()),
        "n_centroid_mismatch": n_centroid_mismatch,
        "value_total_usd": float(value.sum()),
        "value_assigned_usd": float(value_piece.sum()),
        "eal_total_usd": float(eal_piece.sum()),
        "eal_unassigned_usd": float(eai_point[~ok].sum()),
        "check": check,
    }
    return {
        "meta": lp_meta,
        "exposure": {"litpop_value_usd": value_piece, "litpop_eal_usd": eal_piece},
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
    aq: dict[str, Any] | None = None
    if spec.get("source") == AQUEDUCT_SOURCE:
        aq = load_aqueduct(layer, cfg)
        hazard, lon, lat, res_deg = aq["hazard"], aq["lon"], aq["lat"], aq["res_deg"]
    else:
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
    if aq is not None:
        # Outside the layer's own cache box there is no data: null, not the 0 the hazard
        # object needed to be built.
        result["intensity"][:, aq["null_centroid"]] = np.nan
        check["intensity"][:, aq["null_centroid"]] = np.nan
    rp_cfg = cfg["rp_check"]
    shares = board.rp_disagreement(result, check, float(rp_cfg["rel_tol"]))
    check_block = {
        "method": "empirical_step",
        "rel_tol": float(rp_cfg["rel_tol"]),
        "warn_share": float(rp_cfg["warn_share"]),
        "disagree_share_by_period": shares,
        "warning": any(v > float(rp_cfg["warn_share"]) for v in shares.values()),
    }
    if aq is not None:
        check_block.update(aqueduct_check(aq, result, rp_cfg))
        check_block["warning"] = bool(check_block["warning"] or check_block["monotone"]["warning"])
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

    litpop: dict[str, Any] | None = None
    litpop_cfg = cfg.get("litpop")
    if litpop_cfg and layer in litpop_cfg["layers"]:
        litpop = litpop_layer(layer, spec, litpop_cfg, hazard, pieces, result, res_deg)

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
    if litpop is not None:
        meta["litpop"] = litpop["meta"]
    meta["runtime_seconds"] = round(time.time() - t0, 1)
    board_file = out / f"{layer}.board.json"
    board_file.write_text(
        json.dumps(
            board_json_payload(pieces, meta, spec, litpop["exposure"] if litpop else None),
            ensure_ascii=False,
            separators=(",", ":"),
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
    if ch.get("notes"):
        meta["notes"] = ch["notes"]
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
        if spec.get("source") == AQUEDUCT_SOURCE:
            log.info(
                "%s: Aqueduct maps come from <DATA_ROOT>/cache/aqueduct, not fetched here", layer
            )
            continue
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
