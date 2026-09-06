# CLIMADA coverage — status vs the code

Goal: expose CLIMADA's user-facing analytical capabilities through the platform. "100%"
means the decision-relevant surface (engine, exposures, vulnerability, hazards, adaptation,
uncertainty, calibration, forecasting, Data API) — not every internal util/plot variant.

**This file is verified against the code, not against intentions.** Every [x] cites the
module that implements it. Last full audit: **2026-09-06** (previous revision had drifted
badly — forecast, Sobol, raster/OSM exposures, calibration and TC surge were all marked
"planned" while already shipped). If you change a capability, update this file in the same PR.

Status legend: [x] done (code cited) · [~] partial · [ ] not implemented.

## A. Engine — impact

- [x] `ImpactCalc` → AAI, `eai_exp`, return-period curve, present↔future delta
      (`worker/climaterisk_worker/physical.py`)
- [x] **Return periods capped by the event record** — `_resolvable_return_periods`
      (cap = record_years × 0.5, surfaced as `freq_curve.max_resolvable_return_period`);
      no extrapolated 1-in-250 from a 20-year record
- [x] **Annual-loss distribution** via CLIMADA yearsets — `_yearset_summary`
      (mean/P50/P90/P95/P99/max over sampled years)
- [x] **Warn levels** via `climada_petals` Warn — `_warn_levels`
- [x] Per-result plain-language interpretation (`_interpret_result`) disambiguating
      0 = "no data / outside footprint / below threshold"
- [ ] Impact matrix / per-event drill-down surfaced to the UI
- [ ] eai/aai plot layers beyond the current per-asset map overlay

## B. Adaptation — cost-benefit  (DONE)

- [x] Measure / MeasureSet (hazard freq cutoff, MDD/PAA modifiers, risk transfer
      attach/cover) → `CostBenefit.calc` — `worker/climaterisk_worker/cost_benefit.py`
- [x] Adapt view: measure editor + per-measure benefit/cost, NPV, discount rates

## C. Uncertainty & sensitivity  (DONE — proper Sobol)

- [x] **Saltelli sampling + Sobol S1/ST** (SALib — the same engine CLIMADA's `unsequa`
      uses) over exposure value / vulnerability / hazard frequency —
      `worker/climaterisk_worker/uncertainty.py`
- [x] AAI mean/std/percentiles + histogram; present computed at base inputs
      (CalcDeltaImpact-style delta)
- Measured result to remember: **vulnerability ≈ 83 % of output variance** (Sobol run,
  see RISK_REGISTER.md C2) — uncertainty work should chase impact functions, not hazard.
- [ ] `unsequa` class API itself (CalcImpact/CalcCostBenefit objects) — equivalent
      implemented directly; only worth adopting if we need its bookkeeping

## D. Exposures  (broad)

- [x] User point assets (lat/lon, value, sector, impf class, headcount)
- [x] **Polygon / line assets drawn on the map**, sampled to grid points —
      `physical.py::_footprint_points`, MapView draw tools
- [x] LitPop (GPW Earthdata download gated — clear actionable message until provisioned) —
      `worker/climaterisk_worker/litpop.py`
- [x] **Population/value raster** (WorldPop/GHSL GeoTIFF, `Exposures.from_raster`) —
      `exposures.py` `"raster"` source (no login; the recommended default)
- [x] **OSM buildings** (osm-flex, Geofabrik `.osm.pbf`) — `exposures.py` `"osm"`
- [x] BlackMarble nightlights · GDP2Asset · Crop production — `exposures.py`
- [x] Population sources for health perils (headcount carried separately from value so
      people are never priced as money — RISK_REGISTER A13)
- [ ] Value units / deductible / cover columns on Exposures

## E. Vulnerability / impact functions

- [x] Impact-function studio (Vuln tab): per-class TC v½ / wildfire MDR / flood
      depth-damage / EQ MMI editing, session overrides flow into every run
- [x] **Authentic published presets** (one click, baked offline by
      `scripts/build_impf_presets.py`): Eberenz et al. 2021 regional TC v½ (10 regions),
      JRC Huizinga 2017 continental flood curves (6 regions), EMS-98/HAZUS-style
      indicative EQ classes (labelled as indicative)
- [x] **Calibration runner** — fit TC v½ to observed losses (EM-DAT path,
      `CLIMATERISK_EMDAT_PATH`) — `worker/climaterisk_worker/calibration.py`
- [x] European windstorm: calibrated Schwierz (default) ↔ Welker toggle
      (`windstorm_impf` option)
- [ ] **Default-by-geography switching** — presets exist but are *opt-in clicks*; a new
      asset defaults to the indicative class curve regardless of country. This is the
      platform's single largest methodology gap → `docs/GAP_ANALYSIS_KO.md` G1/G2.
- [ ] Vulnerability *bands* (e.g. Eberenz RMSF↔TDR as a range, not a point) — GAP G6

## F. Hazards  (15 perils flagged `supported_mvp`)

Native runners (`physical.py`):
- [x] tropical_cyclone — Data API present + future sets (rcp × year); catalog-first
- [x] river_flood — ISIMIP; catalog-first
- [x] wildfire — historical brightness-temperature; sigmoid impf (see GAP G3)
- [x] european_windstorm — WISC/Schwierz sets
- [x] earthquake — observed catalog, MMI classes
- [x] coastal_flood — WRI Aqueduct layers (ingest-gated, clear message)
- [x] **tc_surge** — `climada_petals` TCSurgeBathtub from TC winds + DEM, optional SLR —
      `_run_tc_surge` (bathtub caveat documented in the runner)
- [x] **tc_rain** — R-CLIPER physical rainfall ingester (`ingest.py`); runs via the
      catalog runner with an indicative ramp (GAP G4)
- [x] **heat_mortality** — exceedance degree-days hazard × age-band dose-response
      (deaths), E-OBS (Europe) + **KMA SSP 1 km onramp** (`kma_scenario.py`,
      `scripts/heat_korea.py`); heatwave (productivity) separate
- [x] Catalog perils with indicative ramps: hail, drought, low_flow, landslide,
      crop_yield, heatwave — `_run_catalog_peril` (honest "needs ingestion" errors)
- [ ] `apply_climate_scenario_knu` frequency-scaling controls (future sets preferred;
      scaling documented in METHODOLOGY.md as the fallback)
- [ ] GCM-ensemble hazard spread; event-level inspection UI

## G. Perils database (custom hazard ingestion)  (DONE, growing)

- [x] Standardized-grid → CLIMADA HDF5 converter · catalog · resolver · build CLI ·
      `/api/hazard-catalog` — `hazard_convert.py`, `catalog.py`, `scripts/build_hazard.py`
- [x] Korea onramps: KMA SSP south-Korea 1 km adapter (tests pass on spec-conformant
      synthetic files), 홍수위험지도 SHP fetcher (`scripts/fetch_floodmap_kor.py`,
      **non-commercial licence — internal validation only**), RSMC Tokyo best track
      downloaded — see RISK_REGISTER.md §E
- [ ] UI catalog browser + ingestion wizard (CLI only today)

## H. Forecast  (DONE)

- [x] `climada_petals` **TCForecast** — latest ECMWF ensemble TC tracks → wind hazard →
      portfolio impact — `worker/climaterisk_worker/forecast.py`. Off-season / no active
      TC degrades with a clear message (expected behaviour, not an error).

## I. Reporting / exports  (DONE)

- [x] TCFD/ISSB HTML report (physical + transition + cost-benefit + uncertainty run ids)

## J. Beyond CLIMADA (platform-native)

- [x] Transition risk: NGFS carbon-price passthrough (see METHODOLOGY.md)
- [x] Finance: Climate Risk Premium — NPV/IRR/DSCR/rating grids, power-generation
      capacity-factor channels (`src/climaterisk/finance/`)
- [x] Supply chain: `climada_petals` SupplyChain (MRIOT) — `supplychain.py`

## Not exposed (deliberate or open)

| CLIMADA capability | Status here | Why |
|---|---|---|
| `unsequa` class API | equivalent via SALib | adopt only for its bookkeeping |
| TCTracks synthetic-track generation UI | worker-internal | Data API sets suffice; RSMC comparison planned (RISK_REGISTER C6) |
| petals drought/landslide/low_flow native hazard builders | catalog ramps only | no vetted global set wired; honest ingestion errors |
| Impact matrix / event drill-down | not surfaced | UI work, low decision value so far |
| **Urban pluvial flood** | **structurally absent** | no global standard hazard exists — do NOT present river_flood 0 as "no flood risk" (GAP G7) |

## Status summary (2026-09-06)

DONE & verified: A engine (+RP cap, yearsets, warn) · B cost-benefit · C Sobol ·
D exposures (7 sources + footprints) · E studio + presets + calibration runner ·
F 15 perils incl. surge/rain/heat-mortality · G perils DB + Korea onramps · H forecast ·
I reporting · J finance/supply-chain.

The open items that matter are no longer *breadth* but **defaults and rigor**: geographic
auto-switching of calibrated impact functions, vulnerability bands, sub-peril combination,
and Korea-data calibration — see **`docs/GAP_ANALYSIS_KO.md`** (methodology gaps) and
**`docs/RISK_REGISTER.md`** C1–C6 (Korea data localization).
