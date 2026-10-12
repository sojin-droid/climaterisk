# ADDING_A_PERIL — 새 재해(peril)를 추가할 때 고칠 곳

작성 2026-10-12. EIOPA CLIMADA-App 문서 §6.5("Implementation actions for adding a new hazard")가
설정 → 하자드 초기화 → 피해 계산 → 보고서의 4단계로 수정 지점을 표로 고정한 것을 이 저장소에 맞게 옮긴 것이다.
**모든 파일·심볼은 코드에서 읽었고, `tests/test_adding_a_peril_doc.py`가 (a) 여기 적힌 경로가 실제로 존재하는지,
(b) 재해 목록의 세 출처(`Peril` enum · `perils.json` · 워커 `_RUNNERS`)가 일치하는지 대조한다.**
재해를 추가하면 세 출처가 함께 바뀌어야 하고, 그렇지 않으면 그 테스트가 먼저 실패한다.

> **TL;DR (EN).** A peril lives in three places that must agree — `Peril` (enum),
> `perils.json` (library) and `_RUNNERS` (worker) — plus a tier row in SCOPE.md. The
> cheapest peril is a catalog-ramp one: one tuple in `_CATALOG_PERILS`, one enum member,
> one JSON entry, one SCOPE row, one test id. A peril with its own hazard source, impact
> function or result kind touches the runner, ingest, frontend and documentation layers
> listed in §1. The Models tab (three-model chain, RF/TC/HEAT) is a separate pipeline (§4).

---

## 0. 코드를 열기 전에 정할 것 (설계 결정 5개)

| # | 결정 | 선택지 | 결과가 미치는 곳 |
|---|---|---|---|
| D1 | **하자드 소스** | CLIMADA Data API / 로컬 카탈로그 인제스트(외부 래스터·NetCDF) / 자체 생성(트랙 → 바람장 등) | 러너의 하자드 해석 순서(카탈로그 우선 → Data API), 인제스터 필요 여부, 카탈로그 키 `(peril, climate_scenario, region, year)` |
| D2 | **CLIMADA `haz_type` 태그와 강도 단위** | 기존 태그(TC, RF, WFseason, WS, EQ, CF, HW, HM …)와 충돌하지 않는 2–3글자 | `ImpactFunc.haz_type`, 노출의 `impf_<HT>` 열, 카탈로그 HDF5의 `haz_type` — 셋이 다르면 `ImpactCalc`가 impact function을 못 찾는다 |
| D3 | **취약성** | ① CLIMADA/petals 발표 곡선 ② 문헌 곡선을 직접 정의 ③ 지시적 램프 | ③이면 [SCOPE.md](SCOPE.md) 등급은 **S**로 시작한다. ①②면 출처를 METHODOLOGY 표에 적는다 |
| D4 | **결과 종류 `result_kind`** | `monetary` / `productivity` / `yield` / `mortality` | 비화폐면 통화 집계·금융 사슬에서 제외되고(RISK_REGISTER A7), 프런트 포매터가 단위를 바꾼다(`format.ts::impactFormatter`) |
| D5 | **급성 / 만성** | 이벤트셋 / 시즌·연도 격자 | 재현주기 상한의 기록 길이가 무엇인지(SCOPE §2), `_params.py`의 연도 앵커 규칙 |

---

## 1. 한눈에 보는 수정 지점

"필수"는 모든 재해, "조건부"는 D1–D5의 선택에 따라. 경로는 저장소 루트 기준.

### 1-A. 도메인 · 라이브러리 (필수)

| 파일 | 수정 | 비고 |
|---|---|---|
| `src/climaterisk/core/enums.py` | `Peril`에 멤버 추가 | 값 = 러너 키 = `perils.json` id |
| `assets/libraries/perils.json` | `perils[]`에 항목 추가: `id`, `label`, `engine_hazard_type`(D2), `future_source`(어디서 미래 하자드를 얻는지 한 줄), `requires_ingest` / `requires_dem` / `historical_only`, 비화폐면 `result_kind` | 프런트 Scenarios 탭의 체크박스는 이 파일로 자동 생성된다 |
| `assets/libraries/impact_functions.json` | 새 취약성 파라미터가 필요할 때만: `classes[]` 각 클래스에 값, 공용 breakpoint 배열 | 기존 파라미터(`tc_v_half`, `wf_max_mdd`, `flood_mdr`, `eq_mdr`)로 충분하면 손대지 않는다. 카탈로그 램프는 `wf_max_mdd`를 스케일로 차용한다(GAP G4 — 의식적으로 선택할 것) |

새 취약성 파라미터를 추가했다면 그 값이 워커까지 흐르도록 세 곳을 더 고친다.

| 파일 | 수정 |
|---|---|
| `src/climaterisk/core/entities.py` | `VulnerabilityOverride`에 필드(스튜디오 편집용) |
| `src/climaterisk/engines/base.py` | `AssetSpec`에 필드 + `resolve_asset_specs`에서 클래스값/override 해석 + `explicit_params` 기록 |
| `worker/climaterisk_worker/vulnerability.py` | 국가별 지역 프리셋이 있는 경우의 해석 함수(`resolve_tc_vhalf` / `resolve_flood_mdr` 패턴) |

### 1-B. 워커 — Results 탭 엔진 (필수)

| 파일 | 수정 | 비고 |
|---|---|---|
| `worker/climaterisk_worker/physical.py` | **경로 1(카탈로그 램프)**: `_CATALOG_PERILS`에 튜플 한 줄 — `(haz_type, intensity[], mdd_shape[], unit, scenario_key or None, "ingest … first." 힌트, result_kind, metric_unit)`. `_RUNNERS`에는 자동 바인딩된다. **경로 2(전용 러너)**: `_run_<peril>(assets, climate_scenario, anchor_years, options)` 작성 후 `_RUNNERS`에 등록 | 전용 러너는 반환 dict의 키를 기존 러너와 맞춘다: `peril, status, target_year, aai_agg, present_aai_agg, delta_pct, total_value, per_asset[{id,lat,lon,eai,country}], yearset, warn_levels, freq_curve{return_periods, impact, max_resolvable_return_period, record_years}, result_kind, metric_unit, detail`. 재현주기는 반드시 `_resolvable_return_periods`로 상한 처리 |
| `worker/climaterisk_worker/_params.py` | 연도 앵커·시나리오 remap 상수가 새로 필요하면 여기에만 | 인제스터와 러너가 같은 카탈로그 키를 계산하게 하는 단일 출처 |
| `worker/climaterisk_worker/ingest.py` | D1이 "인제스트"면 `ingest_<source>(request) -> entry` 작성 + `_REFINERS`에 등록. entry는 `convert_grid_to_catalog`가 쓰는 필드(`peril, haz_type, climate_scenario, region, year, units, file, n_events, n_centroids, source, license`)를 채운다 | 표준화 격자(cells × years × intensity)로 만들 수 있으면 `hazard_convert.grid_to_hazard`를 재사용 |
| `src/climaterisk/api/routers/run.py` | 새 인제스트 소스면 `INGEST_SOURCES`에 추가 | `tests/test_ingest_sources.py`가 UI·API·워커 세 목록을 대조 |
| `assets/libraries/data_sources.json` | 외부 데이터의 `id, category, name, url, access, license, for, place_at`; 자동 인제스트면 `fetch{source, peril}` | `docs/DATA_SOURCES.md`는 이 파일에서 생성된다(`tests/test_data_sources_doc.py`) |

### 1-C. 프런트 — Results 탭 (조건부)

| 파일 | 수정 | 언제 |
|---|---|---|
| `frontend/climaterisk/src/views/ResultsView.tsx` | `perilProvenance()`에 `case "<peril>"` — 결과 카드의 "Data:" 출처 문장 | 항상 권장(없으면 기본 문장) |
| `frontend/climaterisk/src/views/ScenariosView.tsx` | 재해별 옵션 블록(`run_config.perils.includes(...)` 분기) | 그 재해만의 옵션(예: 폭풍 impf 토글, 홍수 방어 수준)이 있을 때 |
| `frontend/climaterisk/src/lib/format.ts` | `impactFormatter`의 `result_kind` 분기 | D4가 새 종류일 때 |

### 1-D. 문서 (필수)

| 파일 | 수정 |
|---|---|
| `docs/SCOPE.md` | §3 등급표에 행, §3.1 요약, §5 변경 기록 — `tests/test_scope_doc.py`가 행 누락을 잡는다 |
| `docs/METHODOLOGY.md` | "Per-peril methodology" 표에 행(하자드, 취약성 기본값, 성숙도/캐비엇) |
| `docs/CLIMADA_METHODS.md` | §5에 `5.x <peril>` 절 — 네이티브/커스텀/미구현 표 |
| `docs/CLIMADA_COVERAGE.md` | 해당 섹션에 `[x]` + 구현 모듈 인용 |
| `docs/KOREA_1KM_PHYSICAL_RISK_COVERAGE.md` | 한국 적용 재해면 감사 행 — `tests/test_korea_1km_coverage_doc.py`가 모든 러너 재해의 존재를 검사하므로 **한국 비적용이라도 한 줄은 필요** |

### 1-E. 테스트 (필수)

| 파일 | 수정 |
|---|---|
| `tests/test_catalog_perils.py` | 카탈로그 램프 재해면 `_PERILS`에 id — 등록과 "ingest first" 정상 실패를 검사 |
| `tests/test_physical_regression.py` | 전용 러너면 워커 환경에서 캡처한 베이스라인 수치 추가(`np.testing.assert_allclose`, `rtol` 명시) |
| `tests/test_ingest_sources.py` | 새 인제스트 소스면 자동으로 검사 범위에 들어온다 — 세 목록을 맞출 것 |
| 문서 테스트 3종 | `test_scope_doc.py`, `test_korea_1km_coverage_doc.py`, `test_adding_a_peril_doc.py`는 손대지 않아도 **문서 누락 시 실패**한다 |

---

## 2. 단계별 절차 (EIOPA 4단계 대응)

| EIOPA 단계 | 이 저장소에서 하는 일 | 끝났다는 신호 |
|---|---|---|
| ① Settings initialization | `Peril` enum, `perils.json`, (필요시) `impact_functions.json` + 오버라이드·AssetSpec 배선 | `uv run pytest tests/test_adding_a_peril_doc.py` 통과, Scenarios 탭에 체크박스가 보임 |
| ② Hazard initialization | 인제스터(`ingest.py`) 또는 Data API 해석 코드, `_params.py` 앵커, 카탈로그 키 | `scripts/build_hazard.py list`에 항목이 보이고, `catalog.load_hazard(peril, scenario, region, year)`가 `Hazard`를 돌려줌 |
| ③ Impact calculation | `_CATALOG_PERILS` 튜플 또는 `_run_<peril>`; 취약성 해석(`vulnerability.py`) | 워커 환경에서 `physical._RUNNERS[peril]([asset], scenario, [year])`가 `status == "ok"`와 `freq_curve`를 반환; 하자드 없을 때 `ValueError("… no local hazard …")` |
| ④ PDF/XLSX report generation | `perilProvenance` 문장, `result_kind` 포매터, SCOPE 등급 행, METHODOLOGY/CLIMADA_METHODS/COVERAGE 행 | 결과 카드와 Excel에 출처·단위·등급이 보이고 문서 테스트 3종이 통과 |

---

## 3. 두 가지 경로의 예

### 3-1. 카탈로그 램프 재해 (가장 짧은 경로, 예: `hail`)

1. `physical._CATALOG_PERILS["hail"] = ("HL", [0,2,4,6,8,10], [0,0,.1,.3,.6,1], "cm", None, "ingest a hail hazard … first.", "monetary", None)`
2. `Peril.HAIL = "hail"`, `perils.json`에 `{"id":"hail", "engine_hazard_type":"HL", "requires_ingest": true, …}`
3. `tests/test_catalog_perils._PERILS`에 `"hail"`
4. `docs/SCOPE.md` §3에 **S** 행(지시적 램프, G4), §3.1 요약, §5 기록. METHODOLOGY 표의 "카탈로그 재해" 행에 이름 추가. KOREA_1KM 커버리지에 한 줄.
5. 하자드는 사용자가 `scripts/build_hazard.py convert <grid.json>`으로 넣는다. 인제스터를 함께 제공하려면 1-B의 `ingest.py` 행.

램프는 **지시적**이다. 발표 곡선으로 바꾸기 전까지 결과 등급은 S이고, 그 사실이 `detail` 문자열("indicative … ramp")과 SCOPE 행에 동시에 나타나야 한다.

### 3-2. 전용 러너 재해 (예: `tc_surge`)

`_run_tc_surge`가 본보기다: 상위 재해의 하자드를 재사용(`_tc_hazard`), 외부 드롭인(DEM)이 없으면 **명확한 에러로 정상 저하**, petals 클래스(`TCSurgeBathtub`)로 하자드 파생, 공용 취약성 세트(`_flood_impf_set`) 재사용, 공용 후처리(`_impact`, `_eai_by_asset`, `_resolvable_return_periods`, `_yearset_summary`). 새 러너는 이 순서를 그대로 따르고, 다른 러너와 **같은 반환 키**를 쓴다(1-B 표).

---

## 4. Models 탭(3모델 체인)에도 넣으려면

Results 탭 엔진과 별개 파이프라인이다. 오늘은 `RF`·`TC`(가격 산정)·`HEAT`(hazard-only) 세 키만 있고, 확장 재해(`DROUGHT`, `SLR`)는 "왜 없는가"만 기록한다. 새 키를 넣으려면 다음을 **모두** 고친다.

| 파일 | 수정 |
|---|---|
| `src/climaterisk/physical_risk/models.py` | `HAZARD_KEYS`, `HAZARD_TAGS`(키 → CLIMADA 태그), `READINESS[키][모델]`(status·hazard_source·detail — 세 모델 전부) |
| `src/climaterisk/physical_risk/coverage.py` | `COVERAGE_KEYS`; 준비되지 않은 재해는 `EXTENDED_HAZARDS`에 증거(`evidence`)와 후보(`candidate`)를 적는다 |
| `worker/climaterisk_worker/physical_risk/adapters.py` | 모델별 `HazardAdapter` 구현(`describe / load / spatial_resolution`) |
| `worker/climaterisk_worker/physical_risk/registry.py` | 발표 impact function을 돌려주는 함수(`flood_impact_function` / `tc_impact_function` 패턴). **이 체인은 발표 곡선만 쓴다** — 없으면 `HAZARD_ONLY` |
| `worker/climaterisk_worker/physical_risk/engine.py` | 가격 산정 태그 목록과 상태 결정 |
| `src/climaterisk/physical_risk/display_copy.py` | `HAZARD_LABEL`, `HAZARD_COPY`, `method_copy()`, `LIMITATIONS` |
| `frontend/climaterisk/src/views/ModelsView.tsx` | `HAZARD_KEYS` 상수 |
| `docs/physical-risk-models.md`, `docs/municipality-physical-risk.md` | 재해 표와 커버리지 표 |
| `tests/test_physical_risk_models.py`, `tests/test_municipality_physical_risk.py` | 키 목록과 준비도 셀 검사 |

Excel의 *Hazard Coverage* 시트와 UI의 커버리지 표는 `coverage_table()`에서 생성되므로 따로 고치지 않는다.

---

## 5. 완료 기준 (definition of done)

- [ ] `Peril` enum · `perils.json` · `_RUNNERS`가 같은 집합 (`test_adding_a_peril_doc.py`)
- [ ] SCOPE §3 행 + 요약 + 변경 기록 (`test_scope_doc.py`)
- [ ] KOREA_1KM 커버리지 감사 행 (`test_korea_1km_coverage_doc.py`)
- [ ] 하자드 없을 때 정상 저하 에러에 "ingest … first" 또는 드롭인 안내가 들어 있음
- [ ] 재현주기가 `_resolvable_return_periods`를 거침, 비화폐면 `result_kind`가 통화 집계에서 제외됨
- [ ] METHODOLOGY 표 · CLIMADA_METHODS §5 · CLIMADA_COVERAGE 갱신, 외부 데이터는 `data_sources.json`
- [ ] 전용 러너면 워커 환경 회귀 베이스라인, 카탈로그 램프면 `test_catalog_perils._PERILS`
- [ ] PR 본문에 D1–D5 결정과 SCOPE 등급의 근거

---

## 6. EIOPA CLIMADA-App §6.5와의 대응

| EIOPA (Electron + py_src) | climaterisk |
|---|---|
| `dataset_info_service.DATA_TYPE` + feather 메타 재생성 | `perils.json` + Data API 조회는 러너 안에서(`_dataapi.resilient_get_hazard`), 로컬 캐시는 카탈로그 |
| `Hazard.js / Scenario.js / TimeHorizon.js`의 elif 분기 | `perils.json`에서 체크박스 자동 생성; 재해별 옵션만 `ScenariosView.tsx` 분기 |
| `hazard_type_enum.py` | `core/enums.py::Peril` |
| `hazards_service._get_hazard_dataset_infos` + `persist_hazard_metadata_as_txt.py` | `_params.py` 앵커 + `ingest.py` + `catalog.register` |
| `impact_functions/<hazard>_impact_function_factory.py` + `impact_functions_dict` | `_CATALOG_PERILS` 튜플 또는 `_run_<peril>` 안의 `ImpactFuncSet`; 발표 곡선은 `registry.py`/`vulnerability.py` |
| `impact_service.calculate_impact` 분기 | `physical._RUNNERS` 디스패치(`compute_physical_risk`) |
| `utils.beautify_hazard_type` 등 보고서 분기 | `perilProvenance`, `display_copy`, SCOPE 등급 행 |

EIOPA 앱에는 없는 요구가 셋 있다: 사용 등급 선언(SCOPE), 하자드 부재 시의 정상 저하 에러, 문서-코드 대조 테스트.
