"""Observed urban floods at asset points: the 서울시 침수흔적도 2010–2025 (validation only).

The 침수흔적도 is the City of Seoul's record of flooded areas after each storm, one
polygon layer per year (``<data root>/external/seoul_flood_traces/<year>/*.shp``, EPSG:5179,
공공누리 제1유형; 2015 and 2021 are not published). Each polygon is about a building or a
parcel. This module reads them next to an asset and reports which years flooded it and how
deep. It **never changes a hazard**: it validates the urban-pluvial map
(:class:`KoreaLocalPluvialAdapter`) and issues no verdict.

Depth: the column ``F_SHIM`` (metres, 0.01–1.8 in the 2010–2025 files). Its meaning is
**inferred** from the name (침수심) and the values; no field dictionary came with the files.
``F_AVR_HGT`` is *not* a depth and is never read. The columns differ between years (2022 adds
``OBJECTID``; 2024–2025 add ``PNU``); only ``F_SHIM`` and the geometry are used, and the year
is the folder name, not a column. Some files are CP949: a file that fails to decode is re-read
as CP949 and that is logged once at warning level (the other columns are not used, so a
mis-decoded text column would be harmless anyway).

Not like-for-like: the map is one 100-year event, the traces are every flood actually
observed in 2010–2025 (mostly small, frequent events, reported where people were affected).
A trace missing from the map is therefore not a map error, and a mapped-wet asset with no
trace is not a false alarm. That is why :func:`validation_summary` reports counts and ratios
and issues no verdict (no ``warn``).

Licence: the trace polygons are read in memory and never written; the data stays under the
data root and is not committed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

TRACE_CRS = 5179  # Korea 2000 / Unified CS — the traces' own CRS (metres)
DEPTH_COLUMN = "F_SHIM"
_warned_cp949: set[str] = set()


def trace_files(root: Path) -> list[tuple[int, Path]]:
    """``[(year, .shp path)]`` for every ``<root>/<year>/*.shp``, sorted by year then name."""
    out: list[tuple[int, Path]] = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and d.name.isdigit():
            out += [(int(d.name), p) for p in sorted(d.glob("*.shp"))]
    return out


def _read_year(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """``(polygons, depth_m)`` of one trace file in EPSG:5179; empty arrays if unusable.

    Only ``F_SHIM`` and the geometry are read. A decode failure retries as CP949 (warned
    once per file); a missing ``F_SHIM`` column gives no depth (NaN) but keeps the polygons.
    """
    import geopandas as gpd

    try:
        gdf = gpd.read_file(path, columns=[DEPTH_COLUMN])
    except UnicodeDecodeError:
        if str(path) not in _warned_cp949:
            _warned_cp949.add(str(path))
            log.warning("%s is not UTF-8; reading it as CP949", path.name)
        gdf = gpd.read_file(path, columns=[DEPTH_COLUMN], encoding="cp949")
    except Exception as exc:  # no F_SHIM (or an odd dbf): geometry only
        log.warning("%s: reading %s failed (%s); geometry only", path.name, DEPTH_COLUMN, exc)
        gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(TRACE_CRS)
    elif gdf.crs.to_epsg() != TRACE_CRS:
        gdf = gdf.to_crs(TRACE_CRS)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    depth = (
        np.asarray(gdf[DEPTH_COLUMN], dtype=float)
        if DEPTH_COLUMN in gdf
        else np.full(len(gdf), np.nan)
    )
    return np.asarray(gdf.geometry.values, dtype=object), depth


def region_coverage(sido_shp: Path, sido_name: str = "서울특별시") -> Any:
    """Lon/lat geometry of one SGIS 시도 (``SIDO_NM == sido_name``), for ``coverage=``.

    Raises:
        ValueError: ``sido_name`` is not in the file.
    """
    import geopandas as gpd

    gdf = gpd.read_file(sido_shp).to_crs(4326)
    sel = gdf[gdf["SIDO_NM"].astype(str) == sido_name]
    if sel.empty:
        raise ValueError(f"{sido_name!r} not found in {sido_shp.name}")
    return sel.geometry.union_all()


def observed_at_points(
    points_lonlat: Sequence[tuple[float, float]],
    root: Path,
    coverage: Any | None = None,
    buffer_m: float = 20.0,
) -> list[dict[str, Any]]:
    """Observed flood years and depth at each asset point.

    A trace "matches" a point when its polygon lies within ``buffer_m`` of the point
    (0 = the polygon must contain or touch it): a building-sized polygon beside an asset
    that is a few metres off its centroid still counts.

    Algorithm:
        $$m_{i,y} = \\{\\, k : d(p_i, P_{y,k}) \\le b \\,\\}, \\qquad
        D_i = \\max_{y,\\,k \\in m_{i,y}} h_{y,k}$$
        ASCII: matches(i, y) = polygons of year y within b metres of point i;
        max_depth(i) = largest F_SHIM among all matches.

        where $p_i$ (m, EPSG:5179) is the asset, $P_{y,k}$ the k-th polygon of year y,
        $b$ (m) = ``buffer_m``, $h$ (m) = ``F_SHIM``.

    One STRtree per year file is queried point by point set; layers are never merged or
    repaired. Points outside ``coverage`` are not compared at all (``years`` empty): a year
    file only describes Seoul, so an absent trace elsewhere is not evidence of no flood.

    Args:
        points_lonlat: ``(lon, lat)`` pairs in degrees (EPSG:4326).
        root: Folder of the ``<year>/*.shp`` layers.
        coverage: A lon/lat shapely geometry of the area the record covers; ``None`` means
            every point is covered.
        buffer_m: Match distance in metres.

    Returns:
        Per point ``{"covered": bool, "years": sorted [int], "max_depth_m": float | None,
        "n_traces": int}``.
    """
    import shapely
    from pyproj import Transformer

    n = len(points_lonlat)
    lon = np.array([p[0] for p in points_lonlat], dtype=float)
    lat = np.array([p[1] for p in points_lonlat], dtype=float)
    if coverage is None:
        covered = np.ones(n, dtype=bool)
    else:
        covered = np.asarray(shapely.contains_xy(coverage, lon, lat), dtype=bool)
    years: list[set[int]] = [set() for _ in range(n)]
    depth = np.full(n, np.nan)
    count = np.zeros(n, dtype=int)
    idx = np.flatnonzero(covered)
    if len(idx):
        xs, ys = Transformer.from_crs(4326, TRACE_CRS, always_xy=True).transform(lon[idx], lat[idx])
        pts = shapely.points(np.asarray(xs), np.asarray(ys))
        for year, path in trace_files(root):
            polys, h = _read_year(path)
            if len(polys) == 0:
                continue
            tree = shapely.STRtree(polys)
            q_pt, q_poly = tree.query(pts, predicate="dwithin", distance=float(buffer_m))
            for qi, pj in zip(q_pt.tolist(), q_poly.tolist(), strict=True):
                i = int(idx[qi])
                years[i].add(year)
                count[i] += 1
                if np.isfinite(h[pj]) and not (depth[i] >= h[pj]):
                    depth[i] = h[pj]
    return [
        {
            "covered": bool(covered[i]),
            "years": sorted(years[i]),
            "max_depth_m": None if np.isnan(depth[i]) else float(depth[i]),
            "n_traces": int(count[i]),
        }
        for i in range(n)
    ]


def validation_summary(
    mapped_wet: Iterable[bool], observed: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Counts of mapped-wet versus observed-flooded assets inside the record's coverage.

    Algorithm:
        $$POD = \\frac{n_{hit}}{n_{obs}}, \\qquad
        S = \\frac{n_{hit}}{n_{map}}$$
        ASCII: pod = n_hit / n_observed; share_mapped_observed = n_hit / n_mapped_wet

        where, over the covered assets only, $n_{obs}$ is the number with at least one trace,
        $n_{map}$ the number the map floods, and $n_{hit}$ the number that are both.

    Diagnostic only: a 100-y map against observed 2010–2025 events is not like-for-like (see
    the module docstring), so no threshold is applied and there is no ``warn`` key.

    Args:
        mapped_wet: Per asset, whether the map floods it (depth above 0).
        observed: The output of :func:`observed_at_points`, same order.

    Returns:
        ``n_covered``, ``n_observed``, ``n_mapped_wet``, ``n_hit``, ``pod`` and
        ``share_mapped_observed`` (None when the denominator is 0), and a ``note``.
    """
    wet = [bool(w) for w in mapped_wet]
    if len(wet) != len(observed):
        raise ValueError(f"{len(wet)} map values for {len(observed)} observed records")
    cov = [o["covered"] for o in observed]
    obs = [c and bool(o["years"]) for c, o in zip(cov, observed, strict=True)]
    mapped = [c and w for c, w in zip(cov, wet, strict=True)]
    n_cov, n_obs, n_map = sum(cov), sum(obs), sum(mapped)
    n_hit = sum(o and m for o, m in zip(obs, mapped, strict=True))
    return {
        "n_covered": n_cov,
        "n_observed": n_obs,
        "n_mapped_wet": n_map,
        "n_hit": n_hit,
        "pod": (n_hit / n_obs) if n_obs else None,
        "share_mapped_observed": (n_hit / n_map) if n_map else None,
        "note": (
            "diagnostic only: the map is one 100-year event, the 서울시 침수흔적도 2010–2025 "
            "records every observed flood, so the two are not like-for-like and no verdict "
            "is issued. pod = share of observed-flooded assets the map also floods; "
            "share_mapped_observed = share of map-flooded assets with an observed trace. "
            "Only assets inside 서울특별시 count."
        ),
    }
