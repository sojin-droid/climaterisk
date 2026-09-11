# 국내 실측 손실 적재 명세 — 재해연보 → ObservedSeries → 보정 → 검증

작성 2026-09-07 · 갱신 **2026-09-11(F1 구현·실데이터 적재)**. 이 문서는 로더 구현 전에 계약을 확정하려고
쓴 것이고, 그 계약대로 F1이 구현됐다. 인증키가 들어오면서 데이터 게이트가 풀렸고, **실제 응답 1건이
남아 있던 스키마 의문 2건(§4 `seq`, §12)을 종결**시켰다.

저장소에는 여전히 재해연보 **값을 커밋하지 않는다** — 로더가 실행 시점에 API에서 받고, 테스트는
합성 픽스처만 쓴다. 이 명세는 어떤 임의 데이터도 만들지 않는다.

**보정은 데이터가 들어온 뒤에도 계속 차단 상태다.** 막고 있는 것이 데이터 부재가 아니기 때문이다(§6-1, §8).

| 단계 | 상태 |
|---|---|
| **F1** `observed_kr.py` (재해연보 로더) | ✅ **IMPLEMENTED** (2026-09-11) — 실 API에서 **2016–2023 전국 태풍 피해 8년 계열**(비영 7년, 백만원, `national:KOR`, nominal)을 만든다. 원천 결함 4건(`tot`·`typhoon_heavy_rain`·중복 `seq`·×1000 연도)을 모두 방어한다. 값은 커밋하지 않는다 |
| **F2** `validation.py` 확장 | ✅ **IMPLEMENTED** — 단위/통화 파싱·검사, 서브페릴 커버리지, 공간범위, 연도기준, `comparability_report` |
| **F3** `calibration.py` | 🟡 **부분** — 소스 선택 + 게이트 + 기록 메타데이터만(전체 리팩터는 F1 이후) |
| **F4** `CalibrationRequest.observed_source` | ✅ **IMPLEMENTED** (기본 `emdat`, 후방호환) |
| **F5** `tests/test_observed_kr.py` | ✅ **IMPLEMENTED** — 픽스처 + 파서 계약 + 로더 + 게이트 테스트 **43건**(CLIMADA 불요) |
| **F6** API 전달 | ✅ **IMPLEMENTED** — `POST …/calibration?observed_source=` |
| **F7** 문서 정합 | ✅ 이 문서 · `CLIMADA_METHODS.md` §8 · `RISK_REGISTER.md` C2/C5 |
| **F8** UI 표시 갭 | ✅ **IMPLEMENTED** — 워커의 비-`ok` 출력을 카드로 표시(이유 + `blockers` + `comparison_status`), opt-in `not_comparable` 적합은 값 옆에 경고. `frontend/…/lib/calibration.ts` |

관련: [GAP_ANALYSIS_KO.md](GAP_ANALYSIS_KO.md) G1·G2·G5 · [RISK_REGISTER.md](RISK_REGISTER.md) C2·C5 ·
[CLIMADA_METHODS.md](CLIMADA_METHODS.md) §8(보정).

---

## 1. 코드가 이미 기대하는 계약 (변경 불필요)

| 계약 | 위치 | 내용 |
|---|---|---|
| **`ObservedSeries`** | `worker/climaterisk_worker/validation.py` | 원계약: `source: str`(출처, **필수**) · `unit: str` · `losses: dict[int, float]`(`{역년: 손실}`) · `peril: str` · `notes: str`. 2026-09-07 후방호환 추가: `currency: str` · `covers_subperils: tuple[str,…]` · `scope: str` · `price_basis: str`, 헬퍼 `unit_spec()` · `mean_annual_loss()` |
| 연도 대조 | `validation.py::annual_comparison(observed, modelled, …)` | 겹치는 연도에서 `bias`(모델−관측) · `mae` · `rmse` · `ratio`(Σ모델/Σ관측) · `coverage`(기존 지표 무변경) + 선택 메타데이터 `years_compared` · `observed_unit` · `modelled_unit` · `peril_coverage` · `comparison_status`(메타 미제공 시 **`unknown`**) |
| 이벤트 대조 | `validation.py::event_comparison(pairs)` | `(label, observed, modelled)` → 이벤트별 비(EDR형) + 총합비(TDR형) |
| 모델 연손실 | `validation.py::modelled_annual_losses(at_event, dates, orig=None)` | CLIMADA `Impact.at_event`/`date` → `{역년: 손실}`. `date <= 0` 건너뜀. `orig` 마스크를 주면 **원본 트랙만** 합산(앙상블 팽창 방지 — 호출자가 제공, 하자드 선택 로직은 만들지 않음) |
| 보정 기록 | `worker/climaterisk_worker/calibration.py::calibration_record` | `observed_source` · `observed_period` · `observed_annual_loss` · `objective` · `method` · `bounds` · `fit_status="fitted"` |
| 기록 영속화 | `vulnerability.py::save_calibration` | `data/calibrations/{peril}_{param}_{ISO3}.json` — **파일 1개 / 국가·재해·파라미터 (덮어씀)** |
| 기록 소비 | `vulnerability.py::resolve_tc_vhalf` | `options["tc_impf_default"] == "calibrated"` 일 때만 적용 |

추가된 게이트: `validation.comparability_report(...)` · `check_units` · `check_peril_coverage` · `check_scope` · `check_annual_basis`, 그리고 보정 측 재사용 래퍼 `calibration.calibration_gate(...)`.

즉 **적재 목표 형태는 이미 정해져 있다**: `{연도: 피해액}` + 출처 + 단위 + (커버리지·범위·물가기준).

## 2. 현재 사슬 vs 필요한 사슬 (끊긴 지점)

```
[현재]  EM-DAT CSV --emdat_to_impact--> 국가 총손실 --/연수--> target --minimize_scalar--> v_half --> data/calibrations/*.json
[없음]  재해연보 --?--> ObservedSeries --?--> target
[없음]  ObservedSeries --?--> annual_comparison   (validation.py는 자기 테스트 외 호출처 0건)
```

| # | 끊긴 링크 | 사실 |
|---|---|---|
| L1 | 재해연보 로더 | 존재하지 않음. `INGEST_SOURCES`는 `("dataapi","aqueduct","copdem","tctracks","tcrain")` — 손실 통계 소스가 없다 |
| L2 🟡 | `ObservedSeries` → 보정 | **부분 해소**: `CalibrationRequest.observed_source`(F4)와 API 전달(F6)이 생겼고, EM-DAT 경로는 게이트용 `ObservedSeries`를 구성한다. **잔여**: 목표값 계산은 여전히 EM-DAT total/span 직접 계산이며 `ObservedSeries` 경유로 통일되지 않았다(F1 이후) |
| L3 🟡 | 검증 실행 경로 | **부분 해소**: `calibration.py`가 게이트로 `validation`을 호출한다(첫 실사용처). **잔여**: 관측 계열이 없어 결과·리포트·API에 검증 지표를 노출하는 경로는 아직 없다 |
| L4 🟡 | 단위·물가 정합 | **부분 해소**: `check_units`가 통화+배수를 파싱해 불일치·미인식을 차단하고 `price_basis`를 기록한다. **잔여**: 통화 변환·디플레이트 **계산**은 없다(F1에서 적재 시 수행) |
| L5 | 연도 정렬 | 합성 이벤트셋에는 정직한 역년이 없다 (§7) |
| L6 ✅ | 범위(scope) 정합 | **해소**: `calibration_gate`가 적합 전에 `check_scope`로 차단한다. 미선언도 차단하며, 강행은 명시적 opt-in만 (§8) |

## 3. 소스 후보 (2026-09-07 확인)

| 소스 | 형태 | 범위 | 라이선스 | 적합성 |
|---|---|---|---|---|
| **행정안전부_통계연보_연도별 자연재난 피해** ([data.go.kr 15107318](https://www.data.go.kr/data/15107318/openapi.do)) | REST/XML, 인증키 | 연도 × 재해유형 | **이용허락범위 제한 없음** | ✅ `ObservedSeries` 형태와 **직결** (1차 채택 권고) |
| 행정안전부_통계연보_지역별 자연재난 피해 ([15107316](https://www.data.go.kr/data/15107316/openapi.do)) | REST/XML, 인증키 | 연도 × 시도·시군구 × 재해유형 | 제한 없음 | 지역 분해 검증용(2차). 시군구 합계가 전국과 일치하는지 대조 필요 |
| 재해연보 원문 PDF ([mois.go.kr](https://www.mois.go.kr/), 2024년판) | PDF | 사건별·지역별 상세 | 공개 | 이벤트 단위 대조(매미·루사 등, `event_comparison`)용. 표 추출 필요 |
| 지표 요약 ([index.go.kr 1628](https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1628)) | 웹 표 | 1995–2024 전국 | 공개 | **교차검증 전용**(공표 단위 억원). 적재 원천으로 쓰지 않는다 |

## 4. 필드 매핑 — **실제 스키마 확인 완료 (2026-09-07)**

컬럼정의서(`FILE_000000002777343`)와 페이지 임베드 Swagger를 **인증키 없이** 확보해 확인한
**실제 필드명**이다. 추측한 이름은 하나도 없다.

**엔드포인트** `https://apis.data.go.kr/1741000/NaturalDisasterDamageByYear/getNaturalDisasterDamageByYear`
(원천 `https://data.mois.go.kr/openapi/NaturalDisasterDamageByYear`) · 응답 **XML** ·
요청 파라미터는 **`ServiceKey`·`pageNo`·`numOfRows` 3개뿐 — 연도 필터가 없다**(전체를 페이징해야 함).
응답 봉투: `head{totalCount,numOfRows,pageNo,RESULT{resultCode,resultMsg},type}` + `row`.

| 실제 항목명 | 설명(원문) | 타입 | 매핑 |
|---|---|---|---|
| `wrttimeid` | 기준년도 | NUMERIC/string | → `losses` 키(연도) ✅ |
| `seq` | **분류 일련번호** | NUMERIC/string | ⛔ **미해결** — 라벨 필드가 없어 어떤 분류인지 알 수 없다 (아래 참조) |
| `tot` | 합계 | NUMERIC/string | (참고) |
| `typhoon` | 태풍 | NUMERIC/string | → `losses` 값 후보 ✅ (단, §6-1 커버리지 함정) |
| `heavy_rain` | 호우 | NUMERIC/string | 플랫폼 재해 없음(G7) |
| `heavy_snow` | 대설 | NUMERIC/string | 미지원 |
| `heavy_wind` | 강풍 | NUMERIC/string | 미지원 |
| `wind_wave_strong_wind` | 풍랑·강풍 | NUMERIC/string | 미지원 |
| `typhoon_heavy_rain` | **태풍·호우** | NUMERIC/string | ⚠️ 태풍과 호우를 **원천이 분리하지 못한** 연도의 합계 칸 |
| `lightning` | 낙뢰 | NUMERIC/string | 미지원 |
| `cold_wave` | 한파 | NUMERIC/string | 인명 계열 |
| `earthquak` | 지진 | NUMERIC/string | (원천 오타 — 철자 그대로 써야 함) |
| `heatwave` | 폭염 | NUMERIC/string | 인명 계열 |

**`seq`(분류 일련번호)** — 원천 표를 확인해 **위험이 크게 줄었다**(§5-A). API의 원천은
「행정안전 통계연보」 표 **7-3-2-2 연도별 자연재난 피해**이고, 그 표는 **행=연도, 측정값=재산피해 하나**다
(인명피해는 **다른 표** 7-3-2-1/-3/-4에 있다). 따라서 *이 데이터셋에서* `seq`가 금액 행과 인명(명) 행을
섞을 위험은 **배제된다** — 모든 행의 단위는 백만원이다(§5-A).
남은 것은 `seq`가 무엇을 세는가(행 순번인지 판본 구분인지)이며 **여전히 미확정**이다. 응답 1건이면 확정된다.
추정으로 코드에 넣지 않는다.

**단위 컬럼은 컬럼정의서·Swagger 어디에도 없다**(변함없음). 그러나 원천 표에는 단위가 명시돼 있고,
그 표가 이 API의 원천이므로 **단위는 §5-A에서 확정**했다.

**⚠️ API 열은 원천 표의 부분집합이다.** 표의 원인 13종 중 API에는 **우박(Hail)·폭풍·해일(Storm and
Tidal Wave)·냉해·동해(Cold and Frost Damage)가 없다**. 2023년 실측 대조: 표의 합계 958,221 =
API 제공 열 합 845,558 + 누락 3종(우박 2,293 + 폭풍·해일 16 + 냉해·동해 110,354 = 112,663).
→ **`tot` ≠ Σ(API 열)** 이며 2023년에는 그 차이가 **11.8 %**다. 태풍 해일이 별도로 계상된 경우조차
API로는 받을 수 없다.

> 지역별 API(15107316)에는 **참고문서(컬럼정의서) 자체가 없다** — 페이지에 첨부 블록이 존재하지 않음.

## 5-A. 단위·가격기준 — **API 원천 표에서 확정 (2026-09-07)**

API 설명이 지목하는 원천은 「행정안전 통계연보」다. 그 표를 직접 받아 확인했다(3개 판본).

| 판본 | 파일 | 표 | 단위(verbatim) | 가격기준 주석(verbatim) | 수록 연도 |
|---|---|---|---|---|---|
| **2026판**('25.12.31 기준) | `FILE_00148682JeSsbgy` | 7-3-2-2 p370 | `(단위: 백만원) (Unit: KRW million)` | `주1) 본 피해액은 당해연도 가격 기준임` | 2018–2024 |
| 2025판('24.12.31 기준) | `FILE_00138511EvCPqJj` | 7-3-2-2 p368 | `(단위 : 백만원)(Unit : KRW 1 million)` | `주 1) 본 피해액은 2023년도 환산가격 기준임` | 2018–2023 |
| 2024판('23.12.31 기준) | `FILE_001321292K8hP5d` | 7-3-2-2 p400 | `(단위 : 백만원)(Unit : KRW 1 million)` | `주 1) 본 피해액은 2022년도 환산가격 기준임` | 2017–2022 |

**확정 1 — 단위 = 백만원.** 세 판본 모두 동일하며 영문(`KRW 1 million`)까지 병기돼 있다.
→ `ObservedSeries(unit="KRW million", currency="KRW")`, 배수 `1e6`.

**확정 2 — 표 구조.** `구분(Classification) = 연도(Year)`, 열 = 재해원인, 측정값은 **재산피해 하나**.
인명피해는 같은 절의 **다른 표**(7-3-2-1 지역별 `(단위 : 백만원, 명)`, 7-3-2-3/-4 시설별)에 있다.
→ 이 API에서 금액 행과 인명 행이 섞일 위험은 **없다**(§4의 `seq` 항목 참조).

**확정 3 — 가격기준은 `당해연도(nominal)`.** 판본 주석이 서로 다르지만(위 표), **값이 판단을 끝낸다**:
2018–2023 행의 값이 세 판본에서 **완전히 동일**하다(예: 2020 = 1,318,177). 환산 기준연도가 2022→2023으로
바뀌었다면 같은 연도 값이 달라져야 한다. 게다가 그 값들은 「2024 재해연보」의
`[표 9] 최근 10년간 피해액 … (단위 : 백만원)` + `※ 각 당해연도 가격 기준` 계열과 **일치**한다
(2020 = 1,318,177 동일). → **계열은 명목이고, 2024·2025판의 "환산가격" 주석은 자기 데이터와
모순되는 표기 오류**로 취급한다. `price_basis="nominal"`, 근거를 `notes`에 남긴다.

## 5-B. 원천 자체의 결함 3건 — 로더가 반드시 처리해야 하는 것

추측이 아니라 판본 대조로 드러난 사실이다.

1. **최신 연도 행의 단위가 1000배 어긋난다(2026판).** 헤더는 백만원인데 2024 행은 **천원**으로 실렸다.
   재해연보 2024와 1:1 대조로 확정:

   | 항목 | 통계연보 2026판 2024행 | 재해연보 2024(백만원) | 배수 |
   |---|---|---|---|
   | 합계 | 910,713,075 | 910,713 | ×1000 |
   | 태풍 | 106,342 | 106 | ×1000 |
   | 호우 | 423,947,425 | 423,947 | ×1000 |
   | 대설 | 454,175,996 | 454,176 | ×1000 |
   | 지진 | 945,965 | 946 | ×1000 |
   | 폭염 | 2,551,156 | 2,551 | ×1000 |

   → **연도 단위 스케일을 신뢰하지 말고 행 단위로 검증**해야 한다. 최신 연도를 그대로 쓰면
   태풍 피해가 **1000배** 과대해진다. (재해연보 값과의 대조가 유일한 안전장치다.)
2. **2024 행의 열 수가 헤더보다 많다.** 헤더 13원인인데 2024 행에는 값 16개가 있고, 끝의 두 값
   `4,856,241` + `24,127,894`(천원) = 28,984 백만원 = 재해연보의 `기타(이상기온)`에 해당한다.
   → 위치 기반 파싱 금지. **원인은 열 이름으로만 읽어야 한다**(API는 이름 기반이므로 이 점은 유리).
3. **API 열이 표보다 3종 적다**(우박·폭풍·해일·냉해·동해) → `tot ≠ Σ(API 열)`, 2023년 기준 11.8 % 차이.
   → 합계를 쓸지 개별 열을 쓸지 **명시**하고, 둘을 섞어 비율을 만들지 않는다.

## 5. 단위·물가기준 — **원문 확인 완료 (2026-09-07, 2024 재해연보 PDF 직접 인용)**

원문: 행정안전부 「2024 재해연보(자연재난)」 PDF 498쪽(`FILE_00141549C3LGhPz`). 아래는 **verbatim 인용**이며
페이지는 PDF 물리 페이지다.

| 확인 항목 | 원문 | 위치 |
|---|---|---|
| 요약표 금액 단위 | `[표 1] 원인별 재산피해 현황 (단위 : 백만원)` | p12 |
| 요약표 물가기준 | `※ 각 당해연도 가격 기준` | p16, p18, p19 |
| **상세표 금액 단위** | `(금액 단위 : 천원)` | p169–177(2-1 기간별), p268(4-2.9) |
| **이중 가격기준** | `1.(가)의 피해액은 2024년도 환산가격 기준임.` / `2.(나)의 피해액은 당해 연도 가격 기준임.` | p268 |
| 인명피해 단위 | `[표 10] 최근 10년간 원인별 인명피해 현황 (단위 : 명)` | p17 |
| **원천이 제공하는 디플레이터** | `연도별 생산자물가지수-총지수(1910~1964)(2020=100)` — 4-1.1 「2024년 화폐가치 기준」 | p209 |
| 환산/원값 병기 | 4-1.2 표가 시설별로 `환산` / `원피해액` 두 열을 병기 | p211 |

**결론 3가지**
1. **단위는 표마다 다르다** — 요약표 **백만원**, 상세표 **천원**. "재해연보의 단위"라는 단일 값은 없다.
   그러므로 **API 값의 단위는 API 문서에서 확인해야 하며, 현재 그 문서에는 단위가 없다(미확인).**
   재해연보에서 확인한 단위를 API에 **전이하지 않는다** — 둘은 다른 발간물이다
   (API 원천은 「행정안전 통계연보」, 위 인용은 「재해연보」).
2. **디플레이터를 우리가 고를 필요가 없다.** 재해연보는 **생산자물가지수(2020=100) 연도별 시계열
   1910~2024**를 수록하고 그것으로 환산한 `(가) 2024년도 환산가격` 계열을 **함께 공표**한다.
   → 실질 비교가 필요하면 **원천의 환산 계열을 쓰거나 원천의 PPI를 쓴다.** 임의의 GDP 디플레이터·CPI를
   코드에 넣지 않는다(명세 §10 유지).
3. 따라서 적재 시 `price_basis`는 **`nominal`(당해연도) / `real:2024`(환산)** 중 **어느 계열을 받았는지**
   기록해야 하며, 둘을 섞으면 안 된다.
- **통화**: 관측은 **KRW**, 자산 `value`/AAI는 기본 **USD**. `annual_comparison`은 단위 검사를 하지
  않으므로 KRW와 USD를 섞으면 조용히 틀린 값이 나온다. **규칙**: 보정·검증 실행 시 자산 통화를 KRW로
  두거나, 적재 단계에서 연도별 환율로 환산하고 `unit`에 명시한다. 코드에 **단위 불일치 assert 추가**(§9 F2/F5).

## 6. 재해유형 → 플랫폼 재해 매핑, 그리고 함정

| 재해연보 유형 | 플랫폼 재해 | 비고 |
|---|---|---|
| 태풍 | `tropical_cyclone` | **§6-1 함정 적용** |
| 호우 | (없음 — pluvial 미지원 G7) · 부분적으로 `river_flood` | 도시 내수침수 구조 공백. 하천홍수로 등치하지 말 것 |
| 대설 | (없음) | 설하중·온대저기압 미지원 |
| 강풍·풍랑 | (없음 — 유럽폭풍 러너는 WISC 전용) | |
| 폭염(2018–) · 한파(2023–) | `heat_mortality`(사망) / `heatwave`(생산성) | 인명 계열 — **재산 손실액 계열과 섞지 말 것** |
| 지진 | 재해연보 자연재난 범주 밖 | `earthquake`는 별도 소스 필요 |

### 6-1. 보정 목표와 모델 커버리지의 불일치 (핵심 위험)

재해연보는 **사건 원인별 귀속**이다. "태풍 피해액"에는 **바람 + 해일 + 호우 피해가 모두 포함**된다.

> **원문 확인(2026-09-07)**: 재해연보의 피해 분해축은 **시설(건물·선박·농경지·농작물·공공시설·사유시설)**
> 과 **인명(사망·실종)·이재민**뿐이다(p267 표 4-2.8/4-2.9 헤더). **바람/해일/호우로 나눈 열은 존재하지
> 않는다.** 원인 목록에 `폭풍해일`이 **별도 항목으로 있으나**(2023년 15,349천원) 이는 해일이 *주 원인*으로
> 신고된 사건에만 붙는 분류이고, 태풍 사건의 해일 피해는 통상 `태풍`에 합산된다 — 즉 귀속은 물리적 분해가
> 아니라 **신고·판정의 산물**이다. 또한 `태풍·호우` 열의 존재는 원천 스스로 두 원인을 **분리하지 못한
> 연도**가 있음을 뜻한다.
> → **`observed = TC aggregate` / `modelled = TC wind-only` 로 확정.** 이 조합에서는 **calibration을
> 실행하지 않는다**(`calibration_gate`가 자동 차단).
반면 플랫폼의 `tropical_cyclone` 러너는 **바람 전용**이다(해일=별도 재해, 호우=지시적 곡선으로 사실상
미반영 — G5).

→ 이 상태로 `v_half`를 "태풍 총피해액"에 맞추면 **없는 호우·해일 피해를 바람 취약성 파라미터가 흡수**한다.
겉보기에는 관측과 일치하지만 **물리적으로 틀린 곡선**이며, 다른 지역·다른 시나리오로 이전하면 무너진다.
7월 실측이 크기를 정량화했다: wind⊕surge 결합이 관측 총피해의 **1.5%(루사, 강우지배) ~ 20%(매미, 바람지배)**.

**규율 (구현 시 강제):**
1. 보정 목표는 **모델이 표현하는 성분만** 담아야 한다. 재해연보에서 태풍의 바람/해일/호우 분해가
   불가능하면 그 계열로 `v_half`를 **보정하지 않고 검증(비교)만** 한다 — `annual_comparison`으로
   **포착률(`ratio`)을 보고**하고 그 값을 캐비엇으로 부착한다.
2. 총피해액 기반 적합을 실행할 경우 기록에 `fit_status`와 별도로
   `target_covers_subperils: ["wind","surge","rain"]` / `model_covers: ["wind"]` 를 남겨
   **비교 불가임을 기계가 읽을 수 있게** 한다.
3. 이 원칙은 **기존 EM-DAT 보정에도 동일하게 적용된다**(EM-DAT TC 손실도 총피해다). 즉 현행 보정 경로도
   같은 함정을 안고 있으며, 아직 실행된 적이 없어 드러나지 않았을 뿐이다.

## 7. 연도 정렬 문제 — 합성 이벤트셋에는 역년이 없다

`modelled_annual_losses`는 `date > 0` 인 이벤트만 연도로 합산한다. 그러나 보정·영향 계산이 쓰는 하자드는
**Data API 합성 세트**(태풍 43,560 이벤트)이고, 합성 멤버는 부모 관측 트랙의 날짜를 물려받는다.
→ 그대로 연도 합산하면 **한 해에 앙상블 배수만큼 부풀려진 손실**이 나온다. 저장소에는 원본 트랙만 고르는
코드(`haz.orig` 필터)가 **없다**(grep 0건).

| 선택지 | 방법 | 결과 | 판단 |
|---|---|---|---|
| **A. 관측 트랙 하자드** | IBTrACS 관측 트랙(디스크 보유: `~/climada/data/IBTrACS.ALL.v04r01.nc`) 또는 합성 세트의 `orig == True` 부분집합 | **진짜 연도별 대조** 가능 | ✅ 검증용 권고 |
| B. AAI ↔ 관측 평균 | 현행 보정 방식 | 연도 정보 소실, 분포·꼬리 검증 불가 | 보정에는 가능, 검증으로는 불충분 |
| C. yearset 분포 대조 | `_yearset_summary`의 표본 연손실 분포 vs 관측 연손실 분포 | 연도 짝짓기 없이 **분포** 비교 | ✅ A와 병행 권고 |

**규칙**: 연도별 대조를 보고할 때 어느 선택지인지 명시하고, B로 얻은 결과를 "연도별 검증"이라 부르지 않는다.

## 8. 범위(scope) 정합 — 현행 보정의 구조적 결함

`compute_calibration`은 관측 목표를 **국가 총피해 / 연수**로 잡고, 모델 측은 **요청에 담긴 자산들**의 AAI를
쓴다. 포트폴리오가 건물 3채면 "전국 손실 = 3채 손실"을 강제하므로 `v_half`가 상한(200 m/s)까지 밀린다.
**국가 관측 목표는 국가 노출과만 짝지어야 한다.**

**구현된 가드**(`calibration.calibration_gate`): 관측 범위는 EM-DAT 경로에서 `national:{ISO3}`로
선언되고, 모델 범위는 **호출자가 `options["exposure_scope"]`로 선언**해야 한다. 불일치는 물론
**미선언도 차단**한다(런너가 검증할 수 없는 것을 통과시키지 않는다). 의도적 강행은
`options["allow_incomparable_calibration"]=true`이며, 그때 기록은 `comparison_status="not_comparable"`로
남는다 — `fit_status="fitted"`와 **별개 키**다(수렴 ≠ 타당성).

(대안: 관측을 자산 소재 시군구로 좁히고 15107316 지역별 API를 쓰되, 그때는 노출도 그 시군구로 한정한다.)

## 9. 로더 구현 시 수정할 파일 (다음 단계 작업 목록)

| # | 파일 | 변경 | 성격 |
|---|---|---|---|
| F1 ✅ | `worker/climaterisk_worker/observed_kr.py` **(신규)** | **구현됨** 2026-09-11: 재해연보 XML → `ObservedSeries`. 인증키 env(`CLIMATERISK_DATAGOKR_KEY`, Decoding 형식), `type=xml` 필수(§4-A), 이름 기반 원인열 판독, 단위·가격기준·범위·커버리지 선언, 결함 방어 4건. **디플레이트는 미구현**(계열이 nominal이고 아직 환산 요구가 없음) | 신규 |
| F2 ✅ | `worker/climaterisk_worker/validation.py` | **구현됨**: `parse_unit`/`check_units`(통화+배수, 미인식은 unknown=차단), `check_peril_coverage`, `check_scope`, `check_annual_basis`, `comparability_report`; `ObservedSeries`에 `currency`·`covers_subperils`·`scope`·`price_basis` 후방호환 추가; `annual_comparison`에 `years_compared`·단위·커버리지·`comparison_status` 메타데이터(기존 지표 무변경) | 확장 |
| F3 🟡 | `worker/climaterisk_worker/calibration.py` | **부분 구현**: 소스 선택(`disaster_yearbook`→데이터게이트 오류), `calibration_gate`(=`comparability_report` 재사용)로 적합 **전** 차단, `portfolio_currency`, 기록에 §10 메타데이터. **목표 계산 자체는 무변경**(EM-DAT total/span) — `ObservedSeries` 경유 통일은 F1 이후 | 리팩터(잔여) |
| F4 ✅ | `src/climaterisk/engines/base.py` | **구현됨**: `OBSERVED_SOURCES=("emdat","disaster_yearbook")`, `CalibrationRequest.observed_source="emdat"`, `from_portfolio(portfolio, observed_source="emdat")` | 스키마 |
| F5 ✅ | `tests/test_observed_kr.py` **(신규)** | **구현됨** 24건: 픽스처(소형 XML, `source`에 "synthetic unit-test fixture") + 파서 계약(연도·금액·재해유형 매핑·결측/비수치 = **0으로 강제하지 않고 skip**) + 단위/커버리지/범위/연도정렬 차단 + 명시적 opt-in 경로. `_reference_extract`는 **테스트 내 계약 증인**이며 로더가 아니다 | 신규 |
| F6 ✅ | `src/climaterisk/api/routers/run.py` · `runs/manager.py` | **구현됨**: `observed_source` 쿼리(기본 `emdat`, 미지원 값 400), `submit_calibration(portfolio, observed_source)` | 소 |
| F7 ✅ | `docs/RISK_REGISTER.md` · `docs/CLIMADA_METHODS.md` | **갱신됨**: C2(게이트·소스선택), C5(비교 프레임워크 존재·관측 계열 없음), §8(게이트·메타데이터·기본동작 변경·UI 갭) | 문서 |
| **F8** ✅ | `frontend/climaterisk/src/lib/calibration.ts` **(신규)** · `views/VulnerabilityView.tsx` · `types.ts` | **구현됨**: `calibrationOutcome()`이 출력 상태를 `ok`/`blocked`/`error`로 분류(블로커 有 또는 `comparison_status` 有 → `blocked`), `detail` 부재 시 일반 문구로 대체(빈 카드 금지). 성공 카드는 무변경이며, opt-in `not_comparable` 적합에는 경고 박스 추가. **게이트 차단은 "결과 없음"이 아니라 보고된 결과** | 소 |

**착수 조건(데이터 게이트)** — **해소됨 2026-09-11.** 인증키가 확보돼 F1을 착수·완료했다. 픽스처 원칙은
그대로다: **픽스처는 형식 검증용 소형 XML이며 분석·인용에 쓰지 않고, 실 응답 값을 저장소에 넣지 않는다.**

**F1의 경계** — F1은 관측 데이터의 공급 계층이다. `v_half` 적합, `ImpactCalc`, 보정 최적화, 검증,
포착률(capture ratio)은 F1의 책임이 **아니며** 하류 계층에 둔다. 특히 **TC capture diagnostic은
별도의 하류 분석**이고 이 명세나 로더 문서에 그 수치를 싣지 않는다.

## 10. 금지 사항

- 재해연보 실데이터 없이 **연도별 피해액을 합성하지 않는다**(단위 테스트 픽스처는 예외이며 `source`에
  `"synthetic unit-test fixture"`로 명시).
- 단위 배수·물가기준을 **추측으로 하드코딩하지 않는다**(컬럼정의서 확인 후 기록).
- 총피해액에 바람 전용 곡선을 맞춘 결과를 "보정 완료"로 보고하지 않는다(§6-1).
- AAI 대조 결과를 "연도별 검증"으로 표기하지 않는다(§7).
- 국가 관측 목표를 소수 점자산에 적합시키지 않는다(§8).

## 11. 확보해야 할 것 (사용자 조치 필요)

1. **공공데이터포털 인증키** — 15107318(+15107316) 활용신청. 로그인·키 발급은 자동화 대상이 아니다.
   **여전히 유일한 하드 블로커.** (환경에 있는 KEPCO·V-World 키는 data.go.kr 키가 아니다 — §12)
2. ~~**`연도별 자연재해 피해_컬럼정의서.xlsx`**~~ → ✅ **확보·판독 완료**(§4). 단, **단위 컬럼이 없어**
   단위는 이 문서로 해소되지 않았다.
3. ~~**GDP 디플레이터 / CPI**~~ → ✅ **불필요**. 재해연보가 **생산자물가지수(2020=100) 1910~2024**와
   `2024년 환산가격` 계열을 **직접 공표**한다(§5). 우리가 디플레이터를 고르지 않는다.
4. **추가 필요**: 「행정안전 통계연보」 해당 표(구분 행·단위 헤더) — `seq`와 단위를 해소할 유일한 문서 경로.
   또는 인증키 확보 후 **응답 1건**으로 두 미해결을 동시에 확인.

---

## 12. 스키마 실측 확인 기록 (2026-09-07)

**목적**: 코드를 만들기 전에 "재해연보가 무엇을 측정하는지" 증명. 코드 변경 0건, 데이터 생성 0건.

| 확보물 | 방법 | 결과 |
|---|---|---|
| 컬럼정의서 `연도별 자연재해 피해_컬럼정의서.xlsx` | 데이터셋 페이지의 `fn_fileDownload('FILE_000000002777343','1')` → `https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000002777343&fileSn=1` | ✅ **로그인 없이 취득**(10,207 B). 13개 항목명·설명·타입 확인, **단위 컬럼 없음** |
| API 명세(Swagger) | 데이터셋 페이지에 임베드된 `swaggerJson` | ✅ 엔드포인트·파라미터 3개·응답 봉투·`row` 13필드 확인. **연도 필터 없음**, XML |
| 지역별(15107316) 참고문서 | 페이지 첨부 블록 탐색 | ❌ **첨부 없음**(컬럼정의서 미제공) |
| 「2024 재해연보(자연재난)」 PDF | `https://www.mois.go.kr/cmm/fms/FileDown.do?atchFileId=FILE_00141549C3LGhPz&fileSn=0` | ✅ 8.09 MB / 498쪽. 단위·가격기준·분해축·PPI 시계열 **원문 인용 확보**(§5, §6-1) |
| 「행정안전 통계연보」 2026·2025·2024판 PDF | `FILE_00148682JeSsbgy` / `FILE_00138511EvCPqJj` / `FILE_001321292K8hP5d` (mois.go.kr `FileDown.do`) | ✅ 12.8 / 8.4 / 19.3 MB. 표 7-3-2-2에서 **단위 백만원 확정**, 가격기준 주석 판본 간 모순 발견, 최신 연도 행의 ×1000 단위 오류 발견(§5-A·5-B) |
| data.go.kr 인증키 | 환경변수·`.env` 조회(값 미출력) | ❌ **없음**. `KEPCO_API_KEY`는 **bigdata.kepco.co.kr**(한전 포털), `VWORLD_API_KEY`는 V-World — **data.go.kr 키가 아니다** |
| 1개 연도 시험 호출 | — | ⛔ **시도하지 않음**(키 없음). 명세 §10 준수 |
| 단일 응답 검증(2차 시도) | `CLIMATERISK_DATAGOKR_KEY` 등 5개 이름 × Windows **Process·User·Machine** 스코프 + `.env` 2곳 조회(값 미출력) | ⛔ **전부 미설정 → 중단.** API 호출 0건. 환경의 KEPCO·V-World 키는 data.go.kr 키가 아니므로 대체 사용하지 않았다. 재시도 절차는 §13 |

원본 파일은 저장소에 커밋하지 않았다(재해연보 8 MB, 라이선스·용량). 위 URL/파일ID로 재취득 가능하다.

**해소된 것 (2026-09-07 2차 조사)**
- ✅ **API 값의 단위 = 백만원** — 원천 표 7-3-2-2의 헤더에서 확정(3판본 일치, 영문 병기).
- ✅ **가격기준 = 당해연도(nominal)** — 판본 주석 모순을 값 대조로 판정(§5-A 확정 3).
- ✅ **`seq`가 금액·인명을 섞을 위험** — 원천 표가 재산피해 단일 측정값임을 확인해 **배제**.

**해소된 것 (2026-09-11, 실 응답 1건)** — §12-A 참조. 위 3건 모두 종결됐다.

### 12-A. 실 응답으로 종결된 항목 (2026-09-11)

인증키를 확보해 **API를 실제로 호출**했다. 응답 1건이 남은 의문을 전부 닫았다.

| 이전 상태 | 실 응답이 보여준 것 | 결론 |
|---|---|---|
| **인증키 없음** (하드 블로커) | `.env`의 `CLIMATERISK_DATAGOKR_KEY`(Decoding 형식, 64자) | 해소 |
| **`seq`가 세는 대상 미확정** | `totalCount=8`, 연도 8개, **모든 행 `seq=1`**, 연도당 정확히 1행 | 이 데이터셋에서 `seq`는 금액/인명을 가르지 않는다(원천 표가 재산피해 단일 측정값). 로더는 **연도당 2행 이상이면 거부**하고 어느 행이 금액인지 추측하지 않는다 |
| **어느 판본을 담는지 미확정** | `wrttimeid` 최댓값 **2023**, 2020 = `1,318,177` | 2024행을 포함한 2026판이 **아니다**. §5-B-1의 ×1000 결함은 현재 응답에 **없다**. 그래도 로더는 매번 검사한다(판본이 갱신될 수 있으므로) |

**명세에 없던 사실 — `type=xml`이 필수다.** Swagger가 고지하는 요청 파라미터는 `ServiceKey`·`pageNo`·
`numOfRows` 3개뿐인데, 이대로 호출하면 게이트웨이가 **HTTP 200 본문에 `HTTP_ERROR`(returnReasonCode 04)
봉투**를 돌려준다. 데이터가 아니라 오류 봉투이므로 **빈 응답으로 오인하기 쉽다** — 로더는 봉투 루트 태그를
검사해 거부하고, 테스트로 고정했다(`test_loader_rejects_a_gateway_error_envelope`).

**단위 재확인.** §5-A가 원천 표에서 확정한 백만원이 실 응답과 일치한다: 2020 `tot` = 1,318,177 =
1조 3,182억 원(공표치). 2023년 `tot`(958,221)과 API 원인열 합(845,558)의 차이 112,663도 §4가 기록한
누락 3종 합계와 정확히 일치한다 — 단위와 열 누락 구조가 동시에 확인됐다.

---

## 13. 단일 응답 검증 절차 — 인증키 확보 시 즉시 실행 (아직 미실행)

이 절은 **아직 실행되지 않았다**. 키가 없어 호출을 시도하지 않았다(§12). 아래는 응답 1건으로
남은 미확정 3건(`seq` 의미 · API 판본 · 값 단위 실측)을 한 번에 끝내기 위한 절차이며, 로더가 아니다.

**요청** — 명세에 있는 파라미터만 사용한다(연도 필터는 존재하지 않으므로 페이징으로 접근):

```
GET https://apis.data.go.kr/1741000/NaturalDisasterDamageByYear/getNaturalDisasterDamageByYear
    ?ServiceKey=<CLIMATERISK_DATAGOKR_KEY>&pageNo=1&numOfRows=10&type=xml
```

키는 환경변수에서만 읽고 로그·문서·저장소에 남기지 않는다. 원문 응답은 스크래치 위치에만 보관한다
(저장소에 커밋 금지 — §10, §13 of this spec).

**응답에서 확인할 것**

| # | 확인 항목 | 판정 기준 |
|---|---|---|
| 1 | `head.totalCount` | 수록 행 수. 표 7-3-2-2의 연도 수(2026판=7)와 비교 → **판본 추정** |
| 2 | `wrttimeid` 최댓값 | **2024면 2026판**(§5-B ①의 ×1000 오류 포함 가능), 2023이면 2025판 |
| 3 | 한 `wrttimeid`에 대한 `seq` 값 집합 | 연도당 1개면 **행 순번**, 여러 개면 **분류**(그 경우 각 seq의 값 크기·합계로 의미 판정) |
| 4 | **단위 실측** | `typhoon`(2023) = **55,777** 이면 백만원 확정 / `55,777,xxx` 류면 천원 |
| 5 | **2024 anomaly** | `wrttimeid=2024`의 `tot`가 **910,713**(백만원)인지 **910,713,075**(천원)인지. 후자면 §5-B ① 이 API에도 존재 |
| 6 | `tot` vs Σ(원인 열) | 2023 기준 Σ = 845,558, `tot` = 958,221 예상(차이 112,663 = 누락 3종). **`tot`은 원천값 그대로 보존**하고 재구성하지 않는다 |
| 7 | 단위 메타데이터 존재 | 응답에 단위 필드가 있으면 기록. (Swagger·컬럼정의서에는 없음) |

**판정 후 조치**
- 4·5에서 단위가 행마다 다르면 → 로더는 **자동 보정하지 않고** `source anomaly`로 표기하고
  해당 연도를 `unknown`으로 두거나 재해연보 값과의 대조 통과 시에만 채택한다(§5-B).
- 3에서 `seq`가 분류로 판명되면 → 각 분류의 단위를 확인할 때까지 **적재 대상 seq를 확정하지 않는다**.
- 어떤 결과든 **TC 보정은 여전히 차단**이다(§6-1: 관측 aggregate vs 모델 wind-only).
