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
with zero clicks**. Since 2026-09-07 that default is **geography-aware**: when the asset's
country is listed on a bundled regional preset (Eberenz 2021 for TC, JRC 2017 for the flood
family) the preset replaces the class value; otherwise the generic class curve is the fallback
(`worker/climaterisk_worker/vulnerability.py`; `options.tc_impf_default` /
`flood_impf_default` = `class` restores the old behaviour). Studio overrides always win.

| Peril | Hazard | Vulnerability default | Maturity / caveat |
|---|---|---|---|
| Tropical cyclone | Data API synthetic sets, present + rcp×year future | Emanuel `v_half` = **Eberenz 2021 regional preset by country** (e.g. KOR/JPN → WP4 190.5, USA → NA2 89.2); class value (residential 70, infra 110, indicative) only where no region lists the country or in `class` mode | Regional default not yet validated on Korean losses (RISK_REGISTER C2/C5); single TDR point, no RMSF↔TDR band → GAP G6 |
| River flood | ISIMIP depth | **JRC 2017 regional residential preset by country** (KOR → Asia, DEU → Europe …); the class curve (JRC-Europe-like to 4 m) is the generic fallback | Residential sector only; Korea floodmap onramp in progress (RISK_REGISTER C3); not validated |
| Coastal flood / SLR | WRI Aqueduct (ingest-gated) | same flood curve family (regional JRC preset by country) | residential sector only; single GCM |
| TC storm surge | petals `TCSurgeBathtub` (wind + DEM, opt. SLR) | flood depth-damage | bathtub = no hydrodynamics, **no seawalls**; separate peril — not event-combined with wind (GAP G5) |
| TC rainfall | R-CLIPER ingester (physical mm) | **indicative ramp** | catalog-ramp peril → GAP G4 |
| Wildfire | historical brightness temperature | sigmoid `x0=325 K` from 295 K, per-class max MDD | **no ignition threshold** — overstates low-intensity seasons vs the Lüthi-calibrated form → GAP G3 |
| European windstorm | WISC/Schwierz sets | calibrated **Schwierz** (default) ↔ Welker toggle | the one peril with a calibrated default |
| Earthquake | observed catalog | EMS-98/HAZUS-style MMI classes | labelled indicative; no published CLIMADA impf exists |
| Heat mortality | exceedance degree-days above a **cited threshold** (93rd percentile of the local summer daily-mean distribution, Kim 2020) on daily **mean** temperature (E-OBS `tg` / KMA `TA`; requested scenario layer if registered, else `historical` with an explicit fallback note) | **climaterisk custom** age-band dose-response over a comfort *band* — CLIMADA ships no heat-mortality impact function | **dose-response parameters indicative / unknown provenance; Korea-specific baseline mortality KOSIS-anchored (2024); none calibrated** (§ below, `HEAT_MORTALITY_PROVENANCE.md`); summer ranking reproduced vs observed 2022 Europe but ~2.3× under on extreme years (RISK_REGISTER B2); Korea not validated |
| Heatwave / drought / low_flow / hail / landslide / crop_yield | local catalog only | **indicative ramps** scaled by the class `wf_max_mdd` | honest "needs ingestion" errors; ramps are not calibrated → GAP G4 |

Cross-cutting rigor already in place: return periods capped at half the event record;
yearset annual-loss distributions; Sobol sensitivity (TC only; one recorded Korean run gave
vulnerability ≈ 83 % of variance under the platform's fixed bounds); non-monetary results
excluded from currency aggregation; result interpretation strings disambiguating every zero.

**Methodology gaps and their fixes are tracked in `docs/GAP_ANALYSIS_KO.md`** (defaults,
bands, sub-peril combination, pluvial absence) and **`docs/RISK_REGISTER.md`** (Korea data).
**Which parts are CLIMADA-native vs platform-custom, per peril and per component, is audited in
`docs/CLIMADA_METHODS.md`** (2026-09-07).

### Heat (HW), Korea-local tier T2 — open-field UTCI (trial, `heat_utci.enabled = false`)

Hazard only (no loss). For each asset point: hourly KMA ASOS observations (air temperature,
humidity, 10 m wind, global radiation `icsr`) of the nearest station with at least 15 valid
years 2000-2019 (May-Sep); air temperature shifted by the 0.05 deg MK-PRISM v3.1 daily-maximum
difference between the asset cell and the station cell, humidity recomputed at constant vapour
pressure; mean radiant temperature by the ERA5-HEAT formula (Di Napoli et al. 2020; Erbs split
of the radiation, Prata sky emissivity with a Crawford-Duchon cloud fraction, ground temperature
$T_a + a + b\,G$ fitted on 2010-2019 observations); UTCI by `pythermalcomfort` 4.8.0 (Brode et al.
2012). Headline = mean days per year with daily-max UTCI >= 38 degC, reported with the albedo
0.15-0.25 sensitivity range (a range for one assumption, not a confidence interval). Assumptions
A1-A6 are listed in `adapters.KoreaLocalUtciAdapter.describe()`; shade, buildings and indoors are
not modelled (T3). Only the historical period is served. Sub check (diagnostic only): yearly rank
agreement with grid heat days and station distance per asset (C1, C3); hourly vs grid heat days
once per station at the station's own cell (C2, a memo that never enters an asset's stamp,
because the asset shift cancels out of that comparison). Seoul 108 baseline: `tests/baselines/heat_utci_seoul108.json` (a regression guard, not
a validation). Design: `<DATA_ROOT>/derived/design_utci/DESIGN_UTCI_T2.md`; code:
`worker/climaterisk_worker/heat_utci.py`.

### Heat mortality — framework, custom parts and status

**Model framework (CLIMADA).** The risk calculation uses CLIMADA as container and engine:
`ImpactFunc` and `ImpactFuncSet` hold the curves, `ImpactCalc` does
`impact = value × mdd × paa` over the event set, and the standard outputs (`aai_agg`,
`at_event`, the frequency curve, `Warn` bands) are CLIMADA's.

**Vulnerability model (climaterisk).** The heat-mortality dose-response is **this
repository's own implementation**, not a CLIMADA-provided function: CLIMADA core ships
impact functions for tropical cyclone and European windstorm, petals for drought, crop
yield, river flood and wildfire — none for heat, mortality or health. Concretely custom:
the minimum-mortality comfort band and its width, the age stratification (<65 / ≥65), the
relative-risk slopes β, the baseline mortality rates folded into `mdd`, and the
degree-days → dose power law.

**Current status (2026-09-11).** Of the 24 constants inventoried, **6 record an external
source** — the exposure metric (daily mean temperature), the adaptation slope (0.8 degC per
degC, Tobías et al. 2021), the Korean heat-onset threshold (93rd percentile of the summer
daily-mean distribution, Kim 2020), Korea's 65+ share, and **Korea's two baseline mortality
rates** (KOSIS 사망원인통계 DT_1B34E01, 2024: 163.2 and 2,928.4 deaths per 100,000
person-years for <65 / 65+; `age_bands_for("KOR")`) — 2 are internal fits, and **16 remain
indicative platform assumptions or of unknown provenance**. Outside Korea the baseline pair
is still the uncited legacy one. What is still uncited everywhere is the dose-response
itself: β per age band and the comfort band. The published Korean estimates are
heatwave-episode relative risks at percentile cut-offs with a lag structure, not per-day
slopes per degC, so they cannot be converted into β by arithmetic alone. The KOSIS-anchored
baseline is a cited level for Korea, not a validation of the Korean model: the age-group
exposure share (0.203, resident registration) and the mortality table are separate sources
whose demographic consistency is not yet unified. None has been calibrated against observed mortality,
Korean or otherwise. The Spain comparison against MoMo is a *comparison*: no parameter was
fitted to a surveillance series. Every value, its location in the code and its status is
listed in **`HEAT_MORTALITY_PROVENANCE.md`**, mirrored in code as
`heat_mortality.PARAMETER_PROVENANCE`, and reported at run time in the result's
`parameter_status` so the Results card carries the caveat next to the number.

Consequence for use: read per-100k rates and the ranking of locations; do not read absolute
death counts as estimates of reality until the B-stage parameter work
(`HEAT_MORTALITY_PROVENANCE.md` §6) is done.

## River flood headline for Korea — official map x ISIMIP change factor (method tier T2)

`KOREA_LOCAL` x RF is the river-flood headline since 2026-11-15 (comparison:
`docs/evidence/river_headline_comparison_2026-11-15.json`). Present climate = the 환경부 홍수위험지도
depth classes as they are. A future scenario keeps that depth and multiplies every event frequency
by one national ISIMIP factor $f = \sum_c EAL_c^{\text{window}} / \sum_c EAL_c^{2010\text{–}2030}$
(unit exposure per KOR centroid, JRC Asia residential curve, same RCP; ASCII: `f = EAL_window /
EAL_2010_2030`, dimensionless). Climate change enters as frequency only; per-시도 factors are a
diagnostic sub check (`flood_delta.regional_check`). See `KOREA_FLOODMAP_INVESTIGATION.md` §10.

## Korea-local coastal flood — connected bathtub (method tier T2, hazard-only)

Models tab, `KOREA_LOCAL` x CF (`worker/climaterisk_worker/coastal_connectivity.py`). The
headline coastal flood stays Aqueduct (`GLOBAL_BASELINE`); this cell reports a hazard, never a loss.

$$ Z_{AHHW} = \frac{A_{M2}+A_{S2}+A_{K1}+A_{O1}}{100},\qquad
d = \max\left(0,\ Z_{AHHW}+s-z_{ground}\right)\ \text{if the 90 m cell is tidal, else } 0 $$

ASCII: `ahhw_m = (M2+S2+K1+O1 amplitudes, cm) / 100`; `depth = max(0, ahhw + slr - ground)` when
the asset's 90 m cell is sea-connected, else 0. $A_c$ = KHOA 조화상수 amplitude of constituent $c$
(cm) at the nearest station; $s$ = sea-level-rise increment (m; 0, 0.5, 1.0 in
`coastal_local.slr_increments_m`); $z_{ground}$ = ground above MSL (m).

1. Terrain: Copernicus GLO-30 reduced to 3 arcsec (~90 m) by the block minimum, which removes
   barriers narrower than ~90 m. Cache: `cache/glo30_blockmin/*_min3.npy` / `_max3.npy`.
2. Sea = outside the SGIS 시도 land polygons, or no elevation.
3. Tidal = cell below the station's AHHW (+ increment) whose 8-connected component of
   low-or-sea cells contains sea (`scipy.ndimage.label`). The threshold is the nearest station's
   AHHW evaluated per ~1.8 km block.
4. Ground at the asset: bare-earth NGII DEM where it covers the point, else GLO-30 (a surface model).
   Unknown ground gives unknown connectivity and depth (null), never 0.

Assumptions (stated on the row): structures narrower than ~90 m (seawalls, levees) are removed;
nearest-station AHHW; present-day tidal datum; no storm surge, no waves; no loss computed.
Sub check (diagnostic only): the slr-0 test on the block maximum keeps those structures;
`narrow_only` counts assets connected only because the structures are assumed to fail, and warns.
Per-asset results are in memory and in the run record only.

## Korea-local urban pluvial flood — official 도시침수지도 (method tier T2, hazard-only)

Models tab, `KOREA_LOCAL` x PF (`KoreaLocalPluvialAdapter`; `floodmap_depth.URBAN_FAMILIES`,
`flood_traces.py`). The global and Data API cells have no pluvial hazard (`NO_HAZARD_DATA`).

$$ d_i = \bar h\big(\max\{c : p_i \in P_c\}\big)\ \text{(m)},\qquad
POD = \frac{n_{hit}}{n_{obs}},\quad S = \frac{n_{hit}}{n_{map}} $$

ASCII: `depth = band midpoint of the deepest class polygon covering the asset, else 0`;
`pod = n_hit / n_observed`; `share_mapped_observed = n_hit / n_mapped_wet`. $P_c$ = polygons of
class $c$ (SEG_CODE N330–N334 = ≤0.5, 0.5–1, 1–2, 2–5, ≥5 m; order inferred from the river maps),
$\bar h$ = representative depth (m; `flood_map` config), $n_{obs}$ = covered assets with a trace,
$n_{map}$ = covered assets the map floods, $n_{hit}$ = both.

One 100-y event (frequency 0.01), present day only, no loss computed. Sub check: DEM edge depth vs
the band (shared with river flood). Validation (diagnostic, no verdict): 서울시 침수흔적도
2010–2025, `F_SHIM` = depth (m, meaning inferred from the name and values), polygons within
`pluvial_local.trace_buffer_m` of the asset, only assets inside 서울특별시. A 100-y map against
observed 2010–2025 events is not like-for-like, so `pod` and `share_mapped_observed` are reported
without a threshold. CFM polygons: 공공누리 제4유형, overlaid in memory only.

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
