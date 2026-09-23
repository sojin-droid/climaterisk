"""Hazard adapters — where each model's hazard comes from, kept apart from the pricing.

::

    HazardAdapter.load()  ->  canonical CLIMADA Hazard  ->  engine.calculate (ImpactCalc)

One adapter per (hazard x model). The three models share exposure and impact function;
an adapter only answers *which dataset* and *what it actually is*:

* ``GLOBAL_BASELINE`` — CLIMADA Data API, ``spatial_coverage=global``.
* ``DATA_API_COUNTRY`` — CLIMADA Data API, ``spatial_coverage=country`` for one ISO3.
  The same product cropped to a country: a **country-specific Data API hazard**, not
  Korea-local source data.
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

    def load(self, bbox: tuple[float, float, float, float] | None = None) -> Any: ...


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

    def load(self, bbox: tuple[float, float, float, float] | None = None) -> Any:
        from climada.util.api_client import Client

        from climaterisk_worker._dataapi import resilient_get_hazard

        client = Client()
        info = client.get_dataset_info(name=self.dataset)
        self._version = str(info.version)
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

    def load(self, bbox: tuple[float, float, float, float] | None = None) -> None:
        """Always None. Never raises, never fabricates, never downloads."""
        return None


class KoreaLocalFloodAdapter(_NotImplementedAdapter):
    hazard_type = "RF"
    source = "환경부 홍수위험지도 (adapter interface only)"
    reason = (
        "no domestic flood dataset connected: 공공누리 제4유형 licence and access unresolved; "
        "the map is not downloaded, copied or computed until they are"
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

    def load(self, bbox: tuple[float, float, float, float] | None = None) -> None:
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

    def describe(self) -> HazardDescription:
        entry = self._lookup()
        return HazardDescription(
            hazard_type=self.hazard_type,
            model_id=self.model_id,
            status=self.status if entry else NO_HAZARD_DATA,
            hazard_source="KMA 남한상세 via local catalog"
            if entry
            else "none — no KOR heat layer in the catalog",
            hazard_dataset=str(entry["file"]) if entry else None,
            hazard_data_version=str(entry.get("source")) if entry else None,
            hazard_country=self.iso3,
            requested_scenario=self.scenario,
            served_scenario=str(entry["climate_scenario"]) if entry else None,
            time_horizon=str(entry.get("year")) if entry else None,
            detail="hazard intensity only — no CLIMADA impact function exists for heat",
        )

    def load(self, bbox: tuple[float, float, float, float] | None = None) -> Any:
        from climaterisk_worker import catalog

        return catalog.load_hazard(
            self._PERIL[self.hazard_type], self.scenario, self.iso3, self.year
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
    out: dict[str, dict[str, str]] = {"RF": {}, "TC": {}, "HEAT": {}}
    for haz, key in (("RF", "RF"), ("TC", "TC"), ("HW", "HEAT")):
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
