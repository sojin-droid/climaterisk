"""Pervious ground from real OpenStreetMap land use (step R6).

A cell is *green* (grass, park, wood...) when its centre lies in an OSM polygon whose tags
are listed in ``spec["landcover"]["green_tags"]``; green cells take the curve number
``landcover.cn_green`` and every other cell keeps ``rain.cn_paved``. Nothing is inferred:
a cell outside every listed polygon is treated as paved, which is itself an assumption
(untagged soil is not pervious here) and is stated in the --real output.

Algorithm:
    $$g_{ij} = \\mathbb{1}\\big[(\\lambda_j, \\varphi_i) \\in \\textstyle\\bigcup_k P_k\\big],
    \\qquad CN_{ij} = g_{ij}\\,CN_{green} + (1 - g_{ij})\\,CN_{paved}$$
    ASCII: green = cell centre inside any green polygon; CN = CN_green if green else CN_paved.

    $\\lambda_j,\\varphi_i$ cell-centre longitude and latitude (deg), $P_k$ green polygons,
    $CN$ NRCS curve number (-).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

from climaterisk_worker.twin.site import SiteGrid, building_mask

logger = logging.getLogger(__name__)


def read_green(
    osm_path: Path | str, bbox: tuple[float, float, float, float], spec: dict[str, Any]
) -> gpd.GeoDataFrame:
    """Green polygons of the OSM ``multipolygons`` layer inside ``bbox``.

    A polygon is kept when one of its tags equals a value listed for that key in
    ``spec["landcover"]["green_tags"]`` (keys are layer columns: ``leisure``, ``landuse``,
    ``natural``).

    Args:
        osm_path: ``.osm.pbf`` file (GDAL OSM driver), as for the buildings.
        bbox: ``(west, south, east, north)`` in degrees.
        spec: City spec with a ``landcover`` section.

    Returns:
        GeoDataFrame in EPSG:4326 with geometry only.
    """
    import pyogrio

    tags: dict[str, list[str]] = spec["landcover"]["green_tags"]
    raw = pyogrio.read_dataframe(
        str(osm_path), layer="multipolygons", bbox=bbox, columns=list(tags)
    )
    keep = np.zeros(len(raw), dtype=bool)
    for key, values in tags.items():
        keep |= raw[key].isin(list(values)).to_numpy(dtype=bool)
    keep &= ~raw.geometry.isna().to_numpy()
    out = gpd.GeoDataFrame(geometry=raw.geometry[keep].reset_index(drop=True), crs="EPSG:4326")
    logger.info("read %d green polygons from %s", len(out), Path(osm_path).name)
    return out


def green_mask(grid: SiteGrid, polygons: gpd.GeoDataFrame) -> np.ndarray:
    """Boolean ``(ny, nx)`` raster: cell centre inside any of ``polygons``.

    Args:
        grid: Site grid.
        polygons: Polygons in any CRS (EPSG:4326 when it has none); may be empty.

    Returns:
        Boolean array, True on green cells; all False for no polygons.
    """
    if len(polygons) == 0:
        return np.zeros((grid.ny, grid.nx), dtype=bool)
    poly = polygons if polygons.crs is not None else polygons.set_crs("EPSG:4326")
    return building_mask(grid, poly.to_crs("EPSG:4326"))


def cn_raster(green: np.ndarray, spec: dict[str, Any]) -> np.ndarray:
    """Curve-number raster: ``landcover.cn_green`` on green cells, ``rain.cn_paved`` elsewhere.

    Args:
        green: Boolean green raster.
        spec: City spec with ``landcover`` and ``rain`` sections.

    Returns:
        Float array of the shape of ``green`` (-).
    """
    return np.where(
        np.asarray(green, dtype=bool),
        float(spec["landcover"]["cn_green"]),
        float(spec["rain"]["cn_paved"]),
    )


__all__ = ["cn_raster", "green_mask", "read_green"]
