"""CLIMADA river-flood Hazard for the synthetic city.

Turns the pure-NumPy depth matrix from :func:`climaterisk.twin.flood.flood_depths` into a
CLIMADA ``Hazard`` (``RF`` tag, metres) with one centroid per asset and a layered
frequency so CLIMADA's ImpactCalc can price the depths directly.
"""

from __future__ import annotations

from typing import Any

from climaterisk.twin.flood import flood_depths, layered_frequencies


def build_hazard(assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """A CLIMADA river-flood ``Hazard`` (``RF``, metres) with one centroid per asset.

    The per-asset ground elevation comes from the asset dicts; the stage-to-depth law and
    the layered frequency come from :mod:`climaterisk.twin.flood` (pure NumPy, no CLIMADA).
    CLIMADA only assembles the event axis and the sparse intensity matrix from them.

    Algorithm:
        One event per return period with layered frequency
        $f_i = 1/T_i - 1/T_{i+1}$ (the rarest keeps $1/T_n$), so CLIMADA's
        $\\mathrm{EAL} = \\sum_i f_i\\,v\\,\\mathrm{MDR}(d_i)$ is the trapezoid-free step
        integral of the loss-exceedance curve.

    Args:
        assets: Asset dicts from :func:`climaterisk.twin.build_city` (need ``latitude``,
            ``longitude``, ``ground_m``).
        spec: City specification (uses the ``flood`` section).

    Returns:
        A :class:`climada.hazard.Hazard` with ``haz_type="RF"``, ``units="m"``.
    """
    import numpy as np
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    rps = sorted(spec["flood"]["return_periods"])
    depth = flood_depths([a["ground_m"] for a in assets], spec)
    return Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(
            lat=np.array([a["latitude"] for a in assets]),
            lon=np.array([a["longitude"] for a in assets]),
        ),
        event_id=np.arange(1, len(rps) + 1),
        event_name=[f"rp{int(r)}" for r in rps],
        date=np.zeros(len(rps), dtype=int),
        frequency=layered_frequencies(rps),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(depth),
        fraction=sparse.csr_matrix((depth > 0).astype(float)),
    )
