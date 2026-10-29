"""Flood frequency and depth — pure maths, no CLIMADA.

Layered frequency (the trapezoid-free step integral of the loss-exceedance curve) and the
stage-to-depth law from the spec JSON. Neither function touches CLIMADA: they return
arrays that the CLIMADA Hazard in :mod:`climaterisk_worker.twin.hazard` turns into an
intensity matrix.
"""

from __future__ import annotations

from typing import Any


def layered_frequencies(return_periods: list[float]) -> Any:
    """f_i = 1/T_i - 1/T_{i+1}; the rarest event keeps 1/T_n (same convention as Aqueduct).

    Algorithm:
        $$f_i = \\frac{1}{T_i} - \\frac{1}{T_{i+1}},\\quad f_n = \\frac{1}{T_n}$$
        ASCII: f_i = 1/T_i - 1/T_{i+1}; f_n = 1/T_n

    $T_i$ return period (years), sorted ascending; $f_i$ annual frequency (1/year).

    Returns:
        A 1-D ndarray of frequencies in the same order as the sorted input.
    """
    import numpy as np

    t = np.asarray(sorted(return_periods), dtype=float)
    f = 1.0 / t
    f[:-1] -= 1.0 / t[1:]
    return f


def flood_depths(ground_m: Any, spec: dict[str, Any]) -> Any:
    """Depth matrix (n_events x n_points) from the stage law h(T) = h_2 + b ln(T/2).

    Algorithm:
        $$h(T) = h_2 + b\\,\\ln(T / 2),\\qquad d(T) = \\max(0,\\; h(T) - z)$$
        ASCII: h(T) = h_2 + b*ln(T/2); d(T) = max(0, h(T) - z)

    $h_2$ stage at the 2-year event (m above river datum), $b$ stage growth
    (m per ln-year), $T$ return period (years), $z$ ground elevation (m),
    $d$ flood depth (m, floored at 0).

    Args:
        ground_m: Ground elevation of every centroid, metres.
        spec: City specification (uses the ``flood`` section).

    Returns:
        A 2-D ndarray of shape ``(n_events, n_points)`` of flood depths.
    """
    import numpy as np

    fl = spec["flood"]
    t = np.asarray(sorted(fl["return_periods"]), dtype=float)
    stage = float(fl["stage_2y_m"]) + float(fl["stage_slope_m"]) * np.log(t / 2.0)
    return np.maximum(0.0, stage[:, None] - np.asarray(ground_m, dtype=float)[None, :])
