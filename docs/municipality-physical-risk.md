# Korea municipality physical-risk assessment (V0.2)

Companion to [`physical-risk-models.md`](physical-risk-models.md) (the three-model engine,
frozen at `v0.1.1-physical-risk-final`) and [`korea-hazard-replacement.md`](korea-hazard-replacement.md).
V0.2 adds one thing on top of that baseline: **an official municipality representative point
can be the assessment target instead of a portfolio asset.** The engine, the adapters, the
published CLIMADA impact functions, the risk bands and the Excel writer are the same code.

```text
Municipality representative point            →  hazard screening
Municipality representative point + user-supplied asset value  →  financial risk assessment
```

Every municipality result carries this sentence, verbatim, in the UI, the detail panel, the
Excel *Run Info* and *Methodology* sheets and the CLI output:

> This result represents hazard conditions at the selected municipality's representative
> point. It is not a municipality-wide spatial aggregation.

## 1. The dataset — `assets/libraries/korea_municipalities.csv`

| | |
|---|---|
| source | 국가데이터처 (Statistics Korea) **SGIS 행정구역 통계 및 경계**, data.go.kr dataset 15129688, package registered 2026-07-23 |
| layers | `bnd_sido_00_2025_2Q.shp` (17 시도) and `bnd_sigungu_00_2025_2Q.shp` (252 SGIS 시군구 units), `BASE_DATE 20250630`, EPSG:5179 |
| licence | 공공데이터포털 **이용허락범위 제한 없음** |
| rows | **269** = 17 시도 + 252 시군구 units (8 METROPOLITAN, 9 PROVINCE, 66 CITY, 82 COUNTY, 104 DISTRICT) |
| representative point | `shapely.representative_point()` of each polygon — a point **guaranteed to lie inside the official boundary**, computed in EPSG:5179 and transformed to WGS84. `point_type = BOUNDARY_INTERIOR_POINT` |
| id | `SGIS-<ADM_CD>` (SGIS code: 2 digits for 시도, 5 for 시군구); `admin_code` keeps the bare code |
| builder | `scripts/build_korea_municipalities.py` (worker env, geopandas) — the CSV is the only committed artefact; the 269 MB source package stays outside the repository |
| loader | `climaterisk.physical_risk.municipalities` — stdlib only; `validate()` checks unique ids, coordinates inside Korea, known levels/point types and stated provenance on every load |

Two things the dataset deliberately is **not**:

* **Not city-hall locations.** The nationwide office-location product (행정안전부 *도로명주소
  민원행정기관 전자지도*, data.go.kr 15050409) is released only after an application with
  identity verification and a purpose review on juso.go.kr (도로명주소법 시행령 제46조), so it
  could not be used. The schema keeps `office_name` / `address` (null today) and a
  `point_type = OFFICE_LOCATION` value so that product can replace the interior points row for
  row, without a schema change, once the user obtains it. No coordinate was typed in or copied
  from a web page.
* **Not asset values.** A municipality row has no value. The engine prices it only with a value
  the user supplies for that municipality (`asset_values` in the API, `asset_value_usd` in the
  CLI CSV, the input box in the UI). Nothing is estimated from population, building area or a
  "typical city hall".

SGIS 시군구 units include the 일반구 of large cities (e.g. 수원시 장안구, level DISTRICT) and
the 행정시 of 제주 (level CITY); the level comes from the official name suffix and the tier from
the source layer. A `DISTRICT` row is therefore a statistical unit, not necessarily a
self-governing 자치구.

## 2. What a municipality row means

| field | value |
|---|---|
| `assessment_target` | `MUNICIPALITY` (portfolio assets stay `FACILITY`) |
| `facility_id` / `municipality_id` | the SGIS id; `municipality_name`, `municipality_level`, `province_name`, `point_type`, `latitude`, `longitude` are carried on every row |
| hazard fields | the hazard grid cell nearest the representative point — exactly what a facility row gets at the same coordinate |
| `spatial_resolution` … | the grid the row was read from (§4) |
| money | present only when a value was supplied **and** the status is `FULL` (or `RETURN_PERIOD_NOT_RESOLVABLE`, EAL only) |
| `calculation_status` | `NO_EXPOSURE_DATA` for a screened point — hazard intensity reported, money `null`, `status_detail` says no value was supplied; shown as **"No asset value"** |

A computed zero stays `0` (Busan / Incheon / Chuncheon flood at their points: dry cell, EAL 0,
Low). A missing value stays empty. The two are never merged.

## 3. Measured on the real chain (2026-09-27, rcp85 / 2040, Recommended)

`scripts/municipality_physical_risk.py` with the five validation municipalities (values equal to
the frozen asset-level fixture) plus two screening-only 구:

| municipality | flood (Country, ISIMIP) | TC (Country) | heat (Local, KMA TAMAX) |
|---|---|---|---|
| 광주광역시, $80 M | 8.31 m · EAL **$597,067** · 0.746 % · **High** | 55.2 m/s · $11,200 · Low | 37.8 °C · hazard only |
| 서울특별시, $100 M | 6.97 m · $30,000 · 0.030 % · Low | 53.8 m/s · $4,235 · Low | 37.6 °C · hazard only |
| 부산광역시, $60 M | 0 m · **$0** · Low (computed zero) | 60.3 m/s · $14,749 · Low | 34.1 °C · hazard only |
| 춘천시, $50 M | 0 m · $0 · Low | 41.1 m/s · $488 · Low | 37.2 °C · hazard only |
| 인천광역시, $80 M | 0 m · $0 · Low | 57.0 m/s · $5,399 · Low | 34.9 °C · hazard only |
| 서초구, no value | 0 m · **No asset value** | 52.3 m/s · No asset value | 38.1 °C · hazard only |
| 광주 서구, no value | 8.31 m · No asset value | 55.4 m/s · No asset value | 37.6 °C · hazard only |

Same functions as the asset baseline (JRC sector curves ids 21/22, Eberenz North West
Pacific id 9); `KOREA_LOCAL` flood/TC stay `NOT_IMPLEMENTED`; Global was not downloaded. The
서울특별시 interior point falls in a wet ISIMIP cell (6.97 m at a very low frequency), the
서초구 point in a dry one — the points were **not** moved to make either case appear.

## 4. Spatial resolution — read from the data

Every row and every adapter description carries `spatial_resolution`, `spatial_resolution_unit`,
`spatial_unit_type` and a description. The worker **measures** the value on the loaded hazard
(`adapters.measure_grid_spacing`: median step of the unique centroid latitudes/longitudes) and,
for the Data API, reads the dataset property `res_arcsec`; a >5 % disagreement is written into
the description. The backend registry (`coverage.SPATIAL_RESOLUTION`) declares the expected
values and `tests/test_municipality_worker.py` asserts the measurement equals the declaration
on the cached layers.

| hazard | source | grid | approx. size in Korea | financial |
|---|---|---|---|---|
| Flood | Data API `river_flood_150arcsec` (ISIMIP) | **150 arcsec** grid cell | ~4–5 km | yes |
| Tropical cyclone | Data API `tropical_cyclone_10synth_tracks_150arcsec` | **150 arcsec** grid cell | ~4–5 km | yes |
| Heatwave | KMA 남한상세 TAMAX via the KOR catalog layer | **0.05°** grid (source 0.005° obs / 0.01° SSP, block-averaged when the layer is built) | ~4–6 km | no |
| Drought | none connected | N/A | — | no |
| Sea-level rise | none — static configuration only | N/A (no grid) | — | no |

Vocabulary: *grid*, *grid cell*, *hazard centroid*, *spatial resolution*. Not "pixel".

> Spatial resolution describes the size of the modeled hazard grid. A finer grid does not by
> itself mean higher model accuracy.

The hazard grid size is not the accuracy of a building-level estimate and it is not a
"municipality-wide resolution": the point assessment reads one cell; the supplied value is one
exposure.

## 5. Five-hazard coverage — generated, not typed

`coverage.coverage_table()` builds the table from `models.READINESS` (the three core hazards)
and `coverage.EXTENDED_HAZARDS` (drought, sea-level rise); the UI, the *Hazard Coverage* Excel
sheet and the "What this tool covers today" lines are all that one function.

| Hazard | Hazard data | Financial loss | Risk level | Resolution | Status |
|---|---|---|---|---|---|
| Flood | Available | Available | Available | 150 arcsec (~4–5 km) | READY |
| Tropical Cyclone | Available | Available | Available | 150 arcsec (~4–5 km) | READY |
| Heatwave | Available | Not available | Not available | 0.05 degree (~4–6 km) | HAZARD_ONLY |
| Drought | Not connected | Not available | Not available | N/A | NOT_READY |
| Sea-level rise | Legacy / static only | Not available | Not available | N/A | NOT_READY |

"Why is this unavailable?" (served as `display.why_unavailable`):

* **Heatwave** — Hazard data is available, but no applicable CLIMADA Impact Function is
  currently available for financial loss calculation.
* **Drought** — A legacy drought result exists, but the current validated financial-risk
  pipeline has not been implemented for this hazard.
* **Sea-level rise** — The current implementation is configuration-based rather than a spatial
  CLIMADA hazard-loss calculation.

### 5.1 Drought — investigated by import / instantiate / inspect (2026-09-27)

| checked | result |
|---|---|
| `climada_petals.entity.impact_funcs.drought.ImpfDrought.from_default()` | instantiates: `haz_type=DR`, intensity `[-6.5, -4, -1, 0]` (SPEI), `mdd = paa = [1, 1, 0, 0]`, unit `"NA"` — a step function on a drought index, not a damage curve for a priced real-estate exposure |
| `climada_petals.hazard.drought.Drought()` | instantiates; reads the global SPEIbase `spei06.nc` (`SPEI_FILE_URL` digital.csic.es, 0.5°); default extent lat 44.5–50 / lon 5–12 (Europe demo) |
| CLIMADA Data API type list (live) | no drought / SPEI hazard type |
| this repository | `perils.json` drought = legacy indicative SPEI ramp ("productivity"), `requires_ingest`, **no ingester, no catalog entry**; no Open-Meteo / ERA5 client exists (contrary to the brief's assumption — recorded here rather than copied) |

Verdict: **NOT_READY**. A drought hazard would need a Korean SPEI product (KMA-derived or
SPEIbase) *and* an impact function whose exposure is a priced asset; neither exists, and none
is authored. KMA 500 m TA/RN grids could feed an SPEI computation, but "KMA data → new drought
formula" is out of scope by rule.

### 5.2 Sea-level rise — investigated the same way

| checked | result |
|---|---|
| `climada.hazard` modules | no sea-level-rise hazard class |
| `climada_petals.hazard.coastal_flood.CoastalFlood.from_aqueduct_tif` | signature `(rcp, target_year, return_periods, subsidence='wtsub', percentile='95', countries, boundaries)`: Aqueduct coastal inundation, RCP 4.5/8.5, 2030/2050/2080 — a *coastal flood* hazard, not connected to the validated chain |
| `TCSurgeBathtub.from_tc_winds(..., add_sea_level_rise=…)` | the legacy `sea_level_rise_m` option (`physical.py _run_tc_surge`): a static offset on the bathtub surge height |
| Data API | no coastal-flood type served (the client config names `aqueduct_coastal_flood`; the server type list does not include it) |

Verdict: **NOT_READY** — configuration-based, no spatial hazard grid, no events/frequencies.
A future phase could connect `CoastalFlood` with the JRC coastal depth-damage function, with
its own licence and validation evidence.

### 5.3 KMA 500 m — investigated (2026-09-27)

| | |
|---|---|
| portal checked | 기후정보포털 data download (`climate.go.kr/home/CCS/contents_2021/35_download1_ssp.php`), file list queried for 남한상세 |
| offered | SSP1-2.6 / 2-4.5 / 3-7.0 / 5-8.5, model `5ENSMN`, elements TA · TAMAX · TAMIN · RN · RHM · WS, daily / monthly / yearly, 2021–2100 by decade, NetCDF / ASCII; and MK-PRISM v2.1 observations 2000–2019 |
| grid of the SSP files (read from the archives in `~/climada/data/kma`) | **0.01° (601 × 751, ~1 km)** — `kma_scenario.GRID_RES_DEG` |
| grid of the MK-PRISM v3.1 observations | **0.005° (1201 × 1501, ~500 m)**, whose even nodes coincide with the 0.01° grid |
| licence / access | KMA 국가 기후변화 표준 시나리오, free registration; cite KMA |
| what the platform stores | 0.05° layers (block-averaged) — a `KOREA_LOCAL` heat row is ~5 km |

Finding: **there is no 500 m climate-change (SSP) product.** The only ~500 m KMA grid is the
observational climatology (MK-PRISM, 2000–2019). So:

```text
CURRENT           KMA 남한상세 → 0.05° catalog layer (heatwave TAMAX p95)   — HAZARD_ONLY
FUTURE CANDIDATE  1 km SSP grids (0.01°) at native resolution; 500 m only for the observed period
```

Neither candidate is connected in V0.2, and connecting one would not change financial
availability: no CLIMADA heat impact function exists, so heat stays hazard-only. A finer grid
would not be reported as "more accurate".

## 6. Product surface

* **UI (Models tab)** — *Assessment Target* toggle (Municipalities default / My Assets), search,
  level filter, select all / clear, per-municipality optional USD value; a map of the selected
  representative points; after a run: warning banner, map coloured by the highest priced level
  (blue = hazard only, grey = screening), the municipality risk table (text levels — High /
  Medium / Low / Hazard only / No asset value / Unavailable), map ⇄ table synchronisation
  (click a point → row + detail; click a row → point highlighted), a detail panel per hazard
  with intensity, status, money when priced, "Financial assessment: Not available — asset value
  not supplied" otherwise, the hazard grid resolution and "Why?". The header lists the five
  coverage lines, the coverage table and the resolution note; drought and sea-level rise appear
  as disabled hazards with their reason.
* **API** — `GET /api/libraries/municipalities[?level=]`; `POST /api/session/{id}/physical-risk-models`
  with `assessment_target = "MUNICIPALITY"`, `municipality_ids`, optional `asset_values`
  (id → positive USD; validated); the existing table, progress, export and history routes.
  `GET /api/libraries/physical-risk-models` now also carries `coverage_table`, `coverage_lines`,
  `why_unavailable`, `resolution`, `resolution_note` and the `municipality` copy + dataset summary.
* **Excel** — `Municipality_Physical_Risk_Report_<N>_Municipalities_<YYYYMMDD>.xlsx` with, in
  order, *Municipality Summary*, *Municipality Risk Matrix*, *Hazard Results*, *Spatial
  Resolution*, *Hazard Coverage*, (*Global vs Country* / *Global vs Korea Local* only when a
  Global scope was actually run, *Climate Change* only with a baseline), *Methodology*, *Run Info*.
  Empty cells are unpriced fields; a computed zero is `0`.
* **CLI** — `scripts/municipality_physical_risk.py` (`municipalities.csv` with `municipality_id`
  or `municipality_name` and optional `asset_value_usd` / `property_type`; or `--ids`, `--level`,
  `--all`), same hazard/model words as the asset batch, same engine, same workbook.
* **Batch** — one adapter load per (hazard × scope), every point priced against it; 269 points
  × 3 hazards × 2 scopes reuse the cached datasets.

## 7. Not done in V0.2, on purpose

* No municipality polygon-wide aggregation (V0.3+ candidate: polygon → all cells → exposure
  aggregation → municipality-wide EAL; the point assessment will keep its own name).
* No Korea-specific impact function; no heat, drought or sea-level-rise financial function.
* No city-hall values, population-as-value, or assumed building values.
* No TC surge / rainfall, no 1 km conversion, no 500 m KMA integration.
* No change to the frozen asset baseline: `v0.1.0-physical-risk-baseline` and
  `v0.1.1-physical-risk-final` are untouched; the asset workbook keeps its sheets; the
  Phase 7 fixture is unchanged and still reproduced.

## 8. Tests

`tests/test_municipality_physical_risk.py` (backend, CLIMADA-free): dataset validity, duplicate /
out-of-Korea / unknown-level detection, order-preserving lookup, no invented value, verbatim
warning, request builder, generated coverage table (and that it follows a changed readiness
table), coverage lines, resolution registry and km classes, extended-hazard evidence, untouched
core readiness, screening vs computed-zero rows, municipality summary / matrix / resolution
frames, municipality workbook sheets and blank cells, filenames by target, facility workbook
unchanged, API validation and the municipalities route.

`tests/test_municipality_worker.py` (worker): grid-spacing measurement, screening-then-pricing
of the same point with the same function, runner tagging and adapter resolution records, and
measured resolution of the cached RF / TC / KMA layers equal to the registry (skipped without
the cache).

## 9. V0.3 official office points — investigation (2026-09-27), blocked

V0.3 asked for a second spatial anchor, `OFFICIAL_OFFICE_POINT` (the 시청 / 군청 / 구청
location), beside the V0.2 `BOUNDARY_INTERIOR_POINT`. The brief required an authoritative,
accessible, inspectable source and forbade substitute coordinates. None was obtainable without
an action only the user can take, so **no office point was created and V0.2 is unchanged.**

| source | holds | access | usable now |
|---|---|---|---|
| 행정안전부 *도로명주소 민원행정기관 전자지도* (data.go.kr 15050409) | point layer of 자치단체 (시도, 시군구, 읍면동) and other offices; 공공누리 제1유형 | application on juso.go.kr with 본인인증 and a 행안부/자치단체 purpose review (도로명주소법 시행령 제46조) | **no** — user must apply |
| 국토지리정보원 *국가관심지점정보(POI) 시도별* (data.go.kr 15144087 → map.ngii.go.kr POI 내려받기) | national POI set (~6.8 M points) built from public-administration source DBs, per 시도, xlsx list or shapefile; 공공누리 제1유형 | free 국토정보플랫폼 **login** (the page also flags a user-authentication step); no purpose review; the format definition downloads without login | **no** — user must sign in (second pass, 2026-09-27) |
| 행정안전부 *도로명주소 위치정보 요약DB* (15050410) | address + main-entrance X/Y for every building | same juso.go.kr application | **no** — user must apply |
| 행정안전부 *도로명주소 전자지도* (15050413) | buildings, entrances, boundaries | same application, approval by the competent authority | **no** — user must apply |
| 행정안전부 *실시간 주소별 좌표정보 조회* (15056663) | X/Y for a structured road-name address | juso.go.kr 승인키 (instant issue); still needs a nationwide office address list | **no** — separate key; storage terms not verified |
| 국토교통부 *지오코더 API* (15101106) | address → coordinate | data.go.kr key, but the terms state results may not be stored in a separate store or database | **no** — licence forbids a stored dataset |
| 행정안전부 *행정표준코드 기관코드* (15077870) | agency code, name, rank, type, 소재지코드 | open | **no** — no address, no coordinates |
| 행정안전부 *지방재정365 청사면적* (15138718) | office floor area | open | **no** — no location |
| regional lists (경상남도 시군구청 정보 15062784, 경상북도 도청·시군청 정보 15044825, 경기도 청사및출장소 15057551) | office name and road address | open | **no** — addresses only, three provinces only |

To unblock, one of these is needed from the user:

1. **Lightest:** sign in to 국토정보플랫폼 (map.ngii.go.kr → 공간정보 → POI 내려받기) and download
   the 시도 files, or sign in inside the app's browser pane and let the agent download them with
   your approval. The 시청/군청/구청 points would be selected by the POI classification and joined
   to the SGIS codes; the portal's "최종 업데이트 2021-12-15" label (data.go.kr says 2025-01-27)
   must be checked against the files before use.
2. **Most authoritative:** apply on juso.go.kr (menu DT04) for the *민원행정기관 전자지도* and place the
   downloaded point files under `~/climada/data/municipality_src/`. Office rows can then be added
   as `point_type = OFFICE_LOCATION` with `office_name` / `address` filled, joined to the SGIS
   codes, without a schema change; the representative points stay as they are.
3. Apply for the *위치정보 요약DB* and supply an authoritative nationwide office address list
   (none exists in open data today) to join against it.

Office-point assessment, the office ⇄ representative comparison sheet and the office UI were
therefore not built.

### 9.1 Third pass — 국토교통부 검색 API (data.go.kr 15058799), 2026-09-27

| item | finding |
|---|---|
| service | LINK entry on data.go.kr; served by 브이월드 (국토교통부 / 공간정보산업진흥원) |
| endpoint | `https://api.vworld.kr/req/search?service=search&request=search&version=2.0&type=place&query=…&format=json&crs=EPSG:4326&key=…` (optional `category`, `bbox`, `size` ≤ 1000, `page`) |
| key | 브이월드 인증키 required — tested: no key → `PARAM_REQUIRED`, dummy key → `INVALID_KEY` ("등록되지 않은 인증키입니다"). Key issuance is a 브이월드 member menu (로그인필요); the data.go.kr "자동승인" does not issue it |
| place fields (documented) | `id`, `title`, `category` (장소분류코드; list file `브이월드_장소분류코드_20240712.xlsx`), `address.road`, `address.parcel`, `point.x`, `point.y` (EPSG:4326 default). No 행정구역 code in place results |
| storage terms | 브이월드 이용약관 제19조 (저작권), in force since 2023-08-18: "오픈플랫폼에서 제공되는 데이터는 사전 승낙 없이 데이터를 무단으로 저장하지 못합니다"; information obtained may not be copied or provided to others without prior consent; 제10조-type conduct rules forbid profit-making use without the operator's consent. The data.go.kr "이용허락범위 제한 없음" label is contradicted by the operator's own terms |
| live tests (서울특별시청 … 전주시청, 광주시청 vs 광주광역시청, 시청/군청/구청, repeatability) | **not run** — no 브이월드 key is available to the agent |

Decision: **blocked** — (1) no key, so no response has been inspected; (2) even with a key,
storing the returned office coordinates in `korea_municipalities.csv` requires the operator's
prior written consent under 제19조, and commercial use requires consent. A key alone would
allow the ambiguity/category/repeatability tests, not a stored office layer.

### 9.2 Fourth pass — local-government office datasets (data.go.kr), 2026-09-27

~120 local-government office / agency datasets were located across all 17 시도 (search on
청사및출장소, 청사 현황, 행정기관 현황, 관공서 현황/위치, 시군구청, plus a per-province sweep),
and every downloadable one was opened. Most carry office name and address only; coordinate-bearing
ones are published per 시군구, not per province. Evidence tables:
[`evidence/v03_office_coverage_matrix.csv`](evidence/v03_office_coverage_matrix.csv) (269 units),
[`evidence/v03_office_source_mapping.csv`](evidence/v03_office_source_mapping.csv) (audited
source → SGIS mapping, point-in-polygon check),
[`evidence/v03_admin_vintage_changes.csv`](evidence/v03_admin_vintage_changes.csv).

| classification | units |
|---|---|
| OFFICIAL_COORDINATE_AVAILABLE | 16 (부산 6, 경기 2, 경남 2, 대구 1, 대전 1, 강원 1, 전북 1, 전남 1, 경북 1); all 16 points fall inside their SGIS polygon |
| SOURCE_NOT_AVAILABLE | 56 — 경기 43 (province-wide 청사및출장소 현황 with WGS84 exists; data.gg.go.kr / openapi.gg.go.kr refuse this machine: "보안 정책에 의해 차단"), 부산 11 and 세종 2 (coordinate APIs exist on apis.data.go.kr; the project key is not activated for them) |
| ADMINISTRATIVE_UNIT_CHANGED | 5 — 광주광역시, 전라남도 (→ 전남광주통합특별시), 인천 중구 / 동구 / 서구 (→ 제물포구 / 영종구 / 서해구) |
| OFFICIAL_COORDINATE_NOT_FOUND | 192 — including every unit in 서울, 울산, 광주 구, 인천 (other), 충북, 충남, 제주, and all 17 시도청 |
| AMBIGUOUS | 0 (양산 has 본청 / 2청사; the row labelled 본청 is used) |

Excluded: 부산광역시_구군 행정기관현황(SHP) (15084225) — despite the title its content is
공개공지 (public open space) points, not agency locations.

Decision: **C. BLOCKED** at 16 / 269 (5.9 %). Unlocking the three reachable-but-refused sources
would give at most 72 / 269 (26.8 %), still with no coordinates for seven 시도 and for any
시도청 row.

### 9.3 Fifth pass — official office address + existing project geocoder, 2026-09-27

Source model tested: `OFFICE_SOURCE` = government office name + address; `COORDINATE_SOURCE` =
the project's own address → coordinate geocoder, `coordinate_method = ADDRESS_GEOCODING`.

**Existing geocoder: not found.** No address geocoder, geocoding audit or geocoder key exists in
this repository, in `project-Ragnarok`, in `Agrivoltaic-Feasibility` (its V-World calls are
land-register / parcel-geometry endpoints — NED `ladfrlList`, `getLandCharacteristics`,
`req/data LP_PA_CBND_BUBUN` — not address geocoding) or in this machine's environment. The only
geocoding in this repo is the Map tab's interactive OSM Nominatim place search. The described
V5 audit (~5 m median error, 읍면동 fallback rejected) is not present on this machine; the
Agrivoltaic scripts point at `C:\Users\user\새 폴더\pipeline_out`, i.e. the previous Windows PC.
If that geocoder was V-World's, its results may not be stored (data.go.kr 15101106: "별도의
저장장치나 데이터베이스에 저장할 수 없습니다"; V-World 이용약관 제19조).

**Official office addresses** ([`evidence/v03_office_address_matrix.csv`](evidence/v03_office_address_matrix.csv)),
matched by exact office name **and** an address naming the unit, one row per unit:

| status | units |
|---|---|
| OFFICIAL_ADDRESS_AVAILABLE | 107 (경북 22, 경남 22, 충북 15, 경기 12, 인천 8, 대구 8, 부산 7, 대전 4, 전북 4, 강원 3, 충남 1, 전남 1) |
| AMBIGUOUS_OFFICE | 3 — 대구광역시청 (동인 / 산격청사), 남양주시청 (제1 / 제2청사), 통영시청 (two different addresses in two sources) |
| ADMINISTRATIVE_VINTAGE_MISMATCH | 5 — 광주광역시, 전라남도, 인천 중구 / 동구 / 서구 |
| SOURCE_NOT_AVAILABLE | 154 — incl. all of 서울 (26), 울산, 광주 구, 세종, 제주, most of 경기 (portal blocked), 부산 (API not activated), 충남, 전남, 강원 |
| GEOCODE_SUCCESS / GEOCODE_FAILED | 0 / 0 — no geocoder to run |

16 of the 107 also have government-published coordinates (§9.2), all inside their SGIS polygon.
Kept unchanged from the source: "층창북도" (상당구청) and "충청북도 충청북도" (충북도청) typos;
옹진군청's address lies in 미추홀구 (the county office is on the mainland) — its point would
legitimately fall outside the 옹진군 polygon.

Decision: **B — address layer partial (107 / 269), geocoder not available** (absent from this
machine; if it is V-World's, its terms forbid a stored layer). No coordinates were generated.

Boundary currency: data.go.kr now lists providers such as 전남광주통합특별시 and 인천광역시
영종구 / 서해구, i.e. administrative reorganisations after the SGIS `2025_2Q` boundaries used
for the V0.2 points. Office points and representative points must come from the same
administrative vintage; the V0.2 dataset should be rebuilt from a post-reorganisation SGIS
release when one is published. Re-checked 2026-09-27: the newest open SGIS release is still
`2025_2Q` ("2025년 기준 경계"), while data.go.kr providers already use 인천광역시 제물포구 /
영종구 / 서해구 and 전남광주통합특별시. 40 of the 269 V0.2 rows are in the affected areas
(광주 6, 전남 23, 인천 11). Any office layer must be joined through an explicit
old-code → new-code mapping that reports unmatched, new and renamed units. Drought and sea-level rise stay `NOT_READY`; KMA 500 m stays a documented
candidate.
