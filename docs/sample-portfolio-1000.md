# 1,000 real OSM buildings — balanced asset classes and the portfolio loss curve

Written 2026-10-14. The portfolio exists to read the portfolio / asset-class / 시도 loss
curves (Portfolio Loss Curve sheet) on a sample in which every asset class has hundreds of
assets. The 5-asset [validation fixture](../tests/fixtures/asset_level_validation/validation_portfolio.csv)
is a set of golden regression cases A–E with one industrial asset; it is not reshaped for this.

**Buildings are real, values are not.** Every asset is an existing OpenStreetMap building
with a use tag — its location, footprint, use and (when tagged) floor count are OSM's. Its
`asset_value_usd` is an assumption: footprint × floors × a class unit value. Read the money
as a method test, never as a valuation.

## 1. Construction

| item | value |
|---|---|
| file | [`tests/fixtures/sample_portfolio_1000/portfolio.csv`](../tests/fixtures/sample_portfolio_1000/portfolio.csv) (+ [README](../tests/fixtures/sample_portfolio_1000/README.md), ODbL) |
| builder | [`scripts/build_sample_portfolio.py`](../scripts/build_sample_portfolio.py) (worker env: geopandas, pyogrio + GDAL OSM driver) |
| spec | [`assets/libraries/sample_portfolio_spec.json`](../assets/libraries/sample_portfolio_spec.json) v2.0.0 — seed `20261014`; no number in the script |
| OSM extract | Geofabrik `south-korea-261013.osm.pbf` (data as of 2026-10-13, 289,218,262 bytes, md5 `08b0d3fd8b2fe3ee8e18be02522c88e6`) in `<DATA_ROOT>/external/osm/` — not in the repository. © OpenStreetMap contributors, ODbL 1.0 |
| eligible buildings | OSM `multipolygons` with one of the spec's `building=*` tags whose interior point lies inside an SGIS 2025_2Q 시군구: residential 336,271 · commercial 67,204 · industrial 54,817. `building=yes` (use unknown) is never used |
| classes | commercial **334** (tags commercial 300, retail 23, office 9, hotel 2) · residential **333** (apartments 148, house 104, residential 75, detached 4, dormitory 2) · industrial **333** (industrial 312, warehouse 20, factory 1) |
| class key | the OSM tag → a `property_type` → the JRC sector the engine uses (`property_type_to_jrc_sector`); tested, so a class is never a second taxonomy |
| draw | per class, uniformly at random without replacement over the eligible buildings (seeded); then shuffled |
| location | the footprint's interior point (`representative_point`, EPSG:5179) → WGS84; 시도 / 시군구 from the SGIS polygon that contains it — 173 시군구 in all 17 시도 |
| value | footprint (m², EPSG:5179) × floors × unit value: commercial $2,000 /m², residential $1,500 /m², industrial $900 /m²; floors = OSM `building:levels` when a positive number ≤ 120 (234 of 1,000 rows), else class default 4 / 3 / 1; clipped to $0.1 M–$500 M, rounded to $10 k. Total $3.44 B |
| determinism | same spec + extract + boundaries ⇒ byte-identical CSV (rebuilt twice: md5 `2348f7aa…`) |

Where the sample lands follows where OSM has *tagged* buildings, not where Korea's building
stock is: 경기 386, 서울 141, 광주 66, 경남 51, 인천 45 … 충북 14. Urban areas and areas with
building imports are over-represented; that is closer to a real-estate portfolio than a
uniform spatial draw, but it is not a census of buildings.

```bash
./.climada-env/bin/python scripts/build_sample_portfolio.py
./.climada-env/bin/python scripts/physical_risk_batch.py tests/fixtures/sample_portfolio_1000/portfolio.csv --hazards flood,typhoon --models recommended --scenario rcp85 --year 2040 --country KOR --output sample1000.xlsx --json sample1000.json
```

## 2. Run (2026-10-14, Mac mini)

Recommended data scope = Korea country dataset (Data API) for both perils; 2,000 rows, all
`FULL`. Datasets: `river_flood_150arcsec_rcp85_KOR_2030_2050` v3 (480 events) and
`tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040` v2.1. All 42 curves (2 perils ×
portfolio, 3 classes, 17 시도) are in
[`docs/evidence/sample1000_loss_curves.csv`](evidence/sample1000_loss_curves.csv).

### 2.1 River flood

| group | assets | with modeled flood | value | EAL | EAL / value | 10-yr | 100-yr | 250-yr | Σ standalone 100-yr | diversification (100-yr) |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | **383** (38.3 %) | $3,438 M | $4.89 M | 0.142 % | $16.1 M | $59.5 M | $186.8 M | $117.3 M | 49.3 % |
| Commercial | 334 | 134 (40.1 %) | $1,342 M | $2.74 M | 0.204 % | $3.9 M | $35.8 M | $97.7 M | $60.4 M | 40.8 % |
| Industrial | 333 | **117** (35.1 %) | $338 M | $0.45 M | 0.133 % | $1.4 M | $8.4 M | $11.5 M | $13.0 M | 35.1 % |
| Residential | 333 | 132 (39.6 %) | $1,759 M | $1.71 M | 0.097 % | $4.2 M | $23.9 M | $80.7 M | $44.0 M | 45.7 % |

With real buildings and a balanced sample every class carries a flood loss: 117 of 333
industrial buildings sit in cells the river-flood set floods. The validation fixture's
"Industrial = $0" was one asset (the Busan warehouse) in a dry cell — a sample-size and
coverage artefact, not a property of the class. Industrial value is small because OSM
industrial footprints are mostly single-storey sheds at the lowest unit value.

### 2.2 Tropical cyclone

| group | assets | with modeled wind | EAL | EAL / value | 100-yr | Σ standalone 100-yr | diversification |
|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | 1,000 | $268 k | 0.008 % | $2.73 M | $6.93 M | 60.6 % |
| Commercial | 334 | 334 | $89 k | 0.007 % | $1.12 M | $2.33 M | 51.9 % |
| Industrial | 333 | 333 | $33 k | 0.010 % | $0.34 M | $0.86 M | 59.8 % |
| Residential | 333 | 333 | $146 k | 0.008 % | $1.48 M | $3.74 M | 60.3 % |

Wind reaches every asset and one regional function (North West Pacific, Eberenz 2021)
applies to all classes, so EAL ÷ value differs only by where each class's buildings are.

## 3. Coverage by 시도 — what the river-flood set does not see

Assets with a non-zero river-flood cell, of those in each 시도:

| 시도 | assets | with flood | 시도 | assets | with flood |
|---|---|---|---|---|---|
| 서울특별시 | 141 | 109 | 경기도 | 386 | 94 |
| 광주광역시 | 66 | 43 | 경상북도 | 41 | 25 |
| 대구광역시 | 38 | 23 | 세종특별자치시 | 25 | 22 |
| 경상남도 | 51 | 18 | 대전광역시 | 20 | 15 |
| 강원특별자치도 | 25 | 8 | 충청북도 | 14 | 8 |
| 전라남도 | 38 | 6 | 전북특별자치도 | 24 | 6 |
| **인천광역시** | **45** | **3** | **부산광역시** | **27** | **2** |
| 충청남도 | 25 | 1 | **울산광역시** | **17** | **0** |
| **제주특별자치도** | **17** | **0** | | | |

The coastal cities are almost invisible to the river-flood set: 인천 3 / 45, 부산 2 / 27,
울산 0 / 17, 제주 0 / 17. Their flood exposure is coastal, storm-surge and pluvial — none of
which the ISIMIP river-flood product carries (GAP_ANALYSIS G7; SCOPE `tc_surge`,
`coastal_flood`). These zeros are coverage gaps, and the sheet says so per line ("Assets
with Modeled Hazard"). This is the evidence for adding a coastal-flood data scope to the
product model next.

## 4. Caveats

- Values are assumptions (footprint × floors × unit value); never a market or portfolio
  estimate. OSM footprints and floor tags are volunteer data with uneven coverage.
- Tier: river flood and tropical cyclone are SCOPE tier **I** (indicative) — see
  [SCOPE.md](SCOPE.md). No domestic loss validation backs these figures.
- 150-arcsec (~4.6 km) grids, nearest-centroid reading: buildings in one cell share its
  depth; small rivers, levees and urban drainage are not represented.
- Losses are in valuation-year values (no exposure growth applied).
- §2–§3 are the **undefended** run (before flood defences were introduced); §5 is the
  same portfolio with coastal flood added and the national design return periods applied.

## 5. Coastal flood and flood defences (2026-10-14)

Same 1,000 buildings, Recommended mode — each hazard under its own pick only (river flood
and typhoon: Korea country dataset; coastal flood: WRI Aqueduct, the Global reference,
because the Data API has no coastal-flood type). 3,000 rows, all `FULL`, 31 s once the
Aqueduct read is cached. Coastal: `aqueduct_inuncoast_rcp8p5_wtsub_2030_0_perc_50`
(rcp85 → Aqueduct rcp8p5, 2040 → nearest 2030, sea-level rise 50th percentile, with
subsidence, 30 arcsec). Defences: national design return period **50 years** for both
floods (`flood_protection`, basis in [FLOOD_DEFENCE_DATA_KR.md](FLOOD_DEFENCE_DATA_KR.md)).
All curves: [`docs/evidence/sample1000_loss_curves_defended.csv`](evidence/sample1000_loss_curves_defended.csv).

| hazard / class | assets in a hazard cell | EAL defended | EAL undefended | EAL / value defended → undefended | "floods at 2 y without defences" |
|---|---|---|---|---|---|
| River flood — portfolio | 383 | $3.06 M | $4.89 M | 0.089 % → 0.142 % | 0 |
| — commercial | 134 | $1.56 M | $2.74 M | 0.117 % → 0.204 % | 0 |
| — industrial | 117 | $0.28 M | $0.45 M | 0.082 % → 0.133 % | 0 |
| — residential | 132 | $1.22 M | $1.71 M | 0.069 % → 0.097 % | 0 |
| Coastal flood — portfolio | **9** | $0.15 M | $6.87 M | 0.004 % → 0.200 % | **8** |
| — commercial | 0 | $0 | $0 | 0 → 0 | 0 |
| — industrial | 8 | $0.08 M | $4.11 M | 0.025 % → 1.217 % | 7 |
| — residential | 1 | $0.06 M | $2.76 M | 0.004 % → 0.157 % | 1 |

**Coastal flood touches 9 of 1,000 buildings** (경기 4, 인천 3, 전북 2) — and 8 of them flood
4–5 m already at the 2-year layer: reclaimed industrial land behind sea walls (시화·반월,
남동, 군산 — inferred from the coordinates). Undefended, their EAL is 45–48 % of value per
year, which no real factory carries; with 50-year defences it is about 2 % of that. The
coastal-flood figures are therefore driven by the defence assumption, and coastal flood is
SCOPE tier **S** (screening). The busy coastal cities (부산, 울산, 제주) still show no
coastal hazard for these buildings: Aqueduct floods only a narrow low-lying strip at 30
arcsec, and the sampled buildings are not in it.

Two structural points for reading the Portfolio Loss Curve:

- **Coastal diversification is 0 by construction.** Aqueduct is one map per return period,
  so each "event" floods every location at that return period at once — a perfectly
  correlated portfolio. The 0 % diversification is a property of the data, not a finding.
- **Defences cut the frequent end, not the tail.** River-flood 10-year portfolio loss falls
  to ~$0.08 M (from $16.1 M undefended) while the 100- and 250-year losses barely move
  ($53.7 M / $154.3 M defended vs $59.5 M / $186.8 M undefended): events above the design
  return period overtop with their full depth.
