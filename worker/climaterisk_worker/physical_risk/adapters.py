"""Hazard adapters — where each model's hazard comes from, kept apart from the pricing.

::

    HazardAdapter.load()  ->  canonical CLIMADA Hazard  ->  engine.calculate (ImpactCalc)

One adapter per (hazard x model). The three models share exposure and impact function;
an adapter only answers *which dataset* and *what it actually is*:

* ``GLOBAL_BASELINE`` — CLIMADA Data API, ``spatial_coverage=global``.
* ``DATA_API_COUNTRY`` — CLIMADA Data API, ``spatial_coverage=country`` for one ISO3.
  The same product cropped to a country: a **country-specific Data API hazard**, not
  Korea-local source data.
* Coastal flood (``CF``) — the Data API has no coastal-flood type, so ``GLOBAL_BASELINE``
  reads the global WRI Aqueduct Floods v2 coastal-inundation GeoTIFFs over the portfolio's
  bbox (:class:`GlobalCoastalFloodAdapter`), ``DATA_API_COUNTRY`` has no data and
  ``KOREA_LOCAL`` is an interface only.
* Urban pluvial flood (``PF``) — only ``KOREA_LOCAL`` has data, the official 100-y 도시침수지도
  (:class:`KoreaLocalPluvialAdapter`, hazard-only); the global and Data API cells have none.
* ``KOREA_LOCAL`` — a domestic dataset through a dedicated adapter. For flood and TC the
  adapters exist with ``status = NOT_IMPLEMENTED`` and ``load()`` returns None: the
  환경부 홍수위험지도 licence and access are unresolved and no domestic TC set is on hand,
  so nothing is downloaded, copied or computed. Heat is the one Korea-local source that
  exists (KMA 남한상세 via the local catalog) and it is hazard-only.

Every Data API fetch goes by **dataset name**, built deterministically from the request
(:func:`dataapi_dataset_name`), so the row records exactly which file was priced and the
served scenario is read off that name rather than assumed. The requested→served mapping
for flood (``rcp45 → rcp60``) is the platform's existing ``RF_SCENARIO_MAP``; the adapter
does not hide it — the row carries both and the engine marks the mismatch.
"""

from __future__ import annotations

import copy
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from climaterisk_worker._params import (
    RF_SCENARIO_MAP,
    RF_YEAR_RANGES,
    TC_REF_YEARS,
    nearest,
)

logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from climaterisk.physical_risk.metrics import ModelId  # noqa: E402
from climaterisk.physical_risk.models import READINESS  # noqa: E402

#: Adapter statuses. ``READY`` / ``HAZARD_ONLY`` can load; the other two never do.
READY = "READY"
HAZARD_ONLY = "HAZARD_ONLY"
NO_HAZARD_DATA = "NO_HAZARD_DATA"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

#: Degrees of padding around a facility bounding box when cropping a global set.
_CROP_PAD_DEG = 1.0


class HazardUnavailable(RuntimeError):
    """The cell can run in principle but has no hazard right now (e.g. offline, not cached).

    The runner turns it into ``NO_HAZARD_DATA`` rows carrying the message — distinct from a
    calculation ``ERROR``.
    """


@dataclass(frozen=True)
class HazardDescription:
    """What an adapter would price — recorded on the row whether or not a load happened."""

    hazard_type: str
    model_id: str
    status: str
    hazard_source: str
    hazard_dataset: str | None
    hazard_data_version: str | None
    hazard_country: str | None
    requested_scenario: str | None
    served_scenario: str | None
    time_horizon: str | None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HazardAdapter(Protocol):
    """The adapter interface. ``describe`` is pure; ``load`` may touch disk or network."""

    hazard_type: str
    model_id: str
    status: str

    def describe(self) -> HazardDescription: ...

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any: ...

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None: ...


# --------------------------------------------------------------------------- #
# Spatial resolution — read from the data, never typed in                        #
# --------------------------------------------------------------------------- #
def measure_grid_spacing(hazard: Any) -> float | None:
    """Median spacing (degrees) between neighbouring centroid rows/columns of a hazard.

    Uses the unique latitudes and longitudes of the centroids, so a cropped or masked grid
    (land cells only) still reports its native step. None when fewer than two distinct
    coordinates exist.
    """
    import numpy as np

    lats = np.unique(np.round(np.asarray(hazard.centroids.lat, dtype=float), 6))
    lons = np.unique(np.round(np.asarray(hazard.centroids.lon, dtype=float), 6))
    steps = np.concatenate([np.diff(lats), np.diff(lons)])
    steps = steps[steps > 0]
    if steps.size == 0:
        return None
    return float(np.median(steps))


def resolution_record(
    value: float,
    unit: str,
    unit_type: str,
    *,
    source: str,
    measured_deg: float | None,
    dataset_family: str,
) -> dict[str, Any]:
    """The row fields for one dataset's resolution, plus a consistency note when the
    declared value and the measured centroid spacing disagree by more than 5 %."""
    from climaterisk.physical_risk.coverage import km_class

    declared_deg = value / 3600.0 if unit == "arcsec" else value
    consistent = (
        None if measured_deg is None else abs(measured_deg - declared_deg) <= 0.05 * declared_deg
    )
    description = f"{value:g} {unit} {unit_type} ({km_class(value, unit)} class in Korea)"
    if consistent is False:
        description += (
            f" — NOTE: measured centroid spacing {measured_deg:.6f} deg differs from the "
            f"declared {declared_deg:.6f} deg"
        )
    return {
        "value": float(value),
        "unit": unit,
        "unit_type": unit_type,
        "description": description,
        "source": source,
        "dataset_family": dataset_family,
        "measured_centroid_spacing_deg": measured_deg,
        "consistent_with_measurement": consistent,
    }


# --------------------------------------------------------------------------- #
# Data API naming — deterministic, testable without network                    #
# --------------------------------------------------------------------------- #
def rf_year_range(year: int) -> str:
    """The Data API river-flood window containing ``year`` (same rule as ``ingest.py``)."""
    return next((r for r in RF_YEAR_RANGES if int(r[:4]) <= year <= int(r[5:])), "2030_2050")


def dataapi_dataset_name(
    hazard_type: str, scenario: str, year: int, *, coverage: str, iso3: str | None
) -> tuple[str, str, str]:
    """``(dataset_name, served_scenario, time_horizon)`` for a Data API RF / TC request.

    ``coverage`` is ``"global"`` or ``"country"``; a country name carries the ISO3 token.
    ``scenario == "historical"`` selects the observed-period product. For flood the served
    scenario follows the platform's ``RF_SCENARIO_MAP`` (the API publishes rcp26/60/85);
    for TC every platform scenario exists, so served equals requested.
    """
    if coverage not in ("global", "country"):
        raise ValueError(f"coverage must be 'global' or 'country', got {coverage!r}")
    if coverage == "country" and not iso3:
        raise ValueError("country coverage needs an ISO3 code")
    geo = iso3 if coverage == "country" else None
    if hazard_type == "RF":
        if scenario == "historical":
            served, window = "historical", "1980_2000"
            token = "hist"
        else:
            served = RF_SCENARIO_MAP.get(scenario, "rcp60")
            window = rf_year_range(year)
            token = served
        parts = ["river_flood_150arcsec", token] + ([geo] if geo else []) + [window]
        return "_".join(parts), served, window.replace("_", "–")
    if hazard_type == "TC":
        if scenario == "historical":
            parts = ["tropical_cyclone_10synth_tracks_150arcsec", geo or "global", "1980_2020"]
            return "_".join(parts), "historical", "1980–2020"
        ref_year = nearest(TC_REF_YEARS, year)
        parts = [
            "tropical_cyclone_10synth_tracks_150arcsec",
            scenario,
            geo or "global",
            str(ref_year),
        ]
        return "_".join(parts), scenario, str(ref_year)
    raise ValueError(f"no Data API naming for hazard type {hazard_type!r}")


def _crop(hazard: Any, bbox: tuple[float, float, float, float] | None) -> Any:
    """Crop to ``(lat_min, lon_min, lat_max, lon_max)`` + padding; whole set if bbox is None."""
    if bbox is None:
        return hazard
    lat0, lon0, lat1, lon1 = bbox
    extent = (
        lon0 - _CROP_PAD_DEG,
        lon1 + _CROP_PAD_DEG,
        lat0 - _CROP_PAD_DEG,
        lat1 + _CROP_PAD_DEG,
    )
    try:
        cropped = hazard.select(extent=extent)
    except Exception:
        return hazard
    return hazard if cropped is None else cropped


# --------------------------------------------------------------------------- #
# Data API adapters (GLOBAL_BASELINE / DATA_API_COUNTRY)                        #
# --------------------------------------------------------------------------- #
class DataApiAdapter:
    """Base for the two Data API models; subclasses fix ``coverage`` and ``model_id``."""

    coverage: str = "global"
    model_id: str = ModelId.GLOBAL_BASELINE.value
    status: str = READY

    def __init__(self, hazard_type: str, scenario: str, year: int, iso3: str | None) -> None:
        if hazard_type not in ("RF", "TC"):
            raise ValueError("Data API adapters price RF and TC only")
        self.hazard_type = hazard_type
        self.scenario = scenario
        self.year = int(year)
        self.iso3 = iso3
        self.dataset, self.served_scenario, self.time_horizon = dataapi_dataset_name(
            hazard_type, scenario, self.year, coverage=self.coverage, iso3=iso3
        )
        self._version: str | None = None
        self._res_arcsec: float | None = None

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """The Data API ``res_arcsec`` property (read on load), checked against the grid."""
        if hazard is None:
            return None
        measured = measure_grid_spacing(hazard)
        if self._res_arcsec is None:
            if measured is None:
                return None
            # no property available (offline cache): the measurement is the record
            return resolution_record(
                round(measured * 3600.0, 3),
                "arcsec",
                "grid cell",
                source="measured centroid spacing (dataset property unavailable)",
                measured_deg=measured,
                dataset_family=self.dataset,
            )
        return resolution_record(
            self._res_arcsec,
            "arcsec",
            "grid cell",
            source="CLIMADA Data API dataset property res_arcsec",
            measured_deg=measured,
            dataset_family=self.dataset,
        )

    def describe(self) -> HazardDescription:
        product = (
            "river_flood (ISIMIP)"
            if self.hazard_type == "RF"
            else "tropical_cyclone (synthetic random_walk tracks)"
        )
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=f"CLIMADA Data API {product}, spatial_coverage={self.coverage}",
            hazard_dataset=self.dataset,
            hazard_data_version=self._version,
            hazard_country=self.iso3 if self.coverage == "country" else None,
            requested_scenario=self.scenario,
            served_scenario=self.served_scenario,
            time_horizon=self.time_horizon,
            detail=(
                "country-specific cut of the Data API product — not Korea-local source data"
                if self.coverage == "country"
                else None
            ),
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        from climada.util.api_client import Client

        from climaterisk_worker._dataapi import OfflineError, make_client, resilient_get_hazard

        client = make_client()
        try:
            info = client.get_dataset_info(name=self.dataset)
        except Client.NoConnection as exc:  # offline, and this dataset was never looked up
            raise OfflineError(
                f"offline mode (CLIMATERISK_OFFLINE): {self.dataset} was never fetched on this "
                "machine — run this analysis once while online"
            ) from exc
        self._version = str(info.version)
        try:
            self._res_arcsec = float((info.properties or {}).get("res_arcsec"))
        except (TypeError, ValueError):
            self._res_arcsec = None
        hazard = resilient_get_hazard(client, info.data_type.data_type, name=self.dataset)
        return _crop(hazard, bbox)


class GlobalFloodAdapter(DataApiAdapter):
    coverage, model_id = "global", ModelId.GLOBAL_BASELINE.value

    def __init__(self, scenario: str, year: int, iso3: str | None = None) -> None:
        super().__init__("RF", scenario, year, iso3)


class CountryFloodAdapter(DataApiAdapter):
    coverage, model_id = "country", ModelId.DATA_API_COUNTRY.value

    def __init__(self, scenario: str, year: int, iso3: str) -> None:
        super().__init__("RF", scenario, year, iso3)


class GlobalTCAdapter(DataApiAdapter):
    coverage, model_id = "global", ModelId.GLOBAL_BASELINE.value

    def __init__(self, scenario: str, year: int, iso3: str | None = None) -> None:
        super().__init__("TC", scenario, year, iso3)


class CountryTCAdapter(DataApiAdapter):
    coverage, model_id = "country", ModelId.DATA_API_COUNTRY.value

    def __init__(self, scenario: str, year: int, iso3: str) -> None:
        super().__init__("TC", scenario, year, iso3)


# --------------------------------------------------------------------------- #
# Coastal flood — WRI Aqueduct (GLOBAL_BASELINE)                                  #
# --------------------------------------------------------------------------- #
def aqueduct_coastal_plan(
    scenario: str, year: int, percentile: int, subsidence: str
) -> tuple[str, str, str, list[tuple[int, str]]]:
    """``(dataset, served_scenario, time_horizon, [(rp, file name), ...])`` for Aqueduct coastal.

    Aqueduct publishes historical and RCP 4.5 / 8.5 at 2030 / 2050 / 2080. A platform
    scenario maps to the emissions scenario the platform's Aqueduct ingest already uses
    (``rcp26 → rcp4p5``, ``rcp60 → rcp8p5``); the *served* scenario is named by its
    platform key so a mismatch is visible on the row. Future layers carry the
    sea-level-rise percentile token (``0_perc_50``; the bare ``0`` is the 95th);
    historical layers have none.
    """
    from climaterisk_worker import ingest

    rps = list(ingest._AQ_COAST_RPS)
    if scenario == "historical":
        token = f"inuncoast_historical_{subsidence}_hist"
        files = [(rp, f"{token}_rp{rp:04d}_0.tif") for rp in rps]
        return f"aqueduct_{token}", "historical", "hist", files
    aq = ingest._AQ_SCENARIO.get(scenario, "rcp8p5")
    served = {"rcp4p5": "rcp45", "rcp8p5": "rcp85"}[aq]
    yr = nearest(ingest._AQ_FUTURE_YEARS, int(year))
    slr = "0" if int(percentile) == 95 else f"0_perc_{int(percentile):02d}"
    token = f"inuncoast_{aq}_{subsidence}_{yr}"
    files = [(rp, f"{token}_rp{rp:04d}_{slr}.tif") for rp in rps]
    return f"aqueduct_{token}_{slr}", served, str(yr), files


def snapped_bbox(
    bbox: tuple[float, float, float, float], pad: float, snap: float
) -> tuple[float, float, float, float]:
    """``(lon_min, lat_min, lon_max, lat_max)`` padded and snapped outward to ``snap`` degrees.

    ``bbox`` is the runner's ``(lat_min, lon_min, lat_max, lon_max)``. Snapping lets
    portfolios in the same area share one cached read.
    """
    import math

    lat0, lon0, lat1, lon1 = bbox
    return (
        math.floor((lon0 - pad) / snap) * snap,
        math.floor((lat0 - pad) / snap) * snap,
        math.ceil((lon1 + pad) / snap) * snap,
        math.ceil((lat1 + pad) / snap) * snap,
    )


class GlobalCoastalFloodAdapter:
    """Coastal flood from WRI Aqueduct Floods v2 (inuncoast GeoTIFFs), one event per RP.

    Each return-period layer is read over ``/vsicurl`` for the portfolio's snapped bbox and
    cached as a compressed array under ``<DATA_ROOT>/cache/aqueduct`` (offline runs read the
    cache; with no cache they get ``NO_HAZARD_DATA``). Event frequencies are the incremental
    exceedance probabilities ``1/RP_i − 1/RP_{i+1}`` (the platform's Aqueduct ingest rule),
    so ``aai_agg`` integrates the exceedance curve and ``calc_freq_curve`` returns the RPs.

    The hazard keeps only (a) cells flooded at some return period and (b) the grid cell
    holding each facility. A facility's nearest centroid is then exactly its own 30-arcsec
    cell — a dry facility reads 0 m, never a wet cell kilometres away — while the centroid
    count stays small.
    """

    model_id = ModelId.GLOBAL_BASELINE.value
    status = READY
    hazard_type = "CF"

    def __init__(self, scenario: str, year: int, iso3: str | None = None) -> None:
        from climaterisk.physical_risk.metrics import load_config

        cfg = load_config()["coastal_flood"]
        self.scenario, self.year, self.iso3 = scenario, int(year), iso3
        self.percentile, self.subsidence = (
            int(cfg["sea_level_rise_percentile"]),
            str(cfg["subsidence"]),
        )
        self.pad, self.snap = float(cfg["bbox_pad_deg"]), float(cfg["bbox_snap_deg"])
        self.dataset, self.served_scenario, self.time_horizon, self.files = aqueduct_coastal_plan(
            scenario, self.year, self.percentile, self.subsidence
        )

    def describe(self) -> HazardDescription:
        slr = (
            "no SLR percentile (historical)"
            if self.served_scenario == "historical"
            else f"sea-level rise {self.percentile}th percentile"
        )
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=(
                f"WRI Aqueduct Floods v2 coastal inundation ({self.subsidence}, {slr}), "
                "30 arcsec — global product; the CLIMADA Data API has no coastal-flood type"
            ),
            hazard_dataset=self.dataset,
            hazard_data_version="Aqueduct Floods v2 (2020)",
            hazard_country=None,
            requested_scenario=self.scenario,
            served_scenario=self.served_scenario,
            time_horizon=self.time_horizon,
            detail="no coastal defences represented; one event per return period (2–1000 y)",
        )

    def _cache_path(self, box: tuple[float, float, float, float]) -> Path:
        from climaterisk_worker._paths import paths

        tag = "_".join(f"{v:g}" for v in box)
        return paths.cache_dir("aqueduct", f"{self.dataset}__{tag}.npz")

    def _layers(self, box: tuple[float, float, float, float]) -> dict[str, Any]:
        """``lat, lon (1-D grid axes), rps, depth[rp, row, col]`` — cache, else Aqueduct."""
        import numpy as np

        from climaterisk_worker import ingest
        from climaterisk_worker._params import offline

        cache = self._cache_path(box)
        if cache.exists():
            with np.load(cache) as z:
                return {k: z[k] for k in z.files}
        if offline():
            raise HazardUnavailable(
                f"offline mode and no cached Aqueduct coastal layer for {self.dataset} over "
                f"{box} — run once online to cache it"
            )
        import rasterio
        from rasterio.windows import from_bounds

        stack, rps, axes = [], [], None
        for rp, name in self.files:
            with rasterio.open(f"/vsicurl/{ingest._AQ_BASE}/{name}") as ds:
                win = from_bounds(*box, ds.transform).round_offsets().round_lengths()
                arr = ds.read(1, window=win).astype("float32")
                nd = ds.nodata
                if axes is None:
                    wt = ds.window_transform(win)
                    nrows, ncols = arr.shape
                    axes = (
                        wt.f + (np.arange(nrows) + 0.5) * wt.e,  # latitude, north → south
                        wt.c + (np.arange(ncols) + 0.5) * wt.a,  # longitude, west → east
                    )
            if nd is not None:
                arr[arr == nd] = 0.0
            arr[~np.isfinite(arr) | (arr < 0)] = 0.0
            stack.append(arr)
            rps.append(rp)
        assert axes is not None
        out = {
            "lat": np.asarray(axes[0]),
            "lon": np.asarray(axes[1]),
            "rps": np.asarray(rps),
            "depth": np.stack(stack),
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, **out)
        return out

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        import numpy as np
        from climada.hazard import Hazard
        from scipy import sparse

        from climaterisk_worker import ingest
        from climaterisk_worker.hazard_convert import _centroids

        if bbox is None:
            raise ValueError("the Aqueduct coastal adapter reads a bbox; none was given")
        box = snapped_bbox(bbox, self.pad, self.snap)
        layers = self._layers(box)
        lat_axis, lon_axis = layers["lat"], layers["lon"]
        depth = layers["depth"]  # (n_rp, n_lat, n_lon)
        n_lat, n_lon = depth.shape[1:]
        keep = (depth > 0).any(axis=0)
        dlat, dlon = float(lat_axis[1] - lat_axis[0]), float(lon_axis[1] - lon_axis[0])
        for lat, lon in points or []:
            i = int(np.clip(np.floor((lat - (lat_axis[0] - dlat / 2)) / dlat), 0, n_lat - 1))
            j = int(np.clip(np.floor((lon - (lon_axis[0] - dlon / 2)) / dlon), 0, n_lon - 1))
            keep[i, j] = True
        ii, jj = np.nonzero(keep)
        intensity = depth[:, ii, jj].astype(float)
        rps = [int(r) for r in layers["rps"]]
        hazard = Hazard(
            haz_type="CF",
            units="m",
            centroids=_centroids(lat_axis[ii], lon_axis[jj]),
            event_id=np.arange(1, len(rps) + 1),
            event_name=[f"rp{rp}" for rp in rps],
            date=np.zeros(len(rps), dtype=int),
            frequency=ingest._incremental_frequency(rps),
            frequency_unit="1/year",
            intensity=sparse.csr_matrix(intensity),
            fraction=sparse.csr_matrix((intensity > 0).astype(float)),
        )
        hazard.check()
        return hazard

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """The GeoTIFF pixel size (30 arcsec), checked against the centroids."""
        if hazard is None:
            return None
        return resolution_record(
            30.0,
            "arcsec",
            "grid cell",
            source="Aqueduct Floods v2 GeoTIFF pixel size (1/120°)",
            measured_deg=measure_grid_spacing(hazard),
            dataset_family=self.dataset,
        )


class _NoCoastalDataApiAdapter:
    """Coastal flood under DATA_API_COUNTRY: the Data API carries no such type."""

    model_id = ModelId.DATA_API_COUNTRY.value
    status = NO_HAZARD_DATA
    hazard_type = "CF"

    def __init__(self, scenario: str | None = None) -> None:
        self.scenario = scenario

    def describe(self) -> HazardDescription:
        why = "the CLIMADA Data API has no coastal-flood hazard type"
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=f"none — {why}",
            hazard_dataset=None,
            hazard_data_version=None,
            hazard_country=None,
            requested_scenario=self.scenario,
            served_scenario=None,
            time_horizon=None,
            detail=why,
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> None:
        return None

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        return None


# --------------------------------------------------------------------------- #
# KOREA_LOCAL — interfaces for flood and TC, a real hazard-only adapter for heat  #
# --------------------------------------------------------------------------- #
class _NotImplementedAdapter:
    """An adapter that exists so the model is addressable, and refuses to produce data."""

    model_id = ModelId.KOREA_LOCAL.value
    status = NOT_IMPLEMENTED
    hazard_type = ""
    source = ""
    reason = ""

    def __init__(
        self, scenario: str | None = None, year: int | None = None, iso3: str | None = "KOR"
    ) -> None:
        self.scenario, self.year, self.iso3 = scenario, year, iso3

    def describe(self) -> HazardDescription:
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=self.source,
            hazard_dataset=None,
            hazard_data_version=None,
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario=None,
            time_horizon=None,
            detail=self.reason,
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> None:
        """Always None. Never raises, never fabricates, never downloads."""
        return None

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        return None


def dem_edge_check(
    res: dict[str, Any],
    fd: Any,
    paths: Any,
    offline: Any,
    check_max: int,
    check_min_n: int,
    check_min_share: float,
) -> dict[str, Any]:
    """The DEM edge-depth sub check of an official class map; a failure is reported, not raised.

    Shared by the river (RF) and urban-pluvial (PF) Korea-local adapters: FwDET depth on up to
    ``check_max`` inside assets (bare-earth NGII DEM where it covers, else GLO-30) against the
    official band of the 100-y class. Diagnostic only.
    """
    try:
        dems: list[tuple[str, Any]] = []
        bare = sorted(paths.external_dir("ngii_dem").glob("**/*.img"))
        bare_s = fd.RasterSampler(bare) if bare else None
        if bare_s is not None:
            dems.append(("ngii_bare_earth_90m", fd.StrictSampler(bare_s)))
        glo = fd.Glo30Sampler(paths.external_dir("copernicus_dem"), allow_download=not offline())
        dems.append(("glo30_surface_model", glo))
        try:
            xs, ys = res["xy"]
            pairs = fd.edge_check(xs, ys, res["codes"][0], res["file100"], dems, check_max)
        finally:
            glo.close()
            if bare_s is not None:
                bare_s.close()
        return fd.check_summary(pairs, check_min_n, check_min_share)
    except Exception as exc:
        return {
            "method": "FwDET edge depth vs official depth band (diagnostic only)",
            "error": f"{type(exc).__name__}: {exc}",
            "warn": None,
        }


class KoreaLocalFloodAdapter:
    """River flood from the 환경부 홍수위험지도 official depth classes at each asset (tier T2).

    Main method: the deepest official depth class covering the asset, priced at a
    representative depth of its published band (``flood_map.depth_representative``, band
    midpoint by default; the open ≥5 m class at ``flood_map.open_class_depth_m``). No DEM.
    Sub check (diagnostic only, never changes the hazard): the FwDET edge depth on a sample
    of inside assets, compared with the band (:mod:`climaterisk_worker.floodmap_depth`).

    Centroids are the asset points themselves: the polygons are overlaid in memory, never
    rasterised or written (공공누리 제4유형; nonprofit internal use, user decision
    2026-11-13). One event per return period (100 / 200 / 500 y) with incremental
    exceedance frequencies, so floods more frequent than 100 y are not represented — the
    EAL is a lower bound on that side.

    Climate: ``historical`` is the map as is. A future scenario keeps the map depth and
    multiplies every event frequency by one national ISIMIP change factor (the EAL of the
    served scenario's window over the EAL of the same RCP's 2010–2030 window, unit exposure
    at every ISIMIP centroid, JRC Asia residential curve; :mod:`climaterisk_worker.flood_delta`).
    The 2010–2030 window itself is the present of that chain, so its factor is 1 by
    definition and nothing is downloaded. Per-시도 factors are a diagnostic sub check. No
    ISIMIP data (offline, nothing cached) raises ``HazardUnavailable`` — never f = 1.
    """

    model_id = ModelId.KOREA_LOCAL.value
    status = READY
    hazard_type = "RF"
    dataset = "floodmap_rfm_depth_class"

    def __init__(self, scenario: str, year: int, iso3: str | None = "KOR") -> None:
        from climaterisk.physical_risk.metrics import load_config

        cfg = load_config().get("flood_map", {})
        self.scenario, self.year, self.iso3 = scenario, int(year), iso3
        self.mode = str(cfg.get("depth_representative", "mid"))
        self.open_depth = float(cfg.get("open_class_depth_m", 5.0))
        self.check_max = int(cfg.get("check_max_points", 200))
        self.check_min_n = int(cfg.get("check_min_points", 20))
        self.check_min_share = float(cfg.get("check_min_share_in_band", 0.35))
        self.delta_min_wet = int(cfg.get("delta_min_wet_centroids", 20))
        self.delta_tol = float(cfg.get("delta_check_tol", 0.25))
        self.diagnostics: dict[str, Any] = {}
        self.climate_delta = scenario != "historical"
        self.served_scenario = "historical"
        self.time_horizon = "present"
        self.map_dataset = self.dataset
        self.isimip_future = self.isimip_present = ""
        if self.climate_delta:
            geo = self.iso3 or "KOR"
            self.served_scenario = RF_SCENARIO_MAP.get(scenario, "rcp60")
            window, present = rf_year_range(self.year), RF_YEAR_RANGES[0]
            self.time_horizon = window.replace("_", "–")
            self.isimip_future = f"river_flood_150arcsec_{self.served_scenario}_{geo}_{window}"
            self.isimip_present = f"river_flood_150arcsec_{self.served_scenario}_{geo}_{present}"
            self.map_dataset = (
                f"{type(self).dataset}+freq({self.isimip_future}/{self.isimip_present})"
            )

    def describe(self) -> HazardDescription:
        if self.climate_delta:
            climate = (
                f"Climate: the map depth is the present-day design flood; every event "
                f"frequency is multiplied by one national ISIMIP change factor "
                f"({self.isimip_future} / {self.isimip_present}, unit exposure, JRC Asia "
                "residential curve); the 2010–2030 window is the present of that chain "
                "(factor 1). Per-시도 factors: diagnostic sub check"
            )
        else:
            climate = "Present-day design-flood maps (levee failure assumed), no climate scenario"
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=(
                "환경부 홍수위험지도 하천범람지도 (국가하천 100/200/500 y, 지방하천 100 y): "
                f"official depth class at each asset, priced at the band {self.mode} "
                f"(open ≥5 m class at {self.open_depth:g} m). 출처: 환경부 (공공누리 제4유형)"
            ),
            hazard_dataset=self.map_dataset,
            hazard_data_version="홍수위험지도 download 2026-02 (map files dated 2026-02-09)",
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario=self.served_scenario,
            time_horizon=self.time_horizon,
            detail=(
                "method tier T2. Depth = official class band (≤0.5 / 0.5–1 / 1–2 / 2–5 / ≥5 m; "
                "code order inferred, not officially confirmed). Sub check: DEM edge depth vs "
                f"the band, diagnostic only. {climate}; "
                "assets outside every mapped polygon read 0 m; a missing national "
                "return-period file gives NaN (reason no_file_for_rp), never another "
                "return period's value; 지방하천 maps are 100-y only and used as a lower "
                "bound at 200/500 y; no event below 100 y"
            ),
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        if not points:
            raise ValueError("the Korea-local flood adapter reads asset points; none were given")
        key = (tuple((float(a), float(b)) for a, b in points), self.mode, self.open_depth)
        memo = KoreaLocalFloodAdapter._map_memo
        if memo is not None and memo[0] == key:
            # the same assets under the same map (e.g. the 2010–2030 baseline run after a
            # future run): the overlay takes minutes, so it is not repeated
            hazard, self.diagnostics = copy.deepcopy(memo[1]), copy.deepcopy(memo[2])
        else:
            hazard = self._map_hazard(points)
            KoreaLocalFloodAdapter._map_memo = (
                key,
                copy.deepcopy(hazard),
                copy.deepcopy(self.diagnostics),
            )
        if not self.climate_delta:
            return hazard
        from climaterisk_worker import flood_delta as fdl

        delta = self._delta(points)
        self.diagnostics["climate_delta"] = delta
        return fdl.scale_frequency(hazard, delta["factor"])

    #: Last map overlay ``(key, hazard, diagnostics)``, reused within one process.
    _map_memo: tuple[Any, Any, dict[str, Any]] | None = None

    def _map_hazard(self, points: list[tuple[float, float]]) -> Any:
        """The present-day map hazard at the asset points; sets the map diagnostics."""
        import numpy as np
        from climada.hazard import Hazard
        from scipy import sparse

        from climaterisk_worker import floodmap_depth as fd
        from climaterisk_worker import ingest
        from climaterisk_worker._params import offline
        from climaterisk_worker._paths import paths
        from climaterisk_worker.hazard_convert import _centroids

        root = paths.floodmap_dir()
        if not root.is_dir():
            raise HazardUnavailable(f"홍수위험지도 not found under {root}")
        index = fd.map_index(root, paths.cache_dir("floodmap", "rfm_index.json"))
        res = fd.classes_at_points(
            [(lon, lat) for lat, lon in points],
            index,
            fd.ClassIndex(),
            named_files=fd.named_files(root),
        )
        dep = fd.class_depths(res["codes"], self.mode, self.open_depth)
        depth = dep["depth"]
        self.diagnostics = {
            "classes": {f"rp{rp}": row for rp, row in zip(res["rps"], res["codes"], strict=True)},
            "depth_lower_m": dep["lower"].tolist(),
            "depth_upper_m": [[None if np.isnan(v) else v for v in r] for r in dep["upper"]],
            "per_point_reason": list(res["reason"]),  # aligned with ``points``
            "local_only": list(res["local_only"]),  # 100-y local map only: a lower bound
            "sub_check": self._check(res, fd, paths, offline),
        }
        rps = [int(r) for r in res["rps"]]
        lat = np.array([p[0] for p in points], dtype=float)
        lon = np.array([p[1] for p in points], dtype=float)
        hazard = Hazard(
            haz_type="RF",
            units="m",
            centroids=_centroids(lat, lon),
            event_id=np.arange(1, len(rps) + 1),
            event_name=[f"rp{rp}" for rp in rps],
            date=np.zeros(len(rps), dtype=int),
            frequency=ingest._incremental_frequency(rps),
            frequency_unit="1/year",
            intensity=sparse.csr_matrix(depth),
            fraction=sparse.csr_matrix((depth > 0).astype(float)),
        )
        hazard.check()
        return hazard

    def _delta(self, points: list[tuple[float, float]]) -> dict[str, Any]:
        """The national factor (main) and the per-시도 check at the assets (diagnostic)."""
        import numpy as np

        from climaterisk_worker import flood_delta as fdl
        from climaterisk_worker._paths import paths

        record: dict[str, Any] = {
            "basis": "national",
            "isimip_present_dataset": self.isimip_present,
            "isimip_future_dataset": self.isimip_future,
            "depth": "present-day official map (unchanged)",
        }
        if self.isimip_future == self.isimip_present:
            return {
                **record,
                "factor": 1.0,
                "by_region": {},
                "check": None,
                "note": "the 2010–2030 window is the present of this chain: factor 1 by definition",
            }
        stats = self._isimip_factors()
        national = stats["national"]
        if national is None or national <= 0:
            raise HazardUnavailable(
                f"no ISIMIP change factor: present-window EAL is zero ({self.isimip_present})"
            )
        check: dict[str, Any]
        try:
            shp = paths.external_dir("municipality", "sgis", self.SIDO_SHP)
            regions = fdl.assign_regions(
                np.array([p[0] for p in points]), np.array([p[1] for p in points]), shp
            )
            check = fdl.regional_check(
                national, stats["by_region"], [str(r) for r in regions], self.delta_tol
            )
        except Exception as exc:  # a diagnostic; never a reason to lose the result
            check = {"error": f"{type(exc).__name__}: {exc}", "warn": None}
        return {
            **record,
            "factor": float(national),
            "by_region": stats["by_region"],
            "n_wet_centroids": stats["n_wet"],
            "check": check,
        }

    SIDO_SHP = "bnd_sido_00_2025_2Q.shp"

    def _isimip_factors(self) -> dict[str, Any]:
        """National and per-시도 factors; cached by dataset names, one download at a time."""
        import json

        import numpy as np

        from climaterisk_worker import flood_delta as fdl
        from climaterisk_worker._dataapi import OfflineError
        from climaterisk_worker._paths import paths
        from climaterisk_worker.physical_risk.registry import flood_impact_function

        key = f"{self.isimip_future}__over__{self.isimip_present}"
        cache = paths.cache_dir("floodmap", f"delta_{key}.json")
        if cache.is_file():
            try:
                return dict(json.loads(cache.read_text()))
            except (OSError, ValueError):
                pass  # unreadable cache: recompute
        iso3 = self.iso3 or "KOR"
        func = flood_impact_function("Asia", "residential")
        x, mdd, paa = (np.asarray(a, dtype=float) for a in (func.intensity, func.mdd, func.paa))
        eals: list[Any] = []
        lat = lon = None
        try:
            for window_year in (RF_YEAR_RANGES[0][:4], str(self.year)):
                hz = CountryFloodAdapter(self.scenario, int(window_year), iso3).load(bbox=None)
                eals.append(fdl.unit_eal(hz.intensity, hz.fraction, hz.frequency, x, mdd, paa))
                if lat is None:
                    lat, lon = hz.centroids.lat, hz.centroids.lon
                elif len(lat) != len(hz.centroids.lat):
                    raise ValueError("ISIMIP present and future grids differ")
                del hz
        except OfflineError as exc:
            raise HazardUnavailable(str(exc)) from exc
        regions = np.full(len(eals[0]), fdl.UNASSIGNED, dtype=object)
        try:
            shp = paths.external_dir("municipality", "sgis", self.SIDO_SHP)
            regions = fdl.assign_regions(lat, lon, shp)
        except Exception as exc:
            logger.warning("시도 assignment of ISIMIP centroids failed: %s", exc)
        out = fdl.change_factor(eals[0], eals[1], regions, self.delta_min_wet)
        out["by_region"].pop(fdl.UNASSIGNED, None)
        out["n_wet"].pop(fdl.UNASSIGNED, None)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        logger.info(
            "ISIMIP change factor %s: national=%s centroids=%d", key, out["national"], len(lat)
        )
        return out

    def _check(self, res: dict[str, Any], fd: Any, paths: Any, offline: Any) -> dict[str, Any]:
        """The DEM edge-depth sub check; any failure is reported, never raised."""
        return dem_edge_check(
            res, fd, paths, offline, self.check_max, self.check_min_n, self.check_min_share
        )

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """Point overlay: the hazard is evaluated at each asset, not on a grid."""
        if hazard is None:
            return None
        return resolution_record(
            1.0,
            "arcsec",
            "asset point (official class polygon)",
            source="official depth-class polygons overlaid at each asset point (no grid)",
            measured_deg=None,
            dataset_family=self.dataset,
        )


class KoreaLocalCoastalFloodAdapter:
    """Coastal flood as a connected bathtub at the 약최고고조면, per asset (tier T2, hazard-only).

    Algorithm:
        $$d = \\max(0,\\ Z_{AHHW} + s - z_{ground})\\ \\text{if the 90 m cell is tidal, else } 0$$
        ASCII: depth = max(0, AHHW + slr - ground) when the asset's 90 m cell is sea-connected

        where $Z_{AHHW}$ (m above MSL) is the nearest KHOA station's M2+S2+K1+O1 amplitude
        sum, $s$ (m) the sea-level-rise increment and $z_{ground}$ (m above MSL) the asset
        ground: bare-earth NGII DEM, else Copernicus GLO-30. Connectivity is
        :func:`climaterisk_worker.coastal_connectivity.tidal_mask` on GLO-30 reduced to 3 arcsec
        by the block minimum (structures narrower than ~90 m are removed).

    The hazard is one event ("ahhw", present sea level) at the asset points; the +0.5 / +1.0 m
    increments are reported in ``diagnostics`` only. No surge, no waves, no loss computed. The
    sub check re-tests slr 0 on the block *maximum* (narrow structures kept) and flags assets
    that are connected only because those structures are assumed to fail.
    """

    model_id = ModelId.KOREA_LOCAL.value
    status = HAZARD_ONLY
    hazard_type = "CF"
    dataset = "khoa_ahhw_connected_bathtub"
    _KEYS = ("slr0", "slr0.5", "slr1")

    def __init__(self, scenario: str, year: int, iso3: str | None = "KOR") -> None:
        from climaterisk.physical_risk.metrics import load_config

        cfg = load_config().get("coastal_local", {})
        self.scenario, self.year, self.iso3 = scenario, int(year), iso3
        self.pad_km = float(cfg.get("pad_km", 20.0))
        self.slr = [float(v) for v in cfg.get("slr_increments_m", [0.0, 0.5, 1.0])]
        self.diagnostics: dict[str, Any] = {}

    def describe(self) -> HazardDescription:
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=(
                "약최고고조면 (KHOA 조화상수 M2+S2+K1+O1) + 연결성 침수 (connected bathtub): "
                "land below the nearest station's AHHW that is 8-connected to the sea on "
                "Copernicus GLO-30 reduced to ~90 m. 출처: 국립해양조사원, ESA Copernicus"
            ),
            hazard_dataset=self.dataset,
            hazard_data_version="KHOA 조화상수 2025-08-12; Copernicus GLO-30; SGIS 2025 2Q",
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario="historical",
            time_horizon="present",
            detail=(
                "method tier T2, hazard only. Depth = max(0, AHHW - ground) where the asset's "
                "90 m cell is sea-connected. Assumptions: coastal structures narrower than "
                "~90 m (seawalls, levees) are removed; nearest-station AHHW; present-day tidal "
                "datum; no storm surge and no waves; ground = bare-earth NGII DEM else GLO-30; "
                "no loss computed"
            ),
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        import math

        import numpy as np
        from climada.hazard import Hazard
        from scipy import sparse

        from climaterisk_worker import coastal_connectivity as cc
        from climaterisk_worker._params import offline
        from climaterisk_worker._paths import paths
        from climaterisk_worker.hazard_convert import _centroids

        if not points:
            raise ValueError("the Korea-local coastal adapter reads asset points; none were given")
        khoa = paths.external_dir("khoa", "tidebed_harmonics_20250812.csv")
        shp = paths.external_dir("municipality", "sgis", "bnd_sido_00_2025_2Q.shp")
        for need in (khoa, shp):
            if not need.exists():
                raise HazardUnavailable(f"coastal flood input not found: {need}")
        stations = cc.load_stations(khoa)
        lat = np.array([p[0] for p in points], dtype=float)
        lon = np.array([p[1] for p in points], dtype=float)
        pad_lat = self.pad_km / 111.32
        pad_lon = pad_lat / math.cos(math.radians(float(lat.mean())))
        try:
            mos = cc.build_mosaic(
                (
                    float(lon.min()) - pad_lon,
                    float(lat.min()) - pad_lat,
                    float(lon.max()) + pad_lon,
                    float(lat.max()) + pad_lat,
                ),
                allow_download=not offline(),
            )
        except cc.TerrainUnavailable as exc:
            raise HazardUnavailable(str(exc)) from exc
        sea = ~cc.land_mask(mos, shp) | np.isnan(mos.zmin)
        base = cc.station_threshold_grid(mos, stations)
        tidal = {
            f"slr{inc:g}": cc.tidal_mask(mos.zmin, base + np.float32(inc), sea) for inc in self.slr
        }
        tidal_max = cc.tidal_mask(mos.zmax, base, sea)  # sub check: structures kept
        ground, source = self._ground(lat, lon, paths, offline)
        rows: list[dict[str, Any]] = []
        for i in range(len(points)):
            st = cc.nearest_station(float(lat[i]), float(lon[i]), stations)
            cell = mos.cell(float(lat[i]), float(lon[i]))
            g = ground[i]
            depth: dict[str, float | None] = {}
            connected: bool | None = None
            for key in self._KEYS:
                inc = float(key[3:])
                if np.isnan(g) or cell is None or key not in tidal:
                    depth[key] = None
                    continue
                is_sea = bool(sea[cell])  # an asset on a sea cell is already at the water
                tid = bool(tidal[key][cell]) or is_sea
                if key == "slr0":
                    connected = tid
                depth[key] = max(0.0, st["ahhw_m"] + inc - float(g)) if tid else 0.0
            rows.append(
                {
                    "station": st["name"],
                    "ahhw_m": float(st["ahhw_m"]),
                    "ground_m": None if np.isnan(g) else float(g),
                    "ground_source": source[i],
                    "connected": connected,
                    "depth_m": depth,
                }
            )
        self.diagnostics = {
            "assets": rows,
            "sub_check": self._sub_check(rows, points, mos, tidal["slr0"], tidal_max, sea),
        }
        d0 = np.array(
            [[np.nan if r["depth_m"]["slr0"] is None else r["depth_m"]["slr0"] for r in rows]]
        )
        hazard = Hazard(
            haz_type="CF",
            units="m",
            centroids=_centroids(lat, lon),
            event_id=np.array([1]),
            event_name=["ahhw"],
            date=np.zeros(1, dtype=int),
            frequency=np.array([1.0]),
            frequency_unit="1/year",
            intensity=sparse.csr_matrix(d0),
            fraction=sparse.csr_matrix(np.nan_to_num(d0 > 0).astype(float)),
        )
        hazard.check()
        return hazard

    @staticmethod
    def _ground(lat: Any, lon: Any, paths: Any, offline: Any) -> tuple[Any, list[str | None]]:
        """Asset ground (m above MSL): bare-earth NGII where it covers the point, else GLO-30."""
        import numpy as np
        from pyproj import Transformer

        from climaterisk_worker import floodmap_depth as fd

        to_map = Transformer.from_crs(4326, fd.MAP_CRS, always_xy=True)
        xs, ys = to_map.transform(lon, lat)
        xy = np.c_[xs, ys]
        ground = np.full(len(xy), np.nan)
        source: list[str | None] = [None] * len(xy)
        bare = sorted(paths.external_dir("ngii_dem").glob("**/*.img"))
        bare_s = fd.RasterSampler(bare) if bare else None
        glo = fd.Glo30Sampler(paths.external_dir("copernicus_dem"), allow_download=not offline())
        try:
            for i in range(len(xy)):
                one = xy[i : i + 1]
                if bare_s is not None:
                    v = fd.StrictSampler(bare_s)(one)[0]
                    if np.isfinite(v):
                        ground[i], source[i] = v, "ngii_bare_earth_90m"
                        continue
                v = glo(one)[0]
                if np.isfinite(v):
                    ground[i], source[i] = v, "glo30_surface_model"
        finally:
            glo.close()
            if bare_s is not None:
                bare_s.close()
        return ground, source

    @staticmethod
    def _sub_check(
        rows: list[dict[str, Any]],
        points: list[tuple[float, float]],
        mos: Any,
        tidal_min: Any,
        tidal_max: Any,
        sea: Any,
    ) -> dict[str, Any]:
        """Diagnostic only: slr 0 repeated with narrow structures kept (block maximum)."""
        main = with_structures = narrow = 0
        for r, (la, lo) in zip(rows, points, strict=True):
            cell = mos.cell(float(la), float(lo))
            if cell is None or r["connected"] is None:
                continue
            on_min = bool(tidal_min[cell]) or bool(sea[cell])
            on_max = bool(tidal_max[cell]) or bool(sea[cell])
            main += on_min
            with_structures += on_max
            narrow += on_min and not on_max
        out: dict[str, Any] = {
            "method": "slr 0 connectivity on the 90 m block-maximum (narrow structures kept)",
            "connected_main": int(main),
            "connected_with_structures": int(with_structures),
            "narrow_only": int(narrow),
            "warn": narrow > 0,
        }
        if narrow > 0:
            out["reason"] = (
                f"{narrow} asset(s) depend on narrow defences: they are sea-connected only "
                "when structures narrower than ~90 m (seawalls, levees) are removed; the main "
                "result assumes those structures fail"
            )
        return out

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """Point overlay of a 3 arcsec (~90 m) connectivity grid."""
        if hazard is None:
            return None
        return resolution_record(
            3.0,
            "arcsec",
            "asset point (90 m connectivity cell)",
            source="GLO-30 block minimum at 3 arcsec; asset ground read at the point",
            measured_deg=None,
            dataset_family=self.dataset,
        )


class KoreaLocalPluvialAdapter:
    """Urban pluvial flood from the 환경부 100-y 도시침수지도 official class (tier T2, hazard-only).

    Main method: the deepest official class of the 도시침수지도 (CFM_SBSN_<basin>_100.zip)
    covering the asset, priced at the representative depth of its band (``flood_map`` config,
    as the river maps; the ``SEG_CODE`` N330–N334 ↔ band order is inferred from the river
    maps). One event, 100 y (frequency 0.01); an asset outside every polygon reads 0 m.
    Present day only: every requested scenario is served ``historical``. No loss is computed.

    Sub check (diagnostic): FwDET edge depth against the band, the river check
    (:func:`dem_edge_check`). Validation (diagnostic, no verdict): per asset, the years and the
    deepest depth of the 서울시 침수흔적도 2010–2025 within ``pluvial_local.trace_buffer_m``,
    for assets inside 서울특별시 only (:mod:`climaterisk_worker.flood_traces`).

    The polygons are overlaid in memory and never rasterised or written (공공누리 제4유형).
    """

    model_id = ModelId.KOREA_LOCAL.value
    status = HAZARD_ONLY
    hazard_type = "PF"
    dataset = "floodmap_cfm_depth_class"
    SIDO_SHP = "bnd_sido_00_2025_2Q.shp"
    SEOUL = "서울특별시"

    def __init__(self, scenario: str, year: int, iso3: str | None = "KOR") -> None:
        from climaterisk.physical_risk.metrics import load_config

        cfg = load_config()
        fm, pl = cfg.get("flood_map", {}), cfg.get("pluvial_local", {})
        self.scenario, self.year, self.iso3 = scenario, int(year), iso3
        self.mode = str(fm.get("depth_representative", "mid"))
        self.open_depth = float(fm.get("open_class_depth_m", 5.0))
        self.check_max = int(fm.get("check_max_points", 200))
        self.check_min_n = int(fm.get("check_min_points", 20))
        self.check_min_share = float(fm.get("check_min_share_in_band", 0.35))
        self.trace_buffer_m = float(pl.get("trace_buffer_m", 20.0))
        self.diagnostics: dict[str, Any] = {}

    def describe(self) -> HazardDescription:
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=(
                "환경부 홍수위험지도 도시침수지도 (유역별 100년 빈도, CFM_SBSN): official depth "
                f"class at each asset, priced at the band {self.mode} "
                f"(open ≥5 m class at {self.open_depth:g} m). 출처: 환경부 (공공누리 제4유형)"
            ),
            hazard_dataset=self.dataset,
            hazard_data_version="홍수위험지도 download 2026-02 (map files dated 2026-02-09)",
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario="historical",
            time_horizon="present",
            detail=(
                "method tier T2, hazard only: 도시침수 depth = official class band of the 100-y "
                "map (≤0.5 / 0.5–1 / 1–2 / 2–5 / ≥5 m; code order inferred from the river "
                "maps, not confirmed), one 100-y event, no other return period. Sub check: DEM "
                "edge depth vs the band, diagnostic only. Validation: 서울시 침수흔적도 "
                "2010–2025 observed floods within "
                f"{self.trace_buffer_m:g} m (Seoul assets only; diagnostic, not like-for-like "
                "with a 100-y map). Present-day map, no climate scenario; assets outside the "
                "mapped basins read 0 m; no loss computed"
            ),
        )

    #: Last overlay ``(key, hazard, diagnostics)``: the baseline run repeats the same assets.
    _memo: tuple[Any, Any, dict[str, Any]] | None = None

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        if not points:
            raise ValueError("the Korea-local pluvial adapter reads asset points; none were given")
        key = (
            tuple((float(a), float(b)) for a, b in points),
            self.mode,
            self.open_depth,
            self.trace_buffer_m,
        )
        memo = KoreaLocalPluvialAdapter._memo
        if memo is not None and memo[0] == key:
            self.diagnostics = copy.deepcopy(memo[2])
            return copy.deepcopy(memo[1])
        hazard = self._map_hazard(points)
        KoreaLocalPluvialAdapter._memo = (
            key,
            copy.deepcopy(hazard),
            copy.deepcopy(self.diagnostics),
        )
        return hazard

    def _map_hazard(self, points: list[tuple[float, float]]) -> Any:
        """The 100-y map hazard at the asset points; sets the diagnostics."""
        import numpy as np
        from climada.hazard import Hazard
        from scipy import sparse

        from climaterisk_worker import floodmap_depth as fd
        from climaterisk_worker._params import offline
        from climaterisk_worker._paths import paths
        from climaterisk_worker.hazard_convert import _centroids

        root = paths.floodmap_dir()
        if not root.is_dir():
            raise HazardUnavailable(f"홍수위험지도 not found under {root}")
        index = fd.map_index(
            root, paths.cache_dir("floodmap", "cfm_index.json"), families=fd.URBAN_FAMILIES
        )
        if not index:
            raise HazardUnavailable(f"도시침수지도 (CFM) files not found under {root}")
        lonlat = [(lon, lat) for lat, lon in points]
        res = fd.classes_at_points(
            lonlat, index, fd.ClassIndex(), named_files=fd.named_files(root, fd.URBAN_FAMILIES)
        )
        codes = [res["codes"][0]]  # the 100-y row
        dep = fd.class_depths(codes, self.mode, self.open_depth)
        depth = dep["depth"]
        self.diagnostics = {
            "classes": {"rp100": codes[0]},
            "per_point_reason": list(res["reason"]),  # aligned with ``points``
            "depth_lower_m": dep["lower"].tolist(),
            "depth_upper_m": [[None if np.isnan(v) else v for v in r] for r in dep["upper"]],
            "sub_check": dem_edge_check(
                res,
                fd,
                paths,
                offline,
                self.check_max,
                self.check_min_n,
                self.check_min_share,
            ),
        }
        self._validate(lonlat, depth[0] > 0, paths)
        lat = np.array([p[0] for p in points], dtype=float)
        lon = np.array([p[1] for p in points], dtype=float)
        hazard = Hazard(
            haz_type="PF",
            units="m",
            centroids=_centroids(lat, lon),
            event_id=np.array([1]),
            event_name=["rp100"],
            date=np.zeros(1, dtype=int),
            frequency=np.array([0.01]),
            frequency_unit="1/year",
            intensity=sparse.csr_matrix(depth),
            fraction=sparse.csr_matrix((depth > 0).astype(float)),
        )
        hazard.check()
        return hazard

    def _validate(self, lonlat: list[tuple[float, float]], wet: Any, paths: Any) -> None:
        """Observed 침수흔적도 per asset and the summary; a failure is recorded, never raised."""
        from climaterisk_worker import flood_traces as ft

        try:
            shp = paths.external_dir("municipality", "sgis", self.SIDO_SHP)
            root = paths.external_dir("seoul_flood_traces")
            for need in (shp, root):
                if not need.exists():
                    raise FileNotFoundError(f"not found: {need}")
            coverage = ft.region_coverage(shp, self.SEOUL)
            observed = ft.observed_at_points(lonlat, root, coverage, self.trace_buffer_m)
            self.diagnostics["observed"] = observed
            self.diagnostics["validation"] = ft.validation_summary(wet, observed)
        except Exception as exc:
            logger.warning("pluvial validation skipped: %s: %s", type(exc).__name__, exc)
            self.diagnostics["observed"] = None
            self.diagnostics["validation"] = {
                "error": f"{type(exc).__name__}: {exc}",
                "note": "diagnostic only: the 서울시 침수흔적도 comparison could not be made",
            }

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """Point overlay: the hazard is evaluated at each asset, not on a grid."""
        if hazard is None:
            return None
        return resolution_record(
            1.0,
            "arcsec",
            "asset point (official class polygon)",
            source="official 도시침수지도 depth-class polygons overlaid at each asset point",
            measured_deg=None,
            dataset_family=self.dataset,
        )


class _NoPluvialAdapter:
    """Urban pluvial flood under GLOBAL_BASELINE / DATA_API_COUNTRY: no such hazard exists."""

    status = NO_HAZARD_DATA
    hazard_type = "PF"

    def __init__(self, model_id: str, scenario: str | None = None) -> None:
        self.model_id = model_id
        self.scenario = scenario

    def describe(self) -> HazardDescription:
        why = "no pluvial hazard in the CLIMADA Data API / global reference"
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=f"none — {why}",
            hazard_dataset=None,
            hazard_data_version=None,
            hazard_country=None,
            requested_scenario=self.scenario,
            served_scenario=None,
            time_horizon=None,
            detail=why,
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> None:
        return None

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        return None


class KoreaLocalTCAdapter(_NotImplementedAdapter):
    hazard_type = "TC"
    source = "국내 공식 태풍 자료 (adapter interface only)"
    reason = "no domestic tropical-cyclone track/wind dataset connected"


class _NoHeatDataAdapter:
    """Heat for the two Data API models: the source has no such hazard type."""

    status = NO_HAZARD_DATA

    def __init__(self, hazard_type: str, model_id: str, scenario: str | None = None) -> None:
        self.hazard_type, self.model_id, self.scenario = hazard_type, model_id, scenario

    def describe(self) -> HazardDescription:
        why = (
            "the CLIMADA Data API has no heat/heatwave hazard type"
            if self.model_id == ModelId.DATA_API_COUNTRY.value
            else "the platform has no global heat hazard layer"
        )
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=f"none — {why}",
            hazard_dataset=None,
            hazard_data_version=None,
            hazard_country=None,
            requested_scenario=self.scenario,
            served_scenario=None,
            time_horizon=None,
            detail=why,
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> None:
        return None

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        return None


class KoreaLocalUtciAdapter:
    """Heat (HW) as open-field UTCI days per year, per asset (tier T2, hazard-only).

    Algorithm:
        $$D_{38} = \\frac{1}{|Y|}\\sum_{y\\in Y}\\big|\\{d\\in y: U^{max}_d \\ge 38\\}\\big|$$
        ASCII: D38 = mean over years of count(days whose max hourly UTCI >= 38 degC)

        $U$ hourly UTCI (degC) of a person standing in the open, from KMA ASOS hourly
        observations at the nearest station with the air temperature shifted by the 0.05 deg
        MK-PRISM daily-maximum difference; $Y$ = 2000-2019 May-Sep. See
        :mod:`climaterisk_worker.heat_utci`.

    One event (the 20-year mean) at the asset points, intensity in days/yr; missing data is
    NaN, never 0. The sub check (agreement with the heat-day count) is diagnostic only.
    """

    model_id = ModelId.KOREA_LOCAL.value
    status = HAZARD_ONLY
    hazard_type = "HW"
    dataset = "kma_asos_open_field_utci"

    def __init__(self, scenario: str, year: int, iso3: str | None = "KOR") -> None:
        self.scenario, self.year, self.iso3 = scenario, int(year), iso3
        self.diagnostics: dict[str, Any] = {}

    def _window(self) -> dict[str, Any]:
        """Served scenario and window for the request (``heat_utci.future``, design 4.1)."""
        from climaterisk.physical_risk.metrics import load_config
        from climaterisk_worker import heat_utci as hu

        return hu.scenario_window(self.scenario, self.year, load_config()["heat_utci"]["future"])

    def describe(self) -> HazardDescription:
        win = self._window()
        served = str(win["served_scenario"])
        future = served != "historical"
        if future:
            first, last = win["window"]
            horizon = f"{first}-{last} May-Sep (delta on 2000-2019 observations)"
            tail = (
                f"Future served: {served} {first}-{last}, air temperature = 2000-2019 hourly "
                "observations + the monthly change of the KMA AR6 5ENSMN daily maximum "
                "(mean 2051-2060 minus mean 2021-2030) at the asset cell; A7 humidity, wind, "
                "radiation and the ground fit held at present values. The warming between "
                "2000-2019 and 2021-2030 is not in the change: direction and size reference"
            )
        else:
            horizon = "2000-2019 May-Sep"
            why = win["reason"] or "historical period requested"
            tail = f"Only the historical period is served for this request ({why})"
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status,
            hazard_source=(
                "개활지 UTCI: 기상청 ASOS 시간자료(기온·습도·풍속·일사) + 기상청 남한상세 "
                "MK-PRISM v3.1 일 최고기온(0.05° 블록 평균). 출처: 기상청"
            ),
            hazard_dataset=self.dataset,
            hazard_data_version=(
                "ASOS 1999-2023 (local copy), MK-PRISM v3.1 2000-2019, pythermalcomfort 4.8.0"
            ),
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario=served,
            time_horizon=horizon,
            detail=(
                "method tier T2, hazard only: days per year with daily-max UTCI >= 38 degC for a "
                "person in the open (no shade, buildings or indoors; no loss computed). "
                "Assumptions: A1 grid daily-max difference added to every hour; A2 equal vapour "
                "pressure at asset and station; A3 irradiance is the preceding-hour mean; "
                "A4 night cloud fraction = day mean; A5 ground emissivity 0.95; A6 ground "
                "albedo 0.20 (0.15-0.25 range reported). "
                + tail
                + self._approval()
                + self._caveat()
            ),
        )

    @staticmethod
    def _caveat() -> str:
        """The reading limit (``heat_utci.risk_caveat``): same UTCI rise != same regional risk."""
        from climaterisk.physical_risk.metrics import load_config

        text = load_config()["heat_utci"].get("risk_caveat")
        return f". {text}" if text else ""

    @staticmethod
    def _approval() -> str:
        """The validation record (승인) from ``heat_utci.validation.result``, if any."""
        from climaterisk.physical_risk.metrics import load_config

        res = load_config()["heat_utci"].get("validation", {}).get("result")
        if not res:
            return ". Supplementary result: none"
        v1, v2, v3 = res["V1"], res["V2"], res["V3"]
        return (
            f". Supplementary result ({res['run_date']}, not a validation): {res['label']}; "
            f"KDCA heat-illness cases "
            f"2011-2019 May-Sep, Spearman V1 daily national UTCI {v1['rho_u']:.2f} vs air "
            f"temperature {v1['rho_t']:.2f}, V2 daily by 시도 median {v2['median_u']:.2f} vs "
            f"{v2['median_t']:.2f}, V3 spatial {v3['rho_u']:.2f} vs T95 {v3['rho_t95']:.2f} / "
            f"heat days {v3['rho_h']:.2f}"
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        from climaterisk.physical_risk.metrics import load_config
        from climaterisk_worker import heat_utci as hu

        if not points:
            raise ValueError("the Korea-local UTCI adapter reads asset points; none were given")
        cfg = load_config()["heat_utci"]
        win = hu.scenario_window(self.scenario, self.year, cfg["future"])
        future = None if win["served_scenario"] == "historical" else str(win["served_scenario"])
        try:
            out = hu.open_field_utci(points, cfg, future=future)
        except (FileNotFoundError, OSError) as exc:
            raise HazardUnavailable(f"open-field UTCI input not found: {exc}") from exc
        # per_asset keeps both the present and the future values of every asset
        self.diagnostics = {"per_asset": out["per_asset"], "sub_check": out["sub_check"]}
        self.record = out["record"]
        key = "utci_days_ge38" if future is None else "utci_days_ge38_future"
        return hu.build_hazard(points, [a[key] for a in out["per_asset"]])

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """The 0.05 deg grid that carries the temperature shift."""
        if hazard is None:
            return None
        return resolution_record(
            0.05,
            "degree",
            "grid + nearest station",
            source=(
                "air temperature shifted on the 0.05 deg MK-PRISM block grid; humidity, wind "
                "and radiation from the nearest ASOS station (one point)"
            ),
            measured_deg=None,
            dataset_family=self.dataset,
        )


class KoreaLocalHeatAdapter:
    """KMA 남한상세 heat via the local catalog's KOR ``heatwave`` / ``heat_mortality`` layers.

    The only Korea-local source that exists today. Hazard-only: CLIMADA ships no heat
    impact function, so the engine reports intensity and null financial fields.
    """

    model_id = ModelId.KOREA_LOCAL.value
    status = HAZARD_ONLY
    _PERIL = {"HW": "heatwave", "HM": "heat_mortality"}

    def __init__(self, hazard_type: str, scenario: str, year: int, iso3: str = "KOR") -> None:
        if hazard_type not in self._PERIL:
            raise ValueError("KoreaLocalHeatAdapter handles HW and HM")
        self.hazard_type, self.scenario, self.year, self.iso3 = (
            hazard_type,
            scenario,
            int(year),
            iso3,
        )
        self._entry: dict[str, Any] | None = None

    def _lookup(self) -> dict[str, Any] | None:
        from climaterisk_worker import catalog

        if self._entry is None:
            self._entry = catalog.lookup(
                self._PERIL[self.hazard_type], self.scenario, self.iso3, self.year
            )
        return self._entry

    def _scenarios_available(self) -> list[str]:
        from climaterisk_worker import catalog

        peril = self._PERIL[self.hazard_type]
        return sorted(
            {
                str(e["climate_scenario"])
                for e in catalog.load_manifest()
                if e.get("peril") == peril and e.get("region") == self.iso3
            }
        )

    def describe(self) -> HazardDescription:
        entry = self._lookup()
        if entry is None:
            have = self._scenarios_available()
            why = (
                f"no KOR {self._PERIL[self.hazard_type]} layer for scenario "
                f"{self.scenario!r} in the local catalog"
                + (f" (available: {', '.join(have)})" if have else " (no KOR heat layers at all)")
                + " — KMA 남한상세 files for that scenario have not been registered"
            )
        else:
            why = "hazard intensity only — no CLIMADA impact function exists for heat"
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status if entry else NO_HAZARD_DATA,
            hazard_source="KMA 남한상세 via local catalog"
            if entry
            else "none — no KOR heat layer for this scenario in the catalog",
            hazard_dataset=str(entry["file"]) if entry else None,
            hazard_data_version=str(entry.get("source")) if entry else None,
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario=str(entry["climate_scenario"]) if entry else None,
            time_horizon=str(entry.get("year")) if entry else None,
            detail=why,
        )

    def load(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        points: list[tuple[float, float]] | None = None,
    ) -> Any:
        from climaterisk_worker import catalog

        return catalog.load_hazard(
            self._PERIL[self.hazard_type], self.scenario, self.iso3, self.year
        )

    def spatial_resolution(self, hazard: Any) -> dict[str, Any] | None:
        """The catalog layer's grid step, measured on its centroids (0.05° today).

        The KMA source grids are finer (observations 0.005°, SSP 0.01°,
        ``kma_scenario.GRID_RES_DEG``); the layer is block-averaged when it is built, so the
        row reports the grid it was actually read from, not the source's.
        """
        if hazard is None:
            return None
        measured = measure_grid_spacing(hazard)
        if measured is None:
            return None
        entry = self._lookup() or {}
        return resolution_record(
            round(measured, 6),
            "degree",
            "grid",
            source="catalog layer centroid spacing (measured on load)",
            measured_deg=measured,
            dataset_family=str(entry.get("file") or self._PERIL[self.hazard_type]),
        )


# --------------------------------------------------------------------------- #
# Factory + consistency with the backend readiness table                        #
# --------------------------------------------------------------------------- #
def adapter_for(hazard_type: str, model_id: str, scenario: str, year: int, iso3: str) -> Any:
    """The adapter for a (hazard tag x model), or a ``NO_HAZARD_DATA`` adapter for heat."""
    g, c, k = (
        ModelId.GLOBAL_BASELINE.value,
        ModelId.DATA_API_COUNTRY.value,
        ModelId.KOREA_LOCAL.value,
    )
    if hazard_type == "RF":
        table = {g: GlobalFloodAdapter, c: CountryFloodAdapter, k: KoreaLocalFloodAdapter}
    elif hazard_type == "TC":
        table = {g: GlobalTCAdapter, c: CountryTCAdapter, k: KoreaLocalTCAdapter}
    elif hazard_type == "PF":
        if model_id == k:
            return KoreaLocalPluvialAdapter(scenario, year, iso3)
        return _NoPluvialAdapter(model_id, scenario)
    elif hazard_type == "CF":
        if model_id == g:
            return GlobalCoastalFloodAdapter(scenario, year, iso3)
        if model_id == c:
            return _NoCoastalDataApiAdapter(scenario)
        return KoreaLocalCoastalFloodAdapter(scenario, year, iso3)
    elif hazard_type in ("HW", "HM"):
        if model_id == k:
            if hazard_type == "HW":
                from climaterisk.physical_risk.metrics import load_config

                if load_config().get("heat_utci", {}).get("enabled"):
                    return KoreaLocalUtciAdapter(scenario, year, iso3)
            return KoreaLocalHeatAdapter(hazard_type, scenario, year, iso3)
        return _NoHeatDataAdapter(hazard_type, model_id, scenario)
    else:
        raise ValueError(f"no adapters for hazard type {hazard_type!r}")
    klass = table[model_id]
    if model_id == k:
        return klass(scenario, year, iso3)
    return klass(scenario, year, iso3)


def adapter_statuses() -> dict[str, dict[str, str]]:
    """Static status of every adapter, in the readiness table's shape — for the cross-check."""
    out: dict[str, dict[str, str]] = {"RF": {}, "PF": {}, "CF": {}, "TC": {}, "HEAT": {}}
    for haz, key in (("RF", "RF"), ("PF", "PF"), ("CF", "CF"), ("TC", "TC"), ("HW", "HEAT")):
        for model in (m.value for m in ModelId):
            out[key][model] = adapter_for(haz, model, "rcp45", 2050, "KOR").status
    return out


def readiness_matches_backend() -> bool:
    """True when every adapter's static status equals the backend readiness cell."""
    mine = adapter_statuses()
    return all(
        mine[key][model] == cell["status"]
        for key, cells in READINESS.items()
        for model, cell in cells.items()
    )
