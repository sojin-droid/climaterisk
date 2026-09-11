# 한국 1 km 물리위험 — 구현 격차와 해상도 경로 표준

작성 2026-09-12 · 기준 `main` `b951a95` (#15 머지 후) · 코드 변경 없음.

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
| **S5 해소 (2026-09-12)** — `main`이 실파일을 읽는다 | PR #15 머지(`b951a95`). 머지 전 `main`의 `_NAME_RE`는 `skorea_` 토큰·`y0_y1` 범위를 요구해 실제 멤버(`MKPRISM_MKPRISMv31_TA_gridraw_daily_2000.nc`, AR6 `.txt`)를 거부했고 본 문서 초안의 측정은 worktree로 우회했다 | 머지 후 `main`에서 `scripts/heat_korea.py files` → 아카이브 3개 `[x]`, `present daily TA seasons: 40 [historical:2000-2019, rcp45:2021-2030, rcp85:2021-2030]`. KOR 폭염 레이어 6개의 생성 경로가 `main`에서 재현 가능해졌다 |
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
| S5 | ~~`main` 로더가 실파일을 못 읽음~~ | **해소 2026-09-12** — #15 머지 `b951a95`; `main`에서 발견·파싱 재현 확인 | 완료 |
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

---

# 제2부 — 공통 인프라·방법론 격차의 설계 (S7 · S1 · S4 · S2/S3) — 2026-09-12

새 페릴을 추가하지 않는다. 아래는 **설계와 정의**이며 코드는 바꾸지 않았다. 목표는 "1 km 기능"이 아니라
**1 km라고 부를 수 있는 정확한 기술·방법론적 조건을 코드와 문서에 고정**하는 것이다.

## Heatwave intensity definition

git이 확정한 사실: `762ad5a`(2026-06-23)가 램프 `[0, 30, 35, 40, 45] °C → [0, 0, .1, .3, .6]`를 **ERA5-HEAT/UTCI
드라이버를 전제한 지시적 램프**로 도입했고, `54f52f7`(2026-09-03)이 KMA **일평균 TA 시즌 p95**(실측 18.7–32.6 °C)를 그
램프에 연결하며 `"daily Tmax"`로 라벨링했다. 램프는 UTCI/열지수 스케일이고, 레이어는 일평균 백분위다. **이 절은 어느
안도 선택하지 않는다.** 세 안을 같은 8항목으로 정의해 결정의 재료만 놓는다.

### Option A — UTCI 해저드 (램프의 원래 전제)

| 항목 | 내용 |
|---|---|
| 물리적 의미 | 기온·습도·풍속·복사를 결합한 **인체 열 스트레스 등가온도**(UTCI, °C). 램프의 30/35/40/45는 UTCI 등급(강한 32·매우 강한 38·극심 46)과 같은 자릿수 |
| 필요 KMA 변수 | `TA`(기온) · `RHM`(→ 수증기압) · `WS`(10 m 풍속) · `SI`(단파 → 평균복사온도 MRT의 한 항). **MRT는 장파·지면온도·태양고도도 필요** — KMA 7종만으로는 근사치 |
| 시간 집계 | UTCI는 **시간 단위**로 정의된다. KMA는 일자료(일평균)라 일평균 입력으로 계산한 UTCI는 정의상의 UTCI가 아니라 **일평균 근사**다. 시즌 통계(p95 등)는 그 위에서 정해야 한다 |
| 공간 해상도 | 0.01°(SSP)/0.005°(관측) 입력 → 저장은 계약 해상도(현 0.05°) |
| 임팩트 함수 호환 | **현 램프와 스케일이 맞는 유일한 안** — 단, 램프 자체가 지시적(출처 없음)이므로 스케일이 맞아도 값의 근거는 여전히 없다 |
| 필요한 과학적 근거 | 일평균 입력 UTCI의 타당성(문헌) · UTCI-생산성 곡선(예: ISO 7243/WBGT 계열과의 대응) · 한국 노동 노출 |
| 현재 구현 | 없음 — petals의 ERA5-HEAT/UTCI 인터페이스는 petals 6.1에서 분리되어 저장소 밖(CLIMADA_METHODS §5.8); RHM·WS·SI 미배선 |
| 다음 결정 | 일평균 UTCI 근사를 받아들일지, 시간자료(KMA 외 소스)를 요구할지 |

### Option B — TA 전용 모델 (현 레이어를 유지, 함수를 바꾼다)

| 항목 | 내용 |
|---|---|
| 물리적 의미 | 일평균기온의 시즌 백분위(현 p95) 또는 한국형 폭염 지표(초과일수·초과도일). 폭염 사망 모델과 **같은 지표 계열** |
| 필요 KMA 변수 | `TA`만 (이미 배선·보유) |
| 시간 집계 | 일 → 시즌(6–9월) 통계 |
| 공간 해상도 | 현 경로 그대로 |
| 임팩트 함수 호환 | **현 램프 사용 불가** — 30–45 °C 구간을 일평균 p95는 닿지 못한다. TA 백분위(또는 초과도일)에 맞는 **새 곡선**이 필요 |
| 필요한 과학적 근거 | 기온-노동생산성 관계의 한국/아시아 추정치(일평균 기준), 임계값 정의(예: Kim 2020 93백분위와의 정합) |
| 현재 구현 | 레이어 ✅(HW 3개), 곡선 ❌(스케일 불일치) |
| 다음 결정 | 지표를 p95로 둘지 초과도일로 바꿀지; 폭염 사망과 지표를 공유할지 |

### Option C — TAMAX 모델

| 항목 | 내용 |
|---|---|
| 물리적 의미 | 일최고기온 기반 폭염(특보 기준 33/35 °C, 초과일수) |
| 필요 KMA 변수 | `TAMAX` (포털 제공, **미다운로드·미배선**) |
| 시간 집계 | 일 → 시즌 초과일수/강도 |
| 공간 해상도 | 현 경로 그대로 |
| 임팩트 함수 호환 | 램프의 °C 구간(30–45)과 자릿수는 맞지만 **램프가 Tmax를 전제한 적이 없다**(`762ad5a` 힌트는 UTCI). 스케일 유사성은 근거가 아니다 |
| 필요한 과학적 근거 | Tmax 기준 생산성/사망 곡선; TA 기준인 폭염 사망 모델과의 지표 분리 정당화 |
| 현재 구현 | 없음 — 2026-09-09 이전 TAMAX를 읽던 코드는 제거됨(`heat_korea.py:84` 기록) |
| 다음 결정 | 램프를 Tmax용으로 **재정의**할 것인지(그러면 B와 같은 "새 곡선" 문제) |

**공통 결론**: 세 안 모두 **새 해저드 정의 + 새 곡선 근거**를 요구한다. A만 현 램프의 스케일과 맞지만 램프의 값 자체는
어느 안에서도 근거가 없다. 결정은 문서로 하고, 결정 전에는 KOR `heatwave` 결과를 **구조적 ≈0**으로 읽어야 한다.

## Resolution metadata design (S1)

현재 매니페스트(`data/hazard_db/catalog.json`, `{"entries": [...]}`)의 항목 필드는 `peril · haz_type · climate_scenario ·
region · year · units · file · n_events · n_centroids · source · license`(`hazard_convert.convert_grid_to_catalog`,
`build_hazard.cmd_cache`, `ingest.*`). 조회는 `catalog.lookup(peril, climate_scenario, region, year)`가 **네 키만** 본다
(`catalog.py`). 해상도는 어디에도 없다.

**설계 원칙**: 기존 키 4개와 조회 로직을 건드리지 않는 **선택적(optional) 필드**만 추가하고, 값은 **파일·배열에서
파생 가능한 것만** 쓴다. 없는 값은 쓰지 않는다(`null`).

| 필드 | 파생 원천 | 파생 가능? |
|---|---|---|
| `stored_resolution` (deg) | `Centroids.lat/lon` 고유값 간격의 중앙값 — 본 문서 §10이 이미 이 방법으로 0.0500°/0.0417°를 읽었다 | ✅ 배열 |
| `grid_spacing` (deg, `[dlat, dlon]`) | 같은 방법, 축별 | ✅ 배열 |
| `grid_width` / `grid_height` | 정규 격자일 때 `unique(lon).size` / `unique(lat).size`; 비정규(트랙 기반 TC 센트로이드 등)면 `null` | ✅ 정규 격자만 |
| `crs` | `Centroids.crs` (기본 EPSG:4326) | ✅ 객체 |
| `source_resolution` (deg) | **배열에서 파생 불가** — 로더가 안다: KMA `GRID_RES_DEG`(0.01)·관측 0.005; Data API는 산출물 메타 | 🟡 로더가 기록해야 함 |
| `calculation_resolution` | 현 엔진에서는 **정의상 `stored_resolution`과 같다**(`assign_centroids=True`가 노출을 저장 격자에 붙임). 별도 값을 저장하지 않고 **규칙**으로 문서화; 엔진이 재격자를 도입하면 그때 필드화 | 규칙 |

`n_centroids`는 이미 있으므로 `grid_width × grid_height ≥ n_centroids`(육지 마스크)로 정합성 검사가 가능하다.

## Scenario metadata design (S4)

현재: 매니페스트 키 `climate_scenario`에는 **요청된** 시나리오가, `source` 문자열에는 **제공된** 시나리오가 들어간다.
기제는 `ingest.py:295` `_RF_SCENARIO_MAP.get(scenario, "rcp60")`(요청→제공 매핑)과 `:351`(`source`에 제공값 기록).
실례: `river_flood/RF_rcp45_KOR_2050.hdf5` — 키 `rcp45`, 소스 `"CLIMADA Data API (river_flood, rcp60, cached)"`.

**설계**: 선택적 필드 두 개. 조회 키는 그대로 `climate_scenario`(= 요청값)로 두어 **선택 로직을 바꾸지 않는다.**

| 필드 | 값 | 파생 |
|---|---|---|
| `requested_scenario` | 플랫폼 키(`rcp45`) | = `climate_scenario` (중복이지만 의미를 이름에 박는다) |
| `served_scenario` | 소스가 실제 제공한 시나리오(`rcp60`, `SSP245`, `historical`, `observed`) | 인제스터가 이미 아는 값(`plan.src_scenario`); KMA는 파일명 토큰(`SSP245`) |

KOR 레이어에 적용하면: RF `rcp45 / rcp60` **불일치 1건**; HM/HW `rcp45 / SSP245`, `rcp85 / SSP585`는 **명명 체계 차이**(불일치
아님, 매핑 `SCENARIO_TOKENS`); 나머지 동일. 소비자는 `requested ≠ served`일 때 결과에 그 사실을 표시할 수 있게 된다.

## Resolution contract (S2 · S3)

현재 해저드와 노출의 해상도는 **서로 모르고** 정해진다: `heat_korea.py --coarsen`(CLI, 기본 5 → 0.05°)과
`exposures.build_exposure(res_arcsec=300)`(0.0833°, WorldPop 0.00833° 대비 블록 배수 10). 1 km 해저드에 기본 노출을 붙이면
해저드 1 km / 노출 8 km가 된다(#18 §2.1 측정).

**계약(contract)**: 하나의 `target_calculation_resolution`에서 나머지를 **유도**한다.

```
source_resolution (로더가 보고)
      ↓
target_calculation_resolution  ← 방법론 결정(페릴별 상수), CLI 옵션이 아님
      ↓ coarsen = round(target / source)                      [hazard grid]
      ↓ res_arcsec = target × 3600                            [exposure grid]
      ↓ ImpactCalc(assign_centroids) — 두 격자가 같은 간격이면 최근접 배정이 1:1
      ↓ manifest.stored_resolution == target  (S1로 검증 가능)
```

| 계약 필드 | 현재 값(폭염 경로) | 비고 |
|---|---|---|
| `source_resolution` | 0.01° (SSP) / 0.005° (관측→0.01° 서브샘플) | 로더 상수 |
| `target_calculation_resolution` | **0.05°** (= `coarsen 5`) | 현재는 CLI 인자 — 계약으로 옮길 대상 |
| hazard grid | 0.05° | 유도 |
| exposure grid | **0.0833°** (`res_arcsec=300`) — **계약 위반** | 유도되면 0.05° = 180 arcsec |
| calculation | 0.05° | = stored |

이번 단계에서 값은 바꾸지 않았다. 바뀌어야 할 것은 "해상도가 어디서 정해지는가"이며, 그 위치가 코드 상수로 옮겨지면
`res_arcsec`는 인자가 아니라 유도값이 된다.

**TC는 별도 계약이다.** TC는 KMA 격자가 아니라 CLIMADA 이벤트셋(트랙→바람장) 구조이고, 자체 생성 센트로이드는
`res=0.1°`(≈10 km, `ingest.py:481`), Data API 레이어는 0.0417°다. **TC를 KMA 1 km와 섞어 "TC = 1 km"라고 부르지 않는다.**
TC의 해상도 방법론은 `TropCyclone.from_tracks(centroids)`의 센트로이드 격자로 따로 기록한다.

## 1km-ready 정의

다음 **다섯 조건을 모두** 충족할 때만 `1km-ready`다. 하나라도 빠지면 `NOT 1km-ready`.

```
1. hazard source/grid ≈ 1 km
2. stored grid ≈ 1 km
3. ImpactCalc calculation grid ≈ 1 km
4. exposure matched at ≈ 1 km
5. metadata records the actual resolution
```

네 상태: `1km-ready` · `1km-data-only`(소스만 1 km) · `5km-current`(저장·계산 0.05°) · `missing`(KOR 레이어·소비자 없음).
**"KMA source = 1 km"라는 이유만으로 1 km 계산이라고 표현하지 않는다** — 폭염 사망이 정확히 그 경우다.

### 페릴별 현재 상태 — 네 해상도를 각각 표시

| 페릴 | source | stored | calculation | exposure | metadata(S1) | 상태 |
|---|---|---|---|---|---|---|
| `heat_mortality` | 0.005°/0.01° (KMA) | 0.05° | 0.05° | 점 / 0.0833° | ❌ 없음 | **5km-current** |
| `heatwave` | 0.005°/0.01° (KMA) | 0.05° | 0.05° | 점 | ❌ | **5km-current** (+ 강도 정의 미결) |
| `tropical_cyclone` | 이벤트셋; Data API 0.0417° / 자체 `res=0.1°` | 0.0417° | 0.0417° | 점 | ❌ | **missing** (KMA 기준) — TC 별도 계약, **1 km 아님** |
| `river_flood` | Data API 0.0417° | 0.0417° | 0.0417° | 점 | ❌ | **missing** (KMA 기준) |
| `wildfire` | Data API 0.0417° | 0.0417° | 0.0417° | 점 | ❌ | **missing** (KMA 기준) |
| `earthquake` | Data API 0.0417° | 0.0417° | 0.0417° | 점 | ❌ | N/A (기후 무관) |
| 열 스트레스 · 극한강수/내수침수 · 태풍 강우 · 가뭄 · 한파 | KMA 0.01° 소스 존재 | — | — | — | — | **1km-data-only** |
| `coastal_flood` · `tc_surge` · `landslide` · `hail` · `crop_yield` · `low_flow` · 대설 · 복합 | — | — | — | — | — | **missing** |

**1km-ready: 0.** 조건 5(메타데이터)는 현재 **모든** 레이어가 미충족이므로, S1 없이는 어떤 페릴도 1km-ready가 될 수 없다.

## 기록해 둘 불일치 두 건 (값·파일 변경 없음)

**Heatwave layer** — `heatwave/HW_{historical,rcp45,rcp85}_KOR_*.hdf5`: 매니페스트 `source` 라벨 `"season p95 daily Tmax"`;
실제 배열은 **일평균 TA의 시즌 p95**, 값 범위 **18.7–32.6 °C**(`heat_korea.py:124` `np.percentile(obs.tmax, 95)`, `obs.tmax`는
일평균의 하위호환 별칭 — `eobs.py:88-89`). 라벨이 Tmax를 주장할 수 없다.

**River-flood layer** — `river_flood/RF_rcp45_KOR_2050.hdf5`: `requested_scenario = rcp45`, `served_scenario = rcp60`
(`_RF_SCENARIO_MAP` 기본값). 홍수 모델 자체는 수정하지 않는다.

## `supported_mvp`는 한국 구현이 아니다

`assets/libraries/perils.json`의 15개 페릴은 **전부 `supported_mvp: true`**다. 그 플래그는 "UI에서 선택 가능"을 뜻하며
`requires_ingest: true`인 9개는 레이어가 있어야 돈다. 이 문서의 상태 표는 그 플래그를 근거로 쓰지 않는다 — KOR 레이어가
없는 페릴은 `1km-data-only` 또는 `missing`이며, `supported_mvp`가 `5km-current`나 `1km-ready`를 만들지 않는다.
