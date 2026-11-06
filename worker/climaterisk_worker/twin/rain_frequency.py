"""Rainfall frequency analysis from an hourly record (step R3b; no I/O).

Annual maxima of the ``window_h``-hour rain depth, sample L-moments (Hosking 1990), Gumbel
and GEV fits by L-moments (Hosking, Wallis & Wood 1985) and a bootstrap band of the
return levels. Pure NumPy/pandas; the caller reads the files.

Algorithm:
    Annual maximum of the rolling ``w``-hour total, year of the window's last hour:
    $$M_y = \\max_{t \\in y} \\sum_{j=0}^{w-1} r_{t-j}$$
    Unbiased probability-weighted moments of the ordered sample $x_{(1)} \\le \\dots \\le
    x_{(n)}$ and L-moments:
    $$b_r = \\frac1n\\sum_{i=1}^{n} \\frac{\\binom{i-1}{r}}{\\binom{n-1}{r}} x_{(i)},\\quad
    \\lambda_1 = b_0,\\; \\lambda_2 = 2b_1 - b_0,\\; \\tau_3 = (6b_2 - 6b_1 + b_0)/\\lambda_2$$
    Gumbel: $\\alpha = \\lambda_2/\\ln 2$, $u = \\lambda_1 - \\gamma\\alpha$. GEV (Hosking sign,
    $k>0$ bounded above): $c = 2/(3+\\tau_3) - \\ln 2/\\ln 3$, $k = 7.8590c + 2.9554c^2$,
    $\\alpha = \\lambda_2 k / ((1-2^{-k})\\Gamma(1+k))$,
    $\\xi = \\lambda_1 - \\alpha(1-\\Gamma(1+k))/k$.
    Return level for return period $T$, $F = 1 - 1/T$:
    $$x_T = u - \\alpha\\ln(-\\ln F)\\ (\\text{Gumbel}),\\qquad
    x_T = \\xi + \\frac{\\alpha}{k}\\big(1 - (-\\ln F)^k\\big)\\ (\\text{GEV})$$
    ASCII: b_r = mean_i[C(i-1,r)/C(n-1,r) x_(i)]; l1 = b0; l2 = 2 b1 - b0; t3 = l3/l2;
    Gumbel x_T = u - a ln(-ln F); GEV x_T = xi + a/k (1 - (-ln F)^k).

    $r$ hourly rain (mm), $w$ window (h), $M_y$ annual maximum (mm), $n$ sample size (years),
    $\\lambda_{1,2}$ L-moments (mm), $\\tau_3$ L-skewness (-), $\\gamma$ Euler's constant,
    $u,\\xi$ location (mm), $\\alpha$ scale (mm), $k$ shape (-), $T$ return period (years).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

EULER_GAMMA = 0.5772156649
_K_EPS = 1e-9


def annual_maxima(rain_mm: pd.Series, window_h: int) -> pd.Series:
    """Annual maximum of the rolling ``window_h``-hour rain total.

    Args:
        rain_mm: Hourly depth (mm) on a ``DatetimeIndex``; absent hours and NaN count as 0.
        window_h: Window length (hours), at least 1.

    Returns:
        Maximum window total (mm) per calendar year of the window's last hour, indexed by
        int year.
    """
    s = rain_mm[~rain_mm.index.duplicated(keep="first")].sort_index()
    full = pd.date_range(s.index.min().floor("h"), s.index.max().ceil("h"), freq="h")
    hourly = s.reindex(full).fillna(0.0).astype(float)
    total = hourly.rolling(int(window_h), min_periods=1).sum()
    out = total.groupby(total.index.year).max()
    out.index = out.index.astype(int)
    logger.debug("annual maxima: %d hours -> %d years (window %d h)", len(full), len(out), window_h)
    return out


def lmoments(x: np.ndarray) -> tuple[float, float, float]:
    """Sample L-moments ``(l1, l2, t3)`` from unbiased PWMs (Hosking 1990).

    Args:
        x: Sample (any order), at least 3 values.

    Returns:
        Mean ``l1``, L-scale ``l2`` and L-skewness ``t3`` (see the module Algorithm).
    """
    xs = np.sort(np.asarray(x, dtype=float))
    n = xs.size
    i = np.arange(1, n + 1, dtype=float)
    b0 = float(xs.mean())
    b1 = float(np.mean((i - 1) / (n - 1) * xs))
    b2 = float(np.mean((i - 1) * (i - 2) / ((n - 1) * (n - 2)) * xs))
    l2 = 2 * b1 - b0
    l3 = 6 * b2 - 6 * b1 + b0
    return b0, l2, l3 / l2


def fit_gumbel(x: np.ndarray) -> tuple[float, float]:
    """Gumbel ``(u, alpha)`` by L-moments (see the module Algorithm)."""
    l1, l2, _ = lmoments(x)
    alpha = l2 / math.log(2.0)
    return l1 - EULER_GAMMA * alpha, alpha


def fit_gev(x: np.ndarray) -> tuple[float, float, float]:
    """GEV ``(xi, alpha, k)`` by L-moments, Hosking's sign (``k > 0`` bounded upper tail)."""
    l1, l2, t3 = lmoments(x)
    c = 2.0 / (3.0 + t3) - math.log(2.0) / math.log(3.0)
    k = 7.8590 * c + 2.9554 * c * c
    if abs(k) < _K_EPS:
        alpha = l2 / math.log(2.0)
        return l1 - EULER_GAMMA * alpha, alpha, 0.0
    g = math.gamma(1.0 + k)
    alpha = l2 * k / ((1.0 - 2.0 ** (-k)) * g)
    return l1 - alpha * (1.0 - g) / k, alpha, k


def quantile(dist: str, params: Sequence[float], T: Any) -> np.ndarray:
    """Return level (mm) for return period ``T`` (years; scalar or array).

    Args:
        dist: ``"gumbel"`` (params ``(u, alpha)``) or ``"gev"`` (params ``(xi, alpha, k)``).
        params: Fitted parameters.
        T: Return period(s), years.

    Returns:
        Array of return levels with the shape of ``T``.
    """
    t = np.asarray(T, dtype=float)
    y = -np.log(1.0 - 1.0 / t)  # -ln F
    if dist == "gumbel":
        u, alpha = params
        return np.asarray(u - alpha * np.log(y))
    if dist == "gev":
        xi, alpha, k = params
        if abs(k) < _K_EPS:
            return np.asarray(xi - alpha * np.log(y))
        return np.asarray(xi - alpha * np.expm1(k * np.log(y)) / k)
    raise ValueError(f"unknown distribution {dist!r}")


def _fit(dist: str, x: np.ndarray) -> tuple[float, ...]:
    return fit_gumbel(x) if dist == "gumbel" else fit_gev(x)


def bootstrap_quantiles(
    x: np.ndarray,
    T: Any,
    dist: str,
    n: int,
    seed: int,
    q: Sequence[float],
) -> np.ndarray:
    """Bootstrap quantiles of the return levels.

    Args:
        x: Annual maxima (mm).
        T: Return periods (years).
        dist: ``"gumbel"`` or ``"gev"``.
        n: Number of resamples (with replacement).
        seed: Seed of ``numpy.random.default_rng``.
        q: Quantile levels of the return-level distribution, e.g. ``(0.05, 0.95)``.

    Returns:
        Array ``(len(q), len(T))`` (mm).
    """
    xs = np.asarray(x, dtype=float)
    t = np.atleast_1d(np.asarray(T, dtype=float))
    rng = np.random.default_rng(seed)
    levels = np.empty((int(n), t.size))
    for j in range(int(n)):
        sample = rng.choice(xs, size=xs.size, replace=True)
        levels[j] = quantile(dist, _fit(dist, sample), t)
    logger.debug("bootstrap %s: %d resamples, levels %s", dist, n, levels.shape)
    return np.quantile(levels, list(q), axis=0)


__all__ = [
    "annual_maxima",
    "bootstrap_quantiles",
    "fit_gev",
    "fit_gumbel",
    "lmoments",
    "quantile",
]
