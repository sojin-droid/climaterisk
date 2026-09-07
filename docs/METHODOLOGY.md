# Methodology & data provenance

This platform orchestrates established open-source methods rather than inventing climate science.
Every bundled dataset must be recorded here with its **source** and **license** before it is used
in a published result.

## Physical risk — CLIMADA

- **Engine:** [CLIMADA](https://climada-python.readthedocs.io) (ETH Zürich), **GPL-3.0**. Risk =
  Hazard × Exposure × Vulnerability; probabilistic, event-based. Run in a separate conda worker.
- **Outputs used:** `aai_agg` (average annual impact), `eai_exp` (per-asset, → map layer),
  `calc_freq_curve()` (return-period curve).

### Future estimation (important)

CLIMADA has **no embedded climate model**. Future hazard is obtained per peril and frozen into the
bundled library:

| Peril | Future-hazard source |
|---|---|
| Tropical cyclone | CLIMADA Data API future sets (rcp26/45/60/85 × 2040/2060/2080); or `apply_climate_scenario_knu` (**frequency-only** scaling — Knutson 2020 projections converted per Jewson 2021, frequency-only per Jewson 2022; opt-in via `options.tc_future_method="knutson"`) |
| River flood | ISIMIP future flood depth/fraction |
| Coastal flood / SLR | WRI Aqueduct coastal-flood future sets (rcp × ref_year) |
| Heat mortality / heatwave | **Present climate only in the platform runner** (E-OBS observations; KMA SSP layers can be registered but `heat_mortality` resolves only `historical` — see CLIMADA_METHODS.md §5.8) |
| Wildfire / drought / low_flow / landslide / earthquake | **No future set** — historical or observed hazard only; `present_aai_agg`/`delta_pct` are `None` |

Time: each runner uses **`max(anchor_years)` snapped to the nearest year the source offers**
(TC 2040/2060/2080; river flood ISIMIP windows; catalog nearest registered year). There is **no**
interpolation between anchor years and **no** baseline blending — present and future are two
independent `ImpactCalc` runs and `delta_pct = (future − present)/present` on AAI. Uncertainty is a
**platform Sobol analysis (SALib, TC only)**, not CLIMADA `unsequa` — see
`docs/CLIMADA_METHODS.md` §6–§7 for the verified method. The `PhysicalEngine` interface allows
adding **physrisk** (Apache-2.0, native `(scenario, year)` hazards) later.

## Transition risk (Phase 3)

- **Scenarios:** NGFS Phase 5 (Nov 2024) — frozen carbon-price + emissions snapshot, extracted via
  `pyam` to IAMC CSV. CC-BY with attribution; redistribution of substantial raw portions restricted.
- **Emission factors:** EDGAR 2024 (CC BY 4.0) — Scope-1 proxy when an asset reports no emissions.
- **Core calc:** carbon-cost passthrough `cost(t) = emissions × carbon_price(t, scenario)`,
  sector-adjusted; rolled up to EBITDA/value impact.
- **Benchmarks (later):** SBTi/PACTA sector pathways; CRREM real-estate stranding pathways.

## Perils database (local hazard catalog)

Beyond the live CLIMADA Data API, the platform can ingest **real source data** into a
local CLIMADA-ready hazard catalog (`data/hazard_db/`). A source is reduced to a
*standardized observation grid* (cells × years × intensity) and converted to a CLIMADA
`Hazard` HDF5 via `scripts/build_hazard.py` — faithfully (per-cell exceedance frequency
equals the source's annual exceedance probability). The worker resolves catalog hazards
before the Data API, which is how coastal flood / heat / drought / custom local sources
are added. See `docs/ARCHITECTURE.md` → *Perils database*. Record each ingested source's
provenance + licence in the catalog entry's `source`/`license` fields.

## Per-peril methodology (as implemented — verified 2026-09-06)

What each peril actually computes today. "Vulnerability default" is what a **new asset gets
with zero clicks**; published presets exist in the Vuln studio but are opt-in.

| Peril | Hazard | Vulnerability default | Maturity / caveat |
|---|---|---|---|
| Tropical cyclone | Data API synthetic sets, present + rcp×year future | Emanuel `v_half` per class (residential **70**, infra 110) — **indicative, not regional** | Eberenz 2021 regional presets exist but are opt-in → GAP G1 |
| River flood | ISIMIP depth | JRC-style depth-damage per class — the residential default matches the JRC **Europe** residential curve to 4 m (differs at 5–6 m: .92/.95 vs .95/1.0) | Region misapplication outside Europe → GAP G2; Korea floodmap onramp in progress (RISK_REGISTER C3) |
| Coastal flood / SLR | WRI Aqueduct (ingest-gated) | same flood curve family | same G2 caveat |
| TC storm surge | petals `TCSurgeBathtub` (wind + DEM, opt. SLR) | flood depth-damage | bathtub = no hydrodynamics, **no seawalls**; separate peril — not event-combined with wind (GAP G5) |
| TC rainfall | R-CLIPER ingester (physical mm) | **indicative ramp** | catalog-ramp peril → GAP G4 |
| Wildfire | historical brightness temperature | sigmoid `x0=325 K` from 295 K, per-class max MDD | **no ignition threshold** — overstates low-intensity seasons vs the Lüthi-calibrated form → GAP G3 |
| European windstorm | WISC/Schwierz sets | calibrated **Schwierz** (default) ↔ Welker toggle | the one peril with a calibrated default |
| Earthquake | observed catalog | EMS-98/HAZUS-style MMI classes | labelled indicative; no published CLIMADA impf exists |
| Heat mortality | exceedance degree-days (E-OBS obs / KMA SSP onramp) | age-band dose-response, comfort *band* | validated vs 2022 Europe ranking; ~2.3× under on extreme years (RISK_REGISTER B2) |
| Heatwave / drought / low_flow / hail / landslide / crop_yield | local catalog only | **indicative ramps** scaled by the class `wf_max_mdd` | honest "needs ingestion" errors; ramps are not calibrated → GAP G4 |

Cross-cutting rigor already in place: return periods capped at half the event record;
yearset annual-loss distributions; Sobol sensitivity (TC only; one recorded Korean run gave
vulnerability ≈ 83 % of variance under the platform's fixed bounds); non-monetary results
excluded from currency aggregation; result interpretation strings disambiguating every zero.

**Methodology gaps and their fixes are tracked in `docs/GAP_ANALYSIS_KO.md`** (defaults,
bands, sub-peril combination, pluvial absence) and **`docs/RISK_REGISTER.md`** (Korea data).
**Which parts are CLIMADA-native vs platform-custom, per peril and per component, is audited in
`docs/CLIMADA_METHODS.md`** (2026-09-07).

## Known data caveats

- **IEA WEO** free dataset is **non-commercial** — do not bundle for commercial use without a license.
- **Asset-level production data** for full PACTA alignment is not freely available → design for
  user-supplied data.
- **홍수위험지도 SHP** (Korea floodmap): non-commercial, no-derivatives — internal validation only.
- **EM-DAT**: commercial use requires a paid licence; 재해연보 API has no such restriction.

## Current status (revised 2026-09-06)

Earlier revisions called all `assets/libraries/*.json` values "PLACEHOLDER". That is now
**only partially true** — be precise about which is which:

- **Authentic (published, citable):** `impact_function_presets.json` — Eberenz 2021 regional
  TC v½, JRC Huizinga 2017 continental flood curves (regenerated from CLIMADA by
  `scripts/build_impf_presets.py`); NGFS carbon prices; windstorm Schwierz/Welker.
- **Indicative (must not be presented as calibrated):** the per-class *defaults* in
  `impact_functions.json` (TC v½ 70–110, flood/EQ class curves, `wf_max_mdd`),
  `finance_channels.json`, sector emission intensities. These drive results whenever the
  user does not pick a preset — which is why GAP G1/G2 (geographic auto-defaults) matter.
