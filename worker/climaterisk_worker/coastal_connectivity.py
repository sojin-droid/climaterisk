"""Coastal flood by hydraulic connectivity at asset points (method tier T2, hazard-only).

The "connected bathtub": ground is flooded by the sea when it lies below the local tidal
datum **and** is joined to the sea through a chain of cells that are themselves below it.
A basin behind a wall higher than the datum is not flooded; a basin with a gap in the wall is.

Method (validated in the agrivoltaic analysis, ported here):

1. Station datum: AHHW (approximate highest high water) above mean sea level per KHOA
   reference station, from the four principal harmonic constituents.
2. Terrain: Copernicus GLO-30 (1 arcsec) reduced to 3 arcsec (~90 m) by the **block minimum**,
   which removes any barrier narrower than ~90 m (seawall, levee crest). The block maximum is
   kept as a diagnostic (barriers retained).
3. Sea: outside the SGIS 시도 land polygons, or no elevation (NaN).
4. Low cells ``z < threshold`` joined to the sea by 8-connectivity are tidal.

Everything is in memory. Only the per-tile block-min/max ``.npy`` cache files are written.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: Aggregation factor 1 arcsec -> 3 arcsec (~90 m) and cells per degree after it.
FACTOR = 3
CELLS_PER_DEG = 3600 // FACTOR
#: Nearest-station threshold is evaluated on blocks of this many 90 m cells (~1.8 km).
THRESHOLD_BLOCK_CELLS = 20
#: Amplitude columns of the KHOA harmonic-constant file (cm).
AMPLITUDE_COLUMNS = ("M2 진폭", "S2 진폭", "K1 진폭", "O1 진폭")

_CACHE_NAME = "glo30_blockmin"


class TerrainUnavailable(RuntimeError):
    """A terrain tile is neither cached nor obtainable (offline or network failure)."""


# --------------------------------------------------------------------------- #
# Pure functions                                                                #
# --------------------------------------------------------------------------- #
def station_ahhw(df: Any) -> Any:
    """AHHW above mean sea level per station, in metres.

    Algorithm:
        $$Z_{AHHW} = \\frac{A_{M2} + A_{S2} + A_{K1} + A_{O1}}{100}$$
        ASCII: ahhw_m = (M2 + S2 + K1 + O1 amplitudes in cm) / 100

        where $A_c$ is the amplitude (cm) of tidal constituent $c$ and $Z_{AHHW}$ the height
        (m) above MSL reached when the four constituents are in phase.

    Args:
        df: KHOA harmonic-constant table (columns ``관측소 명``, ``위도``, ``경도`` and the four
            amplitude columns above).

    Returns:
        A copy with ``ahhw_m`` added, and the normalised columns ``name``, ``lat``, ``lon``.
    """
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    out["ahhw_m"] = out[list(AMPLITUDE_COLUMNS)].sum(axis=1) / 100.0
    out["name"] = out["관측소 명"].astype(str).str.strip()
    out["lat"] = out["위도"].astype(float)
    out["lon"] = out["경도"].astype(float)
    return out


def load_stations(path: Path) -> Any:
    """The KHOA station table with ``name, lat, lon, ahhw_m`` (CSV is cp949-encoded)."""
    import pandas as pd

    raw = pd.read_csv(path, encoding="cp949")
    return station_ahhw(raw)[["name", "lat", "lon", "ahhw_m"]]


def _station_arrays(stations: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.asarray(stations["lat"], dtype=float),
        np.asarray(stations["lon"], dtype=float),
        np.asarray(stations["ahhw_m"], dtype=float),
    )


def nearest_station(lat: float, lon: float, stations: Any) -> dict[str, Any]:
    """The station nearest to a point.

    Algorithm:
        $$d^2 = (\\varphi - \\varphi_s)^2
        + \\left[(\\lambda - \\lambda_s)\\cos\\varphi_0\\right]^2$$
        ASCII: d^2 = dlat^2 + (dlon * cos(lat0))^2, lat0 = the point's latitude

        where $\\varphi$, $\\lambda$ are latitude and longitude (degrees) and $\\varphi_0$ the
        point's latitude, so a degree of longitude is shortened to its true ground length.

    Args:
        lat: Point latitude (degrees).
        lon: Point longitude (degrees).
        stations: Table with ``name, lat, lon, ahhw_m``.

    Returns:
        The station row as a plain dict.
    """
    slat, slon, _ = _station_arrays(stations)
    k = math.cos(math.radians(lat))
    i = int(np.argmin((slat - lat) ** 2 + ((slon - lon) * k) ** 2))
    return dict(stations.iloc[i].to_dict())


def block_reduce(a: np.ndarray, f: int, how: str) -> np.ndarray:
    """Reduce a 2-D array by ``f`` x ``f`` blocks with the minimum or maximum.

    The array is trimmed to multiples of ``f`` first. NaN propagates (a block with any NaN is
    NaN), as in the validated analysis.

    Args:
        a: 2-D array.
        f: Block size in cells.
        how: ``"min"`` (removes barriers narrower than ``f`` cells) or ``"max"``.

    Raises:
        ValueError: ``how`` is neither ``min`` nor ``max``.
    """
    if how not in ("min", "max"):
        raise ValueError(f"how must be 'min' or 'max', not {how!r}")
    ny, nx = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    b = a[:ny, :nx].reshape(ny // f, f, nx // f, f)
    return np.asarray(b.min(axis=(1, 3)) if how == "min" else b.max(axis=(1, 3)))


def tidal_mask(z: np.ndarray, threshold: Any, sea: np.ndarray) -> np.ndarray:
    """Cells below the threshold that are 8-connected to the sea.

    Algorithm:
        $$L = \\{z < \\tau\\} \\cup S,\\qquad T = \\{c \\in L \\setminus S :
        \\operatorname{comp}_8(c) \\cap S \\ne \\varnothing\\}$$
        ASCII: low = (z < tau) or sea; tidal = low cells, not sea, whose 8-connected
        component of low contains a sea cell

        where $z$ is ground elevation above MSL (m), $\\tau$ the threshold (m; a scalar or
        one value per cell), $S$ the sea cells and $T$ the tidal cells. NaN elevation counts
        as sea.

    Args:
        z: Elevation (m), NaN = no data (sea).
        threshold: Scalar or array broadcastable to ``z`` (m).
        sea: Boolean array, True for sea cells.

    Returns:
        Boolean array, True where the cell is tidal land.
    """
    from scipy import ndimage

    nan = np.isnan(z)
    sea_all = np.asarray(sea, dtype=bool) | nan
    low = (np.where(nan, -np.inf, z) < threshold) | sea_all
    labels, _ = ndimage.label(low, structure=np.ones((3, 3), dtype=int))
    sea_labels = np.unique(labels[sea_all])
    sea_labels = sea_labels[sea_labels > 0]
    return np.asarray(np.isin(labels, sea_labels) & ~sea_all)


# --------------------------------------------------------------------------- #
# Terrain mosaic (disk cache + GLO-30)                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Mosaic:
    """3-arcsec block-min / block-max elevation over whole 1 degree tiles, rows north to south."""

    west: float
    north: float
    zmin: np.ndarray
    zmax: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.zmin.shape[0]), int(self.zmin.shape[1]))

    @property
    def res_deg(self) -> float:
        return 1.0 / CELLS_PER_DEG

    def cell(self, lat: float, lon: float) -> tuple[int, int] | None:
        """``(row, col)`` containing a point, or None if outside the mosaic."""
        r = math.floor((self.north - lat) * CELLS_PER_DEG)
        c = math.floor((lon - self.west) * CELLS_PER_DEG)
        ny, nx = self.shape
        return (r, c) if 0 <= r < ny and 0 <= c < nx else None


def _tile_name(la: int, lo: int) -> str:
    return f"N{la:02d}_E{lo:03d}"


def _read_glo30_tile(la: int, lo: int, tile_dir: Path, allow_download: bool) -> np.ndarray | None:
    """Full-resolution GLO-30 tile (local file first, else remote), None for an ocean tile.

    Raises:
        TerrainUnavailable: not local and not downloadable (offline) or a network failure.
    """
    import rasterio

    from climaterisk_worker.ingest import _copdem_tile_url

    url = _copdem_tile_url(la, lo)
    local = tile_dir / url.rsplit("/", 1)[-1]
    if local.exists():
        src = str(local)
    elif allow_download:
        src = "/vsicurl/" + url
    else:
        raise TerrainUnavailable(f"GLO-30 tile {_tile_name(la, lo)} not cached and offline")
    try:
        with rasterio.open(src) as ds:
            return np.asarray(ds.read(1), dtype=np.float32)
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}".lower()
        if "404" in text or "not found" in text or "does not exist" in text:
            return None  # no tile published: open sea
        raise TerrainUnavailable(f"GLO-30 tile {_tile_name(la, lo)}: {exc}") from exc


def load_tile(la: int, lo: int, *, allow_download: bool) -> tuple[np.ndarray, np.ndarray]:
    """Block-min and block-max (3 arcsec) of the 1 degree tile with SW corner ``(la, lo)``.

    Read from ``cache/glo30_blockmin/N{la}_E{lo}_min3.npy`` / ``_max3.npy``; created from
    GLO-30 when missing. Ocean tiles are all NaN.
    """
    from climaterisk_worker._paths import paths

    cache = paths.cache_dir(_CACHE_NAME)
    fmin = cache / f"{_tile_name(la, lo)}_min{FACTOR}.npy"
    fmax = cache / f"{_tile_name(la, lo)}_max{FACTOR}.npy"
    if fmin.exists() and fmax.exists():
        return np.load(fmin), np.load(fmax)
    raw = _read_glo30_tile(la, lo, paths.external_dir("copernicus_dem"), allow_download)
    if raw is None:
        zmin = zmax = np.full((CELLS_PER_DEG, CELLS_PER_DEG), np.nan, np.float32)
    else:
        zmin, zmax = block_reduce(raw, FACTOR, "min"), block_reduce(raw, FACTOR, "max")
    cache.mkdir(parents=True, exist_ok=True)
    np.save(fmin, zmin)
    np.save(fmax, zmax)
    log.info("glo30 block-min/max cached for tile %s", _tile_name(la, lo))
    return zmin, zmax


def build_mosaic(bbox: tuple[float, float, float, float], *, allow_download: bool) -> Mosaic:
    """Block-min / block-max mosaic of the whole tiles covering ``(lon0, lat0, lon1, lat1)``."""
    minlon, minlat, maxlon, maxlat = bbox
    la0, la1 = math.floor(minlat), math.floor(maxlat)
    lo0, lo1 = math.floor(minlon), math.floor(maxlon)
    n = CELLS_PER_DEG
    zmin = np.full(((la1 - la0 + 1) * n, (lo1 - lo0 + 1) * n), np.nan, np.float32)
    zmax = zmin.copy()
    for la in range(la0, la1 + 1):
        for lo in range(lo0, lo1 + 1):
            a, b = load_tile(la, lo, allow_download=allow_download)
            r0, c0 = (la1 - la) * n, (lo - lo0) * n
            zmin[r0 : r0 + n, c0 : c0 + n] = a[:n, :n]
            zmax[r0 : r0 + n, c0 : c0 + n] = b[:n, :n]
    log.debug("coastal mosaic shape=%s dtype=%s", zmin.shape, zmin.dtype)
    return Mosaic(west=float(lo0), north=float(la1 + 1), zmin=zmin, zmax=zmax)


def land_mask(mosaic: Mosaic, shp: Path) -> np.ndarray:
    """True on cells inside the SGIS 시도 land polygons (rasterised on the mosaic grid)."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    ny, nx = mosaic.shape
    res = mosaic.res_deg
    east, south = mosaic.west + nx * res, mosaic.north - ny * res
    gdf = gpd.read_file(shp).to_crs(4326)
    gdf = gdf.cx[mosaic.west : east, south : mosaic.north]
    if gdf.empty:
        return np.zeros((ny, nx), dtype=bool)
    return np.asarray(
        rasterize(
            [(g, 1) for g in gdf.geometry],
            out_shape=(ny, nx),
            transform=from_origin(mosaic.west, mosaic.north, res, res),
            fill=0,
            dtype="uint8",
        ).astype(bool)
    )


def station_threshold_grid(mosaic: Mosaic, stations: Any) -> np.ndarray:
    """AHHW (m) of the nearest station for every cell, evaluated per coarse block.

    The nearest station is found at the centre of each ``THRESHOLD_BLOCK_CELLS`` block
    (~1.8 km) and repeated over its cells, as in the validated analysis. An asset's own
    depth uses the station nearest to the asset itself; the two differ only within a block
    straddling a station boundary.
    """
    from scipy.spatial import cKDTree

    ny, nx = mosaic.shape
    cs = THRESHOLD_BLOCK_CELLS
    gy, gx = np.mgrid[0:ny:cs, 0:nx:cs]
    glat = mosaic.north - (gy + cs / 2) / CELLS_PER_DEG
    glon = mosaic.west + (gx + cs / 2) / CELLS_PER_DEG
    slat, slon, sahhw = _station_arrays(stations)
    k = math.cos(math.radians(float(np.mean(glat))))
    _, idx = cKDTree(np.c_[slat, slon * k]).query(np.c_[glat.ravel(), glon.ravel() * k])
    coarse = sahhw[idx].reshape(gy.shape)
    return np.repeat(np.repeat(coarse, cs, 0), cs, 1)[:ny, :nx].astype(np.float32)
