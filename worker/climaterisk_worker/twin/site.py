"""Real neighbourhood from public data: OSM buildings + Copernicus GLO-30 -> hydraulic raster.

OpenStreetMap footprints (ODbL, (c) OpenStreetMap contributors) become impassable cells in the
2D model; the Copernicus GLO-30 *surface* model (Copernicus DEM, ESA) is turned into bare
earth by masking the footprints and interpolating the ground underneath.

Algorithm:
    Local metric grid (flat-earth, origin at the south-west corner, $\\varphi_s$ = south):
    $$x = (j + \\tfrac12)\\,\\Delta,\\quad y = (i + \\tfrac12)\\,\\Delta,\\quad
    \\varphi = \\varphi_s + \\frac{y}{111320},\\quad
    \\lambda = \\lambda_w + \\frac{x}{111320\\cos\\varphi_s}$$
    ASCII: x=(j+.5)*cell, y=(i+.5)*cell, lat=south+y/111320, lon=west+x/(111320*cos(south))

    Building height: OSM ``height`` (m) if numeric, else ``levels`` * floor-to-floor, else
    typology default levels * floor-to-floor. Bare earth: bilinear DSM sample at cell
    centres, masked cells re-filled by linear (convex hull) then nearest interpolation.

    $\\Delta$ cell size (m), $i,j$ row (north) / column (east) index, $\\varphi,\\lambda$ latitude
    and longitude (deg), $x,y$ metres from the corner.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from scipy.interpolate import griddata
from scipy.ndimage import map_coordinates

logger = logging.getLogger(__name__)

M_PER_DEG = 111320.0
_TAG_RE = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')
_HEIGHT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*m?\s*$")


@dataclass(frozen=True)
class SiteGrid:
    """Raster in local metres; cell ``(i, j)`` is row ``i`` northward, column ``j`` eastward."""

    west: float
    south: float
    cell_m: float
    nx: int
    ny: int

    @property
    def lon_m_per_deg(self) -> float:
        """Metres per degree of longitude (flat-earth, at the south edge)."""
        return M_PER_DEG * math.cos(math.radians(self.south))

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        """Cell-centre ``(lat, lon)`` in degrees, each of shape ``(ny, nx)``."""
        y = (np.arange(self.ny) + 0.5) * self.cell_m
        x = (np.arange(self.nx) + 0.5) * self.cell_m
        lat = self.south + y / M_PER_DEG
        lon = self.west + x / self.lon_m_per_deg
        return (
            np.repeat(lat[:, None], self.nx, axis=1),
            np.repeat(lon[None, :], self.ny, axis=0),
        )


def site_grid(bbox: tuple[float, float, float, float], cell_m: float) -> SiteGrid:
    """Grid covering ``bbox = (west, south, east, north)`` with ``floor(extent / cell_m)`` cells."""
    west, south, east, north = bbox
    width = (east - west) * M_PER_DEG * math.cos(math.radians(south))
    height = (north - south) * M_PER_DEG
    return SiteGrid(west, south, cell_m, int(width // cell_m), int(height // cell_m))


def _parse_tags(other_tags: str | None) -> dict[str, str]:
    """Parse GDAL's ``"k"=>"v","k2"=>"v2"`` hstore text."""
    if not other_tags:
        return {}
    return dict(_TAG_RE.findall(other_tags))


def _height_from_tag(value: str | None) -> float | None:
    """Metres from a ``height`` tag ("45", "45 m", "45.5"); ``None`` for anything else."""
    if value is None:
        return None
    match = _HEIGHT_RE.match(value)
    if match is None:
        return None
    height = float(match.group(1))
    return height if height > 0 else None


def _levels_from_tag(value: str | None) -> float | None:
    """A positive number of storeys from ``building:levels``; ``None`` otherwise."""
    if value is None:
        return None
    try:
        levels = float(value.strip())
    except ValueError:
        return None
    return levels if math.isfinite(levels) and levels > 0 else None


def read_buildings(
    osm_path: Path | str, bbox: tuple[float, float, float, float], spec: dict[str, Any]
) -> gpd.GeoDataFrame:
    """Building footprints intersecting ``bbox`` with a height and where it came from.

    Heights follow ``height`` (m) -> ``building:levels`` x floor-to-floor -> typology default
    levels x floor-to-floor (``spec["site"]``); ``height_source`` is ``height``, ``levels``
    or ``typology``.

    Args:
        osm_path: ``.osm`` XML or ``.osm.pbf`` file (read with the GDAL OSM driver).
        bbox: ``(west, south, east, north)`` in degrees.
        spec: City spec containing a ``site`` section.

    Returns:
        GeoDataFrame in EPSG:4326 with ``building``, ``height_m``, ``height_source``, geometry.
    """
    import pyogrio

    site_cfg = spec["site"]
    floor_m = float(site_cfg["floor_to_floor_m"])
    defaults: dict[str, float] = site_cfg["default_levels"]
    raw = pyogrio.read_dataframe(
        str(osm_path), layer="multipolygons", bbox=bbox, columns=["building", "other_tags"]
    )
    raw = raw[raw["building"].notna() & ~raw.geometry.isna()].reset_index(drop=True)
    kinds: list[str] = []
    heights: list[float] = []
    sources: list[str] = []
    for kind, other in zip(raw["building"], raw["other_tags"], strict=True):
        tags = _parse_tags(other)
        height = _height_from_tag(tags.get("height"))
        levels = _levels_from_tag(tags.get("building:levels"))
        if height is not None:
            source = "height"
        elif levels is not None:
            height, source = levels * floor_m, "levels"
        else:
            height = float(defaults.get(kind, defaults["yes"])) * floor_m
            source = "typology"
        kinds.append(str(kind))
        heights.append(float(height))
        sources.append(source)
    out = gpd.GeoDataFrame(
        {"building": kinds, "height_m": heights, "height_source": sources},
        geometry=raw.geometry.values,
        crs="EPSG:4326",
    )
    logger.info("read %d buildings from %s", len(out), Path(osm_path).name)
    return out


def building_mask(grid: SiteGrid, buildings: gpd.GeoDataFrame) -> np.ndarray:
    """Boolean ``(ny, nx)`` raster: cell centre strictly inside a building footprint."""
    lat, lon = grid.centres()
    mask = np.zeros((grid.ny, grid.nx), dtype=bool)
    lat_axis, lon_axis = lat[:, 0], lon[0, :]
    for geom in buildings.geometry:
        if geom is None or geom.is_empty:
            continue
        w, s, e, n = geom.bounds
        i0, i1 = np.searchsorted(lat_axis, [s, n])
        j0, j1 = np.searchsorted(lon_axis, [w, e])
        if i1 <= i0 or j1 <= j0:
            continue
        sub_lon, sub_lat = np.meshgrid(lon_axis[j0:j1], lat_axis[i0:i1])
        mask[i0:i1, j0:j1] |= shapely.contains_xy(geom, sub_lon, sub_lat)
    return mask


def bare_earth(dsm_path: Path | str, grid: SiteGrid, mask: np.ndarray) -> np.ndarray:
    """Ground elevation (m) at the cell centres from a surface model with buildings removed.

    Algorithm:
        $z(\\varphi,\\lambda)$ = bilinear sample of the DSM; masked cells are replaced by
        linear interpolation over a Delaunay triangulation of the unmasked cells, nearest
        unmasked cell outside their convex hull.
        ASCII: z = bilinear(DSM); z[mask] = griddata(unmasked, linear, then nearest)

    Args:
        dsm_path: GeoTIFF in EPSG:4326 (elevation in m).
        grid: Site grid.
        mask: Boolean ``(ny, nx)`` building raster.

    Returns:
        Float array ``(ny, nx)``, m.
    """
    lat, lon = grid.centres()
    with rasterio.open(dsm_path) as ds:
        pad = 2
        rows, cols = rasterio.transform.rowcol(
            ds.transform, [lon.min(), lon.max()], [lat.max(), lat.min()]
        )
        r0, r1 = max(min(rows) - pad, 0), min(max(rows) + pad + 1, ds.height)
        c0, c1 = max(min(cols) - pad, 0), min(max(cols) + pad + 1, ds.width)
        window = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
        data = ds.read(1, window=window, masked=True).astype("float64").filled(np.nan)
        inv = ~ds.transform
    # fractional pixel index of pixel centres: pixel (r, c) has its centre at (r + .5, c + .5)
    col_f, row_f = inv * (lon, lat)
    coords = np.array([row_f - 0.5 - r0, col_f - 0.5 - c0])
    z = map_coordinates(data, coords, order=1, mode="nearest").reshape(lat.shape)
    ground = np.array(z, dtype="float64")
    if mask.any():
        keep = ~mask & np.isfinite(ground)
        if keep.any():
            pts = np.column_stack([lat[keep], lon[keep]])
            fill = ~keep
            targets = np.column_stack([lat[fill], lon[fill]])
            vals = griddata(pts, ground[keep], targets, method="linear")
            nan = ~np.isfinite(vals)
            if nan.any():
                vals[nan] = griddata(pts, ground[keep], targets[nan], method="nearest")
            ground[fill] = vals
    return ground


def build_site(spec: dict[str, Any]) -> dict[str, Any]:
    """Build the real site named in ``spec["site"]`` from the files on disk.

    Returns:
        Dict with ``grid`` (SiteGrid), ``buildings`` (GeoDataFrame), ``mask`` (bool raster)
        and ``ground`` (bare-earth elevation raster, m).
    """
    from climaterisk import paths

    cfg = spec["site"]
    bbox = tuple(float(v) for v in cfg["bbox"])
    grid = site_grid(bbox, float(cfg["cell_m"]))  # type: ignore[arg-type]
    buildings = read_buildings(paths.osm_dir(cfg["osm_file"]), bbox, spec)  # type: ignore[arg-type]
    mask = building_mask(grid, buildings)
    dem = paths.external_dir("copernicus_dem", cfg["dem_file"])
    ground = bare_earth(dem, grid, mask)
    return {"grid": grid, "buildings": buildings, "mask": mask, "ground": ground}


__all__ = [
    "SiteGrid",
    "bare_earth",
    "build_site",
    "building_mask",
    "read_buildings",
    "site_grid",
]
