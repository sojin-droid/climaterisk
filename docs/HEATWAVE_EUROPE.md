# European heatwave mortality — a CLIMADA-native tool (ES / IT / FR / DE / GR / PT)

Heat-attributable **mortality** — the health end-point that dominates European heat risk —
modelled on the real CLIMADA 6.1 API and executed in the project's worker env
(`./.climada-env`). It complements the platform's built-in `heatwave` peril, which reports
*labour productivity* loss from a degC hazard.

It ships in **two forms that share one science core**:

| | What it is | Entry point |
|---|---|---|
| **Platform peril** `heat_mortality` | Runs inside the app like any other peril: expected annual heat-attributable **deaths** among each asset's on-site people. | `worker/climaterisk_worker/physical.py::_run_heat_mortality` |
| **City-level CLI** | The European city study (54 metros, figure, return periods, adaptation comparison). | `scripts/heatwave_europe.py` |

Both import `worker/climaterisk_worker/heat_mortality.py`, which is the **single source of
truth** for the reference-city table, comfort bands, age bands, the season ensemble and the
two heat metrics — so the platform peril and the study can never drift apart.

Coverage: **Spain (ESP)**, **Italy (ITA)**, **France (FRA)**, **Germany (DEU)**, **Greece (GRC)**,
**Portugal (PRT)** — 54 province / department / metro reference locations. Add a country by
appending rows to `REF_CITIES` in the worker module with a new ISO-3 code (and a colour in
`_COUNTRY_COLOR` for the CLI figure).

## Running it as a platform peril

```bash
# 1. File the hazard layers in the local catalog (writes BOTH the degC `heatwave`
#    layer and the degC-days `heat_mortality` layer, one pair per country). The
#    mortality layer is laid on a land-masked country grid — 0.25 deg by default
#    (~849 cells for Spain vs 15 reference points).
./.climada-env/bin/python scripts/heatwave_europe.py --register
./.climada-env/bin/python scripts/heatwave_europe.py --register --grid-res-deg 0.1  # finer
```

Then pick an **exposure** — the peril runs on any of three, so a facility is optional:

| Exposure | What it answers | How |
|---|---|---|
| **Facilities** (`headcount`) | deaths among people at *my sites* | select "Heat mortality (deaths)" with assets on the map; set each asset's `headcount` |
| **Reference-city population** (`population_ref`) | deaths across a country's **major metros**, no facility needed | modeled-exposure run: `{country: "ESP", exposure_source: "population_ref", peril: "heat_mortality"}` |
| **Population raster** (`raster`) | deaths across the **whole country** on a real grid | drop `<iso3>_ppp_2020_1km_Aggregated.tif` (WorldPop, free) in `~/climada/data`, then `exposure_source: "raster"` |

Population exposures carry **people** in `value`; `litpop._grid_to_assets` passes them as
`headcount` and zeroes `value`, so no damage peril can mistake people for currency.

### Agreement across exposures (Spain, present climate)

An internal consistency check between exposure layers of the same model — not a validation
against observed mortality.

| Exposure | Cells | People | Deaths/yr | per 100k |
|---|---|---|---|---|
| Reference-city population | 15 | 27.2 M | 2,035 | **7.48** |
| WorldPop raster (~8 km) | 8,245 | 46.8 M | 3,323 | **7.11** |
| City-level CLI (same science) | 15 | 27.2 M | 2,051 | **7.54** |

The three paths agree on the **per-capita rate to within ~5%** while absolute totals scale
with the population actually covered — independent confirmation that the degree-days → dose
calibration and the exposure plumbing are consistent. The full-country figure (**≈3,300
deaths/yr**) is directly comparable to MoMo's national series and lands in its recent
observed range (~1,500–3,800).

The result carries `result_kind: "mortality"` and `metric_unit:
"expected annual heat-attributable deaths"`, so `aai_agg` and every `per_asset.eai` are
**persons, not currency**. Non-monetary perils are excluded from the financial rollup
(`src/climaterisk/finance/service.py::per_asset_aai`) — summing deaths into an EBITDA/DSCR
chain would be meaningless.

### How the peril is wired

| Layer | Where |
|---|---|
| Science core (bands, dose-response, calibration, CLIMADA objects) | `worker/climaterisk_worker/heat_mortality.py` |
| Peril runner (asset headcount → deaths) | `worker/climaterisk_worker/physical.py::_run_heat_mortality`, registered in `_RUNNERS` |
| Peril id whitelist | `src/climaterisk/core/enums.py::Peril.HEAT_MORTALITY` |
| Exposure field | `Asset.headcount` (`src/climaterisk/core/entities.py`) → `AssetSpec.headcount` (`src/climaterisk/engines/base.py`) |
| UI catalog entry | `assets/libraries/perils.json` (`supported_mvp: true`) |
| Result contract | `result_kind: "mortality"` (`frontend/climaterisk/src/types.ts`) |
| Impact formatting | `impactFormatter()` (`frontend/climaterisk/src/lib/format.ts`) — one formatter injected into the KPIs, map tooltip, per-asset grid and return-period axis, so deaths are never money-formatted |
| Methodology figure | `GET /api/libraries/method-figure/heat_mortality.png` (`src/climaterisk/api/routers/libraries.py`) → `MethodFigure` component, shown in the Results card |
| Tests | `tests/test_heat_mortality.py` |

### The figure inside the app

The CLI's four-panel figure is served to the Results view under **"How this peril is
modelled"**. It is a *method* artifact, not a run output, so the endpoint 404s until the
script has produced it — and the panel then hides itself rather than showing a broken image:

```bash
./.climada-env/bin/python scripts/heatwave_europe.py --country ESP   # writes the PNG
```

Register a figure for another peril by adding a row to `_METHOD_FIGURES` in
`src/climaterisk/api/routers/libraries.py`.

**Hazard = physics, impact function = vulnerability.** The catalog layer stores season
**exceedance degree-days** above each location's comfort band (`HM`, `degC-days`) — a single,
band-agnostic quantity a real E-OBS/ERA5 ingest produces directly. The CLIMADA
`ImpactFuncSet` then carries the age-stratified non-linearity: `dose ≈ a·D^b` (fitted once on
a seeded reference ensemble, exponent > 1) times the band's baseline mortality, so
`impact = headcount × mdd × paa` comes out in deaths. Each site becomes two exposure rows
(<65 / ≥65, split by the nearest reference location's age structure) resolved in one
`ImpactCalc` pass.

## Running the city-level CLI

```bash
# all six countries: numbers + four-panel figure under data/heatwave_europe/
./.climada-env/bin/python scripts/heatwave_europe.py

# a single country
./.climada-env/bin/python scripts/heatwave_europe.py --country ITA

# also file a per-country heat hazard in the local catalog so the platform's
# built-in `heatwave` peril becomes runnable for each ISO-3 country
./.climada-env/bin/python scripts/heatwave_europe.py --register

# bigger ensemble (stabler rare tails) / different seed
./.climada-env/bin/python scripts/heatwave_europe.py --seasons 5000 --seed 7

# warmer-climate sensitivity: shift every city's summer up by 1.5 degC
./.climada-env/bin/python scripts/heatwave_europe.py --warming-c 1.5
```

Outputs: `data/heatwave_europe/results.json` (per-city + per-country + Europe rollup, per-100k
rates, comfort bands, heat loads, return periods) and `data/heatwave_europe/heatwave_europe.png`.

### What the figure argues

The four panels are an argument, read top-left → bottom-right:

| Panel | Question it answers |
|---|---|
| **1 — risk map** | *Where* is the risk? Colour = deaths per 100k (sequential single hue), size = population. The geography of risk, not of temperature. |
| **2 — comfort bands** | *Why* do cities differ? Each city's minimum-mortality band; higher & wider = better adapted. |
| **3 — naive** | Does **absolute summer temperature** explain per-capita risk? Largely no. |
| **4 — adaptation-adjusted** | Does **temperature relative to local adaptation** explain it? Yes. |

**Panels 3 and 4 are a controlled comparison**: identical y-axis limits, identical marker size and
colour, and the *same* labelled cities in both — the only thing that changes is the x variable
(raw °C vs °C-days above the comfort band). Each panel prints its Pearson *r*, so the gain is
quantified rather than asserted. Tracking one labelled city across the two panels shows the flip
directly: Sevilla sits at the far right of panel 3 (hottest) but at the far left of panel 4
(lowest adaptation-adjusted load).

Return-period severity is reported as a table in the terminal output and in `results.json`
(`return_period_deaths`) rather than as a fifth panel.

## The comfort band (minimum-mortality range), not a single MMT point

The temperature–mortality curve is **U-shaped**: mortality is lowest across a *plateau* of
comfortable temperatures and rises on both arms — **cold below, heat above**.

```
mortality
  ↑ ＼                          ／
    ＼                        ／
      ＼____________________／        ← the plateau: minimum-mortality "comfort band"
        [ low  ..….…..  high ]
      cold arm              heat arm  ← this tool models ONLY the heat arm
```

So each city carries a **band** `[comfort_low .. comfort_high]`, not one MMT point.
Heat-attributable deaths accrue **only above `comfort_high`** (the heat-onset edge); the cold arm
below `comfort_low` is a different peril and out of scope here — the "people freezing to death"
side is **cold-related mortality**, which across Europe still carries a larger total burden than
heat, but is not what this tool computes.

**Where the band sits and how wide it is *is* the city's thermal adaptation.** `comfort_band()`
derives it from the heat-onset edge: `width = clip(0.35·(comfort_high − 22), 1.5, 8)` — hotter-
adapted places have a **higher and wider** plateau (physiological acclimatization + air
conditioning + shade/siesta behaviour), so the same 35 °C day sits inside the band in Córdoba
(≈ 35–40 °C) but far above it in Paris (≈ 26–28 °C).

## Why not a single degC→damage curve

Heat kills through **cumulative** exposure across a whole warm season, and the dose-response is
strongly **age-stratified** (the ≥65 slope is several times the <65 slope) and **locally
adapted** (the band above). A scalar temperature→damage impact function cannot carry that
non-linearity.

So the hazard is split by age band and its **intensity is the season-integrated excess-risk
dose**, which makes the CLIMADA impact function exactly linear and physically interpretable.
Two `ImpactCalc` passes (one per band) are summed.

## Why this peril is a good (and a bad) way to explain CLIMADA

**Good, because it makes the framework's structure unavoidable.**

| | |
|---|---|
| `Risk = Hazard × Exposure × Vulnerability` becomes *visible* | For most perils the risk map looks like the hazard map, so the three factors can stay blurred. Here they must not: raw summer temperature explains per-capita risk at **r = −0.24**, temperature *relative to local adaptation* at **r = +0.90**. Spain's hottest cities carry its lowest per-capita risk. |
| The hazard's *intensity* is a modelling decision | Wind speed and flood depth are given; "heat intensity" is not. Putting exceedance degree-days in the `Hazard` and the exponential biology in the `ImpactFuncSet` is what let synthetic input be swapped for observed E-OBS **with no code change** (9.89 → 10.11 per 100k). |
| `ImpactFuncSet` carries a dose-response, not a ramp | `impact = value × mdd × paa` with `value` = people and `mdd` = death probability yields deaths directly. The *form* is a dose-response (relative risk × baseline mortality) rather than an indicative damage ramp; its *parameters* are indicative platform assumptions and are not calibrated (`docs/HEAT_MORTALITY_PROVENANCE.md`). CLIMADA provides no heat-mortality impact function, so this curve is climaterisk's own. |
| It stress-tests the units contract | Everything else in the platform is money. Mortality exposed three places that silently assumed currency (finance rollup, KPI formatting, portfolio aggregation). |
| **It is falsifiable** | MoMo/SISMG publish observed excess deaths, so the demo is not self-referential: 2022 came out as the extreme it was, *and* a real 2.3× tail under-prediction surfaced. |
| Adaptation is an explicit parameter | The comfort band *is* the adaptation level, so "raise MMT by 1 °C via cooling centres" is a directly interpretable intervention. |

**Bad, because it sits outside the platform's core assumptions.**

| | |
|---|---|
| The financial chain does not apply | No monetary loss ⇒ no EBITDA/DSCR/rating path. Use a damage peril to demonstrate those. |
| It is not asset-shaped | Exposure is population, not facility value, which is why `population_ref` / `raster` exposure sources exist. |
| The observed record is short | 45 summers, reported to 22 years. Long tails need the synthetic generator. |
| Attribution is contested | MoMo's window and definition differ from this model's, so part of the 2.3× gap is methodology, not model error. |

## The season ensemble, the mean, and return periods

Heat risk is not "the average summer" — it is the **distribution of summer severity** (most
summers mild, a few catastrophic like 2003). The tool therefore Monte-Carlo simulates many
synthetic present-day summers (`--seasons`, default **1000**) and reads the metrics off that
distribution:

| Metric | Meaning |
|---|---|
| `mean/yr` | expected-annual heat deaths = average over all simulated seasons (CLIMADA `aai_agg`) |
| `max-yr` | the single worst simulated season in the ensemble |
| `1-in-T-yr` | the **return-period** season: severity exceeded on average once every `T` years |

**Return period = how extreme a scenario you are quoting.** A 1-in-100-year summer is rarer and
more severe than a 1-in-20-year summer, so the reported death count rises with `T`. The printed
number next to `1-in-50yr` is the **deaths in such a season**, not the period itself. Estimating a
1-in-100 tail from only 100 seasons would rest on a single draw, which is why the ensemble default
is 1000 seasons — the tail estimate is then stable. The tool never prints calendar years, because
the temperature input is synthetic and a year label would be meaningless.

## CLIMADA object mapping

| CLIMADA object | This tool |
|---|---|
| `Hazard` (`HW_u65`, `HW_o65`) | province → centroid; summer season → event; intensity = season dose `I` (`excess_RR_days`); each event 1/n_years |
| `Exposures` | one point per province; `value` = band population (persons); `impf_HW_<band>` = 1 |
| `ImpactFuncSet` | linear `mdd(I) = m0 · I`, `paa = 1` (m0 = baseline daily all-cause mortality of the band) |
| `ImpactCalc(exp, impf, haz).impact()` | `eai_exp` = expected annual attributable deaths per province; `imp_mat` rolled up by country for per-country / Europe tails |

Countries share one age-band dose-response but carry their own population, age structure,
JJA-Tmax climatology and MMT. The temperature generator adds a **Europe-wide** anomaly plus a
**per-country** anomaly each year, so provinces within a country co-vary more than across borders
(2003/2022-style continental heat) — which shapes both the per-country and the Europe return-period
curves. A single set of two `ImpactCalc` runs (one per band, over all active provinces) feeds every
rollup via the event × province death matrix.

### The dose and the impact function

For band *b*, city *p*, simulated season *s* (`H_p` = `comfort_high`, the heat-onset edge):

```
dose:   I[p,s]   = Σ_days max( exp(beta_b · (Tmax[p,s,d] − H_p)) − 1 , 0 )   [excess_RR_days]
deaths: AD[p,s]  = pop_b[p] · m0_b · I[p,s]                                   [attributable deaths]
```

Because the dose `I` already carries the full non-linear temperature response, deaths are exactly
linear in it — hence `mdd(I) = m0_b · I` through the origin. `RR_b(T) = exp(beta_b·(T−H_p))` is the
relative risk; `beta_o65 ≫ beta_u65`; `H_p` is higher in hotter, more-adapted cities.

## Observed climate: E-OBS

With an E-OBS daily-Tmax file present the hazard is built from **observations** instead of
the synthetic generator — `heat_mortality.observed_country_grid()` via
`worker/climaterisk_worker/eobs.py`. `--register` prefers it automatically (`--no-eobs`
opts out).

```bash
# open, no login (~0.8 GB, 0.25 deg regular grid, ensemble mean)
curl -sSfL -o ~/climada/data/tx_ens_mean_0.25deg_reg_v31.0e.nc \
  https://knmi-ecad-assets-prd.s3.amazonaws.com/ensembles/data/Grid_0.25deg_reg_ensemble/tx_ens_mean_0.25deg_reg_v31.0e.nc
./.climada-env/bin/python scripts/heatwave_europe.py --register      # now observed
```

What changes: E-OBS's own land grid (827 cells for Spain, vs 15 reference points), measured
per-cell climatology instead of interpolation, and every event a **real summer with its
calendar year** — so the hazard can be checked against history.

### The check that only observations make possible

Ranking Spain's 45 observed summers (1980–2024) by mean exceedance load reproduces reality:

| Rank | Summer | Load (°C-days) | Modeled deaths (full Spain) |
|---|---|---|---|
| 1 | **2022** | 241.6 | 8,876 |
| 2 | 2023 | 179.6 | 6,693 |
| 3 | 2024 | 169.9 | 5,686 |
| 4 | 2017 | 168.1 | 5,565 |
| 5 | **2003** | 165.3 | 6,189 |
| … | 1997 (coolest) | 37.0 | 1,390 |

2022 comes out first by a wide margin — it *was* Spain's hottest summer on record and
MoMo's deadliest. 2003, the other landmark European heatwave, sits in the top five. The
mean over all 45 observed summers is **3,522 deaths/yr (7.53 per 100k)**, consistent with
the synthetic run (3,323) and with MoMo's recent range.

### …and the discrepancy it exposes

The **extreme years are under-predicted by roughly 2.3×**: 8,876 modeled for 2022 against
MoMo's ~20,291, and 6,189 for 2003 against ~18,000. The mean is right while the tail is
too low, which points at specific causes rather than a global calibration error:

1. **Grid smoothing.** A 0.25° cell mean is cooler at the peak than any point inside it, and
   the dose is *exponential* in exceedance — so area-averaging shaves the extreme seasons
   far more than the ordinary ones. This is exactly the mean-right/tail-low signature.
2. **No urban-heat-island term.** Population concentrates in cities; the temperature does
   not know they are there.
3. **Attribution width.** MoMo attributes excess mortality across May–September including
   moderate-heat days; this model integrates only above the comfort band.

Closing it means calibrating β (or the band) against MoMo's *per-year* series rather than
its mean, and adding a UHI/point-extreme correction — not a change any synthetic ensemble
could have motivated, since there was nothing to disagree with.

## Data

The daily-Tmax input is a **physically-grounded synthetic** generator seeded per province from
summer climatology (JJA mean/spread of daily Tmax, a warming trend, and Europe-wide + per-country
interannual anomalies so the return-period curves are non-degenerate). It is a drop-in stand-in
for **E-OBS / ERA5** daily reanalysis: replace `_daily_tmax_series` with a NetCDF loader (same
array shape `(n_provinces, n_years, season_days)`) and the rest is unchanged.
`numpy.random.default_rng(seed)` makes every run reproducible.

Province exposure (country, coordinates, population, share ≥65, JJA-Tmax climatology, local MMT)
is in the `PROVINCES` table — realistic approximate values, not a substitute for
INE/ISTAT/INSEE + AEMET/E-OBS microdata.

## Comparison against surveillance (not a calibration)

Outputs are **compared** against the national heat-mortality surveillance systems — MoMo
(ES, ISCIII), SISMG/ISS (IT), Santé publique France (FR), RKI/UBA (DE), EODY/NOA (GR),
DGS/INSA (PT). No parameter is fitted to these series:

Per-country expected-annual deaths from the default all-country run (seed 42, 60 seasons; each
country is a **major-metro sample**, not full national coverage, so totals scale with the sampled
population, while per-capita rates are the comparable quantity):

Default run: 54 cities, **1000 simulated present-day seasons**, seed 42. All death figures are
heat-attributable deaths in the **sampled** metro population (`pop`), so **`per100k` is the
cross-country comparable quantity**; `max-yr` is the worst single season in the ensemble.

| Country | pop (M) | mean/yr | per 100k | ≥65 | max-yr | max per 100k |
|---|---|---|---|---|---|---|
| Spain | 27.2 | 2,185 | 8.03 | 97% | 8,302 | 30.5 |
| Italy | 19.0 | 2,084 | **10.98** | 97% | 8,701 | 45.8 |
| France | 21.1 | 1,630 | 7.74 | 97% | 8,520 | 40.4 |
| Germany | 10.3 | 713 | 6.92 | 97% | 3,627 | 35.2 |
| Greece | 4.9 | 306 | 6.24 | 97% | 1,543 | 31.5 |
| Portugal | 5.6 | 338 | 5.99 | 97% | 2,804 | 49.7 |
| **Europe** | **88.1** | **7,256** | **8.24** | 97% | **27,944** | — |

Europe-wide rare-season severity (return-period curve — the extremity dial):

| Return period | Heat deaths in that single season |
|---|---|
| 1-in-2 yr | 6,573 |
| 1-in-5 yr | 9,933 |
| 1-in-10 yr | 12,095 |
| 1-in-20 yr | 14,174 |
| 1-in-50 yr | 17,110 |
| 1-in-100 yr | 19,187 |

For scale, the observed 2003 European summer killed on the order of 15,000 (FR) and 20,000 (IT) —
so a 1-in-50-to-100-year season in this ensemble lands in the right order of magnitude for a
2003-class continental event across the sampled metros.

Consistency checks against the literature: a large majority of decedents are ≥65 everywhere;
2003/2018/2022 were continental-scale (the model's Europe tail pulls several countries together);
Italy and the old Spanish/Po-valley interior lead **per-capita** (top: Bologna 15.3, Zamora 15.0,
Firenze 14.2, Milano 13.7, Venezia 12.8 per 100k/yr); big metros lead **absolute** burden (Madrid,
Barcelona, Roma, Milano, Paris). Germany's cool-summer cities carry a modest per-capita rate despite
a large old population; hot-adapted Sevilla/Córdoba/Alentejo have a high, wide comfort band that
caps their dose —
the temperature-vs-adaptation interplay the model is meant to expose.

The key analytical result — **absolute burden tracks population while per-capita intensity tracks
age structure and local adaptation (MMT)** — is exactly CLIMADA's
`Risk = Hazard × Exposure × Vulnerability` decomposition made visible across six countries.

## Platform integration (`--register`)

`--register` writes **one degC heat hazard per country** (per-cell-year 95th-percentile daily
Tmax) into `data/hazard_db/` via the standardized-grid on-ramp
(`hazard_convert.convert_grid_to_catalog`) — `HW_historical_ESP_2020.hdf5`,
`…_ITA_…`, `…_FRA_…`. The worker then resolves `(heatwave, historical, <ISO3>)` from the catalog
and runs its own indicative *productivity* ramp (breakpoints 30/35/40/45 °C) — so the platform's
`heatwave` peril, which previously errored with "no local hazard", now returns a non-zero result
for assets in any of the three countries (verified: Madrid, Roma, Paris resolve to ESP/ITA/FRA).

Note the two hazards are intentionally different: the mortality tool uses an `excess_RR_days`
**dose** hazard; the catalog registration uses a **degC temperature** hazard because that is what
the platform's productivity ramp expects.

## Limitations

- **Extreme seasons are under-predicted ~2.3× against MoMo** even on observed E-OBS input
  (2022: 8,876 modeled vs ~20,291 reported). See "the discrepancy it exposes" above — grid
  smoothing of daily peaks, no urban-heat-island term, and a narrower attribution window
  than MoMo's. The mean and the year *ranking* are sound; the tail magnitude is not yet.
- With E-OBS the event set is **45 observed summers**, and the reported return-period curve
  is **capped at half the record — 22 years** (`physical._RP_RECORD_FRACTION`), because a
  1-in-N-year value estimated from an N-year record rests on a single event. The cap is
  derived from the event frequencies, not hard-coded: a longer record raises it
  automatically, and a Data-API tropical-cyclone set (tens of thousands of events) is
  unaffected. Both the applied cap and the record length are reported in
  `freq_curve.max_resolvable_return_period` / `record_years`, and the UI states them, so the
  truncation is never silent. Genuine long tails still need the synthetic generator (ideally
  re-fitted to the observed climatology), which is why both paths are kept.
- Without an E-OBS file the temperature is synthetic — magnitudes come from the generator's
  assumed climatology, not from observations, and the "years" are sequential labels, not
  calendar years.
- **The gridded hazard covers only the reference locations' bounding box**, so Spain's
  Canary and Balearic islands fall outside it: in the WorldPop run ~4% of cells (333 of
  8,245) sit further than 0.5° from any hazard centroid and are attached to a distant
  mainland one. Fixing it properly needs island reference locations (or real gridded
  climatology), not extrapolation.
- Grid-cell climatology is inverse-distance interpolated from 54 reference points and the
  comfort band is derived from it via `adaptation_fit()` — so a population raster gives real
  **exposure** resolution but the **hazard** resolution is still interpolated, not observed.
- Provinces as points, not gridded exposure; no humidity/WBGT, night-time minima, or urban-heat-
  island term (all raise elderly risk and would refine MMT).
- Single historical climate; no explicit RCP/SSP future set yet (the warming trend is a proxy).
- MMT and β are **indicative platform assumptions with no external source recorded** (`docs/HEAT_MORTALITY_PROVENANCE.md`), not province-level fitted GAMs.
- Baseline mortality and the dose-response β are shared across countries; national all-cause rates
  differ modestly and country-specific GAMs would refine the per-country totals.
