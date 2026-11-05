"""Basement flooding of the real-site buildings during the design storm (step R3c).

OSM has no basement data, so basement storeys are an assumption by use (the defaults of the
user's storey sheet). Street water reaches a basement through one ramp entrance modelled as a
broad-crested weir; the water that ends up inside fills the basement storeys from the lowest
one, and every storey is priced through the repo engine with the JRC curve. Every number comes
from ``spec["basement"]``, ``spec["loss"]``, ``spec["site"]`` and ``spec["rain"]``. The loss
is that of ONE storm. Water entering a basement is NOT removed from the 2D street flow
(one-way coupling).

Algorithm:
    Ring depth of building $b$ at time $t$: $h_b(t) = \\max_{R_b} h(t)$ with the ring $R_b$ of
    ``site_loss.entry_depths``. Water in the basement over the run:
    $$V_b = \\max\\Big(\\int_{t_0}^{t_{end}} C\\,B\\,\\max(h_b(t) - T, 0)^{3/2}\\,dt
    + A_r\\,\\frac{P}{1000} - Q_p\\,\\frac{t_{end} - t_0}{3600},\;0\\Big)$$
    Storey $k$ (0 = lowest) of $n_b$ storeys, each with floor area $A_b$:
    $$d_{b,k} = \\mathrm{clip}\\big(V_b / A_b - k\\,\\Delta_f,\;0,\;\\Delta_f\\big),\\quad k < n_b$$
    Basement loss $L_b = \\sum_k A_b\\,c_{j(b)}\\,\\mathrm{MDR}_{j(b)}(d_{b,k})$ (engine).
    ASCII: V = max(sum(C*B*max(h-T,0)^1.5 * dt) + A_r*P/1000 - Q_p*(t_end-t0)/3600, 0);
    depth_k = clip(V/area - k*ff, 0, ff); loss = sum_k area*cost*MDR(depth_k).

    $C$ weir coefficient (m^0.5/s), $B$ ramp width (m), $T$ no-damage threshold (m), $h$ ring
    depth (m), $t$ time (s), $A_r$ ramp rain area (m2), $P$ storm rain total (mm), $Q_p$ pump
    capacity (m3/h), $\\Delta_f$ floor-to-floor height (m), $A_b$ footprint area (m2),
    $c_j$ replacement cost (USD/m2) of property type $j$, $\\mathrm{MDR}$ mean damage ratio (-).
"""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd
import numpy as np

from climaterisk_worker.twin import site_loss
from climaterisk_worker.twin.site import SiteGrid

logger = logging.getLogger(__name__)


def basement_levels(buildings: gpd.GeoDataFrame, spec: dict[str, Any]) -> np.ndarray:
    """Basement storeys per building (int): 0 unless an above-ground wall with enough storeys.

    Args:
        buildings: Buildings with ``building``, ``height_m``, ``is_wall``, ``underground``.
        spec: City spec with ``basement`` and ``site`` sections.

    Returns:
        Integer array, length ``len(buildings)``.
    """
    bs = spec["basement"]
    levels: dict[str, Any] = bs["levels"]
    min_storeys = int(bs["min_storeys"])
    n_above = site_loss.storeys(buildings, spec)
    is_wall = buildings["is_wall"].to_numpy(dtype=bool)
    under = buildings["underground"].to_numpy(dtype=bool)
    out = np.zeros(len(buildings), dtype=int)
    for i, kind in enumerate(buildings["building"]):
        if is_wall[i] and not under[i] and n_above[i] >= min_storeys:
            out[i] = int(levels.get(str(kind), levels["_default"]))
    return out


def ring_series(
    grid: SiteGrid,
    buildings: gpd.GeoDataFrame,
    depth_t: np.ndarray,
    walls: np.ndarray,
) -> np.ndarray:
    """Maximum depth (m) over each wall building's ring, for every frame.

    The ring is exactly that of ``site_loss.entry_depths`` (``site_loss.ring_cells``); rows
    that are not walls, are underground, or have an empty ring read 0.

    Args:
        grid: Site grid.
        buildings: Buildings (EPSG:4326) with ``is_wall`` and ``underground``.
        depth_t: Depth frames (m), shape ``(n_t, ny, nx)``.
        walls: Boolean ``(ny, nx)`` wall raster.

    Returns:
        Float array ``(n_t, n_buildings)``.
    """
    depth = np.asarray(depth_t, dtype=float)
    is_wall = buildings["is_wall"].to_numpy(dtype=bool)
    under = buildings["underground"].to_numpy(dtype=bool)
    out = np.zeros((depth.shape[0], len(buildings)), dtype=float)
    for i, geom in enumerate(buildings.geometry):
        if not is_wall[i] or under[i]:
            continue
        rows, cols = site_loss._cells(grid, geom)
        rr, cc = site_loss.ring_cells(grid, rows, cols, walls)
        if rr.size:
            out[:, i] = depth[:, rr, cc].max(axis=1)
    logger.debug("ring series shape %s dtype %s", out.shape, out.dtype)
    return out


def basement_volume_m3(
    h_series: np.ndarray, times_s: np.ndarray, spec: dict[str, Any]
) -> np.ndarray:
    """Water (m3) in each building's basement after the storm (see module docstring).

    Args:
        h_series: Ring depth (m), shape ``(n_t, n_buildings)``.
        times_s: Frame times (s), length ``n_t``.
        spec: City spec with ``basement``, ``loss`` and ``rain`` sections.

    Returns:
        Float array ``(n_buildings,)``, floored at 0.
    """
    bs = spec["basement"]
    thr = float(spec["loss"]["entry_threshold_m"])
    h = np.asarray(h_series, dtype=float)
    t = np.asarray(times_s, dtype=float)
    flow = (
        float(bs["weir_coefficient"]) * float(bs["ramp_width_m"]) * np.maximum(h - thr, 0.0) ** 1.5
    )
    weir = np.sum(0.5 * (flow[1:] + flow[:-1]) * np.diff(t)[:, None], axis=0)
    ramp_rain = float(bs["ramp_rain_area_m2"]) * float(spec["rain"]["total_mm"]) / 1000.0
    pumped = float(bs["pump_m3_h"]) * (float(t[-1]) - float(t[0])) / 3600.0
    return np.maximum(weir + ramp_rain - pumped, 0.0)


def basement_depths(
    volume_m3: np.ndarray, area_m2: np.ndarray, n_levels: np.ndarray, spec: dict[str, Any]
) -> np.ndarray:
    """Water depth (m) per basement storey, filling from the lowest (column 0).

    Args:
        volume_m3: Water in the basement (m3), per building.
        area_m2: Footprint (= storey floor) area (m2), per building.
        n_levels: Basement storeys per building.
        spec: City spec with a ``site`` section (floor-to-floor height).

    Returns:
        Float array ``(n_buildings, max(n_levels))``; 0 beyond a building's storeys.
    """
    ff = float(spec["site"]["floor_to_floor_m"])
    n = np.asarray(n_levels, dtype=int)
    k = np.arange(int(n.max()) if n.size else 0)
    fill = np.asarray(volume_m3, dtype=float)[:, None] / np.asarray(area_m2, dtype=float)[:, None]
    depth = np.clip(fill - k[None, :] * ff, 0.0, ff)
    return np.where(k[None, :] < n[:, None], depth, 0.0)


def price_with_basements(
    buildings: gpd.GeoDataFrame,
    ground_depth: np.ndarray,
    basement_depth: np.ndarray,
    spec: dict[str, Any],
) -> dict[str, np.ndarray]:
    """Ground and basement loss (USD) of one storm; each basement storey through the engine.

    Args:
        buildings: Buildings (EPSG:4326).
        ground_depth: Ground-storey entry depth (m) per building.
        basement_depth: Storey depths (m), ``(n_buildings, max_levels)`` from
            ``basement_depths``.
        spec: City spec.

    Returns:
        ``ground_loss_usd``, ``basement_value_usd``, ``basement_loss_usd`` and ``loss_usd``.
    """
    cost: dict[str, float] = spec["loss"]["cost_usd_per_m2"]
    n_levels = basement_levels(buildings, spec)
    area = site_loss.footprint_area_m2(buildings)
    unit = np.array(
        [area[i] * float(cost[kind]) for i, kind in enumerate(site_loss._types(buildings, spec))]
    )
    ground = site_loss.price_buildings(
        buildings, ground_depth, site_loss.exposed_value_usd(buildings, spec), spec
    )["loss_usd"]
    basement_loss = np.zeros(len(buildings), dtype=float)
    depth = np.asarray(basement_depth, dtype=float)
    for k in range(depth.shape[1]):
        idx = np.nonzero(n_levels > k)[0]
        if idx.size == 0:
            continue
        sub = buildings.iloc[idx]
        basement_loss[idx] += site_loss.price_buildings(sub, depth[idx, k], unit[idx], spec)[
            "loss_usd"
        ]
    value = n_levels * unit
    return {
        "ground_loss_usd": ground,
        "basement_value_usd": value,
        "basement_loss_usd": basement_loss,
        "loss_usd": ground + basement_loss,
    }


def entrance_cells(
    grid: SiteGrid,
    buildings: gpd.GeoDataFrame,
    bed: np.ndarray,
    walls: np.ndarray,
    n_levels: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Ramp entrance cell (row, col) of each building: the lowest cell of its ring.

    The ring is that of ``site_loss.ring_cells``; ties go to the first cell in row-major
    order. Buildings without basements (``n_levels == 0``) or with an empty ring get -1.

    Args:
        grid: Site grid.
        buildings: Buildings (EPSG:4326).
        bed: Bed elevation (m), shape ``(ny, nx)``.
        walls: Boolean ``(ny, nx)`` wall raster.
        n_levels: Basement storeys per building.

    Returns:
        ``(rows, cols)`` int arrays of length ``len(buildings)``.
    """
    n = np.asarray(n_levels, dtype=int)
    rows_out = np.full(len(buildings), -1, dtype=int)
    cols_out = np.full(len(buildings), -1, dtype=int)
    for i, geom in enumerate(buildings.geometry):
        if n[i] <= 0:
            continue
        rows, cols = site_loss._cells(grid, geom)
        rr, cc = site_loss.ring_cells(grid, rows, cols, walls)
        if rr.size:
            k = int(np.argmin(np.asarray(bed)[rr, cc]))  # argmin: first minimum, row-major
            rows_out[i], cols_out[i] = int(rr[k]), int(cc[k])
    return rows_out, cols_out


class BasementSink:
    """Two-way coupled basements: a ``sink_fn`` for ``hydraulics.run`` (step R3d).

    Each building with an entrance cell takes ramp weir flow from the street water above the
    door sill, limited by the water above the sill in the shared cell and by its free
    capacity. The water is removed from the 2D flow by the solver.

    Algorithm:
        Wanted volume in a step: $$W_b = C\\,B\\,\\max(h - T, 0)^{3/2}\\,\\Delta t$$
        Cell scaling over the buildings $b$ sharing the cell:
        $$s = \\min\\big(1,\\ \\max(h - T, 0)\\,\\Delta x^2 / \\textstyle\\sum_b W_b\\big)$$
        Taken: $V_b \\mathrel{+}= \\min(s W_b,\\ K_b - V_b)$; removed depth in the cell is
        $\\sum_b \\Delta V_b / \\Delta x^2$.
        ASCII: W = C*B*max(h-T,0)^1.5*dt; s = min(1, max(h-T,0)*dx^2/sum(W));
        dV = min(s*W, K - V).

        $C$ weir coefficient (m^0.5/s), $B$ ramp width (m), $T$ sill (m), $h$ cell depth (m),
        $\\Delta t$ step (s), $\\Delta x$ cell size (m), $K_b$ capacity (m3), $V_b$ volume (m3).

    Attributes:
        volume_m3: Cumulative water per building (m3), before pumping.
    """

    def __init__(
        self,
        rows: np.ndarray,
        cols: np.ndarray,
        capacity_m3: np.ndarray,
        cell_m: float,
        spec: dict[str, Any],
    ) -> None:
        """Precompute the index arrays.

        Args:
            rows: Entrance row per building (-1: no basement).
            cols: Entrance column per building.
            capacity_m3: Basement capacity per building (m3).
            cell_m: Cell size (m).
            spec: City spec with ``basement`` and ``loss`` sections.
        """
        bs = spec["basement"]
        self._q = float(bs["weir_coefficient"]) * float(bs["ramp_width_m"])
        self._sill = float(spec["loss"]["entry_threshold_m"])
        self._pump = float(bs["pump_m3_h"])
        self._cell_area = float(cell_m) ** 2
        r = np.asarray(rows, dtype=int)
        c = np.asarray(cols, dtype=int)
        self._cap = np.asarray(capacity_m3, dtype=float)
        self.volume_m3 = np.zeros(r.size, dtype=float)
        self._idx = np.nonzero(r >= 0)[0]
        self._r = r[self._idx]
        self._c = c[self._idx]
        self._cap_active = self._cap[self._idx]
        # unique entrance cells and each active building's slot among them
        flat = self._r * (int(self._c.max()) + 1 if self._c.size else 1) + self._c
        self._cells, self._slot = np.unique(flat, return_inverse=True)
        first = np.zeros(self._cells.size, dtype=int)
        first[self._slot[::-1]] = np.arange(self._slot.size)[::-1]
        self._cell_r = self._r[first]
        self._cell_c = self._c[first]

    def __call__(self, t: float, h: np.ndarray, dt: float) -> np.ndarray:
        """Remove the weir flow of one step; return the removed depth raster (m).

        Args:
            t: Time (s), unused (the sink has no explicit time dependence).
            h: Updated street depth (m), shape ``(ny, nx)``.
            dt: Step (s).

        Returns:
            Depth (m) removed per cell, same shape as ``h``.
        """
        out = np.zeros_like(h, dtype=float)
        if self._idx.size == 0:
            return out
        head_cell = np.maximum(h[self._cell_r, self._cell_c] - self._sill, 0.0)
        if not head_cell.any():
            return out
        want = self._q * head_cell[self._slot] ** 1.5 * dt
        total = np.bincount(self._slot, weights=want, minlength=self._cells.size)
        avail = head_cell * self._cell_area
        scale = np.ones_like(total)
        over = total > avail
        scale[over] = avail[over] / total[over]
        take = np.minimum(want * scale[self._slot], self._cap_active - self.volume_m3[self._idx])
        take = np.maximum(take, 0.0)
        self.volume_m3[self._idx] += take
        removed = np.bincount(self._slot, weights=take, minlength=self._cells.size)
        out[self._cell_r, self._cell_c] = removed / self._cell_area
        return out

    def final_volume_m3(self, t_end_s: float) -> np.ndarray:
        """Volume (m3) left after pumping for ``t_end_s``: ``max(V - pump t / 3600, 0)``."""
        return np.maximum(self.volume_m3 - self._pump * float(t_end_s) / 3600.0, 0.0)


__all__ = [
    "BasementSink",
    "basement_depths",
    "basement_levels",
    "basement_volume_m3",
    "entrance_cells",
    "price_with_basements",
    "ring_series",
]
