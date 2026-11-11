"""Run-on from the catchment outside the site box (step R6).

D8 flow routing (O'Callaghan & Mark 1984) on the bare-earth ground of the site box padded
by ``spec["runon"]["pad_m"]``: every cell that drains into the site from outside delivers
its whole upstream area to the receiving site cell. The upstream area is paved (same excess
rain as the streets) and has its own sewer; only the excess above the sewer capacity runs
on. There is no travel-time lag and no pit filling: both are assumptions stated in the
--real output.

Algorithm:
    D8 receiver of cell $c$: the neighbour $n$ with the largest drop
    $$\\sigma_n = \\frac{z_c - z_n}{d_n},\\quad d_n = 1\\ \\text{(N,E,S,W)},\\
    \\sqrt2\\ \\text{(diagonals)},$$
    provided $\\sigma_n > 0$ (ties: first of N, NE, E, SE, S, SW, W, NW; none = pit).
    Accumulation $a_c = 1 + \\sum_{m \\to c} a_m$ (cells whose path passes through $c$).
    Run-on discharge from an upstream area $A = a\\,\\Delta^2$:
    $$Q(t) = A\\,\\max\\big(r(t) - s,\\ 0\\big)$$
    ASCII: sigma_n = (z_c - z_n)/d_n; receiver = argmax sigma_n if > 0; a_c = 1 + sum a_m;
    Q = A * max(r - s, 0).

    $z$ ground elevation (m), $a$ number of cells (-), $\\Delta$ cell size (m), $A$ upstream
    area (m^2), $r$ rain excess rate at ``cn_paved`` (m s^-1), $s$ sewer capacity (m s^-1),
    $Q$ discharge (m^3 s^-1).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from climaterisk_worker.twin import pluvial
from climaterisk_worker.twin.site import SiteGrid

logger = logging.getLogger(__name__)

# neighbour offsets (d_row, d_col) in the order N, NE, E, SE, S, SW, W, NW; row = south
_NEIGHBOURS = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))


def d8_receivers(z: np.ndarray) -> np.ndarray:
    """Flat index of the D8 receiver of every cell, ``-1`` for a cell without lower neighbour.

    Args:
        z: Ground elevation (m), shape ``(ny, nx)``.

    Returns:
        Integer array ``(ny, nx)``: ``row * nx + col`` of the steepest-descent neighbour.
    """
    z = np.asarray(z, dtype=float)
    ny, nx = z.shape
    best_slope = np.zeros(z.shape)
    recv = np.full(z.shape, -1, dtype=np.int64)
    flat = np.arange(ny * nx, dtype=np.int64).reshape(z.shape)
    for dr, dc in _NEIGHBOURS:
        dist = math.sqrt(2.0) if dr != 0 and dc != 0 else 1.0
        r0, r1 = max(-dr, 0), ny - max(dr, 0)
        c0, c1 = max(-dc, 0), nx - max(dc, 0)
        if r1 <= r0 or c1 <= c0:
            continue
        here = z[r0:r1, c0:c1]
        there = z[r0 + dr : r1 + dr, c0 + dc : c1 + dc]
        slope = (here - there) / dist
        better = slope > best_slope[r0:r1, c0:c1]  # strict: ties keep the earlier neighbour
        best_slope[r0:r1, c0:c1] = np.where(better, slope, best_slope[r0:r1, c0:c1])
        recv[r0:r1, c0:c1] = np.where(
            better, flat[r0 + dr : r1 + dr, c0 + dc : c1 + dc], recv[r0:r1, c0:c1]
        )
    return recv


def _accumulate(z: np.ndarray, recv: np.ndarray) -> np.ndarray:
    """Number of cells draining through each cell, from the receivers (highest cell first)."""
    flat_z = z.ravel()
    flat_recv = recv.ravel()
    acc = np.ones(flat_z.size, dtype=np.int64)
    # a receiver is strictly lower, so descending elevation visits every donor before it
    for c in np.argsort(-flat_z, kind="stable"):
        r = flat_recv[c]
        if r >= 0:
            acc[r] += acc[c]
    return acc.reshape(z.shape)


def d8_accumulation(z: np.ndarray) -> np.ndarray:
    """Number of cells (itself included) whose D8 steepest-descent path passes through each cell.

    Algorithm:
        See the module docstring: steepest drop to one of 8 neighbours (diagonal drops over
        $\\sqrt2$), ties to the first of N, NE, E, SE, S, SW, W, NW; $a_c = 1 + \\sum a_m$.
        ASCII: a[c] = 1 + sum of a[m] over cells m whose receiver is c.

    Args:
        z: Ground elevation (m), shape ``(ny, nx)``.

    Returns:
        Integer array ``(ny, nx)``.
    """
    z = np.asarray(z, dtype=float)
    return _accumulate(z, d8_receivers(z))


def edge_inflow(z_ext: np.ndarray, inside: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inside cells that receive the flow of outside cells, with the upstream cell count.

    For every OUTSIDE cell whose D8 receiver is INSIDE, the receiving cell and the
    accumulation of the outside cell; several outside cells feeding one inside cell are
    summed. Cells of the site that drain out and back in are counted in ``n_up`` (rare).

    Args:
        z_ext: Ground of the extended raster (m), shape ``(ny, nx)``.
        inside: Boolean raster of the site cells.

    Returns:
        ``(rows, cols, n_up)`` in the coordinates of ``z_ext``, sorted row-major.
    """
    z = np.asarray(z_ext, dtype=float)
    inside = np.asarray(inside, dtype=bool)
    nx = z.shape[1]
    recv = d8_receivers(z)
    acc = _accumulate(z, recv)
    flat_in = inside.ravel()
    flat_recv = recv.ravel()
    donors = np.flatnonzero(~flat_in & (flat_recv >= 0))
    donors = donors[flat_in[flat_recv[donors]]]
    if donors.size == 0:
        empty = np.empty(0, dtype=int)
        return empty, empty.copy(), empty.copy()
    target = flat_recv[donors]
    uniq, inv = np.unique(target, return_inverse=True)  # sorted flat index = row-major
    total = np.zeros(uniq.size, dtype=np.int64)
    np.add.at(total, inv, acc.ravel()[donors])
    return uniq // nx, uniq % nx, total


def runon_rate(t_s: float, area_m2: Any, spec: dict[str, Any]) -> Any:
    """Run-on discharge (m^3/s) from an upstream paved area draining to its own sewer first.

    Algorithm:
        $Q = A\\,\\max(r(t) - s,\\ 0)$ with $r$ = ``pluvial.excess_rate(t, spec, cn_paved)``.
        ASCII: Q = A * max(r - s, 0).

    Args:
        t_s: Time since the storm start (s).
        area_m2: Upstream area $A$ (m^2), scalar or array.
        spec: City spec (``rain.cn_paved``, ``rain.sewer_capacity_mm_h``).

    Returns:
        Discharge (m^3 s^-1), same shape as ``area_m2``.
    """
    r = pluvial.excess_rate(t_s, spec, float(spec["rain"]["cn_paved"]))
    s = float(spec["rain"]["sewer_capacity_mm_h"]) / 3.6e6
    q = max(r - s, 0.0)
    return np.asarray(area_m2, dtype=float) * q if np.ndim(area_m2) else float(area_m2) * q


@dataclass(frozen=True)
class PaddedGrid(SiteGrid):
    """A site grid enlarged by whole cells, sharing the site's longitude scale exactly.

    ``lon_ref_south`` is the latitude whose cosine converts degrees of longitude to metres,
    so a padded cell centre is the same point as the site cell centre it coincides with.
    """

    lon_ref_south: float = 0.0

    @property
    def lon_m_per_deg(self) -> float:
        """Metres per degree of longitude at ``lon_ref_south``."""
        from climaterisk_worker.twin.site import M_PER_DEG

        return M_PER_DEG * math.cos(math.radians(self.lon_ref_south))


def padded_grid(grid: SiteGrid, pad_cells: int) -> PaddedGrid:
    """Grid with ``pad_cells`` extra cells on every side of ``grid`` (integer offsets).

    Padded cell ``(i + pad_cells, j + pad_cells)`` is site cell ``(i, j)``.

    Args:
        grid: Site grid.
        pad_cells: Cells added on each side.

    Returns:
        Padded grid.
    """
    from climaterisk_worker.twin.site import M_PER_DEG

    shift = pad_cells * grid.cell_m
    return PaddedGrid(
        west=grid.west - shift / grid.lon_m_per_deg,
        south=grid.south - shift / M_PER_DEG,
        cell_m=grid.cell_m,
        nx=grid.nx + 2 * pad_cells,
        ny=grid.ny + 2 * pad_cells,
        lon_ref_south=grid.south,
    )


def padded_ground(spec: dict[str, Any], site: dict[str, Any]) -> dict[str, Any]:
    """Bare-earth ground of the site box padded by ``spec["runon"]["pad_m"]``.

    Same machinery as :func:`site.build_site`: OSM footprints of the padded box are removed
    from the Copernicus GLO-30 surface model and the ground is interpolated underneath.

    Args:
        spec: City spec (``site`` and ``runon`` sections).
        site: Result of ``build_site`` (its grid defines the offsets).

    Returns:
        Dict with ``grid`` (PaddedGrid), ``ground`` ``(ny + 2p, nx + 2p)`` (m), ``inside``
        (site cells, boolean), ``pad_cells`` and ``n_buildings``.
    """
    from climaterisk import paths
    from climaterisk_worker.twin import site as site_mod

    cfg = spec["site"]
    grid = site["grid"]
    pad_cells = round(float(spec["runon"]["pad_m"]) / grid.cell_m)
    pg = padded_grid(grid, pad_cells)
    bbox = (
        pg.west,
        pg.south,
        pg.west + pg.nx * pg.cell_m / pg.lon_m_per_deg,
        pg.south + pg.ny * pg.cell_m / site_mod.M_PER_DEG,
    )
    osm = paths.osm_dir(cfg["osm_file"])
    buildings = site_mod.read_buildings(osm, bbox, spec)
    dsm_mask = site_mod.building_mask(pg, buildings[~buildings["underground"]])
    dem = paths.external_dir("copernicus_dem", cfg["dem_file"])
    ground = site_mod.bare_earth(dem, pg, dsm_mask)
    inside = np.zeros((pg.ny, pg.nx), dtype=bool)
    inside[pad_cells : pad_cells + grid.ny, pad_cells : pad_cells + grid.nx] = True
    return {
        "grid": pg,
        "ground": ground,
        "inside": inside,
        "pad_cells": pad_cells,
        "n_buildings": len(buildings),
    }


def site_inflow(
    ext: dict[str, Any], cell_m: float
) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray], dict[str, float]]:
    """Run-on cells of the site as ``(rows, cols, area_m2)`` in SITE coordinates.

    Args:
        ext: Result of :func:`padded_ground`.
        cell_m: Cell size (m).

    Returns:
        ``((rows, cols, area_m2), stats)`` with ``stats`` holding ``upstream_area_km2``,
        ``n_edge_cells``, ``pits_interior`` (cells of the padded raster without a lower
        neighbour, not on its border), ``pits_share`` and ``largest_pit_km2``.
    """
    z = ext["ground"]
    p = int(ext["pad_cells"])
    rows, cols, n_up = edge_inflow(z, ext["inside"])
    area = n_up.astype(float) * cell_m**2
    recv = d8_receivers(z)
    acc = _accumulate(z, recv)
    pit = recv < 0
    interior = np.zeros(z.shape, dtype=bool)
    interior[1:-1, 1:-1] = True
    pit_i = pit & interior
    stats = {
        "upstream_area_km2": float(area.sum()) / 1e6,
        "n_edge_cells": float(rows.size),
        "pits_interior": float(pit_i.sum()),
        "pits_share": float(pit_i.sum()) / float(pit_i.size),
        "largest_pit_km2": float(acc[pit_i].max()) * cell_m**2 / 1e6 if pit_i.any() else 0.0,
    }
    logger.info(
        "run-on: %d edge cells, upstream area %.3f km2", rows.size, stats["upstream_area_km2"]
    )
    return (rows - p, cols - p, area), stats


__all__ = [
    "PaddedGrid",
    "d8_accumulation",
    "d8_receivers",
    "edge_inflow",
    "padded_grid",
    "padded_ground",
    "runon_rate",
    "site_inflow",
]
