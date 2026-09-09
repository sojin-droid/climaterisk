# Heat mortality — model status and parameter provenance

**Purpose**: state exactly what the `heat_mortality` peril is, what it is not, and what every
constant in it rests on. This document changes no number. It exists because the model was
described in places as if its constants came from published sources and had been fitted to
observed mortality. Neither is true, and the wording has been corrected.

Inventory taken from the code on 2026-09-09. The table below is rendered from
`heat_mortality.PARAMETER_PROVENANCE`, which is the machine-readable copy the runner reports
and `tests/test_heat_provenance.py` enforces.

## 1. What is CLIMADA and what is ours

CLIMADA ships **no** heat-mortality impact function. Verified against the installed
distribution: core provides `ImpfTropCyclone` and `ImpfStormEurope`; petals provides
`ImpfDrought`, `ImpfRelativeCropyield`, `ImpfRiverFlood`, `ImpfWildfire`. Nothing for
heat, mortality or health.

```
CLIMADA        ImpactFunc / ImpactFuncSet / ImpactCalc, impact = value × mdd × paa
   ↓
climaterisk    heat-mortality vulnerability: comfort band, age-stratified dose-response,
               baseline mortality  ← custom, this repository
```

This is unlike tropical cyclone (Eberenz et al. regional `v_half` presets) or the flood
family (JRC regional presets), where a published curve is bundled and selected by country.

| Component | Source | Status |
|---|---|---|
| `ImpactCalc` | CLIMADA | Native |
| `ImpactFunc` / `ImpactFuncSet` container | CLIMADA | Native |
| Heat-mortality vulnerability curve | climaterisk | **Custom** |
| Age stratification (<65 / ≥65) | climaterisk | **Custom** |
| Baseline mortality rates | climaterisk | **Indicative** — no source recorded |
| MMT / comfort band and its width | climaterisk | **Indicative / unknown** — no source recorded |
| Future scenario routing | platform hazard routing (baseline band + explicit shift) | Partial |
| Korean calibration | observed Korean mortality | **Not yet implemented** |
| Empirical validation | Korean mortality / loss data | **Not validated** |

The hazard side is on a different footing: the intensity layer is observed daily Tmax
(E-OBS, or the KMA 남한상세 grids) reduced to exceedance degree-days. The provenance problem
is the **vulnerability** side.

## 2. Provenance classes

| Class | Meaning |
|---|---|
| **A · external** | an external source is recorded in the code or docs and the value comes from it |
| **B · internal fit** | computed inside this repository (a fit or a reduction over repo data) |
| **C · indicative assumption** | a platform design choice; no external source, no calibration |
| **D · unknown provenance** | the basis cannot be traced from the code or the docs |

No source was invented for this pass. Where none exists the citation column says
*source unavailable*.

## 3. Parameter table

| Parameter | Current value | Unit | Location | Rationale in the code | Citation | Status |
|---|---|---|---|---|---|---|
| beta, under 65 | `0.010` | 1/degC | `heat_mortality.py::AGE_BANDS` | comment: the elderly heat-mortality slope is several times the non-elderly slope | source unavailable | C · indicative assumption |
| beta, 65 and over | `0.034` | 1/degC | `heat_mortality.py::AGE_BANDS` | comment: the elderly heat-mortality slope is several times the non-elderly slope | source unavailable | C · indicative assumption |
| baseline daily mortality, under 65 | `1.3e-3 / 365` | 1/day | `heat_mortality.py::AGE_BANDS` | comment: crude all-cause rate ~1.3/1000/yr, shared across countries (a limitation) | source unavailable | C · indicative assumption |
| baseline daily mortality, 65 and over | `45.0e-3 / 365` | 1/day | `heat_mortality.py::AGE_BANDS` | comment: crude all-cause rate ~45/1000/yr, shared across countries (a limitation) | source unavailable | C · indicative assumption |
| mmt_high (comfort-band upper edge) per reference city | `54 values, 26.0-40.0` | degC | `heat_mortality.py::REF_CITIES` | comment: approximate but realistic, a locally-adapted heat-onset edge; explicitly not a substitute for national statistics + AEMET/E-OBS microdata. Since the gridded paths read the threshold off the measured distribution, this table now sets only the synthetic/interpolated path's level and the fitted intercept | source unavailable | D · unknown provenance |
| tmax_jja_mean / tmax_jja_sd per reference city | `54 pairs, 23.0-36.5 / 3.0-4.6` | degC | `heat_mortality.py::REF_CITIES` | comment: present-day summer daily-Tmax climatology, approximate | source unavailable | D · unknown provenance |
| population / share_over65 per reference city | `54 pairs` | persons / - | `heat_mortality.py::REF_CITIES` | comment: provincial or metro figures, approximate | source unavailable | D · unknown provenance |
| band-width coefficients | `0.35, 22.0, clip 1.5-8.0` | - / degC | `heat_mortality.py::comfort_band` | docstring: the width scales with how hot-adapted the place is | source unavailable | C · indicative assumption |
| exposure metric | `daily mean temperature, Jun-Sep` | degC | `eobs.py::load_summer_daily_mean / kma_scenario.py (TA)` | every citable threshold and exposure-response estimate used here is expressed in daily mean temperature, so the hazard is built in that metric | Kim (2020) IJERPH 17:5720 doi:10.3390/ijerph17165720; Gasparrini et al. (2015) Lancet 386:369-375; Tobías et al. (2021) Environ Epidemiol 5:e169 | **A · external** |
| adaptation slope (MMT vs local climate) | `0.8 degC per degC of mean temperature (SD slope 1.0 recorded, not applied)` | - | `heat_mortality.py::MMT_ANNUAL_MEAN_SLOPE / adaptation_fit` | published spatial adaptation of the minimum mortality temperature; replaces the repository's own OLS slope of 1.078, which let warming reduce the exceedance load | Tobías A, Hashizume M, Honda Y, Sera F, Ng CFS, et al. (2021) Environ Epidemiol 5:e169, doi:10.1097/EE9.0000000000000169 (658 communities, 43 countries) | **A · external** |
| adaptation intercept | `a = mean(mmt_high) - 0.8 x mean(tmax_jja_mean)` | degC | `heat_mortality.py::adaptation_fit` | only the level is fitted, over REF_CITIES, with the slope held at the published value; inherits the reference table's provenance | source unavailable | B · internal fit |
| heat-onset percentile, Korea | `93.0` | percentile of the summer (Jun-Sep) daily-mean distribution | `heat_mortality.py::HEAT_ONSET_PERCENTILE` | threshold point of heatwave mortality risk over 229 Korean districts, same season window and same metric as this model (urban 92nd, rural 95th) | Kim (2020) Heatwave-Related Mortality Risk and the Risk-Based Definition of Heat Wave in South Korea, IJERPH 17:5720, doi:10.3390/ijerph17165720 | **A · external** |
| heat-onset percentile, countries without a local study | `93.0` | percentile of the summer daily-mean distribution | `heat_mortality.py::DEFAULT_HEAT_ONSET_PERCENTILE` | the Korean estimate applied elsewhere for want of a summer-window study; an extrapolation, reported as one by heat_onset_percentile() | source unavailable | C · indicative assumption |
| dose power law per age band | `fitted (a, b) per band` | - | `heat_mortality.py::dose_curve` | least squares in log space on a seeded internal ensemble of REF_CITIES seasons | source unavailable | B · internal fit |
| internal calibration ensemble | `_CALIB_SEED = 20240811, _CALIB_SEASONS = 400` | - | `heat_mortality.py` | fixed so the fitted dose curve is identical in every process (reproducibility) | source unavailable | C · indicative assumption |
| season anomaly spreads (synthetic generator) | `1.0 Europe-wide, 1.2 per country` | degC | `heat_mortality.py::simulate_seasons` | a physically-grounded stand-in for reanalysis when no observed grid is present | source unavailable | C · indicative assumption |
| warm-season window | `Jun 1 - Sep 30, 122 days` | days | `heat_mortality.py::SEASON_START/SEASON_END/SEASON_DAYS` | modelling choice; narrower than MoMo's attribution window, a known cause of the tail under-prediction | source unavailable | C · indicative assumption |
| age-structure projection multipliers | `6 countries x 4 years, 1.00-1.65` | - | `heat_mortality.py::_AGE_SHARE_MULTIPLIER` | used to project share_over65 to 2030/2040/2050 | source unavailable | D · unknown provenance |
| age-share ceiling | `0.45` | - | `heat_mortality.py::MAX_SHARE_OVER65` | comment: a demographic ceiling so interpolation cannot produce nonsense | source unavailable | C · indicative assumption |
| share_over65 for Korea | `0.203` | - | `heat_mortality.py::COUNTRY_SHARE_OVER65` | comment: 주민등록인구 기준 2025년 약 20.3% | KOSIS / 행정안전부 주민등록인구통계 (recorded in the code comment; figure not re-verified against the source in this pass) | **A · external** |
| impact-function intensity grid | `0-900 degC-days, 61 points` | degC-days | `heat_mortality.py::build_impact_functions` | discretisation of the curve; the upper end is far above any realistic season so the mdd cap does not bind | source unavailable | C · indicative assumption |
| default headcount per site | `250` | persons | `physical.py::_DEFAULT_HEADCOUNT` | exposure fallback when an asset carries no headcount; the assumption used is always stated in the result detail | source unavailable | C · indicative assumption |

**Tally**: 4 x A, 2 x B, 12 x C, 4 x D of 22 records. The **band and its response to
climate are now cited** (Kim 2020 for the Korean threshold percentile; Tobías et al. 2021 for
the adaptation slope; the metric choice follows all three source papers). What remains
unsupported is the **dose-response itself** — β per age band and the baseline mortality rates —
and none of the 22 is calibrated against observed mortality.

Why β is still uncited, precisely: the Korean estimates available are **heatwave-episode
relative risks at percentile cut-offs with a lag structure** (Kim 2020: total 1.11 at the 93rd,
65+ 1.20 at the 98th; 65+ urban 1.13 at the 95th), not per-day slopes per degC. Converting them
into this model's per-day β needs a reconciliation of exposure definition and lag that this pass
does not attempt — doing it by arithmetic alone would overstate the slope.

## 4. What this means when reading a result

- The **form** of the model is a dose-response (relative risk × baseline mortality integrated
  over the season), not an indicative damage ramp. That much is a real modelling choice.
- The **level** of the numbers rests on uncited constants. Read per-100k rates and the
  *ranking* of locations; do not read absolute death counts as estimates of reality.
- The Spain run's agreement with MoMo's recent range is a **comparison**, not a calibration —
  no parameter was fitted to any surveillance series. The known 2.3× tail under-prediction is
  reported in `HEATWAVE_EUROPE.md`.
- The runner reports this status in `parameter_status` and the Results card shows it, so the
  caveat travels with the number instead of living only in a document.

## 5. Potential external anchors (roadmap only — not used as provenance)

Recorded for the B stage. **None of these is currently a source for any value in the table**,
and numerical similarity between an existing constant and a published estimate is explicitly
not treated as provenance.

| Constant needing a source | Potential anchor | What would still be required |
|---|---|---|
| β (all-age) | Kim YM, Kim S, Liu Y (2014) *Front Environ Sci* 2:3, doi:10.3389/fenvs.2014.00003 — six Korean cities, +2.7 % all-cause mortality per 1 °C above the 75th-percentile MMT | Age-band split; a decision on which MMT definition (daily mean vs daily max) the platform uses; refit of the dose curve and a new regression baseline |
| β by age band | A Korean age-stratified temperature-mortality study (candidate: *Sci Rep* 2023, Korea 1999–2018 — full text not obtained in this pass) | Obtain the paper; check the exposure metric and lag structure match this model's |
| Baseline mortality by age band | KOSIS / 통계청 사망원인통계 age-specific crude death rates | Per-country rates instead of one shared pair; a year and a definition to cite |
| Korean MMT / comfort band | No Korean MMT table exists in this repository | Estimate from MK-PRISM (2000–2019) + national daily mortality; access conditions for the mortality series are unverified |
| Adaptation (threshold shift) | Lee JY, Lee WS, Ebi KL, Kim H (2019) *IJERPH* 16:1026 — already the basis of the `band_shift_c` axis (`docs/HEAT_ADAPTATION_KR.md`) | Already recorded there as the scenario set; the *magnitude* for Korea remains unmeasured |

## 6. Remaining scientific gap (B stage)

1. Korean age-specific mortality rates (KOSIS) to replace the two shared baseline rates.
2. A Korean heat-mortality dose-response, age-stratified, with a stated exposure metric.
3. A Korean MMT / temperature-mortality relationship — the platform has none; the 54-city
   table is European and its own comment disclaims it.
4. Validation against observed Korean mortality (`RISK_REGISTER.md` C2/C5).
5. If the future-scenario adaptation axis is to be more than a reported scenario, a measured
   threshold shift for Korea (`HEAT_ADAPTATION_KR.md` §6).

Changing any of 1–3 moves every heat-mortality number, so it needs a fresh regression
baseline in the same change (CLAUDE.md: numerical correctness).
