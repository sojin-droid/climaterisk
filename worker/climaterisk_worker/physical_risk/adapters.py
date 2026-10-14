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

        from climaterisk_worker._dataapi import resilient_get_hazard

        client = Client()
        info = client.get_dataset_info(name=self.dataset)
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


class KoreaLocalFloodAdapter(_NotImplementedAdapter):
    hazard_type = "RF"
    source = "환경부 홍수위험지도 (adapter interface only)"
    reason = (
        "no domestic flood dataset connected: the 환경부 홍수위험지도 is 공공누리 제4유형 "
        "(출처표시 + 상업적 이용금지 + 변경금지; confirmed 2026-09-23), which does not permit "
        "the grid conversion or commercial use without separate permission — the map is not "
        "downloaded, copied or computed until it is granted"
    )


class KoreaLocalCoastalFloodAdapter(_NotImplementedAdapter):
    hazard_type = "CF"
    source = "국립해양조사원 해안침수예상도 (adapter interface only)"
    reason = (
        "no domestic coastal-inundation dataset connected; the licence and access of the "
        "국립해양조사원 해안침수예상도 have not been investigated"
    )


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
    elif hazard_type == "CF":
        if model_id == g:
            return GlobalCoastalFloodAdapter(scenario, year, iso3)
        if model_id == c:
            return _NoCoastalDataApiAdapter(scenario)
        return KoreaLocalCoastalFloodAdapter(scenario, year, iso3)
    elif hazard_type in ("HW", "HM"):
        if model_id == k:
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
    out: dict[str, dict[str, str]] = {"RF": {}, "CF": {}, "TC": {}, "HEAT": {}}
    for haz, key in (("RF", "RF"), ("CF", "CF"), ("TC", "TC"), ("HW", "HEAT")):
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
