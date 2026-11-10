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


def rp_intensity_check(hazard: Any, return_periods: list[float]) -> dict[str, Any]:
    """Sub check of :func:`rp_intensity`: the empirical step rule, no interpolation.

    Diagnostic only. It never replaces the main CLIMADA result; ``scripts/board_layer.py``
    records how often the two disagree and warns when that is frequent.

    Algorithm:
        With events $e$ of annual frequency $f_e$ and intensity $I_e(c)$ at centroid $c$,
        the exceedance frequency of the level reached by event $e$ is

        $$F_c(I_e) = \\sum_{k:\\,I_k(c) \\ge I_e(c)} f_k$$

        and the intensity of return period $T$ (years) is

        $$x_T(c) = \\max\\{I_e(c) : F_c(I_e) \\ge 1/T\\}$$

        or 0 if no event qualifies. Ties share one $F$ (all events of equal intensity are
        counted). Kept periods, cap and record come from
        ``physical._resolvable_return_periods`` exactly as in :func:`rp_intensity`.

        ASCII: F(I_e) = sum(f_k where I_k >= I_e); x_T = max(I_e where F(I_e) >= 1/T), else 0.

    Vectorised over centroids: the sparse intensity matrix is taken column-wise (CSC), the
    non-zero entries are sorted by (centroid, descending intensity), $F$ is a running sum
    inside each centroid, and the first entry reaching $1/T$ is found for all centroids at
    once. The matrix is never densified.

    Args:
        hazard: A CLIMADA ``Hazard``.
        return_periods: Requested return periods in years.

    Returns:
        Same dict as :func:`rp_intensity` (``periods``, ``max_supported``,
        ``record_years``, ``intensity`` of shape ``(len(periods), n_centroids)``).
    """
    from climaterisk_worker.physical import _resolvable_return_periods

    wanted = sorted({float(p) if float(p) != int(p) else int(p) for p in return_periods})
    _, max_rp, record_years = _resolvable_return_periods(hazard, wanted)
    kept = [p for p in wanted if max_rp is not None and p <= max_rp]
    n_centroids = int(hazard.intensity.shape[1])
    out = {
        "periods": kept,
        "max_supported": max_rp,
        "record_years": record_years,
        "intensity": np.zeros((len(kept), n_centroids)),
    }
    if not kept:
        return out
    mat = hazard.intensity.tocsc()
    freq = np.asarray(hazard.frequency, dtype=float)
    col = np.repeat(np.arange(n_centroids), np.diff(mat.indptr))
    inten = np.asarray(mat.data, dtype=float)
    f_ev = freq[mat.indices]
    pos = inten > 0
    col, inten, f_ev = col[pos], inten[pos], f_ev[pos]
    if inten.size == 0:
        return out
    order = np.lexsort((-inten, col))  # by centroid, then intensity descending
    col, inten, f_ev = col[order], inten[order], f_ev[order]
    cum = np.cumsum(f_ev)
    first = np.r_[True, col[1:] != col[:-1]]
    start_cum = (cum - f_ev)[first]  # running total before each centroid's first entry
    cum = cum - start_cum[np.cumsum(first) - 1]
    # Ties share the exceedance frequency of the whole tie group (value at its last entry).
    is_end = np.r_[(col[1:] != col[:-1]) | (inten[1:] != inten[:-1]), True]
    ends = np.flatnonzero(is_end)
    cum = cum[ends[np.searchsorted(ends, np.arange(cum.size), side="left")]]
    for i, period in enumerate(kept):
        reach = cum >= (1.0 / period) * (1.0 - 1e-9)
        hit = np.flatnonzero(reach)
        if hit.size == 0:
            continue
        cols_hit, first_hit = np.unique(col[hit], return_index=True)
        out["intensity"][i, cols_hit] = inten[hit[first_hit]]
    log.debug("rp_intensity_check nnz=%d shape=%s", inten.size, out["intensity"].shape)
    return out


def rp_disagreement(
    main: dict[str, Any], check: dict[str, Any], rel_tol: float
) -> dict[str, float]:
    """Share of centroids where the main method and the sub check differ per period.

    Algorithm:
        For period $T$ over the centroids $C_T$ with $m_c > 0$ or $k_c > 0$ ($m$ main,
        $k$ check, hazard units),

        $$s_T = \\frac{|\\{c \\in C_T : |m_c - k_c| > \\tau \\max(m_c, k_c)\\}|}{|C_T|}$$

        with relative tolerance $\\tau$ (dimensionless); $s_T = 0$ when $C_T$ is empty.

        ASCII: s_T = count(|m-k| > tol*max(m,k)) / count(m>0 or k>0).

    Args:
        main: Output of :func:`rp_intensity`.
        check: Output of :func:`rp_intensity_check` (same kept periods).
        rel_tol: Relative tolerance $\\tau$.

    Returns:
        ``{period_key: share}`` with ``period_key`` formatted as ``f"{period:g}"``.

    Raises:
        ValueError: The two results have different periods or shapes.
    """
    if list(main["periods"]) != list(check["periods"]):
        raise ValueError(f"periods differ: {main['periods']} vs {check['periods']}")
    m = np.asarray(main["intensity"], dtype=float)
    k = np.asarray(check["intensity"], dtype=float)
    if m.shape != k.shape:
        raise ValueError(f"shapes differ: {m.shape} vs {k.shape}")
    out: dict[str, float] = {}
    for p, mr, kr in zip(main["periods"], m, k, strict=True):
        active = (mr > 0) | (kr > 0)
        n = int(active.sum())
        bad = int((np.abs(mr - kr) > rel_tol * np.maximum(mr, kr))[active].sum()) if n else 0
        out[f"{p:g}"] = bad / n if n else 0.0
    return out


def change_values(base: Any, future: Any) -> tuple[Any, Any]:
    """Future / base ratio with an explicit status where the ratio is undefined.

    Algorithm:
        $$r = x_{\\mathrm{fut}} / x_{\\mathrm{base}}$$ (dimensionless) where both are
        finite and $x_{\\mathrm{base}} > 0$. Otherwise $r$ is NaN with a status: both
        NaN-free and both 0 -> ``both_zero``; base 0 and future $> 0$ -> ``base_zero`` (the
        ratio is undefined, not infinite); either NaN -> ``null``. A defined ratio has
        status ``""``.

        ASCII: r = fut / base if base > 0; else NaN with status both_zero / base_zero / null.

    Args:
        base: Base-period values (any shape).
        future: Future values, same shape.

    Returns:
        ``(ratio, status)``: float array and ``<U9`` string array of the input shape.

    Raises:
        ValueError: Shapes differ.
    """
    b = np.asarray(base, dtype=float)
    f = np.asarray(future, dtype=float)
    if b.shape != f.shape:
        raise ValueError(f"shapes differ: {b.shape} vs {f.shape}")
    ratio = np.full(b.shape, np.nan)
    status = np.full(b.shape, "", dtype="<U9")
    null = np.isnan(b) | np.isnan(f)
    both0 = ~null & (b == 0) & (f == 0)
    base0 = ~null & (b == 0) & (f > 0)
    ok = ~null & (b > 0)
    status[null] = "null"
    status[both0] = "both_zero"
    status[base0] = "base_zero"
    ratio[ok] = f[ok] / b[ok]
    return ratio, status


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


def simplify_regions(regions: Any, simplify_m: float) -> Any:
    """Display-size reduction of region outlines (same rule as ``scripts/board_layer.py``).

    Each outline is simplified on its own in metres (EPSG:5179), so neighbouring outlines may
    leave gaps or overlaps up to about ``simplify_m``; only which region label a point within
    that distance of a boundary gets can change.

    Args:
        regions: Region polygons in any CRS.
        simplify_m: Douglas-Peucker tolerance in metres; ``<= 0`` returns ``regions`` unchanged.

    Returns:
        GeoDataFrame in EPSG:4326 (unchanged object when ``simplify_m <= 0``).
    """
    if simplify_m <= 0:
        return regions
    out = regions.to_crs(KOREA_TM)
    out["geometry"] = out.geometry.simplify(simplify_m, preserve_topology=True)
    return out.to_crs(WGS84)


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


# ---------------------------------------------------------------------------------------
# Aqueduct coastal flood: one depth map per return period (not an event set)
# ---------------------------------------------------------------------------------------
def aqueduct_union(layers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Put cached Aqueduct depth stacks on one grid and keep the cells wet in any of them.

    Each cached stack (``<DATA_ROOT>/cache/aqueduct/*.npz``) covers its own bounding box on
    the same 30 arcsec grid. The union of the boxes is the board grid; a cell outside a
    layer's own box is **NaN (no data) for that layer, never 0**. Inside its box the cache
    stores nodata as 0, so a 0 there means "no flooding modelled"; the data cannot separate
    dry land from nodata and that is stated in the layer notes.

    Args:
        layers: ``name -> {"lat", "lon", "rps", "depth"}`` with 1-D grid axes (cell centres,
            latitude descending) and ``depth`` of shape ``(n_rp, n_lat, n_lon)`` in metres.
            All layers share the spacing and the return-period list and are grid-aligned.

    Returns:
        ``{"lat", "lon", "res_deg", "rps", "depth"}``: ``lat``/``lon`` of the kept cells,
        ``depth[name]`` of shape ``(n_rp, n_cells)`` (NaN outside the layer's box).

    Raises:
        ValueError: Layers differ in spacing, return periods, or grid alignment.
    """
    names = list(layers)
    first = layers[names[0]]
    step = float(abs(first["lat"][1] - first["lat"][0]))
    rps = [int(r) for r in first["rps"]]
    lat0 = max(float(layers[n]["lat"][0]) for n in names)
    lon0 = min(float(layers[n]["lon"][0]) for n in names)
    placed: dict[str, tuple[int, int]] = {}
    n_lat = n_lon = 0
    for n in names:
        ly = layers[n]
        if [int(r) for r in ly["rps"]] != rps:
            raise ValueError(f"{n}: return periods differ from {names[0]}")
        if abs(abs(float(ly["lat"][1] - ly["lat"][0])) - step) > 1e-6 * step:
            raise ValueError(f"{n}: grid spacing differs")
        fi, fj = (lat0 - float(ly["lat"][0])) / step, (float(ly["lon"][0]) - lon0) / step
        if abs(fi - round(fi)) > 1e-3 or abs(fj - round(fj)) > 1e-3:
            raise ValueError(f"{n}: grid not aligned with the other layers")
        i0, j0 = round(fi), round(fj)
        placed[n] = (i0, j0)
        n_lat = max(n_lat, i0 + len(ly["lat"]))
        n_lon = max(n_lon, j0 + len(ly["lon"]))
    full: dict[str, Any] = {}
    for n in names:
        arr = np.full((len(rps), n_lat, n_lon), np.nan, dtype=np.float32)
        i0, j0 = placed[n]
        d = np.asarray(layers[n]["depth"], dtype=np.float32)
        arr[:, i0 : i0 + d.shape[1], j0 : j0 + d.shape[2]] = d
        full[n] = arr
    with np.errstate(invalid="ignore"):
        wet = np.zeros((n_lat, n_lon), dtype=bool)
        for arr in full.values():
            wet |= (arr > 0).any(axis=0)
    ii, jj = np.nonzero(wet)
    lat_c = lat0 - step * ii
    lon_c = lon0 + step * jj
    log.debug(
        "aqueduct_union layers=%d grid=%s wet_cells=%d step=%.6f", len(names), (n_lat, n_lon),
        ii.size, step,
    )  # fmt: skip
    return {
        "lat": lat_c,
        "lon": lon_c,
        "res_deg": step,
        "rps": rps,
        "depth": {n: full[n][:, ii, jj].astype(float) for n in names},
    }


def aqueduct_hazard(lat: Any, lon: Any, rps: list[int], depth: Any) -> Any:
    """CLIMADA ``Hazard`` (one event per return-period map) from Aqueduct depth maps.

    Event $i$ is the map of return period $T_i$ with the incremental exceedance frequency
    $f_i = 1/T_i - 1/T_{i+1}$ (the largest period keeps $1/T_n$), the rule of
    ``ingest._incremental_frequency`` that the Models tab uses. Because the maps are nested
    (depth grows with the period), the exceedance frequency of event $i$'s depth is
    $\\sum_{k \\ge i} f_k = 1/T_i$, so ``board.rp_intensity`` returns the maps themselves
    wherever they are monotone; where they are not, it returns the CLIMADA reading and the
    difference is what the sub check reports.

    Args:
        lat: Cell-centre latitudes, shape ``(n_cells,)``.
        lon: Cell-centre longitudes.
        rps: Return periods in years of the maps (ascending).
        depth: Depth in metres, ``(len(rps), n_cells)``; NaN is taken as 0 here.

    Returns:
        A checked ``Hazard`` with units ``m``.
    """
    from climada.hazard import Hazard
    from scipy import sparse

    from climaterisk_worker import ingest
    from climaterisk_worker.hazard_convert import _centroids

    inten = np.nan_to_num(np.asarray(depth, dtype=float), nan=0.0)
    n_ev = len(rps)
    haz = Hazard(
        haz_type="CF",
        units="m",
        centroids=_centroids(np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)),
        event_id=np.arange(1, n_ev + 1),
        event_name=[f"rp{int(rp)}" for rp in rps],
        date=np.zeros(n_ev, dtype=int),
        frequency=ingest._incremental_frequency([int(r) for r in rps]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(inten),
        fraction=sparse.csr_matrix((inten > 0).astype(float)),
    )
    haz.check()
    log.debug("aqueduct_hazard events=%d centroids=%d", n_ev, inten.shape[1])
    return haz


def rp_monotone_check(rps: list[float], depth: Any, tol_m: float = 1e-6) -> dict[str, Any]:
    """Sub check for a one-map-per-period product: depth must not fall as the period grows.

    Algorithm:
        For every cell $c$ with finite depths, a violation is any consecutive pair with
        $d_c(T_{k+1}) < d_c(T_k) - \\epsilon$ ($d$ in m, $\\epsilon$ = ``tol_m``). The share is
        the violating cells over the cells with any positive depth.

        ASCII: violation if d(T_{k+1}) < d(T_k) - tol; share = violating / wet cells.

    Args:
        rps: Return periods (ascending).
        depth: ``(len(rps), n_cells)`` depth in metres; NaN cells are skipped.
        tol_m: Tolerance $\\epsilon$ in metres.

    Returns:
        ``{"n_cells", "n_violating", "share_violating", "max_drop_m"}``.
    """
    d = np.asarray(depth, dtype=float)
    if d.shape[0] != len(rps):
        raise ValueError(f"depth has {d.shape[0]} maps for {len(rps)} periods")
    finite = np.isfinite(d).all(axis=0)
    d = d[:, finite]
    wet = (d > 0).any(axis=0)
    drop = (d[:-1] - d[1:]).max(axis=0) if d.shape[0] > 1 else np.zeros(d.shape[1])
    bad = wet & (drop > tol_m)
    return {
        "n_cells": int(wet.sum()),
        "n_violating": int(bad.sum()),
        "share_violating": float(bad.sum() / wet.sum()) if wet.any() else 0.0,
        "max_drop_m": float(drop[wet].max()) if wet.any() else 0.0,
    }
