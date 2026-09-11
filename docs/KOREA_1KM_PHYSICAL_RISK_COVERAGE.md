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
