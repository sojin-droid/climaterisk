"""Effect of the no-damage threshold on the loss of the real Gangnam buildings (step R5).

Only buildings whose use is *known* from OpenStreetMap are priced (no assumed use), only
their ground storey, and every storm of a return-period ladder is priced through the repo
engine once per threshold. The threshold is the engine's own facility input
``flood_zero_damage_threshold_m``; the curve is either CLIMADA's JRC Asia curve (which
already has zero damage up to 0.05 m) or the published JRC table without that inserted
zero (``pure_jrc``, the baseline the user's sheet starts from).

Algorithm:
    $$\\mathrm{EAL}_i(T) = \\sum_{k=1}^{K} f_k\\,V_i\\,\\mathrm{MDR}_c(d_{ki})\\,
    \\mathbb{1}[d_{ki} > T]$$
    ASCII: EAL_i(T) = sum_k f_k * V_i * MDR_c(d_ki) * [d_ki > T]

    $f_k$ annual frequency of the $k$-th layer (1/year, ``rain_eal.layered_frequencies``),
    $V_i$ exposed ground-storey value of building $i$ (USD), $\\mathrm{MDR}_c$ mean damage
    ratio of curve $c$ (dimensionless, 0-1), $d_{ki}$ ground entry depth (m) of building
    $i$ in storm $k$, $T$ zero-damage threshold (m; no threshold = no indicator).
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import geopandas as gpd
import numpy as np

from climaterisk_worker.twin import rain_eal

logger = logging.getLogger(__name__)

Threshold = float | None


def known_use(buildings: gpd.GeoDataFrame, spec: dict[str, Any]) -> list[str | None]:
    """JRC property type of each building when its use is listed in the spec, else ``None``.

    A building counts only if it is a wall (``is_wall``), not underground, and its OSM
    ``building`` value is listed in ``spec["threshold_compare"]["known_uses"]``. Nothing
    falls back to a default (``"yes"``, schools, unlisted values give ``None``).

    Args:
        buildings: Buildings with ``building``, ``is_wall`` and ``underground`` columns.
        spec: City spec with a ``threshold_compare`` section.

    Returns:
        One entry per row: ``"residential"``, ``"commercial"``, ``"industrial"`` or ``None``.
    """
    lookup: dict[str, str] = {}
    for use, values in spec["threshold_compare"]["known_uses"].items():
        for v in values:
            lookup[str(v)] = use
    out: list[str | None] = []
    for tag, wall, under in zip(
        buildings["building"],
        buildings["is_wall"].to_numpy(dtype=bool),
        buildings["underground"].to_numpy(dtype=bool),
        strict=True,
    ):
        out.append(lookup.get(str(tag)) if wall and not under else None)
    return out


def pure_jrc(func: Any) -> Any:
    """Copy of a CLIMADA ``ImpfRiverFlood`` without the inserted zero-damage points.

    CLIMADA puts extra points with zero damage between 0 m and the first damaging depth
    (JRC Asia: 0.05 m), which is a built-in no-damage threshold. Removing every point with
    $d > 0$ and zero damage before the first damaging point gives the published JRC table:
    linear from (0, 0) to the first damaging depth.

    Args:
        func: A published ``ImpfRiverFlood``.

    Returns:
        A deep copy with those points removed (``func`` is not changed).

    Algorithm:
        $$\\mathrm{MDR}(d) = \\mathrm{MDR}(d_1)\\,d/d_1,\\quad 0 \\le d \\le d_1$$
        ASCII: mdr(d) = mdr(d1) * d / d1 for d in [0, d1]; d1 = first depth with mdr > 0.

        $d$ depth (m), $d_1$ first depth with positive damage (m).
    """
    x = np.asarray(func.intensity, dtype=float)
    mdr = np.asarray(func.mdd, dtype=float) * np.asarray(func.paa, dtype=float)
    positive = np.flatnonzero(mdr > 0)
    out = copy.deepcopy(func)
    if positive.size == 0:
        return out
    first = int(positive[0])
    keep = np.ones(x.size, dtype=bool)
    keep[1:first] = False  # drop the zero-damage points strictly between 0 m and d_1
    keep[0] = True
    out.intensity = x[keep]
    out.mdd = np.asarray(func.mdd, dtype=float)[keep]
    out.paa = np.asarray(func.paa, dtype=float)[keep]
    out.name = f"{func.name} | published JRC table (no inserted zero)"
    return out


@contextmanager
def curve_swap(curve: str, threshold_m: Threshold) -> Iterator[None]:
    """Temporarily replace ``registry.flood_impact_function`` (restored in ``finally``).

    The wrapper returns the CLIMADA curve (``curve="climada"``) or ``pure_jrc`` of it
    (``"pure"``). When a threshold is given the wrapper also applies the engine's own
    ``registry.thresholded_flood_impact_function``, so the per-event losses that
    ``rain_eal`` reads through ``registry`` see the same curve as the engine rows (the
    engine applies the threshold again, which is idempotent). Only the river-flood (RF)
    function is swapped; ``coastal_flood_impact_function`` is not used by an RF ladder.

    Args:
        curve: ``"climada"`` or ``"pure"``.
        threshold_m: Zero-damage threshold (m) or ``None``.

    Yields:
        Nothing.
    """
    from climaterisk_worker.physical_risk import registry

    if curve not in ("climada", "pure"):
        raise ValueError(f"curve must be 'climada' or 'pure', got {curve!r}")
    original = registry.flood_impact_function

    def wrapped(region: str, sector: str) -> Any:
        func = original(region, sector)
        if curve == "pure":
            func = pure_jrc(func)
        if threshold_m is not None:
            func = registry.thresholded_flood_impact_function(func, float(threshold_m))
        return func

    registry.flood_impact_function = wrapped
    try:
        yield
    finally:
        registry.flood_impact_function = original


def price_thresholds(
    buildings: gpd.GeoDataFrame,
    depth_m: np.ndarray,
    value_usd: np.ndarray,
    rps: Any,
    thresholds: list[Threshold],
    spec: dict[str, Any],
    curve: str,
) -> dict[Threshold, dict[str, np.ndarray]]:
    """Price a storm ladder once per threshold through the engine.

    Uses ``rain_eal.price_ladder`` (one RF hazard, ``K`` events, layered frequencies) with
    the threshold passed as the facility key ``flood_zero_damage_threshold_m``.

    Args:
        buildings: Buildings (EPSG:4326), length ``n``.
        depth_m: Ground entry depth (m), shape ``(K, n)``.
        value_usd: Exposed value (USD), length ``n``.
        rps: Ascending return periods (years), length ``K``.
        thresholds: ``None`` (no threshold) and/or thresholds in m.
        spec: City spec with a ``loss`` section.
        curve: ``"climada"`` (the engine's curve) or ``"pure"`` (``pure_jrc`` of it).

    Returns:
        Dict keyed by threshold with ``eal_usd`` ``(n,)`` and ``event_loss_usd`` ``(K, n)``.
    """
    out: dict[Threshold, dict[str, np.ndarray]] = {}
    for t in thresholds:
        extra = None if t is None else {"flood_zero_damage_threshold_m": float(t)}
        with curve_swap(curve, t):
            lad = rain_eal.price_ladder(buildings, depth_m, value_usd, rps, spec, extra)
        out[t] = {"eal_usd": lad["eal_usd"], "event_loss_usd": lad["event_loss_usd"]}
        logger.debug(
            "threshold %s curve %s: EAL %.6g USD over %d buildings",
            t,
            curve,
            float(lad["eal_usd"].sum()),
            len(buildings),
        )
    return out


__all__ = ["curve_swap", "known_use", "price_thresholds", "pure_jrc"]
