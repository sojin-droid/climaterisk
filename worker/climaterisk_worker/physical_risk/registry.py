"""Impact-function registry — what CLIMADA actually ships, read off the objects themselves.

Every entry here is produced by **instantiating** a CLIMADA or CLIMADA-Petals impact
function and reading its attributes. Nothing is transcribed from documentation, and no
curve is authored in this repository: the point of the registry is to make "which published
function did this number come from" answerable from the result row.

What the installed environment provides (climada 6.1.0 / climada_petals 6.2.0, verified
2026-09-22):

* ``RF`` — :class:`climada_petals.entity.impact_funcs.river_flood.ImpfRiverFlood`, JRC
  depth-damage curves (Huizinga et al. 2017), six continents x six sectors. Note the hazard
  tag is ``RF``, not ``FL``.
* ``TC`` — :class:`climada.entity.impact_funcs.trop_cyclone.ImpfTropCyclone` (Emanuel 2011)
  and the ten regionally calibrated functions of
  :class:`~climada.entity.impact_funcs.trop_cyclone.ImpfSetTropCyclone` (Eberenz et al. 2021).
* **Heat / heatwave — nothing.** ``climada.entity.impact_funcs`` contains only ``base``,
  ``impact_func_set``, ``storm_europe`` and ``trop_cyclone``; petals adds ``drought``,
  ``relative_cropyield``, ``river_flood`` and ``wildfire``. :func:`heat_status` reports that
  absence as a first-class answer so the engine can mark heat rows ``NO_IMPACT_FUNCTION``
  instead of inventing a curve.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import asdict, dataclass
from typing import Any

#: Sectors ``ImpfRiverFlood.from_jrc_region_sector`` accepts.
JRC_SECTORS: tuple[str, ...] = (
    "residential",
    "commercial",
    "industrial",
    "transport",
    "infrastructure",
    "agriculture",
)

#: Continents it accepts.
JRC_REGIONS: tuple[str, ...] = (
    "Africa",
    "Asia",
    "Europe",
    "North America",
    "Oceania",
    "South America",
)

_JRC_REFERENCE = (
    "Huizinga, J., de Moel, H., Szewczyk, W. (2017) Global flood depth-damage functions: "
    "methodology and the database with guidelines, JRC105688"
)
_EBERENZ_REFERENCE = (
    "Eberenz, S., Lüthi, S., Bresch, D.N. (2021) Regional tropical cyclone impact functions "
    "for globally consistent risk assessments, NHESS 21:393-415"
)
_EMANUEL_REFERENCE = (
    "Emanuel, K. (2011) Global warming effects on U.S. hurricane damage, "
    "Weather, Climate and Society 3:261-268"
)


@dataclass(frozen=True)
class ImpactFunctionEntry:
    """One published impact function, described by the object itself."""

    hazard_type: str
    impact_function_id: int
    impact_function_name: str
    impact_function_class: str
    source: str
    reference: str
    intensity_unit: str
    intensity_min: float
    intensity_max: float
    n_points: int
    mdd_min: float
    mdd_max: float
    paa_min: float
    paa_max: float
    sector: str | None
    region: str | None
    version: str
    status: str = "AVAILABLE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _versions() -> tuple[str, str | None]:
    import importlib.metadata as md

    core = md.version("climada")
    try:
        petals: str | None = md.version("climada_petals")
    except Exception:  # pragma: no cover - petals is a hard dependency of this worker
        petals = None
    return core, petals


def _describe(
    func: Any,
    *,
    source: str,
    reference: str,
    sector: str | None,
    region: str | None,
    klass: str,
    version: str,
) -> ImpactFunctionEntry:
    """Read a CLIMADA ``ImpactFunc`` into an entry — attributes only, nothing assumed."""
    import numpy as np

    intensity = np.asarray(func.intensity, dtype=float)
    mdd = np.asarray(func.mdd, dtype=float)
    paa = np.asarray(func.paa, dtype=float)
    return ImpactFunctionEntry(
        hazard_type=str(func.haz_type),
        impact_function_id=int(func.id),
        impact_function_name=str(func.name),
        impact_function_class=klass,
        source=source,
        reference=reference,
        intensity_unit=str(func.intensity_unit),
        intensity_min=float(intensity.min()),
        intensity_max=float(intensity.max()),
        n_points=int(intensity.size),
        mdd_min=float(mdd.min()),
        mdd_max=float(mdd.max()),
        paa_min=float(paa.min()),
        paa_max=float(paa.max()),
        sector=sector,
        region=region,
        version=version,
    )


def flood_entries(region: str = "Asia") -> list[ImpactFunctionEntry]:
    """Every JRC sector curve that actually instantiates for ``region``."""
    from climada_petals.entity.impact_funcs.river_flood import ImpfRiverFlood

    _core, petals = _versions()
    out: list[ImpactFunctionEntry] = []
    for sector in JRC_SECTORS:
        try:
            func = ImpfRiverFlood.from_jrc_region_sector(region, sector)
        except Exception:  # pragma: no cover - a sector the installed version drops
            continue
        out.append(
            _describe(
                func,
                source="CLIMADA Petals (climada_petals.entity.impact_funcs.river_flood)",
                reference=_JRC_REFERENCE,
                sector=sector,
                region=region,
                klass="ImpfRiverFlood",
                version=f"climada_petals {petals}",
            )
        )
    return out


def tc_entries() -> list[ImpactFunctionEntry]:
    """The ten regionally calibrated TC functions plus the Emanuel default."""
    from climada.entity.impact_funcs.trop_cyclone import ImpfSetTropCyclone, ImpfTropCyclone

    core, _petals = _versions()
    out = [
        _describe(
            ImpfTropCyclone.from_emanuel_usa(),
            source="CLIMADA core (climada.entity.impact_funcs.trop_cyclone)",
            reference=_EMANUEL_REFERENCE,
            sector=None,
            region="USA (generic)",
            klass="ImpfTropCyclone.from_emanuel_usa",
            version=f"climada {core}",
        )
    ]
    impf_set = ImpfSetTropCyclone.from_calibrated_regional_ImpfSet()
    v_half = ImpfSetTropCyclone.calibrated_regional_vhalf()
    by_id = {}
    for code, value in v_half.items():
        by_id.setdefault(code, value)
    for fid in impf_set.get_ids().get("TC", []):
        func = impf_set.get_func(haz_type="TC", fun_id=fid)
        func = func[0] if isinstance(func, list) else func
        out.append(
            _describe(
                func,
                source="CLIMADA core (ImpfSetTropCyclone.from_calibrated_regional_ImpfSet)",
                reference=_EBERENZ_REFERENCE,
                sector=None,
                region=str(func.name),
                klass="ImpfSetTropCyclone",
                version=f"climada {core}",
            )
        )
    return out


def tc_region_for_country(iso3: str) -> tuple[int, str, str] | None:
    """``(impf_id, region_code, region_name)`` CLIMADA assigns to ``iso3``, or None.

    Read from CLIMADA's own country table
    (``ImpfSetTropCyclone.get_impf_id_regions_per_countries``) rather than matched by
    name: Korea resolves to ``(9, 'WP4', 'North West Pacific')``, and that has to be a
    lookup, not a reading of the label.
    """
    from climada.entity.impact_funcs.trop_cyclone import ImpfSetTropCyclone

    try:
        ids, codes, names = ImpfSetTropCyclone.get_impf_id_regions_per_countries([iso3])
    except Exception:
        return None
    if not ids:
        return None
    return int(ids[0]), str(codes[0]), str(names[0])


def heat_status() -> dict[str, Any]:
    """Whether any installed module offers a heat/heatwave impact function.

    Returns the modules actually present so the answer is evidence, not an assertion.
    """
    modules: dict[str, list[str]] = {}
    for base in ("climada.entity.impact_funcs", "climada_petals.entity.impact_funcs"):
        try:
            mod = importlib.import_module(base)
        except Exception:  # pragma: no cover - both are installed in the worker env
            modules[base] = []
            continue
        modules[base] = sorted(n for _, n, _ in pkgutil.iter_modules(mod.__path__))
    heat_like = {
        name
        for names in modules.values()
        for name in names
        if any(tok in name for tok in ("heat", "thermal", "mortal", "utci"))
    }
    return {
        "available": bool(heat_like),
        "status": "AVAILABLE" if heat_like else "NO_IMPACT_FUNCTION",
        "modules_present": modules,
        "heat_like_modules": sorted(heat_like),
        "detail": (
            "CLIMADA core and Petals ship no heat/heatwave impact function; the engine "
            "therefore reports heat hazards as HAZARD_ONLY with null financial fields "
            "rather than authoring a curve (docs/physical-risk-methodology.md 7)."
        ),
    }


def build_registry(flood_region: str = "Asia") -> dict[str, Any]:
    """The whole registry: environment, available functions, and the heat verdict."""
    core, petals = _versions()
    flood = flood_entries(flood_region)
    tc = tc_entries()
    heat = heat_status()
    return {
        "environment": {"climada": core, "climada_petals": petals},
        "entries": [e.to_dict() for e in (*flood, *tc)],
        "by_hazard": {
            "RF": [e.to_dict() for e in flood],
            "TC": [e.to_dict() for e in tc],
            "heat": [],
        },
        "heat": heat,
        "generated_from": "live instantiation of installed CLIMADA objects",
    }


def flood_impact_function(region: str, sector: str) -> Any:
    """The JRC curve itself — ``ImpfRiverFlood.from_jrc_region_sector`` unmodified.

    The engine uses this directly. The 8-point resampled JSON the legacy runner reads
    (``assets/libraries/impact_function_presets.json``) is deliberately not used here: the
    published curve has 11 points over 0-12 m, and re-deriving it would make the number
    this repository's rather than the JRC's.
    """
    from climada_petals.entity.impact_funcs.river_flood import ImpfRiverFlood

    return ImpfRiverFlood.from_jrc_region_sector(region, sector)


def tc_impact_function(impf_id: int) -> Any:
    """One of the ten Eberenz regional functions, by CLIMADA's own id."""
    from climada.entity.impact_funcs.trop_cyclone import ImpfSetTropCyclone

    impf_set = ImpfSetTropCyclone.from_calibrated_regional_ImpfSet()
    func = impf_set.get_func(haz_type="TC", fun_id=impf_id)
    return func[0] if isinstance(func, list) else func
