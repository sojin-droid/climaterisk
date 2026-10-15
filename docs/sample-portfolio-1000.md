# 1,000 real OSM buildings — spread over the hazard grid, with the portfolio loss curve

Written 2026-10-14; spec 2.1 (floors-known buildings only) 2026-10-15; spec 2.2 (one building
of a class per hazard cell) and spec 2.3 (official building costs, no value bounds) 2026-10-16. The portfolio exists to read the portfolio /
asset-class / 시도 loss curves (Portfolio Loss Curve sheet) on a sample in which every asset
class has hundreds of assets. It uses neither the 5-asset
[validation fixture](../tests/fixtures/asset_level_validation/validation_portfolio.csv) (golden
regression cases A–E — hand-made points, not real buildings) nor the upload-template examples.

**Buildings are real; values are a cited cost proxy, not valuations.** Every asset is an
existing OpenStreetMap building with a use tag **and a floor count** — its location,
footprint, use and number of floors are OSM's, and each row carries its `osm_type` /
`osm_id`, so it can be looked up at `https://www.openstreetmap.org/<osm_type>/<osm_id>`. Its
`asset_value_usd` is floor area (footprint × floors) × the official new-build cost per m² for
its use (§1, *value*). That is a replacement-cost proxy, not what the building is worth.

## 1. Construction

| item | value |
|---|---|
| file | [`tests/fixtures/sample_portfolio_1000/portfolio.csv`](../tests/fixtures/sample_portfolio_1000/portfolio.csv) (+ [README](../tests/fixtures/sample_portfolio_1000/README.md), ODbL; [`build_env.json`](../tests/fixtures/sample_portfolio_1000/build_env.json)) |
| builder | [`scripts/build_sample_portfolio.py`](../scripts/build_sample_portfolio.py) (worker env: geopandas, pyogrio + GDAL OSM driver, CLIMADA for the centroid match) |
| spec | [`assets/libraries/sample_portfolio_spec.json`](../assets/libraries/sample_portfolio_spec.json) v2.3.0 — seed `20261014`, `require_levels_tag: true`, `max_per_cell_per_class: 1`, building costs and exchange rate with their sources; no number in the script |
| OSM extract | Geofabrik `south-korea-261013.osm.pbf` (data as of 2026-10-13, 289,218,262 bytes, md5 `08b0d3fd8b2fe3ee8e18be02522c88e6`) in `<DATA_ROOT>/external/osm/` — not in the repository. © OpenStreetMap contributors, ODbL 1.0 |
| eligible buildings | OSM `multipolygons` with one of the spec's `building=*` tags, a usable `building:levels` (a positive whole number ≤ 120), an interior point inside an SGIS 2025_2Q 시군구 and a hazard centroid within CLIMADA's 100 km matching threshold: commercial 13,709 in 507 cells · residential 151,049 in 828 cells · **industrial 1,837 in 192 cells**. `building=yes` (use unknown) and buildings without a floor count are never used |
| hazard cell | the centroid of `river_flood_150arcsec_rcp85_KOR_2030_2050` v3 the building is read from, matched with CLIMADA's `match_coordinates` exactly as `Exposures.assign_centroids` does (the tropical-cyclone set uses the same 5,708-centroid grid). Written as `hazard_centroid`; a test re-derives it with CLIMADA for every row |
| classes | commercial **405** (tags commercial 326, retail 44, office 19, hotel 15, supermarket 1) · residential **405** (apartments 331, house 46, residential 17, detached 8, dormitory 3) · industrial **190** (industrial 127, warehouse 62, manufacture 1) |
| why not 3 : 3 : 3 | floor-tagged industrial buildings fall in only 192 cells, so one per cell cannot reach 333; 190 keeps the total at 1,000 |
| class key | the OSM tag → a `property_type` → the JRC sector the engine uses (`property_type_to_jrc_sector`) — the impact-function family; tested, so a class is never a second taxonomy |
| draw | per class, visit the eligible buildings in a seeded random order and accept one while its cell holds no building of that class yet, until the class count is reached; then shuffle. Busy cells are more likely to be picked (more buildings), but hold at most one per class |
| spread | 578 cells: 253 hold one building, 228 two, 97 three (one of each class at most). 201 시군구 in all 17 시도 |
| location | the footprint's interior point (`representative_point`, EPSG:5179) → WGS84; 시도 / 시군구 from the SGIS polygon that contains it |
| floors | OSM `building:levels` for **all 1,000** rows (no class default). Median: commercial 3 (max 28), residential 15 (max 49), industrial 2 (max 25) |
| value | floor area (footprint m², EPSG:5179 × floors) × **2026 건물신축가격기준액** for the use — 주거용 860,000, 상업용 860,000, 공업용 840,000 원/㎡ (행정안전부장관 고시, the per-m² new-build cost that 지방세 건축물 시가표준액 starts from) — ÷ **1,422.22 KRW/USD** (Bank of Korea ECOS 731Y004, 원/미국달러 매매기준율, 2025 average) = $604.69 /m² (residential, commercial), $590.63 /m² (industrial). To the whole dollar, **no lower or upper bound**. Total **$6.13 B** (industrial $2.39 B, residential $2.21 B, commercial $1.53 B); smallest $3,936 (a 6.7 m² warehouse), largest $248.6 M |
| value sources | costs: 여수시, 「2026년 건축물 시가표준액 산정기준」, Ⅲ-1 건물신축가격기준액 and 붙임1 신구대비표 ([PDF](https://yscms.yeosu.go.kr/contents/2422/value_260530_1.pdf), retrieved 2026-10-16), which reproduces the 행정안전부 figures; rate: [ECOS](https://ecos.bok.or.kr/) 「3.1.2.1. 주요국 통화의 대원화환율」 (retrieved 2026-10-16). Both are recorded in the spec |
| determinism | same spec + extract + boundaries + library build + reference hazard ⇒ byte-identical CSV (md5 `fb640d1f…`, rebuilt and compared; GDAL 3.10.3, GEOS 3.14.1, PROJ 9.7.1 recorded in `build_env.json`). See the note below |

Where the sample lands: 경기 270, 경남 137, 서울 84, 부산 53, 충남 51, 강원 49 … 대전 16.
The one-per-cell rule moves weight out of the dense cores (서울 158 → 84 against spec 2.1)
into the rest of the country. Floor tags are still densest on apartment complexes, so
residential is mostly high-rise apartments. Industrial is the thin pool: 190 of the 192
cells that hold a floor-tagged industrial building are used, so the industrial class is
close to a census of those cells, not a random sample of Korean industry.

**What the cost does and does not cover.** The 건물신축가격기준액 is new-build cost per m² of
floor area by broad use. The 시가표준액 then multiplies it by structure, detailed-use and
location indices and an age-based residual rate (경과연수별 잔가율). None of these are
applied here: OSM does not record structure or age, and depreciation is not what a flood
destroys. The three uses cost almost the same (840,000–860,000 원/㎡), so the class mix no
longer moves money between classes the way the earlier assumed prices ($2,000 / $1,500 /
$900) did. Industrial now carries the most value because its buildings have the largest
floor plates.

**Why the cell comes from the hazard grid, not from rounding.** The Korea Data API sets hold
5,708 of the 10,904 nodes of the 150-arcsec rectangle. Rounding coordinates to the grid
agreed with CLIMADA's assignment for 931 of 1,000 buildings; the other 69 sit where the
nearest node is missing (mostly the coast) and are read from a neighbouring centroid, so two
"different" rounded cells can share one centroid. The builder therefore matches against the
real centroids.

**Determinism across machines.** The CSV is byte-identical only on the recorded library
build. Rebuilding the earlier v2.0.0 sample (Mac-built) on Windows with the same spec,
extract and boundaries gave the same 830 of 1,000 buildings with identical fields; the
eligible pools differed by one building in commercial and one in industrial (67,203 vs
67,204; 54,816 vs 54,817), which is enough to shift a seeded draw. The rebuild test
(`test_rebuild_is_byte_identical`) therefore compares against `build_env.json` and skips,
naming the differing versions, on any other build.

```bash
./.climada-env/bin/python scripts/build_sample_portfolio.py
./.climada-env/bin/python scripts/physical_risk_batch.py tests/fixtures/sample_portfolio_1000/portfolio.csv --hazards flood,typhoon --models recommended --scenario rcp85 --year 2040 --country KOR --output sample1000.xlsx --json sample1000.json
```

## 2. Run (2026-10-16, Windows, spec 2.3)

Recommended data scope = Korea country dataset (Data API) for both perils; 2,000 rows, all
`FULL`. Datasets: `river_flood_150arcsec_rcp85_KOR_2030_2050` v3 (480 events) and
`tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040` v2.1. All 42 curves (2 perils ×
portfolio, 3 classes, 17 시도) are in
[`docs/evidence/sample1000_loss_curves.csv`](evidence/sample1000_loss_curves.csv).

### 2.1 River flood

| group | assets | with modeled flood | value | EAL | EAL / value | 10-yr | 100-yr | 250-yr | Σ standalone 100-yr | diversification (100-yr) |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | **339** (33.9 %) | $6,134 M | $11.15 M | 0.182 % | $15.6 M | $193.4 M | $309.6 M | $280.1 M | 31.0 % |
| Commercial | 405 | 134 (33.1 %) | $1,528 M | $2.07 M | 0.135 % | $3.2 M | $34.7 M | $53.3 M | $54.9 M | 36.8 % |
| Industrial | 190 | 69 (36.3 %) | $2,391 M | $6.13 M | 0.256 % | $1.2 M | $132.1 M | $202.0 M | $132.2 M | **0.06 %** |
| Residential | 405 | 136 (33.6 %) | $2,215 M | $2.94 M | 0.133 % | $5.3 M | $67.4 M | $80.5 M | $93.1 M | 27.7 % |

With buildings spread over the grid, each class floods at about the same rate (33–36 %):
the earlier gap (commercial 44 %, industrial 21 %) came from where clusters happened to sit
— 90 commercial buildings in 서울 cells, 75 industrial buildings in one dry 평택 cell.

- **The cost change moves money, not ratios.** EAL ÷ value per class is identical to the
  run with the earlier assumed prices (commercial 0.135 %, industrial 0.256 %, residential
  0.133 %): a building's EAL is proportional to its value, so a unit cost scales money and
  shifts weight between buildings, but never changes a building's loss rate.
- **One building is the industrial class — and almost half the portfolio.** A 10-storey
  industrial building in 평택 (OSM way 893651036; footprint 42,093 m², value $248.6 M, peak
  modelled depth 4.3 m) carries $5.20 M of flood EAL — **85 % of the industrial EAL and
  47 % of the portfolio's** (34 % under the earlier prices, which valued industrial floor
  area lowest). The industrial 100-yr loss ($132.1 M) equals the sum of standalone 100-yr
  losses ($132.2 M): no diversification, the class curve is that building's curve. Read the
  industrial line as one site, not as "industrial vulnerability". Commercial's largest
  contributor (수원 권선구) is 38 % of its class; residential's is 10 %.
- **Frequent losses are super-additive.** No single building floods often enough to have a
  10-yr loss (Σ standalone 10-yr = 0), yet the portfolio does ($15.6 M): across 1,000
  buildings, one cell or another floods often enough. Diversification is only defined at return
  periods where the standalone sum is positive.

### 2.2 Tropical cyclone

| group | assets | with modeled wind | EAL | EAL / value | 100-yr | Σ standalone 100-yr | diversification |
|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | 1,000 | $544 k | 0.009 % | $5.97 M | $13.81 M | 56.7 % |
| Commercial | 405 | 405 | $128 k | 0.008 % | $1.27 M | $3.22 M | 60.6 % |
| Industrial | 190 | 190 | $161 k | 0.007 % | $1.88 M | $4.33 M | 56.6 % |
| Residential | 405 | 405 | $255 k | 0.012 % | $3.23 M | $6.26 M | 48.3 % |

Wind reaches every asset and one regional function (North West Pacific, Eberenz 2021,
`v_half` 190.5 m/s) applies to all classes, so EAL ÷ value differs only by where each
class's buildings are. TC events are single storms, so the TC return-period losses are per
storm (occurrence), not per year.

## 3. Coverage by 시도 — what the river-flood set does not see

Assets with a non-zero river-flood cell, of those in each 시도:

| 시도 | assets | with flood | 시도 | assets | with flood |
|---|---|---|---|---|---|
| 서울특별시 | 84 | 69 | 경기도 | 270 | 80 |
| 대구광역시 | 47 | 30 | 광주광역시 | 34 | 22 |
| 세종특별자치시 | 24 | 18 | 경상북도 | 45 | 18 |
| 충청북도 | 28 | 16 | 경상남도 | 137 | 40 |
| 전라남도 | 32 | 11 | 강원특별자치도 | 49 | 10 |
| 전북특별자치도 | 29 | 8 | 대전광역시 | 16 | 5 |
| **부산광역시** | **53** | **7** | **인천광역시** | **45** | **3** |
| **충청남도** | **51** | **2** | **울산광역시** | **29** | **0** |
| **제주특별자치도** | **27** | **0** | | | |

The coastal 시도 stay almost invisible to the river-flood set even with one building per
cell: 부산 7 / 53, 인천 3 / 45, 충남 2 / 51, 울산 0 / 29, 제주 0 / 27. Their flood exposure
is coastal, storm-surge and pluvial — none of which the ISIMIP river-flood product carries
(GAP_ANALYSIS G7; SCOPE `tc_surge`, `coastal_flood`). These zeros are coverage gaps, and the
sheet says so per line ("Assets with Modeled Hazard"). The pattern held on all three draws
(v2.0, v2.1, v2.2 / 2.3), so it is a property of the dataset, not of one sample — the evidence for
adding a coastal-flood data scope to the product model next.

## 4. Caveats

- Values are floor area × an official average new-build cost per m² for three broad uses —
  no structure, detailed use, location or age adjustment, and no market value. OSM
  footprints and floor tags are volunteer data with uneven coverage; with no value bounds,
  an OSM footprint or floor-tag error passes straight into the value, so check the largest
  assets against their OSM ids before reading a class tail.
- Requiring a floor tag biases the sample towards tagged areas and tall buildings
  (residential median 15 floors). It trades representativeness for a value that rests on an
  observed floor count rather than a class default.
- One building per class per cell spreads the sample but does not make it a census: busy
  cells are still more likely to be chosen, and a single large asset can dominate a class
  (industrial, §2.1).
- Tier: river flood and tropical cyclone are SCOPE tier **I** (indicative) — see
  [SCOPE.md](SCOPE.md). No domestic loss validation backs these figures.
- 150-arcsec (~4.6 km × 3.7 km) grids, nearest-centroid reading: buildings in one cell share
  its depth and flooded fraction; small rivers, levees and urban drainage are not
  represented, and loss = value × damage(depth) × flooded fraction is a cell-average
  expectation, not the building's own outcome.
- Losses are in valuation-year values (no exposure growth applied).
