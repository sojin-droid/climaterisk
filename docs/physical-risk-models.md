# Physical-risk models — global / country / Korea-local hazard replacement

Phase 3 of the CLIMADA-based real-estate physical-risk engine. One facility, one hazard,
**one published impact function**, three hazard sources. Everything in this document is
read off the code in `src/climaterisk/physical_risk/` and
`worker/climaterisk_worker/physical_risk/`; the tests named in §14 fail if the two drift.

```text
GLOBAL_BASELINE   = global reference calculation
DATA_API_COUNTRY  = country-specific Data API calculation
KOREA_LOCAL       = domestic-source calculation
```

**DATA_API_COUNTRY is not treated as Korea-local source data.** It is the CLIMADA Data API
product with `spatial_coverage=country` — the same model output as the global set, cut to
one ISO3. **Korea-local results are reported only when a domestic hazard dataset and
compatible adapter are actually available.** Today that is heat (KMA, hazard-only); flood
and tropical cyclone have the adapter interface and nothing behind it.

## 1. The three models

| model_id | hazard | exposure | impact function |
|---|---|---|---|
| `GLOBAL_BASELINE` | CLIMADA Data API, `spatial_coverage=global` | user portfolio | published CLIMADA function |
| `DATA_API_COUNTRY` | CLIMADA Data API, `spatial_coverage=country`, `country_iso3alpha=KOR` | **same** | **same** |
| `KOREA_LOCAL` | domestic source through a dedicated adapter (환경부 홍수위험지도, KMA, 국내 공식 태풍 자료) | **same** | **same** |

The definitions are code: `MODEL_DEFINITIONS` in
[`metrics.py`](../src/climaterisk/physical_risk/metrics.py), repeated verbatim on the
`methodology` export sheet.

## 2. Invariants

1. **The impact function is fixed across models.** Flood uses
   `ImpfRiverFlood.from_jrc_region_sector(region, sector)` (Huizinga 2017, 11 points,
   0–12 m); TC uses the Eberenz 2021 regional function CLIMADA's own country table assigns
   (`KOR → id 9, WP4 'North West Pacific'`). No curve is authored, resampled or
   re-calibrated in this phase. The runner reports `impact_function_fixed[hazard]` and every
   comparison row carries `same_impact_function`.
2. **The exposure is fixed across models** — the user's facilities (`lat`, `lon`,
   `asset_value_usd`, `property_type`). LitPop is allowed as a *separately labelled*
   exposure; population is never used as an asset-value proxy.
3. **A number that cannot be computed is `null`, never `0`.** `ResultRow.finalise` clears
   every financial field under a non-financial status.
4. **Requested and served scenario are two fields.** A dataset that answers `rcp45` with
   `rcp60` is recorded as exactly that and the row is `SCENARIO_MISMATCH`.
5. **Percent and percentage points are different units**, and **a model-vs-model ratio is
   not a climate-change multiplier.**

## 3. Model ids

One `StrEnum`, one place:

```python
class ModelId(StrEnum):            # src/climaterisk/physical_risk/metrics.py
    GLOBAL_BASELINE = "GLOBAL_BASELINE"
    DATA_API_COUNTRY = "DATA_API_COUNTRY"
    KOREA_LOCAL = "KOREA_LOCAL"
```

The Phase 2 ids `KOREA_HAZARD` / `KOREA_HAZARD_EXPOSURE` were removed: `KOREA_HAZARD` could
not distinguish a country cut of the Data API from a domestic source, which is the whole
point of this phase. `test_exactly_three_canonical_model_ids_and_no_stray_strings` scans
the backend package for any other spelling.

## 4. Hazard adapters

```text
HazardAdapter.describe()  ->  HazardDescription (source, dataset, version, country,
                              requested_scenario, served_scenario, horizon, status)
HazardAdapter.load(bbox)  ->  canonical CLIMADA Hazard | None
                                   ↓
                    engine.calculate(facility, hazard, tag, model_id=...)
                                   ↓
                    ImpactCalc(exposure, ImpactFuncSet([same function]), hazard)
```

[`adapters.py`](../worker/climaterisk_worker/physical_risk/adapters.py):

| hazard | `GLOBAL_BASELINE` | `DATA_API_COUNTRY` | `KOREA_LOCAL` |
|---|---|---|---|
| RF | `GlobalFloodAdapter` — READY | `CountryFloodAdapter` — READY | `KoreaLocalFloodAdapter` — **NOT_IMPLEMENTED** |
| TC | `GlobalTCAdapter` — READY | `CountryTCAdapter` — READY | `KoreaLocalTCAdapter` — **NOT_IMPLEMENTED** |
| HEAT | `_NoHeatDataAdapter` — NO_HAZARD_DATA | `_NoHeatDataAdapter` — NO_HAZARD_DATA | `KoreaLocalHeatAdapter` — HAZARD_ONLY |

Data API adapters fetch **by dataset name**, built deterministically by
`dataapi_dataset_name(hazard, scenario, year, coverage, iso3)`:

```text
river_flood_150arcsec_rcp60_2030_2050          GLOBAL_BASELINE   RF rcp60 2050
river_flood_150arcsec_rcp60_KOR_2030_2050      DATA_API_COUNTRY  RF rcp60 2050
tropical_cyclone_10synth_tracks_150arcsec_rcp45_global_2040
tropical_cyclone_10synth_tracks_150arcsec_rcp45_KOR_2040
```

The two names differ only by the country token — that is the entire difference between
the two models. The loaded set is cropped to the portfolio bounding box (+1°); cropping
does not change the priced number (`test_cropping_a_hazard_to_the_facility_does_not_change_the_price`,
and measured: 5,708 → 1,492 centroids, EAL identical to the last digit).

`NOT_IMPLEMENTED` adapters exist so the model is addressable: `load()` returns `None`
without raising, downloading or copying anything, and the runner emits rows with
`calculation_status = NOT_IMPLEMENTED` and every number `null`. The 환경부 홍수위험지도 is
not on disk and its 공공누리 제4유형 licence is unresolved; nothing is fetched until it is
(see [`korea-hazard-replacement.md`](korea-hazard-replacement.md)).

## 5. Readiness

[`models.py`](../src/climaterisk/physical_risk/models.py) is the single table both sides
read; `test_adapter_statuses_match_the_backend_readiness_table` asserts the worker's
adapters agree with it. Served at `GET /api/libraries/physical-risk-models`.

```json
{
  "RF":   {"GLOBAL_BASELINE": "READY",          "DATA_API_COUNTRY": "READY",          "KOREA_LOCAL": "NOT_IMPLEMENTED"},
  "TC":   {"GLOBAL_BASELINE": "READY",          "DATA_API_COUNTRY": "READY",          "KOREA_LOCAL": "NOT_IMPLEMENTED"},
  "HEAT": {"GLOBAL_BASELINE": "NO_HAZARD_DATA", "DATA_API_COUNTRY": "NO_HAZARD_DATA", "KOREA_LOCAL": "HAZARD_ONLY"}
}
```

The heat row differs from the brief's illustration on purpose: the CLIMADA Data API has
**no heat hazard type** and the platform has **no global heat layer**, so the two Data API
cells are `NO_HAZARD_DATA`, not `HAZARD_ONLY`. Only KMA-derived heat exists, and it is
hazard-only because CLIMADA ships no heat impact function.

## 6. Scenario — requested vs served

The Data API publishes flood for `historical / rcp26 / rcp60 / rcp85` and TC for every
platform scenario. The platform's default `rcp45` therefore has **no flood dataset**; the
existing `RF_SCENARIO_MAP` sends it to `rcp60`. The legacy catalog files that fetch under
the requested key (`RF_rcp45_KOR_2050.hdf5`, source "rcp60") — the very case the brief
forbids. This engine records both:

```text
requested_scenario = rcp45
served_scenario    = rcp60
calculation_status = SCENARIO_MISMATCH      financial fields null, provenance kept
```

Ask for `rcp60` (or `rcp26` / `rcp85`) and the same row is `FULL`. The mismatch is
decided in `ResultRow.finalise`, so no caller can skip it. The `scenario` table column
mirrors `requested_scenario`.

## 7. Canonical result schema

`ResultRow` ([`metrics.py`](../src/climaterisk/physical_risk/metrics.py)); the worker
emits `to_dict()`, the backend rebuilds with `from_dict()`.

```text
facility_id  facility_name  hazard_type  model_id

hazard_source  hazard_dataset  hazard_data_version  hazard_country
impact_function_id  impact_function_name  impact_function_source
exposure_source  exposure_version

requested_scenario  served_scenario  scenario  time_horizon
hazard_intensity  hazard_intensity_unit
probability  return_period_years
asset_value_usd  asset_value_currency
potential_loss_usd  potential_loss_return_period_years
eal_usd  eal_as_pct_of_assets
risk_level  risk_level_criteria
climate_change_multiplier          (same-model pairing only, see §9)
calculation_status  status_detail  confidence
property_type  floor_area_m2  year_built  structure_type
```

Statuses: `FULL`, `HAZARD_ONLY`, `NO_IMPACT_FUNCTION`, `NO_HAZARD_DATA`, `NO_EXPOSURE_DATA`,
`RETURN_PERIOD_NOT_RESOLVABLE` (keeps EAL, nulls PML), `SCENARIO_MISMATCH`,
`NOT_IMPLEMENTED`, `ERROR`. All but `FULL` and `RETURN_PERIOD_NOT_RESOLVABLE` null every
financial field.

## 8. Comparison

`compare_models(rows, baseline_model, comparison_model, facility_id, hazard_type)` is
source-independent and returns `None` when either row is absent. `comparison_row` produces:

```text
baseline_hazard_intensity   comparison_hazard_intensity   hazard_intensity_change_pct
baseline_potential_loss_usd comparison_potential_loss_usd potential_loss_change_usd  potential_loss_change_pct
baseline_eal_usd            comparison_eal_usd            eal_change_usd             eal_change_pct
baseline_eal_as_pct_of_assets  comparison_eal_as_pct_of_assets
    eal_as_pct_assets_change_pp                 (percentage points: 0.61 % → 0.77 % = +0.16 pp)
    eal_as_pct_assets_relative_change_pct       ((0.77 / 0.61 − 1) × 100 = +26.2 %)
baseline_risk_level  comparison_risk_level
baseline_calculation_status  comparison_calculation_status
same_impact_function  impact_function_id  comparison_kind
```

```python
change_pct = (comparison / baseline - 1) * 100      # null when baseline is 0 or missing
change_usd = comparison - baseline
change_pp  = comparison_pct - baseline_pct          # never mixed with change_pct
```

`relabel_comparison(cmp, "country")` renames `comparison_*` → `country_*` for the
`global_vs_country` sheet; `"korea_local"` for `global_vs_korea_local`. In the sheets
`available` is true only when **both** models priced the pair; otherwise the line says
which status each side had.

## 9. Climate-change multiplier is not a model comparison

`climate_change_multiplier(baseline, future)` = `future_EAL / baseline_EAL` **within one
model_id**, between two periods/scenarios. It refuses — with the reason in `detail` — rows
from different models ("hazard-source comparison, not a climate-change multiplier"), rows
sharing scenario and period, missing EALs and a zero baseline. Every comparison row
carries `comparison_kind = "hazard source comparison — not a climate-change multiplier"`.

## 10. Exposure

User portfolio only: `lat`, `lon`, `asset_value_usd`, `asset_value_currency`,
`property_type` (mapped to a JRC sector by configuration). A zero or missing value is
`NO_EXPOSURE_DATA`. No Korean asset-value dataset is fabricated; WorldPop is not used.

## 11. Where results live

* Worker mode `physical_risk_models` → `runs/<id>/result.json`
  (`PhysicalRiskModelsOutput`: `rows`, `comparisons`, `readiness`, `adapters`,
  `impact_function_fixed`). The legacy `physical` runs and their outputs are untouched;
  the run store only gained the sentinel kind `physical_risk_models`.
* `POST /api/session/{id}/physical-risk-models` submits;
  `GET /api/session/{id}/run/{run_id}` polls;
  `GET /api/session/{id}/run/{run_id}/physical-risk-table?hazard=&model=&risk_level=&scenario=`
  returns the batch table in `CANONICAL_COLUMNS` plus the five export frames.
* UI: **Models** tab — per facility × hazard the three model cards (EAL, EAL/assets, risk,
  PML, status), the Δ line (country vs global, pp and % kept apart), the readiness table
  and the filterable batch table. A model without numbers shows **Not available**.
* Export frames ([`results_table.py`](../src/climaterisk/physical_risk/results_table.py)):
  `hazard_results`, `asset_summary`, `global_vs_country`, `global_vs_korea_local`,
  `methodology` — lists of dicts.
* **Excel (Phase 5 → 6)** — [`excel_export.py`](../src/climaterisk/physical_risk/excel_export.py)
  renders the frames with openpyxl. Sheets, in order: **Portfolio Summary** (non-expert:
  one line per facility — Asset Value, per-hazard Status / Risk / EAL / EAL ÷ Assets / Data
  Source, Heatwave Tmax p95, Overall EAL as a plain sum), **Asset Risk Matrix** (facility ×
  hazard risk level or status label; "Overall" counts calculated hazards — no score),
  **Hazard Results** (canonical 13 columns + full provenance), **Global vs Country**,
  **Global vs Korea Local**, **Climate Change** (only when a baseline scenario was run),
  **Methodology** (definitions, formulas, band copy quoting the configured thresholds,
  status copy, limitations), **Run Info**. Currency `$#,##0`, ratios `0.000%`, risk cells
  colour-coded by conditional formatting (a visual aid, not a rule). A `None` is an **empty
  cell, never 0** and a computed zero is `0`; every result row carries `model_id` and
  `calculation_status`. Technical fields (`impact_function_id`, hazard tags…) never appear
  on the first two sheets.
  `GET /api/session/{id}/run/{run_id}/physical-risk-export.xlsx` and the **Download Excel**
  button on the Models tab serve it.
* **Batch CLI (Phase 5)** — `scripts/physical_risk_batch.py` (worker env): facilities CSV
  (`facility_id, facility_name, lat, lon, asset_value_usd[, property_type]`) → all hazards ×
  all models → workbook + raw JSON. `--baseline-scenario historical` runs the same models a
  second time and fills `climate_change_multiplier = future_EAL / baseline_EAL` **within each
  model** — the only form that ratio takes; without it the column is empty.

  ```bash
  ./.climada-env/bin/python scripts/physical_risk_batch.py --facilities facilities.csv \
      --out results.xlsx --json results.json --scenario rcp45 --year 2040 --country KOR \
      --baseline-scenario historical
  ```

### Non-expert workflow (Phase 6)

The **Models** tab is the portfolio tool: *select assets → select risks → analysis data →
Run Assessment → results → Download Excel*. The user never sees an impact-function id,
a hazard adapter or a model id; the words come from
[`display_copy.py`](../src/climaterisk/physical_risk/display_copy.py) and the numbers from
the same runner and rows as everything else — nothing is recomputed in the browser.

| step | what the user sees | what runs |
|---|---|---|
| 1 | asset table with checkboxes, search, select all / clear, "n of m selected", 25 per page | `facility_ids` on the request → `PhysicalRiskModelsRequest.from_portfolio` filters the portfolio |
| 2 | Flood · Tropical Cyclone · Heatwave with one-line copy | `hazards` |
| 3 | **Recommended** (default) or Custom: Global reference / Korea country dataset / Korea local, each with per-hazard availability | `recommended_models()` reads the readiness registry: RF → DATA_API_COUNTRY, TC → DATA_API_COUNTRY, HEAT → KOREA_LOCAL (hazard only). An unrunnable cell is never chosen |
| 4 | one Run Assessment; "n / N calculations complete" | one batch job; the worker rewrites `progress.json` per hazard × model, served at `GET …/run/{id}/progress` |
| 5 | Physical Risk Summary counts (per asset × risk, **no overall score**), asset risk matrix, asset detail with EAL / EAL ÷ assets / Potential Loss / Data, **Why?** (band copy + method chain + impact function + dataset), Compare data (Global vs Country when both were run, with the "same family, near-zero difference expected" note), collapsed technical table | `results_table.primary_rows` picks one row per facility × hazard (recommended model first, then most local, informative only); `summary_counts`, `asset_risk_matrix_frame`, `portfolio_summary_frame` |
| 6 | Download Excel (top of results) | the Phase 5 route, unchanged |

"Financial loss: Not available (not $0)" is written wherever a hazard is unpriced;
"What this tool covers today" lists the limitations (`display_copy.LIMITATIONS`).

CLI equivalent (worker env):

```bash
./.climada-env/bin/python scripts/physical_risk_batch.py assets.csv \
    --hazards flood,typhoon,heatwave --models recommended --output risk_report.xlsx
```

CSV validation speaks plainly: missing `facility_id` / `lat` / `lon` / `asset_value_usd`
column, non-numeric coordinates or value, coordinates out of range, negative value and
duplicate `facility_id` are each a one-line error naming the line.

## 12. Measured (this repository, 2026-09-23, 100 M USD office at 37.5 N 127.0 E)

| hazard | scenario / horizon | model | dataset (version) | intensity | EAL | EAL/assets | PML100 | risk | status |
|---|---|---|---|---|---|---|---|---|---|
| TC | rcp45 / 2040 | GLOBAL_BASELINE | `…rcp45_global_2040` (v2.1) | 51.80 m/s | \$3,576.18 | 0.00358 % | \$121,636.62 | Low | FULL |
| TC | rcp45 / 2040 | DATA_API_COUNTRY | `…rcp45_KOR_2040` (v2.1) | 51.80 m/s | \$3,576.18 | 0.00358 % | \$121,636.62 | Low | FULL |
| TC | rcp45 | KOREA_LOCAL | — | — | null | null | null | null | NOT_IMPLEMENTED |
| RF | rcp60 / 2030–2050 | GLOBAL_BASELINE | `river_flood_150arcsec_rcp60_2030_2050` (v3, 1.5 GB) | 0.00 m | \$0.00 | 0.0000 % | \$0.00 | Low | FULL |
| RF | rcp60 / 2030–2050 | DATA_API_COUNTRY | `river_flood_150arcsec_rcp60_KOR_2030_2050` (v3) | 0.00 m | \$0.00 | 0.0000 % | \$0.00 | Low | FULL |
| RF | rcp60 | KOREA_LOCAL | — | — | null | null | null | null | NOT_IMPLEMENTED |
| RF | **rcp45** → served rcp60 | GLOBAL_BASELINE | `…rcp60_2030_2050` (v3) | 0.00 m | null | null | null | null | **SCENARIO_MISMATCH** |
| RF | **rcp45** → served rcp60 | DATA_API_COUNTRY | `…rcp60_KOR_2030_2050` (v3) | 0.00 m | null | null | null | null | **SCENARIO_MISMATCH** |
| HEAT (HW) | rcp45 / 2030 | GLOBAL_BASELINE | — | — | null | null | null | null | NO_HAZARD_DATA |
| HEAT (HW) | rcp45 / 2030 | DATA_API_COUNTRY | — | — | null | null | null | null | NO_HAZARD_DATA |
| HEAT (HW) | rcp45 / 2030 | KOREA_LOCAL | `heatwave/HW_rcp45_KOR_2030.hdf5` (KMA **TAMAX** SSP245 5ENSMN, S7 Option C) | 37.14 °C (31.85 °C before S7, when the layer was TA-based) | null | null | null | null | HAZARD_ONLY |

**Flood at this facility is a real, computed zero.** The nearest ISIMIP 150-arcsec centroid
to 37.5 N 127.0 E carries no depth in any of the 480 events — in the rcp60 2030–2050 set and,
checked directly, in the cached rcp26 2010–2030, rcp26 2030–2050 and historical KOR sets as
well (two of the five cells within 5 km are wet in rcp26 2010–2030; none in the others). The
row is `FULL` with `eal_usd = 0.0`, which under the null-not-zero rule means "priced, no
loss", not "not computed". The comparison then reports `eal_change_pct = null` (baseline is
zero — the Test I rule in real data) and `eal_as_pct_assets_change_pp = 0.0`. Requesting
`rcp45` for flood gives `SCENARIO_MISMATCH` on both Data API models because the product is
published for rcp26/60/85 only.

**Global vs country, TC: Δ = 0.0 % exactly** (EAL, PML and intensity). This is the expected
result, not a coincidence: the KOR set is the global set cropped, so the comparison
framework is verified by it rather than informed by it. A non-zero Δ for a Data API pair
would indicate a bug. Impact function id 9 on both rows (`impact_function_fixed.TC = true`).

**Correction to Phase 2.** The rows in `physical-risk-methodology.md` §13 (TC \$2,511 /
\$3,976, flood \$75,000 / \$7.2 M) could **not be reproduced** from the cached KOR Data API
files at the documented facility: the same event sets (3,890 and 43,560 events) give
\$2,845.89 and \$3,576.18 for TC, and the flood point carries zero depth. Whatever exposure
point or set produced them is not recorded, and they were in any case computed on KOR
country files, i.e. `DATA_API_COUNTRY`, not `GLOBAL_BASELINE`. §13 now carries the
reproducible figures above and says so.

## 13. Legacy protection

Unchanged: `worker/climaterisk_worker/physical.py` (legacy runners, 8-point flood curve),
`ingest.py`, `catalog.json` and every KOR layer, `impact_function_presets.json`,
`PhysicalRunResult`, the `/run` routes and their outputs.
`test_l_legacy_runner_module_does_not_import_the_new_engine` and the whole
`test_physical_regression.py` guard this.

## 14. Tests

| brief | test |
|---|---|
| A / B flood global & country, C same IF | `test_abcde_global_and_country_rows_use_the_same_published_function[RF]` |
| D / E TC global & country | `…[TC]`, `test_country_cut_of_the_same_data_api_product_prices_identically` (cached real files) |
| F heat financials null | `test_f_heat_rows_never_carry_money_under_any_model`, `test_f_runner_heat_cells_follow_the_readiness_table` |
| G no fake KOREA_LOCAL | `test_g_korea_local_flood_and_tc_adapters_load_nothing_and_say_why`, `test_g_runner_emits_not_implemented_rows…`, `test_g_missing_or_unimplemented_korea_local_is_not_available_not_zero` |
| H requested/served | `test_h_scenario_mismatch_nulls_the_financials…`, `test_h_engine_marks_a_served_scenario…` |
| I baseline = 0 | `test_i_zero_or_missing_baseline_gives_null_never_infinity` |
| J pct vs pp | `test_j_percentage_points_and_relative_percent_are_different_numbers` |
| K multiplier ≠ comparison | `test_k_multiplier_refuses_cross_model_rows_and_same_period_rows` |
| L legacy | `test_l_legacy_runner_module_does_not_import_the_new_engine`, `test_physical_regression.py` |
| schema / table / frames | `test_batch_table_has_the_canonical_columns_in_order_and_filters`, `test_export_frames_have_the_five_sheets…` |
| readiness consistency | `test_adapter_statuses_match_the_backend_readiness_table` |

Backend: `tests/test_physical_risk_models.py`. Worker: `tests/test_physical_risk_adapters.py`.

## 15. Completion

```text
[x] GLOBAL_BASELINE retained                 [x] requested/served scenario separated
[x] DATA_API_COUNTRY model implemented       [x] global vs country comparison
[x] KOREA_LOCAL schema/adapter interface     [x] climate multiplier not confused with model comparison
[x] Flood global calculation (adapter)       [x] percentage vs percentage-point separated
[x] Flood country calculation (adapter)      [x] canonical schema finalized
[x] TC global calculation                    [x] batch table data source ready
[x] TC country calculation                   [x] export-compatible dataframe ready
[x] Same IF across comparison                [x] tests pass
[x] Heatwave remains HAZARD_ONLY             [x] legacy regression passes
                                             [x] methodology documentation updated
```
