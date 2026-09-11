# 리스크 등록표 — 원본(파라메트릭) → 재구축본(CLIMADA) 전환 및 국내 특화

각 항목에 **상태 · 해결된 이유(근거) · 해결 방법(코드/절차) · 검증**을 기록한다.
"해결"은 이 저장소에서 코드·테스트·실측 실행으로 확인된 것만 표기한다. 근거가 없는 항목은
해결로 적지 않았다. (기준일 2026-09-01)

| 상태 | 뜻 |
|---|---|
| ✅ 해결 | 코드 반영 + 자동 테스트 또는 실측 실행으로 확인 |
| 🟡 부분 | 메커니즘은 있으나 데이터·검증이 남음 |
| 🔴 미해결 | 방법은 확정했으나 실행 전 (국내 특화 항목 대부분) |

---

## A. 해결 완료

| # | 리스크 | 해결된 이유 (근거) | 해결 방법 (코드) | 검증 |
|---|---|---|---|---|
| A1 | **폐쇄망에서 실행 실패** — 원본은 Open-Meteo 실시간 호출이 막히면 조용히 하드코딩 기본값으로 폴백 | 재구축본은 실시간 외부 API가 **없음**. 해저드를 1회 받아 로컬 HDF5 카탈로그에 캐시하고 러너가 카탈로그를 우선 조회 | `worker/climaterisk_worker/catalog.py::load_hazard` (카탈로그 우선 → Data API 폴백), `scripts/build_hazard.py cache` | 한국 4페릴(TC/RF/WF/EQ)이 `data/hazard_db/` 캐시로 오프라인 실행됨 (`tests/test_catalog.py`) |
| A2 | **포트폴리오 상관 과소평가** — 지점별 독립 계산은 한 태풍이 여러 자산을 동시 타격하는 꼬리를 놓침 | CLIMADA 이벤트셋은 사건 단위 행렬이라 동일 사건의 다자산 손실이 자연히 합산됨 | 태풍 43,560 이벤트셋 × `ImpactCalc`; 포트폴리오 `at_event`로 사건별 합산 | 한국 실행: 울산 7.74억 / 부산 3.64억 / 서울 0.93억 — 동일 사건셋에서 산출 |
| A3 | **근거 없는 재현주기 외삽** — 기록이 못 받치는 1-in-250년 값을 산출 | 재현주기를 이벤트 기록에서 유도해 상한(기록의 절반)을 적용, 초과분은 산출하지 않음 | `physical.py::_resolvable_return_periods`, `_RP_RECORD_FRACTION=0.5`, 결과 `freq_curve.max_resolvable_return_period`/`record_years`, UI 문구 | 태풍 344년 / 지진 57년 / 산불 10년(이벤트 20개→10) 자동 산출; `tests/test_heat_mortality.py` 재현주기 4건 |
| A4 | **CLI와 러너의 한도 규칙 불일치** — CLI 출력 문구가 코드 동작과 어긋났던 결함 | CLI가 러너의 상수를 직접 import해 두 경로가 갈라질 수 없게 함 | `scripts/heatwave_europe.py` → `from physical import _RP_RECORD_FRACTION` | 관측 45년 → 22년 상한이 CLI·러너 동일 표시 |
| A5 | **앙상블 확대를 증거 확대로 착각** — 합성 1000시즌이면 500년 재현주기가 나옴 | 관측 기록(45년) 상한이 합성 앙상블 상한을 지배하도록 분리 | `_print_surge_planning(observed_years=…)` | 300시즌 합성에서도 22년에서 절단 표시 |
| A6 | **0의 의미 모호성** — "데이터 없음/footprint 밖/임계 미만"이 구분되지 않음 | 모든 결과에 해석문을 첨부해 세 경우를 명시 구분 | `physical.py::_interpret_result` | 한국 홍수·산불 0에 "자산이 footprint 밖" 해석 첨부 |
| A7 | **비화폐 결과가 통화 집계로 유출** — 사망자 수가 EBITDA/DSCR 체인에 합산 | 백엔드·프론트 양쪽에서 `result_kind != monetary`를 제외하고, 제외 사실을 화면에 명시 | `finance/service.py::per_asset_aai`, `Aggregation.tsx` | `tests/test_heat_mortality.py::test_non_monetary_perils_excluded_from_financial_aai`; UI "Excluded from the currency total" 실측 |
| A8 | **비화폐 값의 통화 표기** — 사망자가 "€0.15/yr"로 표시 | 결과 종류별 포매터 하나를 KPI·지도·표·축에 공통 주입 | `frontend/src/lib/format.ts::impactFormatter` | 브라우저 실측 "0.15 persons/yr", "1 person" |
| A9 | **잘못된 데이터 출처 표기** — 폭염 카드에 하천홍수 출처 문구 | 페릴별 출처 함수로 분리 | `ResultsView.tsx::perilProvenance` | DOM에서 `river-flood` 문구 소멸 확인 |
| A10 | **리로드 시 결과 소실** — 실행 결과가 브라우저 메모리에만 존재 | 세션의 종류별 최근 완료 실행을 서버에서 재부착. 스키마 변경 없이 `perils` 센티널로 종류 유도 | `runs/store.py::latest_by_kind`, `run_kind`; `GET /latest-runs`; `useResults` 복원(진행 중 실행은 덮어쓰지 않음) | `tests/test_run_restore.py` 9건; 리로드 후 실행 버튼 없이 결과 복원 실측 |
| A11 | **지도가 0×0 컨테이너에서 마운트되면 세계지도로 고정** — one-shot 가드가 크기 확인 전에 소진 | 크기 확인 후에만 프레이밍하고, 0이면 `ResizeObserver`로 대기 | `MapView.tsx::FitToAssets`, `ResultsMap.tsx::FitBounds` | 타일 프레임 z=2(세계) → z=7(스페인) 실측 |
| A12 | **보건 페릴의 노출값을 UI에서 입력 불가** | 자산 편집기에 headcount 필드 추가 | `AssetEditor.tsx` | 1200→1500 편집이 서버에 저장됨 실측 |
| A13 | **시설 단위만 지원** — 인구 기반 페릴을 돌릴 노출 경로 부재 | 인구 노출 소스 2종 추가, 인구 격자는 headcount로 전달하고 value=0으로 두어 피해 페릴이 사람을 돈으로 오인하지 않게 함 | `exposures.py::POPULATION_SOURCES`, `litpop.py::_grid_to_assets(is_population)` | 스페인 8,245셀 / 46.8M명 실행; 도시표본과 1인당 5% 이내 일치 |
| A14 | **격자 노출의 국가 판정 붕괴** — 해안·도서 셀의 ISO3가 None이면 해저드를 못 찾음 | 호출자가 국가를 명시하고, 폴백으로 다수결 ISO3 사용 | `physical.py::_majority_iso3`, `options["country_iso3"]` | 격자 실행 성공 |
| A15 | **로그인 게이트 소스로 유도되는 혼란** — 기본값 LitPop이 GPW 로그인 필요 | 드롭다운에 "no login/no data needed" 표기, 안내문에 로그인 필요 소스 구분 | `MapView.tsx` 모델 노출 섹션 | UI 실측 |
| A16 | **합성 시즌 라벨이 실제 연도로 오인** | 합성이면 순번 라벨 + `synthetic` 라이선스, 관측이면 실제 연도 + ECA&D 라이선스로 구분 | `heat_mortality.py::standardized_grid(years=…)` | `test_observed_hazard_grid_carries_real_calendar_years` |
| A17 | **결과 표시가 금융 사슬 밖 페릴에서 오해 유발** | 사망률 KPI를 "People exposed / Annual risk per 100k"로 전환 | `ResultsView.tsx` | UI 실측 |

## B. 부분 해결

| # | 리스크 | 현재 상태 | 남은 것 | 완료 방법 |
|---|---|---|---|---|
| B1 | **홍수의 점자산 민감성** — 서울(한강 도시) 홍수 0 | 해석문으로 "footprint 밖"임은 명시됨 | 방법론적 해결 | 하천 인접 자산은 폴리곤(footprint) 입력(`_footprint_points`로 격자 분해 지원); 임해자산은 Aqueduct 연안침수 ingest |
| B2 | **폭염 사망의 검증 불가(합성 데이터)** | E-OBS 관측 연결로 2022년 최극단 재현 검증 (`test_observed_grid_reproduces_the_real_ranking…`). 2026-09-11: **한국 기저사망률 2개**(<65 / 65+)를 KOSIS 사망원인통계 2024 실측(DT_1B34E01)으로 교체 — β·MMT·밴드·용량곡선 미변경, 65+ 노출 비중(0.203, 주민등록) 미변경·출처 미통일. KOSIS-anchored baseline이지 모델 검증은 아님 | 극단 연도 ~2.3배 과소예측 (격자 평활화·UHI·귀속창 차이) | MoMo 5~9월 창 통일 + β 연도별 시계열 재적합 + UHI 보정. **한국 적용 대상 아님**(검증 인프라 부재) |
| B3 | **운영 복잡도** — conda 워커·GPL 격리 | `run.command`, `env_climada.yml`, `launch.json`로 기동 자동화 | 다수 자산 일괄 시 병렬 설계 | `max_workers` + 해저드 캐시 재사용(자산 수에 선형) |
| B4 | **대용량 1회 다운로드** — MRIOT 900MB, E-OBS 843MB | 드롭인/카탈로그 캐시로 1회성 처리, 문서화 | 폐쇄망 반입 절차 | 외부망에서 1회 확보 → `~/climada/data/` 반입 |

## C. 미해결 — 국내 특화 (최우선)

재구축본도 CLIMADA **전지구** 데이터를 쓴다. "CLIMADA라서 자동 국내 정밀"이 아니다. 아래는
방법을 확정했으나 **한국 데이터로 실행·검증은 아직 하지 않은** 항목이다.

**검증 원칙: 국내 모델은 국내 실측(재해연보·EM-DAT 한국·보험 손해통계)으로만 검증한다.**
유럽 데이터는 이 표 어디에서도 한국 검증에 쓰이지 않는다. 아래에 유럽이 언급되면 그것은
"코드 경로가 이미 작동함"의 근거일 뿐이다.

| # | 리스크 | 왜 미해결인가 | 해결 방법 (구체) | 이미 준비된 것 |
|---|---|---|---|---|
| C1 🟡신청필요 | **해저드 국내 해상도 부재** — 전지구 이벤트셋의 격자·통계가 한국 지형(산악·도시)을 평활화 | KMA 자료를 아직 주입하지 않음 | ① 기상청 기상자료개방포털(ASOS/AWS 관측, 격자 분석장) → ② 표준화 그리드 JSON으로 변환 → ③ `hazard_convert.convert_grid_to_catalog` → 카탈로그 등록 → 러너가 자동 우선 사용. 미래: 기상청 SSP 기반 남한상세 시나리오를 동일 온램프로 — 2026-09-07부터 `heat_mortality` 러너가 **요청 시나리오 레이어를 우선 조회**하고 없으면 `historical`로 폴백(폴백 사실을 `detail`에 명시). 합성 KMA 파일로 request→SSP 레이어→ImpactCalc 연결 테스트(`tests/test_kma_scenario.py`, CLIMADA 환경). **실제 KMA 파일 실행은 아직 없음.** CLIMADA_METHODS.md §5.8 | 온램프 코드·테스트 존재. 주입 **경로(배관)**는 유럽 E-OBS 격자(827셀×45년)로 작동 확인됨 — 이는 코드가 외부 관측 격자를 받아 러너가 실제로 쓰는지 본 것이며, **한국 모델의 검증과는 무관**. 국내 검증은 C5(재해연보)로만 함 |
| C2 ✅접근확인 | **취약성 곡선 미보정** — 불확실성의 **83%** 발생원(Sobol 실측). 기본 곡선은 글로벌/HAZUS(미국) | 국내 손실자료를 넣지 않음 | 행정안전부 **재해연보**(지역·재해별 피해액, 공개) + EM-DAT 한국 + 보험 손해통계 → `calibration` 실행(현재 TC `v_half` 대상)으로 피해곡선 재적합. 데이터 희소 시 베이지안 사전분포 접근 | 보정 러너 존재(EM-DAT 경로: `CLIMATERISK_EMDAT_PATH`). 2026-09-07부터 결과가 `data/calibrations/`에 출처·기간·목적함수·방법·경계·시각과 함께 저장되고, `tc_impf_default="calibrated"` 옵션으로 이후 실행에 적용됨(`fit_status="fitted"`, 검증 아님). **재해연보 로더는 미작성**(러너는 EM-DAT CSV만 읽음 — `observed_source="disaster_yearbook"`는 데이터 게이트 오류를 반환하고 대체 계열을 쓰지 않음). 2026-09-07부터 **비교가능성 게이트**가 적합 전에 단위·서브페릴 커버리지·공간범위를 검사해 기본적으로 차단한다(집계 관측 vs 바람 전용 모델, 전국 관측 vs 미선언·점자산 노출). 그 전까지 기본값은 Eberenz WP4 지역 프리셋(190.5). 적재 명세: `docs/OBSERVED_LOSSES_KR_SPEC.md` |
| C3 ⚠️라이선스 | **홍수 해저드의 국내 정합성** — ISIMIP 전지구 하천홍수가 한국 하천망·제방을 반영 못함 | 국내 홍수위험지도 미연동 | 환경부 **홍수위험지도**(공개 래스터) → 표준화 그리드 → 카탈로그 `river_flood/KOR`. 연안은 WRI Aqueduct 연안침수 ingest(플랫폼 내 지원) | Aqueduct ingest 러너, 래스터 온램프 |
| C4 ✅접근확인 | **노출의 국내 정밀도** — WorldPop 1km는 있으나 건물·자산가치 격자 없음(GPW 로그인) | GPW 미확보, 건물 데이터 미연동 | ① GPW v4 확보(무료 Earthdata) → LitPop KOR ② 국토부 건축물대장/GIS건물통합정보 또는 OSM(.osm.pbf, Geofabrik) → 건물 풋프린트 노출 | `kor_ppp_2020_1km` 디스크 보유, OSM 러너 존재 |
| C5 🟡틀만존재 | **국내 검증 기준 부재** — 모델값을 대조할 실측 손실 | 피해 페릴용 국내 벤치마크 미연동 | 재해연보 연도·지역별 피해액을 **피해 페릴의 MoMo 상당물**로 사용 → 태풍·홍수 AAI/연도별 손실을 대조 | 비교 프레임워크 존재(`worker/climaterisk_worker/validation.py`: `ObservedSeries`·bias/mae/rmse/ratio/coverage·`event_comparison`(TDR·EDR형)·`comparability_report`). **관측 계열은 아직 없음** — 테스트는 합성 숫자 전용. 연도별 대조는 합성 앙상블에서 부모 트랙 날짜가 중복되므로 원본 트랙 부분집합(`orig`)이 필요하고, `check_annual_basis`가 미선언·미필터 사례를 연도별 검증으로 인정하지 않음(명세 §7) |
| C6 ✅접근확인 | **태풍 이벤트셋의 한반도 적합성** | IBTrACS는 서태평양(WP) 분지를 포함하나 국내 상륙 통계로 보정 안 됨 | 국가태풍센터/RSMC Tokyo best track으로 빈도·강도 분포 대조, `calibration`으로 `v_half` 보정(C2와 연동) | `TCTracks` 생성 러너 존재 |

### 국내 특화 실행 순서 (권장)

1. **C2 취약성 보정** — 재해연보 + EM-DAT 확보 → `calibration`. 불확실성 83%를 직접 줄이는 최대 레버. (데이터 확보 1~2일, 보정 1일)
2. **C1 해저드 주입** — KMA 격자/관측 → `hazard_convert` → 카탈로그. E-OBS 선례가 있어 코드 작업은 작고, 데이터 사양 확정이 관건. (2~3일)
3. **C5 검증 루프** — 재해연보 연도별 손실 vs 모델 연도별 손실 대조 → 오차 분해. (1일)
4. **C3·C4** — 홍수지도·건물 노출로 정밀도 확장. (각 2~3일)
5. **C6** — 태풍 best track 대조는 C2에 흡수.

> 위 소스의 접근성은 2026-09-02에 웹으로 직접 확인했다 → **E절**. 확인 결과 C1은 '신청 양식',
> C3은 '비상업 라이선스', C2의 EM-DAT는 '상업 이용 유료'라는 조건이 붙는다. 격자 사양(투영·차원)은
> 파일을 실제 내려받아야 확정된다.

### E. 국내 데이터 소스 접근성 확인 결과 (2026-09-02 웹 확인)

확인 방법: 각 포털 페이지·공공데이터포털 메타데이터·공식 문서를 직접 열어 읽었다. "확인"은 페이지에 명시된
내용이고, "추정"은 명시가 없어 검색 요약에 의존한 것이다. 파일을 내려받아 열어본 것은 없다.

| C# | 소스 | 접근 | 조건 | 포맷·범위 | 온램프 적합성 | 비고 |
|---|---|---|---|---|---|---|
| C1 | **기상청 API허브** (apihub.kma.go.kr) | ✅ 무료 | 회원가입 + 연락처 등록 → 인증키 | ASOS/AWS 지상관측, 태풍, 수치모델 등 API | 관측 시계열 → 격자화 필요 | 호출 제한 미표기 |
| C1 | **기상자료개방포털** (data.kma.go.kr) 종관 ASOS 파일셋 | ✅ 무료 | **로그인 필수** | CSV(zip), 분/시/일/월/연, 1904~; 1회 조회 상한 일자료 10년 | 지점 자료 → 격자화(IDW/크리깅) 필요 | 파일셋 41,459개, 지점·연도별 |
| C1 | **기상청 SSP 남한상세 1km** (기후정보포털 → 2025년부터 **기후변화 상황지도**로 이관) | ✅ 무료(확인) | 이용목적·분야·소속 입력 + 동의 후 '데이터 다운로드 신청'. **기후정보포털 자체 다운로드는 2024-07-01 종료**(활용매뉴얼 v5.1 명시), 상황지도에서 제공 | NetCDF·ASCII(tar.gz). **격자 확정: 751(경도)×601(위도), 0.01°(≈1 km), 시작 (33.0N, 124.5E), 위경도 직각좌표 WGS84, 결측 -9990.** 변수 TA/TAMAX/TAMIN/RN/RHM/WS/SI, 일/월/연. 과거 MK-PRISM v2.1 **2000–2019**, 미래 **2021–2100**. SSP126/245/370/585 × 5개 RCM(HadGEM3-RA·WRF·CCLM·GRIMs·RegCM4) + 앙상블(5ENSMN) | ✅ 정규 위경도 격자라 `hazard_convert`에 재투영 없이 직결. TAMAX daily → 폭염 degC/degC-days, RN daily → 극한강수 | 공공기관 정책 수립 시 **의무 사용** 표준 시나리오. 파일명 예 `AR6_SSP585_5ENSMN_skorea_TAMAX_gridraw_daily_2021_2100_nc.tar.gz` |
| C1 | **농촌진흥청 SSP** (weather.rda.go.kr) | ✅ 무료 | 이용목적 설문만, 로그인 없음(추정) | zip; 1km 격자를 **167개 시군구로 집계**, 18 GCM + 앙상블 + OBS, 1981–2100 일자료, SSP1/2/3/5 | 🟡 행정구역 단위라 격자 온램프에는 부적합, **자산 단위 대조·검증용**에 적합 | 기상청 1km 원격자의 파생물 |
| C2 | **행안부 통계연보 지역별 자연재난 피해** (data.go.kr 15107316) | ✅ 무료 | 공공데이터포털 인증키(개발계정 1만건/일) | REST/XML, 시도·시군구 × 재해유형(태풍·대설·낙뢰…) × 연도, 재산·인명피해 | 🟡 `calibration` 입력으로 쓰려면 **로더 추가 필요** — 현재 러너는 EM-DAT CSV(`emdat_to_impact`)만 읽음 | **이용허락 제한 없음** (상업 OK) |
| C2 | **재해연보 원문** (mois.go.kr) | ✅ 무료 | 없음 | PDF (2024년판 확인) | 표 추출 필요 | 위 API의 원자료 |
| C2 | **EM-DAT** (public.emdat.be) | 🟡 조건부 | 등록 필수. 학술·비영리·공공·언론은 무료. **영리 기업은 별도 라이선스 + 연회비** | xlsx(추정) | 기존 `CLIMATERISK_EMDAT_PATH` 경로 | **자산운용사 고객 납품물에 쓰면 상업 이용 → 라이선스 검토 필수**. 파생 DB 생성 금지 조항 |
| C3 | **한강홍수통제소 홍수위험지도** (data.go.kr 15077744) | ✅ 무료 | 로그인 없음 | **SHP**, 2,770행, 침수범위 폴리곤 + 침수심 5등급(≤0.5/0.5–1/1–2/2–5/>5 m), 국가하천 100/200/500년, 전국, 2025-11-27 갱신 | ✅ 폴리곤 래스터화 → `river_flood/KOR` 카탈로그 | ⚠️ **공공저작물 제4유형: 출처표시·상업적 이용금지·변경금지** — 상업 납품·재가공 시 별도 협의 필요 |
| C3 | **홍수위험지도 정보제공포털** (data.floodmap.go.kr) | ✅ 열람·다운로드, **로그인 없음(확인)** | 공공누리 제4유형(출처표시·상업적 이용금지·변경금지) | **SHP, EPSG:5186**, 데이터셋 51개: 국가하천 하천범람지도 100/200/500년·기왕최대(유역별·행정구역별), **도시침수지도 기왕최대**; 유역별 100년 세트는 중권역 zip 84개(0.9 MB~272 MB), 속성은 **범람 범위 폴리곤(SEG_CODE·FLDLV_FREQ·SBSN_CD)만** — 표본 파일(한탄강, 5폴리곤 4.7 km², EPSG:5186 확인) 기준으로 침수심 필드는 없고 포털 표본 표에만 시군구별 침수심 등급 면적이 있음. → 온램프에는 **빈도별 범위 = 이진 침수(1/0) 또는 빈도 역수 강도**로 넣고, 침수심 기반 피해곡선은 별도 침수심 레이어 확보 후 | ✅ 폴리곤 → 래스터화 → `river_flood/KOR` (지방하천은 별도 확인) | 표본 데이터에 시군구코드·권역·중권역·빈도·침수심별 면적 컬럼 |
| C4 | **GPW v4 Rev.11** (earthdata.nasa.gov) | ✅ 무료 | Earthdata Login(무료 가입) | GeoTIFF/ASCII, 30″·2.5′·15′·30′·1° | LitPop KOR 게이트 해제 | SEDAC → Earthdata 이관 중(2026 말까지) |
| C4 | **국토부 GIS건물통합정보** (data.go.kr 15083092 → V-World dsId=18) | 🟡 조건부 | 공공데이터포털 메타는 '로그인 없음'이나 실제 제공처 **V-World는 로그인 + 전용 다운로드 프로그램 설치 필요(확인)**, 용량 표기 500 MB | SHP, 약 666만 건, 전국, 건축물대장 속성 결합 | ✅ 건물 풋프린트 노출 (OSM으로 우선 대체 가능) | 이용허락 제한 없음 |
| C4 | **OSM Geofabrik south-korea-latest** | ✅ 무료 | 없음 | .osm.pbf **273 MB**, 2026-09-01 기준 | 기존 OSM 러너 | ODbL |
| C4 | **KOSIS Open API** (kosis.kr/openapi) | ✅ 무료 | 회원가입 → 활용신청(**자동 승인, 즉시**) → 인증키 | 시군구별 1세별 주민등록인구 (DT_1B04006), 읍면동별 5세별 (DT_1B04005N) | 연령 구조 → 고령 비율 | 행안부 jumin.mois.go.kr에서 CSV 직접도 가능 |
| C6 | **RSMC Tokyo best track** (jma.go.jp) | ✅ 무료 | 없음 | 텍스트, 1951–2026 전체 zip | `TCTracks` 대조 | 공식 포맷 문서 있음 |
| C6 | 기상자료개방포털 태풍경로·목록 | ✅ 무료 | **로그인 필수** | CSV/Excel; 6시간 간격 중심위치·기압·풍속·반경 포함 여부 페이지에서 미확인 | RSMC Tokyo로 대체 가능 | 한반도 영향 태풍 연도별 개수 표 제공 |

**미확인으로 남은 것 (3건)**
1. 1km 시나리오의 **신청 → 제공까지 소요**(즉시 vs 담당자 승인). 격자 투영·차원은 활용매뉴얼 v5.1(117쪽, 2024-12-22)에서 **확정됨**(위 표). 현재 제공 창구는 **기후변화 상황지도 다운로드 페이지**(climate.go.kr/atlas/ana/cdd)로 확인됨: 데이터셋 144개, 필터 시나리오(SSP/RCP)×형태(격자/행정구역/유역/지점)×해상도(남한상세 포함). **로그인 필수 확인**(다운로드 버튼이 `/auth/check-login`을 조회해 미로그인 시 로그인 페이지로 보냄; 아이디/비밀번호 계정, 회원가입 가능). 로그인 후에는 소속·활용분야·사용목적 설문 제출 → 파일 제공. 필요한 17개 파일의 데이터셋/파일 ID는 [KMA_DOWNLOAD_LIST.md](KMA_DOWNLOAD_LIST.md)에 정리. 자료 형식 단서: E-OBS와 같은 정규 위경도 NetCDF이므로 `eobs.py::load_summer_tmax`를 변수명(`tx`→`TAMAX`)·좌표명만 바꿔 KMA 로더로 재사용 가능.
2. floodmap 정보제공포털의 원자료 제공 범위(지방하천·도시침수 SHP 포함 여부).
3. 기상자료개방포털 태풍 자료의 6시간 best-track 필드 구성 — RSMC Tokyo가 대체하므로 실행을 막지는 않음.

**2026-09-02 진행 상황 (자동 다운로드·구현)**
- ✅ 받음: RSMC Tokyo best track 1951–2026 (`~/climada/data/rsmc/bst_all.zip`, 0.7 MB), OSM 한국 (`~/climada/data/osm/south-korea-latest.osm.pbf`, 286 MB).
- ✅ 받는 중(백그라운드): 홍수위험지도 **유역별 100/200/500년 국가하천 하천범람지도** SHP 전체(100년 세트 84개 zip 1.7 GB; 200·500년은 비슷한 규모) → `~/climada/data/floodmap/<데이터셋>/RFM_SBSN_NTN_<중권역코드>_<빈도>.zip`. 다운로더 `scripts/fetch_floodmap_kor.py`(로그인 없음, 포털 API `POST /api/shp/download`, 크기 검증·재시도 안전). 로그 `~/climada/data/floodmap/download.log`. **라이선스: 비상업·변경금지 → 내부 검증 전용.**
- ⛔ 로그인 게이트라 자동 불가: 기상청 남한상세(계정 필요), V-World 건물(계정+프로그램), GPW(Earthdata), KOSIS/공공데이터포털 API 키. 계정 생성·비밀번호 입력은 자동화 대상이 아니므로 사용자가 직접 로그인해야 한다.
- ✅ 구현 완료: KMA 남한상세 어댑터 `worker/climaterisk_worker/kma_scenario.py`(tar.gz 해제·파일명 파싱·계절 추출·블록 평균·결측 처리) + 공용 온램프 `heat_mortality.grid_from_summer_tmax` + CLI `scripts/heat_korea.py`(files/register/check/run) + 러너의 국가별 고령 비율(`COUNTRY_SHARE_OVER65["KOR"]=0.203`). 스펙 준수 합성 NetCDF로 tar.gz → 카탈로그 → CLIMADA 러너까지 **테스트 9건 통과**(`tests/test_kma_scenario.py`). 실제 파일은 `~/climada/data/kma/`에 넣고 `heat_korea.py register`만 실행하면 된다.

**라이선스가 실행 순서를 바꾼다**
- 재해연보 API·GIS건물·GPW·OSM·RSMC: 상업 이용 제한 없음 → 자산운용사 납품에 바로 사용 가능.
- **홍수위험지도 SHP**: 상업적 이용·변경 금지. 내부 검증에는 쓸 수 있으나 **고객 납품 산출물의 입력으로 쓰려면 한강홍수통제소와 협의**해야 한다. 협의 전까지 C3는 "내부 검증 전용"으로 격하.
- **EM-DAT**: 영리 이용은 유료. 재해연보 API가 국내 범위를 더 세밀하게 덮으므로 **C2는 EM-DAT 없이 재해연보만으로 진행 가능**.

**확인 후 조정된 실행 순서**
1. **C2** — 재해연보 API 인증키(즉시) → 시군구×재해유형×연도 피해액 적재 → `calibration`. 라이선스 장애 없음. **가장 먼저, 가장 안전.**
2. **C4** — KOSIS 키(즉시) + GPW Earthdata 가입 + GIS건물 SHP 다운로드. 모두 무료·즉시.
3. **C1** — 기후정보포털 1km 신청(소요 미확인) 병행 시작; 대기 중엔 API허브 ASOS로 관측 격자화 선행.
4. **C6** — RSMC Tokyo 텍스트 → `TCTracks` 대조 (즉시).
5. **C3** — 홍수위험지도는 **내부 검증용으로만** 먼저 적용, 상업 이용 협의는 별도 트랙.

---

## D. 두 도구 공통으로 남는 리스크

| 리스크 | 왜 공통인가 | 대응 |
|---|---|---|
| 피해곡선의 해외 기준 (HAZUS=미국, CLIMADA 기본=글로벌·지역 프리셋) | 2026-09-07부터 재구축본 기본값은 국가별 Eberenz/JRC **지역 프리셋**(한국: WP4 190.5 / JRC Asia)이지만 국내 실측 검증은 없음 — 국내 보정은 엔진과 무관한 데이터 작업 | C2 |
| 배출·전환 파라미터의 자산별 실측 부재 → 섹터 프록시 의존 | 두 도구 모두 프록시 사용 | 자산별 Scope-1 실측 입력 |
| 시나리오 다양성 | 단일 RCP/SSP로 보고하면 민감도 누락 | RCP2.6/4.5/6.0/8.5 병렬 산출 |
