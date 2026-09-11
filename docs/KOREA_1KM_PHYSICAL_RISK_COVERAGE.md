# 한국 남한상세 1 km 물리위험 커버리지 감사

감사일 2026-09-11 · 기준 `main` `0afac50` (+ 미머지 브랜치는 행마다 표기) · **코드·카탈로그·디스크에서만
채웠다.** "KMA 1 km 자료가 존재한다"와 "climaterisk가 그것으로 실제 물리위험을 계산한다"는 다른 문제이므로,
모든 셀은 `파일:줄` 또는 카탈로그 매니페스트 항목을 근거로 한다. 근거가 없는 셀은 **Missing**이다.

관련: [CLIMADA_COVERAGE.md](CLIMADA_COVERAGE.md)(엔진 기능 커버리지) · [GAP_ANALYSIS_KO.md](GAP_ANALYSIS_KO.md)(G1–G8) ·
[RISK_REGISTER.md](RISK_REGISTER.md)(C1–C6) · [CLIMADA_METHODS.md](CLIMADA_METHODS.md) §5(페릴별 방법).

## 0. 등급

| 등급 | 뜻 |
|---|---|
| **Native** | CLIMADA/petals가 제공하는 해저드 또는 곡선을 그대로 사용 |
| **Custom** | 이 저장소가 직접 구현(해저드 정의·곡선·온램프) |
| **Partial** | 체인의 일부만 존재(예: 해저드는 있으나 미래 없음, 인제스터는 있으나 KOR 레이어 없음) |
| **Missing** | 코드에 해당 체인 단계가 없음 |
| **Not validated** | 계산은 되지만 **한국 실측**과 대조된 적 없음 — 이 문서에서 "검증"은 한국 관측 손실·사망 대조만 뜻한다 |

## 1. KMA 남한상세 1 km 자료 — 무엇이 있고 무엇을 쓰는가

기후변화 상황지도(climate.go.kr/atlas/ana/cdd)의 남한상세 격자는 **기후요소 7종**을 제공한다
(RISK_REGISTER E절): `TA` 평균기온 · `TAMAX` 최고기온 · `TAMIN` 최저기온 · `RN` 강수량 · `RHM` 상대습도 ·
`WS` 풍속 · `SI` 일사량. 관측(MK-PRISM v3.1, 0.005°)과 SSP 시나리오(5ENSMN, 0.01°) 모두.

| KMA 변수 | 디스크 | 코드가 읽는가 | 근거 |
|---|---|---|---|
| **TA** 평균기온 | ✅ MK-PRISM 2000–2019 + SSP245/585 2021–2030 (`~/climada/data/kma/`) | ✅ | `kma_scenario.py:56` `DEFAULT_VARIABLE = "TA"`; `load_summer_tmax` |
| TAMAX 최고기온 | ❌ | ❌ | 2026-09-09 이전에 읽었고 지금은 코드 어디에도 없음 (`heat_korea.py:84` 드리프트 기록) |
| TAMIN 최저기온 | ❌ | ❌ | 참조 없음 |
| RN 강수량 | ❌ | ❌ | 참조 없음 |
| RHM 상대습도 | ❌ | ❌ | 참조 없음 |
| WS 풍속 | ❌ | ❌ | 참조 없음 |
| SI 일사량 | ❌ | ❌ | 참조 없음 (`build_impf_presets.py:50`의 `"SI"`는 남인도양 분지 코드) |

**결론: KMA 1 km 자료 중 climaterisk가 소비하는 것은 `TA` 하나이고, 그것도 폭염 두 페릴에서만이다.**
`grep -rn 'TAMAX\|"RN"\|"RHM"\|"WS"\|TAMIN' worker scripts` → `kma_scenario.py`의 문서 주석 외 0건.

해상도: 로더는 0.01°(관측은 0.005°를 짝수 노드 서브샘플링)로 정합한 뒤 **`coarsen=5` → 0.05°(≈5 km, 4,312셀)**로
뭉갠다(`kma_scenario.load_summer_tmax`, `heat_korea.py --coarsen`). 즉 폭염 경로조차 **1 km로 계산하지 않는다** —
5 km다. 이는 취약성 파라미터가 1 km 근거를 갖지 않는 상태에서의 의도적 선택이다(HEAT_MORTALITY_PROVENANCE.md).

## 2. 감사 체인 (페릴마다 8단계)

```
① KMA 1 km 자료 존재?      ② climaterisk 인제스션 존재?     ③ Hazard 객체 생성(KOR 레이어)?
④ Exposure 매칭?           ⑤ Impact function 존재?         ⑥ ImpactCalc 실행?
⑦ 현재/미래 시나리오 작동?  ⑧ 한국 실측 검증?
```

## 3. 6축 매트릭스 — Hazard × Exposure × Vulnerability × Resolution × Scenario × Validation

KOR 카탈로그 매니페스트(`data/hazard_db/`, 2026-09-11)에 있는 레이어: `earthquake observed 2020` ·
`heat_mortality historical/rcp45/rcp85` · `heatwave historical/rcp45/rcp85` · `river_flood rcp45 2050` ·
`tropical_cyclone rcp45 2040` · `wildfire historical 2020`. **그 외 페릴의 KOR 레이어는 없다.**

| 물리위험 | 한국 해저드 (KMA 1 km?) | Exposure | Vulnerability | 해상도(KOR) | 시나리오 | 한국 검증 | 종합 |
|---|---|---|---|---|---|---|---|
| **폭염 사망** `heat_mortality` | **KMA TA ✅** — 초과도일, `kma_scenario`→`heat_mortality.grid_from_summer_tmax`→`hazard_convert` | 인구: 점자산 headcount / WorldPop KOR 1 km(`exposures._raster_exposure`) / `COUNTRY_SHARE_OVER65["KOR"]=0.203` | **Custom** 용량-반응. β·밴드 indicative; 기저사망률 `main`=legacy indicative, **PR #16(미머지)**=KOSIS 2024 anchored | 0.05° (4,312셀) — 1 km 아님 | historical + SSP245/585 **2030 앵커만**(2021–2030 10시즌, 20시즌 창의 절반) | ❌ 사망 대조 없음. 해저드 수준: 2018 1위·2003 최하위 재현(`test_real_kma_files…`, **PR #15 미머지**) | **Custom · Partial · Not validated** |
| **폭염 생산성** `heatwave` | **KMA TA ✅** — 시즌 p95(`heat_korea._heatwave_grid`) | 점자산 value | Indicative 램프 `wf_max_mdd`(`_CATALOG_PERILS["heatwave"]`, G4) | 0.05° | historical + SSP 2030 | ❌ | **Custom hazard + indicative curve · Not validated** |
| **열 스트레스(WBGT/UTCI)** | ❌ KMA `RHM`·`WS`·`SI` 미배선 | — | — | — | — | — | **Missing** (`heatwave` 힌트는 "ERA5-HEAT/UTCI 인제스트"를 요구하나 인제스터 없음) |
| **극한강수 / 내수침수(pluvial)** | ❌ KMA `RN` 미배선; 페릴 자체 없음 | — | — | — | — | — | **Missing** — G7 구조 공백 |
| **하천홍수** `river_flood` | ❌ KMA 아님 — CLIMADA Data API ISIMIP 전지구(`physical.py:508-525`). 환경부 홍수위험지도 SHP 4.9 GB **디스크에만 있고 소비자 0건**(`fetch_floodmap_kor.py`만 참조; 공공누리 4유형) | 점자산 value(+`_footprint_points` 폴리곤) | **Native(JRC Asia 주거)** 프리셋 `flood_jrc_asia`(`vulnerability.flood_regional_preset`) — 한국 보정 아님 | 5,708 센트로이드(Data API 국가 격자, 1 km 아님) | KOR 레이어 `rcp45 2050` 1개 — **소스는 `rcp60`**(§5 불일치) | ❌ | **Native · Partial · Not validated** |
| **해안침수** `coastal_flood` | ❌ 카탈로그 전용(`physical.py:822-833`), **KOR 레이어 없음**; Aqueduct 연안 인제스터 존재(`ingest.py:561`) | 점자산 | Native(JRC) 수심-피해 | — | 현재/미래 카탈로그 키 있음, KOR 미등록 | ❌ | **Partial(배관만) · Missing(레이어)** |
| **태풍 바람** `tropical_cyclone` | ❌ KMA `WS` 미배선 — IBTrACS 합성 43,560 이벤트(Data API) | 점자산 value | **Native(Emanuel)** + **Eberenz WP4 프리셋** `v_half=190.5`(`vulnerability.tc_regional_preset`) — 지역 프리셋, 한국 보정 아님 | 5,708 센트로이드 | `rcp45 2040` 레이어; 2060/2080 Data API 조회 가능 | ❌ 보정 BLOCKED(재해연보 집계 vs 바람 전용, `calibration_gate`). 진단만: `analysis/tc-capture-kr`(미머지) | **Native · Not validated** |
| **태풍 강우** `tc_rain` | ❌ KMA `RN` 미배선 — petals `TCRain` R-CLIPER 인제스터 존재(`ingest.py:564`), **KOR 레이어 없음** | 점자산 | Indicative 램프(`_CATALOG_PERILS["tc_rain"]`, mm) | — | — | ❌ | **Partial(인제스터) · Missing(레이어) · indicative curve** — G5 |
| **태풍 해일** `tc_surge` | ❌ KMA 아님 — `TCSurgeBathtub`(바람 레이어 + Copernicus DEM). DEM 모자이크 `data/hazard_db/dem/portfolio_dem.tif` 존재(포트폴리오 bbox 한정) | 점자산 | Native(JRC) 수심-피해 | DEM ≤250 px 데시메이션(`ingest.py:58`) | 바람 레이어의 시나리오를 따름 | ❌ | **Native · Partial · Not validated** |
| **태풍 복합(바람+강우+해일)** | — | — | ❌ 결합 없음 — 각 페릴 독립 합산(G5) | — | — | ❌ | **Missing** |
| **가뭄** `drought` | ❌ KMA `RN`+`TA`로 SPEI 산출 가능하나 인제스터 없음; 카탈로그 전용 | 점자산 | Indicative 램프(SPEI) | — | — | ❌ | **Missing(레이어·인제스터)** |
| **산불** `wildfire` | ❌ KMA 아님 — Data API `historical` 20 이벤트 | 점자산 | **Native-형 sigmoid** `from_sigmoid_impf`(`physical.py:605`) — 무임계, G3 | 5,708 센트로이드 | **현재만** — 러너가 `"historical"` 하드코딩(`physical.py:589`) | ❌ | **Native · Partial(미래 없음) · Not validated** |
| **한파** | ❌ KMA `TA`/`TAMIN`로 가능하나 **페릴 자체 없음**(`perils.json`·`_RUNNERS`에 부재) | — | — | — | — | — | **Missing** |
| **대설** | ❌ 페릴 자체 없음 | — | — | — | — | — | **Missing** |
| **산사태** `landslide` | ❌ 카탈로그 전용(NASA COOLR 힌트), KOR 레이어 없음; DEM 경사 미사용 | 점자산 | Indicative 램프(확률) | — | — | ❌ | **Missing(레이어)** |
| **우박** `hail` | ❌ KOR 레이어 없음(MeteoSwiss 힌트 — 한국 소스 없음) | 점자산 | Indicative 램프(cm) | — | — | ❌ | **Missing** |
| **작물수확** `crop_yield` | ❌ ISIMIP 힌트, KOR 레이어 없음 | 농업 | Indicative | — | — | ❌ | **Missing** |
| **저수량** `low_flow` | ❌ GloFAS 힌트, KOR 레이어 없음 | 점자산 | Indicative | — | — | ❌ | **Missing** |
| 지진 `earthquake` | 기후 무관 — Data API `observed` 41,710 이벤트, KOR 레이어 ✅ | 점자산 | Native | 5,708 센트로이드 | N/A | ❌ | **Native · Not validated** (KMA 범위 밖) |
| 유럽 폭풍 `european_windstorm` | 한국 비대상(`storm_europe`) | — | — | — | — | — | N/A |

### 읽는 법

* **KMA 1 km를 실제로 쓰는 행은 2개**(폭염 사망·폭염 생산성)이고, 둘 다 `TA` 하나에서 나온다.
* 그 외 한국 레이어 4개(태풍·하천홍수·산불·지진)는 **CLIMADA Data API 전지구/국가 산출물**이다 — 1 km가 아니라
  5,708 센트로이드 격자이고, 한국 지형·하천망·제방을 반영하지 않는다(RISK_REGISTER C1·C3).
* **한국 실측 검증 열은 전부 ❌다.** 폭염은 해저드 수준(연도 순위)만, 태풍은 진단(포착률 아님)만 있고, 둘 다
  미머지 브랜치에 있다. 손실·사망 자체를 대조한 페릴은 없다.

## 4. 페릴별 8단계 체인 — 근거

### 4.1 폭염 사망 `heat_mortality` — 유일하게 ①~⑦이 이어진 KMA 경로

| 단계 | 상태 | 근거 |
|---|---|---|
| ① KMA 자료 | ✅ TA 3 아카이브 | `~/climada/data/kma/MKPRISM_MKPRISMv31_TA_…_nc.tar.gz`, `AR6_SSP{245,585}_…_asc.tar.gz` |
| ② 인제스션 | ✅ (**PR #15**에서 실파일 형식 흡수: asc 스트리밍·`skorea` 유무·0.005° 서브샘플·공통 풋프린트) | `kma_scenario.load_summer_tmax`, `common_footprint` |
| ③ Hazard | ✅ 3 레이어, 4,312 센트로이드 | 매니페스트 `heat_mortality historical/rcp45/rcp85` |
| ④ Exposure | ✅ 연령 2행/자산, `value_unit="persons"` | `physical.py:1177-1195` |
| ⑤ Impf | ✅ Custom `mdd = min(m₀·a·D^b, 1)` | `heat_mortality.build_impact_functions` |
| ⑥ ImpactCalc | ✅ | `physical._impact` |
| ⑦ 시나리오 | 🟡 요청 시나리오 우선·`historical` 폴백 명시(`_resolve_heat_hazard`); **2030 앵커만 채워짐** | `heat_korea.FUTURE_WINDOWS` 4창 중 1창, 10시즌 |
| ⑧ 한국 검증 | ❌ 사망 대조 없음. 해저드: 2018 1위(3.7×)·2003 최하위(PR #15 테스트) | KOSIS 사망 시계열 대조 미착수 |

취약성 근거: β 0.010/0.034 indicative(출처 없음), MMT 93백분위 외부(Kim 2020), 적응기울기 0.8 외부(Tobías 2021),
기저사망률 `main` legacy indicative → **PR #16** KOSIS 2024 외부. 상세: `HEAT_MORTALITY_PROVENANCE.md`.

### 4.2 폭염 생산성 `heatwave`
①②③⑥⑦ 폭염 사망과 동일 파이프(`heat_korea.register_window`가 두 그리드를 함께 등록). ⑤는 **indicative 램프**
(`_CATALOG_PERILS["heatwave"]`, G4). ⑧ 없음. §5의 라벨 불일치 참조.

### 4.3 하천홍수 `river_flood`
① KMA 무관. **환경부 홍수위험지도 SHP(100/200/500년, 4.9 GB)**를 `fetch_floodmap_kor.py`로 받아두었으나
②가 없다 — `grep -rln floodmap worker scripts` → 페치 스크립트 1건. 라이선스(공공누리 4유형: 비상업·변경금지)도
미해결(RISK_REGISTER C3). ③ Data API ISIMIP KOR 레이어 1개. ⑤ JRC Asia(외부 프리셋, 2026-09-07 G2 해소).
⑧ 없음(재해연보 호우 열은 F1 로더가 읽을 수 있으나 홍수 러너와 연결 안 됨).

### 4.4 태풍 `tropical_cyclone` / `tc_rain` / `tc_surge`
바람: ③ Data API 합성 43,560 이벤트, ⑤ Emanuel+Eberenz WP4. 강우: ② petals `TCRain` 인제스터 있음, ③ KOR
레이어 없음, ⑤ indicative 램프. 해일: ② `copdem` 인제스터·DEM 존재, ③ 실행 시 파생(카탈로그 레이어 아님), ⑤ JRC.
**결합 없음**(G5). ⑧ 재해연보 태풍 열은 바람+해일+호우 집계라 바람 전용 곡선 보정을 `calibration_gate`가 차단
(`OBSERVED_LOSSES_KR_SPEC.md` §6-1); 연도별 진단(`analysis/tc-capture-kr`)은 검증이 아니다.

### 4.5 KMA로 가능하지만 페릴/인제스터가 없는 것
| 후보 | 필요한 KMA 변수 | 현재 |
|---|---|---|
| 극한강수·내수침수 | `RN` 일자료 | 페릴 없음(G7), 변수 미배선 |
| 태풍 강우(실측 기반) | `RN` | petals R-CLIPER 합성만, KMA 연결 없음 |
| SPEI 가뭄 | `RN` + `TA` | 인제스터 없음 |
| 한파 사망/생산성 | `TA`/`TAMIN` | 페릴 없음 |
| 대설 | 강설 파생(제공 안 됨) | 페릴 없음 |
| 열 스트레스(WBGT) | `TA`+`RHM`(+`WS`,`SI`) | 페릴 없음(`heatwave`는 p95 기온) |
| 바람 해저드(비태풍) | `WS` | 페릴 없음 |

이 표는 **가능성**이지 계획이 아니다. 각 행은 새 해저드 정의·새 취약성 곡선·새 검증이 필요하다.

## 5. 감사 중 발견한 일관성 문제 (수정하지 않음 — 기록)

1. **`heatwave` 레이어 라벨이 틀렸다.** 매니페스트 `source`가 `"season p95 daily Tmax — KMA 남한상세 TA …"`인데,
   2026-09-09부터 입력은 **일평균(TA)**이다. `heat_korea._heatwave_grid`가 `obs.tmax`(하위호환 별칭)의 p95를 계산하고
   레이블 문자열이 갱신되지 않았다. 계산은 맞고 **이름만 틀리다.**
2. **KOR `river_flood` 레이어가 `rcp45`로 등록됐는데 소스는 `rcp60`이다**(`CLIMADA Data API (river_flood, rcp60, cached)`).
   시나리오 키와 실제 강제력이 다르다. 매니페스트 `climate_scenario`를 신뢰하면 안 되는 사례.
3. **홍수위험지도 4.9 GB가 소비자 없이 디스크에 있다.** 다운로드 스크립트만 있고 온램프가 없다.
4. `_CATALOG_PERILS["heatwave"]`의 힌트(`"ingest a heat (ERA5-HEAT/UTCI) hazard first."`)와 실제 KOR 레이어 소스(KMA TA p95)가
   다른 물리량을 가리킨다.

## 6. 상태 요약

| 집계 | 값 |
|---|---|
| `perils.json` 페릴 | 15 |
| KOR 카탈로그 레이어가 있는 페릴 | 6 (heat_mortality · heatwave · tropical_cyclone · river_flood · wildfire · earthquake) |
| **KMA 1 km에서 나온 KOR 레이어** | **2 (heat_mortality · heatwave) — 모두 `TA`** |
| KMA 변수 7종 중 코드가 소비하는 것 | **1 (`TA`)** |
| 실제 계산 해상도(KMA 경로) | 0.05° ≈ 5 km (1 km 아님) |
| 미래 시나리오가 KOR에서 작동하는 페릴 | 3 (heat_mortality · heatwave 2030 앵커 / tropical_cyclone 2040; river_flood 2050은 키 불일치) |
| **한국 실측(손실·사망) 검증 완료 페릴** | **0** |
| 페릴로 존재조차 않는 한국 주요 위험 | 한파 · 대설 · 내수침수 · 열 스트레스 · 복합재해 |

**한 줄 결론**: "CLIMADA를 썼다"는 참이고, "한국 1 km 물리위험을 계산한다"는 **폭염 두 페릴의 해저드 단계까지만
참**이며 그것도 5 km로 계산한다. 나머지는 전지구 산출물·지역 프리셋·지시적 램프이고, 한국 실측 검증은 어느 페릴도
끝나지 않았다.

---

# 제2부 — KMA 변수 → 물리위험 방법론 인벤토리 (2026-09-11)

제1부는 "무엇이 구현돼 있는가"를 페릴 축으로 감사했다. 제2부는 축을 뒤집어 **KMA 남한상세 변수 7종 각각이 어떤
한국 물리위험에 연결될 수 있는가**를 방법론 수준에서 정리한다. **코드 변경 없음** — 새 러너·새 `ImpactFunc`·새
인제스션·해상도 변경·미래 시나리오 구현을 하지 않았다. 아래 모든 "가능"은 *물리적 경로가 존재한다*는 뜻이고
*구현돼 있다*는 뜻이 아니다.

두 원칙을 문서 전체에 적용한다.

```
rainfall ≠ flood          강수 입력이 있다고 홍수 해저드가 생기지 않는다 — 수문·수리 모델이 사이에 있다
Fetched ≠ Implemented     디스크에 받아둔 자료(홍수위험지도 4.9 GB)는 worker 소비자가 없으면 미구현이다
```

## 7. KMA 변수 인벤토리 — 7종

정의의 1차 출처: 기후변화 상황지도 카드의 기후요소 버튼 라벨(2026-09-11 화면: 평균기온 · 최고평균기온 ·
최저평균기온 · 강수량 · 상대습도 · 풍속 · 일사량)과 `kma_scenario.py` 모듈 docstring이 인용하는 기상청
기후변화 시나리오 활용매뉴얼 v5.1(2024-12). **단위·집계 정의는 이번 패스에서 매뉴얼을 다시 열어 재검증하지
않았다** — 표에 "매뉴얼 미재확인"으로 표시한다. 포털 페이지는 JS 렌더링이라 정적 HTML에서 라벨을 추출할 수 없었다.

공통: 남한상세 격자, 관측 MK-PRISM v3.1 0.005°(1201×1501) / SSP 5ENSMN 0.01°(601×751), 일자료(윤년 366행),
결측 −9990. 파일 규칙은 `kma_scenario._NAME_RE`(`AR6_<SSP>_5ENSMN_skorea_<VAR>_gridraw_daily_<y0>_<y1>_asc` /
`MKPRISM_MKPRISMv31_<VAR>_…_nc`).

### 7.1 TA — 평균기온 (일평균)

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일평균기온, °C (포털 라벨 "평균기온"; 매뉴얼 미재확인) |
| 물리적 의미 | 하루 평균 열 부하 — 인용 가능한 한국 임계값(Kim 2020 93백분위)과 MMT 적응 문헌(Tobías 2021)이 모두 이 지표 |
| 원 해상도 | 관측 0.005°, SSP 0.01° |
| 시간 해상도 | 일 |
| **현재 소비자** | **✅ 유일하게 소비되는 변수.** `kma_scenario.DEFAULT_VARIABLE = "TA"`(`:56`) → `load_summer_tmax` → `heat_mortality.grid_from_summer_tmax` → `hazard_convert` |
| 기존 KOR 레이어 | `HM_{historical,rcp45,rcp85}` 3개 · `HW_{historical,rcp45,rcp85}` 3개 (§10) |
| 후보 페릴 | 폭염 사망(구현) · 폭염 생산성(구현, 단 §13-2) · 한파(TA/TAMIN 기반, 미구현) · SPEI 가뭄의 온도 항(미구현) |
| CLIMADA 표현 | `Hazard(haz_type="HM", units="degC-days")` 초과도일 / `Hazard("HW", "degC")` p95 — 둘 다 **CLIMADA container only**, 곡선은 climaterisk custom |
| 필요 노출 | 인구(연령 2행), WorldPop KOR 1 km 보유 |
| 필요 취약성 | 연령별 용량-반응(β·MMT·기저사망률) — β·밴드 indicative, 기저사망률 PR #16에서 KOSIS anchored |
| 현재/미래 | historical 2000–2019 · SSP245/585 **2021–2030만** (2030 앵커, 10시즌) |
| 현재 구현 | **Partial** — 체인 ①~⑦ 연결, ⑧ 없음 |
| 방법론 성숙도 | 중 — 해저드 검증(2018 1위) 있음, 사망 검증 없음 |
| 주요 갭 | 2031–2060 파일 미확보; β 미인용; 5 km 계산 |

**실제 배열 내용(§10에서 HDF5 직접 읽음)**: `HM_historical` 4,312 센트로이드, 간격 정확히 0.0500°, 20 이벤트(연도명
2000–2019), 초과도일 중앙값 4.93 · 최대 90.1 °C·days, 빈도 합 1.0. `HW_historical` 같은 격자, 시즌 p95 값
**18.7–32.3 °C** — 이 범위는 일*평균*의 p95이며 일최고의 p95(한국 여름 ~33–36 °C)가 아니다. 파일명·매니페스트
`source`는 "daily Tmax"라고 적혀 있다(§13-1).

### 7.2 TAMAX — 최고기온

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일최고기온, °C (포털 라벨 "최고평균기온" — 일최고기온의 기간 평균을 뜻하는 표기로 보이나 매뉴얼 미재확인) |
| 물리적 의미 | 하루 최고 열 부하 — 폭염특보 기준(일최고 33/35 °C), 열 스트레스 상한 |
| 원/시간 해상도 | 동일, 일 |
| 현재 소비자 | ❌ 없음. 2026-09-09 이전 소비했고 `heat_korea.py:84`가 드리프트 사고를 기록. 디스크 파일도 없음 |
| 기존 KOR 레이어 | 없음 (HW 레이어는 이름과 달리 TA 기반) |
| 후보 페릴 | 폭염특보형 heatwave(일최고 임계 초과일수) · Tmax 기반 사망 관계 · 열대야는 **TAMIN**의 영역이라 제외 |
| CLIMADA 표현 | `Hazard("HW", "degC")` 또는 초과일수 — container only |
| 필요 노출 / 취약성 | 인구·노동인구 / **Tmax 기준으로 추정된 노출-반응** — 현재 모델의 β·MMT는 일평균 기준이라 재사용 불가(지표 혼용 금지) |
| 현재 구현 | **Missing** (변수 미배선) |
| 성숙도 | 낮음 |
| 주요 갭 | 현 `heatwave` 램프(`[0,30,35,40,45] °C`)는 Tmax/열지수 스케일로 설계된 듯하나 TA p95를 먹고 있다(§13-2). TAMAX를 넣으면 램프와 지표가 맞아지지만 그건 **새 해저드 정의**다 |

### 7.3 TAMIN — 최저기온

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일최저기온, °C (포털 라벨 "최저평균기온"; 매뉴얼 미재확인) |
| 물리적 의미 | 야간 최저 — 열대야(≥25 °C), 한파(≤−12 °C 특보), 냉해 |
| 현재 소비자 | ❌ 없음 |
| 기존 KOR 레이어 | 없음 |
| 후보 페릴 | **한파 사망/생산성**(페릴 부재) · 열대야(수면·사망 보정 인자) · 냉해/동해(농업) · 대설은 강설 변수가 없어 TAMIN만으로 불가 |
| 러너 존재? | **❌** — `perils.json`·`physical._RUNNERS`·`_CATALOG_PERILS`에 cold/snow 항목 없음(grep 결과: `windstorm_impf` 1건만, 유럽 폭풍) |
| CLIMADA 표현 | cold-arm 용량-반응이 필요 — 현 `heat_mortality`는 "The cold arm is a different peril and out of scope"로 명시 제외(`heat_mortality.py` docstring) |
| 필요 취약성 | 한국 저온-사망 곡선(연령별) — 저장소에 없음 |
| 현재 구현 | **Missing** |
| 주요 갭 | 페릴 정의 자체 부재; 한파는 재해연보 원인열(`cold_wave`)이 있어 **관측 손실은 F1 로더로 읽을 수 있다** — 모델만 없다 |

### 7.4 RN — 강수량

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일강수량, mm (포털 라벨 "강수량"; 매뉴얼 미재확인) |
| 물리적 의미 | 하루 강수 총량 — 극한강수·유출·침수의 **강제력**이지 침수 자체가 아님 |
| 현재 소비자 | ❌ 없음 |
| 기존 KOR 레이어 | 없음 (RF 레이어는 ISIMIP 전지구 수문모델 산출, KMA 아님) |
| 후보 페릴 | 세 경로를 **분리**한다(§8) — 극한강수 / 내수침수 입력 / 수문모델 강제력 |
| CLIMADA 표현 | 극한강수: `Hazard("<RN>", "mm")` 직접 가능(container only). 내수·하천침수: **불가** — 수문·수리 모델(외부)이 사이에 필요 |
| 필요 노출 / 취약성 | 자산 / 강수-피해 곡선은 문헌 희소; 침수 수심-피해(JRC)는 **수심**을 입력으로 요구하므로 RN에 직접 못 붙임 |
| 현재 구현 | **Missing** (`rainfall ≠ flood`) |
| 성숙도 | 극한강수 지표 — 중(정의 명확); 침수 — 외부 모델 필요 |
| 주요 갭 | G7 내수침수 구조 공백; 홍수위험지도 4.9 GB `Fetched ≠ Implemented`; 재해연보 `heavy_rain` 열은 읽을 수 있으나 대응 모델 없음 |

### 7.5 RHM — 상대습도

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일평균 상대습도, % (매뉴얼 미재확인) |
| 물리적 의미 | 증발 냉각 억제 — 체감온도·열지수·WBGT의 둘째 인자 |
| 현재 소비자 | ❌ 없음 |
| 후보 페릴 | 인체 열 스트레스(WBGT/UTCI/Heat Index) · 노동생산성(WBGT 기반 ISO 7243) |
| CLIMADA 표현 | 파생 지표 해저드(container only). petals의 ERA5-HEAT/UTCI 인터페이스는 petals 6.1에서 분리되어 **저장소에 없음**(CLIMADA_METHODS §5.8) |
| 필요 취약성 | **별도 역학 함수 필요** — 현 폭염 사망 β는 일평균기온 단일 지표로 정의됐으므로 RHM을 "추가"할 수 없다. 습도를 넣으려면 지표 자체(예: WBGT)를 바꾸고 그 지표로 추정된 노출-반응이 있어야 한다 |
| 현재 구현 | **Missing** |
| 주요 갭 | `_CATALOG_PERILS["heatwave"]` 힌트가 "ERA5-HEAT/UTCI"를 요구하지만 인제스터가 없고, 있는 KOR HW 레이어는 기온 p95다 |

### 7.6 WS — 풍속

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일평균 풍속, m/s (매뉴얼 미재확인; **일최대·순간최대 제공 여부 미확인**) |
| 물리적 의미 | 평균 풍속 — 구조 피해는 **최대풍속/돌풍**이 지배하므로 일평균 WS는 피해 지표로 부족할 수 있다 |
| 현재 소비자 | ❌ 없음 |
| 기존 KOR 레이어 | 없음. TC 레이어(`TC_rcp45_KOR_2040`)는 IBTrACS 합성 트랙의 **1분 지속풍속** 바람장(m/s, 17.5–70.7)이며 KMA 아님 |
| 후보 페릴 | 비태풍 강풍(페릴 부재) · 태풍 바람의 지역 검증 자료(대체 아님) |
| CLIMADA 관계 | **KMA WS로 CLIMADA TC 해저드를 대체하지 않는다** — TC 바람장은 이벤트(트랙) 단위이고 Emanuel 곡선은 지속풍속 정의에 묶여 있다. 유럽 폭풍(`storm_europe`)은 돌풍 기반이며 한국 비대상 |
| 필요 취약성 | 일평균 풍속-피해 곡선은 문헌·저장소 모두 없음 |
| 현재 구현 | **Missing** |
| 주요 갭 | 극한풍 변수 존재 여부부터 확인 필요 |

### 7.7 SI — 일사량

| 항목 | 내용 |
|---|---|
| KMA 정의 | 일사량 (포털 라벨 "일사량"; 단위 MJ/m²/day 추정 — **매뉴얼 미재확인**) |
| 물리적 의미 | 지표 단파 복사 — 태양광 발전량·증발산·WBGT 복사 항·농업 |
| 현재 소비자 | ❌ 없음 (`build_impf_presets.py:50`의 `"SI"`는 **남인도양 TC 분지 코드**로 무관) |
| 후보 용도 | 태양광 발전 잠재량(자산 수익 축 — 물리 *피해* 페릴 아님) · WBGT 복사 항 · 가뭄 증발산 |
| CLIMADA 표현 | `present in data`이나 **`usable as hazard`가 아님** — 일사량은 피해를 일으키는 강도가 아니라 부차 입력이다 |
| 현재 구현 | **Missing** |
| 주요 갭 | 물리위험 해저드로서의 정의 자체가 성립하지 않음; 열 스트레스 지표의 보조 입력으로만 의미 |

## 8. 물리 경로 — 변수를 페릴에 바로 잇지 않는다

```
TA ──┬─ 폭염 사망 (초과도일 → 용량-반응 → 사망)          [구현 · 5 km]
     ├─ 폭염 생산성 (시즌 p95 → 램프)                    [구현 · 램프 스케일 불일치 §13-2]
     └─ 한파 (저온 arm)                                   [페릴 없음]

TAMAX ── 폭염특보형 heatwave / Tmax 사망 관계             [변수 미배선 · Tmax 기준 곡선 필요]

TAMIN ─┬─ 한파 / 냉해                                    [페릴 없음]
       └─ 열대야                                          [보정 인자, 단독 페릴 아님]

RN ────┬─ 극한강수 지표 (RX1day, R95p …)                  [직접 가능 · 미구현]
       ├─ 내수침수 입력 (강수 → 유출 → 침수)               [수리 모델 필요 · G7]
       └─ 수문모델 강제력 (강수 → 유역 수문 → 하천홍수)    [외부 모델 · ISIMIP가 이미 그 산출물]

RHM ─── 열 스트레스 (TA+RHM → WBGT/HI → 생산성/사망)       [지표 교체 + 별도 역학 함수 필요]

WS ────┬─ 비태풍 강풍                                     [페릴 없음 · 극한풍 변수 미확인]
       └─ 태풍 바람 지역 검증 자료                         [CLIMADA TC 대체 아님]

SI ────┬─ WBGT 복사 항 / 증발산                            [보조 입력]
       └─ 태양광 발전량                                   [피해 페릴 아님]
```

## 9. KMA Variable → Physical Risk Methodology Matrix

CLIMADA 구분: **CLIMADA native**(엔진 제공 해저드/곡선) · **CLIMADA container only**(`Hazard`/`ImpactFunc`/`ImpactCalc`
그릇만) · **climaterisk custom**(우리 정의) · **external model needed**(수문·수리·역학 등 외부 모델 선행) ·
**not implemented**.

| KMA variable | Candidate peril | Physical pathway | CLIMADA role | Exposure | Vulnerability | Current status | Gap | Evidence |
|---|---|---|---|---|---|---|---|---|
| TA | heat mortality | 일평균 → 초과도일 → 연령별 용량-반응 → 사망 | **container only + climaterisk custom** (native heat 곡선 없음) | 인구, 연령 2행 | β indicative · MMT 외부(Kim 2020) · 기저사망률 legacy→KOSIS(PR #16) | **Partial** (①–⑦) | ⑧ 사망 검증 0; 2030 앵커만; 5 km | `kma_scenario.py:56`, `heat_mortality.grid_from_summer_tmax`, `physical.py:1173-1195`, HM 레이어 3 |
| TA | heat productivity (`heatwave`) | 시즌 p95 일평균 → indicative 램프 | container only + climaterisk custom | 자산 value | 램프 `[0,30,35,40,45]°C→[0,0,.1,.3,.6]` indicative | **Partial** — 계산되나 램프가 지표와 불일치 | 레이어 최대 32.6 °C < 램프 0.1 지점 35 °C → 손실 구조적 ≈0 | `heat_korea.py:124,134`, `_CATALOG_PERILS["heatwave"]`, HW 레이어 3 |
| TA / TAMIN | cold wave | 저온 arm → 사망/생산성 | not implemented | 인구 | 없음 | **Missing** | 페릴·러너·곡선 전부 부재 | grep cold/snow → 0건 |
| TAMAX | heatwave (일최고 임계) | Tmax → 초과일수/강도 | not implemented | 인구·노동 | Tmax 기준 곡선 필요 | **Missing** | 변수 미배선; 지표 혼용 금지 | `heat_korea.py:84` 드리프트 기록 |
| RN | extreme precipitation | 일강수 → 극한지표(RX1day 등) → 피해 | container only 가능 | 자산 | 강수-피해 곡선 없음 | **Missing** | 지표 정의만 명확 | 소비자 0 |
| RN | pluvial flood | 강수 → 유출 → 침수 수심 | **external model needed** | 자산 | JRC 수심-피해(수심 입력) | **Missing** | G7 구조 공백; 수리 모델 없음 | `GAP_ANALYSIS_KO.md` G7 |
| RN | river flood | 강수 → 유역 수문 → 하천 수심 | **external model needed**; 현 RF는 **CLIMADA native**(ISIMIP 전지구) | 자산 | **native JRC Asia** 프리셋 | **Partial/External** — KMA RN 미사용 | 홍수위험지도 `Fetched ≠ Implemented`; 키 rcp45/소스 rcp60(§13-3) | `physical.py:508-525`, `ingest.py:295,351`, RF 레이어 1 |
| RHM (+TA) | heat stress (WBGT/HI/UTCI) | 기온+습도 → 열지수 → 생산성/사망 | container only; petals UTCI 인터페이스 저장소 밖 | 인구·노동 | **별도 역학 함수 필요** | **Missing** | 지표 교체 없이는 추가 불가 | `_CATALOG_PERILS["heatwave"]` 힌트 vs 실제 레이어 |
| WS | non-TC wind | 풍속 → 구조 피해 | not implemented (TC 바람은 **CLIMADA native** 별도) | 자산 | 없음 | **Missing** | 극한풍 변수 미확인; TC 대체 금지 | TC 레이어 IBTrACS 합성 |
| SI | solar / secondary | 일사 → 발전량·증발산·WBGT 항 | not a hazard | — | — | **Missing** | `present in data ≠ usable as hazard` | `build_impf_presets.py:50` 오인 주의 |

## 10. KOR 레이어 실태 — HDF5 직접 판독 (2026-09-11)

`data/hazard_db/catalog.json`의 `region == "KOR"` 10건. 해상도는 `Centroids.lat/lon` 고유값 간격의 중앙값.

| 파일 | 변수/강도 | 등록 시나리오 키 | 기간(이벤트명) | 저장 해상도 | 소스(매니페스트 `source`) | haz_type · 단위 |
|---|---|---|---|---|---|---|
| `heat_mortality/HM_historical_KOR_2020.hdf5` | KMA **TA** → 초과도일 | historical | 2000–2019 (20) | **0.0500°**, 4,312 | KMA 남한상세 TA MKPRISMv31, 0.05 deg block mean | HM · degC-days |
| `heat_mortality/HM_rcp45_KOR_2030.hdf5` | TA | rcp45 | 2021–2030 (10) | 0.0500°, 4,312 | KMA TA SSP245 5ENSMN | HM · degC-days |
| `heat_mortality/HM_rcp85_KOR_2030.hdf5` | TA | rcp85 | 2021–2030 (10) | 0.0500°, 4,312 | KMA TA SSP585 5ENSMN | HM · degC-days |
| `heatwave/HW_historical_KOR_2020.hdf5` | TA → 시즌 p95 (**라벨 "daily Tmax"**) | historical | 2000–2019 (20) | 0.0500°, 4,312 | "season p95 daily Tmax — KMA … TA MKPRISMv31" | HW · degC (18.7–32.3) |
| `heatwave/HW_rcp45_KOR_2030.hdf5` | TA (라벨 Tmax) | rcp45 | 2021–2030 (10) | 0.0500°, 4,312 | "… daily Tmax — KMA TA SSP245" | HW · degC (19.5–32.5) |
| `heatwave/HW_rcp85_KOR_2030.hdf5` | TA (라벨 Tmax) | rcp85 | 2021–2030 (10) | 0.0500°, 4,312 | "… daily Tmax — KMA TA SSP585" | HW · degC (20.6–32.6) |
| `river_flood/RF_rcp45_KOR_2050.hdf5` | ISIMIP 하천 수심 | **rcp45** | 2030–… (480; `2030_clm45_gfdl-esm2m` …) | 0.0417°, 5,708 | "CLIMADA Data API (river_flood, **rcp60**, cached)" | RF · m |
| `tropical_cyclone/TC_rcp45_KOR_2040.hdf5` | IBTrACS 합성 바람장 | rcp45 | 43,560 이벤트 (`…_gen1…`) | 0.0417°, 5,708 | CLIMADA Data API (tropical_cyclone, rcp45) | TC · m/s (17.5–70.7) |
| `wildfire/WFseason_historical_KOR_2020.hdf5` | FIRMS 시즌 최대 밝기온도 | historical | 2001–2020 (20) | 0.0417°, 5,708 | CLIMADA Data API (wildfire, historical) | WFseason · K |
| `earthquake/EQ_observed_KOR_2020.hdf5` | MMI | observed | 41,710 이벤트 | 0.0417°, 5,708 | CLIMADA Data API (earthquake, observed) | EQ · MMI |

## 11. 해상도 감사 — 세 해상도는 다르다

| 층 | 값 | 근거 |
|---|---|---|
| **KMA 원 해상도** | 관측 0.005° (~500 m, 1201×1501) · SSP 0.01° (~1 km, 601×751) | `kma_scenario.GRID_RES_DEG`, `_to_native` 서브샘플링 |
| **저장 레이어 해상도** | **0.0500°** (~5 km), 4,312 센트로이드 — HDF5 판독으로 재확인 | `load_summer_tmax(coarsen=5)` 블록평균; §10 |
| **ImpactCalc 계산 해상도** | 해저드 센트로이드 간격 = 0.05°. 노출은 최근접 센트로이드에 배정(`assign_centroids=True`) — 점자산이면 그 점의 값이 5 km 셀 강도를 받고, WorldPop 래스터 노출이면 `res_arcsec=300`(0.083°)로 블록합산 후 배정 | `physical.py:302`, `exposures.build_exposure(res_arcsec=300)`, `_raster_exposure` |
| Data API 레이어 | 0.0417° (~4.6 km), 5,708 센트로이드 | §10 |

**"남한상세 1 km"는 소스의 이름이고, 계산은 5 km다.** 1 km 원본은 로더 안에서 5×5 블록으로 뭉개진 뒤 저장되며,
그 이후 어떤 단계도 1 km로 돌아가지 않는다. `coarsen=1`이면 ~10만 셀로 계산은 가능하지만(코드상 허용), 취약성
파라미터에 1 km 근거가 없는 상태의 의도적 선택이며 **현재 등록된 레이어 10개 중 1 km인 것은 없다.**

## 12. 1 km 적격성 — 19행 재평가

기준: **1km-ready** = 실제 1 km 격자 + 해저드 소비자 + 노출 매칭 + ImpactCalc · **1km-data-only** = 1 km 소스는
있으나 계산 소비자 없음 · **5km-current** = 현재 계산이 0.05° · **missing** = 한국 레이어·소비자 모두 없음.

| 물리위험 | 판정 | 근거 |
|---|---|---|
| 폭염 사망 | **5km-current** | HM 레이어 0.05°; 1 km 소스 있음, 1 km 계산 없음 |
| 폭염 생산성 | **5km-current** | HW 레이어 0.05° (+§13-2 램프 불일치) |
| 열 스트레스 | **1km-data-only** | TA·RHM·WS·SI 1 km 소스 존재, 소비자 0 |
| 극한강수 / 내수침수 | **1km-data-only** | RN 1 km 소스 존재(미다운로드), 소비자 0, 페릴 없음 |
| 하천홍수 | **missing**(KMA 기준) — Data API 4.6 km 레이어만 | RN 미사용; 홍수위험지도 소비자 0 |
| 해안침수 | **missing** | KOR 레이어 없음 |
| 태풍 바람 | **missing**(KMA 기준) — Data API 4.6 km | WS 미사용 |
| 태풍 강우 | **1km-data-only** | RN 소스 존재; TCRain 인제스터는 합성 R-CLIPER |
| 태풍 해일 | **missing**(KMA 기준) | DEM+TC 바람, KMA 무관 |
| 복합 | **missing** | 결합 없음 |
| 가뭄 | **1km-data-only** | RN+TA로 SPEI 가능, 인제스터 없음 |
| 산불 | **missing**(KMA 기준) — Data API 4.6 km, 현재만 | `physical.py:589` historical 하드코딩 |
| 한파 | **1km-data-only** | TA/TAMIN 소스 존재, 페릴 없음 |
| 대설 | **missing** | 강설 변수 없음, 페릴 없음 |
| 산사태 · 우박 · 작물 · 저수량 | **missing** | KOR 레이어·소비자 없음 |
| 지진 · 유럽폭풍 | N/A | KMA 범위 밖 |

**1km-ready: 0행.**

## 13. 제2부에서 추가로 확인된 사실 (수정하지 않음)

1. **HW 레이어 라벨 ≠ 배열 내용.** `heat_korea.py:124`가 `obs.tmax`(일평균 별칭 — `eobs.py:88-89` "The attribute keeps
   its historical name; the quantity is the daily mean")의 p95를 계산하고 `:134`가 `"season p95 daily Tmax"`로 라벨링.
   배열 값 18.7–32.6 °C가 일평균임을 독립적으로 뒷받침한다.
2. **HW 램프가 레이어 지표와 스케일이 다르다.** `_CATALOG_PERILS["heatwave"]` 램프는 30 °C에서 0, 35 °C에서 0.1 —
   Tmax/열지수 스케일. KOR HW 레이어 최대치는 32.6 °C(일평균 p95)이므로 **KOR 폭염 생산성 손실은 구조적으로 0~0.05
   구간에 갇힌다.** 라벨 문제가 아니라 지표-곡선 불일치이며, TAMAX 배선 또는 램프 재정의 중 하나가 필요한 **새 해저드
   정의** 문제다. 이번 작업에서 수정하지 않는다.
3. **RF 레이어 키 rcp45 / 소스 rcp60의 기제.** `ingest.py:295` `_RF_SCENARIO_MAP.get(scenario, "rcp60")`가 요청
   시나리오를 Data API가 제공하는 시나리오로 매핑하고, `:351`이 **제공된** 시나리오를 `source`에, 매니페스트 키는
   **요청된** 시나리오를 유지한다. 설계상 의도지만 키만 읽는 소비자는 오독한다.
4. **`Fetched ≠ Implemented` 사례 1건**: `~/climada/data/floodmap/` 4.9 GB, `fetch_floodmap_kor.py` 외 참조 0.
5. **CLIMADA native 오표기 금지 재확인**: 폭염 두 페릴은 CLIMADA `ImpactFunc`/`ImpactCalc` 그릇을 쓰는 custom 페릴이며
   native heat-mortality 함수는 없다(`heat_mortality.provenance_summary()["model"]`).

## 14. 제2부 상태 요약

| | |
|---|---|
| KMA 변수 소비 | 1 / 7 (`TA`) |
| 변수별 "데이터 있음" | 7 / 7 (포털 제공) — 디스크 보유는 `TA` 3 아카이브만 |
| 변수별 "위험 모델 있음" | `TA` 2 페릴(둘 다 5 km, 한쪽은 램프 불일치) · 나머지 6변수 **0** |
| 1km-ready 페릴 | **0** |
| 5km-current | 2 (폭염 사망 · 폭염 생산성) |
| 1km-data-only | 5 (열 스트레스 · 극한강수/내수침수 · 태풍 강우 · 가뭄 · 한파) |
| missing(KMA 기준) | 나머지 |
| 코드 변경 | **없음** — 문서·가드 테스트만 |
