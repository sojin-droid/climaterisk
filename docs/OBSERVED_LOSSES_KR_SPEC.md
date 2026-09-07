# 국내 실측 손실 적재 명세 — 재해연보 → ObservedSeries → 보정 → 검증

작성 2026-09-07 · 갱신 2026-09-07(구현 반영). 이 문서는 로더 **구현 전에** 계약을 확정하기 위한 것이며,
지금은 데이터 게이트 **이전 단계(F2·F4·F5)가 구현된** 상태다. 현재 저장소에 재해연보 데이터는 **없고**,
이 명세는 어떤 임의 데이터도 만들지 않는다.

| 단계 | 상태 |
|---|---|
| **F1** `observed_kr.py` (재해연보 로더) | 🔴 **BLOCKED — 데이터 게이트**(인증키 + 컬럼정의서 미확보). 만들지 않았다 |
| **F2** `validation.py` 확장 | ✅ **IMPLEMENTED** — 단위/통화 파싱·검사, 서브페릴 커버리지, 공간범위, 연도기준, `comparability_report` |
| **F3** `calibration.py` | 🟡 **부분** — 소스 선택 + 게이트 + 기록 메타데이터만(전체 리팩터는 F1 이후) |
| **F4** `CalibrationRequest.observed_source` | ✅ **IMPLEMENTED** (기본 `emdat`, 후방호환) |
| **F5** `tests/test_observed_kr.py` | ✅ **IMPLEMENTED** — 픽스처 + 파서 계약 + 게이트 테스트 24건(CLIMADA 불요) |
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

## 4. 필드 매핑 (확정분 / 미확정분)

| `ObservedSeries` | 소스에서 오는 값 | 상태 |
|---|---|---|
| `losses` 키(연도) | 통계연보 연도 항목 | `unknown` — 정확한 항목명은 포털 첨부 **`연도별 자연재해 피해_컬럼정의서.xlsx`** 확인 필요 |
| `losses` 값(피해액) | 재산피해액 항목 | `unknown` — 항목명·단위 동일 문서 필요 |
| `unit` | §5 참조 | **`not_normalized`** — 원천 단위(천원 추정) vs 지표 공표 단위(억원) 불일치 |
| `peril` | 재해유형(태풍·호우·대설·…) | 확정 (§6) |
| `source` | 문자열로 직접 기입 | 예: `"행정안전부 통계연보 연도별 자연재난 피해 (data.go.kr 15107318), 태풍, 전국, 당해연도 가격"` |
| `notes` | 물가기준·집계기준·결측 | 필수 기입 |

> **미확정 3건은 실제 파일 없이 확정 불가**이며, 추측으로 채우지 않는다(불확실성은 값으로 표기).
> 확보 절차: 포털 로그인 → 활용신청(개발계정 1만건/일) → 컬럼정의서 다운로드 → 1개 연도 시험 호출.

## 5. 단위·물가기준 (가장 사고나기 쉬운 지점)

- **물가기준 = 당해연도 가격(명목).** 원문 유의사항 인용: *"재산피해는 당해연도 가격 기준임"*
  ([index.go.kr 1628](https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1628)).
  → 1995년 1,000억원과 2024년 1,000억원은 **같은 값이 아니다**. 모델 측 노출은 **단일 기준연도의
  고정 자산가치**이므로 명목 관측 시계열과의 직접 비교는 **연도 편향**을 낳는다.
  **필수 조치**: 적재 시 기준연도로 디플레이트(GDP 디플레이터 또는 CPI)하고 `notes`에 기준연도·출처를
  남긴다. 디플레이터를 확보하지 못하면 **비교 창을 최근 10년으로 좁혀** 편향을 줄이고 그 사실을 명시한다.
- **단위 배수**: 원천 추정 **천원**, 지표 공표 **억원**. 로더는 배수를 **하드코딩하지 말고** 컬럼정의서
  값을 읽어 기록하고, 적재 후 총액을 index.go.kr 억원 값과 **대조 검증**한다(단독 적재 금지 — 대조군 병기).
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
| F1 | `worker/climaterisk_worker/observed_kr.py` **(신규)** | 재해연보 XML → `ObservedSeries`. 인증키 env(`CLIMATERISK_DATAGOKR_KEY`), 응답 원문 캐시, 단위·물가기준 `notes` 기입, 디플레이트 옵션 | 신규 |
| F2 ✅ | `worker/climaterisk_worker/validation.py` | **구현됨**: `parse_unit`/`check_units`(통화+배수, 미인식은 unknown=차단), `check_peril_coverage`, `check_scope`, `check_annual_basis`, `comparability_report`; `ObservedSeries`에 `currency`·`covers_subperils`·`scope`·`price_basis` 후방호환 추가; `annual_comparison`에 `years_compared`·단위·커버리지·`comparison_status` 메타데이터(기존 지표 무변경) | 확장 |
| F3 🟡 | `worker/climaterisk_worker/calibration.py` | **부분 구현**: 소스 선택(`disaster_yearbook`→데이터게이트 오류), `calibration_gate`(=`comparability_report` 재사용)로 적합 **전** 차단, `portfolio_currency`, 기록에 §10 메타데이터. **목표 계산 자체는 무변경**(EM-DAT total/span) — `ObservedSeries` 경유 통일은 F1 이후 | 리팩터(잔여) |
| F4 ✅ | `src/climaterisk/engines/base.py` | **구현됨**: `OBSERVED_SOURCES=("emdat","disaster_yearbook")`, `CalibrationRequest.observed_source="emdat"`, `from_portfolio(portfolio, observed_source="emdat")` | 스키마 |
| F5 ✅ | `tests/test_observed_kr.py` **(신규)** | **구현됨** 24건: 픽스처(소형 XML, `source`에 "synthetic unit-test fixture") + 파서 계약(연도·금액·재해유형 매핑·결측/비수치 = **0으로 강제하지 않고 skip**) + 단위/커버리지/범위/연도정렬 차단 + 명시적 opt-in 경로. `_reference_extract`는 **테스트 내 계약 증인**이며 로더가 아니다 | 신규 |
| F6 ✅ | `src/climaterisk/api/routers/run.py` · `runs/manager.py` | **구현됨**: `observed_source` 쿼리(기본 `emdat`, 미지원 값 400), `submit_calibration(portfolio, observed_source)` | 소 |
| F7 ✅ | `docs/RISK_REGISTER.md` · `docs/CLIMADA_METHODS.md` | **갱신됨**: C2(게이트·소스선택), C5(비교 프레임워크 존재·관측 계열 없음), §8(게이트·메타데이터·기본동작 변경·UI 갭) | 문서 |
| **F8** ✅ | `frontend/climaterisk/src/lib/calibration.ts` **(신규)** · `views/VulnerabilityView.tsx` · `types.ts` | **구현됨**: `calibrationOutcome()`이 출력 상태를 `ok`/`blocked`/`error`로 분류(블로커 有 또는 `comparison_status` 有 → `blocked`), `detail` 부재 시 일반 문구로 대체(빈 카드 금지). 성공 카드는 무변경이며, opt-in `not_comparable` 적합에는 경고 박스 추가. **게이트 차단은 "결과 없음"이 아니라 보고된 결과** | 소 |

**착수 조건(데이터 게이트)**: F1은 실제 인증키와 컬럼정의서 확보 후 착수한다. 그 전에는 F2·F4의 계약과
F5의 픽스처 골격까지만 진행 가능하다. **픽스처는 형식 검증용 소형 XML이며 분석·인용에 쓰지 않는다.**

## 10. 금지 사항

- 재해연보 실데이터 없이 **연도별 피해액을 합성하지 않는다**(단위 테스트 픽스처는 예외이며 `source`에
  `"synthetic unit-test fixture"`로 명시).
- 단위 배수·물가기준을 **추측으로 하드코딩하지 않는다**(컬럼정의서 확인 후 기록).
- 총피해액에 바람 전용 곡선을 맞춘 결과를 "보정 완료"로 보고하지 않는다(§6-1).
- AAI 대조 결과를 "연도별 검증"으로 표기하지 않는다(§7).
- 국가 관측 목표를 소수 점자산에 적합시키지 않는다(§8).

## 11. 확보해야 할 것 (사용자 조치 필요)

1. **공공데이터포털 인증키** — 15107318(+15107316) 활용신청. 로그인·키 발급은 자동화 대상이 아니다.
2. **`연도별 자연재해 피해_컬럼정의서.xlsx`** — 항목명·단위 확정용(§4 미확정 3건 해소).
3. (선택) **GDP 디플레이터 / CPI 시계열** — 명목→실질 환산(§5). 한국은행 ECOS 또는 KOSIS.
