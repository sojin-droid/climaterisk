"""Case twin: Pohang Naengcheon, Typhoon Hinnamnor 2022-09-06 (rain-on-grid, 30 m).

A 2D local-inertial (:mod:`climaterisk_worker.twin.hydraulics`) rain-on-grid flood of the
Naengcheon catchment. Rain: the published Pohang ASOS maximum depths per duration become a
nested alternating-block hyetograph (main method); a Seoul-gauge shape scaled to the published
9-h depth is a diagnostic check only. Everything that is assumed is listed in
``assets/libraries/case_pohang_spec.json`` (``assumptions``); the result carries the badge
"미검증" and no money.

Algorithm:
    Nested alternating block. With depth $P(D)$ (mm) of duration $D$ (min), $P$ log-log
    interpolated between the listed durations, $P(D) = P(60)\\,D/60$ below 60 min, the block
    of the $k$-th step of length $\\Delta$ is
    $$b_k = P(k\\Delta) - P((k-1)\\Delta),\\quad k = 1..N,\\ N\\Delta = D_{max}$$
    and the blocks, ordered from the largest, are placed in the middle, then alternately to
    the right and to the left. The $k$ largest blocks are contiguous, so the maximum rain over
    any window of $k\\Delta$ minutes is $\\sum_{i \\le k} b_i = P(k\\Delta)$.
    ASCII: b_k = P(k*dt) - P((k-1)*dt); largest block in the middle, next right, next left;
    max window sum over k blocks = P(k*dt).

    Catchment: D8 steepest descent (as :mod:`runon`), the catchment is every cell whose path
    reaches the outlet cell.

    Channel burn (rectangular section, wide-channel Manning law of the solver):
    $$W = a\\sqrt{A},\\quad Q_{bf}(A) = Q_{ref}\\,(A/A_{ref})^{0.75},\\quad
      d = s\\Big(\\frac{Q_{bf}\\,n}{W\\sqrt{S}}\\Big)^{3/5}$$
    and the scale $s$ is chosen so that the backwater profile
    $\\eta_{k-1} = \\eta_k + S_f \\Delta s$, $S_f = (Q n / (W h^{5/3}))^2$, from the sea
    ($\\eta = 0$) carries $Q_{ref}$ at the bridge without leaving the banks.
    ASCII: W = a*sqrt(A); Qbf = Qref*(A/Aref)^0.75; d = s*(Qbf*n/(W*sqrt(S)))^(3/5);
    eta[k-1] = eta[k] + Sf*ds, Sf = (Q*n/(W*h^(5/3)))^2.

    $A$ upstream area (km^2), $W$ width (m), $Q$ discharge (m^3 s^-1), $n$ Manning
    roughness (s m^-1/3), $S$ bed slope (-), $d$ burn depth (m), $\\eta$ water surface (m),
    $h$ depth (m), $a$ = 11.4 m km^-1.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from climaterisk_worker.twin import hydraulics, landcover, pluvial, runon, site, validate

logger = logging.getLogger(__name__)

ASSETS_SPEC = Path(__file__).resolve().parents[3] / "assets" / "libraries" / "case_pohang_spec.json"


def load_case_spec(path: str | Path = ASSETS_SPEC) -> dict[str, Any]:
    """Read the case spec JSON (every assumption lives there).

    Args:
        path: Spec file; defaults to ``assets/libraries/case_pohang_spec.json``.

    Returns:
        The parsed spec.
    """
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


# ---------------------------------------------------------------------------------------------
# rain
# ---------------------------------------------------------------------------------------------


def _curve(depth_duration_mm: Mapping[str, float]) -> tuple[np.ndarray, np.ndarray]:
    """Sorted durations (min) and depths (mm) of a depth-duration table."""
    pairs = sorted((float(k), float(v)) for k, v in depth_duration_mm.items())
    return np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])


def depth_at(duration_min: float, durations: np.ndarray, depths: np.ndarray) -> float:
    """Maximum rain depth (mm) over ``duration_min`` from a depth-duration curve.

    Algorithm:
        Between listed durations $D_a < D < D_b$ the depth is log-log interpolated,
        $$P = P_a (D/D_a)^{b},\\quad b = \\ln(P_b/P_a)/\\ln(D_b/D_a);$$
        below the first duration the depth grows linearly, $P = P_1 D/D_1$ (the first
        duration's depth spread uniformly; an assumption), above the last it stays $P_n$.
        ASCII: P = Pa*(D/Da)^b, b = ln(Pb/Pa)/ln(Db/Da); P = P1*D/D1 below D1.

    Args:
        duration_min: Duration $D$ (min).
        durations: Listed durations (min), increasing.
        depths: Listed depths (mm).

    Returns:
        Depth (mm).
    """
    d = float(duration_min)
    if d <= 0.0:
        return 0.0
    if d <= durations[0]:
        return float(depths[0] * d / durations[0])
    if d >= durations[-1]:
        return float(depths[-1])
    k = int(np.searchsorted(durations, d, side="right")) - 1
    b = math.log(depths[k + 1] / depths[k]) / math.log(durations[k + 1] / durations[k])
    return float(depths[k] * (d / durations[k]) ** b)


def nested_hyetograph(depth_duration_mm: Mapping[str, float], dt_min: float) -> np.ndarray:
    """Alternating-block hyetograph nested on a depth-duration table.

    Algorithm:
        $b_k = P(k\\Delta) - P((k-1)\\Delta)$ for $k = 1..N$ ($N\\Delta$ = longest listed
        duration), blocks sorted from the largest and placed in the middle, then alternately
        to the right and to the left. The $k$ largest blocks are contiguous, so the largest
        sum over $k$ consecutive blocks is $P(k\\Delta)$ for every $k$ (exact for a concave
        $P$; the listed durations are then reproduced to round-off).
        ASCII: b_k = P(k*dt) - P((k-1)*dt); max over windows of k blocks = P(k*dt).

    Args:
        depth_duration_mm: ``{"60": mm, ...}`` maximum depth per duration (min as keys).
        dt_min: Block length $\\Delta$ (min); every listed duration must be a multiple of it.

    Returns:
        Rain depth per step (mm), shape ``(N,)``, summing to the longest depth.

    Raises:
        ValueError: If a duration is not a multiple of ``dt_min``.
    """
    durations, depths = _curve(depth_duration_mm)
    for d in durations:
        if abs(d / dt_min - round(d / dt_min)) > 1e-9:
            raise ValueError(f"duration {d} min is not a multiple of dt_min {dt_min}")
    n = round(float(durations[-1]) / dt_min)
    cum = np.array([depth_at(k * dt_min, durations, depths) for k in range(n + 1)])
    blocks = np.sort(np.diff(cum))[::-1]  # largest first
    out = np.zeros(n)
    centre = (n - 1) // 2
    slot = centre
    out[slot] = blocks[0]
    right, left = centre + 1, centre - 1
    for m in range(1, n):
        # right first, but never run off an end (the longer side takes the surplus)
        go_right = m % 2 == 1
        if go_right and right >= n:
            go_right = False
        if not go_right and left < 0:
            go_right = True
        if go_right:
            out[right] = blocks[m]
            right += 1
        else:
            out[left] = blocks[m]
            left -= 1
    return out


def scaled_shape(cum_fraction: Any, total_mm: float) -> np.ndarray:
    """Cumulative rain (mm) of a normalised shape scaled to ``total_mm``.

    Algorithm:
        $P_k = F_k P_{tot}$ with $F$ non-decreasing from 0 to 1.
        ASCII: P = F * total.

    Args:
        cum_fraction: Cumulative fraction $F$ (-), non-decreasing, from 0 to 1.
        total_mm: Storm total $P_{tot}$ (mm).

    Returns:
        Cumulative rain (mm), same length.

    Raises:
        ValueError: If ``cum_fraction`` is not non-decreasing from 0 to 1.
    """
    f = np.asarray(cum_fraction, dtype=float)
    if f.ndim != 1 or f.size < 2:
        raise ValueError("cum_fraction must be a 1-D array of at least two values")
    if abs(f[0]) > 1e-9 or abs(f[-1] - 1.0) > 1e-9 or bool(np.any(np.diff(f) < 0.0)):
        raise ValueError("cum_fraction must be non-decreasing from 0 to 1")
    return f * float(total_mm)


def rain_spec(increments_mm: Any, dt_min: float, cn_default: float) -> dict[str, Any]:
    """A ``pluvial``-style rain spec (equally spaced cumulative fractions) for a hyetograph.

    Args:
        increments_mm: Rain depth per step (mm).
        dt_min: Step length (min).
        cn_default: Curve number stored as ``rain.cn_paved`` (not used by the case run).

    Returns:
        ``{"rain": {...}}`` accepted by :func:`pluvial.hyetograph` and :func:`pluvial.excess_rate`.
    """
    p = np.asarray(increments_mm, dtype=float)
    total = float(p.sum())
    return {
        "rain": {
            "total_mm": total,
            "duration_min": float(p.size) * float(dt_min),
            "cumulative_fraction": [0.0, *(np.cumsum(p) / total).tolist()],
            "cn_paved": float(cn_default),
        }
    }


def seoul_check_increments(check: Mapping[str, Any]) -> np.ndarray:
    """Check hyetograph: Seoul gauge shape of the 540-min maximum, scaled to the Pohang total.

    The Seoul rain (공공누리 2유형, internal check only) is read from the local files, the
    window of ``duration_min`` with the largest area-mean rain is found, normalised to a
    cumulative fraction and scaled to ``total_mm``. Nothing is written out.

    Args:
        check: ``spec["rain"]["check"]``.

    Returns:
        Rain depth per step (mm), summing to ``total_mm``.
    """
    import pandas as pd

    year, month = (int(v) for v in str(check["month"]).split("-"))
    step = float(check["step_min"])
    df = validate.read_gauge_file(year, month)
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthBegin(1)
    series = validate.gauge_series(df, check["gauges"], start, end, step)
    p = series.to_numpy(dtype=float)
    w = round(float(check["duration_min"]) / step)
    csum = np.concatenate([[0.0], np.cumsum(p)])
    window = csum[w:] - csum[:-w]
    k = int(np.argmax(window))
    seg = p[k : k + w]
    cum_fraction = np.concatenate([[0.0], np.cumsum(seg) / seg.sum()])
    cum = scaled_shape(cum_fraction, float(check["total_mm"]))
    logger.info("check shape: %d steps scaled to %.1f mm", w, cum[-1])
    return np.diff(cum)


# ---------------------------------------------------------------------------------------------
# terrain and catchment
# ---------------------------------------------------------------------------------------------


def delineate(dem: Any, outlet_rc: tuple[int, int]) -> np.ndarray:
    """Cells whose D8 steepest-descent path reaches ``outlet_rc`` (the outlet included).

    Algorithm:
        D8 as in :func:`runon.d8_receivers` (steepest drop, diagonal over $\\sqrt2$, ties to
        the first of N, NE, E, SE, S, SW, W, NW; no receiver = pit). A receiver is strictly
        lower, so visiting the cells from the lowest up, $m_c = [c = o] \\lor m_{r(c)}$.
        ASCII: mask[c] = (c == outlet) or mask[receiver(c)], cells visited in ascending z.

    Args:
        dem: Ground elevation (m), shape ``(ny, nx)``.
        outlet_rc: ``(row, col)`` of the outlet cell.

    Returns:
        Boolean raster, True on the catchment.
    """
    z = np.asarray(dem, dtype=float)
    ny, nx = z.shape
    recv = runon.d8_receivers(z).ravel()
    mask = np.zeros(ny * nx, dtype=bool)
    mask[int(outlet_rc[0]) * nx + int(outlet_rc[1])] = True
    order = np.argsort(z.ravel(), kind="stable")
    for c in order.tolist():
        r = recv[c]
        if r >= 0 and mask[r]:
            mask[c] = True
    return mask.reshape(ny, nx)


def fill_depressions(z: np.ndarray, epsilon: float = 1e-4) -> np.ndarray:
    """Pit-filled ground (priority flood, Barnes et al. 2014) with a tiny gradient on flats.

    Algorithm:
        Cells are flooded from the raster border upward: a cell is raised to at least
        $z_{nb} + \\epsilon$ of the lowest already-flooded neighbour.
        ASCII: z_fill = max(z, z_neighbour_flooded + eps), processed lowest-first.

    Args:
        z: Ground (m), shape ``(ny, nx)``.
        epsilon: Minimum drop per cell on filled flats (m).

    Returns:
        Filled ground (m); every interior cell has a lower neighbour.
    """
    ny, nx = z.shape
    filled = z.astype(float).copy()
    closed = np.zeros(z.shape, dtype=bool)
    heap: list[tuple[float, int, int]] = []
    for i in range(ny):
        for j in (0, nx - 1):
            if not closed[i, j]:
                closed[i, j] = True
                heapq.heappush(heap, (filled[i, j], i, j))
    for j in range(nx):
        for i in (0, ny - 1):
            if not closed[i, j]:
                closed[i, j] = True
                heapq.heappush(heap, (filled[i, j], i, j))
    moves = ((-1, 0), (1, 0), (0, 1), (0, -1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    while heap:
        level, i, j = heapq.heappop(heap)
        for di, dj in moves:
            a, b = i + di, j + dj
            if 0 <= a < ny and 0 <= b < nx and not closed[a, b]:
                closed[a, b] = True
                filled[a, b] = max(filled[a, b], level + epsilon)
                heapq.heappush(heap, (filled[a, b], a, b))
    return filled


def mosaic_dem(path: Path, bbox: tuple[float, float, float, float]) -> Path:
    """GeoTIFF mosaic of the Copernicus GLO-30 tiles covering ``bbox`` (cached at ``path``).

    Args:
        path: Output GeoTIFF (EPSG:4326, float32).
        bbox: ``(west, south, east, north)`` in degrees.

    Returns:
        ``path``.
    """
    import rasterio
    from rasterio.merge import merge

    from climaterisk import paths

    if path.exists():
        return path
    west, south, east, north = bbox
    tiles = []
    for lat in range(math.floor(south), math.floor(north) + 1):
        for lon in range(math.floor(west), math.floor(east) + 1):
            name = f"Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif"
            tile = paths.external_dir("copernicus_dem", name)
            if tile.exists():
                tiles.append(tile)
    srcs = [rasterio.open(t) for t in tiles]
    try:
        pad = 0.01
        arr, transform = merge(srcs, bounds=(west - pad, south - pad, east + pad, north + pad))
    finally:
        for s in srcs:
            s.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[1],
        width=arr.shape[2],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(arr[0].astype("float32"), 1)
    logger.info("DEM mosaic %s from %d tiles", arr.shape, len(tiles))
    return path


def _cell_of(grid: site.SiteGrid, lon: float, lat: float) -> tuple[int, int]:
    """``(row, col)`` of the grid cell holding ``(lon, lat)`` (may lie outside the raster)."""
    i = math.floor((lat - grid.south) * site.M_PER_DEG / grid.cell_m)
    j = math.floor((lon - grid.west) * grid.lon_m_per_deg / grid.cell_m)
    return int(i), int(j)


def _snap_outlet(
    acc: np.ndarray, grid: site.SiteGrid, lon: float, lat: float, radius: int
) -> tuple[int, int]:
    """Cell of the largest D8 accumulation within ``radius`` cells of ``(lon, lat)``."""
    i, j = _cell_of(grid, lon, lat)
    i0, i1 = max(i - radius, 0), min(i + radius + 1, acc.shape[0])
    j0, j1 = max(j - radius, 0), min(j + radius + 1, acc.shape[1])
    sub = acc[i0:i1, j0:j1]
    k = np.unravel_index(int(sub.argmax()), sub.shape)
    return int(i0 + k[0]), int(j0 + k[1])


def _dilate(mask: np.ndarray, cells: int) -> np.ndarray:
    """Binary dilation of ``mask`` by a square of radius ``cells``."""
    from scipy.ndimage import maximum_filter

    return np.asarray(maximum_filter(mask, size=2 * cells + 1, mode="constant"), dtype=bool)


def build_terrain(spec: dict[str, Any], cell_m: float | None = None) -> dict[str, Any]:
    """Bare-earth ground, catchment and domain of the Naengcheon case.

    Reads OSM buildings, mosaics the GLO-30 tiles, removes the buildings and interpolates the
    ground (:func:`site.bare_earth`), fills the pits, delineates the catchment from the outlet
    and adds a dry floodplain buffer and the sea cells (see ``spec["domain"]``).

    Args:
        spec: Case spec.
        cell_m: Cell size (m); default ``spec["domain"]["cell_m"]``.

    Returns:
        Dict with ``grid``, ``ground``, ``filled``, ``acc`` (D8 cells), ``outlet_rc``,
        ``mask`` (catchment), ``domain`` (catchment + buffer + sea), ``sea`` (all sea cells of
        the domain), ``ground_bare`` (bare earth, m), ``buildings`` (GeoDataFrame) and
        ``catchment_area_km2``.
    """
    from climaterisk import paths

    dom = spec["domain"]
    cell = float(cell_m if cell_m is not None else dom["cell_m"])
    bbox = tuple(float(v) for v in dom["bbox"])
    grid = site.site_grid(bbox, cell)  # type: ignore[arg-type]
    t0 = time.time()
    seed = {"site": {"floor_to_floor_m": 3.3, "default_levels": {"yes": 1}}}
    buildings = site.read_buildings(paths.osm_dir("south-korea-261013.osm.pbf"), bbox, seed)  # type: ignore[arg-type]
    dsm_mask = site.building_mask(grid, buildings[~buildings["underground"]])
    mosaic = mosaic_dem(paths.derived_dir("case_twins", "pohang", "dem_mosaic.tif"), bbox)  # type: ignore[arg-type]
    ground = site.bare_earth(mosaic, grid, dsm_mask)
    filled = fill_depressions(ground)
    fill_above = float(dom["fill_above_m"])
    ground_bare = ground
    ground = np.where(ground_bare > fill_above, filled, ground_bare)
    recv = runon.d8_receivers(filled)
    acc = runon._accumulate(filled, recv)
    o = spec["outlet"]
    outlet = _snap_outlet(acc, grid, float(o["lon"]), float(o["lat"]), 6)
    mask = delineate(filled, outlet)
    area = float(mask.sum()) * cell**2 / 1e6
    logger.info(
        "terrain %s, %d buildings, catchment %.1f km2 (%.0f s)",
        ground.shape,
        len(buildings),
        area,
        time.time() - t0,
    )

    # sea: ground at datum, connected to the outlet cell
    from scipy.ndimage import label

    low = ground <= float(dom["sea_ground_max_m"])
    lab, _ = label(low)
    sea_label = lab[outlet]
    sea_all = lab == sea_label if sea_label > 0 else np.zeros_like(low)
    near = _dilate(mask, max(round(float(dom["floodplain_buffer_m"]) / cell), 1))
    buffer = near & ~mask & ((ground <= float(dom["floodplain_buffer_max_ground_m"])) | sea_all)
    land = (mask | buffer) & ~sea_all
    # sea cells touching the land of the domain (one ring is the boundary)
    sea = sea_all & _dilate(land, 1) & near
    domain = land | sea
    return {
        "grid": grid,
        "ground": ground,
        "ground_bare": ground_bare,
        "filled": filled,
        "acc": acc,
        "outlet_rc": outlet,
        "mask": mask & ~sea_all,
        "domain": domain,
        "sea": sea,
        "buildings": buildings,
        "catchment_area_km2": area,
    }


# ---------------------------------------------------------------------------------------------
# channel
# ---------------------------------------------------------------------------------------------


def _line_cells(
    coords: np.ndarray, grid: site.SiteGrid, shape: tuple[int, int]
) -> list[tuple[int, int]]:
    """4-connected cells touched by a lon/lat polyline (consecutive duplicates removed)."""
    cells: list[tuple[int, int]] = []

    def add(i: int, j: int) -> None:
        if 0 <= i < shape[0] and 0 <= j < shape[1] and (not cells or cells[-1] != (i, j)):
            cells.append((i, j))

    prev: tuple[int, int] | None = None
    for a, b in pairwise(coords):
        steps = max(int(math.hypot(*(b - a)) * site.M_PER_DEG / (grid.cell_m * 0.25)), 1)
        for s in range(steps + 1):
            p = a + (b - a) * (s / steps)
            i, j = _cell_of(grid, float(p[0]), float(p[1]))
            if prev is not None and i != prev[0] and j != prev[1]:
                add(prev[0], j)  # step through a side cell: faces connect, corners do not
            add(i, j)
            prev = (i, j)
    return cells


def _stage_profile(
    bed: np.ndarray,
    width: np.ndarray,
    q: np.ndarray,
    n: float,
    eta_end: float,
    ds: float,
) -> np.ndarray:
    """Backwater water surface along a channel (upstream to downstream), from ``eta_end``.

    Algorithm:
        Wide-channel friction slope of the solver, $S_f = (Q n / (W h^{5/3}))^2$, stepped
        upstream from the downstream end: $\\eta_{k-1} = \\eta_k + S_f(h_k)\\,\\Delta s$
        (velocity head neglected).
        ASCII: eta[k-1] = eta[k] + (Q*n/(W*h^(5/3)))^2 * ds.

    Args:
        bed: Bed elevation (m), upstream first.
        width: Section width (m).
        q: Discharge (m^3 s^-1).
        n: Manning n.
        eta_end: Water surface at the downstream end (m).
        ds: Cell spacing along the channel (m).

    Returns:
        Water surface (m), same length.
    """
    eta = np.empty(bed.size)
    eta[-1] = max(eta_end, bed[-1] + 0.05)
    for k in range(bed.size - 1, 0, -1):
        h = max(eta[k] - bed[k], 0.05)
        sf = (q[k] * n / (width[k] * h ** (5.0 / 3.0))) ** 2
        eta[k - 1] = max(eta[k] + sf * ds, bed[k - 1] + 0.05)
    return eta


def _read_channel_lines(spec: dict[str, Any], bbox: tuple[float, float, float, float]) -> Any:
    """OSM waterway lines (river, stream) of ``bbox`` with their names."""
    import pyogrio

    from climaterisk import paths

    lines = pyogrio.read_dataframe(
        str(paths.osm_dir("south-korea-261013.osm.pbf")),
        layer="lines",
        bbox=bbox,
        columns=["name", "waterway"],
    )
    keep = lines["waterway"].isin(list(spec["channel"]["waterways"]))
    return lines[keep & ~lines.geometry.isna()].reset_index(drop=True)


def burn_channels(spec: dict[str, Any], terrain: dict[str, Any]) -> dict[str, Any]:
    """Burn the OSM waterway lines of the catchment into the ground.

    See the module docstring (Algorithm) and assumptions A12 and A13 of the spec: width
    $W = a\\sqrt A$, bed = running minimum of the ground downstream minus the burn depth,
    depth from the Manning normal-flow law so that the section carries ``channel_capacity_m3s``
    at 냉천교 (scale $s = 1$). The backwater capacity of the main stem from 냉천교 to the sea
    (sea at datum, free overfall at the mouth) is reported as a diagnostic only.

    Args:
        spec: Case spec.
        terrain: Result of :func:`build_terrain`.

    Returns:
        Dict with ``bed`` (burnt ground, m), ``channel`` (boolean raster of burnt cells),
        ``depth_burn`` (burn depth raster, m), ``stem`` (dict with ``cells`` (i, j) upstream
        first, ``bed``, ``ground``, ``width``) and ``report`` (scalars of the calculation).
    """
    cfg = spec["channel"]
    grid: site.SiteGrid = terrain["grid"]
    ground: np.ndarray = terrain["ground"]
    domain: np.ndarray = terrain["domain"]
    sea: np.ndarray = terrain["sea"]
    mask: np.ndarray = terrain["mask"]
    cell = grid.cell_m
    n_man = float(spec["hydraulic"]["manning_n"])
    q_cap = float(spec["channel_capacity_m3s"])
    sea_level = float(spec["domain"]["sea_level_m"])
    bbox = tuple(float(v) for v in spec["domain"]["bbox"])
    lines = _read_channel_lines(spec, bbox)  # type: ignore[arg-type]
    # the OSM line can sit a cell beside the DSM valley: take the largest D8 area within 2 cells
    from scipy.ndimage import maximum_filter

    area_km2 = maximum_filter(terrain["acc"].astype(float), size=5) * cell**2 / 1e6

    selected: list[tuple[str, list[tuple[int, int]]]] = []
    for name, geom in zip(lines["name"], lines.geometry, strict=True):
        geoms = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
        for g in geoms:
            cells = [
                c
                for c in _line_cells(np.asarray(g.coords)[:, :2], grid, ground.shape)
                if domain[c] and not sea[c]
            ]
            if len(cells) < 2:
                continue
            share = float(np.mean([mask[c] for c in cells]))
            nm = str(name) if name is not None and str(name) != "nan" else ""
            if nm in cfg["names_always"] or share >= float(cfg["min_share_in_catchment"]):
                selected.append((nm, cells))
    logger.info("channel: %d lines selected", len(selected))

    profiles: list[dict[str, Any]] = []
    for nm, cells in selected:
        idx = np.array(cells)
        g_line = ground[idx[:, 0], idx[:, 1]]
        if g_line[: min(5, g_line.size)].mean() < g_line[-min(5, g_line.size) :].mean():
            idx = idx[::-1]
            g_line = g_line[::-1]
        zc = np.minimum.accumulate(g_line)
        a_km2 = area_km2[idx[:, 0], idx[:, 1]]
        w_m = float(cfg["width_coeff_m_per_km"]) * np.sqrt(np.maximum(a_km2, 0.0))
        radius = np.maximum(np.round((w_m / cell - 1.0) / 2.0), 0).astype(int)
        width = (2 * radius + 1) * cell
        win = int(cfg["slope_window_cells"])
        k = np.arange(idx.shape[0])
        up = zc[np.maximum(k - win, 0)]
        dn = zc[np.minimum(k + win, k.size - 1)]
        span = (np.minimum(k + win, k.size - 1) - np.maximum(k - win, 0)).clip(min=1) * cell
        slope = np.maximum((up - dn) / span, float(cfg["slope_min"]))
        profiles.append(
            {
                "name": nm,
                "idx": idx,
                "zc": zc,
                "area": a_km2,
                "radius": radius,
                "width": width,
                "slope": slope,
            }
        )

    # reference: the main stem and the bridge used as the capacity section
    stem = max(
        (p for p in profiles if p["name"] == cfg["name_main"]),
        key=lambda p: len(p["idx"]),
    )
    bridge_ij = terrain["points"]["냉천교"]["ij"] if "points" in terrain else None
    if bridge_ij is None:
        k_ref = int(np.argmin(np.abs(stem["area"] - 0.97 * stem["area"].max())))
    else:
        d2 = (stem["idx"][:, 0] - bridge_ij[0]) ** 2 + (stem["idx"][:, 1] - bridge_ij[1]) ** 2
        k_ref = int(np.argmin(d2))
    # the stem ends where it first touches the sea; the mouth section is the sea around that cell
    sea_ring = _dilate(sea, 1)
    hit = np.flatnonzero(sea_ring[stem["idx"][:, 0], stem["idx"][:, 1]])
    if hit.size:
        for key in ("idx", "zc", "area", "radius", "width", "slope"):
            stem[key] = stem[key][: int(hit[0]) + 1]
    k_ref = min(k_ref, stem["idx"].shape[0] - 1)
    mouth_rc = (int(stem["idx"][-1, 0]), int(stem["idx"][-1, 1]))
    rr = max(round(float(spec["domain"]["sea_open_radius_m"]) / cell), 1)
    ii, jj = np.indices(ground.shape)
    mouth_sea = sea & (np.abs(ii - mouth_rc[0]) <= rr) & (np.abs(jj - mouth_rc[1]) <= rr)
    a_ref = float(stem["area"][k_ref])
    expo = float(cfg["capacity_exponent"])
    d_min, d_max = float(cfg["depth_min_m"]), float(cfg["depth_max_m"])

    def depths(p: dict[str, Any], scale: float) -> np.ndarray:
        q_bf = q_cap * (np.maximum(p["area"], 1e-3) / a_ref) ** expo
        d_norm = (q_bf * n_man / (p["width"] * np.sqrt(p["slope"]))) ** 0.6
        return np.clip(scale * d_norm, d_min, d_max)

    def bed_of(p: dict[str, Any], scale: float) -> np.ndarray:
        return np.minimum.accumulate(p["zc"] - depths(p, scale))

    land_ok = ground[stem["idx"][:, 0], stem["idx"][:, 1]] > 0.5
    land_ok[:k_ref] = False  # the capacity section is the reach from the bridge to the sea
    q_stem = q_cap * (np.maximum(stem["area"], 1e-3) / a_ref) ** expo

    ny, nx = ground.shape

    def paint(scale: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Burnt bed, channel mask and burn depth for a depth scale."""
        bed = ground.copy()
        channel = np.zeros(ground.shape, dtype=bool)
        depth_burn = np.zeros(ground.shape)
        for p in profiles:
            bed_line = bed_of(p, scale)
            for (i, j), r, b in zip(
                p["idx"].tolist(), p["radius"].tolist(), bed_line.tolist(), strict=True
            ):
                i0, i1 = max(i - r, 0), min(i + r + 1, ny)
                j0, j1 = max(j - r, 0), min(j + r + 1, nx)
                block = bed[i0:i1, j0:j1]
                ok = domain[i0:i1, j0:j1] & ~sea[i0:i1, j0:j1]
                new = np.where(ok, np.minimum(block, b), block)
                depth_burn[i0:i1, j0:j1] = np.where(
                    ok,
                    np.maximum(depth_burn[i0:i1, j0:j1], ground[i0:i1, j0:j1] - new),
                    depth_burn[i0:i1, j0:j1],
                )
                bed[i0:i1, j0:j1] = new
                channel[i0:i1, j0:j1] |= ok
        bed[sea] = sea_level  # sea cells are flat at datum
        # the channel enters the sea through an estuary as deep as its last section: on the 30 m
        # grid a sea cell at datum next to the channel would be a 0 m sill that holds it back
        bed[mouth_sea] = np.minimum(bed[mouth_sea], bed_of(stem, scale)[-1])
        return bed, channel, depth_burn

    scale = 1.0  # Manning normal depth for the reference discharge (no further tuning)
    bed, channel, depth_burn = paint(scale)
    d_stem = depths(stem, scale)
    bed_stem = bed_of(stem, scale)
    eta_end = sea_level
    eta = _stage_profile(bed_stem, stem["width"], q_stem, n_man, eta_end, cell)
    # capacity check: largest multiple of the reference discharge that stays in the banks
    lo_q, hi_q = 0.2, 3.0
    for _ in range(18):
        mid = 0.5 * (lo_q + hi_q)
        head = sea_level
        eta_m = _stage_profile(bed_stem, stem["width"], q_stem * mid, n_man, head, cell)
        if float(np.percentile((stem["zc"] - eta_m)[land_ok], 5)) < 0.0:
            hi_q = mid
        else:
            lo_q = mid
    report = {
        "n_lines": len(profiles),
        "n_channel_cells": int(channel.sum()),
        "mouth_cell": list(mouth_rc),
        "reference_cell": [int(stem["idx"][k_ref, 0]), int(stem["idx"][k_ref, 1])],
        "reference_area_km2": a_ref,
        "reference_width_m": float(stem["width"][k_ref]),
        "reference_slope": float(stem["slope"][k_ref]),
        "reference_depth_m": float(d_stem[k_ref]),
        "reference_depth_normal_m": float(
            (q_cap * n_man / (stem["width"][k_ref] * math.sqrt(stem["slope"][k_ref]))) ** 0.6
        ),
        "depth_scale": scale,
        "reference_depth_unscaled_m": float(depths(stem, 1.0)[k_ref]),
        "depth_min_m": float(d_stem.min()),
        "depth_max_m": float(d_stem.max()),
        "backwater_capacity_m3s": q_cap * (lo_q + hi_q) / 2.0,
        "freeboard_q05_m": float(np.percentile((stem["zc"] - eta)[land_ok], 5)),
        "stem_cells": int(stem["idx"].shape[0]),
        "manning_n": n_man,
    }
    logger.info(
        "burn: scale %.2f, depth at bridge %.1f m, backwater capacity %.0f m3/s",
        scale,
        report["reference_depth_m"],
        report["backwater_capacity_m3s"],
    )
    return {
        "bed": bed,
        "mouth_sea": mouth_sea,
        "mouth_rc": mouth_rc,
        "channel": channel,
        "depth_burn": depth_burn,
        "stem": {
            "idx": stem["idx"],
            "bed": bed_stem,
            "ground": stem["zc"],
            "width": stem["width"],
            "radius": stem["radius"],
            "k_ref": k_ref,
        },
        "report": report,
    }


# ---------------------------------------------------------------------------------------------
# points
# ---------------------------------------------------------------------------------------------


def find_points(spec: dict[str, Any], terrain: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Report points from OSM: bridges, POSCO centroid and the 인덕동 apartment complex.

    Args:
        spec: Case spec.
        terrain: Result of :func:`build_terrain`.

    Returns:
        ``{name: {"lon", "lat", "ij", ...}}``; the apartment has also ``cells`` (every cell
        of its polygon inside the domain) and the bridges are matched to a channel cell later.
    """
    import pyogrio

    from climaterisk import paths

    grid: site.SiteGrid = terrain["grid"]
    bbox = tuple(float(v) for v in spec["domain"]["bbox"])
    osm = str(paths.osm_dir("south-korea-261013.osm.pbf"))
    lines = pyogrio.read_dataframe(osm, layer="lines", bbox=bbox, columns=["name", "highway"])
    polys = pyogrio.read_dataframe(
        osm, layer="multipolygons", bbox=bbox, columns=["name", "landuse", "building"]
    )
    pts: dict[str, dict[str, Any]] = {}
    for key, name in spec["points"]["bridge_names"].items():
        sel = lines[(lines["name"] == name) & lines.geometry.notna()]
        if sel.empty:
            raise ValueError(f"OSM way {name} not found")
        c = sel.geometry.union_all().centroid
        pts[key] = {"lon": float(c.x), "lat": float(c.y), "ij": _cell_of(grid, c.x, c.y)}
    posco = polys[(polys["name"] == spec["points"]["posco_name"]) & polys.geometry.notna()]
    posco_cells = site.building_mask(grid, posco) & terrain["domain"] & (terrain["ground"] > 0.5)
    ij_all = np.argwhere(posco_cells)
    if ij_all.size == 0:
        raise ValueError("no POSCO land cell inside the model domain")
    c = posco.geometry.union_all().centroid  # polygon centroid (may lie on water)
    ci = np.array(_cell_of(grid, c.x, c.y))
    nearest = ij_all[int(np.argmin(((ij_all - ci) ** 2).sum(axis=1)))]
    lon_c = grid.west + (nearest[1] + 0.5) * grid.cell_m / grid.lon_m_per_deg
    lat_c = grid.south + (nearest[0] + 0.5) * grid.cell_m / site.M_PER_DEG
    pts["POSCO"] = {
        "lon": float(lon_c),
        "lat": float(lat_c),
        "ij": (int(nearest[0]), int(nearest[1])),
        "cells": ij_all,
        "polygon_centroid": [float(c.x), float(c.y)],
    }
    for apt_key, apt_name in spec["points"]["apartments"].items():
        apt = polys[(polys["name"] == apt_name) & polys.geometry.notna()]
        if apt.empty:
            raise ValueError(f"apartment polygon {apt_name} not found in OSM")
        c = apt.geometry.union_all().centroid
        pts[apt_key] = {"lon": float(c.x), "lat": float(c.y), "ij": _cell_of(grid, c.x, c.y)}
        cells_mask = site.building_mask(grid, apt)
        pts[apt_key]["cells"] = np.argwhere(cells_mask & terrain["domain"])
    return pts


# ---------------------------------------------------------------------------------------------
# storm run
# ---------------------------------------------------------------------------------------------


class _Recorder:
    """Per-step recorder: mouth discharge, point depths and channel overtopping."""

    def __init__(
        self,
        faces: dict[str, np.ndarray],
        cell_m: float,
        points: dict[str, tuple[int, int]],
        bed: np.ndarray,
        bank: dict[str, float],
        sample_s: float,
        classes: dict[str, np.ndarray] | None = None,
        snapshots: Any | None = None,
    ) -> None:
        self.classes = classes or {}
        self.snapshots = snapshots  # optional object with ``offer(t, h)`` (cropped grid)
        self.fr_samples: list[tuple[float, dict[str, tuple[int, int]]]] = []
        self.faces = faces
        self.cell_m = cell_m
        self.names = list(points)
        self.rows = np.array([points[k][0] for k in self.names])
        self.cols = np.array([points[k][1] for k in self.names])
        self.bed_pts = bed[self.rows, self.cols]
        self.bank = np.array([bank.get(k, np.nan) for k in self.names])
        self.sample_s = sample_s
        self.t_last = 0.0
        self.vol_window = 0.0
        self.t_window = 0.0
        self.q_peak_inst = 0.0
        self.times: list[float] = []
        self.q_mean: list[float] = []
        self.depth_rows: list[np.ndarray] = []
        self.next_sample = sample_s
        self.t_over = dict.fromkeys(self.names, math.nan)
        self.max_h = np.zeros(len(self.names))
        self.t_max = np.zeros(len(self.names))
        self.q_at_over: dict[str, float] = {}

    def _discharge(self, q_x: np.ndarray, q_y: np.ndarray) -> float:
        f = self.faces
        total = 0.0
        total += float(q_x[f["xr"], f["xc"]].sum()) - float(q_x[f["xr2"], f["xc2"]].sum())
        total += float(q_y[f["yr"], f["yc"]].sum()) - float(q_y[f["yr2"], f["yc2"]].sum())
        return total * self.cell_m

    def _froude_counts(
        self, h: np.ndarray, q_x: np.ndarray, q_y: np.ndarray
    ) -> dict[str, tuple[int, int]]:
        """Per class: (wet cells deeper than 5 cm, of which Froude number above 1)."""
        wet = h > 0.05
        hd = np.where(wet, h, 1.0)
        qxc = 0.5 * (q_x[:, 1:] + q_x[:, :-1])
        qyc = 0.5 * (q_y[1:, :] + q_y[:-1, :])
        fr = np.hypot(qxc, qyc) / (hd * np.sqrt(9.81 * hd))
        over = wet & (fr > 1.0)
        return {k: (int((wet & m).sum()), int((over & m).sum())) for k, m in self.classes.items()}

    def record(self, t: float, h: np.ndarray, q_x: np.ndarray, q_y: np.ndarray) -> None:
        if self.snapshots is not None:
            self.snapshots.offer(t, h)
        dt = t - self.t_last
        self.t_last = t
        q = self._discharge(q_x, q_y)
        self.q_peak_inst = max(self.q_peak_inst, q)
        self.vol_window += q * dt
        self.t_window += dt
        hp = h[self.rows, self.cols]
        np.maximum(self.max_h, hp, out=self.max_h)
        newmax = hp >= self.max_h
        self.t_max = np.where(newmax, t, self.t_max)
        eta = self.bed_pts + hp
        for k, name in enumerate(self.names):
            if (
                math.isnan(self.t_over[name])
                and not math.isnan(self.bank[k])
                and eta[k] > self.bank[k]
            ):
                self.t_over[name] = t
                self.q_at_over[name] = q
        if t >= self.next_sample:
            self.times.append(t)
            self.q_mean.append(self.vol_window / max(self.t_window, 1e-9))
            self.depth_rows.append(hp.copy())
            if self.classes:
                self.fr_samples.append((t, self._froude_counts(h, q_x, q_y)))
            self.vol_window = 0.0
            self.t_window = 0.0
            while self.next_sample <= t:
                self.next_sample += self.sample_s


def _mouth_faces(land: np.ndarray, mouth_sea: np.ndarray) -> dict[str, np.ndarray]:
    """Index arrays of the faces between land and mouth-sea cells (positive = out to sea)."""
    ny, nx = land.shape
    xr, xc, xr2, xc2, yr, yc, yr2, yc2 = ([] for _ in range(8))
    for i, j in np.argwhere(land):
        if j + 1 < nx and mouth_sea[i, j + 1]:
            xr.append(i)
            xc.append(j + 1)
        if j - 1 >= 0 and mouth_sea[i, j - 1]:
            xr2.append(i)
            xc2.append(j)
        if i + 1 < ny and mouth_sea[i + 1, j]:
            yr.append(i + 1)
            yc.append(j)
        if i - 1 >= 0 and mouth_sea[i - 1, j]:
            yr2.append(i)
            yc2.append(j)
    arr = {
        k: np.array(v, dtype=int)
        for k, v in {
            "xr": xr,
            "xc": xc,
            "xr2": xr2,
            "xc2": xc2,
            "yr": yr,
            "yc": yc,
            "yr2": yr2,
            "yc2": yc2,
        }.items()
    }
    return arr


def run_storm(
    spec: dict[str, Any],
    terrain: dict[str, Any],
    burn: dict[str, Any],
    cn: np.ndarray,
    increments_mm: np.ndarray,
    points: dict[str, tuple[int, int]],
    bank: dict[str, float],
    crop: tuple[slice, slice],
    label: str,
    rain_scope: str = "domain",
    fr_max: float | None = None,
    snapshot_s: Sequence[float] | None = None,
    snapshot_hook: Callable[[float, np.ndarray], None] | None = None,
) -> dict[str, Any]:
    """Run one hyetograph over the domain on the local-inertial solver.

    Rain excess of each cell is the NRCS excess of its curve number (:mod:`pluvial`), added
    on every land cell of the domain (``rain_scope="domain"``, the result) or on the delineated
    catchment only (``"catchment"``, the delineation diagnostic); the cells outside the domain
    are walls; the sea cells hold the level ``domain.sea_level_m`` (stage boundary).

    Args:
        spec: Case spec.
        terrain: Result of :func:`build_terrain`.
        burn: Result of :func:`burn_channels`.
        cn: Curve-number raster (-), full grid.
        increments_mm: Rain depth per ``rain.main.dt_min`` step (mm).
        points: ``{name: (row, col)}`` to record (full-grid indices).
        bank: ``{name: bank ground (m)}`` of the points where overtopping is tracked.
        crop: ``(row slice, col slice)`` of the computed window.
        label: Name for logs.
        rain_scope: ``"domain"`` or ``"catchment"``.
        fr_max: Optional Froude limiter (sensitivity only), see :func:`hydraulics.step`.
        snapshot_s: Optional model times (s) at which to keep the depth field (cropped grid);
            ``None`` (default) leaves the run exactly as before.
        snapshot_hook: Optional ``f(t, h)`` called every solver step (read-only use of ``h``);
            only with ``snapshot_s``.

    Returns:
        Dict with ``h_max`` (cropped), ``recorder``, ``n_steps``, ``runtime_s``, volumes and
        ``t_end_s``; with ``snapshot_s`` also ``snapshots`` (a ``Snapshots``).
    """
    sl_r, sl_c = crop
    dt_min = float(spec["rain"]["main"]["dt_min"])
    rspec = rain_spec(increments_mm, dt_min, float(spec["landcover"]["cn_untagged"]))
    bed = burn["bed"][crop]
    domain = terrain["domain"][crop]
    sea = terrain["sea"][crop]
    mouth_sea = burn["mouth_sea"][crop]
    rain_area = terrain["mask"][crop] if rain_scope == "catchment" else terrain["domain"][crop]
    mask = rain_area & ~sea
    cn_c = cn[crop]
    levels, which = np.unique(cn_c[mask], return_inverse=True)
    sea_level = float(spec["domain"]["sea_level_m"])
    cell = float(terrain["grid"].cell_m)

    t_storm = float(rspec["rain"]["duration_min"]) * 60.0
    t_end = t_storm + float(spec["rain"]["recession_min"]) * 60.0

    def rain_fn(t: float) -> np.ndarray:
        per_level = np.array([pluvial.excess_rate(t, rspec, float(c)) for c in levels])
        field = np.zeros(mask.shape)
        field[mask] = per_level[which]
        return field

    rain_fn.breakpoints_s = pluvial.hyetograph(rspec)[0]  # type: ignore[attr-defined]
    sea_rc = np.argwhere(sea)
    sea_rows, sea_cols = sea_rc[:, 0], sea_rc[:, 1]

    def stage_fn(_t: float) -> tuple[np.ndarray, np.ndarray, float]:
        return sea_rows, sea_cols, sea_level

    h0 = np.where(domain, np.maximum(sea_level - bed, 0.0), 0.0)
    land = domain & ~terrain["sea"][crop]
    faces = _mouth_faces(land, mouth_sea)
    pts_c = {k: (v[0] - sl_r.start, v[1] - sl_c.start) for k, v in points.items()}
    chan = burn["channel"][crop] & land
    low_m = land & ~chan & (terrain["ground"][crop] <= float(spec["hydraulic"]["low_ground_m"]))
    classes = {"channel": chan, "floodplain_below_20m": low_m, "upland": land & ~chan & ~low_m}
    snaps = None
    if snapshot_s is not None:
        from climaterisk_worker.twin.case_pohang_propagation import Snapshots

        snaps = Snapshots(snapshot_s, on_offer=snapshot_hook)
    rec = _Recorder(faces, cell, pts_c, bed, bank, 60.0, classes, snaps)
    t0 = time.time()
    res = hydraulics.run(
        h0,
        bed,
        cell,
        cell,
        float(spec["hydraulic"]["manning_n"]),
        t_end,
        alpha_cfl=float(spec["hydraulic"]["alpha_cfl"]),
        blocked=~domain,
        rain_fn=rain_fn,
        stage_fn=stage_fn,
        recorder=rec,
        fr_max=fr_max,
    )
    runtime = time.time() - t0
    logger.info(
        "%s: %d steps, %.0f s, grid %s, t_end %.1f h",
        label,
        res["n_steps"],
        runtime,
        bed.shape,
        t_end / 3600,
    )
    res["recorder"] = rec
    if snaps is not None:
        res["snapshots"] = snaps
    res["runtime_s"] = runtime
    res["t_end_s"] = t_end
    res["t_storm_s"] = t_storm
    res["rain_total_mm"] = float(np.sum(increments_mm))
    return res


def hydrograph_stats(rec: _Recorder, window_s: float = 600.0) -> dict[str, float]:
    """Peak of the discharge at the mouth: 10-min mean and instantaneous.

    Args:
        rec: Recorder of a run.
        window_s: Averaging window (s) of the peak.

    Returns:
        ``peak_m3s`` (max of the running mean over ``window_s``), ``peak_time_h``,
        ``peak_instant_m3s``.
    """
    t = np.asarray(rec.times)
    q = np.asarray(rec.q_mean)
    if q.size == 0:
        return {"peak_m3s": 0.0, "peak_time_h": 0.0, "peak_instant_m3s": rec.q_peak_inst}
    w = max(round(window_s / rec.sample_s), 1)
    run_mean = np.convolve(q, np.ones(w) / w, mode="same") if q.size >= w else q
    k = int(np.argmax(run_mean))
    return {
        "peak_m3s": float(run_mean[k]),
        "peak_time_h": float(t[k] / 3600.0),
        "peak_instant_m3s": float(rec.q_peak_inst),
    }


def _cn_raster(spec: dict[str, Any], terrain: dict[str, Any]) -> tuple[np.ndarray, float]:
    """Curve-number raster of the grid from OSM green polygons (:func:`landcover.cn_raster`).

    Returns:
        ``(cn, green_share)`` with the share of green cells within the catchment.
    """
    from climaterisk import paths

    grid: site.SiteGrid = terrain["grid"]
    bbox = tuple(float(v) for v in spec["domain"]["bbox"])
    polys = landcover.read_green(paths.osm_dir("south-korea-261013.osm.pbf"), bbox, spec)  # type: ignore[arg-type]
    green = landcover.green_mask(grid, polys)
    lc = {
        "landcover": {"cn_green": spec["landcover"]["cn_green"]},
        "rain": {"cn_paved": spec["landcover"]["cn_untagged"]},
    }
    cn = landcover.cn_raster(green, lc)
    share = float(green[terrain["mask"]].mean())
    return cn, share


def _hours(t_s: float) -> float | None:
    """Seconds to hours, ``None`` for NaN."""
    return None if math.isnan(t_s) else round(t_s / 3600.0, 3)


def build_case_inputs(spec: dict[str, Any], cell_m: float | None = None) -> dict[str, Any]:
    """Everything the main storm needs: terrain, burnt channels, CN, points, bank, crop, rain.

    Shared by :func:`run_case` and the propagation rerun, so both run the same inputs.

    Args:
        spec: Case spec.
        cell_m: Cell size (m); default ``spec["domain"]["cell_m"]``.

    Returns:
        Dict with ``terrain`` (``points`` set), ``burn``, ``cn``, ``green_share``, ``pts``,
        ``rec_points`` (full-grid cells of the recorded points), ``bank``, ``crop``,
        ``apt_keys``, ``n_domain`` and ``main_inc`` (rain per step, mm).
    """
    terrain = build_terrain(spec, cell_m)
    pts = find_points(spec, terrain)
    terrain["points"] = pts
    burn = burn_channels(spec, terrain)
    ground = terrain["ground"]
    cn, green_share = _cn_raster(spec, terrain)

    stem = burn["stem"]
    ch_cells: dict[str, tuple[int, int]] = {}
    stem_k: dict[str, int] = {}
    for key in ("냉천교", "인덕교"):
        d2 = (stem["idx"][:, 0] - pts[key]["ij"][0]) ** 2 + (
            stem["idx"][:, 1] - pts[key]["ij"][1]
        ) ** 2
        k = int(np.argmin(d2))
        ch_cells[key] = (int(stem["idx"][k, 0]), int(stem["idx"][k, 1]))
        stem_k[key] = k
    apt_keys = list(spec["points"]["apartments"])
    apt_low: dict[str, tuple[int, int]] = {}
    for ak in apt_keys:
        ac = pts[ak]["cells"]
        ag = ground[ac[:, 0], ac[:, 1]]
        apt_low[ak] = (int(ac[int(np.argmin(ag)), 0]), int(ac[int(np.argmin(ag)), 1]))
    posco_ij = tuple(int(v) for v in pts["POSCO"]["ij"])
    rec_points: dict[str, tuple[int, int]] = {
        "냉천교": ch_cells["냉천교"],
        "인덕교": ch_cells["인덕교"],
        "POSCO": posco_ij,  # type: ignore[dict-item]
    }
    for ak in apt_keys:
        rec_points[f"{ak} 입구(최저지반)"] = apt_low[ak]
    # bank = the lowest ground beside the burnt section (outside the channel), where it spills
    bank = {}
    for key, (ci, cj) in ch_cells.items():
        w = int(stem["radius"][stem_k[key]]) + 2
        win = (slice(max(ci - w, 0), ci + w + 1), slice(max(cj - w, 0), cj + w + 1))
        side = ~burn["channel"][win] & terrain["domain"][win]
        bank[key] = float(ground[win][side].min())

    dom = terrain["domain"]
    rows = np.flatnonzero(dom.any(axis=1))
    cols = np.flatnonzero(dom.any(axis=0))
    crop = (slice(int(rows[0]), int(rows[-1]) + 1), slice(int(cols[0]), int(cols[-1]) + 1))
    n_domain = int(dom.sum())

    main_inc = nested_hyetograph(
        spec["rain"]["main"]["depth_duration_mm"], float(spec["rain"]["main"]["dt_min"])
    )
    return {
        "terrain": terrain,
        "burn": burn,
        "cn": cn,
        "green_share": green_share,
        "pts": pts,
        "rec_points": rec_points,
        "bank": bank,
        "crop": crop,
        "apt_keys": apt_keys,
        "n_domain": n_domain,
        "main_inc": main_inc,
    }


def run_case(
    spec: dict[str, Any],
    out_dir: Path,
    cell_m: float | None = None,
    with_check: bool = True,
    t_scale: float = 1.0,
) -> dict[str, Any]:
    """Build and run the whole Naengcheon case; write the outputs to ``out_dir``.

    Pipeline: bare-earth terrain, catchment (D8, compared with ``catchment_ref``), channel
    burn calibrated on 700 m3/s, CN from OSM green, the main storm (nested hyetograph of the
    published ASOS depths) and, as a diagnostic, the Seoul-shape check storm.

    Args:
        spec: Case spec.
        out_dir: Output folder (created).
        cell_m: Cell size (m); default ``spec["domain"]["cell_m"]``.
        with_check: Also run the check storm.
        t_scale: Factor on the simulated time (1 = full; < 1 only for quick tests).

    Returns:
        The summary dict (also written as ``summary.json``) plus the arrays under the keys
        ``_arrays`` (not serialised).
    """
    t_start = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    inp = build_case_inputs(spec, cell_m)
    terrain, burn, cn, green_share = inp["terrain"], inp["burn"], inp["cn"], inp["green_share"]
    grid: site.SiteGrid = terrain["grid"]
    cell, pts, ground = grid.cell_m, inp["pts"], terrain["ground"]
    dom = terrain["domain"]
    rec_points, bank, crop = inp["rec_points"], inp["bank"], inp["crop"]
    apt_keys, n_domain, main_inc = inp["apt_keys"], inp["n_domain"], inp["main_inc"]
    if t_scale != 1.0:
        spec = json.loads(json.dumps(spec))
        spec["rain"]["recession_min"] = float(spec["rain"]["recession_min"]) * t_scale
        n_keep = max(int(len(main_inc) * t_scale), 1)
        main_inc = main_inc[:n_keep]
    res = run_storm(spec, terrain, burn, cn, main_inc, rec_points, bank, crop, "main")
    rec: _Recorder = res["recorder"]
    hs = hydrograph_stats(rec)

    check: dict[str, Any] = {"warn_peak_rel": float(spec["check"]["warn_peak_rel"])}
    if with_check:
        chk_inc = seoul_check_increments(spec["rain"]["check"])
        if t_scale != 1.0:
            chk_inc = chk_inc[: max(int(len(chk_inc) * t_scale), 1)]
        res_c = run_storm(spec, terrain, burn, cn, chk_inc, rec_points, bank, crop, "check")
        hs_c = hydrograph_stats(res_c["recorder"])
        rel = abs(hs_c["peak_m3s"] - hs["peak_m3s"]) / max(hs["peak_m3s"], 1e-9)
        check.update(
            {
                "peak_main_m3s": round(hs["peak_m3s"], 1),
                "peak_check_m3s": round(hs_c["peak_m3s"], 1),
                "peak_rel_diff": round(rel, 4),
                "warning": bool(rel > check["warn_peak_rel"]),
                "runtime_s": round(res_c["runtime_s"], 1),
            }
        )
        if check["warning"]:
            logger.warning("check: peak differs by %.0f %% from the main run", 100 * rel)
    else:
        check.update({"peak_rel_diff": None, "warning": False, "note": "check not run"})

    # delineation diagnostic: rain on the delineated catchment only
    res_cat = run_storm(
        spec, terrain, burn, cn, main_inc, rec_points, bank, crop, "catchment-only", "catchment"
    )
    hs_cat = hydrograph_stats(res_cat["recorder"])
    # Froude shares at the peak of the mouth discharge
    froude_peak: dict[str, Any] = {"time_h": round(hs["peak_time_h"], 2)}
    if rec.fr_samples:
        k_pk = int(np.argmin([abs(t_ / 3600.0 - hs["peak_time_h"]) for t_, _ in rec.fr_samples]))
        for cls, (n_wet, n_over) in rec.fr_samples[k_pk][1].items():
            froude_peak[cls] = {
                "wet_cells": n_wet,
                "share_fr_above_1": round(n_over / max(n_wet, 1), 4),
            }
    sensitivity: dict[str, Any] = {}
    fr_lim = spec["hydraulic"].get("froude_limit_sensitivity")
    if with_check and fr_lim:
        res_f = run_storm(
            spec, terrain, burn, cn, main_inc, rec_points, bank, crop, "froude-limited", "domain",
            float(fr_lim),
        )  # fmt: skip
        hs_f = hydrograph_stats(res_f["recorder"])
        hm_f = res_f["h_max"]
        land_f = terrain["domain"][crop] & ~terrain["sea"][crop]
        nonch = land_f & ~burn["channel"][crop]
        sensitivity = {
            "fr_max": float(fr_lim),
            "source": "Froude-number cap on face discharges, LISFLOOD-FP practice "
            "(Bates et al. 2010, J. Hydrol. 387)",
            "peak_m3s": round(hs_f["peak_m3s"], 1),
            "peak_rel_change": round((hs_f["peak_m3s"] - hs["peak_m3s"]) / hs["peak_m3s"], 4),
            "peak_time_h": round(hs_f["peak_time_h"], 2),
            "max_depth_m": round(float(hm_f[nonch].max()), 2),
            "flooded_area_km2": round(
                float((nonch & (hm_f > float(spec["hydraulic"]["flood_depth_m"]))).sum())
                * cell**2
                / 1e6,
                3,
            ),
            "mean_abs_depth_change_m_on_cells_flooded_in_either": None,
            "points_max_depth_m": {
                n: round(float(v), 2)
                for n, v in zip(res_f["recorder"].names, res_f["recorder"].max_h, strict=True)
            },
            "max_froude": round(float(res_f["max_froude"]), 3),
            "runtime_s": round(res_f["runtime_s"], 1),
        }
        wet_either = nonch & ((hm_f > 0.3) | (res["h_max"] > 0.3))
        sensitivity["mean_abs_depth_change_m_on_cells_flooded_in_either"] = round(
            float(np.abs(hm_f - res["h_max"])[wet_either].mean()), 3
        )

    h_max = res["h_max"]
    sea_c = terrain["sea"][crop]
    dom_c = dom[crop]
    chan_c = burn["channel"][crop]
    mask_c = terrain["mask"][crop]
    land_c = dom_c & ~sea_c
    thr = float(spec["hydraulic"]["flood_depth_m"])
    low = float(spec["hydraulic"]["low_ground_m"])
    ground_c = ground[crop]
    flooded = land_c & ~chan_c & (h_max > thr)
    cell_km2 = cell**2 / 1e6
    flooded_area = {
        "catchment_km2": round(float((flooded & mask_c).sum()) * cell_km2, 3),
        "buffer_floodplain_km2": round(float((flooded & ~mask_c).sum()) * cell_km2, 3),
        "catchment_ground_below_low_km2": round(
            float((flooded & mask_c & (ground_c <= low)).sum()) * cell_km2, 3
        ),
        "threshold_m": thr,
        "low_ground_m": low,
    }

    # points
    t_h = np.asarray(rec.times) / 3600.0
    depth_rows = np.vstack(rec.depth_rows) if rec.depth_rows else np.zeros((0, len(rec.names)))
    points_out: dict[str, Any] = {}
    for k, name in enumerate(rec.names):
        ij = rec_points[name]
        entry: dict[str, Any] = {
            "ground_m": round(float(ground[ij]), 2),
            "bed_m": round(float(burn["bed"][ij]), 2),
            "max_depth_m": round(float(rec.max_h[k]), 2),
            "time_of_max_h": round(float(rec.t_max[k]) / 3600.0, 2),
        }
        if name in bank:
            entry["bank_ground_m"] = round(bank[name], 2)
            entry["overtopping_time_h_after_rain_start"] = _hours(rec.t_over[name])
            entry["mouth_discharge_at_overtopping_m3s"] = (
                round(rec.q_at_over[name], 1) if name in rec.q_at_over else None
            )
        points_out[name] = entry
    for ak in apt_keys:
        ac = pts[ak]["cells"] - np.array([crop[0].start, crop[1].start])
        ad = h_max[ac[:, 0], ac[:, 1]]
        entry_a = points_out[f"{ak} 입구(최저지반)"]
        entry_a["polygon_max_depth_m"] = round(float(ad.max()), 2)
        entry_a["polygon_mean_max_depth_m"] = round(float(ad.mean()), 2)
        entry_a["polygon_cells"] = int(ac.shape[0])
        entry_a["osm_name"] = spec["points"]["apartments"][ak]

    pc = pts["POSCO"]["cells"] - np.array([crop[0].start, crop[1].start])
    pdep = h_max[pc[:, 0], pc[:, 1]]
    points_out["POSCO"]["polygon_land_cells_in_domain"] = int(pc.shape[0])
    points_out["POSCO"]["polygon_land_area_in_domain_km2"] = round(
        float(pc.shape[0]) * cell**2 / 1e6, 2
    )
    points_out["POSCO"]["polygon_share_flooded"] = round(float((pdep > thr).mean()), 3)
    points_out["POSCO"]["polygon_median_max_depth_m"] = round(float(np.median(pdep)), 2)
    points_out["POSCO"]["polygon_p90_max_depth_m"] = round(float(np.percentile(pdep, 90)), 2)
    points_out["POSCO"]["polygon_centroid_lonlat"] = [
        round(v, 4) for v in pts["POSCO"]["polygon_centroid"]
    ]

    q_ts = np.asarray(rec.q_mean)
    vol = float(np.sum(q_ts) * 60.0)
    p_main = hs["peak_m3s"]
    ref = spec["channel_capacity_m3s"]
    dflood = spec["design_flood_m3s"]
    area = terrain["catchment_area_km2"]
    summary: dict[str, Any] = {
        "name": spec["name"],
        "badge": spec["badge"],
        "n_assumptions": len(spec["assumptions"]),
        "assumptions": list(spec["assumptions"]),
        "catchment_area_km2": round(area, 2),
        "catchment_ref_km2": spec["catchment_ref"]["area_km2"],
        "catchment_rel_diff": round(
            (area - spec["catchment_ref"]["area_km2"]) / spec["catchment_ref"]["area_km2"], 4
        ),
        "outlet_cell": [int(v) for v in terrain["outlet_rc"]],
        "grid": {
            "cell_m": cell,
            "full_shape": [int(v) for v in ground.shape],
            "computed_shape": [int(v) for v in h_max.shape],
            "n_domain_cells": n_domain,
            "n_catchment_cells": int(terrain["mask"].sum()),
            "refinement_10m": "not done (see assumption A11)",
        },
        "runtime_s": {
            "total": round(time.time() - t_start, 1),
            "main_run": round(res["runtime_s"], 1),
            "n_steps_main": int(res["n_steps"]),
        },
        "rain": {
            "main_total_mm": round(float(main_inc.sum()), 1),
            "main_duration_min": float(len(main_inc)) * float(spec["rain"]["main"]["dt_min"]),
            "recession_min": float(spec["rain"]["recession_min"]),
            "landcover_green_share_of_catchment": round(green_share, 3),
            "cn_green": spec["landcover"]["cn_green"],
            "cn_untagged": spec["landcover"]["cn_untagged"],
        },
        "discharge_mouth": {
            "peak_10min_mean_m3s": round(p_main, 1),
            "peak_time_h_after_rain_start": round(hs["peak_time_h"], 2),
            "peak_instant_m3s": round(hs["peak_instant_m3s"], 1),
            "channel_capacity_ref_m3s": ref,
            "design_100yr_m3s": dflood["100"],
            "design_500yr_m3s": dflood["500"],
            "ratio_to_capacity": round(p_main / ref, 3),
            "ratio_to_design_100yr": round(p_main / dflood["100"], 3),
            "ratio_to_design_500yr": round(p_main / dflood["500"], 3),
            "runoff_volume_hm3": round(vol / 1e6, 2),
        },
        "channel": burn["report"],
        "points": points_out,
        "flooded_area": flooded_area,
        "max_depth_m": round(float(h_max[land_c & ~chan_c].max()), 2),
        "water_balance": {
            "rain_volume_m3": round(float(res["rain_volume_m3"]), 0),
            "sea_boundary_net_removed_m3": round(-float(res["boundary_volume_m3"]), 0),
            "max_froude": round(float(res["max_froude"]), 3),
        },
        "discharge_mouth_rain_catchment_only": {
            "role": "delineation diagnostic (rain on the delineated catchment only)",
            "peak_10min_mean_m3s": round(hs_cat["peak_m3s"], 1),
            "peak_time_h_after_rain_start": round(hs_cat["peak_time_h"], 2),
            "peak_rel_diff_to_result": round(
                (hs_cat["peak_m3s"] - hs["peak_m3s"]) / hs["peak_m3s"], 4
            ),
        },
        "rain_scope": "every land cell of the domain, including the floodplain buffer (result)",
        "froude_at_peak_discharge": froude_peak,
        "froude_limiter_sensitivity": sensitivity,
        "check": check,
        "time_reference": (
            "hours after the start of the 12-h storm window; the clock time of the rain is unknown"
        ),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # rasters and series
    _write_outputs(out_dir, terrain, burn, crop, h_max, rec, t_h, depth_rows, rec_points, summary)
    logger.info("case written to %s (%.0f s)", out_dir, time.time() - t_start)
    return summary


def _write_outputs(
    out_dir: Path,
    terrain: dict[str, Any],
    burn: dict[str, Any],
    crop: tuple[slice, slice],
    h_max: np.ndarray,
    rec: _Recorder,
    t_h: np.ndarray,
    depth_rows: np.ndarray,
    rec_points: dict[str, tuple[int, int]],
    summary: dict[str, Any],
) -> None:
    """Max-depth GeoTIFF, hydrograph and point series CSVs, and the two PNG figures."""
    import csv

    import rasterio
    from rasterio.transform import Affine

    from climaterisk_worker.twin import plot as plot_mod

    grid: site.SiteGrid = terrain["grid"]
    cell = grid.cell_m
    dom_c = terrain["domain"][crop]
    r0, c0 = crop[0].start, crop[1].start
    ny, nx = h_max.shape
    west = grid.west + c0 * cell / grid.lon_m_per_deg
    south = grid.south + r0 * cell / site.M_PER_DEG
    dlon, dlat = cell / grid.lon_m_per_deg, cell / site.M_PER_DEG
    north = south + ny * dlat
    arr = np.where(dom_c, h_max, -9999.0).astype("float32")[::-1]
    with rasterio.open(
        out_dir / "max_depth.tif",
        "w",
        driver="GTiff",
        height=ny,
        width=nx,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=Affine(dlon, 0.0, west, 0.0, -dlat, north),
        nodata=-9999.0,
        compress="deflate",
    ) as dst:
        dst.write(arr, 1)
    with (out_dir / "hydrograph_mouth.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_h_after_rain_start", "discharge_m3s"])
        for t, q in zip(t_h, rec.q_mean, strict=True):
            w.writerow([round(float(t), 4), round(float(q), 2)])
    with (out_dir / "points_depth.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        bridges = [k for k, name in enumerate(rec.names) if not math.isnan(rec.bank[k])]
        w.writerow(
            [
                "time_h_after_rain_start",
                *rec.names,
                *[f"{rec.names[k]}_water_surface_above_bank_m" for k in bridges],
            ]
        )
        for t, row in zip(t_h, depth_rows, strict=True):
            over = [row[k] + rec.bed_pts[k] - rec.bank[k] for k in bridges]
            w.writerow(
                [
                    round(float(t), 4),
                    *[round(float(v), 3) for v in row],
                    *[round(float(v), 3) for v in over],
                ]
            )
    ext = (west, west + nx * dlon, south, north)
    plot_mod.plot_case_depth_map(
        out_dir / "max_depth_map.png",
        h_max=np.where(dom_c & ~terrain["sea"][crop], h_max, np.nan),
        extent=ext,
        buildings=terrain["buildings"],
        channel=burn["channel"][crop],
        mask=terrain["mask"][crop],
        points={
            k: (
                west + (v[1] - c0 + 0.5) * dlon,
                south + (v[0] - r0 + 0.5) * dlat,
            )
            for k, v in rec_points.items()
        },
        title=f"{summary['name']} - max depth (m), {summary['badge']}",
    )
    plot_mod.plot_case_hydrograph(
        out_dir / "hydrograph_mouth.png",
        t_h=t_h,
        q=np.asarray(rec.q_mean),
        refs={
            "channel capacity (700)": float(summary["discharge_mouth"]["channel_capacity_ref_m3s"]),
            "100-yr design (866)": float(summary["discharge_mouth"]["design_100yr_m3s"]),
            "500-yr design (1189)": float(summary["discharge_mouth"]["design_500yr_m3s"]),
        },
        title=(
            f"{summary['name']} - outlet hydrograph (hours after the rain start), "
            f"{summary['badge']}"
        ),
    )


__all__ = [
    "build_terrain",
    "burn_channels",
    "delineate",
    "depth_at",
    "fill_depressions",
    "find_points",
    "hydrograph_stats",
    "load_case_spec",
    "mosaic_dem",
    "nested_hyetograph",
    "rain_spec",
    "run_case",
    "run_storm",
    "scaled_shape",
    "seoul_check_increments",
]
