"""Visual board, step 1: return-period intensity, grid-cell pieces, point lookup, summaries.

See ``docs/VISUAL_BOARD_KO.md`` §2. A *piece* is one hazard grid cell intersected with one
administrative region; it carries the hazard value of its cell unchanged. Nothing here
averages, interpolates or invents a value: a region is summarised by piece count, minimum
and maximum only, and a return period beyond what the event record supports is dropped,
never replaced.

Heavy imports (geopandas, shapely, CLIMADA objects) are deferred into the functions so the
module imports in any environment; the functions themselves need the worker environment.
Logging uses the standard library (the ``climaterisk`` logger imports pydantic, which the
worker environment does not have).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

WGS84 = 4326
KOREA_TM = 5179  # EPSG:5179 (Korea 2000 / Unified CS): equal-area enough for km2 reporting
_BOARD_CONFIG = "visual_board.json"


def board_config_path() -> Path:
    """Location of ``visual_board.json`` (``CLIMATERISK_LIBRARY_DIR`` overrides the default)."""
    repo = Path(__file__).resolve().parents[2]
    override = os.environ.get("CLIMATERISK_LIBRARY_DIR")
    base = Path(override) if override else repo / "assets" / "libraries"
    if not base.is_absolute():
        base = repo / base
    return base / _BOARD_CONFIG


def load_board_config(path: Path | None = None) -> dict[str, Any]:
    """Read the board configuration (return periods and layer definitions).

    Args:
        path: Explicit file; defaults to :func:`board_config_path`.

    Returns:
        The parsed JSON. ``return_periods_years`` must be a non-empty list of positive
        numbers and ``layers`` a non-empty mapping; anything else raises ``ValueError``.
    """
    cfg = json.loads((path or board_config_path()).read_text(encoding="utf-8"))
    rps = cfg.get("return_periods_years")
    if not isinstance(rps, list) or not rps or any(not (float(x) > 0) for x in rps):
        raise ValueError("visual_board.json: return_periods_years must be positive numbers")
    if not cfg.get("layers"):
        raise ValueError("visual_board.json: no layers defined")
    return cfg


def rp_intensity(hazard: Any, return_periods: list[float]) -> dict[str, Any]:
    """Hazard intensity at fixed return periods for every centroid.

    Algorithm:
        With events $e$ of annual frequency $f_e$ and intensity $I_e(c)$ at centroid $c$,
        the exceedance frequency of a level $x$ is

        $$F_c(x) = \\sum_{e:\\,I_e(c) \\ge x} f_e$$

        and the intensity of return period $T$ (years) solves $F_c(x) = 1/T$. CLIMADA's
        ``method="extrapolate_constant"`` returns 0 when $1/T$ exceeds the exceedance
        frequency of the weakest event (the period is shorter than the most frequent
        event). The record length is $R = 1/\\min_e f_e$ (years) and only periods
        $T \\le R/2$ are kept (``physical._resolvable_return_periods``); if none
        qualifies, nothing is substituted.

        ASCII: F_c(x) = sum(f_e where I_e(c) >= x); solve F_c(x) = 1/T; keep T <= R/2,
        R = 1 / min(f_e).

    Args:
        hazard: A CLIMADA ``Hazard``.
        return_periods: Requested return periods in years.

    Returns:
        ``{"periods", "max_supported", "record_years", "intensity"}``: ascending kept
        periods, the cap in years and the record length (both ``None`` if the hazard has
        no positive frequency), and a float array ``(len(periods), n_centroids)`` in the
        hazard's units. A too-frequent period is 0; NaN is kept only if CLIMADA reports
        no data (not expected).
    """
    from climaterisk_worker.physical import _resolvable_return_periods

    wanted = sorted({float(p) if float(p) != int(p) else int(p) for p in return_periods})
    _, max_rp, record_years = _resolvable_return_periods(hazard, wanted)
    kept = [p for p in wanted if max_rp is not None and p <= max_rp]
    n_centroids = int(hazard.intensity.shape[1])
    if not kept:
        log.warning(
            "no requested return period within cap %s years (record %s years)",
            max_rp,
            record_years,
        )
        return {
            "periods": [],
            "max_supported": max_rp,
            "record_years": record_years,
            "intensity": np.zeros((0, n_centroids)),
        }
    gdf = hazard.local_exceedance_intensity(kept, method="extrapolate_constant")[0]
    values = gdf.drop(columns="geometry").to_numpy(dtype=float).T  # (n_periods, n_centroids)
    # extrapolate_constant yields NaN where the period is more frequent than every event
    # of the centroid; that is a computed zero, not missing data.
    n_nan = int(np.isnan(values).sum())
    values = np.nan_to_num(values, nan=0.0)
    log.debug("rp_intensity shape=%s dtype=%s nan_to_zero=%d", values.shape, values.dtype, n_nan)
    return {
        "periods": kept,
        "max_supported": max_rp,
        "record_years": record_years,
        "intensity": values,
    }


def grid_cells(lon: Any, lat: Any, res_deg: float) -> Any:
    """One square cell of side ``res_deg`` centred on each centroid.

    Args:
        lon: Centroid longitudes (degrees east).
        lat: Centroid latitudes (degrees north).
        res_deg: Cell side in degrees.

    Returns:
        GeoDataFrame in EPSG:4326, column ``centroid`` = index into ``lon``/``lat``.
    """
    import geopandas as gpd
    import shapely

    lon_a = np.asarray(lon, dtype=float)
    lat_a = np.asarray(lat, dtype=float)
    half = res_deg / 2.0
    boxes = shapely.box(lon_a - half, lat_a - half, lon_a + half, lat_a + half)
    log.debug("grid_cells n=%d res_deg=%s", lon_a.size, res_deg)
    return gpd.GeoDataFrame(
        {"centroid": np.arange(lon_a.size)}, geometry=boxes, crs=f"EPSG:{WGS84}"
    )


def clip_pieces(cells: Any, regions: Any, code_col: str, name_col: str) -> Any:
    """Intersect every cell with every region it overlaps (cell value is never averaged).

    A cell crossed by a boundary yields one piece per region, all with the same
    ``centroid``. Empty or boundary-only (zero area) intersections are dropped. A spatial
    index restricts the work to overlapping pairs.

    Args:
        cells: GeoDataFrame from :func:`grid_cells`.
        regions: Region polygons in any CRS.
        code_col: Region code column of ``regions``.
        name_col: Region name column of ``regions``.

    Returns:
        GeoDataFrame (EPSG:4326) with ``centroid``, ``code``, ``name``, ``area_km2``
        (computed in EPSG:5179) and the piece geometry.
    """
    import geopandas as gpd
    import shapely

    reg = regions.to_crs(WGS84).reset_index(drop=True)
    cell_geoms = cells.geometry.to_numpy()
    reg_geoms = reg.geometry.to_numpy()
    pair_cell, pair_reg = reg.sindex.query(cell_geoms, predicate="intersects")
    inter = shapely.intersection(cell_geoms[pair_cell], reg_geoms[pair_reg])
    polygonal = np.isin(shapely.get_type_id(inter), [3, 6])  # Polygon, MultiPolygon
    ok = polygonal & ~shapely.is_empty(inter)
    out = gpd.GeoDataFrame(
        {
            "centroid": cells["centroid"].to_numpy()[pair_cell[ok]],
            "code": reg[code_col].astype(str).to_numpy()[pair_reg[ok]],
            "name": reg[name_col].astype(str).to_numpy()[pair_reg[ok]],
        },
        geometry=inter[ok],
        crs=f"EPSG:{WGS84}",
    )
    out["area_km2"] = out.to_crs(KOREA_TM).area.to_numpy() / 1e6
    out = out[out["area_km2"] > 0].sort_values(["centroid", "code"]).reset_index(drop=True)
    log.debug("clip_pieces cells=%d regions=%d pieces=%d", len(cells), len(reg), len(out))
    return out[["centroid", "code", "name", "area_km2", "geometry"]]


def lookup(pieces: Any, lon: float, lat: float) -> dict[str, Any] | None:
    """The piece containing a point.

    On a shared edge several pieces match; the lowest (centroid, code) is returned so the
    answer is deterministic.

    Args:
        pieces: GeoDataFrame from :func:`clip_pieces`.
        lon: Longitude (degrees east).
        lat: Latitude (degrees north).

    Returns:
        ``{"centroid", "code", "name"}`` or ``None`` outside every piece.
    """
    import shapely

    idx = pieces.sindex.query(shapely.Point(lon, lat), predicate="intersects")
    if len(idx) == 0:
        return None
    hits = pieces.iloc[np.asarray(idx)].sort_values(["centroid", "code"])
    row = hits.iloc[0]
    return {"centroid": int(row["centroid"]), "code": str(row["code"]), "name": str(row["name"])}


def region_summary(pieces: Any, values: Any) -> dict[str, dict[str, Any]]:
    """Per-region piece count, minimum and maximum of the piece values (no mean).

    Args:
        pieces: GeoDataFrame from :func:`clip_pieces`.
        values: Hazard value per centroid (indexed by the ``centroid`` column); NaN means
            no data and is ignored for min/max.

    Returns:
        ``code -> {"name", "n_pieces", "min", "max"}``; ``min``/``max`` are ``None`` when
        every piece value is NaN.
    """
    vals = np.asarray(values, dtype=float)
    out: dict[str, dict[str, Any]] = {}
    for code, grp in pieces.groupby("code", sort=True):
        v = vals[grp["centroid"].to_numpy(dtype=int)]
        v = v[~np.isnan(v)]
        out[str(code)] = {
            "name": str(grp["name"].iloc[0]),
            "n_pieces": len(grp),
            "min": float(v.min()) if v.size else None,
            "max": float(v.max()) if v.size else None,
        }
    return out
