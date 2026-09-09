"""Heat-attributable **mortality** science — the shared core of the `heat_mortality` peril.

This module is the single source of truth for the heat-health model. It is imported by
both:

* ``physical._run_heat_mortality`` — the platform peril runner (asset workforce), and
* ``scripts/heatwave_europe.py`` — the standalone city-level European analysis.

It complements the platform's ``heatwave`` peril, which reports *labour productivity*
loss from a degC hazard. This one reports **deaths**.

The CLIMADA split
-----------------
Hazard carries the **physics**, the impact function carries the **vulnerability**:

* ``Hazard`` intensity = season **exceedance degree-days** above the local comfort band
  (``degC-days``) — a single, band-agnostic, physically meaningful layer that a real
  E-OBS/ERA5 ingest produces directly.
* ``ImpactFuncSet`` = one function per age band, carrying the non-linear
  dose-response and the band's baseline mortality, so ``impact = value * mdd * paa``
  comes out directly in **attributable deaths**.

The comfort band (minimum-mortality range), not a single MMT point
------------------------------------------------------------------
The temperature-mortality curve is U-shaped: mortality is lowest across a *plateau*
of comfortable temperatures and rises on both arms (cold below, heat above). Each
location therefore carries a band ``[low, high]``; heat-attributable deaths accrue
only above ``high``. The cold arm is a different peril and out of scope here.
**Where the band sits and how wide it is is the location's thermal adaptation** —
hot-adapted places have a higher, wider plateau, so the same 35 degC day can sit
inside the band in Cordoba and far above it in Hamburg.

Scientific status (read this before quoting any number)
-------------------------------------------------------
CLIMADA ships **no** heat-mortality impact function (core: tropical cyclone, European
windstorm; petals: drought, crop yield, river flood, wildfire). What is CLIMADA here is the
*container and engine* — ``ImpactFunc`` / ``ImpactFuncSet`` / ``ImpactCalc`` and
``impact = value * mdd * paa``. **The vulnerability curve itself is a climaterisk custom
implementation**, unlike tropical cyclone (Eberenz presets) or flood (JRC presets) where a
published curve is bundled.

Its parameters are, with one exception, **indicative platform assumptions or of unknown
provenance**: no external source is recorded for beta, the baseline mortality rates, the
reference-city minimum-mortality edges or the band-width coefficients, and none of them has
been calibrated against observed mortality. :data:`PARAMETER_PROVENANCE` records each value,
its location and its status; :func:`provenance_summary` is what the runner reports and the UI
displays. Full table and the remaining gaps: ``docs/HEAT_MORTALITY_PROVENANCE.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from climaterisk_worker.eobs import SummerTmax

HAZ_TYPE = "HM"  # CLIMADA hazard tag for the heat-mortality (degree-day) layer
INTENSITY_UNIT = "degC-days"  # season exceedance degree-days above the comfort band
SEASON_DAYS = 122  # Jun 1 - Sep 30 warm-season window


# --------------------------------------------------------------------------- #
# Reference locations: local climate + adaptation + age structure.            #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RefCity:
    """A reference location supplying local climate, adaptation and age structure.

    Attributes:
        name: City / province label.
        country: ISO-3166 alpha-3 country code.
        lat: Latitude, decimal degrees north.
        lon: Longitude, decimal degrees east.
        population: Total resident population (persons).
        share_over65: Fraction of the population aged >=65 (dimensionless, 0-1).
        tmax_jja_mean: Present-day mean daily maximum temperature, Jun-Aug, degC.
        tmax_jja_sd: Daily spread of daily maximum temperature, Jun-Aug, degC.
        mmt_high: Upper edge of the minimum-mortality (comfort) band, degC — the
            heat-onset threshold above which attributable deaths accrue.
    """

    name: str
    country: str
    lat: float
    lon: float
    population: int
    share_over65: float
    tmax_jja_mean: float
    tmax_jja_sd: float
    mmt_high: float


# Approximate but realistic values: capital-city coordinates, provincial / metro
# population, share >=65, present-day summer (JJA) daily-Tmax climatology, and a
# locally-adapted heat-onset edge (cool north -> low; hot Guadalquivir /
# Mezzogiorno / Thessaly / Alentejo -> high). Not a substitute for
# INE/ISTAT/INSEE/DESTATIS/ELSTAT/INE-PT + AEMET/E-OBS microdata.
REF_CITIES: tuple[RefCity, ...] = (
    # --- Spain (ESP) -------------------------------------------------------- #
    RefCity("Madrid", "ESP", 40.42, -3.70, 6_750_000, 0.185, 31.5, 4.2, 34.0),
    RefCity("Barcelona", "ESP", 41.39, 2.17, 5_700_000, 0.200, 28.5, 3.4, 30.0),
    RefCity("Valencia", "ESP", 39.47, -0.38, 2_590_000, 0.190, 30.5, 3.2, 32.0),
    RefCity("Sevilla", "ESP", 37.39, -5.99, 1_950_000, 0.180, 36.0, 4.0, 40.0),
    RefCity("Malaga", "ESP", 36.72, -4.42, 1_690_000, 0.180, 30.5, 3.0, 33.0),
    RefCity("Murcia", "ESP", 37.99, -1.13, 1_520_000, 0.170, 34.0, 3.6, 37.0),
    RefCity("Vizcaya", "ESP", 43.26, -2.93, 1_150_000, 0.230, 25.5, 3.0, 27.0),
    RefCity("A Coruna", "ESP", 43.36, -8.41, 1_120_000, 0.250, 23.5, 2.8, 26.0),
    RefCity("Zaragoza", "ESP", 41.65, -0.89, 970_000, 0.210, 32.5, 4.0, 35.0),
    RefCity("Granada", "ESP", 37.18, -3.60, 920_000, 0.200, 34.0, 4.0, 37.0),
    RefCity("Cordoba", "ESP", 37.89, -4.78, 780_000, 0.210, 36.5, 4.2, 40.0),
    RefCity("Toledo", "ESP", 39.86, -4.02, 700_000, 0.210, 33.5, 4.4, 36.0),
    RefCity("Badajoz", "ESP", 38.88, -6.97, 670_000, 0.210, 35.0, 4.2, 39.0),
    RefCity("Valladolid", "ESP", 41.65, -4.72, 520_000, 0.240, 30.5, 4.4, 33.0),
    RefCity("Zamora", "ESP", 41.50, -5.75, 170_000, 0.300, 30.5, 4.4, 33.0),
    # --- Italy (ITA) — old population, humid Po valley ---------------------- #
    RefCity("Roma", "ITA", 41.90, 12.50, 4_300_000, 0.220, 30.5, 3.6, 32.0),
    RefCity("Milano", "ITA", 45.46, 9.19, 3_250_000, 0.230, 29.0, 3.4, 30.0),
    RefCity("Napoli", "ITA", 40.85, 14.27, 3_000_000, 0.190, 30.0, 3.2, 32.0),
    RefCity("Torino", "ITA", 45.07, 7.69, 2_250_000, 0.250, 28.5, 3.4, 30.0),
    RefCity("Bari", "ITA", 41.12, 16.87, 1_230_000, 0.210, 30.0, 3.2, 32.0),
    RefCity("Palermo", "ITA", 38.12, 13.36, 1_250_000, 0.210, 30.5, 3.0, 33.0),
    RefCity("Bologna", "ITA", 44.49, 11.34, 1_020_000, 0.240, 30.0, 3.6, 31.0),
    RefCity("Firenze", "ITA", 43.77, 11.26, 1_000_000, 0.250, 31.5, 3.8, 33.0),
    RefCity("Venezia", "ITA", 45.44, 12.32, 850_000, 0.260, 28.5, 3.4, 30.0),
    RefCity("Genova", "ITA", 44.41, 8.93, 830_000, 0.280, 27.5, 3.0, 29.0),
    # --- France (FRA) — 2003-type risk, weaker adaptation in the north ------ #
    RefCity("Paris", "FRA", 48.85, 2.35, 6_700_000, 0.160, 25.5, 3.6, 28.0),
    RefCity("Lille", "FRA", 50.63, 3.06, 2_600_000, 0.160, 23.5, 3.4, 26.0),
    RefCity("Marseille", "FRA", 43.30, 5.37, 2_040_000, 0.210, 29.5, 3.2, 31.0),
    RefCity("Lyon", "FRA", 45.76, 4.83, 1_880_000, 0.180, 28.0, 3.6, 30.0),
    RefCity("Bordeaux", "FRA", 44.84, -0.58, 1_600_000, 0.210, 27.5, 3.6, 29.0),
    RefCity("Nantes", "FRA", 47.22, -1.55, 1_430_000, 0.200, 24.5, 3.2, 26.0),
    RefCity("Toulouse", "FRA", 43.60, 1.44, 1_420_000, 0.190, 29.0, 3.8, 31.0),
    RefCity("Montpellier", "FRA", 43.61, 3.88, 1_180_000, 0.220, 29.5, 3.4, 31.0),
    RefCity("Strasbourg", "FRA", 48.58, 7.75, 1_130_000, 0.190, 26.5, 3.8, 28.0),
    RefCity("Nice", "FRA", 43.70, 7.27, 1_090_000, 0.270, 27.5, 3.0, 29.0),
    # --- Germany (DEU) — old, cool summers, weak heat adaptation ------------ #
    RefCity("Berlin", "DEU", 52.52, 13.40, 3_750_000, 0.190, 24.5, 3.8, 27.0),
    RefCity("Hamburg", "DEU", 53.55, 10.00, 1_900_000, 0.200, 23.0, 3.6, 26.0),
    RefCity("Munchen", "DEU", 48.14, 11.58, 1_560_000, 0.190, 24.5, 3.8, 27.0),
    RefCity("Koln", "DEU", 50.94, 6.96, 1_090_000, 0.190, 25.0, 3.6, 27.0),
    RefCity("Frankfurt", "DEU", 50.11, 8.68, 770_000, 0.180, 25.5, 3.8, 28.0),
    RefCity("Stuttgart", "DEU", 48.78, 9.18, 630_000, 0.190, 25.0, 3.8, 27.0),
    RefCity("Leipzig", "DEU", 51.34, 12.37, 600_000, 0.210, 24.5, 3.8, 27.0),
    # --- Greece (GRC) — hot, adapted (high band), aging --------------------- #
    RefCity("Athina", "GRC", 37.98, 23.73, 3_150_000, 0.220, 33.5, 3.4, 36.0),
    RefCity("Thessaloniki", "GRC", 40.64, 22.94, 1_010_000, 0.210, 31.0, 3.4, 34.0),
    RefCity("Larissa", "GRC", 39.64, 22.42, 165_000, 0.210, 34.5, 3.8, 37.0),
    RefCity("Volos", "GRC", 39.36, 22.94, 145_000, 0.220, 33.0, 3.6, 36.0),
    RefCity("Patra", "GRC", 38.25, 21.73, 215_000, 0.220, 32.0, 3.2, 35.0),
    RefCity("Iraklio", "GRC", 35.34, 25.13, 210_000, 0.200, 29.0, 2.8, 32.0),
    # --- Portugal (PRT) — mild Atlantic coast, hot old Alentejo ------------- #
    RefCity("Lisboa", "PRT", 38.72, -9.14, 2_870_000, 0.230, 28.5, 3.0, 31.0),
    RefCity("Porto", "PRT", 41.15, -8.61, 1_730_000, 0.230, 25.5, 2.8, 28.0),
    RefCity("Coimbra", "PRT", 40.21, -8.43, 430_000, 0.240, 29.5, 3.4, 32.0),
    RefCity("Faro", "PRT", 37.02, -7.93, 320_000, 0.240, 29.0, 3.0, 32.0),
    RefCity("Evora", "PRT", 38.57, -7.91, 150_000, 0.260, 32.5, 4.0, 35.0),
    RefCity("Beja", "PRT", 38.02, -7.86, 145_000, 0.270, 33.5, 4.2, 36.0),
)

COUNTRY_NAMES = {
    "ESP": "Spain",
    "ITA": "Italy",
    "FRA": "France",
    "DEU": "Germany",
    "GRC": "Greece",
    "PRT": "Portugal",
}


# Projected national share of population aged >=65, as a MULTIPLIER on the 2020 baseline
# already carried by each RefCity. Southern/Mediterranean Europe ages fastest; these are
# order-of-magnitude-correct national projections, not municipal ones — replace with
# Eurostat EUROPOP / INE / ISTAT / INSEE / DESTATIS / ELSTAT figures for production use.
#
# Why this matters: 97% of modelled heat deaths fall on the >=65 band, so demographic
# ageing raises heat mortality *even with no warming at all*. Attributing a future change
# to climate without holding demography fixed silently credits warming for ageing.
_AGE_SHARE_MULTIPLIER: dict[str, dict[int, float]] = {
    #        2020   2030   2040   2050
    "ESP": {2020: 1.00, 2030: 1.20, 2040: 1.45, 2050: 1.65},
    "ITA": {2020: 1.00, 2030: 1.17, 2040: 1.35, 2050: 1.50},
    "PRT": {2020: 1.00, 2030: 1.18, 2040: 1.40, 2050: 1.58},
    "GRC": {2020: 1.00, 2030: 1.16, 2040: 1.36, 2050: 1.52},
    "DEU": {2020: 1.00, 2030: 1.15, 2040: 1.28, 2050: 1.35},
    "FRA": {2020: 1.00, 2030: 1.14, 2040: 1.28, 2050: 1.38},
}
AGE_PROJECTION_YEARS = (2020, 2030, 2040, 2050)
MAX_SHARE_OVER65 = 0.45  # a demographic ceiling, so interpolation cannot produce nonsense


def project_age_structure(cities: tuple[RefCity, ...], year: int) -> tuple[RefCity, ...]:
    """Return ``cities`` with ``share_over65`` scaled to a projection ``year``.

    Only the age structure moves — climate, coordinates and total population are untouched —
    so a run against the projected cities isolates the **demographic** contribution to future
    heat mortality. Combine with ``warming_c`` to separate climate from ageing (see
    ``scripts/heatwave_europe.py --decompose``).

    Args:
        cities: Reference/grid locations to project.
        year: Target year; linearly interpolated between the tabulated projection years and
            clamped at the ends.

    Returns:
        A new tuple with projected ``share_over65`` (capped at :data:`MAX_SHARE_OVER65`).
    """
    from dataclasses import replace

    ys = list(AGE_PROJECTION_YEARS)
    out = []
    for c in cities:
        table = _AGE_SHARE_MULTIPLIER.get(c.country)
        if table is None:  # unknown country: leave demography as-is rather than invent it
            out.append(c)
            continue
        mult = float(np.interp(year, ys, [table[y] for y in ys]))
        out.append(replace(c, share_over65=min(c.share_over65 * mult, MAX_SHARE_OVER65)))
    return tuple(out)


def comfort_band(city: RefCity) -> tuple[float, float, float]:
    """Minimum-mortality (comfort) band ``(low, high, width)`` for a location, degC.

    The upper edge is the heat-onset threshold ``mmt_high``. The width scales with how
    hot-adapted the location is — hotter-adapted places have both a higher AND a wider
    low-mortality plateau — via ``width = clip(0.35*(mmt_high-22), 1.5, 8)``. The lower
    edge is ``mmt_high - width`` (below it lies the cold arm, out of scope).

    Args:
        city: The reference location.

    Returns:
        ``(low, high, width)`` in degrees Celsius.
    """
    width = float(np.clip(0.35 * (city.mmt_high - 22.0), 1.5, 8.0))
    return (city.mmt_high - width, city.mmt_high, width)


#: Share of population aged 65+ for countries that have **no** reference cities in
#: :data:`REF_CITIES`. Used by the runner instead of the (wrong-continent) nearest city.
#: KOR: 주민등록인구 기준 2025년 약 20.3% (KOSIS, 행정안전부).
COUNTRY_SHARE_OVER65: dict[str, float] = {
    "KOR": 0.203,
}


def share_over65_for(
    lat: float,
    lon: float,
    iso3: str | None = None,
    override: float | None = None,
) -> float:
    """Resolve the 65+ population share for one site.

    Order: explicit ``override`` → :data:`COUNTRY_SHARE_OVER65` by ``iso3`` → the nearest
    reference city (only meaningful where reference cities exist, i.e. Europe).
    """
    if override is not None:
        return float(np.clip(override, 0.0, MAX_SHARE_OVER65))
    if iso3 and iso3.upper() in COUNTRY_SHARE_OVER65:
        return COUNTRY_SHARE_OVER65[iso3.upper()]
    return nearest_city(lat, lon).share_over65


def nearest_city(lat: float, lon: float) -> RefCity:
    """Return the reference location closest to a coordinate (great-circle-ish).

    Used by the platform runner to give an arbitrary facility a local comfort band and
    age structure. Longitude degrees are scaled by ``cos(lat)`` so the nearest-neighbour
    search is not distorted at European latitudes.

    Args:
        lat: Latitude, decimal degrees north.
        lon: Longitude, decimal degrees east.

    Returns:
        The closest ``RefCity``.
    """
    scale = float(np.cos(np.deg2rad(lat)))
    return min(
        REF_CITIES,
        key=lambda c: (c.lat - lat) ** 2 + ((c.lon - lon) * scale) ** 2,
    )


# --------------------------------------------------------------------------- #
# Age-band dose-response (relative-risk) parameters.                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgeBand:
    """Dose-response + baseline-mortality parameters for one age band.

    Attributes:
        key: Short band id (``"u65"`` / ``"o65"``).
        beta: Log relative-risk slope per degC above the comfort band (1/degC).
            ``RR(T) = exp(beta * (T - mmt_high))`` for ``T > mmt_high``, else 1.
        baseline_daily_mortality: All-cause baseline deaths per person per day for the
            band (1/day). Folds into the impact function so impact comes out directly
            in attributable deaths.
    """

    key: str
    beta: float
    baseline_daily_mortality: float


# beta: the elderly heat-mortality slope is several times the non-elderly slope.
# baseline_daily_mortality: crude all-cause rate per band (>=65 ~ 45/1000/yr,
# <65 ~ 1.3/1000/yr) / 365. Shared across countries (a limitation).
AGE_BANDS: tuple[AgeBand, ...] = (
    AgeBand("u65", beta=0.010, baseline_daily_mortality=1.3e-3 / 365.0),
    AgeBand("o65", beta=0.034, baseline_daily_mortality=45.0e-3 / 365.0),
)


def band_by_key(key: str) -> AgeBand:
    """Return the age band with ``key`` (raises ``KeyError`` if unknown)."""
    for band in AGE_BANDS:
        if band.key == key:
            return band
    raise KeyError(f"unknown age band {key!r}")


# --------------------------------------------------------------------------- #
# Provenance of every constant this model uses.                               #
# --------------------------------------------------------------------------- #
#: Provenance classes, deliberately coarse so a value cannot be dressed up:
#:
#: * ``external``     — an external source is recorded and the value comes from it
#: * ``internal_fit`` — computed inside this repository (a fit or a reduction)
#: * ``indicative``   — a platform design choice, no external source
#: * ``unknown``      — provenance cannot be traced from the code or the docs
PROVENANCE_CLASSES = ("external", "internal_fit", "indicative", "unknown")


@dataclass(frozen=True)
class ParameterRecord:
    """One constant of the heat-mortality model and where it comes from.

    Attributes:
        parameter: Human-readable name.
        value: The value **as written in the code**, rendered as text. Never re-derived
            here — this record documents, it does not compute.
        unit: Physical unit, or ``"-"`` when dimensionless.
        location: ``file::symbol`` where the value lives.
        rationale: The justification actually present in the code/docs, verbatim in spirit.
        citation: External source, or ``"source unavailable"``.
        provenance: One of :data:`PROVENANCE_CLASSES`.
    """

    parameter: str
    value: str
    unit: str
    location: str
    rationale: str
    citation: str
    provenance: str


#: Inventory taken from the code on 2026-09-09. Adding a constant to the model without
#: adding it here fails ``tests/test_heat_provenance.py``.
PARAMETER_PROVENANCE: tuple[ParameterRecord, ...] = (
    ParameterRecord(
        "beta, under 65",
        "0.010",
        "1/degC",
        "heat_mortality.py::AGE_BANDS",
        "comment: the elderly heat-mortality slope is several times the non-elderly slope",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "beta, 65 and over",
        "0.034",
        "1/degC",
        "heat_mortality.py::AGE_BANDS",
        "comment: the elderly heat-mortality slope is several times the non-elderly slope",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "baseline daily mortality, under 65",
        "1.3e-3 / 365",
        "1/day",
        "heat_mortality.py::AGE_BANDS",
        "comment: crude all-cause rate ~1.3/1000/yr, shared across countries (a limitation)",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "baseline daily mortality, 65 and over",
        "45.0e-3 / 365",
        "1/day",
        "heat_mortality.py::AGE_BANDS",
        "comment: crude all-cause rate ~45/1000/yr, shared across countries (a limitation)",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "mmt_high (comfort-band upper edge) per reference city",
        "54 values, 26.0-40.0",
        "degC",
        "heat_mortality.py::REF_CITIES",
        "comment: approximate but realistic, a locally-adapted heat-onset edge; explicitly "
        "not a substitute for national statistics + AEMET/E-OBS microdata",
        "source unavailable",
        "unknown",
    ),
    ParameterRecord(
        "tmax_jja_mean / tmax_jja_sd per reference city",
        "54 pairs, 23.0-36.5 / 3.0-4.6",
        "degC",
        "heat_mortality.py::REF_CITIES",
        "comment: present-day summer daily-Tmax climatology, approximate",
        "source unavailable",
        "unknown",
    ),
    ParameterRecord(
        "population / share_over65 per reference city",
        "54 pairs",
        "persons / -",
        "heat_mortality.py::REF_CITIES",
        "comment: provincial or metro figures, approximate",
        "source unavailable",
        "unknown",
    ),
    ParameterRecord(
        "band-width coefficients",
        "0.35, 22.0, clip 1.5-8.0",
        "- / degC",
        "heat_mortality.py::comfort_band",
        "docstring: the width scales with how hot-adapted the place is",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "adaptation regression (spatial)",
        "a = -0.002, b = 1.0778",
        "degC / -",
        "heat_mortality.py::adaptation_fit",
        "OLS of mmt_high on tmax_jja_mean across REF_CITIES — inherits the table's provenance",
        "source unavailable",
        "internal_fit",
    ),
    ParameterRecord(
        "dose power law per age band",
        "fitted (a, b) per band",
        "-",
        "heat_mortality.py::dose_curve",
        "least squares in log space on a seeded internal ensemble of REF_CITIES seasons",
        "source unavailable",
        "internal_fit",
    ),
    ParameterRecord(
        "internal calibration ensemble",
        "_CALIB_SEED = 20240811, _CALIB_SEASONS = 400",
        "-",
        "heat_mortality.py",
        "fixed so the fitted dose curve is identical in every process (reproducibility)",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "season anomaly spreads (synthetic generator)",
        "1.0 Europe-wide, 1.2 per country",
        "degC",
        "heat_mortality.py::simulate_seasons",
        "a physically-grounded stand-in for reanalysis when no observed grid is present",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "warm-season window",
        "Jun 1 - Sep 30, 122 days",
        "days",
        "heat_mortality.py::SEASON_START/SEASON_END/SEASON_DAYS",
        "modelling choice; narrower than MoMo's attribution window, a known cause of the "
        "tail under-prediction",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "age-structure projection multipliers",
        "6 countries x 4 years, 1.00-1.65",
        "-",
        "heat_mortality.py::_AGE_SHARE_MULTIPLIER",
        "used to project share_over65 to 2030/2040/2050",
        "source unavailable",
        "unknown",
    ),
    ParameterRecord(
        "age-share ceiling",
        "0.45",
        "-",
        "heat_mortality.py::MAX_SHARE_OVER65",
        "comment: a demographic ceiling so interpolation cannot produce nonsense",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "share_over65 for Korea",
        "0.203",
        "-",
        "heat_mortality.py::COUNTRY_SHARE_OVER65",
        "comment: 주민등록인구 기준 2025년 약 20.3%",
        "KOSIS / 행정안전부 주민등록인구통계 (recorded in the code comment; figure not "
        "re-verified against the source in this pass)",
        "external",
    ),
    ParameterRecord(
        "impact-function intensity grid",
        "0-900 degC-days, 61 points",
        "degC-days",
        "heat_mortality.py::build_impact_functions",
        "discretisation of the curve; the upper end is far above any realistic season so "
        "the mdd cap does not bind",
        "source unavailable",
        "indicative",
    ),
    ParameterRecord(
        "default headcount per site",
        "250",
        "persons",
        "physical.py::_DEFAULT_HEADCOUNT",
        "exposure fallback when an asset carries no headcount; the assumption used is "
        "always stated in the result detail",
        "source unavailable",
        "indicative",
    ),
)


def provenance_summary() -> dict[str, Any]:
    """Machine-readable status of the model, for the run payload and the UI.

    Returns:
        ``model`` (what is CLIMADA and what is custom), ``counts`` per provenance class,
        ``calibrated_on_observed_mortality`` (always False until a calibration exists) and
        a short ``label`` for display.
    """
    counts = dict.fromkeys(PROVENANCE_CLASSES, 0)
    for record in PARAMETER_PROVENANCE:
        counts[record.provenance] += 1
    unsupported = counts["indicative"] + counts["unknown"]
    return {
        "model": (
            "climaterisk custom heat-mortality dose-response; CLIMADA supplies "
            "ImpactFunc/ImpactFuncSet/ImpactCalc only (it ships no heat-mortality "
            "impact function)"
        ),
        "counts": counts,
        "n_parameters": len(PARAMETER_PROVENANCE),
        "calibrated_on_observed_mortality": False,
        "label": (
            "Custom heat-mortality model · vulnerability parameters: indicative / not calibrated"
        ),
        "detail": (
            f"{unsupported} of {len(PARAMETER_PROVENANCE)} parameters are indicative "
            "platform assumptions or of unknown provenance; none is calibrated against "
            "observed mortality (docs/HEAT_MORTALITY_PROVENANCE.md)"
        ),
    }


# --------------------------------------------------------------------------- #
# Season ensemble + the two heat metrics.                                     #
# --------------------------------------------------------------------------- #
def simulate_seasons(
    rng: np.random.Generator,
    n_seasons: int,
    cities: tuple[RefCity, ...],
    warming_c: float = 0.0,
) -> np.ndarray:
    """Simulate daily summer Tmax for many present-day seasons (stationary climate).

    A physically-grounded stand-in for E-OBS/ERA5 daily reanalysis. Each season is an
    i.i.d. draw from the present-day climate and gets a Europe-wide anomaly plus a
    per-country anomaly, so locations within a country co-vary more than across borders
    (continental heat like 2003/2022).

    Algorithm:
        $$ T_{p,s,d} = \\bar T_p + w + a^{\\mathrm{EU}}_s + a^{c(p)}_s
                      + \\varepsilon_{p,s,d} $$
        ASCII: T[p,s,d] = mean[p] + warming + eu_anom[s] + country_anom[c,s] + noise
        with $a^{\\mathrm{EU}}\\sim\\mathcal N(0,1.0^2)$, $a^{c}\\sim\\mathcal N(0,1.2^2)$,
        $\\varepsilon\\sim\\mathcal N(0,\\sigma_p^2)$ degC.

    Args:
        rng: Seeded NumPy generator (reproducibility).
        n_seasons: Number of synthetic summers to simulate.
        cities: Active reference locations.
        warming_c: Uniform degC offset (0 = present climate).

    Returns:
        ``tmax`` of shape ``(n_cities, n_seasons, SEASON_DAYS)`` in degC.
    """
    n_p = len(cities)
    countries = sorted({c.country for c in cities})
    cidx = np.array([countries.index(c.country) for c in cities])
    eu_anom = rng.normal(0.0, 1.0, size=n_seasons)
    country_anom = rng.normal(0.0, 1.2, size=(len(countries), n_seasons))
    anom = (eu_anom[None, :] + country_anom[cidx, :])[:, :, None]

    means = np.array([c.tmax_jja_mean for c in cities])[:, None, None] + warming_c
    sds = np.array([c.tmax_jja_sd for c in cities])[:, None, None]
    noise = rng.normal(0.0, 1.0, size=(n_p, n_seasons, SEASON_DAYS)) * sds
    return means + anom + noise


def excess_rr_dose(tmax: np.ndarray, band: AgeBand, cities: tuple[RefCity, ...]) -> np.ndarray:
    """Season-integrated excess relative-risk **dose** per location-season for a band.

    Algorithm:
        $$ I_{p,s} = \\sum_{d}\\max\\!\\big(e^{\\beta (T_{p,s,d}-H_p)}-1,\\ 0\\big) $$
        where $H_p$ is the comfort band's upper edge (degC).
        ASCII: dose[p,s] = sum_days max(exp(beta*(Tmax - mmt_high)) - 1, 0)
        Units: dimensionless * days (``excess_RR_days``).

    Args:
        tmax: Daily Tmax ``(n_cities, n_seasons, SEASON_DAYS)``, degC.
        band: Age band supplying ``beta``.
        cities: Active reference locations (supply ``mmt_high``).

    Returns:
        Dose array ``(n_cities, n_seasons)`` in ``excess_RR_days``.
    """
    mmt_high = np.array([c.mmt_high for c in cities])[:, None, None]
    excess = np.exp(band.beta * (tmax - mmt_high)) - 1.0
    return np.clip(excess, 0.0, None).sum(axis=2)


def exceedance_degree_days(tmax: np.ndarray, cities: tuple[RefCity, ...]) -> np.ndarray:
    """Season **exceedance degree-days** above the comfort band, per location-season.

    The adaptation-adjusted hazard: how much heat a location gets *relative to what its
    population is adapted to*. Unlike raw temperature it is comparable across climates,
    and it is exactly what a real E-OBS/ERA5 ingest can produce. This is the intensity
    stored in the CLIMADA ``Hazard``.

    Algorithm:
        $$ D_{p,s} = \\sum_d \\max(T_{p,s,d}-H_p,\\ 0) $$
        ASCII: load[p,s] = sum_days max(Tmax - mmt_high, 0)
        Units: degC-days per season.

    Args:
        tmax: Daily Tmax ``(n_cities, n_seasons, SEASON_DAYS)``, degC.
        cities: Active reference locations (supply ``mmt_high``).

    Returns:
        Array ``(n_cities, n_seasons)`` in degC-days.
    """
    mmt_high = np.array([c.mmt_high for c in cities])[:, None, None]
    return np.clip(tmax - mmt_high, 0.0, None).sum(axis=2)


# --------------------------------------------------------------------------- #
# degC-days -> dose: the internally fitted non-linear vulnerability curve.     #
# --------------------------------------------------------------------------- #
_CALIB_SEED = 20240811  # fixed so the fitted curve is identical on every process
_CALIB_SEASONS = 400


@lru_cache(maxsize=4)
def dose_curve(band_key: str) -> tuple[float, float]:
    """Fit ``dose ≈ a * D^b`` mapping exceedance degree-days to excess-RR-days.

    The hazard layer stores the *linear* physical measure ``D`` (degree-days), while
    mortality responds to the *exponential* dose. This fits the monotone map between
    them once, on a seeded reference ensemble across all reference locations, so the
    CLIMADA impact function can carry the full non-linearity while the hazard stays a
    simple, ingestable temperature-exceedance layer.

    Algorithm:
        Least squares in log space on the reference ensemble:
        $$ \\ln I = \\ln a + b \\ln D \\quad (D>0) $$
        ASCII: fit ln(dose) = ln(a) + b*ln(degree_days) over all city-seasons with D>0.
        ``b`` comes out slightly above 1 (the exponential dose grows faster than the
        linear degree-day sum), which is the curvature the impact function needs.

    Args:
        band_key: Age-band id (``"u65"`` / ``"o65"``).

    Returns:
        ``(a, b)`` of the power law. Cached per band.
    """
    band = band_by_key(band_key)
    rng = np.random.default_rng(_CALIB_SEED)
    tmax = simulate_seasons(rng, _CALIB_SEASONS, REF_CITIES)
    dd = exceedance_degree_days(tmax, REF_CITIES).ravel()
    dose = excess_rr_dose(tmax, band, REF_CITIES).ravel()
    keep = (dd > 1e-6) & (dose > 1e-12)
    if keep.sum() < 10:  # pragma: no cover - degenerate ensemble
        return (float(band.beta), 1.0)
    b, ln_a = np.polyfit(np.log(dd[keep]), np.log(dose[keep]), 1)
    return (float(np.exp(ln_a)), float(b))


def dose_from_degree_days(degree_days: np.ndarray, band_key: str) -> np.ndarray:
    """Map exceedance degree-days to excess-RR-days via the fitted curve."""
    a, b = dose_curve(band_key)
    dd = np.clip(np.asarray(degree_days, dtype=float), 0.0, None)
    return a * np.power(dd, b, where=dd > 0, out=np.zeros_like(dd))


def build_impact_functions(
    haz_type: str = HAZ_TYPE, max_degree_days: float = 900.0
) -> tuple[Any, dict[str, int]]:
    """CLIMADA ``ImpactFuncSet`` turning degree-days into attributable deaths.

    One function per age band in a single set, so one ``ImpactCalc`` pass over an
    exposure that carries one row per (asset, age band) computes both bands at once.

    Algorithm:
        $$ \\text{mdd}_b(D) = \\min\\!\\big(m_{0,b} \\cdot a_b D^{b_b},\\ 1\\big),
           \\quad \\text{paa}=1 $$
        ASCII: mdd(D) = min(baseline_daily_mortality * dose(D), 1); paa = 1
        CLIMADA's ``impact = value * mdd * paa`` with ``value`` = exposed headcount then
        yields attributable **deaths** directly.

    Args:
        haz_type: CLIMADA hazard tag the functions attach to.
        max_degree_days: Upper end of the intensity grid (degC-days); far above any
            realistic European season, so the ``mdd`` cap never binds in practice.

    Returns:
        ``(impact_func_set, {band_key: impf_id})``.
    """
    from climada.entity import ImpactFunc, ImpactFuncSet

    grid = np.linspace(0.0, float(max_degree_days), 61)
    funcs = []
    ids: dict[str, int] = {}
    for i, band in enumerate(AGE_BANDS, start=1):
        mdd = np.clip(
            band.baseline_daily_mortality * dose_from_degree_days(grid, band.key), 0.0, 1.0
        )
        funcs.append(
            ImpactFunc(
                haz_type=haz_type,
                id=i,
                intensity=grid,
                mdd=mdd,
                paa=np.ones_like(grid),
                intensity_unit=INTENSITY_UNIT,
                name=f"heat_mortality_{band.key}",
            )
        )
        ids[band.key] = i
    return ImpactFuncSet(funcs), ids


def build_hazard(
    degree_days: np.ndarray, cities: tuple[RefCity, ...], haz_type: str = HAZ_TYPE
) -> Any:
    """Assemble a CLIMADA ``Hazard`` from per-location-season exceedance degree-days.

    Mapping (faithful, lossless): location -> centroid, simulated season -> event,
    degree-days -> ``intensity[event, centroid]``, each event equally likely.

    Args:
        degree_days: ``(n_cities, n_seasons)`` exceedance degree-days.
        cities: Active reference locations (supply centroid coordinates).
        haz_type: CLIMADA hazard tag to stamp on the object.

    Returns:
        A checked ``climada.hazard.Hazard``.
    """
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    lat = np.array([c.lat for c in cities])
    lon = np.array([c.lon for c in cities])
    try:
        centroids = Centroids(lat=lat, lon=lon)
    except TypeError:  # older signature
        centroids = Centroids.from_lat_lon(lat, lon)

    intensity = np.asarray(degree_days, dtype=float).T  # (n_events, n_centroids)
    n_ev = intensity.shape[0]
    haz = Hazard(
        haz_type=haz_type,
        units=INTENSITY_UNIT,
        centroids=centroids,
        event_id=np.arange(1, n_ev + 1),
        event_name=[f"s{i}" for i in range(1, n_ev + 1)],
        date=np.arange(1, n_ev + 1),
        frequency=np.full(n_ev, 1.0 / n_ev),
        intensity=sparse.csr_matrix(intensity),
        fraction=sparse.csr_matrix((intensity > 0).astype(float)),
    )
    haz.check()
    return haz


@lru_cache(maxsize=1)
def adaptation_fit() -> tuple[float, float]:
    """Fit ``mmt_high ≈ a + b · tmax_jja_mean`` across the reference locations.

    Thermal adaptation tracks local climate: places with hotter summers sit at a higher
    heat-onset threshold. Fitting that relation lets a *gridded* hazard derive a comfort
    band anywhere from interpolated climatology alone, instead of needing a hand-set MMT
    per grid cell.

    Algorithm:
        Ordinary least squares over the reference table:
        $$ \\mathrm{mmt\\_high} = a + b\\,\\bar T $$
        ASCII: mmt_high = a + b * tmax_jja_mean

    Returns:
        ``(a, b)`` — intercept (degC) and slope (dimensionless).
    """
    t = np.array([c.tmax_jja_mean for c in REF_CITIES])
    m = np.array([c.mmt_high for c in REF_CITIES])
    b, a = np.polyfit(t, m, 1)
    return (float(a), float(b))


def _land_mask(lats: np.ndarray, lons: np.ndarray, country: str) -> np.ndarray:
    """Boolean mask of points inside ``country``'s Natural Earth polygon.

    Uses the repo's own boundary layer (``data/downloads/ne_110m_admin_0_countries.geojson``,
    registered in ``assets/libraries/data_sources.json``) so no network is needed. If that
    layer is absent the mask is all-True and the caller simply gets a bounding-box grid.
    """
    geojson = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "downloads"
        / "ne_110m_admin_0_countries.geojson"
    )
    if not geojson.is_file():  # pragma: no cover - boundary layer not fetched
        return np.ones(lats.shape, dtype=bool)
    import geopandas as gpd
    from shapely.geometry import Point

    world = gpd.read_file(geojson)
    col = "ADM0_A3" if "ADM0_A3" in world.columns else "ISO_A3"
    shape = world.loc[world[col] == country.upper(), "geometry"]
    if shape.empty:  # pragma: no cover - country not in the 110m layer
        return np.ones(lats.shape, dtype=bool)
    poly = shape.union_all() if hasattr(shape, "union_all") else shape.unary_union
    return np.array([poly.contains(Point(x, y)) for x, y in zip(lons, lats, strict=True)])


def country_grid(country: str, res_deg: float = 0.25) -> tuple[RefCity, ...]:
    """Build a gridded set of hazard points covering one country.

    The hazard's spatial resolution, not the exposure's, is what limits a population-grid
    run: 54 reference points cannot resolve a 1 km population raster. This lays a regular
    lat/lon grid over the country, masks it to land, and gives every cell a climatology
    (inverse-distance interpolated from the reference locations) and a comfort band
    (derived from that climatology via :func:`adaptation_fit`).

    Algorithm:
        Inverse-distance weighting with power 2 over the K nearest reference cities:
        $$ \\hat T(x) = \\frac{\\sum_i w_i T_i}{\\sum_i w_i},\\quad w_i = d_i^{-2} $$
        ASCII: T_hat = sum(T_i / d_i^2) / sum(1 / d_i^2)
        Longitudes are scaled by cos(lat) so distances are not stretched at European
        latitudes.

    Args:
        country: ISO3 country code.
        res_deg: Grid spacing in degrees (0.25 ~ 25 km).

    Returns:
        Grid points as ``RefCity`` records (``population`` is 0 — exposure comes from a
        population raster, not from these points).

    Raises:
        ValueError: when no reference locations cover the country.
    """
    refs = [c for c in REF_CITIES if c.country == country.upper()]
    if not refs:
        raise ValueError(f"no reference locations for country {country!r}")

    pad = 1.0
    lat_v = np.arange(
        min(c.lat for c in refs) - pad, max(c.lat for c in refs) + pad + res_deg, res_deg
    )
    lon_v = np.arange(
        min(c.lon for c in refs) - pad, max(c.lon for c in refs) + pad + res_deg, res_deg
    )
    lon_g, lat_g = np.meshgrid(lon_v, lat_v)
    lat_f, lon_f = lat_g.ravel(), lon_g.ravel()

    keep = _land_mask(lat_f, lon_f, country)
    lat_f, lon_f = lat_f[keep], lon_f[keep]
    if lat_f.size == 0:  # pragma: no cover - degenerate bbox
        raise ValueError(f"country grid for {country!r} is empty after the land mask")

    # Interpolate climatology from ALL reference cities (cross-border neighbours inform
    # border cells), inverse-distance weighted on a cos(lat)-corrected plane.
    r_lat = np.array([c.lat for c in REF_CITIES])
    r_lon = np.array([c.lon for c in REF_CITIES])
    r_t = np.array([c.tmax_jja_mean for c in REF_CITIES])
    r_sd = np.array([c.tmax_jja_sd for c in REF_CITIES])
    scale = np.cos(np.deg2rad(lat_f))[:, None]
    d2 = (r_lat[None, :] - lat_f[:, None]) ** 2 + ((r_lon[None, :] - lon_f[:, None]) * scale) ** 2
    w = 1.0 / np.clip(d2, 1e-6, None)
    w /= w.sum(axis=1, keepdims=True)
    t_grid = w @ r_t
    sd_grid = w @ r_sd

    a, b = adaptation_fit()
    mmt_grid = a + b * t_grid

    return tuple(
        RefCity(
            name=f"{country.upper()}_{i}",
            country=country.upper(),
            lat=float(lat_f[i]),
            lon=float(lon_f[i]),
            population=0,
            share_over65=0.0,
            tmax_jja_mean=float(t_grid[i]),
            tmax_jja_sd=float(sd_grid[i]),
            mmt_high=float(mmt_grid[i]),
        )
        for i in range(lat_f.size)
    )


def degree_days_chunked(
    cities: tuple[RefCity, ...],
    n_seasons: int,
    seed: int,
    warming_c: float = 0.0,
    chunk: int = 25,
) -> np.ndarray:
    """Exceedance degree-days for many grid cells, computed in season chunks.

    A full ``(cells, seasons, days)`` array is far too large for a country grid
    (1000 cells x 200 seasons x 122 days ≈ 250 MB), so seasons are simulated in blocks and
    reduced to degree-days immediately.

    Args:
        cities: Grid points (from :func:`country_grid`).
        n_seasons: Total seasons to simulate.
        seed: RNG seed (reproducibility).
        warming_c: Uniform degC offset (0 = present climate).
        chunk: Seasons per block.

    Returns:
        ``(n_cities, n_seasons)`` exceedance degree-days.
    """
    rng = np.random.default_rng(seed)
    out = np.empty((len(cities), n_seasons), dtype=float)
    done = 0
    while done < n_seasons:
        n = min(chunk, n_seasons - done)
        tmax = simulate_seasons(rng, n, cities, warming_c)
        out[:, done : done + n] = exceedance_degree_days(tmax, cities)
        done += n
    return out


def masked_summer_tmax(obs: SummerTmax, country: str, land_mask: bool = True) -> SummerTmax:
    """Restrict an observed/scenario Tmax stack to the cells inside ``country``.

    Args:
        obs: Any :class:`~climaterisk_worker.eobs.SummerTmax` (E-OBS, KMA, …).
        country: ISO3 code used for the Natural Earth polygon test.
        land_mask: Apply the polygon test. Pass False for products that are already
            land-only within the country (KMA 남한상세 is masked to South Korea at source,
            and the 1:110m polygon would wrongly drop Jeju and thin coastal cells).

    Returns:
        A new :class:`SummerTmax` with the kept cells (all cells if none pass the mask).
    """
    from climaterisk_worker.eobs import SummerTmax

    if not land_mask:
        return obs
    inside = _land_mask(obs.lat, obs.lon, country)
    if not inside.any():  # pragma: no cover - boundary layer missing or tiny country
        return obs
    return SummerTmax(
        lat=obs.lat[inside],
        lon=obs.lon[inside],
        years=obs.years,
        tmax=obs.tmax[inside],
        source=obs.source,
    )


def grid_from_summer_tmax(
    obs: SummerTmax,
    country: str,
    tag: str = "obs",
    land_mask: bool = True,
    reference_band: Sequence[RefCity] | None = None,
    band_shift_c: float = 0.0,
) -> tuple[tuple[RefCity, ...], np.ndarray, np.ndarray]:
    """Turn a daily-Tmax stack into hazard cells + season exceedance degree-days.

    The comfort band of every cell comes from its own measured climatology via
    :func:`adaptation_fit` (there is no observational product for local minimum-mortality
    temperature). Each season is one event carrying its calendar year.

    Algorithm:
        $$ D_{c,y} = \\sum_{d} \\max\\bigl(0,\\; T_{c,y,d} - \\mathrm{mmt\\_high}_c\\bigr) $$
        ASCII: D[c,y] = sum_d max(0, T[c,y,d] - mmt_high[c]),  mmt_high = a + b*mean_T[c]

    **Adaptation.** The band is a property of the *population*, not of the weather, so a
    future window must not silently re-derive it from its own (warmer) climatology: doing so
    shifts the threshold by ``b·dT`` with ``b = 1.078 > 1``, which makes warming *reduce*
    exceedance load — an outcome no projection supports. Pass ``reference_band`` (the cells of
    the baseline window) to hold each cell's threshold fixed, and express adaptation as an
    explicit absolute shift via ``band_shift_c``.

    ``band_shift_c`` is the threshold-shift axis of Lee et al. (2019, *IJERPH* 16:1026,
    doi:10.3390/ijerph16061026), whose projection for seven Korean metropolitan cities uses
    +1, +2 and +3 degC against a no-adaptation reference of 0 degC. Korean studies report
    substantial *past* adaptation (Choi et al. 2024, *PLoS One*, doi:10.1371/journal.pone.0310797:
    heat-wave excess death rate 17.6 -> 8.3 per 100k between 1994 and 2018), but every Korean
    projection reviewed treats no adaptation as the reference case and adaptation as a reported
    scenario — see ``docs/HEAT_ADAPTATION_KR.md``.

    Args:
        obs: Daily Tmax stack, degC, shape ``(n_cells, n_years, SEASON_DAYS)``.
        country: ISO3 code (names the cells; frames the optional land mask).
        tag: Short source tag embedded in cell names (``eobs``, ``kma``).
        land_mask: See :func:`masked_summer_tmax`.
        reference_band: Cells of a baseline window whose ``mmt_high`` this window reuses,
            cell for cell. Required for any window that is not the baseline itself; the cells
            must be the same grid (same length and coordinates), which holds when both windows
            come from the same loader and ``coarsen``.
        band_shift_c: Absolute upward shift of the comfort band, degC. ``0.0`` = no
            adaptation (the reference case). Applies to the reference band, or to the fitted
            band when no reference is given.

    Raises:
        ValueError: when ``reference_band`` does not describe the same grid as ``obs``.

    Returns:
        ``(cells, degree_days, years)`` with ``degree_days`` in degC-days,
        shape ``(n_cells, n_years)``.
    """
    obs = masked_summer_tmax(obs, country, land_mask=land_mask)
    lat, lon, tmax = obs.lat, obs.lon, obs.tmax

    t_mean = tmax.mean(axis=(1, 2))
    t_sd = tmax.reshape(tmax.shape[0], -1).std(axis=1)
    if reference_band is None:
        a, b = adaptation_fit()
        mmt_high = a + b * t_mean
    else:
        ref = tuple(reference_band)
        if len(ref) != lat.size:
            raise ValueError(
                f"reference_band has {len(ref)} cells but this window has {lat.size}; "
                "both windows must come from the same loader and coarsen"
            )
        ref_lat = np.array([c.lat for c in ref], dtype=float)
        ref_lon = np.array([c.lon for c in ref], dtype=float)
        if not (np.allclose(ref_lat, lat, atol=1e-6) and np.allclose(ref_lon, lon, atol=1e-6)):
            raise ValueError(
                "reference_band cells do not line up with this window's grid — the bands "
                "would be attached to the wrong locations"
            )
        mmt_high = np.array([c.mmt_high for c in ref], dtype=float)
    mmt_high = mmt_high + float(band_shift_c)

    cells = tuple(
        RefCity(
            name=f"{country.upper()}_{tag}_{i}",
            country=country.upper(),
            lat=float(lat[i]),
            lon=float(lon[i]),
            population=0,
            share_over65=0.0,
            tmax_jja_mean=float(t_mean[i]),
            tmax_jja_sd=float(t_sd[i]),
            mmt_high=float(mmt_high[i]),
        )
        for i in range(lat.size)
    )
    degree_days = np.clip(tmax - mmt_high[:, None, None], 0.0, None).sum(axis=2)
    return cells, degree_days, obs.years


def observed_country_grid(
    country: str,
    year_start: int = 1980,
    year_end: int | None = None,
) -> tuple[tuple[RefCity, ...], np.ndarray, np.ndarray]:
    """Build the hazard grid from **observed** E-OBS daily Tmax instead of the generator.

    This is the same object as :func:`country_grid` + :func:`degree_days_chunked`, except
    every number comes from observations: the grid is E-OBS's own 0.25° land grid, the
    climatology is that cell's measured Jun-Sep Tmax, and each event is a **real summer**
    carrying its calendar year — so 2003 and 2022 appear as the extremes they were, rather
    than as anonymous draws. See :func:`grid_from_summer_tmax` for the reduction; the same
    path serves the KMA 남한상세 grids for Korea (``scripts/heat_korea.py``).

    Args:
        country: ISO3 country code (frames the bounding box and masks to land).
        year_start: First observed season to include.
        year_end: Last observed season (default: last complete season in the file).

    Returns:
        ``(cells, degree_days, years)`` where ``degree_days`` is
        ``(n_cells, n_years)`` in degC-days and ``years`` are calendar years.

    Raises:
        ValueError: when no reference locations frame the country.
        eobs.EobsUnavailable: when the E-OBS file is missing or covers no land here.
    """
    from climaterisk_worker import eobs

    refs = [c for c in REF_CITIES if c.country == country.upper()]
    if not refs:
        raise ValueError(f"no reference locations for country {country!r}")
    pad = 1.0
    bbox = (
        min(c.lon for c in refs) - pad,
        max(c.lon for c in refs) + pad,
        min(c.lat for c in refs) - pad,
        max(c.lat for c in refs) + pad,
    )
    obs = eobs.load_summer_tmax(bbox, year_start=year_start, year_end=year_end)
    # E-OBS covers the whole box, so clip to the country itself.
    return grid_from_summer_tmax(obs, country, tag="eobs", land_mask=True)


def standardized_grid(
    degree_days: np.ndarray,
    cities: tuple[RefCity, ...],
    region: str,
    ref_year: int = 2020,
    climate_scenario: str = "historical",
    years: np.ndarray | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Build the platform's standardized-grid dict for the ``heat_mortality`` peril.

    Feeds ``hazard_convert.convert_grid_to_catalog`` so the hazard lands in the local
    catalog and the platform runner can resolve it.

    Args:
        degree_days: ``(n_cities, n_seasons)`` exceedance degree-days for ``cities``.
        cities: The locations these columns belong to (already filtered to ``region``).
        region: ISO-3 region code to file the hazard under.
        ref_year: Catalog year key.
        climate_scenario: Catalog scenario key.
        years: Calendar year of each season, when the seasons are **observed** (E-OBS).
            Synthetic ensembles pass None and get sequential 1..n labels, which is honest:
            a simulated season has no calendar year.
        source: Provenance string; defaults to the synthetic-generator description.

    Returns:
        A standardized-grid dict (see ``hazard_convert`` for the schema).
    """
    dd = np.asarray(degree_days)
    n_seasons = int(dd.shape[1])
    if years is None:
        labels = list(range(1, n_seasons + 1))
        src = source or "heat_mortality synthetic season exceedance degree-days"
        lic = "synthetic; replace with E-OBS/ERA5 for production"
    else:
        labels = [int(y) for y in np.asarray(years)]
        if len(labels) != n_seasons:
            raise ValueError(f"years has {len(labels)} entries but degree_days has {n_seasons}")
        src = source or "heat_mortality observed exceedance degree-days (E-OBS)"
        lic = "E-OBS / ECA&D — see https://www.ecad.eu/ for terms"
    return {
        "peril": "heat_mortality",
        "haz_type": HAZ_TYPE,
        "units": INTENSITY_UNIT,
        "climate_scenario": climate_scenario,
        "region": region,
        "year": int(ref_year),
        "source": src,
        "license": lic,
        "cells": [{"cell_id": f"c{i}", "lat": c.lat, "lon": c.lon} for i, c in enumerate(cities)],
        "observations": [
            {
                "cell_id": f"c{i}",
                "year": labels[s],
                "intensity": float(dd[i, s]),
                "valid": True,
            }
            for i in range(len(cities))
            for s in range(n_seasons)
        ],
    }
