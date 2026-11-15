# Heat UTCI Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve future open-field UTCI (SSP2-4.5/5-8.5, 2051–2060), and compute the pre-registered KDCA heat-illness check V1–V4 that decides whether UTCI becomes the HW headline.

**Architecture:** Pure math stays at the top of `heat_utci.py`. A new pure module `heat_validation.py` handles the KDCA cleaning, counts, Spearman tests and the decision. A script ties the real data together and writes to `<DATA_ROOT>/derived/heat_validation/`.

**Tech Stack:** Python 3.11, numpy, pandas, scipy (Spearman), pythermalcomfort 4.8.0, CLIMADA env `/Users/junsojin/Developer/climaterisk/.climada-env/bin/python`.

**Spec:** `docs/superpowers/specs/2026-11-16-heat-utci-phase2-design.md` (commit 4fee26a; its criteria are frozen).

## Global Constraints

- **Gate tests come first and are fixed.**
  - `tests/test_heat_utci_future.py` and `tests/test_heat_validation.py` were written before any code.
  - Do not weaken an assertion. If a test is wrong, stop and report it.
- **Thresholds:** `v1_min_rho 0.5`, `v1_margin 0.02`, `v2_margin 0.02`, `v3_margin 0.10`, validation years `[2011, 2019]`, months `[5, 6, 7, 8, 9]`. They live only in `assets/libraries/physical_risk_config.json` → `heat_utci.validation`.
- **Missing data:** missing is NaN/None, never 0. A missing metric fails its test.
- **Repository hygiene:**
  - No data files in git.
  - Never print or commit the KDCA rows or the API keys.
  - The KOSIS key is read from `.env` inside `python -I` and masked in any output.
- **Unchanged items:**
  - `heat_utci.enabled` stays `false` in this plan. The flip is a separate step after the real run.
  - Present-period numbers must not change: the Seoul baseline `tests/baselines/heat_utci_seoul108.json`.
- **Project conventions:** Google docstrings with an `Algorithm:` section (LaTeX plus an ASCII line); type hints; log shapes, not arrays.
- **Run tests with:** `/Users/junsojin/Developer/climaterisk/.climada-env/bin/python -m pytest <files> -q -p no:cacheprovider`.
  - `-q` prints no summary line in this setup, so check the exit code.
  - Lint: `export PATH=$HOME/.local/bin:/usr/bin:/bin; uv run ruff check . && uv run ruff format --check . && uv run mypy src/`.

## Review Focus

- **A month of the future cache missing at a cell:** those hours must be NaN, so the year falls under the 95 % rule. They must not keep the present temperature.
- **Present output when `future=None`:** byte-identical to before. The baseline test pins it.
- **A station whose `addr` 시도 is unknown:** it is excluded from V2/V3 and counted. It never lands in a wrong 시도.
- **A 시도 with no valid station** (all years fail): its V2 ρ is None and it is left out of the median. V3 uses only 시도 that have both values, and `n` is reported.
- **KDCA rows dated outside May–Sep or outside 2011–2019:** they are ignored by the counts. They are not an error.

---

### Task 1: Future UTCI in `heat_utci.py`

**Files:** Modify `worker/climaterisk_worker/heat_utci.py`, `assets/libraries/physical_risk_config.json`. Test: `tests/test_heat_utci_future.py` (all non-adapter tests).

**Interfaces (Produces):**
- `monthly_delta(base_tmax, base_months, fut_tmax, fut_months, months: Sequence[int]) -> np.ndarray` returns shape `(len(months), ny, nx)`.
  - NaN-aware monthly means; reuse `heat_days.monthly_mean`.
  - Raises `ValueError` on a shape mismatch.
- `apply_future(ta_c, time_kst, delta_by_month: Mapping[int, float]) -> np.ndarray`: adds the hour's month delta. A missing or NaN month gives NaN hours.
- `scenario_window(scenario: str, year: int, future_cfg: dict) -> dict`, keys `served_scenario`, `window` (list or None), `reason` (str or None).
  - Historical: `"historical"`, `None`, and `None` when the requested scenario was `"historical"`.
- `open_field_utci(points, cfg, root=None, future: str | None = None)`.
  - When `future` is an SSP key, every served row adds `future_scenario`, `future_years`, `utci_days_ge38_future`, `utci_days_ge38_future_low`, `utci_days_ge38_future_high`, `utci_hours_ge38_future`, `utci_days_ge32_future` and `future_delta_t_mean_c` (mean δ over the season months at the asset cell).
  - The present keys are unchanged.
  - `_nan_row` gets the same keys as None.
  - `record["future"]` names the caches.
- Config `heat_utci.future` exactly as the `FUT` dict in the test, using the cache paths relative to `<DATA_ROOT>/cache`. Load the caches the way `_grid_path` does.
- The future run reuses the present hourly series. RH is fixed (assumption A7): recompute nothing but `Ta`, `e` (= RH·es(Ta_fut)), MRT and UTCI.
- Add `"A7 (future) humidity, wind, radiation and the ground fit held at present values"` to `ASSUMPTIONS`.

- [ ] Step 1: run the Task 1 tests; they fail on missing attributes.
- [ ] Step 2: implement.
- [ ] Step 3: Task 1 tests pass, plus `tests/test_heat_utci.py`, including the real-data tests.
- [ ] Step 4: commit `feat(heat): future open-field UTCI by monthly delta (phase 2)`.

### Task 2: Adapter serves the future

**Files:** Modify `worker/climaterisk_worker/physical_risk/adapters.py` (`KoreaLocalUtciAdapter`). Test: the adapter tests in `tests/test_heat_utci_future.py`.

**Interfaces:** Consumes `scenario_window` and `open_field_utci(..., future=...)`.
- `describe()` uses `scenario_window(self.scenario, self.year, cfg["future"])`.
  - `served_scenario` is the SSP key or `"historical"`.
  - `time_horizon` is `"2051-2060 May-Sep (delta on 2000-2019 observations)"` or `"2000-2019 May-Sep"`.
  - `detail` mentions A7 when the future is served, and the fallback reason otherwise.
- `load()` passes `future` and builds the hazard from `utci_days_ge38_future` when the future is served.
- `diagnostics["per_asset"]` keeps both the present and future values.

- [ ] Step 1: the tests fail.
- [ ] Step 2: implement.
- [ ] Step 3: the adapter tests pass, plus `tests/test_physical_risk_adapters.py` and `tests/test_heat_utci.py`.
- [ ] Step 4: commit `feat(heat): UTCI adapter serves SSP2-4.5/5-8.5 2051-2060`.

### Task 3: Validation module + station daily series

**Files:**
- Create `worker/climaterisk_worker/heat_validation.py`.
- Modify `worker/climaterisk_worker/heat_utci.py`, and the config (`heat_utci.validation`).
- Test: `tests/test_heat_validation.py`.

**Interfaces (Produces):**
- `SIDO16: tuple[str, ...]`, in the order `("서울","부산","대구","인천","광주","대전","울산","경기","강원","충북","충남","전북","전남","경북","경남","제주")`.
- `normalise_sido(name: str | None) -> str | None` and `station_sido(addr: str) -> str | None`. 세종 maps to 충남; old and new names (강원도/강원특별자치도, 전라북도/전북특별자치도, 제주도/제주특별자치도) both map to the same region.
- `load_kdca(path: Path) -> tuple[pd.DataFrame, dict]`. The frame has columns `date` (datetime64) and `sido`. `meta` has keys `n_rows`, `n_blank` (every field empty) and `n_unknown_sido`.
- `daily_counts(cases, years: tuple[int,int], months) -> pd.DataFrame`: every season day as the index (zeros included); columns are `SIDO16`; values are ints.
- `spearman(a, b) -> float | None` (finite pairs only, None if fewer than 3 pairs or constant).
- `weighted_national(indicator: pd.DataFrame, pop: pd.DataFrame) -> pd.Series`: weights are the population of the row's year, renormalised over the non-missing regions.
- `rate_per_100k(counts_by_year: pd.DataFrame, pop: pd.DataFrame) -> pd.Series`.
- `annual_from_daily(df, years, min_day_fraction) -> pd.DataFrame`.
  - Columns `d38` (days with `utci_max >= 38`), `t95` (95th percentile of `tmax_grid`, `np.percentile` linear) and `heat_days` (days with `tmax_grid >= 33`).
  - A year is NaN when finite `utci_max` days are fewer than `min_day_fraction` of the season days.
- `v1_daily_national`, `v2_daily_by_sido`, `v3_spatial`, `v4_yearly`: return the dict shapes the decision tests use (`rho_u` / `rho_t`, `median_u` / `median_t` plus `per_sido`, `rho_u` / `rho_t95` / `rho_h` / `n`, `rho` / `n`).
- `decide(v1, v2, v3, rules) -> dict` with keys `switch`, `items` (each with `pass` and the numbers) and `reasons`.
- `heat_utci.station_daily_series(cfg, stations: Sequence[int] | None = None, root=None) -> dict[int, pd.DataFrame]`.
  - Index: the season days of `obs_years`. Columns `["utci_max", "tmax_grid"]`.
  - `utci_max` is the daily max of the present hourly UTCI at the station point (shift 0), NaN when the day has fewer than 20 finite hours [설계값].
  - `tmax_grid` is the grid daily max at the station cell.
  - `None` means every station that passes the existing `meteo_ok` and radiation rules.
  - Reuse the `_asset_row` internals. Refactor a private helper rather than copying it.
- Config `heat_utci.validation`: `{"years":[2011,2019],"months":[5,6,7,8,9],"v1_min_rho":0.5,"v1_margin":0.02,"v2_margin":0.02,"v3_margin":0.10,"min_day_fraction":0.95,"min_hours_per_day":20,"kdca_file":"external/kdca_heat_illness/raw_20261116/kdca_heat_illness_15149889.csv","kosis_population":{"orgId":"101","tblId":"DT_1B040A3","itmId":"T20"}}` plus a `note` naming the sources.

- [ ] Step 1: the tests fail.
- [ ] Step 2: implement.
- [ ] Step 3: `tests/test_heat_validation.py` passes, including the real Seoul test, and `tests/test_heat_utci.py` still passes.
- [ ] Step 4: commit `feat(heat): KDCA heat-illness check module (V1-V4, pre-registered decision)`.

### Task 4: Validation script

**Files:** Create `scripts/heat_utci_validate_kdca.py`. No unit test beyond a `--help` smoke; the real run is done by the reviewer.

The script:
1. Loads KDCA from the config path.
2. Fetches the 시도 population for 2011–2019 from KOSIS. It uses the key from `.env`, read inside the process, masked, never printed. It caches the result to `<DATA_ROOT>/external/kosis_population/sido_2011_2019.json`; this file holds public aggregates.
3. Runs `station_daily_series` for all stations, maps them to 시도 by `station_sido`, and averages the stations per 시도.
4. Computes V1–V4 and `decide`.
5. Writes `<DATA_ROOT>/derived/heat_validation/kdca_check_<YYYYMMDD>.json`, containing:
   - all numbers, per-시도 tables, station→시도 map, `meta` counts;
   - `n` for each test;
   - the criteria;
   - the KDCA region caveat (hospital location ≤2018, occurrence 2019+);
   - source citations.
6. Writes a PNG of grouped bars (dark blue U, light blue T or T95, grey H) for V1–V3 ρ.
7. Prints only aggregate numbers.

- [ ] Step 1: implement.
- [ ] Step 2: `--help` works; ruff, ruff format and mypy are clean.
- [ ] Step 3: commit `feat(scripts): KDCA heat-illness validation run`.
