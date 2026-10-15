# 1,000 real OSM buildings — balanced asset classes and the portfolio loss curve

Written 2026-10-14; spec 2.1 (floors-known buildings only) 2026-10-15. The portfolio exists
to read the portfolio / asset-class / 시도 loss curves (Portfolio Loss Curve sheet) on a
sample in which every asset class has hundreds of assets. It uses neither the 5-asset
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
| builder | [`scripts/build_sample_portfolio.py`](../scripts/build_sample_portfolio.py) (worker env: geopandas, pyogrio + GDAL OSM driver) |
| spec | [`assets/libraries/sample_portfolio_spec.json`](../assets/libraries/sample_portfolio_spec.json) v2.1.0 — seed `20261014`, `require_levels_tag: true`; no number in the script |
| OSM extract | Geofabrik `south-korea-261013.osm.pbf` (data as of 2026-10-13, 289,218,262 bytes, md5 `08b0d3fd8b2fe3ee8e18be02522c88e6`) in `<DATA_ROOT>/external/osm/` — not in the repository. © OpenStreetMap contributors, ODbL 1.0 |
| eligible buildings | OSM `multipolygons` with one of the spec's `building=*` tags, a usable `building:levels` (a positive whole number ≤ 120) and an interior point inside an SGIS 2025_2Q 시군구: residential 151,100 · commercial 13,709 · **industrial 1,837**. `building=yes` (use unknown) and buildings without a floor count are never used |
| classes | commercial **334** (tags commercial 270, retail 33, office 20, hotel 10, supermarket 1) · residential **333** (apartments 255, house 58, residential 15, detached 5) · industrial **333** (industrial 301, warehouse 31, manufacture 1) |
| class key | the OSM tag → a `property_type` → the JRC sector the engine uses (`property_type_to_jrc_sector`) — the impact-function family; tested, so a class is never a second taxonomy |
| draw | per class, uniformly at random without replacement over the eligible buildings (seeded); then shuffled |
| location | the footprint's interior point (`representative_point`, EPSG:5179) → WGS84; 시도 / 시군구 from the SGIS polygon that contains it — 143 시군구 in all 17 시도 |
| floors | OSM `building:levels` for **all 1,000** rows (no class default). Median: commercial 4 (max 53), residential 15 (max 42), industrial 2 (max 22) |
| value | footprint (m², EPSG:5179) × floors × unit value: commercial $2,000 /m², residential $1,500 /m², industrial $900 /m²; clipped to $0.1 M–$500 M (53 rows at the floor, none at the cap), rounded to $10 k. Total **$11.91 B** (commercial $4.53 B, residential $4.36 B, industrial $3.01 B) |
| determinism | same spec + extract + boundaries + library build ⇒ byte-identical CSV (md5 `8f5e3b35…`; GDAL 3.10.3, GEOS 3.14.1, PROJ 9.7.1 recorded in `build_env.json`). See the note below |

Where the sample lands follows where OSM has buildings tagged with a use *and* floors, not
where Korea's building stock is: 경기 378, 서울 158, 경남 123, 광주 57, 부산 55, 충남 55,
인천 50 … 제주 4. Floor tags are densest on apartment complexes and city-centre buildings, so
residential is mostly high-rise apartments. Industrial is the thin pool: only 1,837
industrial buildings in Korea's OSM carry a floor count, so the 333 drawn are 18 % of all
of them — the industrial class is close to a census of that subset, not a random sample of
Korean industry.

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

## 2. Run (2026-10-15, Windows, spec 2.1)

Recommended data scope = Korea country dataset (Data API) for both perils; 2,000 rows, all
`FULL`. Datasets: `river_flood_150arcsec_rcp85_KOR_2030_2050` v3 (480 events) and
`tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040` v2.1. All 42 curves (2 perils ×
portfolio, 3 classes, 17 시도) are in
[`docs/evidence/sample1000_loss_curves.csv`](evidence/sample1000_loss_curves.csv).

### 2.1 River flood

| group | assets | with modeled flood | value | EAL | EAL / value | 10-yr | 100-yr | 250-yr | Σ standalone 100-yr | diversification (100-yr) |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | **322** (32.2 %) | $11,906 M | $9.45 M | 0.079 % | $22.9 M | $105.0 M | $601.4 M | $193.7 M | 45.8 % |
| Commercial | 334 | 148 (44.3 %) | $4,535 M | $2.98 M | 0.066 % | $1.3 M | $17.8 M | $450.9 M | $33.6 M | 47.0 % |
| Industrial | 333 | **70** (21.0 %) | $3,014 M | $0.46 M | 0.015 % | $0.3 M | $11.3 M | $14.6 M | $16.5 M | 31.8 % |
| Residential | 333 | 104 (31.2 %) | $4,357 M | $6.01 M | 0.138 % | $16.0 M | $80.4 M | $142.5 M | $143.6 M | 44.0 % |

Every class carries a flood loss. Two things drive the numbers more than the class does:

- **Concentration.** One 25-storey apartment complex in 경기 ($419 M, the largest asset)
  contributes $1.76 M — 19 % of the portfolio EAL. With floors observed, tall buildings carry
  large values, so losses are lumpy. The commercial curve jumps from $17.8 M at 100 yr to
  $451 M at 250 yr: the set has 480 events (return periods 1–480 yr), so the 250-yr point
  rests on its top two events — read it as an indication, not an estimate.
- **Where each class sits.** Commercial floods most often (148 of 334) because 90 of its
  buildings are in 서울, where 81 sit in flooded cells. Industrial floods least (70 of 333;
  EAL / value 0.015 %): 162 of its buildings are in 경기, of which only 30 flood, and of the
  105 in 경남 / 충남 / 인천 / 부산 only 5 do (§3).

### 2.2 Tropical cyclone

| group | assets | with modeled wind | EAL | EAL / value | 100-yr | Σ standalone 100-yr | diversification |
|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | 1,000 | $992 k | 0.008 % | $11.07 M | $25.51 M | 56.6 % |
| Commercial | 334 | 334 | $295 k | 0.007 % | $4.54 M | $7.97 M | 43.1 % |
| Industrial | 333 | 333 | $280 k | 0.009 % | $4.08 M | $6.96 M | 41.4 % |
| Residential | 333 | 333 | $417 k | 0.010 % | $5.02 M | $10.58 M | 52.6 % |

Wind reaches every asset and one regional function (North West Pacific, Eberenz 2021)
applies to all classes, so EAL ÷ value differs only by where each class's buildings are.

## 3. Coverage by 시도 — what the river-flood set does not see

Assets with a non-zero river-flood cell, of those in each 시도:

| 시도 | assets | with flood | 시도 | assets | with flood |
|---|---|---|---|---|---|
| 서울특별시 | 158 | 136 | 경기도 | 378 | 95 |
| 광주광역시 | 57 | 25 | 대구광역시 | 25 | 16 |
| 경상남도 | 123 | 13 | 대전광역시 | 10 | 9 |
| 세종특별자치시 | 11 | 8 | 경상북도 | 10 | 5 |
| 강원특별자치도 | 17 | 3 | 전북특별자치도 | 14 | 3 |
| 충청북도 | 18 | 3 | 전라남도 | 7 | 3 |
| **인천광역시** | **50** | **2** | **부산광역시** | **55** | **1** |
| **충청남도** | **55** | **0** | **울산광역시** | **8** | **0** |
| **제주특별자치도** | **4** | **0** | | | |

The coastal 시도 are almost invisible to the river-flood set: 부산 1 / 55, 인천 2 / 50,
충남 0 / 55, 울산 0 / 8, 제주 0 / 4. Their flood exposure is coastal, storm-surge and
pluvial — none of which the ISIMIP river-flood product carries (GAP_ANALYSIS G7; SCOPE
`tc_surge`, `coastal_flood`). These zeros are coverage gaps, and the sheet says so per line
("Assets with Modeled Hazard"). The same pattern held on the v2.0.0 sample (인천 3 / 45,
부산 2 / 27), so it is a property of the dataset, not of one draw — the evidence for adding a
coastal-flood data scope to the product model next.

## 4. Caveats

- Values are assumptions (footprint × floors × unit value); never a market or portfolio
  estimate. OSM footprints and floor tags are volunteer data with uneven coverage.
- Requiring a floor tag biases the sample towards tagged areas and tall buildings
  (residential median 15 floors). It trades representativeness for a value that rests on an
  observed floor count rather than a class default.
- Tier: river flood and tropical cyclone are SCOPE tier **I** (indicative) — see
  [SCOPE.md](SCOPE.md). No domestic loss validation backs these figures.
- 150-arcsec (~4.6 km) grids, nearest-centroid reading: buildings in one cell share its
  depth; small rivers, levees and urban drainage are not represented.
- Losses are in valuation-year values (no exposure growth applied).
