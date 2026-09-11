# 한국 1 km 물리위험 — 구현 격차와 해상도 경로 표준

작성 2026-09-12 · 기준 `main` `0afac50` (+ PR #15 로더는 별도 표기) · 코드 변경 없음.

[KOREA_1KM_PHYSICAL_RISK_COVERAGE.md](KOREA_1KM_PHYSICAL_RISK_COVERAGE.md)(#17)가 "무엇이 있고 없는가"를 감사했다.
이 문서는 그 다음 질문에 답한다 — **"진짜 1 km 물리위험을 만들려면 코드의 어디를 바꿔야 하는가?"** 그러기 위해
페릴마다 다음 경로를 코드로 추적한다.

```
KMA source → transform → stored grid → hazard object → exposure grid → ImpactCalc resolution → output resolution
```

그리고 그 결과를 페릴마다 하나의 **Korea Physical Risk Standard** 카드(9필드)로 고정한다. 이 카드가 있어야 나중에
"한국 1 km physical risk를 지원한다"고 말할 때 **어느 페릴이 정말 1 km인지 객관적으로 증명**할 수 있다.

## 0. 용어 — 세 해상도는 다른 것이다

| 용어 | 정의 | 코드에서 결정되는 곳 |
|---|---|---|
| **source resolution** | 원자료 격자 간격 | KMA: 관측 0.005°(MK-PRISM v3.1 1201×1501) / SSP 0.01°(601×751); Data API: 산출물 고유 |
| **stored resolution** | 카탈로그 HDF5의 `Centroids` 간격 | `kma_scenario.load_summer_tmax(coarsen)` 블록평균 → `hazard_convert._centroids` |
| **calculation resolution** | `ImpactCalc`가 실제로 곱하는 격자 = 해저드 센트로이드 간격; 노출은 최근접 센트로이드에 배정 | `physical._impact` → `ImpactCalc(...).impact(assign_centroids=True)` (`physical.py:302`) |
| exposure resolution | 노출 객체의 공간 단위 | 점자산: 점 / 래스터: `build_exposure(res_arcsec=300)` 블록합산(0.0833°) |
| output resolution | 결과가 표현되는 단위 | 자산별 `per_asset.eai` + 포트폴리오 `aai_agg`; 지도는 자산 점 마커 |

**규칙**: "1 km"라는 말은 **calculation resolution이 1 km일 때만** 쓴다. source가 1 km인 것은 `1km-data-only`,
stored/calculation이 0.05°인 것은 `5km-current`다. 현재 한국에서 1 km 계산인 페릴은 **없다**.

## 1. 공통 경로 — 코드 추적

### 1.1 KMA → 카탈로그 (폭염 두 페릴만 이 경로를 탄다)

| 단계 | 코드 | 해상도에 일어나는 일 |
|---|---|---|
| KMA source | `~/climada/data/kma/*.tar.gz` | 관측 0.005°, SSP 0.01° |
| **⚠ `main`의 로더는 이 파일을 못 읽는다** | `main` `kma_scenario._NAME_RE`는 `skorea_` 토큰과 `y0_y1` 범위를 요구하나 실제 멤버는 `MKPRISM_MKPRISMv31_TA_gridraw_daily_2000.nc`(토큰 없음, 단일 연도)·AR6는 `.txt`(asc) | `main`에서 `load_summer_tmax("historical")` → `KmaUnavailable`. **디스크의 KOR 폭염 레이어 6개는 PR #15 로더가 만든 것**이며, #15가 머지되기 전까지 `main`은 이 경로를 재현할 수 없다 (본 문서 작성 중 실측: `main` 로더로 측정 시도 → `KmaUnavailable`) |
| transform ① 정합 | PR #15 `_to_native`: 0.005°를 `[::2, ::2]` 서브샘플 → 0.01° 노드 | 관측·SSP를 같은 0.01° 노드로 |
| transform ② 뭉개기 | `load_summer_tmax(coarsen)` → `_block_mean(arr, coarsen)`; **`coarsen` 기본값 5** (`heat_korea.py:298`, `"--coarsen", type=int, default=5`) | **0.01° → 0.05°**. 여기가 1 km가 사라지는 지점 |
| transform ③ 육지 마스크 | `missing ≤ 2 %` 셀만 유지 (+ PR #15 `common_footprint` 교집합) | 4,312 셀 |
| stored grid | `heat_mortality.standardized_grid(...)` → `hazard_convert.convert_grid_to_catalog` → `Centroids(lat, lon)` → HDF5 | 0.0500° 정확 (HDF5 판독) |
| **매니페스트** | `hazard_convert.convert_grid_to_catalog`가 기록하는 필드: peril · haz_type · climate_scenario · region · year · units · file · n_events · n_centroids · source · license | **해상도 필드가 없다.** 5 km인지 1 km인지 매니페스트로는 알 수 없고 HDF5를 열어야 한다 |
| hazard object | `catalog.load_hazard` → `Hazard.from_hdf5` | 0.05° |
| crop | `physical._crop_hazard(pad_deg=_CROP_PAD_DEG)`; **`_CROP_PAD_DEG = 2.0`** → 포트폴리오 bbox를 2° 패딩 | 센트로이드 수만 줄고 간격 불변 |
| exposure | 폭염 사망: 자산당 연령 2행, 점 (`physical.py:1177-1195`); 래스터 경로: `exposures._raster_exposure`가 WorldPop 픽셀을 `res_arcsec/3600 ÷ 픽셀` 배수 블록합산 | 점 / 0.0833° |
| ImpactCalc | `assign_centroids=True` → 각 노출점이 최근접 0.05° 센트로이드의 강도를 받음 | **0.05°** |
| output | `per_asset[i].eai`, `aai_agg`, `freq_curve`; UI `ResultsMap`은 자산 `CircleMarker`; `hazard_preview`는 센트로이드 최대강도를 **220 px** 폭 래스터로 다시 격자화한 PNG(렌더링용, 계산과 무관) | 자산 점 |

### 1.2 Data API → 카탈로그 (태풍·하천홍수·산불·지진)

| 단계 | 코드 | 해상도 |
|---|---|---|
| source | CLIMADA Data API 국가 산출물 | 0.0417° (~4.6 km), 5,708 센트로이드 |
| transform | 없음 (`ingest_dataapi` 그대로 저장; `source`에 **제공된** 시나리오, 키에 **요청된** 시나리오 — `ingest.py:295,351`) | 불변 |
| 자체 생성 경로 | `ingest_tctracks` / `ingest_tcrain`: `Centroids.from_pnt_bounds(bbox, res=0.1)` (`ingest.py:481`) | **0.1° ≈ 10 km** — Data API보다 성기다 |
| crop · ImpactCalc · output | 1.1과 동일 | 0.0417° (또는 0.1°) |

### 1.3 DEM 경로 (태풍 해일)
`ingest_copernicus_dem`: GLO-30(30 m) 모자이크를 **`_DEM_MAX_PIXELS = 250`** → 큰 변이 250 px로 데시메이션
(`ingest.py:58`). 포트폴리오 bbox 3°면 픽셀 ~1.3 km, 6°면 ~2.7 km. 즉 해일의 지형 해상도는 **bbox 크기에 따라
변한다** — 고정 해상도가 아니다.

## 2. "진짜 1 km"로 가려면 — 페릴별 변경 지점

### 2.1 폭염 사망 `heat_mortality` · 폭염 생산성 `heatwave` — 가장 가깝다

바꿀 곳은 **하나의 숫자**다: `heat_korea.py --coarsen 5 → 1`. 코드는 이미 허용한다. 대신 세 가지가 따라온다.

**측정** (2026-09-12, MK-PRISM 2000년 1시즌, PR #15 로더 — `main` 로더는 이 파일을 열지 못해 worktree로 측정):

| `coarsen` | 저장 간격 | ≈km | 육지 셀 | 로드 시간 | 강도 행렬(밀집, 20 이벤트) | ×20 시즌 추정 |
|---:|---:|---:|---:|---:|---:|---:|
| **5 (현재)** | 0.05° | 5.5 | 4,691* | 60.4 s | 0.8 MB | ~20 min |
| 2 | 0.02° | 2.2 | 26,697 | 53.8 s | 4.3 MB | ~18 min |
| **1** | 0.01° | 1.1 | **102,369** | 53.9 s | 16.4 MB | ~18 min |

\* 카탈로그의 4,312는 PR #15 `common_footprint`로 관측·시나리오 교집합을 취한 값; 단일 시나리오 로드는 4,691.

두 가지가 표에서 바로 읽힌다. **로드 시간은 `coarsen`과 무관하다** — 압축 해제·I/O가 지배하므로 1 km로 가도 로드
비용은 오르지 않는다. **강도 행렬도 작다** — 이벤트가 시즌 단위(20개)라 1 km에서도 16 MB다. 폭염의 1 km 장벽은
계산 비용이 아니다.

노출 쪽: WorldPop KOR 래스터의 픽셀은 **0.00833° ≈ 0.92 km**(661×704)인데 `build_exposure` 기본 `res_arcsec=300`은
0.0833°라 **블록 배수 10** — 래스터 노출은 원본보다 10배, 1 km 해저드보다 8배 성기게 합산된다. 1 km 해저드에
기본 래스터 노출을 붙이면 해상도 불일치가 노출 쪽에서 생긴다.

| 따라오는 문제 | 내용 | 판정 |
|---|---|---|
| 계산 비용 | 위 표 — 셀 22배(4,691→102,369), 로드 시간 불변, 강도 행렬 16 MB | **장벽 아님** |
| **노출 해상도 불일치** | 점자산은 문제없음. 래스터 노출은 `res_arcsec=300`(0.0833°) 블록합산이라 **해저드 1 km에 노출 8 km**가 된다 — 300→30 arcsec로 함께 내려야 한다(`exposures.build_exposure` 인자) | 코드 인자 변경 |
| **취약성 근거 부재** | β·밴드 폭은 1 km 근거가 없다. 1 km 해저드에 5 km와 같은 곡선을 쓰면 **정밀도가 겉보기로만 오른다** — `HEAT_MORTALITY_PROVENANCE.md`가 5 km를 택한 이유 | 방법론 결정 필요 |
| 매니페스트 | 해상도 필드가 없어 1 km 레이어와 5 km 레이어를 이름만으로 구분 못 함 | 필드 추가 필요(코드) |

**결론**: 폭염은 `1km-ready`까지의 **기술적** 거리가 가장 짧고(인자 1개 + 노출 인자 1개 + 매니페스트 필드), **방법론적**
거리는 남아 있다(1 km 취약성 근거). 두 거리를 섞어 말하지 않는다.

### 2.2 `heatwave` — 해상도 이전에 **강도 정의**가 틀렸다

`_CATALOG_PERILS["heatwave"]` 램프 `[0, 30, 35, 40, 45] °C → [0, 0, 0.1, 0.3, 0.6]`의 원래 전제를 git에서 확정했다.

| 커밋 | 날짜 | 무엇을 했나 |
|---|---|---|
| **`762ad5a`** | 2026-06-23 | 램프 도입. 메시지: *"indicative ramps pending an ingested driver, no fabricated science"*. 힌트: **`"ingest a heat (ERA5-HEAT/UTCI) hazard first."`** — 전제 입력은 **ERA5-HEAT / UTCI(열 스트레스 지수, °C 등가)** |
| **`54f52f7`** | 2026-09-03 | `heat_korea._heatwave_grid`가 KMA **일평균(TA) 시즌 p95**를 이 램프에 연결. 라벨 `"season p95 daily Tmax"` |

즉 램프는 **UTCI 스케일**(강한 열 스트레스 32 °C, 극심 38 °C 부근)을 전제로 설계됐고, 일최고기온도 일평균기온도
전제하지 않았다. 지금 들어가는 것은 일평균 p95(18.7–32.6 °C)라 램프의 0.1 지점(35 °C)에 닿지 않는다.

**결정이 필요하고, 이 문서는 결정하지 않는다.** 선택지:
- (a) 전제대로 **UTCI/열지수 해저드**를 만든다 — KMA `TA`+`RHM`(+`WS`,`SI`)로 계산 가능, 인제스터 신설.
- (b) 램프를 **일평균 TA p95 스케일로 재정의**한다 — 새 곡선, 근거 필요.
- (c) `TAMAX`를 배선해 램프의 °C 스케일에 가깝게 맞춘다 — 그러나 램프가 Tmax를 전제한 적도 없으므로 **또 하나의 임의 수정**이 된다.

어느 쪽이든 **새 해저드 정의**이며, 해상도를 1 km로 올리기 전에 끝나야 한다. 틀린 지표를 더 촘촘히 계산하는 것은
의미가 없다.

### 2.3 하천홍수 `river_flood` — 1 km는 다른 모델을 뜻한다

현재 경로는 ISIMIP 전지구 수문모델 산출(0.0417°)이다. RN을 넣어도 홍수가 되지 않는다:

```
RN(강수) → 유출 → 하천유량 → 범람 → 수심 → 피해
```

이 사슬의 중간(유출·유량·범람)이 **외부 수문·수리 모델**이고 저장소에 없다. 1 km 하천홍수의 현실적 경로는
**환경부 홍수위험지도**(수리계산이 이미 들어간 수심 래스터)인데, 4.9 GB가 디스크에 있고 소비자가 없다
(`Fetched ≠ Implemented`). 바꿀 곳: SHP→표준화 그리드 온램프(신규) + 라이선스 해소(공공누리 4유형). 그러면
**source = 홍수위험지도 해상도**가 되고 KMA와 무관하다.

### 2.4 내수침수 — 페릴 부재

```
RN → 강우강도/누적강수 → 배수능력 초과 → 침수 → 피해
```

"배수능력"이 도시 하부구조 자료를 요구한다. KMA `RN`은 첫 칸일 뿐이다. 페릴·러너·곡선 전부 없음(G7).

### 2.5 태풍 바람 `tropical_cyclone` — KMA와 무관, 1 km는 재격자
IBTrACS 트랙 → `TropCyclone.from_tracks(centroids)`. 1 km는 `res=0.1` → `0.01`로 센트로이드를 바꾸는 문제이나
이벤트 43,560개 × 셀 ~10만 = 희소행렬이라도 수십 GB — **비용이 지배**한다. KMA `WS`(일평균 풍속)로 대체할 수
없다(이벤트 단위 아님, 지속풍속 정의 불일치).

### 2.6 태풍 해일 `tc_surge`
DEM 250 px 캡을 풀고 1 km 이하 DEM을 쓰면 배스텁 모델 메모리가 문제(`ingest.py:55-58` 주석). 해상도는 bbox 종속.

### 2.7 산불·지진
Data API 산출물 그대로. 1 km는 소스 교체를 뜻한다(FIRMS 원자료 / 국내 지진 해저드). KMA 무관.

### 2.8 페릴이 없는 것 — 한파·대설·열 스트레스·비태풍 강풍
해상도 논의 이전 단계. KMA 변수(`TAMIN`, `TA+RHM`, `WS`)는 있으나 해저드 정의·곡선·검증이 모두 없다.

## 3. Korea Physical Risk Standard — 페릴별 카드

9필드. 값이 없으면 `없음`, 모르면 `미확인`이라고 쓴다. "1 km"는 calculation resolution 열에만 쓴다.

### `heat_mortality` — 폭염 사망
| 필드 | 값 |
|---|---|
| Hazard source | KMA 남한상세 `TA` (MK-PRISM v3.1 관측 / AR6 5ENSMN SSP245·585), 초과도일로 변환 |
| Source resolution | 0.005° (관측) / 0.01° (SSP) |
| Stored resolution | **0.0500°** (4,312 센트로이드), `coarsen=5` |
| Calculation resolution | **0.05° ≈ 5 km** → `5km-current` |
| Exposure resolution | 점자산(연령 2행) / 래스터 시 0.0833° |
| Impact function | climaterisk custom `mdd = min(m₀·a·D^b, 1)`; β indicative, MMT 외부(Kim 2020), 기저사망률 `main` legacy → PR #16 KOSIS 2024 |
| Scenario | historical 2000–2019 · SSP245/585 **2021–2030만** |
| Temporal scale | 시즌(6–9월) 이벤트, 연 1회 |
| Validation source | 없음(사망 대조). 해저드 수준: 2018 1위·2003 최하위 (PR #15) |

### `heatwave` — 폭염 생산성
| 필드 | 값 |
|---|---|
| Hazard source | KMA `TA` 시즌 p95 (라벨 "daily Tmax" — 오표기) |
| Source resolution | 0.005° / 0.01° |
| Stored resolution | 0.0500° (4,312) |
| Calculation resolution | 0.05° → `5km-current` |
| Exposure resolution | 점자산 value |
| Impact function | indicative 램프 `[0,30,35,40,45]°C` — **UTCI 전제(`762ad5a`), 입력과 스케일 불일치** |
| Scenario | historical · SSP245/585 2021–2030 |
| Temporal scale | 시즌 이벤트 |
| Validation source | 없음 |

### `river_flood` — 하천홍수
| 필드 | 값 |
|---|---|
| Hazard source | CLIMADA Data API ISIMIP (KMA 아님); 홍수위험지도 4.9 GB 미소비 |
| Source resolution | 0.0417° |
| Stored resolution | 0.0417° (5,708) |
| Calculation resolution | 0.0417° ≈ 4.6 km → `missing`(KMA 기준) |
| Exposure resolution | 점자산 / 폴리곤 footprint 점 분해 |
| Impact function | CLIMADA native JRC Asia 주거 (지역 프리셋) |
| Scenario | 키 `rcp45 2050` — **소스 `rcp60`** (`_RF_SCENARIO_MAP` 기본값) |
| Temporal scale | 연 최대 수심 이벤트 480 (GCM×연도) |
| Validation source | 없음(재해연보 `heavy_rain` 열은 F1 로더로 읽을 수 있으나 미연결) |

### `tropical_cyclone` — 태풍 바람
| 필드 | 값 |
|---|---|
| Hazard source | IBTrACS 합성 트랙 → `TropCyclone` 바람장 (Data API; KMA `WS` 무관) |
| Source resolution | 0.0417° (Data API) / 자체 생성 시 `res=0.1` |
| Stored resolution | 0.0417° (5,708) |
| Calculation resolution | 0.0417° → `missing`(KMA 기준) |
| Exposure resolution | 점자산 |
| Impact function | CLIMADA native Emanuel + Eberenz WP4 `v_half=190.5` (한국 보정 아님) |
| Scenario | `rcp45 2040`; 2060/2080 조회 가능 |
| Temporal scale | 이벤트(트랙) 43,560, 빈도 합 96/yr(합성 배수 포함) |
| Validation source | 재해연보 태풍(집계) — **보정 BLOCKED**, 진단만(`analysis/tc-capture-kr`) |

### `wildfire` — 산불
| 필드 | 값 |
|---|---|
| Hazard source | Data API FIRMS 시즌 최대 밝기온도 |
| Source / Stored / Calculation | 0.0417° (5,708) → `missing`(KMA 기준) |
| Exposure resolution | 점자산 |
| Impact function | sigmoid `from_sigmoid_impf`, 무임계(G3) |
| Scenario | **historical만** (`physical.py:589` 하드코딩) |
| Temporal scale | 시즌 이벤트 20 |
| Validation source | 없음 |

### `earthquake` — 지진 (기후 무관, 비교용)
| 필드 | 값 |
|---|---|
| Hazard source | Data API observed MMI |
| Source / Stored / Calculation | 0.0417° (5,708) |
| Exposure resolution | 점자산 |
| Impact function | CLIMADA native |
| Scenario | N/A |
| Temporal scale | 이벤트 41,710 |
| Validation source | 없음 |

### 카드가 없는 페릴
`coastal_flood` · `tc_rain` · `tc_surge`(실행 시 파생) · `drought` · `landslide` · `hail` · `crop_yield` · `low_flow`:
KOR 레이어가 없어 stored/calculation 해상도가 **존재하지 않는다**. 한파·대설·내수침수·열 스트레스: 페릴 자체 없음.

## 4. 공통 구조 격차 — 페릴을 하나 붙이기 전에 고칠 것

이것들을 먼저 고치지 않으면 RN·WS·TAMAX를 붙일 때마다 서로 다른 방식으로 구현된다.

| # | 격차 | 왜 구조적인가 | 바꿀 곳 |
|---|---|---|---|
| S1 | **매니페스트에 해상도 필드가 없다** | 1 km와 5 km 레이어를 구분할 수단이 파일명뿐 | `hazard_convert.convert_grid_to_catalog` 기록 필드 + `catalog` 스키마 |
| S2 | **`coarsen`이 CLI 인자다** | 해상도가 방법론 결정이 아니라 실행 옵션으로 흩어짐 | 페릴별 표준 카드의 값을 코드 상수로 |
| S3 | **노출 해상도가 해저드와 독립** | `res_arcsec=300` 고정 → 해저드를 1 km로 올려도 노출은 8 km | 해저드 해상도에서 노출 해상도를 유도 |
| S4 | **시나리오 키 ≠ 소스 시나리오** | `rcp45` 키 아래 `rcp60` 데이터 | 매니페스트에 `requested_scenario`/`served_scenario` 분리 |
| S5 | **`main` 로더가 실파일을 못 읽음** | PR #15 미머지 — 재현 불가 상태 | #15 머지 |
| S6 | **KMA 변수 온램프가 `TA` 전용** | `load_summer_tmax`가 여름·기온 전제(`SEASON_*`, `tmax` 필드명) — RN·WS는 다른 시간창·집계가 필요 | 변수 일반화된 로더(계절·집계 파라미터화) |
| S7 | **heatwave 강도 정의 미확정** | UTCI 전제 램프에 TA p95 | §2.2 결정 |

## 5. 우선순위 (제안, 구현 아님)

```
S5 #15 머지 (재현성 회복)
  → S7 heatwave 강도 정의 결정 (UTCI / TA 재정의 / TAMAX — 문서로 결정, 근거 첨부)
  → S1+S4 매니페스트 해상도·시나리오 필드
  → S2+S3 해상도를 상수화하고 노출 해상도를 해저드에서 유도
  → S6 KMA 로더 일반화 (RN·WS·TAMIN 진입 전제)
  → 그 다음에야 RN(극한강수) · TAMIN(한파) · RHM(열 스트레스) 순
```

1 km 자체는 폭염에서 인자 하나로 되지만, **그 전에 S7이 끝나야 하고, 그 후에 1 km 취약성 근거가 있어야** "1 km
물리위험"이라 부를 수 있다. 지금 정확한 표현은 `1km-data-only` 또는 `5km-current`다.
