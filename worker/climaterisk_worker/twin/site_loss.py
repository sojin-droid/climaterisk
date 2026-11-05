"""Per-building loss of one design storm on the real site (step R3).

Footprints (OSM) get an exposed value (assumed cost per m2), the 2D peak street depth
``h_max`` is turned into the depth that reaches each building, and the repo's engine prices
it against one river-flood event with the published JRC curves. Every number comes from
``spec["loss"]``; the result is the loss of ONE storm, never an expected annual loss.

Algorithm:
    Exposed value of building $b$:
    $$V_b = A_b\\,\\min(n_b, n_{max})\\,c_{k(b)},\\qquad n_b = \\max\\big(1,
    \\mathrm{round}(H_b / \\Delta_f)\\big)$$
    Entry depth of an above-ground wall (ring $R_b$ = 8-neighbours of its cells, minus its
    cells, minus all wall cells; $T$ = no-damage threshold, a step, the curve is not shifted):
    $$m_b = \\max_{R_b} h_{max},\\qquad d_b = m_b\\,\\mathbb{1}[m_b > T]$$
    Underground feature (water above the threshold over its footprint drains in, capped at one
    storey):
    $$d_b = \\min\\Big(\\tfrac{1}{A_b}\\sum_{c\\in b}\\max(h_{max,c} - T,\\,0)\\,\\Delta^2,\\;
    \\Delta_f\\Big)$$
    Loss $L_b = V_b\\,\\mathrm{MDR}_{k(b)}(d_b)$ (engine, JRC depth-damage curve).
    ASCII: V = area*min(storeys, max_exposed)*cost[type]; storeys = max(1, round(H/ff));
    wall depth = m if m > T else 0 with m = max(h_max over ring); underground depth =
    min(sum(max(h_max - T, 0))*cell^2 / area, ff); loss = V * MDR(depth).

    $A_b$ footprint area (m2), $n_b$ storeys, $n_{max}$ exposed storeys, $c_k$ replacement
    cost (USD/m2) of property type $k$, $H_b$ building height (m), $\\Delta_f$ floor-to-floor
    height (m), $h_{max}$ peak street depth (m), $T$ no-damage threshold (m), $\\Delta$ cell
    size (m), $\\mathrm{MDR}$ mean damage ratio (-).
"""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd
import numpy as np
import shapely
from scipy.ndimage import binary_dilation

from climaterisk_worker.twin.site import SiteGrid
from climaterisk_worker.twin.spec import MODEL_ID

logger = logging.getLogger(__name__)

_RING = np.ones((3, 3), dtype=bool)


def property_type(building: str, spec: dict[str, Any]) -> str:
    """Engine property type of an OSM ``building`` value (``_default`` when unlisted)."""
    table: dict[str, str] = spec["loss"]["property_type"]
    return str(table.get(building, table["_default"]))


def _types(buildings: gpd.GeoDataFrame, spec: dict[str, Any]) -> list[str]:
    """Engine property type per row; underground features use the underground type."""
    under = str(spec["loss"]["underground_property_type"])
    return [
        under if bool(u) else property_type(str(k), spec)
        for k, u in zip(buildings["building"], buildings["underground"], strict=True)
    ]


def footprint_area_m2(buildings: gpd.GeoDataFrame) -> np.ndarray:
    """Planar footprint area (m2) in the GeoDataFrame's estimated UTM zone."""
    metric = buildings.to_crs(buildings.estimate_utm_crs())
    return np.asarray(metric.geometry.area, dtype=float)


def storeys(buildings: gpd.GeoDataFrame, spec: dict[str, Any]) -> np.ndarray:
    """Storeys $\\max(1, \\mathrm{round}(H / \\Delta_f))$ per building (int array)."""
    ff = float(spec["site"]["floor_to_floor_m"])
    return np.array([max(1, round(float(h) / ff)) for h in buildings["height_m"]], dtype=int)


def exposed_value_usd(buildings: gpd.GeoDataFrame, spec: dict[str, Any]) -> np.ndarray:
    """USD of the part of each building street water can reach (see module docstring)."""
    loss = spec["loss"]
    cost: dict[str, float] = loss["cost_usd_per_m2"]
    k = int(loss["max_exposed_storeys"])
    area = footprint_area_m2(buildings)
    n = storeys(buildings, spec)
    out = np.zeros(len(buildings), dtype=float)
    for i, (kind, under) in enumerate(
        zip(_types(buildings, spec), buildings["underground"], strict=True)
    ):
        n_exposed = 1 if bool(under) else min(int(n[i]), k)
        out[i] = area[i] * n_exposed * float(cost[kind])
    return out


def _cells(grid: SiteGrid, geom: Any) -> tuple[np.ndarray, np.ndarray]:
    """Rows and columns of the cells whose centre is inside ``geom`` (as ``building_mask``).

    When no centre is inside, the single cell containing ``representative_point()``
    (clipped to the grid when the point lies beyond its edge).
    """
    lat, lon = grid.centres()
    lat_axis, lon_axis = lat[:, 0], lon[0, :]
    w, s, e, n = geom.bounds
    i0, i1 = np.searchsorted(lat_axis, [s, n])
    j0, j1 = np.searchsorted(lon_axis, [w, e])
    rows = np.empty(0, dtype=int)
    cols = np.empty(0, dtype=int)
    if i1 > i0 and j1 > j0:
        sub_lon, sub_lat = np.meshgrid(lon_axis[j0:j1], lat_axis[i0:i1])
        inside = shapely.contains_xy(geom, sub_lon, sub_lat)
        rr, cc = np.nonzero(inside)
        rows, cols = rr + i0, cc + j0
    if rows.size == 0:
        p = geom.representative_point()
        rows = np.array([int(np.floor((p.y - grid.south) * 111320.0 / grid.cell_m))])
        cols = np.array([int(np.floor((p.x - grid.west) * grid.lon_m_per_deg / grid.cell_m))])
        # a building that only touches the site (representative point beyond the grid edge)
        # is read at the nearest edge cell rather than dropped
        rows = np.clip(rows, 0, grid.ny - 1)
        cols = np.clip(cols, 0, grid.nx - 1)
    return rows, cols


def entry_depths(
    grid: SiteGrid,
    buildings: gpd.GeoDataFrame,
    h_max: np.ndarray,
    walls: np.ndarray,
    spec: dict[str, Any],
) -> np.ndarray:
    """Depth (m) that goes into the depth-damage curve, one per building row.

    Walls: ring maximum if above the no-damage threshold T else 0 (a step, not a shift);
    canopies/roofs: the same with the own-cell maximum; underground: volume over the
    footprint divided by area, capped at one storey.
    See the module docstring for the equations.

    Args:
        grid: Site grid.
        buildings: Buildings with ``is_wall``, ``underground`` and geometry (EPSG:4326).
        h_max: Peak street depth (m), shape ``(ny, nx)``.
        walls: Boolean ``(ny, nx)`` wall raster (cells blocked in the 2D run).
        spec: City spec with a ``loss`` and a ``site`` section.

    Returns:
        Float array of depths (m), length ``len(buildings)``.
    """
    thr = float(spec["loss"]["entry_threshold_m"])
    ff = float(spec["site"]["floor_to_floor_m"])
    cell_area = grid.cell_m**2
    area = footprint_area_m2(buildings)
    is_wall = buildings["is_wall"].to_numpy(dtype=bool)
    under = buildings["underground"].to_numpy(dtype=bool)
    out = np.zeros(len(buildings), dtype=float)
    for i, geom in enumerate(buildings.geometry):
        rows, cols = _cells(grid, geom)
        if rows.size == 0:
            continue
        if under[i]:
            vol = float(np.maximum(h_max[rows, cols] - thr, 0.0).sum()) * cell_area
            out[i] = min(vol / area[i], ff)
        elif is_wall[i]:
            r0, r1 = max(int(rows.min()) - 1, 0), min(int(rows.max()) + 2, grid.ny)
            c0, c1 = max(int(cols.min()) - 1, 0), min(int(cols.max()) + 2, grid.nx)
            own = np.zeros((r1 - r0, c1 - c0), dtype=bool)
            own[rows - r0, cols - c0] = True
            ring = binary_dilation(own, structure=_RING) & ~own & ~walls[r0:r1, c0:c1]
            if ring.any():
                m = float(h_max[r0:r1, c0:c1][ring].max())
                out[i] = m if m > thr else 0.0
        else:
            m = float(h_max[rows, cols].max())
            out[i] = m if m > thr else 0.0
    return out


def price_buildings(
    buildings: gpd.GeoDataFrame,
    depth_m: np.ndarray,
    value_usd: np.ndarray,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Price every building against ONE river-flood event through the repo engine.

    Args:
        buildings: Buildings (EPSG:4326).
        depth_m: Entry depth per building (m).
        value_usd: Exposed value per building (USD).
        spec: City spec with a ``loss`` section.

    Returns:
        ``rows`` (engine ``ResultRow`` per building), ``loss_usd`` (each row's ``eal_usd``,
        the loss of this one event; ``None`` counts as 0) and ``event_loss_usd`` (portfolio
        ``ImpactCalc`` ``at_event[0]``, the cross-check).
    """
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    from climaterisk_worker.physical_risk import engine
    from climaterisk_worker.twin.pricing import portfolio_impact

    depth = np.asarray(depth_m, dtype=float)
    value = np.asarray(value_usd, dtype=float)
    points = [g.representative_point() for g in buildings.geometry]
    lat = np.array([p.y for p in points])
    lon = np.array([p.x for p in points])
    kinds = _types(buildings, spec)
    assets = [
        {
            "facility_id": f"b{i}",
            "facility_name": f"building {i}",
            "latitude": float(lat[i]),
            "longitude": float(lon[i]),
            "asset_value_usd": float(value[i]),
            "property_type": kinds[i],
        }
        for i in range(len(buildings))
    ]
    hazard = Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=lat, lon=lon),
        event_id=np.array([1]),
        event_name=["design storm"],
        date=np.zeros(1, dtype=int),
        frequency=np.array([1.0]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(depth.reshape(1, -1)),
        fraction=sparse.csr_matrix((depth.reshape(1, -1) > 0).astype(float)),
    )
    rows = [
        engine.calculate(
            a,
            hazard,
            "RF",
            model_id=MODEL_ID,
            flood_region="Asia",
            hazard_source="2D pluvial peak depth (twin/pluvial.py)",
            hazard_dataset="gangnam_design_storm",
            exposure_source="OSM footprints x assumed cost per m2 (spec loss)",
        )
        for a in assets
    ]
    loss = np.array([0.0 if r.eal_usd is None else float(r.eal_usd) for r in rows], dtype=float)
    _, impact = portfolio_impact(assets, hazard)
    event_loss = float(impact.at_event[0])
    logger.debug(
        "priced %d buildings: sum %.6g vs ImpactCalc %.6g", len(rows), loss.sum(), event_loss
    )
    return {"rows": rows, "loss_usd": loss, "event_loss_usd": event_loss}


__all__ = [
    "entry_depths",
    "exposed_value_usd",
    "footprint_area_m2",
    "price_buildings",
    "property_type",
    "storeys",
]
