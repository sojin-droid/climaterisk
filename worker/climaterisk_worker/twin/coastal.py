"""Coastal storm-surge flood, bathtub: typhoon winds -> CLIMADA Petals -> engine ``CF`` pricing.

The coast is synthetic (a straight east-west line ``coast.coastline_offset_m`` south of grid
row 0). CLIMADA's own distance-to-coast comes from a NASA raster download and means nothing
for an imaginary city, so the centroids override it. The surge itself is Petals'
``TCSurgeBathtub`` (Xu 2010 wind-surge line, Pielke and Pielke 1997 inland decay) on the
step-7 typhoon winds; it is never re-implemented here. Pricing goes through
:func:`climaterisk_worker.physical_risk.engine.calculate` (hazard type ``CF``).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from climaterisk_worker.twin.spec import MODEL_ID

M_PER_DEG = 111320.0  # metres per degree of latitude (flat-earth step, as city._to_latlon)


def coast_latitude(spec: dict[str, Any]) -> float:
    """Latitude of the synthetic east-west coastline.

    Algorithm:
        $$\\phi_c = \\phi_0 - \\frac{s}{111320}$$

        ASCII: coast_lat = origin_lat - offset_m / 111320

    $\\phi_0$ is the grid origin latitude (deg), $s$ the ``coastline_offset_m`` (m) south of
    grid row 0 and $\\phi_c$ the coast latitude (deg).

    Args:
        spec: City specification (uses ``grid.origin_lat`` and ``coast.coastline_offset_m``).

    Returns:
        Coast latitude (deg N).
    """
    return (
        float(spec["grid"]["origin_lat"]) - float(spec["coast"]["coastline_offset_m"]) / M_PER_DEG
    )


def coast_centroids(lat: Any, lon: Any, spec: dict[str, Any]) -> Any:
    """CLIMADA ``Centroids`` whose distance to the synthetic coast is analytic.

    Algorithm:
        $$d = -(\\phi - \\phi_c)\\,111320$$

        ASCII: d = -(lat - coast_lat) * 111320   (m, negative on land)

    $\\phi$ is the point latitude (deg), $\\phi_c$ the coast latitude (deg) and $d$ the signed
    distance to the coast (m), negative on land (CLIMADA's convention).

    Args:
        lat: Point latitudes (deg).
        lon: Point longitudes (deg).
        spec: City specification (uses the ``coast`` section).

    Returns:
        A ``Centroids`` subclass instance; ``get_dist_coast`` never touches the network.
    """
    from climada.hazard import Centroids

    class CoastCentroids(Centroids):  # type: ignore[misc]
        """Centroids with a synthetic straight coastline; ``coast_lat`` survives deepcopy."""

        coast_lat: float = 0.0

        def get_dist_coast(self, signed: bool = False, precomputed: bool = True) -> Any:
            """Distance (m) to the synthetic coast, negative on land when ``signed``."""
            d = -(np.asarray(self.lat, float) - self.coast_lat) * M_PER_DEG
            return d if signed else np.abs(d)

    cent = CoastCentroids(lat=np.asarray(lat, float), lon=np.asarray(lon, float))
    cent.coast_lat = coast_latitude(spec)
    return cent


def write_dem(graph: Any, spec: dict[str, Any], path: str | Path) -> Path:
    """Write the city terrain plus a strip of sea as a float32 EPSG:4326 GeoTIFF.

    The land rows are :func:`climaterisk_worker.twin.inundation.city_raster` without its river
    row; land row ``i`` has its pixel centres at ``y = i * cell`` m north and column ``j`` at
    ``x = j * cell`` m east of the grid origin, so sampling a node returns its exact ground.
    Land cells between the coast and row 0 repeat row 0; sea rows (``sea_bed_m``) start
    south of the coast.

    Algorithm:
        $$\\Delta\\phi = \\frac{\\Delta y}{111320},\\quad
        \\Delta\\lambda = \\frac{\\Delta x}{111320\\cos\\phi_0}$$

        ASCII: dlat = cell/111320 ; dlon = cell/(111320*cos(origin_lat))

    $\\Delta y,\\Delta x$ are the cell size (m), $\\phi_0$ the origin latitude (deg).

    Args:
        graph: City graph from :func:`climaterisk_worker.twin.build_city`.
        spec: City specification.
        path: Output ``.tif`` path.

    Returns:
        The path written.
    """
    import rasterio
    from rasterio.transform import from_origin

    from climaterisk_worker.twin.inundation import city_raster

    co, grid = spec["coast"], spec["grid"]
    bed, cell, _ = city_raster(graph, spec)
    land = np.asarray(bed[1:], float)  # row 0 is the river row
    offset = float(co["coastline_offset_m"])
    n_pad = math.floor(offset / cell)  # land rows at y = -cell ... -n_pad cell
    if not math.isclose(offset, (n_pad + 0.5) * cell, rel_tol=0, abs_tol=1e-6):
        raise ValueError("coast.coastline_offset_m must lie on a raster cell edge: (n + 0.5) cell")
    pad = np.repeat(land[:1], n_pad, axis=0)
    sea = np.full((int(co["sea_rows"]), land.shape[1]), float(co["sea_bed_m"]))
    south_up = np.vstack([sea[::-1], pad[::-1], land])  # row 0 = southernmost
    z = np.ascontiguousarray(south_up[::-1], dtype=np.float32)  # north-up
    lat0, lon0 = float(grid["origin_lat"]), float(grid["origin_lon"])
    dlat = cell / M_PER_DEG
    dlon = cell / (M_PER_DEG * math.cos(math.radians(lat0)))
    top_lat = lat0 + (land.shape[0] - 1) * dlat + dlat / 2
    west_lon = lon0 - dlon / 2
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=z.shape[0],
        width=z.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west_lon, top_lat, dlon, dlat),
    ) as ds:
        ds.write(z, 1)
    return path


def surge_hazard(
    assets: list[dict[str, Any]], graph: Any, spec: dict[str, Any], dem_path: str | Path
) -> Any:
    """Storm-surge depth hazard (``CF``, m) from the synthetic typhoons and the DEM.

    Algorithm:
        $$h_s = \\max\\big(0.1023\\,(v - 26.8224) + 1.8288 - (z + \\delta d - s),\\,0\\big)$$

        ASCII: surge = max(0.1023*(v-26.8224)+1.8288 - (z + decay*d - slr), 0)

    This is Petals' ``TCSurgeBathtub`` (not re-implemented): $v$ peak wind (m/s), $z$ ground
    elevation from the DEM (m), $\\delta$ inland decay (m/km), $d$ distance inland (km),
    $s$ sea-level rise (m).

    Args:
        assets: Asset dicts from :func:`climaterisk_worker.twin.build_city`.
        graph: City graph (unused beyond API symmetry with :func:`write_dem`).
        spec: City specification (``coast`` and ``typhoon`` sections).
        dem_path: GeoTIFF from :func:`write_dem`.

    Returns:
        A CLIMADA ``Hazard`` with ``haz_type == "CF"`` and ``units == "m"``.
    """
    from climada.hazard import TropCyclone
    from climada_petals.hazard.tc_surge_bathtub import TCSurgeBathtub

    from climaterisk_worker.twin.typhoon import synthetic_tracks

    del graph
    tracks, frequency = synthetic_tracks(spec)
    lat = np.array([a["latitude"] for a in assets], float)
    lon = np.array([a["longitude"] for a in assets], float)
    cent = coast_centroids(lat, lon, spec)
    wind = TropCyclone.from_tracks(tracks, centroids=cent, ignore_distance_to_coast=True)
    wind.frequency = np.asarray(frequency, dtype=float)
    wind.centroids = cent  # from_tracks swaps in plain centroids (NASA download on dist-coast)
    co = spec["coast"]
    surge = TCSurgeBathtub.from_tc_winds(
        wind,
        str(dem_path),
        inland_decay_rate=float(co["inland_decay_m_per_km"]),
        add_sea_level_rise=float(co["sea_level_rise_m"]),
    )
    surge.haz_type = "CF"
    surge.units = "m"
    return surge


def price_surge(assets: list[dict[str, Any]], hazard: Any) -> list[Any]:
    """One engine ``ResultRow`` per asset for the surge hazard (``CF``, region Asia)."""
    from climaterisk_worker.physical_risk import engine

    rows = []
    for a in assets:
        facility = {k: v for k, v in a.items() if k not in ("node", "edge", "ground_m", "kind")}
        rows.append(
            engine.calculate(
                facility,
                hazard,
                "CF",
                model_id=MODEL_ID,
                flood_region="Asia",
                hazard_source="synthetic storm surge (synthetic_city_spec.json)",
                hazard_dataset="synthetic_city",
                exposure_source="synthetic virtual city (NetworkX grid)",
            )
        )
    return rows
