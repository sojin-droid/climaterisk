# 1,000 real OSM buildings — spread over the hazard grid, with the portfolio loss curve

Written 2026-10-14; spec 2.1 (floors-known buildings only) 2026-10-15; spec 2.2 (one building
of a class per hazard cell) 2026-10-16. The portfolio exists to read the portfolio /
asset-class / 시도 loss curves (Portfolio Loss Curve sheet) on a sample in which every asset
class has hundreds of assets. It uses neither the 5-asset
[validation fixture](../tests/fixtures/asset_level_validation/validation_portfolio.csv) (golden
regression cases A–E — hand-made points, not real buildings) nor the upload-template examples.

**Buildings are real, values are not.** Every asset is an existing OpenStreetMap building
with a use tag **and a floor count** — its location, footprint, use and number of floors are
OSM's, and each row carries its `osm_type` / `osm_id`, so it can be looked up at
`https://www.openstreetmap.org/<osm_type>/<osm_id>`. Its `asset_value_usd` is an
assumption: footprint × floors × a class unit value. Read the money as a method test, never
as a valuation.

## 1. Construction

| item | value |
|---|---|
| file | [`tests/fixtures/sample_portfolio_1000/portfolio.csv`](../tests/fixtures/sample_portfolio_1000/portfolio.csv) (+ [README](../tests/fixtures/sample_portfolio_1000/README.md), ODbL; [`build_env.json`](../tests/fixtures/sample_portfolio_1000/build_env.json)) |
| builder | [`scripts/build_sample_portfolio.py`](../scripts/build_sample_portfolio.py) (worker env: geopandas, pyogrio + GDAL OSM driver, CLIMADA for the centroid match) |
| spec | [`assets/libraries/sample_portfolio_spec.json`](../assets/libraries/sample_portfolio_spec.json) v2.2.0 — seed `20261014`, `require_levels_tag: true`, `max_per_cell_per_class: 1`; no number in the script |
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
| value | footprint (m², EPSG:5179) × floors × unit value: commercial $2,000 /m², residential $1,500 /m², industrial $900 /m²; clipped to $0.1 M–$500 M (35 rows at the floor, none at the cap), rounded to $10 k. Total **$14.19 B** (commercial $5.05 B, residential $5.49 B, industrial $3.64 B) |
| determinism | same spec + extract + boundaries + library build + reference hazard ⇒ byte-identical CSV (md5 `d9a682dc…`, rebuilt and compared; GDAL 3.10.3, GEOS 3.14.1, PROJ 9.7.1 recorded in `build_env.json`). See the note below |

Where the sample lands: 경기 270, 경남 137, 서울 84, 부산 53, 충남 51, 강원 49 … 대전 16.
The one-per-cell rule moves weight out of the dense cores (서울 158 → 84 against spec 2.1)
into the rest of the country. Floor tags are still densest on apartment complexes, so
residential is mostly high-rise apartments. Industrial is the thin pool: 190 of the 192
cells that hold a floor-tagged industrial building are used, so the industrial class is
close to a census of those cells, not a random sample of Korean industry.

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

## 2. Run (2026-10-16, Windows, spec 2.2)

Recommended data scope = Korea country dataset (Data API) for both perils; 2,000 rows, all
`FULL`. Datasets: `river_flood_150arcsec_rcp85_KOR_2030_2050` v3 (480 events) and
`tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040` v2.1. All 42 curves (2 perils ×
portfolio, 3 classes, 17 시도) are in
[`docs/evidence/sample1000_loss_curves.csv`](evidence/sample1000_loss_curves.csv).

### 2.1 River flood

| group | assets | with modeled flood | value | EAL | EAL / value | 10-yr | 100-yr | 250-yr | Σ standalone 100-yr | diversification (100-yr) |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | **339** (33.9 %) | $14,192 M | $23.50 M | 0.166 % | $39.7 M | $394.5 M | $619.0 M | $613.9 M | 35.7 % |
| Commercial | 405 | 134 (33.1 %) | $5,053 M | $6.85 M | 0.135 % | $10.4 M | $114.8 M | $176.2 M | $181.6 M | 36.8 % |
| Industrial | 190 | 69 (36.3 %) | $3,645 M | $9.35 M | 0.256 % | $1.8 M | $201.3 M | $307.8 M | $201.4 M | **0.05 %** |
| Residential | 405 | 136 (33.6 %) | $5,495 M | $7.30 M | 0.133 % | $13.1 M | $167.1 M | $199.6 M | $230.9 M | 27.7 % |

With buildings spread over the grid, each class floods at about the same rate (33–36 %):
the earlier gap (commercial 44 %, industrial 21 %) came from where clusters happened to sit
— 90 commercial buildings in 서울 cells, 75 industrial buildings in one dry 평택 cell.

- **One building is the industrial class.** A 10-storey industrial building in 평택 (OSM
  way 893651036; footprint 42,093 m², value $378.8 M, peak modelled depth 4.3 m) carries
  $7.92 M of flood EAL — **85 % of the industrial EAL and 34 % of the portfolio's**. The
  industrial 100-yr loss ($201.3 M) is almost exactly the sum of standalone 100-yr losses
  ($201.4 M), i.e. no diversification: the class curve is that building's curve. Read the
  industrial line as one site, not as "industrial vulnerability".
- **Spreading raises the portfolio tail.** Portfolio EAL is 2.5× spec 2.1's ($23.5 M vs
  $9.45 M) and the 100-yr loss 3.8× ($394.5 M vs $105.0 M), with diversification falling
  from 45.8 % to 35.7 %. The 평택 building above alone accounts for a third of the EAL;
  the sample changed, not the method.
- **Frequent losses are super-additive.** No single building floods often enough to have a
  10-yr loss (Σ standalone 10-yr = 0), yet the portfolio does ($39.7 M): across 1,000
  buildings, one cell or another floods often enough. Diversification is only defined at return
  periods where the standalone sum is positive.

### 2.2 Tropical cyclone

| group | assets | with modeled wind | EAL | EAL / value | 100-yr | Σ standalone 100-yr | diversification |
|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | 1,000 | $1.30 M | 0.009 % | $14.6 M | $32.8 M | 55.4 % |
| Commercial | 405 | 405 | $422 k | 0.008 % | $4.2 M | $10.7 M | 60.6 % |
| Industrial | 190 | 190 | $246 k | 0.007 % | $2.9 M | $6.6 M | 56.6 % |
| Residential | 405 | 405 | $633 k | 0.012 % | $8.0 M | $15.5 M | 48.3 % |

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
(v2.0, v2.1, v2.2), so it is a property of the dataset, not of one sample — the evidence for
adding a coastal-flood data scope to the product model next.

## 4. Caveats

- Values are assumptions (footprint × floors × unit value); never a market or portfolio
  estimate. OSM footprints and floor tags are volunteer data with uneven coverage.
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
