# Method Registry — Part 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One method registry decides, per hazard, the single rank-1 (model, dataset) that becomes the result everywhere in the asset pipeline. Other models become labelled references that never fill a gap. Checks run in a fixed order and are recorded. Unknown hazard values stay NaN with a per-asset reason.

**Architecture:**
- `assets/libraries/method_registry.json` is the single source of truth.
- `src/climaterisk/physical_risk/method_registry.py` reads it.
- `recommended_models()`, `primary_row()` and the worker runner consult it instead of their own preference orders.
- Flood-map return-period gaps become NaN at the point where they are read (`floodmap_depth.py`). The engine turns NaN into a per-asset `NO_HAZARD_DATA` row with a reason.

**Tech Stack:** Python 3.11, numpy/scipy, CLIMADA env `/Users/junsojin/Developer/climaterisk/.climada-env/bin/python`, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-16-method-registry-design.md` (commit 967a8b1). Read it first.

**Part 2 (not in this plan):**
- basin boundaries and `no_map` NaN for points outside every polygon (needs a download the user approves);
- the board map (UTCI layer, coastal, river polygons);
- Studio labels;
- the heat productivity curve.

## Global Constraints

- **Run tests:**
  - Command: `cd ~/Developer/climaterisk-registry && /Users/junsojin/Developer/climaterisk/.climada-env/bin/python -m pytest <files> -q -p no:cacheprovider`.
  - `-q` prints no summary here, so check the exit code.
  - Lint: `export PATH=$HOME/.local/bin:/usr/bin:/bin; uv run ruff check . && uv run ruff format --check . && uv run mypy src/`.
- **Project conventions (CLAUDE.md):**
  - type hints and Google docstrings on public functions;
  - an `Algorithm:` section (LaTeX + ASCII) for any math;
  - log shapes, not arrays;
  - conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Missing is NaN or None, never 0.** A value that cannot be known must not become a number.
- **홍수위험지도 (공공누리 제4유형):** in-memory overlay only. Never rasterise it, never write its geometry, never commit it. File names and counts are fine.
- **Never commit data files or keys.** Do not download anything.
- **Tests:**
  - Do not weaken an existing assertion except in the tests this plan names as rewritten. Those encode the old fallback policy that the user replaced on 2026-11-16:
    - `tests/test_physical_risk_portfolio.py::test_primary_row_prefers_the_recommended_model_but_never_hides_a_priced_one`
    - `::test_recommended_models_follow_readiness_and_never_pick_an_unrunnable_cell`
    - `tests/test_municipality_physical_risk.py:233`
    - `tests/test_asset_level_validation_evidence.py:81`
  - If any other test breaks, stop and report it.
- **User decisions (fixed):**
  - Coastal rank 1 = KOREA_LOCAL KHOA bathtub (hazard-only).
  - River rank 1 = KOREA_LOCAL official map, with no defences applied.
  - No filling from another model or another rank.
  - A missing national return-period file gives NaN for that return period.
  - 지방하천 maps are 100-y by design: a point under a local map only uses the 100-y depth for 200/500 y, flagged as a lower bound.
  - 재해연보 and KDCA are supplementary, never checks.

## Review Focus

1. A run with only `--models global`: the summary must show the hazard as not run by its rank-1 model. It must not show the global numbers as the result. (Task 2 test.)
2. A future RF run whose map hazard contains NaN: the frequency factor scales frequencies only, so NaN stays NaN and the other assets stay priced. (Task 4 test.)
3. A portfolio with one NaN asset: totals cover the priced assets and report `Priced` n of N. They are not None and not a sum that counts the NaN as 0. (Task 4 test.)
4. Offline with an iCloud-dataless map zip: the points in that file's bounds get NaN with `map_unreadable`. There is no adapter-wide `ERROR`. (Task 6 test.)
5. Export: `method_id`, `method_rank`, `method_role` and `unavailable_reason` reach `table_rows` and the Excel hazard sheet. (Task 2 test.)

---

### Task 1: Registry file, loader, consistency tests (no behaviour change)

**Files:**
- Create: `assets/libraries/method_registry.json`
- Create: `src/climaterisk/physical_risk/method_registry.py`
- Test: `tests/test_method_registry.py`

**Interfaces (Produces):**
- `load_registry(path: Path | None = None) -> dict[str, Any]` (cached; `path` overrides for tests)
- `TAGS: tuple[str, ...] = ("RF", "PF", "CF", "TC", "HW", "HM")`
- `main_method(tag: str, rank: int = 1) -> dict[str, Any] | None`
- `rank1_model(tag: str) -> str | None`
- `method_for(tag: str, model_id: str) -> dict[str, Any]`, returning `{"method_id": str, "method_rank": int | None, "method_role": "main" | "reference"}`.
  - For a reference model, `method_id` is `f"{tag.lower()}.reference.{model_id.lower()}"` and `method_rank` is None.
- `defences_allowed(tag: str, model_id: str) -> bool`: False only for a main entry with `"defences": false`.
- `checks(tag: str) -> list[dict[str, Any]]`, sorted by `order`.
- `supplementary(tag: str) -> list[dict[str, Any]]`
- `readiness_key(tag: str) -> str`: `"HEAT"` for HW/HM, else the tag.

**JSON content.** The user fixed these values. Copy them; English text is fine. Each main entry has `rank, method_id, model_id, dataset, label, source, when_unavailable, defences`. Each check has `check_id, order, status ("exists"|"to_build"), diagnostics_key (dot path into adapter.diagnostics, exists only), label, threshold`. Each supplementary entry has `id, label, use`.

| tag | main rank 1 (method_id / model / dataset) | defences | checks (order: id, status, diagnostics_key) | supplementary |
|---|---|---|---|---|
| RF | `rf.floodmap_class` / KOREA_LOCAL / `floodmap_rfm_depth_class` | false | 1 `rf.fwdet_edge` exists `sub_check`; 2 `rf.isimip_sido_factor` exists `climate_delta.check`; 3 `rf.isimip_grid_depth` to_build | `rf.disaster_yearbook` |
| PF | `pf.floodmap_urban_class` / KOREA_LOCAL / `floodmap_cfm_depth_class` | false | 1 `pf.fwdet_edge` exists `sub_check`; 2 `pf.seoul_traces` exists `validation` | — |
| CF | `cf.khoa_ahhw_bathtub` / KOREA_LOCAL / `khoa_ahhw_connected_bathtub` | false | 1 `cf.structures_kept` exists `sub_check`; 2 `cf.aqueduct` to_build | — |
| TC | `tc.dataapi_country` / DATA_API_COUNTRY / `null` | true | 1 `tc.dataapi_global` to_build | — |
| HW | `hw.open_field_utci` / KOREA_LOCAL / `kma_asos_open_field_utci` | false | 1 `hw.utci_vs_heat_days` exists `sub_check` (C1, C3 and asset rank live in this one dict) | `hw.kdca_heat_illness` |
| HM | `hm.degree_days` / KOREA_LOCAL / `null` | false | 1 `hm.excess_mortality_2018` to_build (exists on the board only) | — |

Top-level keys:
- `_meta`: source spec path, date `2026-11-16`, and the rule text "rank 1 only; no filling from other models or ranks; checks never change the main value".
- `studio`: maps each of the 15 ids in `assets/libraries/perils.json` to `"other method (reference)"` or `"out of scope"`.
- `out_of_scope`: board layers wildfire / drought / LitPop, the twin, the case studies.

- [ ] **Step 1: Write the failing tests** in `tests/test_method_registry.py`:
  - `test_every_tag_has_exactly_one_rank1_main`: for t in TAGS, exactly one main with rank 1, and ranks are contiguous from 1.
  - `test_rank1_cells_are_runnable_in_readiness`: `READINESS[readiness_key(t)][rank1_model(t)]["status"] in {"READY","HAZARD_ONLY"}` and `.get("trial") != "yes"`.
  - `test_rank1_adapter_dataset_matches`: for t whose main dataset is not None, `adapter_for(t, rank1_model(t), "rcp45", 2050, "KOR")` has class attribute `dataset` equal to the registry dataset. Skip if climada is missing (`pytest.importorskip("climada")` only if the import needs it).
  - `test_heat_switch_matches_registry`: `load_config()["heat_utci"]["enabled"] is (main_method("HW")["dataset"] == "kma_asos_open_field_utci")`.
  - `test_user_decisions_2026_11_16`:
    - `rank1_model("CF") == "KOREA_LOCAL"`;
    - `defences_allowed("RF","KOREA_LOCAL") is False`;
    - `defences_allowed("RF","DATA_API_COUNTRY") is True`;
    - `defences_allowed("CF","GLOBAL_BASELINE") is True`;
    - `{s["id"] for s in supplementary("RF")} == {"rf.disaster_yearbook"}`;
    - `{s["id"] for s in supplementary("HW")} == {"hw.kdca_heat_illness"}`;
    - no check id contains `kdca` or `yearbook`.
  - `test_checks_are_ordered_and_typed`: orders are 1..n; status is one of the two values; an `exists` check has a non-empty `diagnostics_key`.
  - `test_every_studio_peril_is_listed`: the set of ids in `perils.json` equals the keys of `registry["studio"]`.
  - `test_method_for_reference_model`: `method_for("TC","GLOBAL_BASELINE") == {"method_id":"tc.reference.global_baseline","method_rank":None,"method_role":"reference"}`.
- [ ] **Step 2:** Run the tests. They fail (module missing).
- [ ] **Step 3:** Write the JSON and the loader. Use `climaterisk.paths` or the same asset-path helper that `metrics.load_config` uses; check `src/climaterisk/physical_risk/metrics.py`.
- [ ] **Step 4:** The tests pass. `tests/test_physical_risk_adapters.py` also still passes.
- [ ] **Step 5:** Commit `feat(registry): method registry — ranked main methods, ordered checks, supplementary results`.

### Task 2: Rank 1 is the result; references never fill; rows carry their method

**Files:**
- Modify: `src/climaterisk/physical_risk/display_copy.py:252-279` (`recommended_models`)
- Modify: `src/climaterisk/physical_risk/results_table.py:399-464` (`primary_row`, `primary_rows`, `_MODEL_PREFERENCE`)
- Modify: `src/climaterisk/physical_risk/metrics.py` (`ResultRow`)
- Modify: `worker/climaterisk_worker/physical_risk/runner.py:117-235` (`_rows_for`)
- Tests: rewrite the four tests named in Global Constraints. Add new tests to `tests/test_method_registry.py`.

**Interfaces:**
- Consumes: Task 1 `rank1_model`, `method_for`, `readiness_key`.
- Produces:
  - `ResultRow` gains `method_id: str | None = None`, `method_rank: int | None = None`, `method_role: str | None = None` and `unavailable_reason: str | None = None`. Place them after `model_id`.
  - `recommended_models(hazards=None, readiness=None) -> dict[str, str]` keeps its signature. For each key (`"HEAT"` → tag `"HW"`) it returns `rank1_model(tag)` only when that cell is runnable in `readiness` (READY or HAZARD_ONLY, not trial). Otherwise the key is omitted; it never picks another model.
  - `primary_row(candidates, preferred_model) -> ResultRow | None` returns the candidate whose `model_id == preferred_model`, whatever its status, else None.
  - `primary_rows` omits keys whose primary is None.
  - Delete `_MODEL_PREFERENCE` if nothing else uses it.

- [ ] **Step 1: Write the failing tests.**
  - `test_recommended_models_are_the_registry_rank1`: `recommended_models() == {"RF":"KOREA_LOCAL","PF":"KOREA_LOCAL","CF":"KOREA_LOCAL","TC":"DATA_API_COUNTRY","HEAT":"KOREA_LOCAL"}`. With a readiness copy where `RF/KOREA_LOCAL` is `NOT_IMPLEMENTED`, `"RF" not in recommended_models(readiness=table)`.
  - Rewrite `test_primary_row_prefers_the_recommended_model_but_never_hides_a_priced_one` as `test_primary_row_is_the_rank1_row_and_never_falls_back`, using the existing `_portfolio()` fixture:
    - the RF primary is the KOREA_LOCAL row even when that row is `NO_HAZARD_DATA` and a country row is priced;
    - with only global rows, `("F1","RF") not in primary_rows(only_global, recommended_models())`.
  - Update the expectations at `test_municipality_physical_risk.py:233` and `test_asset_level_validation_evidence.py:81`: CF is now KOREA_LOCAL. Update `test_recommended_models_follow_readiness_and_never_pick_an_unrunnable_cell` to the no-fallback rule. Keep every other assertion in those tests.
  - `test_rows_are_stamped_with_their_method`: call `runner._rows_for` with a stub adapter whose `describe()` returns `HazardDescription(hazard_type="HW", model_id="KOREA_LOCAL", status="NO_HAZARD_DATA", hazard_source="stub", hazard_dataset=None, hazard_data_version=None, hazard_country="KOR", requested_scenario="historical", served_scenario="historical", time_horizon=None)`, and one facility. Assert `method_id == "hw.open_field_utci"`, `method_rank == 1`, `method_role == "main"`. The same with `model_id="GLOBAL_BASELINE"` gives role `"reference"` and rank None.
  - `test_method_fields_reach_table_rows`: `table_rows([row])[0]` has the four new keys.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
  - In `_rows_for`, stamp `row.method_id/rank/role` from `method_for(tag, desc.model_id)` on every returned row, in all four return paths.
  - Add the four fields to the Excel hazard sheet column list if that list is explicit (`results_table.export_frames` / `hazard_results_frame`).
- [ ] **Step 4:** Run the tests you touched, plus `tests/test_physical_risk_portfolio.py tests/test_physical_risk_excel.py tests/test_municipality_physical_risk.py tests/test_sample_portfolio.py tests/test_physical_risk_models.py`. All pass.
- [ ] **Step 5:** Commit `feat(registry): rank-1 model is the result; other models are labelled references and never fill a gap`.

### Task 3: No defences on the rank-1 flood map

**Files:**
- Modify: `worker/climaterisk_worker/physical_risk/runner.py:195-200`
- Modify: `docs/METHODOLOGY.md` (river T2 entry, one sentence)
- Test: `tests/test_method_registry.py`

**Interfaces:**
- Consumes: Task 1 `defences_allowed`.
- Change: call `_with_defences(hazard, tag)` only when `desc.status != HAZARD_ONLY and defences_allowed(tag, desc.model_id)`. Otherwise `defences = None`.

- [ ] **Step 1: Write the failing test** `test_rank1_flood_map_is_never_defended` (skip without climada):
  - monkeypatch `runner._with_defences` to raise `AssertionError`;
  - build a stub RF adapter with `model_id="KOREA_LOCAL"`, `status=READY` and `load()` returning a 1-centroid, 3-event `Hazard` built the way `KoreaLocalFloodAdapter._map_hazard` builds one (depth 1.0 m at rp100/200/500, `ingest._incremental_frequency([100,200,500])`);
  - run `_rows_for(stub, "RF", [facility], "KOR", None)`;
  - assert the row is `FULL`, `flood_protection_return_period_years is None` and `eal_usd > 0`.
- [ ] **Step 2:** Run it. It fails.
- [ ] **Step 3:** Implement. Add the METHODOLOGY sentence: "The official map assumes levee failure and is never defended; the national 50-y defence applies only to the reference models (ISIMIP, Aqueduct) [사용자 결정 2026-11-16]."
- [ ] **Step 4:** The test passes, and so do `tests/test_flood_protection*.py` (or whatever tests cover `_with_defences`; find them with grep).
- [ ] **Step 5:** Commit `fix(flood): the official map is not defended a second time`.

### Task 4: Missing national return-period files are NaN; local maps are a 100-y lower bound; NaN becomes a per-asset reason

**Files:**
- Modify: `worker/climaterisk_worker/floodmap_depth.py` (`classes_at_points`, `class_for_return_period`, `class_depths`; new `basin_of`, `available_files`)
- Modify: `worker/climaterisk_worker/physical_risk/adapters.py` (`KoreaLocalFloodAdapter._map_hazard`: put `per_point_reason` in diagnostics)
- Modify: `worker/climaterisk_worker/physical_risk/engine.py` (`calculate`: new keyword `point_reason`)
- Modify: `worker/climaterisk_worker/physical_risk/runner.py` (pass `point_reason=reasons[i]`)
- Tests: `tests/test_floodmap_depth.py` (find the existing file with grep; else create it), `tests/test_physical_risk_engine.py`

**Interfaces:**
- `basin_of(path: str) -> str | None`: the 4-digit basin from `..._<basin>_<rp>.zip`.
- `available_files(index: list[dict]) -> set[tuple[str, str, int]]`: the set of (family, basin, rp).
- `classes_at_points(...)` returns, in addition:
  - `"missing": list[list[bool]]` (per RP, per point);
  - `"reason": list[str | None]` per point;
  - `"local_only": list[bool]`.
  - A missing code is `None`, not `""`.
- `class_depths`: a `None` code gives NaN in depth, lower and upper.
- `engine.calculate(..., point_reason: str | None = None)`. For a priced hazard (RF/CF/TC) whose intensity column at the facility's centroid contains NaN:
  - `calculation_status = NO_HAZARD_DATA`;
  - `status_detail = f"hazard unknown at this asset: {point_reason or 'NaN in hazard'}"`;
  - `unavailable_reason = point_reason or "nan_hazard"`.
  - Hazard-only rows whose intensity is None also get `unavailable_reason`.

**Rule** (put it in the docstring with an `Algorithm:` block):

$$c_r(p) = \max_{f}\ \max_{r' \le r,\ (f,b,r')\ \text{exists}} \text{code}_{f,r'}(p)$$

ASCII: class at rp r = deepest code over families and over existing files with rp ≤ r. This is today's envelope, which handles map inconsistency.

Exception: if the point hit a national file of basin b, and the national file (national, b, r) does not exist, then code_r = None (NaN). The reason is `"no_file_for_rp:<r>"`; join several reasons with `;`.

- The local family is 100-y by design and is never "missing" at 200/500 y. When the only family hit is local, set `local_only = True`.
- Points outside every polygon keep `""` (0 m). The `no_map` rule is Part 2.

- [ ] **Step 1: Write the failing tests.** Use a fake `cidx` with `code_at(path, x, y)` driven by a dict, and index rows with fake paths. No real maps.
  - `test_missing_national_rp_is_nan`: national files for basin 2101 at rp 100 and 500 only; the point is inside the 100 polygon (`N331`) and the 500 polygon (`N332`). Result: codes 100 → `N331`, 200 → None, 500 → `N332`; reason `"no_file_for_rp:200"`; and `class_depths` gives NaN at 200.
  - `test_local_only_is_a_100y_lower_bound`: only a local 100 hit (`N331`). Result: all three RPs → `N331`, `local_only[0] is True`, reason None.
  - `test_existing_file_dry_point_keeps_the_envelope`: national 100/200/500 all exist; the point is inside 100 only. Result: 200 → `N331` (unchanged behaviour), reason None.
  - `test_outside_everything_reads_zero`: codes are all `""`, depth 0.0 and reason None.
  - Engine: `test_nan_intensity_is_no_hazard_data_with_reason`. Build a 2-centroid RF hazard where asset A has NaN at rp200 and asset B is finite.
    - A → `NO_HAZARD_DATA`, `unavailable_reason == "no_file_for_rp:200"`, `eal_usd is None`.
    - B → `FULL` with finite EAL.
  - `test_future_factor_keeps_nan`: `flood_delta.scale_frequency(h, 2.0)` keeps the NaN entries NaN and doubles the frequencies.
  - `test_totals_cover_priced_assets_only`: `portfolio_totals_frame` on rows A (NO_HAZARD_DATA) and B (FULL, EAL 10). Total EAL 10; the `Priced` column shows 1 of 2 assets.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement. Keep `"file100"` behaviour. Make sure `hazard.check()` accepts NaN in the sparse intensity; if it does not, report it rather than replacing NaN with 0.
- [ ] **Step 4:** The tests pass, plus `tests/test_physical_risk_adapters.py tests/test_flood_delta.py tests/test_pluvial_local.py` and the existing floodmap tests.
- [ ] **Step 5:** Commit `fix(flood): no return-period borrowing across a missing national map; NaN carries a per-asset reason`.

### Task 5: Check harness

**Files:**
- Modify: `src/climaterisk/physical_risk/method_registry.py` (add `run_checks`)
- Modify: `worker/climaterisk_worker/physical_risk/runner.py` (`record["checks"]`)
- Test: `tests/test_method_registry.py`

**Interfaces:**
- `run_checks(tag: str, model_id: str, diagnostics: dict[str, Any] | None) -> list[dict[str, Any]]`. It returns `[]` for reference models. For a main model it returns one dict per registry check, in order: `{"check_id","order","state","reason"}`.
  - `state` is one of `"ran"`, `"warn"`, `"skipped"`, `"error"`.
  - `to_build` → skipped, reason `"not built yet"`.
  - The `diagnostics_key` path is absent → skipped, reason `"no diagnostics for this run"`.
  - The value has `"error"` → error, with the error text as reason.
  - `warn is True` → warn, with reason `warn_reason` or `note`.
  - `warn is False` → ran.
  - `warn is None` → skipped, with reason `note` or `"not enough points"`.
- The runner sets `record["checks"] = run_checks(tag, desc.model_id, adapter.diagnostics)` on the success path, and `[]` otherwise. The existing `sub_check` / `climate_delta` keys stay.

- [ ] **Step 1: Write the failing tests:**
  - `test_run_checks_rf_present_day`: diagnostics `{"sub_check":{"warn":False}}` → `[("rf.fwdet_edge","ran"),("rf.isimip_sido_factor","skipped"),("rf.isimip_grid_depth","skipped")]`.
  - `test_run_checks_warn_and_error`:
    - HW `{"sub_check":{"warn":True,"warn_reason":"x"}}` → warn with reason `"x"`;
    - PF `{"sub_check":{"warn":None},"validation":{"error":"E"}}` → `[skipped, error]`.
  - `test_reference_models_run_no_checks`: `run_checks("RF","GLOBAL_BASELINE",{}) == []`.
- [ ] **Step 2:** Run them. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** The tests pass, and `tests/test_physical_risk_*.py` still passes.
- [ ] **Step 5:** Commit `feat(registry): checks run in registry order and are recorded (ran / warn / skipped / error)`.

### Task 6: Flood-map coverage table and unreadable files

**Files:**
- Modify: `worker/climaterisk_worker/floodmap_depth.py` (new `coverage_table`, `is_dataless`; `classes_at_points` catches a per-file read failure)
- Create: `scripts/floodmap_coverage.py`
- Modify: `docs/DATA_LAYOUT.md:134` (the zip count, and the fact that production code reads the maps)
- Test: the floodmap test file from Task 4

**Interfaces:**
- `is_dataless(path: Path) -> bool`: on macOS, `os.stat(path).st_flags & 0x40000000` (SF_DATALESS, `<sys/stat.h>`; verify the constant in the header or Apple docs and cite it in the docstring). On other systems it returns False.
- `coverage_table(root: Path, families: dict | None = None) -> list[dict[str, Any]]`, rows `{"family","basin","rp","state"}`, where `state` is `"present"` or `"dataless"`. It is built from file names and stat only; nothing is opened.
- In `classes_at_points`, a file whose polygons cannot be read (any exception from `cidx.code_at`) makes every point inside its bounds `None`, with reason `"map_unreadable"`. It is logged once per file at WARNING (file name only).
- The script writes `<DATA_ROOT>/derived/floodmap/coverage.json` (`climaterisk.paths` is the only path authority) and prints counts per family and state. `--help` works.

- [ ] **Step 1: Write the failing tests:**
  - `test_coverage_table_from_names`: in tmp_path create empty files `유역별_100년_빈도_국가하천_하천범람지도/RFM_SBSN_NTN_2101_100.zip` and `..._500` in the 500 folder. The table has those two rows, state `"present"`.
  - `test_unreadable_file_gives_nan_with_reason`: the fake `cidx.code_at` raises `OSError` for one path. A point in its bounds gets code None and reason containing `"map_unreadable"`. Another file still reads.
- [ ] **Step 2:** Run them. They fail.
- [ ] **Step 3:** Implement. Run the script once against the real data root, read-only. Report the counts in the commit message body. Do not commit the JSON.
- [ ] **Step 4:** The tests pass; ruff and mypy are clean.
- [ ] **Step 5:** Commit `feat(floodmap): coverage table by file name; unreadable maps give NaN, not an error`.

### Task 7: Documents

**Files:** `docs/SCOPE.md` (coastal_flood, river_flood and heatwave rows; §5 change log), `docs/TIERS.md`, `docs/CLIMADA_METHODS.md`, `docs/METHODOLOGY.md`, `docs/ARCHITECTURE.md` (one line naming the registry as the method authority), and the spec's "남은 결정".

- [ ] **Step 1:** Write the doc changes:
  - **coastal_flood:** the headline is now the KHOA bathtub, hazard-only, so no coastal money in totals. Aqueduct is a reference row.
  - **river_flood:** the official map is not defended. A missing national RP gives NaN. Local maps give a 100-y lower bound.
  - **Rule summary:** rank 1 only; references never fill; checks are ordered and recorded.
  - Add a §5 change-log line dated 2026-11-16.
  - `tests/test_scope_doc.py` must still pass.
- [ ] **Step 2:** Run the full suite `pytest -q -p no:cacheprovider` (exit code 0) and lint.
- [ ] **Step 3:** Commit `docs: method registry — rank-1 headlines, coastal change, flood NaN rules`.
