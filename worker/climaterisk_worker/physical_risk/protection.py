"""Flood defences as a design return period — the CLIMADA / FLOPROS "protection standard" rule.

Neither flood hazard the Models tab reads represents defences: the ISIMIP river-flood sets
have no Korean levees, pump stations or sluices, and WRI Aqueduct coastal inundation has no
sea walls. Undefended, reclaimed land behind a sea wall floods every two years to 5 m.

The rule applied here is the one CLIMADA's own flood work and the ISIMIP "protected" runs
use: a location protected to a design return period ``P`` does not flood in events up to
``P``; an event that exceeds it overtops and floods with its full depth.

Algorithm:
    $$I^{P}_{c} = \\text{local exceedance intensity of centroid } c \\text{ at } P, \\qquad
      \\tilde{I}_{e,c} = I_{e,c}\\,\\mathbb{1}\\big[I_{e,c} > I^{P}_{c}\\big]$$
    ASCII: threshold[c] = intensity at centroid c for return period P (CLIMADA
    Hazard.local_exceedance_intensity, method "stepfunction"); keep an event's intensity
    at c only where it is above that threshold, else 0.

    ``I_{e,c}``: flood depth of event e at centroid c (m); ``P``: design return period
    (years); ``I^P_c``: depth at c reached once every ``P`` years (m). ``stepfunction``
    returns the largest observed depth for ``P`` beyond the cell's record (fully protected)
    and 0 for ``P`` shorter than its most frequent event (nothing removed).

The design return period is a **national default** from the configuration
(``physical_risk_config.json`` → ``flood_protection``), not the defence actually in place at
a location — a known limitation recorded on every row.
"""

from __future__ import annotations

import copy
from typing import Any


def local_intensity(hazard: Any, return_period: float) -> Any:
    """Per-centroid intensity at ``return_period`` (CLIMADA, ``stepfunction``), as an array."""
    import numpy as np

    gdf, _, _ = hazard.local_exceedance_intensity(
        return_periods=(float(return_period),), method="stepfunction"
    )
    col = next(c for c in gdf.columns if c != "geometry")
    return np.nan_to_num(np.asarray(gdf[col], dtype=float), nan=0.0)


def protect(hazard: Any, return_period: float) -> Any:
    """A copy of ``hazard`` without the events at or below the local ``return_period`` depth."""
    import numpy as np

    threshold = local_intensity(hazard, return_period)
    intensity = hazard.intensity.tocsr(copy=True)
    keep = intensity.data > threshold[intensity.indices]
    intensity.data = np.where(keep, intensity.data, 0.0)
    intensity.eliminate_zeros()
    out = copy.deepcopy(hazard)
    out.intensity = intensity
    if hazard.fraction is not None and hazard.fraction.nnz:
        out.fraction = hazard.fraction.tocsr().multiply(intensity > 0).tocsr()
    else:
        out.fraction = (intensity > 0).astype(float).tocsr()
    return out


def nearest_centroid(hazard: Any, lat: float, lon: float) -> int | None:
    """Index of the centroid nearest to a point (the engine's own readout rule)."""
    import numpy as np

    lats = np.asarray(hazard.centroids.lat, dtype=float)
    lons = np.asarray(hazard.centroids.lon, dtype=float)
    if lats.size == 0:
        return None
    return int(np.argmin((lats - lat) ** 2 + (lons - lon) ** 2))
