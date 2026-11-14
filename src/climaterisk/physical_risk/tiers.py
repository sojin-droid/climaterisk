"""Method tiers: *what* a peril result was computed from (T1 / T2 / T3).

Two axes are kept apart on purpose:

* **Method tier** (this module, ``docs/TIERS.md``) — the level of the inputs, modelled on
  the IPCC inventory tiers: T1 = global default data and curves, T2 = Korean data and
  standards, T3 = a physics simulation with Korean parameters.
* **Use tier** (``docs/SCOPE.md``) — what a number may be used for (S / I / D).

A higher method tier never raises the use tier by itself: a T3 run that has not passed a
domestic validation stays where SCOPE puts it.

A tier is reached only when **every** required input of that tier is available;
otherwise :func:`resolve_tier` falls back one tier at a time and says which inputs were
missing. T1 needs nothing beyond what the engine already fetches, so it is always reachable.
No I/O here: availability is passed in by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MethodTier(StrEnum):
    """Input level of a result, lowest first."""

    T1 = "T1"  #: global default data and curves (CLIMADA Data API, Aqueduct, ISIMIP)
    T2 = "T2"  #: Korean data and standards (KMA 1 km, 공식 홍수지도, KHOA 조위, KDS)
    T3 = "T3"  #: physics simulation with Korean parameters (SOLWEIG, SFINCS, LISFLOOD-FP …)


_ORDER = [MethodTier.T1, MethodTier.T2, MethodTier.T3]

#: Status of an input, as in the data inventory ("데이터 목록" sheet).
HELD = "held"  # on disk under DATA_ROOT
KEY = "key"  # reachable now with a key already in .env
APPLY = "apply"  # needs a data.go.kr / portal application by the user
RESTRICTED = "restricted"  # security or MOU data; not expected
NONE = "none"  # no source exists yet (must be built or digitised)
STATUSES = (HELD, KEY, APPLY, RESTRICTED, NONE)


@dataclass(frozen=True)
class TierInput:
    """One input a tier needs. ``inventory_id`` points at the data inventory row."""

    key: str
    label: str
    inventory_id: str | None
    status: str


@dataclass(frozen=True)
class PerilTiers:
    """Current tier (what the engine runs today), target tier, and inputs per tier."""

    current: MethodTier
    target: MethodTier
    inputs: dict[MethodTier, tuple[TierInput, ...]] = field(default_factory=dict)


def _i(key: str, label: str, inv: str | None, status: str) -> TierInput:
    return TierInput(key, label, inv, status)


# Shared inputs.
_ASOS = _i("asos_hourly", "기상청 ASOS 시간자료", "HT-11", KEY)
_DEM_GLO30 = _i("dem_glo30", "Copernicus DEM GLO-30", "PF-07", HELD)
_FLOODMAP = _i("floodmap_polygons", "환경부 홍수위험지도 원문 폴리곤 (겹쳐 보기만)", "RF-03", HELD)
_BUILDINGS_3D = _i("building_heights", "건물 높이 (GIS건물통합정보)", "CO-07", APPLY)

#: Every peril the worker can run. ``current`` must match what the runner actually does.
PERIL_TIERS: dict[str, PerilTiers] = {
    "river_flood": PerilTiers(
        MethodTier.T1,
        MethodTier.T3,
        {
            MethodTier.T2: (_FLOODMAP,),
            MethodTier.T3: (
                _i("hrfco_discharge", "홍수통제소 하천 유량", None, KEY),
                _i("dem_5m", "국토지리정보원 5 m DEM", "PF-05", APPLY),
                _i("levee_crest", "하천 단면·제방고 (하천기본계획)", "RF-04", APPLY),
            ),
        },
    ),
    "coastal_flood": PerilTiers(
        MethodTier.T1,
        MethodTier.T3,
        {
            MethodTier.T2: (
                _i("khoa_harmonics", "KHOA 조화상수 (약최고고조면)", None, HELD),
                _DEM_GLO30,
            ),
            MethodTier.T3: (
                _i("khoa_harmonics", "KHOA 조화상수 (약최고고조면)", None, HELD),
                _i("bathymetry_fine", "상세 연안 수심", None, APPLY),
                _i("seawall_inventory", "방조제·항만 외곽시설 위치와 설계빈도", "CF-03", NONE),
            ),
        },
    ),
    "tc_surge": PerilTiers(
        MethodTier.T1,
        MethodTier.T3,
        {
            MethodTier.T3: (
                _i("tc_tracks", "IBTrACS / RSMC Tokyo 태풍 경로", "TC-02", HELD),
                _i("bathymetry_fine", "상세 연안 수심", None, APPLY),
                _i("khoa_tide_gauges", "KHOA 조위관측 (검증)", None, KEY),
            ),
        },
    ),
    "tropical_cyclone": PerilTiers(
        MethodTier.T1,
        MethodTier.T2,
        {
            MethodTier.T2: (
                _i("kds_basic_wind", "KDS 41 기본풍속 지도 (디지털화)", None, NONE),
                _ASOS,
            ),
        },
    ),
    "tc_rain": PerilTiers(MethodTier.T1, MethodTier.T1),
    "heatwave": PerilTiers(
        MethodTier.T2,
        MethodTier.T3,
        {
            MethodTier.T2: (
                _i("kma_1km_tmax", "기상청 남한상세 1 km TAMAX", "HT-01", HELD),
                _ASOS,
            ),
            MethodTier.T3: (_BUILDINGS_3D, _ASOS, _DEM_GLO30),
        },
    ),
    "heat_mortality": PerilTiers(
        MethodTier.T2,
        MethodTier.T2,
        {
            MethodTier.T2: (
                _i("kma_1km_ta", "기상청 남한상세 1 km TA", "HT-01", HELD),
                _i("kosis_mortality", "KOSIS 연령별 사망률·인구", "HT-06", HELD),
            ),
        },
    ),
    "drought": PerilTiers(
        MethodTier.T1,
        MethodTier.T2,
        {
            MethodTier.T2: (
                _i("terraclimate", "TerraClimate 강수·증발산 (SPEI)", "DR-01", HELD),
                _i("reservoir_levels", "농촌용수 저수지 수위", "DR-04", APPLY),
            ),
        },
    ),
    "crop_yield": PerilTiers(
        MethodTier.T1,
        MethodTier.T3,
        {
            MethodTier.T3: (
                _i("kr_cultivar_coeffs", "한국 품종 계수 (DSSAT·WOFOST)", None, HELD),
                _i("kosis_rice_yield", "KOSIS 시군 논벼 수량 (검증)", "DR-08", HELD),
                _i("asos_daily", "기상청 ASOS 일자료", "HT-08", APPLY),
            ),
        },
    ),
    "low_flow": PerilTiers(MethodTier.T1, MethodTier.T1),
    "wildfire": PerilTiers(MethodTier.T1, MethodTier.T1),
    "earthquake": PerilTiers(MethodTier.T1, MethodTier.T1),
    "landslide": PerilTiers(MethodTier.T1, MethodTier.T1),
    "hail": PerilTiers(MethodTier.T1, MethodTier.T1),
    "european_windstorm": PerilTiers(MethodTier.T1, MethodTier.T1),
}


#: Perils whose current T2 rests on Korean sources (KMA 1 km, KOSIS); elsewhere the same
#: runner reads E-OBS / global layers, which is T1.
KOREA_SOURCED = frozenset({"heatwave", "heat_mortality"})


def method_tier_for(peril: str, region: str | None = "KOR") -> str | None:
    """The tier the engine runs today for ``peril`` in ``region``, or None if unknown.

    Args:
        peril: Runner key.
        region: ISO3 the run resolved to (None or "global" when unknown).

    Returns:
        ``"T1"`` / ``"T2"`` / ``"T3"``, or None for a peril not in :data:`PERIL_TIERS`.
    """
    entry = PERIL_TIERS.get(peril)
    if entry is None:
        return None
    if peril in KOREA_SOURCED and region != "KOR":
        return MethodTier.T1.value
    return entry.current.value


def missing_inputs(peril: str, tier: MethodTier, available: set[str]) -> list[str]:
    """Labels of the inputs ``tier`` needs for ``peril`` that are not in ``available``."""
    needed = PERIL_TIERS[peril].inputs.get(tier, ())
    return [i.label for i in needed if i.key not in available]


def resolve_tier(
    peril: str, requested: MethodTier, available: set[str]
) -> tuple[MethodTier, str | None]:
    """Highest tier ≤ ``requested`` whose inputs are all available, and why it fell back.

    Algorithm:
        $$t^* = \\max\\{t \\le t_{req} : \\text{inputs}(t) \\subseteq A\\}$$
        ASCII: walk down from the requested tier; stop at the first tier whose inputs
        are all in the available set A. T1 has no extra inputs, so it always qualifies.

    Args:
        peril: Runner key (e.g. ``"river_flood"``).
        requested: The tier asked for.
        available: Input keys that are present for this run.

    Returns:
        ``(tier, reason)``; ``reason`` is None when the requested tier was reached.

    Raises:
        KeyError: ``peril`` is not in :data:`PERIL_TIERS`.
    """
    entry = PERIL_TIERS[peril]
    start = min(_ORDER.index(requested), _ORDER.index(entry.target))
    notes: list[str] = []
    if _ORDER.index(requested) > start:
        notes.append(f"{requested.value} is above the target tier {entry.target.value}")
    for t in reversed(_ORDER[: start + 1]):
        gap = missing_inputs(peril, t, available)
        if not gap:
            return t, ("; ".join(notes) or None)
        notes.append(f"{t.value} needs: {', '.join(gap)}")
    return MethodTier.T1, "; ".join(notes)  # pragma: no cover - T1 has no inputs
