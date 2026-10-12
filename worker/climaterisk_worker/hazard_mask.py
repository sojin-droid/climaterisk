"""Mask a CLIMADA ``Hazard`` to one country's land (plus an optional buffer).

The CLIMADA Data API serves some products only as a global set. Caching that whole set
under a country key is wasteful and, worse, keeps the neighbours' cells: a bounding-box
crop around a Korean portfolio still contains Japanese and Chinese centroids. EIOPA's
CLIMADA-App solves this with ``split_hdf5_files.py`` (keep the centroids inside each
country's geometry). This module is the same operation as a reusable worker step.

Geometry comes from CLIMADA itself — ``climada.util.coordinates.get_country_geometries``
(Natural Earth 10m, cached by CLIMADA) — so no second boundary file is introduced, and the
point-in-polygon test is shapely's vectorised ``contains_xy``. The buffer (degrees) keeps
near-shore sea cells: with a strict polygon a coastal asset's nearest centroid can be
pushed inland, which changes its price. ``0`` reproduces EIOPA's strict behaviour.

Events are never dropped and frequencies are never touched — only centroid columns are
selected — so the hazard's event-frequency model is exactly the source's.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from climaterisk_worker._params import MASK_BUFFER_DEG

METHOD = (
    "CLIMADA get_country_geometries (Natural Earth 10m) + shapely contains_xy; "
    "centroid columns selected, events and frequencies unchanged"
)


def country_geometry(iso3: str, buffer_deg: float = 0.0) -> Any:
    """The (multi)polygon of ``iso3``'s land, optionally buffered by ``buffer_deg`` degrees."""
    from climada.util import coordinates as u_coord

    if buffer_deg < 0:
        raise ValueError(f"buffer_deg must be >= 0, got {buffer_deg}")
    gdf = u_coord.get_country_geometries(country_names=[iso3.upper()], resolution=10)
    if gdf is None or len(gdf) == 0:
        raise ValueError(f"no country geometry for ISO3 {iso3!r}")
    geoms = gdf.geometry
    geom = geoms.union_all() if hasattr(geoms, "union_all") else geoms.unary_union
    return geom.buffer(buffer_deg) if buffer_deg > 0 else geom


def centroid_mask(hazard: Any, geometry: Any) -> np.ndarray:
    """Boolean mask over the hazard's centroids: inside ``geometry`` or not."""
    import shapely

    lat = np.asarray(hazard.centroids.lat, dtype=float)
    lon = np.asarray(hazard.centroids.lon, dtype=float)
    return np.asarray(shapely.contains_xy(geometry, lon, lat), dtype=bool)


def mask_hazard_to_country(
    hazard: Any, iso3: str, buffer_deg: float = MASK_BUFFER_DEG
) -> tuple[Any, dict[str, Any]]:
    """``(masked hazard, record)``: the hazard restricted to ``iso3`` (+ buffer).

    Algorithm:
        $$ C' = \\{\\, c \\in C : c \\in \\mathrm{buffer}(G_{iso3}, b) \\,\\} $$
        ASCII: keep centroid c iff c lies inside the country polygon G buffered by b degrees

    where ``C`` are the hazard's centroids and ``b = buffer_deg`` (decimal degrees, ~111 km
    per degree of latitude). Intensity and fraction keep every event (rows) and only the
    kept centroids (columns); ``frequency``, ``event_id`` and dates are untouched.

    Raises:
        ValueError: no centroid lies inside the (buffered) country — a mask that would
            leave an empty hazard is a wrong region, not a result.
    """
    geom = country_geometry(iso3, buffer_deg)
    keep = centroid_mask(hazard, geom)
    n_before = int(keep.size)
    n_after = int(keep.sum())
    if n_after == 0:
        raise ValueError(
            f"no centroid of the hazard lies inside {iso3.upper()} (+{buffer_deg:g} deg); "
            f"{n_before} centroids checked"
        )
    record = {
        "iso3": iso3.upper(),
        "buffer_deg": float(buffer_deg),
        "n_centroids_before": n_before,
        "n_centroids_after": n_after,
        "method": METHOD,
    }
    if n_after == n_before:  # nothing to cut — hand back the same object
        return hazard, record
    idx = np.flatnonzero(keep)
    out = copy.deepcopy(hazard)
    out.centroids = hazard.centroids.select(sel_cen=idx)
    out.intensity = hazard.intensity[:, idx]
    out.fraction = hazard.fraction[:, idx]
    out.check()
    return out, record
