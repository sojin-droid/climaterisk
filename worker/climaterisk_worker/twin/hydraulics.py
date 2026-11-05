"""2D local-inertial shallow-water solver — NumPy only.

Implements the Bates, Horritt & Fewtrell (2010) local inertial scheme on a
uniform raster, stabilised by the de Almeida, Bates, Freer & Souvignet (2012)
q-centred averaging and adaptive time step. Manning friction, wet/dry threshold,
rainfall, infiltration and point/area inflow hydrographs are included.

Algorithm:
    Continuity
    $$\\frac{\\partial h}{\\partial t} = -\\frac{\\partial q_x}{\\partial x}
    -\\frac{\\partial q_y}{\\partial y} + R - I$$
    ASCII: dh/dt = -dq_x/dx - dq_y/dy + R - I

    q-centred momentum update (de Almeida et al. 2012, eq. 12; Bates et al. 2010)
    $$q_x^{n+1} = \\frac{q_c - g\\,h_f\\,\\Delta t\\,\\nabla_w\\eta}
    {1 + g\\,\\Delta t\\,n^2\\,|q_c|\\,/\\,h_f^{7/3}}$$
    ASCII: q_x^{n+1} = [q_c - g h_f dt Sw] / [1 + g dt n^2 |q_c| / h_f^{7/3}]

    where $q_c = \\theta\\,q_x^n + \\tfrac{1-\\theta}{2}(q_x^{n}_{\\text{prev}}
    + q_x^{n}_{\\text{next}})$ and $h_f = \\max(\\eta_L,\\eta_R) -
    \\max(z_L,z_R)$. $q_y$ is analogous.

    Adaptive time step (de Almeida et al. 2012, eq. 15)
    $$\\Delta t_{\\max} = \\alpha\\,\\frac{\\min(\\Delta x,\\Delta y)}
    {\\sqrt{g\\,\\max(h,\\;h_{\\text{dry}})}}$$
    ASCII: dt_max = alpha * min(dx, dy) / sqrt(g * max(h, h_dry))

Units:
    $h$ water depth [m], $\\mathbf{q}=(q_x,q_y)$ specific discharge
    [m$^2$ s$^{-1}$], $z$ bed elevation [m], $\\eta=h+z$ free surface [m],
    $\\Delta x,\\Delta y$ grid spacing [m], $t$ time [s], $R$ rainfall [m s$^{-1}$],
    $I$ infiltration [m s$^{-1}$], $Q_{\\text{in}}$ inflow discharge [m$^3$ s$^{-1}$],
    $n$ Manning roughness [s m$^{-1/3}$], $g$ gravity [m s$^{-2}$].
"""

from __future__ import annotations

from typing import Any

import numpy as np


def adaptive_dt(
    h: np.ndarray,
    dx: float,
    dy: float,
    manning_n: float,
    alpha_cfl: float = 0.7,
    g: float = 9.81,
    h_dry: float = 1e-4,
) -> float:
    """Maximum stable explicit time step from the de Almeida et al. (2012) CFL condition.

    Algorithm:
        $$\\Delta t_{\\max} = \\alpha\\,\\frac{\\min(\\Delta x,\\Delta y)}
        {\\sqrt{g\\,\\max(h,\\;h_{\\text{dry}})}}$$
        ASCII: dt_max = alpha * min(dx, dy) / sqrt(g * max(h, h_dry))

    Args:
        h: Current water depth [m], shape ``(ny, nx)``.
        dx, dy: Grid spacing [m].
        manning_n: Manning roughness [s m$^{-1/3}$].
        alpha_cfl: Stability coefficient [$-$], $0 < \\alpha < 1$.
        g: Gravity [m s$^{-2}$].
        h_dry: Depth threshold for wet/dry [m].

    Returns:
        Maximum stable time step [s].
    """
    h_eff = np.maximum(h, h_dry)
    h_max = float(np.max(h_eff))
    if h_max <= 0.0:
        return float("inf")
    return float(alpha_cfl * min(dx, dy) / np.sqrt(g * h_max))


def step(
    h: np.ndarray,
    q_x: np.ndarray,
    q_y: np.ndarray,
    bed_z: np.ndarray,
    dx: float,
    dy: float,
    dt: float,
    manning_n: float,
    rain: np.ndarray | None = None,
    infiltration: np.ndarray | None = None,
    inflow_cells: tuple[np.ndarray, np.ndarray] | None = None,
    inflow_rate: float = 0.0,
    theta: float = 0.7,
    h_dry: float = 1e-4,
    g: float = 9.81,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Advance the local-inertial solver by one explicit time step.

    Algorithm:
        Flux update (q-centred, de Almeida et al. 2012):
        $$q_x^{n+1} = \\frac{q_c - g\\,h_f\\,\\Delta t\\,\\nabla_w\\eta}
        {1 + g\\,\\Delta t\\,n^2\\,|q^n|\\,/\\,h_f^{7/3}}$$
        ASCII: q_x^{n+1} = [q_c - g h_f dt Sw] / [1 + g dt n^2 |q^n| / h_f^{7/3}]

        Continuity (explicit Euler):
        $$h^{n+1} = h^n + \\Delta t\\bigl[-\\nabla\\cdot\\mathbf{q}^{n+1}
        + R - I + Q_{\\text{in}}/(\\Delta x\\,\\Delta y)\\bigr]$$
        ASCII: h^{n+1} = h^n + dt * [-div(q) + R - I + Qin/(dx*dy)]

    Args:
        h: Water depth [m], shape ``(ny, nx)``.
        q_x: x-direction specific discharge [m$^2$ s$^{-1}$], shape ``(ny, nx+1)``.
        q_y: y-direction specific discharge [m$^2$ s$^{-1}$], shape ``(ny+1, nx)``.
        bed_z: Bed elevation [m], shape ``(ny, nx)``.
        dx, dy: Grid spacing [m].
        dt: Time step [s].
        manning_n: Manning roughness [s m$^{-1/3}$].
        rain: Rainfall rate [m s$^{-1}$], shape ``(ny, nx)`` or ``None``.
        infiltration: Infiltration rate [m s$^{-1}$], shape ``(ny, nx)`` or ``None``.
        inflow_cells: Tuple of (row_indices, col_indices) for point inflow cells.
        inflow_rate: Total inflow discharge [m$^3$ s$^{-1}$], split equally over inflow_cells.
        theta: q-centring weight [$-$], $0 \\le \\theta \\le 1$.
        h_dry: Wet/dry threshold [m]; faces with $h_f < h_{\\text{dry}}$ get $q=0$.
        g: Gravity [m s$^{-2}$].

    Returns:
        ``(h_new, q_x_new, q_y_new)`` updated arrays.
    """
    ny, nx = h.shape
    eta = h + bed_z

    # --- x-fluxes on internal faces 1..nx-1 ---
    q_x_new = np.zeros_like(q_x)
    if nx > 1:
        eta_L = eta[:, :-1]
        eta_R = eta[:, 1:]
        z_L = bed_z[:, :-1]
        z_R = bed_z[:, 1:]
        h_f = np.maximum(eta_L, eta_R) - np.maximum(z_L, z_R)
        S_w = (eta_R - eta_L) / dx
        wet = h_f > h_dry

        q_c = theta * q_x[:, 1:-1] + (1.0 - theta) * 0.5 * (q_x[:, :-2] + q_x[:, 2:])
        q_c = np.where(wet, q_c, 0.0)
        h_f_face = np.where(wet, h_f, 1.0)
        q_n = q_x[:, 1:-1]

        num = q_c - g * h_f_face * dt * S_w
        den = 1.0 + g * dt * manning_n**2 * np.abs(q_n) / (h_f_face ** (7.0 / 3.0))
        q_x_new[:, 1:-1] = np.where(wet, num / den, 0.0)

    # --- y-fluxes on internal faces 1..ny-1 ---
    q_y_new = np.zeros_like(q_y)
    if ny > 1:
        eta_B = eta[:-1, :]
        eta_T = eta[1:, :]
        z_B = bed_z[:-1, :]
        z_T = bed_z[1:, :]
        h_f = np.maximum(eta_B, eta_T) - np.maximum(z_B, z_T)
        S_w = (eta_T - eta_B) / dy
        wet = h_f > h_dry

        q_c = theta * q_y[1:-1, :] + (1.0 - theta) * 0.5 * (q_y[:-2, :] + q_y[2:, :])
        q_c = np.where(wet, q_c, 0.0)
        h_f_face = np.where(wet, h_f, 1.0)
        q_n = q_y[1:-1, :]

        num = q_c - g * h_f_face * dt * S_w
        den = 1.0 + g * dt * manning_n**2 * np.abs(q_n) / (h_f_face ** (7.0 / 3.0))
        q_y_new[1:-1, :] = np.where(wet, num / den, 0.0)

    # --- outflow limiter: no cell may lose more water than it holds ---
    _limit_outflow(h, q_x_new, q_y_new, dx, dy, dt)

    # --- continuity ---
    div_x = (q_x_new[:, 1:] - q_x_new[:, :-1]) / dx
    div_y = (q_y_new[1:, :] - q_y_new[:-1, :]) / dy

    dh = dt * (-div_x - div_y)
    if rain is not None:
        dh += dt * rain
    if inflow_cells is not None and inflow_rate != 0.0:
        rows, cols = inflow_cells
        for r, c in zip(rows, cols, strict=True):
            dh[r, c] += dt * inflow_rate / (dx * dy) / len(rows)
    h_new = h + dh
    if infiltration is not None:
        # infiltration can only take the water that is there
        h_new = h_new - np.minimum(dt * infiltration, np.maximum(h_new, 0.0))
    # the limiter keeps h_new >= 0 up to round-off; clip only that round-off
    h_new = np.maximum(h_new, 0.0)

    return h_new, q_x_new, q_y_new


def _limit_outflow(
    h: np.ndarray, q_x: np.ndarray, q_y: np.ndarray, dx: float, dy: float, dt: float
) -> None:
    """Scale each cell's outgoing face fluxes so it cannot drain below zero (in place).

    Without this, an explicit step can take more water out of a shallow cell than it holds
    and the negative depth would have to be clipped, creating mass. Each face's flux leaves
    exactly one cell (its upwind cell), so scaling by that cell's factor conserves mass.

    Algorithm:
        $$V^{out}_c = \Delta t\Big(\frac{\sum q^{+}_{x}}{\Delta x}
          + \frac{\sum q^{+}_{y}}{\Delta y}\Big),\qquad
          r_c = \min\big(1,\; h_c / V^{out}_c\big)$$
        ASCII: V_out_c = dt * (sum of outgoing q_x / dx + sum of outgoing q_y / dy);
        r_c = min(1, h_c / V_out_c); every outgoing flux of cell c is multiplied by r_c.

        $h_c$ depth (m), $q$ face discharge per unit width (m^2/s), $\Delta t$ (s),
        $\Delta x, \Delta y$ (m), $r_c$ dimensionless.
    """
    out_x = (np.maximum(q_x[:, 1:], 0.0) + np.maximum(-q_x[:, :-1], 0.0)) / dx
    out_y = (np.maximum(q_y[1:, :], 0.0) + np.maximum(-q_y[:-1, :], 0.0)) / dy
    v_out = dt * (out_x + out_y)
    drained = v_out > h
    if not drained.any():
        return
    r = np.ones_like(h)
    r[drained] = h[drained] / v_out[drained]
    # x faces: positive flux leaves the left cell, negative leaves the right cell
    fx = q_x[:, 1:-1]
    q_x[:, 1:-1] = np.where(fx > 0, fx * r[:, :-1], fx * r[:, 1:])
    fy = q_y[1:-1, :]
    q_y[1:-1, :] = np.where(fy > 0, fy * r[:-1, :], fy * r[1:, :])


def run(
    h0: np.ndarray,
    bed_z: np.ndarray,
    dx: float,
    dy: float,
    manning_n: float,
    t_end: float,
    rain: np.ndarray | None = None,
    infiltration: np.ndarray | None = None,
    inflow_fn: Any | None = None,
    theta: float = 0.7,
    alpha_cfl: float = 0.7,
    h_dry: float = 1e-4,
    g: float = 9.81,
    recorder: Any | None = None,
    stage_fn: Any | None = None,
    froude_min_depth: float = 0.05,
    snapshot_every_s: float | None = None,
) -> dict[str, Any]:
    """Run the local-inertial solver from $t=0$ to $t_{\\text{end}}$.

    Algorithm:
        Repeatedly call :func:`step` with an adaptive time step from
        :func:`adaptive_dt` until $t \\ge t_{\\text{end}}$.

    Args:
        h0: Initial water depth [m], shape ``(ny, nx)``.
        bed_z: Bed elevation [m], shape ``(ny, nx)``.
        dx, dy: Grid spacing [m].
        manning_n: Manning roughness [s m$^{-1/3}$].
        t_end: Final simulation time [s].
        rain: Rainfall rate [m s$^{-1}$], shape ``(ny, nx)`` or ``None``.
        infiltration: Infiltration rate [m s$^{-1}$], shape ``(ny, nx)`` or ``None``.
        inflow_fn: Callable ``(t, h, q_x, q_y) -> (rows, cols, rate)`` or ``None``.
        theta: q-centring weight [$-$].
        alpha_cfl: CFL safety coefficient [$-$].
        h_dry: Wet/dry threshold [m].
        g: Gravity [m s$^{-2}$].
        recorder: Optional object with ``record(t, h, q_x, q_y)`` method.
        stage_fn: Callable ``t -> (rows, cols, eta)`` giving a water-surface boundary: after
            every step the depth in those cells is reset to ``max(0, eta - z)`` (m). The
            water this adds or removes is counted in ``boundary_volume_m3``.
        froude_min_depth: Cells shallower than this (m) are left out of ``max_froude``; the
            thin film at a wet/dry front has meaningless velocities.
        snapshot_every_s: If set (s), store the depth raster at $t=0$, at the first step
            reaching each further multiple of this interval, and at the end time.

    Returns:
        Dict with ``h``, ``q_x``, ``q_y``, ``t``, ``n_steps``, ``h_max`` (peak depth per
        cell over the run, m), ``boundary_volume_m3`` (net water added by ``stage_fn``,
        m^3; zero without it) and ``max_froude`` (largest cell Froude number over the run,
        dimensionless). With ``snapshot_every_s`` also ``snapshots``: a list of
        ``(t_s, h)`` pairs (``h`` a copy, m) with strictly increasing times, the last being
        the end time.

    Algorithm:
        Cell Froude number from the face discharges averaged to the cell centre:
        $$\mathrm{Fr} = \frac{\sqrt{\bar q_x^2 + \bar q_y^2}}{h\,\sqrt{g h}}$$
        ASCII: Fr = sqrt(qx_mean^2 + qy_mean^2) / (h * sqrt(g h)), for h > froude_min_depth.
    """
    ny, nx = h0.shape
    q_x = np.zeros((ny, nx + 1), dtype=h0.dtype)
    q_y = np.zeros((ny + 1, nx), dtype=h0.dtype)
    h = h0.copy()
    t = 0.0
    n_steps = 0
    rate = 0.0
    boundary_volume = 0.0
    max_froude = 0.0

    def _apply_stage(time: float) -> float:
        """Reset the boundary cells to the stage at ``time``; return the volume added (m^3)."""
        rows_b, cols_b, eta_b = stage_fn(time)  # type: ignore[misc]
        target = np.maximum(float(eta_b) - bed_z[rows_b, cols_b], 0.0)
        added = float((target - h[rows_b, cols_b]).sum() * dx * dy)
        h[rows_b, cols_b] = target
        return added

    if stage_fn is not None:
        boundary_volume += _apply_stage(0.0)
    h_max = h.copy()
    snapshots: list[tuple[float, np.ndarray]] = []
    next_snapshot = 0.0
    if snapshot_every_s is not None:
        snapshots.append((0.0, h.copy()))
        next_snapshot = float(snapshot_every_s)

    while t < t_end:
        dt = adaptive_dt(h, dx, dy, manning_n, alpha_cfl, g, h_dry)
        dt = min(dt, t_end - t)

        inflow_cells = None
        if inflow_fn is not None:
            rows, cols, rate = inflow_fn(t)
            inflow_cells = (rows, cols)

        h, q_x, q_y = step(
            h,
            q_x,
            q_y,
            bed_z,
            dx,
            dy,
            dt,
            manning_n,
            rain=rain,
            infiltration=infiltration,
            inflow_cells=inflow_cells,
            inflow_rate=rate,
            theta=theta,
            h_dry=h_dry,
            g=g,
        )
        t += dt
        n_steps += 1
        if stage_fn is not None:
            boundary_volume += _apply_stage(t)
        np.maximum(h_max, h, out=h_max)
        deep = h > froude_min_depth
        if deep.any():
            qx_c = 0.5 * (q_x[:, 1:] + q_x[:, :-1])
            qy_c = 0.5 * (q_y[1:, :] + q_y[:-1, :])
            hd = h[deep]
            fr = np.hypot(qx_c[deep], qy_c[deep]) / (hd * np.sqrt(g * hd))
            max_froude = max(max_froude, float(fr.max()))
        if snapshot_every_s is not None and (t >= next_snapshot or t >= t_end):
            if t > snapshots[-1][0]:
                snapshots.append((t, h.copy()))
            while next_snapshot <= t:
                next_snapshot += float(snapshot_every_s)
        if recorder is not None:
            recorder.record(t, h, q_x, q_y)

    result: dict[str, Any] = {
        "h": h,
        "q_x": q_x,
        "q_y": q_y,
        "t": t,
        "n_steps": n_steps,
        "h_max": h_max,
        "boundary_volume_m3": boundary_volume,
        "max_froude": max_froude,
    }
    if snapshot_every_s is not None:
        result["snapshots"] = snapshots
    return result


__all__ = ["adaptive_dt", "run", "step"]
