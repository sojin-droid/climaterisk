"""Verification gate for the 2D local-inertial hydraulic solver (AGENTS.md step 6).

Every reference below is an analytical solution or a conservation law; the tolerances were
fixed from the model's known error, not from whatever the code happened to produce:

- The local inertial approximation drops the advection term d(q^2/h)/dx. Its error therefore
  does not shrink with the grid; it grows with the Froude number (Bates et al. 2010; de
  Almeida & Bates 2013; Neal et al. 2012 recommend Fr < 0.5). The analytical tests sit in
  that range and each also checks that the error is a model error (flat under refinement),
  not a discretisation bug.
- Mass and lake-at-rest are exact properties of the scheme and are held to round-off or 0.1 %.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker.twin.hydraulics import run  # noqa: E402

G = 9.81


def _l1(h: np.ndarray, h_exact: np.ndarray) -> float:
    """Normalised L1 depth error ||h - h_exact||_1 / ||h_exact||_1 (dimensionless)."""
    return float(np.abs(h - h_exact).sum() / np.abs(h_exact).sum())


# ---------------------------------------------------------------------------
# 1. Dam break on a wet bed — Stoker (1957) analytical solution
# ---------------------------------------------------------------------------
def _stoker(x: np.ndarray, t: float, h_l: float, h_r: float, x0: float) -> np.ndarray:
    """Exact depth for a frictionless dam break on a wet, flat bed.

    Algorithm:
        Middle state $h_m$ from matching the rarefaction and the shock:
        $$2(\\sqrt{g h_L} - \\sqrt{g h_m}) = (h_m - h_R)\\sqrt{\\frac{g (h_m + h_R)}{2 h_m h_R}}$$
        then, with $\\xi = (x - x_0)/t$, $c = \\sqrt{g h}$, $u_m = 2(c_L - c_m)$ and shock
        speed $s = h_m u_m / (h_m - h_R)$:
        $h = h_L$ for $\\xi < -c_L$; $h = (2 c_L - \\xi)^2 / (9 g)$ for $-c_L < \\xi < u_m - c_m$;
        $h = h_m$ for $u_m - c_m < \\xi < s$; $h = h_R$ beyond.
        ASCII: rarefaction h = (2 cL - xi)^2 / (9 g); plateau h_m; shock at s; then h_R.

    Depths in m, x in m, t in s, g in m/s^2.
    """
    c_l = math.sqrt(G * h_l)

    def mismatch(h_m: float) -> float:
        u_rare = 2.0 * (c_l - math.sqrt(G * h_m))
        u_shock = (h_m - h_r) * math.sqrt(G * (h_m + h_r) / (2.0 * h_m * h_r))
        return u_rare - u_shock

    lo, hi = h_r, h_l  # mismatch(lo) > 0 > mismatch(hi)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if mismatch(mid) > 0 else (lo, mid)
    h_m = 0.5 * (lo + hi)
    c_m = math.sqrt(G * h_m)
    u_m = 2.0 * (c_l - c_m)
    s = h_m * u_m / (h_m - h_r)
    xi = (x - x0) / t
    return np.where(
        xi < -c_l,
        h_l,
        np.where(xi < u_m - c_m, (2.0 * c_l - xi) ** 2 / (9.0 * G), np.where(xi < s, h_m, h_r)),
    )


def _dam_break_error(nx: int) -> float:
    length, h_l, h_r, t_end = 100.0, 1.0, 0.5, 5.0
    dx = length / nx
    x = (np.arange(nx) + 0.5) * dx
    h0 = np.where(x < 50.0, h_l, h_r).reshape(1, -1)
    res = run(h0, np.zeros_like(h0), dx, dx, 0.0, t_end)
    return _l1(res["h"][0], _stoker(x, t_end, h_l, h_r, 50.0))


def test_dam_break_wet_bed_vs_stoker() -> None:
    """h_R/h_L = 0.5 (plateau Fr ~ 0.3). Tolerance 5 % normalised L1 (measured ~2.4 %).

    The error comes from the dropped advection term, so it must also be flat under
    refinement: halving dx may not change it by more than 20 % either way.
    """
    coarse, fine = _dam_break_error(200), _dam_break_error(400)
    assert coarse < 0.05, f"Stoker L1 {coarse:.4f} >= 0.05"
    assert fine < 0.05, f"Stoker L1 {fine:.4f} >= 0.05"
    assert abs(fine - coarse) < 0.2 * coarse, (coarse, fine)


# ---------------------------------------------------------------------------
# 2. Thacker (1981) planar oscillation in a parabolic bowl — wetting and drying
# ---------------------------------------------------------------------------
def _thacker_error(nx: int, amplitude_m: float) -> float:
    """Normalised L1 error after one full period.

    Algorithm:
        Bed $z = h_0 (\\tilde x^2 / a^2 - 1)$, $\\tilde x = x - L/2$. Exact frictionless
        solution: a rigid parabolic water body sliding back and forth,
        $$h(x,t) = \\max\\Big(0,\\;
          h_0\\Big[1 - \\frac{(\\tilde x + A\\cos\\omega t)^2}{a^2}\\Big]\\Big),
          \\qquad \\omega = \\frac{\\sqrt{2 g h_0}}{a}$$
        with uniform velocity $u = A\\omega\\sin\\omega t$ (starts at rest).
        ASCII: h = max(0, h0 * (1 - ((x~ + A cos(w t)) / a)^2)), w = sqrt(2 g h0) / a.

    $h_0$ centre depth (m), $a$ half-width of the water body (m), $A$ amplitude (m).
    """
    length, a, h0 = 4.0, 1.0, 0.5
    omega = math.sqrt(2.0 * G * h0) / a
    period = 2.0 * math.pi / omega
    dx = length / nx
    xt = (np.arange(nx) + 0.5) * dx - length / 2.0
    bed = (h0 * (xt**2 / a**2 - 1.0)).reshape(1, -1)

    def exact(t: float) -> np.ndarray:
        return np.maximum(0.0, h0 * (1.0 - ((xt + amplitude_m * math.cos(omega * t)) / a) ** 2))

    res = run(exact(0.0).reshape(1, -1), bed, dx, dx, 0.0, period)
    return _l1(res["h"][0], exact(period))


def test_thacker_bowl_one_period() -> None:
    """A = 0.05 m (peak Fr ~ 0.07): tolerance 2 % L1 after a period (measured ~1.1 %)."""
    coarse, fine = _thacker_error(200, 0.05), _thacker_error(400, 0.05)
    assert coarse < 0.02, f"Thacker L1 {coarse:.4f} >= 0.02"
    assert fine < 0.02, f"Thacker L1 {fine:.4f} >= 0.02"
    assert abs(fine - coarse) < 0.2 * coarse, (coarse, fine)


def test_thacker_error_grows_with_froude() -> None:
    """Documents the limit of the scheme: faster sloshing (higher Fr) -> larger error.

    Measured: A = 0.05 / 0.1 / 0.2 m (Fr ~0.07 / 0.14 / 0.28) -> ~1 % / ~4 % / ~9 %.
    Do not use this model for fast, supercritical flow (dam-break fronts on a dry bed).
    """
    errs = [_thacker_error(200, amp) for amp in (0.05, 0.1, 0.2)]
    assert errs == sorted(errs), errs
    assert errs[-1] < 0.15, errs


# ---------------------------------------------------------------------------
# 3. Mass balance: inflow + rain - storage = 0 to < 0.1 %
# ---------------------------------------------------------------------------
def test_mass_balance_rain_plus_inflow_on_uneven_ground() -> None:
    """Closed 2D box over rough ground with dry cells, default wet/dry threshold.

    Stored volume must equal rain + inflow to < 0.1 % (the outflow limiter means no
    negative depth is ever clipped, so mass is conserved to round-off in practice).
    """
    rng = np.random.default_rng(20261030)
    ny, nx, dx = 30, 40, 5.0
    bed = rng.normal(0.0, 0.3, size=(ny, nx)) + 0.01 * np.arange(nx) * dx
    rain_rate = 50e-3 / 3600.0  # 50 mm/h in m/s
    inflow = 2.0  # m^3/s
    t_end = 600.0

    def inflow_fn(t: float) -> tuple[np.ndarray, np.ndarray, float]:
        return np.array([ny // 2]), np.array([0]), inflow

    res = run(
        np.zeros((ny, nx)),
        bed,
        dx,
        dx,
        0.03,
        t_end,
        rain=np.full((ny, nx), rain_rate),
        inflow_fn=inflow_fn,
    )
    stored = float(res["h"].sum() * dx * dx)
    added = (rain_rate * nx * ny * dx * dx + inflow) * t_end
    assert abs(stored - added) / added < 1e-3, (stored, added)


# ---------------------------------------------------------------------------
# 4. Lake at rest stays at rest over uneven ground
# ---------------------------------------------------------------------------
def test_lake_at_rest_uneven_ground() -> None:
    """A flat surface over a bowl with dry rims must not move (well-balanced scheme)."""
    n = 40
    xv, yv = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    bed = 0.01 * ((xv - n / 2) ** 2 + (yv - n / 2) ** 2)
    h0 = np.maximum(1.0 - bed, 0.0)
    res = run(h0, bed, 1.0, 1.0, 0.0, t_end=10.0)
    np.testing.assert_allclose(res["h"], h0, rtol=0, atol=1e-12)
    np.testing.assert_allclose(res["q_x"], 0.0, rtol=0, atol=1e-12)
    np.testing.assert_allclose(res["q_y"], 0.0, rtol=0, atol=1e-12)
