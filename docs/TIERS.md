# TIERS — 방법 티어 (T1 / T2 / T3)

작성 2026-11-14. [SCOPE.md](SCOPE.md)의 **사용 등급**(S/I/D: 결과를 어디에 써도 되나)과 다른 축인 **방법 티어**(결과를 무엇으로 계산했나)를 정의한다. 표의 값은 코드 `src/climaterisk/physical_risk/tiers.py`의 `PERIL_TIERS`와 같아야 하며, `tests/test_tiers_doc.py`가 둘을 대조한다. **이 문서는 숫자를 바꾸지 않는다.**

> **TL;DR (EN).** Every peril result carries a *method tier* next to its *use tier*. **T1** =
> global default data and curves (CLIMADA Data API, Aqueduct, ISIMIP). **T2** = Korean data
> and standards (KMA 1 km grids, official flood-map polygons, KHOA tidal datums, KDS design
> values). **T3** = a physics simulation with Korean parameters (SOLWEIG, SFINCS,
> LISFLOOD-FP, DSSAT …). A tier is reached only when all its inputs are present; otherwise the
> run falls back and says why. A higher method tier does **not** raise the use tier: only a
> domestic validation (SCOPE §1.1) does.

---

## 1. 두 축

| 축 | 질문 | 단계 | 문서 |
|---|---|---|---|
| **방법 티어** | 무엇으로 계산했나 | **T1** 전 지구 기본자료·곡선 → **T2** 한국 자료·기준 → **T3** 물리 시뮬레이션 + 한국 매개변수 | 이 문서 |
| **사용 등급** | 어디에 써도 되나 | **S** 순위만 → **I** 범위와 함께 절대값 → **D** 국내 실측 검증 통과 | [SCOPE.md](SCOPE.md) |

- 결과에는 두 꼬리표가 함께 붙는다(예: "T2 · I").
- 티어는 IPCC 국가 인벤토리의 Tier 1/2/3을 본뜬 것이다. IPCC처럼 상위 티어는 **입력이 갖춰지고 검증됐을 때만** 더 정확하다고 본다. 해상도가 높아도 매개변수가 틀리면 정밀하게 틀린다.
- IPCC의 "핵심 범주(key category)" 원칙도 따른다. 도시 레질리언스 판단에 영향이 큰 재해만 T3를 목표로 하고, 영향이 작은 재해(산불, 지진, 산사태, 우박)는 T1에 둔다.
- Models 탭의 데이터 범위는 티어에 이렇게 대응한다. 모델 정의와 계산은 바꾸지 않는다.

  | Models 탭 데이터 범위 | 방법 티어 |
  |---|---|
  | `GLOBAL_BASELINE` | T1 |
  | `DATA_API_COUNTRY` | T1 (같은 전 지구 제품을 국가로 자른 것) |
  | `KOREA_LOCAL` | T2 |

## 2. 티어 판정 규칙 (데이터 완전성)

1. 재해·티어마다 **필요한 입력 목록**이 있다(§3). 각 입력에는 상태가 붙는다.

   | 상태 | 뜻 |
   |---|---|
   | `held` | DATA_ROOT에 있음 |
   | `key` | .env에 있는 기존 키로 바로 받을 수 있음 |
   | `apply` | 사용자 활용신청이 필요함 |
   | `restricted` | 보안·협약 자료라 기대하지 않음 |
   | `none` | 출처가 없음 (만들거나 디지털화해야 함) |

2. 실행할 때 요청한 티어의 입력이 **하나라도** 없으면 한 단계씩 내려간다(`resolve_tier`). 결과에는 실제로 쓴 티어와 내려간 이유가 남는다. T1은 추가 입력이 없으므로 항상 가능하다.
3. 결측은 0으로 바꾸지 않는다. 입력이 빈 칸이면 "자료 없음"으로 드러낸다.
4. 상태 값은 데이터 목록 스프레드시트("데이터 목록" 시트, 열 "보유 상태")의 ID(`RF-03`, `HT-11` 등)로 추적한다.

### 2.1 자료 사용 규칙 (티어와 별개로 항상 적용)

- **환경부 홍수위험지도(공공누리 제4유형)**: 원문 폴리곤에 자산을 **겹쳐 보는 것만** 한다. 격자화하거나 클립·재투영한 결과를 배포하지 않는다. 원자료를 재배포하지 않는다. 화면과 보고서에는 출처를 표시한다. 비영리 싱크탱크 내부 분석이라는 사용자 판단(2026-11-13)에 따른다.
- **침수흔적도**: 조회용으로만 쓴다.
- **고객 실사자료**: 쓰지 않는다.

---

## 3. 재해별 티어표 (2026-11-14)

"현재"는 엔진이 지금 실제로 계산하는 티어다. "목표"는 핵심 범주 원칙으로 정한 최고 티어다.

| peril_id | 현재 | 목표 | T2 입력 | T3 입력 | 메모 |
|---|---|---|---|---|---|
| `river_flood` | **T1** | **T3** | 홍수위험지도 원문 폴리곤 (RF-03, held) | 하천 유량 (key), 5 m DEM (PF-05, apply), 하천 단면·제방고 (RF-04, apply) | ISIMIP 150초. 방어는 전국 일괄 50년 가정만. **T2 시험 연결(2026-11-14)**: Models 탭 `KOREA_LOCAL` = 원문 폴리곤 겹침 + 경계 지형 깊이, 헤드라인 전환은 비교 후 ([KOREA_FLOODMAP_INVESTIGATION.md](KOREA_FLOODMAP_INVESTIGATION.md) §8). T3 후보: LISFLOOD-FP + ANUGA 보조 점검 |
| `coastal_flood` | **T1** | **T3** | KHOA 조화상수 (held), GLO-30 (PF-07, held) | 조화상수, 상세 수심 (apply), 방조제 대장 (CF-03, none) | T2 = 약최고고조면 + 바다 연결성. T3 후보: SFINCS + GeoClaw |
| `tc_surge` | **T1** | **T3** | — | 태풍 경로 (TC-02, held), 상세 수심 (apply), 조위관측 (key) | T3 후보: SFINCS / GeoClaw, 매미·차바 재현 |
| `tropical_cyclone` | **T1** | **T2** | KDS 기본풍속 지도 (none, 디지털화 필요), ASOS (HT-11, key) | — | 개별 태풍 사례만 WindNinja / OpenFOAM |
| `tc_rain` | **T1** | **T1** | — | — | 도시 내수 T2/T3는 별도 재해로 추가 예정 (§4) |
| `heatwave` | **T2** | **T3** | 기상청 1 km TAMAX (HT-01, held), ASOS (HT-11, key) | 건물 높이 (CO-07, apply), ASOS, GLO-30 | 한국(기상청)일 때만 T2, 유럽(E-OBS)은 T1. T2.5: 도시형태 유형별 보정. T3: SOLWEIG + OpenFOAM 바람 → UTCI |
| `heat_mortality` | **T2** | **T2** | 기상청 1 km TA (HT-01, held), KOSIS 사망률 (HT-06, held) | — | 한국일 때만 T2, 그 외 T1. β 재적합은 T2 안에서 진행 |
| `drought` | **T1** | **T2** | TerraClimate (DR-01, held), 저수지 수위 (DR-04, apply) | — | SWAT+ T3는 농업 분석 쪽에서 판단 |
| `crop_yield` | **T1** | **T3** | — | 한국 품종 계수 (held), KOSIS 논벼 수량 (DR-08, held), ASOS 일자료 (HT-08, apply) | DSSAT + WOFOST. 영농형 논문 세션과 공유 |
| `low_flow` | **T1** | **T1** | — | — | |
| `wildfire` | **T1** | **T1** | — | — | 도시 레질리언스 영향이 작음 |
| `earthquake` | **T1** | **T1** | — | — | 같은 이유 |
| `landslide` | **T1** | **T1** | — | — | 같은 이유 |
| `hail` | **T1** | **T1** | — | — | 오픈소스 손실 모형 없음 |
| `european_windstorm` | **T1** | **T1** | — | — | 한국 비적용 |

### 3.1 요약

| 티어 | 현재 | 목표 |
|---|---|---|
| **T3** | 없음 | `river_flood`, `coastal_flood`, `tc_surge`, `heatwave`, `crop_yield` |
| **T2** | `heatwave`, `heat_mortality` | `tropical_cyclone`, `heat_mortality`, `drought` |
| **T1** | 나머지 13개 | `tc_rain`, `low_flow`, `wildfire`, `earthquake`, `landslide`, `hail`, `european_windstorm` |

---

## 4. 순서

1. **이 PR**: 정의, 표, `resolve_tier`, 결과의 `method_tier` 꼬리표. 숫자는 바뀌지 않는다.
2. **T2 구현**: 이미 가진 자료로 되는 것부터 한다. 구현이 끝날 때마다 §3의 "현재"를 올린다.
   - `KOREA_LOCAL` 하천: 홍수위험지도 원문 폴리곤 겹침
   - 해안: 약최고고조면 + 연결성
   - 폭염: 개활지 UTCI
3. **도시 내수**: 새 재해로 추가한다([ADDING_A_PERIL.md](ADDING_A_PERIL.md)). T2는 도시침수지도 겹침, T3는 itzi다. 하수관망은 `restricted`라 배수 용량을 가정으로 명시한다.
4. **T3 시범**: 서울 1 km² SOLWEIG, 한 곳의 SFINCS로 계산 시간을 재고, 같은 사건을 T1·T2와 비교한다.

T3 도구 후보와 라이선스는 2026-11-14 조사 결과(재해별 오픈소스 시뮬레이션 도구)를 따른다.
