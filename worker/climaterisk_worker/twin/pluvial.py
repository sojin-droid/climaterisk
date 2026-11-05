"""Rain (pluvial) flood on a real site — repo-specific design-storm method.

Design storm, NRCS curve-number losses, a capacity-limited sewer sink and open site edges on
the 2D local-inertial model (:mod:`climaterisk_worker.twin.hydraulics`). Not a calibrated
drainage model: the storm shape, sewer capacity and slope are assumptions in the spec.

Algorithm:
    Hyetograph: cumulative rain $P_k = F_k P_{tot}$ at $t_k = (k/K) T$, piecewise-constant
    intensity $i_k = (P_{k+1}-P_k)/(t_{k+1}-t_k)$.
    ASCII: P_k = F_k * P_tot at t_k = k/K * T; i_k = dP / dt in interval k.

    NRCS curve number (USDA TR-55): $S = 25400/CN - 254$, $I_a = 0.2 S$,
    $$Q = \\frac{(P-I_a)^2}{P-I_a+S}\\ (P > I_a),\\quad Q = 0\\ \\text{otherwise}$$
    ASCII: Q = (P - Ia)^2 / (P - Ia + S) for P > Ia, else 0.

    Excess rain rate in interval $k$: $r_k = [Q(P_{k+1}) - Q(P_k)]/(t_{k+1}-t_k)$.
    ASCII: r_k = (Q(P_k+1) - Q(P_k)) / dt_k.

    Roof runoff: with $N$ cells of which $N_o$ are open, the open-cell rate is
    $r\\,N/N_o$ and wall cells get 0, so the rain volume is the same as without buildings.

Units:
    $P$, $Q$, $S$, $I_a$ [mm]; $CN$ [-]; $t$ [s]; $i$, $r$ [m s$^{-1}$] (output of the rate
    functions); sewer capacity [mm h$^{-1}$] in the spec, [m s$^{-1}$] in the solver.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from climaterisk_worker.twin import hydraulics


def hyetograph(spec: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Breakpoints of the design storm.

    Args:
        spec: City spec; uses ``rain.cumulative_fraction``, ``total_mm``, ``duration_min``.

    Returns:
        ``(t_s, cum_mm)``: times (s) equally spaced over the storm and the cumulative rain
        (mm) there, both of length ``len(cumulative_fraction)``.
    """
    r = spec["rain"]
    frac = np.asarray(r["cumulative_fraction"], dtype=float)
    t_s = np.linspace(0.0, float(r["duration_min"]) * 60.0, frac.size)
    return t_s, frac * float(r["total_mm"])


def _interval(t_s: float, t_grid: np.ndarray) -> int:
    """Index of the hyetograph interval holding ``t_s``, or -1 outside the storm."""
    if t_s < t_grid[0] or t_s >= t_grid[-1]:
        return -1
    return int(np.searchsorted(t_grid, t_s, side="right")) - 1


def rain_rate(t_s: float, spec: dict[str, Any]) -> float:
    """Design-storm rain intensity at time ``t_s`` (m/s), constant within each interval.

    Args:
        t_s: Time since the storm start (s).
        spec: City spec.

    Returns:
        Intensity (m s$^{-1}$); 0 before the start and from the end of the storm.
    """
    t_grid, cum = hyetograph(spec)
    k = _interval(t_s, t_grid)
    if k < 0:
        return 0.0
    return float((cum[k + 1] - cum[k]) / (t_grid[k + 1] - t_grid[k]) / 1000.0)


def runoff_mm(p_mm: Any, cn: float) -> Any:
    """NRCS curve-number direct runoff.

    Algorithm:
        $S = 25400/CN - 254$, $I_a = 0.2 S$, $Q = (P-I_a)^2/(P-I_a+S)$ for $P > I_a$ else 0.
        ASCII: Q = (P - Ia)^2 / (P - Ia + S) if P > Ia else 0.

    Args:
        p_mm: Cumulative rain $P$ (mm), scalar or array.
        cn: Curve number $CN$ (-), in (0, 100].

    Returns:
        Cumulative runoff $Q$ (mm), same shape as ``p_mm``.
    """
    p = np.asarray(p_mm, dtype=float)
    s_mm = 25400.0 / cn - 254.0
    excess = p - 0.2 * s_mm
    q = np.where(excess > 0.0, excess**2 / np.maximum(excess + s_mm, 1e-12), 0.0)
    return float(q) if q.ndim == 0 else q


def excess_rate(t_s: float, spec: dict[str, Any], cn: float) -> float:
    """Rain excess rate (m/s) after NRCS losses, constant within each hyetograph interval.

    Algorithm:
        $r_k = [Q(P_{k+1}) - Q(P_k)] / (t_{k+1} - t_k)$, so $\\int r\\,dt = Q(P_{tot})$.
        ASCII: r_k = (Q(P_k+1) - Q(P_k)) / (t_k+1 - t_k).

    Args:
        t_s: Time since the storm start (s).
        spec: City spec.
        cn: Curve number (-).

    Returns:
        Excess rate (m s$^{-1}$); 0 outside the storm.
    """
    t_grid, cum = hyetograph(spec)
    k = _interval(t_s, t_grid)
    if k < 0:
        return 0.0
    dq_mm = float(runoff_mm(cum[k + 1], cn) - runoff_mm(cum[k], cn))
    return dq_mm / (t_grid[k + 1] - t_grid[k]) / 1000.0


def run_pluvial(
    ground: np.ndarray,
    walls: np.ndarray,
    cell_m: float,
    spec: dict[str, Any],
    snapshot_every_s: float | None = None,
    sink_fn: Any | None = None,
) -> dict[str, Any]:
    """Run the design storm over a site on the 2D hydraulic model.

    Rain excess (``cn_paved``) falls on every cell; the share that falls on wall cells
    (roofs) is moved onto the open cells by scaling their rate by ``n_cells / n_open``
    (spread uniformly: a simplification). The sewer capacity is per unit of *catchment*
    area — roofs drain to the sewer through downpipes — so the open cells drain at
    ``sewer_capacity_mm_h x n_cells / n_open`` (a capacity-limited sink that only takes water
    present). With or without buildings the site then gets the same rain and the same total
    sewer capacity; only where the water can go changes. The site edges let water out at
    Manning normal flow with slope ``edge_slope``.

    Args:
        ground: Bed elevation (m), shape ``(ny, nx)``.
        walls: Boolean raster of building cells.
        cell_m: Cell size (m).
        spec: City spec (``rain`` and ``hydraulic`` sections).
        snapshot_every_s: Optional snapshot interval (s) for animation.
        sink_fn: Optional ``(t, h, dt) -> depth raster`` removed from the flow every step
            (see :func:`hydraulics.run`), e.g. a ``BasementSink``.

    Returns:
        Dict with ``h_max``, ``h`` (m), ``rain_volume_m3``, ``sink_volume_m3``,
        ``outflow_volume_m3``, ``sink_fn_volume_m3``, ``max_froude`` and, if requested,
        ``snapshots``.
    """
    r = spec["rain"]
    hyd = spec["hydraulic"]
    walls = np.asarray(walls, dtype=bool)
    n_cells = walls.size
    n_open = int(n_cells - walls.sum())
    if n_open == 0:
        raise ValueError("no open cells: the whole site is walls")
    scale = n_cells / n_open
    cn = float(r["cn_paved"])
    open_mask = ~walls

    def rain_fn(t_s: float) -> np.ndarray:
        return np.where(open_mask, excess_rate(t_s, spec, cn) * scale, 0.0)

    rain_fn.breakpoints_s = hyetograph(spec)[0]  # type: ignore[attr-defined]
    drain = np.where(open_mask, float(r["sewer_capacity_mm_h"]) / 3.6e6 * scale, 0.0)
    t_end = (float(r["duration_min"]) + float(r["recession_min"])) * 60.0
    res = hydraulics.run(
        np.zeros(ground.shape),
        np.asarray(ground, dtype=float),
        cell_m,
        cell_m,
        float(hyd["manning_n"]),
        t_end,
        infiltration=drain,
        alpha_cfl=float(hyd["alpha_cfl"]),
        snapshot_every_s=snapshot_every_s,
        blocked=walls,
        rain_fn=rain_fn,
        open_edges=float(r["edge_slope"]),
        sink_fn=sink_fn,
    )
    return res


__all__ = ["excess_rate", "hyetograph", "rain_rate", "run_pluvial", "runoff_mm"]
