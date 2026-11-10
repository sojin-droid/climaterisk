#!/usr/bin/env python
"""Build a national visual-board layer: hazard grid cell x 시군구 pieces with per-period values.

Layer ``river_flood_hist_kor`` (``assets/libraries/visual_board.json``): the CLIMADA Data API
river-flood historical hazard for KOR (150 arcsec grid), read from CLIMADA's local download
cache only (offline), cut by the SGIS 시군구 boundaries. Each piece keeps its cell's value;
nothing is averaged (docs/VISUAL_BOARD_KO.md section 2).

Runs in the CLIMADA worker environment::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    export CLIMATERISK_CLIMADA_DATA_DIR=~/Data/climaterisk/external/climada
    ./.climada-env/bin/python scripts/board_layer.py [--layer river_flood_hist_kor]

Outputs under ``<DATA_ROOT>/derived/board/``: ``<layer>.geojson`` (columns ``centroid``,
``code``, ``name``, ``area_km2``, ``rp<T>`` per kept return period in the hazard's units)
and ``<layer>.meta.json`` (source, resolution, periods kept, max_supported, record_years,
n_pieces, null vs 0 counts). A null value means no hazard data; 0 is a computed zero.
"""

from __future__ import annotations

import argparse
import json
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


def period_column(period: float) -> str:
    """GeoJSON column name of a return period: ``rp2``, ``rp2.5``, ``rp100``."""
    return f"rp{period:g}"


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
            f"{spec['year_range']} {spec['country_iso3']} (local download cache, offline)"
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
        properties={
            "climate_scenario": spec["climate_scenario"],
            "year_range": spec["year_range"],
            "spatial_coverage": "country",
            "country_iso3alpha": spec["country_iso3"],
        },
    )
    lon = np.asarray(hazard.centroids.lon, dtype=float)
    lat = np.asarray(hazard.centroids.lat, dtype=float)
    meta_t = hazard.centroids.get_meta()["transform"]
    res_deg = float(abs(meta_t.a))

    result = board.rp_intensity(hazard, requested)

    shp = paths.municipality_sgis_dir() / f"bnd_sigungu_00_{spec['boundary_version']}.shp"
    regions = gpd.read_file(shp, encoding="utf-8")
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
    meta["runtime_seconds"] = round(time.time() - t0, 1)
    (out / f"{layer}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--layer", default="river_flood_hist_kor")
    ap.add_argument("--out", type=Path, default=None, help="default: <DATA_ROOT>/derived/board")
    args = ap.parse_args()
    meta = run(args.layer, args.out)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
