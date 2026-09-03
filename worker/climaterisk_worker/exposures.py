"""Modeled-exposure builders — turn a country into a gridded CLIMADA ``Exposures``.

The platform's default exposure is the user's hand-placed point assets. This module
adds the *modeled* exposure sources CLIMADA / climada_petals can synthesise for a whole
country, so an impact run does not need a hand-built portfolio:

  - ``litpop``      LitPop nightlight × population value grid (CLIMADA).
  - ``blackmarble`` BlackMarble nightlight-only value grid (climada_petals).
  - ``gdp``         GDP2Asset gridded GDP-to-asset value (climada_petals).
  - ``crop``        CropProduction agricultural exposure (climada_petals, ISIMIP/SPAM).
  - ``osm``         OpenStreetMap building footprints (climada_petals osm-flex).

Most need external data that is login-gated (GPW for LitPop, BlackMarble tiles),
large (OSM ``.osm.pbf`` extracts), or scenario-specific (ISIMIP crop NetCDF). When the
data is absent we raise :class:`ExposureUnavailable` with an actionable message — the
same graceful-degradation contract the rest of the platform uses — instead of failing
opaquely. CLIMADA itself is imported lazily so this module stays importable (and its
help text testable) without the worker env.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Actionable "where to get the data" help per source. Kept climada-free at module level
# so the registry and messages are importable/testable without the CLIMADA worker env.
EXPOSURE_HELP: dict[str, str] = {
    "litpop": (
        "LitPop needs the GPW v4 population GeoTIFF (free NASA Earthdata login, no "
        "auto-download). Get gpw-v4-population-count-rev11_2020_30_sec_tif.zip from "
        "https://sedac.ciesin.columbia.edu/data/collection/gpw-v4 and unzip under "
        "~/climada/data/, then re-run."
    ),
    "blackmarble": (
        "BlackMarble needs NASA Black Marble nightlight tiles and the GPW population "
        "layer (Earthdata login). CLIMADA fetches the nightlights on first use; if that "
        "fails, download them manually into ~/climada/data/ and re-run."
    ),
    "gdp": (
        "GDP2Asset needs a gridded GDP NetCDF (e.g. the ISIMIP/Geiger asset-value grid). "
        "Place it under ~/climada/data/ and set the GDP2Asset path, then re-run."
    ),
    "crop": (
        "CropProduction needs an ISIMIP crop NetCDF (e.g. histsoc yield/area) or the "
        "SPAM raster. Download the ISIMIP product, drop it under ~/climada/data/, and "
        "use the crop-production importer; this source cannot be auto-fetched."
    ),
    "osm": (
        "OSM building exposure needs an OpenStreetMap extract: download the country's "
        ".osm.pbf from https://download.geofabrik.de/ (open, no login) into "
        "~/climada/data/osm/ (any filename containing the country name or ISO3 works), "
        "then re-run. Large countries can be multi-GB and take minutes to extract."
    ),
    "raster": (
        "No population/value raster found for this country. Use the Data tab to download "
        "WorldPop 1 km population for the portfolio's country (it lands in ~/climada/data), "
        "or drop a GeoTIFF there and set CLIMATERISK_EXPOSURE_RASTER, then re-run."
    ),
    "population_ref": (
        "Reference-city population covers only the countries in the heat-health reference "
        "table (ESP, ITA, FRA, DEU, GRC, PRT). Add rows for this country to REF_CITIES in "
        "worker/climaterisk_worker/heat_mortality.py, or use the 'raster' source with a "
        "WorldPop population GeoTIFF for full national coverage."
    ),
}

# Display metadata for the UI / result payloads.
EXPOSURE_SOURCES: dict[str, dict[str, str]] = {
    "litpop": {"label": "LitPop (nightlight × population)", "engine": "climada.LitPop"},
    "blackmarble": {"label": "BlackMarble (nightlights)", "engine": "petals.BlackMarble"},
    "gdp": {"label": "GDP2Asset (gridded GDP)", "engine": "petals.GDP2Asset"},
    "crop": {"label": "Crop production (ISIMIP/SPAM)", "engine": "petals.CropProduction"},
    "osm": {"label": "OSM buildings (osm-flex)", "engine": "osm_flex.extract"},
    "raster": {
        "label": "Population/value raster (WorldPop/GHSL)",
        "engine": "climada.Exposures.from_raster",
    },
    "population_ref": {
        "label": "Reference-city population (health perils)",
        "engine": "climaterisk_worker.heat_mortality.REF_CITIES",
    },
}

# Exposure sources whose cell ``value`` is **people**, not currency. Health perils
# (heat_mortality) consume these as headcount; damage perils must not treat them as money.
POPULATION_SOURCES = frozenset({"population_ref", "raster"})

_HOME_CLIMADA = Path.home() / "climada" / "data"
_OSM_DIR = _HOME_CLIMADA / "osm"


def _resolve_osm_pbf(country: str) -> Path | None:
    """Find a Geofabrik ``.osm.pbf`` for ``country`` under ``~/climada/data/osm``.

    Matches, in order: a filename starting with the ISO3 code (``kor.osm.pbf``), a
    filename containing a word of the country's short name (``south-korea-latest``),
    and finally — when exactly one extract is present — that file.
    """
    if not _OSM_DIR.is_dir():
        return None
    pbfs = sorted(_OSM_DIR.glob("*.osm.pbf"))
    if not pbfs:
        return None
    iso = country.lower()
    for p in pbfs:
        if p.name.lower().startswith(iso):
            return p
    try:
        import country_converter as coco

        name = str(coco.convert(names=[country], to="name_short", not_found=country))
        tokens = [t for t in name.lower().replace(",", " ").split() if len(t) >= 4]
    except Exception:
        tokens = []
    for p in pbfs:
        if any(t in p.name.lower() for t in tokens):
            return p
    # Single-file fallback only when the country name could not be derived at all —
    # otherwise a lone extract for country A would silently serve a request for B.
    return pbfs[0] if (len(pbfs) == 1 and not tokens) else None


def _osm_building_exposure(pbf: Path, res_arcsec: int) -> Any:
    """Gridded building-footprint exposure (m² of built area per cell) from a ``.osm.pbf``.

    Extracts every ``building`` multipolygon with osm-flex, then aggregates footprint
    area onto a ``res_arcsec`` grid so the result flows through the same per-peril
    runners as the other modeled sources (a country's buildings one-by-one would not
    be tractable). ``value_unit`` is ``m²`` — impacts read as damaged built area.
    """
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    # Extracting ~10⁶ polygons takes minutes; cache the aggregated grid per (pbf, res)
    # so re-runs (other perils/scenarios on the same extract) start in seconds.
    cache = _OSM_DIR / f"{pbf.name}.{res_arcsec}s.cells.csv"
    if cache.is_file() and cache.stat().st_mtime >= pbf.stat().st_mtime:
        cells = pd.read_csv(cache)
    else:
        from osm_flex.extract import extract

        buildings = extract(pbf, "multipolygons", ["building"])
        if buildings.empty:
            raise ExposureUnavailable("osm", f"no building polygons found in {pbf.name}")

        # Equal-area CRS for footprint areas; centroids computed there too (planar-safe).
        eq = buildings.geometry.to_crs(epsg=6933)
        cent = eq.centroid.to_crs(epsg=4326)
        res_deg = res_arcsec / 3600.0
        df = pd.DataFrame(
            {
                "longitude": np.floor(cent.x / res_deg) * res_deg + res_deg / 2,
                "latitude": np.floor(cent.y / res_deg) * res_deg + res_deg / 2,
                "value": eq.area.to_numpy(),
            }
        )
        cells = df.groupby(["longitude", "latitude"], as_index=False)["value"].sum()
        cells.to_csv(cache, index=False)

    from climada.entity import Exposures

    exp = Exposures(
        gpd.GeoDataFrame(
            cells,
            geometry=gpd.points_from_xy(cells["longitude"], cells["latitude"]),
            crs="EPSG:4326",
        )
    )
    exp.value_unit = "m²"
    return exp


def _resolve_exposure_raster(country: str) -> Path | None:
    """Find a population/value GeoTIFF for the modeled-exposure 'raster' source.

    Resolution order: explicit ``CLIMATERISK_EXPOSURE_RASTER`` env path → the WorldPop
    1 km file the Data tab downloads (``<iso3>_ppp_2020_1km_Aggregated.tif``) under
    ~/climada/data. Returns None if nothing is present (caller degrades gracefully).
    """
    import os

    explicit = os.environ.get("CLIMATERISK_EXPOSURE_RASTER")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    worldpop = _HOME_CLIMADA / f"{country.lower()}_ppp_2020_1km_Aggregated.tif"
    if worldpop.is_file():
        return worldpop
    return None


class ExposureUnavailable(Exception):
    """Raised when a modeled-exposure source's data is missing; carries actionable help."""

    def __init__(self, source: str, detail: str | None = None) -> None:
        self.source = source
        self.detail = detail or EXPOSURE_HELP.get(source, f"exposure source '{source}' unavailable")
        super().__init__(self.detail)


def _raster_exposure(raster: Path, res_arcsec: int) -> Any:
    """Load a population/value GeoTIFF as ``Exposures``, block-aggregated to ``res_arcsec``.

    A WorldPop 1 km raster is ~776k cells for Spain — far finer than any hazard grid, so
    running it cell-by-cell buys no accuracy and costs a lot. Cells are aggregated in
    integer blocks by **sum**, which is the correct reduction for counts (people) and for
    values (money); averaging would silently destroy the total. Empty cells are dropped.

    Args:
        raster: Path to the GeoTIFF.
        res_arcsec: Target resolution in arc-seconds (300 ~ 8 km).

    Returns:
        A CLIMADA ``Exposures`` with ``value`` summed per aggregated cell. ``value_unit``
        is "persons" for WorldPop population counts (``*_ppp_*`` files).
    """
    import numpy as np
    import pandas as pd
    import rasterio
    from climada.entity import Exposures

    with rasterio.open(raster) as src:
        arr = src.read(1, masked=True).filled(0.0).astype(float)
        arr[arr < 0] = 0.0  # WorldPop uses a large negative nodata
        transform, res_deg = src.transform, abs(src.transform.a)

    factor = max(1, round((res_arcsec / 3600.0) / res_deg))
    if factor > 1:  # trim to a whole number of blocks, then sum within each block
        rows = (arr.shape[0] // factor) * factor
        cols = (arr.shape[1] // factor) * factor
        arr = (
            arr[:rows, :cols]
            .reshape(rows // factor, factor, cols // factor, factor)
            .sum(axis=(1, 3))
        )

    n_row, n_col = arr.shape
    step = res_deg * factor
    lon0, lat0 = transform.c, transform.f  # upper-left corner
    lon = lon0 + (np.arange(n_col) + 0.5) * step
    lat = lat0 - (np.arange(n_row) + 0.5) * step
    lon_g, lat_g = np.meshgrid(lon, lat)

    keep = arr > 0
    exp = Exposures(
        pd.DataFrame(
            {
                "latitude": lat_g[keep],
                "longitude": lon_g[keep],
                "value": arr[keep],
            }
        ),
        value_unit="persons" if "_ppp_" in raster.name else "USD",
    )
    return exp


def _reference_population_exposure(country: str) -> Any:
    """Resident population of a country's reference locations, as a CLIMADA ``Exposures``.

    The exposure unit for **health** perils: each point is a province / metro area and its
    ``value`` is **people**, so ``heat_mortality`` can be run for a whole country without
    the user placing any facility. Coarse by construction (one point per reference
    location) — the ``raster`` source gives a true population grid where one exists.

    Args:
        country: ISO3 country code.

    Returns:
        A CLIMADA ``Exposures`` with ``value`` = residents and ``value_unit`` "persons".

    Raises:
        ExposureUnavailable: when no reference locations cover the country.
    """
    import numpy as np
    import pandas as pd
    from climada.entity import Exposures

    from climaterisk_worker.heat_mortality import REF_CITIES

    cities = [c for c in REF_CITIES if c.country == country.upper()]
    if not cities:
        covered = sorted({c.country for c in REF_CITIES})
        raise ExposureUnavailable(
            "population_ref",
            f"no reference-population points for '{country}'; covered: {', '.join(covered)}",
        )
    exp = Exposures(
        pd.DataFrame(
            {
                "latitude": np.array([c.lat for c in cities]),
                "longitude": np.array([c.lon for c in cities]),
                "value": np.array([float(c.population) for c in cities]),
            }
        ),
        value_unit="persons",
    )
    return exp


def build_exposure(source: str, country: str, res_arcsec: int = 300) -> Any:
    """Build a gridded CLIMADA ``Exposures`` for ``country`` from a modeled source.

    Args:
        source: one of :data:`EXPOSURE_SOURCES` keys.
        country: ISO3 country code.
        res_arcsec: target grid resolution (arc-seconds) where the source supports it.

    Returns:
        A CLIMADA ``Exposures`` with a ``value`` column.

    Raises:
        ExposureUnavailable: when the source is unknown or its data is absent.
    """
    if source not in EXPOSURE_SOURCES:
        raise ExposureUnavailable(source, f"unknown exposure source '{source}'")

    # Sources that require an explicit local data file we cannot synthesise here: fail fast
    # with the actionable message rather than a doomed call deep inside the library.
    if source == "crop":
        raise ExposureUnavailable(source)

    try:
        if source == "litpop":
            from climada.entity import LitPop

            return LitPop.from_countries(country, res_arcsec=res_arcsec)
        if source == "blackmarble":
            from climada_petals.entity.exposures.black_marble import BlackMarble

            exp = BlackMarble()
            exp.set_countries([country])
            return exp
        if source == "gdp":
            from climada_petals.entity.exposures.gdp_asset import GDP2Asset

            exp = GDP2Asset()
            exp.set_countries(countries=[country], res_arcsec=res_arcsec)
            return exp
        if source == "population_ref":
            return _reference_population_exposure(country)
        if source == "raster":
            raster = _resolve_exposure_raster(country)
            if raster is None:
                raise ExposureUnavailable("raster")  # actionable: no raster on disk
            return _raster_exposure(raster, res_arcsec)
        if source == "osm":
            pbf = _resolve_osm_pbf(country)
            if pbf is None:
                raise ExposureUnavailable("osm")  # actionable: no .osm.pbf on disk
            return _osm_building_exposure(pbf, res_arcsec)
    except ExposureUnavailable:
        raise
    except (FileNotFoundError, OSError) as exc:
        raise ExposureUnavailable(source, f"{EXPOSURE_HELP[source]} ({str(exc)[:120]})") from exc
    except Exception as exc:  # library-specific failures (missing tiles, bad config)
        detail = f"{EXPOSURE_HELP[source]} ({type(exc).__name__})"
        raise ExposureUnavailable(source, detail) from exc

    raise ExposureUnavailable(source)  # unreachable, keeps type-checkers happy
