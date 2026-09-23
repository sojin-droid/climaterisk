# Asset-level physical-risk validation (Phase 7)

> **Frozen regression baseline** — tag `v0.1.0-physical-risk-baseline` (2026-09-24). Any later change (domestic datasets, new impact functions) must keep `tests/test_asset_level_validation.py` and `tests/test_asset_level_validation_evidence.py` green against `tests/fixtures/asset_level_validation/`, or update the evidence as a deliberate, reviewed decision.

**Purpose.** Prove that the existing chain — Data API hazard → published CLIMADA impact
function → `ImpactCalc` → EAL / PML → risk level — produces correct, distinguishable results
at asset level for real Korean sites: a positive loss, a true zero, a low loss, and a
hazard-only case. Nothing was added to the methodology: no impact function, curve, formula,
multiplier or score. Every number below was produced by the engine on 2026-09-24 and is
recorded in `tests/fixtures/asset_level_validation/run_rcp85_2040_recommended.json`; the
tests in §11 re-derive or re-run them.

Run: scenario **rcp85**, target year **2040**, **Recommended** models (readiness →
`DATA_API_COUNTRY` for flood and TC, `KOREA_LOCAL` for heat). rcp85 was chosen because it
is the one scenario for which the Data API publishes flood (rcp26/60/85) *and* the local
catalog holds a KMA heatwave layer (historical/rcp45/rcp85), so flood can be `FULL` and heat
`HAZARD_ONLY` in the same run. Global sets were not touched.

## 1. Validation facilities — chosen from the hazard data, not from expectations

The sites were selected by scanning the KOR Data API sets themselves
(`river_flood_150arcsec_rcp85_KOR_2030_2050`, 480 events, 5,708 centroids, 1,401 wet;
`tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040`, 42,790 events) and then picking
real places whose nearest centroid shows the wanted behaviour. Nothing was moved to make a
number larger.

| id | facility | lat, lon | value | property_type → JRC sector | case | why this site |
|---|---|---|---|---|---|---|
| VAL-A-GWANGJU | Gwangju office (Yeongsan floodplain) | 35.16, 126.85 | $80 M | office → commercial (id 22) | **A** positive flood loss | nearest RF centroid: max depth **8.31 m**, water in 8 / 480 events — a major city on the Yeongsan river; Seoul/Busan/Incheon/Daegu/Daejeon/Ulsan cells are all dry in this product |
| VAL-B-SEOUL | Seoul office (Concordian) | 37.50, 127.00 | $100 M | office → commercial (id 22) | **B** zero flood loss | nearest RF centroid dry in **every** cached KOR set (hist, rcp26 ×2, rcp60, rcp85) |
| VAL-C-BUSAN | Busan logistics centre | 35.18, 129.08 | $60 M | warehouse → industrial (id 23) | **C** positive TC loss | max modeled wind **59.2 m/s**, 176 events above the Eberenz threshold (25.7 m/s) |
| VAL-D-CHUNCHEON | Chuncheon residential | 37.88, 127.73 | $50 M | residential (id 21) | **D** low TC loss | among the weakest inland cells: max **40.5 m/s**, 36 events above threshold. **No Korean cell is wind-free** in this product (weakest cell max 37.5 m/s), so D is *low*, not zero — and the doc says so |
| VAL-E-INCHEON | Incheon apartments | 37.46, 126.71 | $80 M | residential (id 21) | **E** heatwave hazard-only | any site: heat is hazard-only everywhere; Incheon is a third flood-dry site |

Fixture: `tests/fixtures/asset_level_validation/validation_portfolio.csv`.

## 2. Hazard sources and impact functions used

| hazard | model | dataset (version) | impact function | source |
|---|---|---|---|---|
| RF | DATA_API_COUNTRY | `river_flood_150arcsec_rcp85_KOR_2030_2050` (v3) | JRC Asia by sector: 21 residential / 22 commercial / 23 industrial | `ImpfRiverFlood.from_jrc_region_sector`, Huizinga et al. 2017 |
| TC | DATA_API_COUNTRY | `tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040` (v2.1) | 9 "North West Pacific" (WP4) | `ImpfSetTropCyclone` regional calibration, Eberenz et al. 2021, via CLIMADA's own country table |
| HW | KOREA_LOCAL | `heatwave/HW_rcp85_KOR_2030.hdf5` — KMA 남한상세 **TAMAX** SSP585 5ENSMN, season p95 | none exists | — |
| RF / TC | KOREA_LOCAL | — | — | `NOT_IMPLEMENTED` (no domestic dataset connected; licence pending) |
| HW | DATA_API_COUNTRY | — | — | `NO_HAZARD_DATA` (the Data API has no heat type) |

`impact_function_fixed = {RF: true, TC: true, HW: true}` — per facility, one function id
across the models that priced it.

## 3. Positive-loss evidence (canonical regression values)

| facility | hazard | hazard source | intensity | impact function | potential loss (100-yr) | EAL | EAL / asset | risk |
|---|---|---|---|---|---|---|---|---|
| Gwangju office | RF | Data API KOR rcp85 2030–2050 | **8.314 m** | 22 Flood Asia JRC Commercial noPAA | **$35,967,998.50** | **$597,066.65** | **0.74633 %** | **High** |
| Chuncheon residential | RF | Data API KOR rcp85 2030–2050 | 12.366 m | 21 Flood Asia JRC Residential noPAA | $8,639,999.48 | $149,499.99 | 0.29900 % | **Medium** |
| Busan logistics centre | TC | Data API KOR rcp85 2040 | 59.153 m/s | 9 North West Pacific | $405,324.72 | $15,795.42 | 0.02633 % | Low |
| Gwangju office | TC | Data API KOR rcp85 2040 | 55.165 m/s | 9 North West Pacific | $271,878.18 | $11,200.16 | 0.01400 % | Low |
| Incheon apartments | TC | Data API KOR rcp85 2040 | 53.612 m/s | 9 North West Pacific | $156,623.30 | $4,654.82 | 0.00582 % | Low |
| Seoul office | TC | Data API KOR rcp85 2040 | 52.562 m/s | 9 North West Pacific | $128,170.34 | $3,767.72 | 0.00377 % | Low |
| Chuncheon residential | TC | Data API KOR rcp85 2040 | 40.467 m/s | 9 North West Pacific | $20,370.09 | $488.21 | 0.00098 % | Low (case D: low) |

Chain checks on case A (test `test_a_positive_flood_loss_is_a_full_row_with_every_link_of_the_chain`):
`hazard_intensity > 0`, `potential_loss_usd > 0`, `eal_usd > 0`, `eal_as_pct_of_assets ==
eal / value × 100`, `risk_level == risk_level(pct)` from the configured bands,
`calculation_status = FULL`, `probability = 1/100`, requested = served = rcp85, dataset and
function recorded on the row.

Chuncheon was picked for its weak winds; the flood product also floods its Bukhan-river
cell (12.37 m), so it doubles as a second positive flood case in the Medium band. That was
not planned and was not adjusted away — it is what the data gives.

## 4. Zero-loss evidence

| facility | hazard | intensity | PML | EAL | EAL / asset | risk | status |
|---|---|---|---|---|---|---|---|
| Seoul office | RF | 0.00 m | $0.00 | $0.00 | 0.00000 % | Low | **FULL** |
| Busan logistics centre | RF | 0.00 m | $0.00 | $0.00 | 0.00000 % | Low | FULL |
| Incheon apartments | RF | 0.00 m | $0.00 | $0.00 | 0.00000 % | Low | FULL |

These are **computed zeros**: the impact function was resolved (id 22 / 23 / 21), the
hazard was assigned, `ImpactCalc` ran, and the nearest centroid carries no depth in any of
the 480 events. They are distinct from `NO_HAZARD_DATA`, `NOT_IMPLEMENTED` and
`NO_IMPACT_FUNCTION`, which carry `null` and never `0` (`ResultRow.finalise`).

## 5. HAZARD_ONLY evidence (heat)

| facility | intensity (season p95 Tmax, KMA TAMAX SSP585, 2021–2030) | financial fields | status |
|---|---|---|---|
| Gwangju | 37.55 °C | all null | HAZARD_ONLY |
| Seoul | 37.87 °C | all null | HAZARD_ONLY |
| Busan | 34.69 °C | all null | HAZARD_ONLY |
| Chuncheon | 37.21 °C | all null | HAZARD_ONLY |
| Incheon | 36.64 °C | all null | HAZARD_ONLY |

No heat impact function exists in the installed CLIMADA 6.1.0 / Petals 6.2.0
(`registry.heat_status()`); none was created. Under `DATA_API_COUNTRY` heat is
`NO_HAZARD_DATA` (the Data API has no heat type). The UI shows **"Financial loss: Not
available (not $0)"** with the reason; the Excel cells are blank with the status beside them.

## 6. PML / return-period guard evidence (real data)

The observed-only KOR TC set (`tropical_cyclone_0synth_tracks_150arcsec_historical_KOR_1980_2020`,
3,890 events) has `min(frequency) = 0.02439 /yr` → ceiling **41.0 years**. Pricing the
Busan facility against it:

```
calculation_status = RETURN_PERIOD_NOT_RESOLVABLE
eal_usd            = 17,756.31      (kept: EAL needs no extrapolation)
potential_loss_usd = null           (no fake 100-year PML)
detail: requested 100-year loss exceeds the 41.0-year ceiling this event set can express …
```

What CLIMADA alone would have returned: `calc_freq_curve([41, 100, 250]) → 394,016 /
394,016 / 394,016` — the largest event's loss under every label past the ceiling. The
guard is what stops that number appearing as a "100-year PML".
Tests: `test_return_period_guard_on_the_real_observed_tc_set` (real),
`test_an_unresolvable_return_period_is_refused_not_saturated` (synthetic).

## 7. Risk-threshold evidence

Bands from `assets/libraries/physical_risk_config.json` (project methodology, GRESB-informed,
**not** an official GRESB threshold): Low `< 0.10 %`, Medium `0.10–< 0.50 %`, High `≥ 0.50 %`.
Exact edges (tests): 0.0999 → Low, 0.10 → Medium, 0.4999 → Medium, 0.50 → High.
Real non-zero ratios in this run land in **all three bands**: 0.74633 % High (Gwangju
flood), 0.29900 % Medium (Chuncheon flood), 0.02633 % … 0.00098 % Low (every TC row).

## 8. Batch evidence

One call, 5 facilities × 3 hazards × 2 models = **30 rows**, every (facility, hazard,
model) exactly once; **6 adapters** loaded — one per hazard × model, not one per asset;
7 s on cached data. Recommended mode used `DATA_API_COUNTRY` + `KOREA_LOCAL` only: no
`GLOBAL_BASELINE` adapter ran and no dataset with a `global` token was fetched
(`adapters` list in the evidence file).

## 9. Excel evidence

Workbook from the same rows (`excel_export.workbook_bytes`): sheets **Portfolio Summary,
Asset Risk Matrix, Hazard Results, Global vs Country, Global vs Korea Local, Methodology,
Run Info** — no *Climate Change* sheet because no baseline scenario was run.
Checked cell by cell (`test_excel_from_the_evidence_keeps_zero_blank_and_status_apart`):

| case | cell | value |
|---|---|---|
| Seoul flood (computed zero) | Hazard Results `eal_usd`, `potential_loss_usd` | `0`, `0`; status `FULL`; Portfolio Summary `Flood EAL` = `0` |
| Incheon heat (hazard-only) | `eal_usd`, `risk_level` | blank; `hazard_intensity` present; status `HAZARD_ONLY`; Portfolio Summary `Heatwave Status` = "Hazard only" |
| Gwangju local flood (not implemented) | `eal_usd` | blank; status `NOT_IMPLEMENTED` |
| Gwangju flood (positive) | `eal_usd`, `risk_level`, `impact_function_id` | `597,066.65`, `High`, `22` |

No `None` became `0`; every `0` is a computed zero.

## 10. UI interpretation evidence

Browser run of the same five assets (rcp85, Recommended): the asset matrix shows
**High** (Gwangju flood), **Medium** (Chuncheon flood), **Low** (all other priced cells) and
**Hazard only** (every heat cell), each colour-coded from the computed level; the asset
detail shows EAL / EAL ÷ Asset Value / Potential Loss with the data source, and for heat
"Financial loss **Not available** (not $0)" with the reason "no applicable CLIMADA Impact
Function"; **Why?** opens the band copy (quoting the configured threshold), the method
chain, the impact function and the dataset. "Not available" appears for the KOREA_LOCAL
flood/TC rows in the technical table with status `NOT_IMPLEMENTED`.

## 11. Tests

| file | env | what |
|---|---|---|
| `tests/test_asset_level_validation_evidence.py` | backend (CLIMADA-free) | the recorded evidence obeys every rule (cases A–E, thresholds, batch shape, Recommended, no Global), the non-expert views read it correctly, the Excel keeps 0 / blank / status apart |
| `tests/test_asset_level_validation.py` | worker | re-runs the fixture on the cached Data API sets and compares every number to the evidence (rel 1e-6); PML guard on the real observed set; skips when the cache is unavailable, never downloads Global |
| existing | both | boundary tests (`test_risk_level_boundaries`), synthetic guard, engine, adapters, legacy regression |

## 12. Completion

```
[x] PR #24 merged                       [x] PML guard verified (real + synthetic)
[x] positive Flood loss fixture (A)     [x] batch execution verified (30 rows, 6 adapters)
[x] zero Flood loss fixture (B)         [x] Recommended mode verified (no Global)
[x] positive TC loss fixture (C)        [x] Excel verified (0 / blank / status)
[x] low TC fixture (D — low, not zero)  [x] UI interpretation verified (browser)
[x] Heatwave hazard-only fixture (E)    [x] regression suite passes
[x] EAL / EAL÷assets / Risk Level verified
```

Not done here, on purpose: no Korean hazard datasets were solved, no Korea-specific or
heat impact functions were created, no risk score was introduced, and no site was
fabricated — where the data gave a zero, the zero is reported as a zero.
