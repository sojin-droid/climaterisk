# Synthetic 1,000-asset portfolio — balanced asset classes and the portfolio loss curve

Written 2026-10-14. **Every asset here is synthetic.** The portfolio exists to read the
portfolio / asset-class / 시도 loss curves (Portfolio Loss Curve sheet) on a sample in which
every asset class has hundreds of assets. The 5-asset
[validation fixture](../tests/fixtures/asset_level_validation/validation_portfolio.csv) is a
set of golden regression cases A–E with one industrial asset; it is not reshaped for this.

## 1. Construction

| item | value |
|---|---|
| file | [`tests/fixtures/sample_portfolio_1000/portfolio.csv`](../tests/fixtures/sample_portfolio_1000/portfolio.csv) |
| builder | [`scripts/build_sample_portfolio.py`](../scripts/build_sample_portfolio.py) (worker env, geopandas) |
| spec | [`assets/libraries/sample_portfolio_spec.json`](../assets/libraries/sample_portfolio_spec.json) — seed `20261014`, no number in the script |
| classes | commercial **334** (office / retail / hotel) · residential **333** (apartment / residential) · industrial **333** (warehouse / logistics / factory) |
| class key | the JRC sector the engine maps the property type to (`property_type_to_jrc_sector`) — tested, so a class is never a second taxonomy |
| location | a 시군구 uniformly at random (SGIS `bnd_sigungu_00_2025_2Q`, 252 units), then a point uniformly at random **inside its official polygon** (rejection sampling in EPSG:5179); 1,000 draws reach 248 of the 252 units, in all 17 시도 |
| value | lognormal per class (medians $30 M / $20 M / $25 M; log-σ 0.8 / 0.7 / 0.9), clipped to $1 M–$500 M, rounded to $10 k — total $34.2 B |
| determinism | same spec + same boundary files ⇒ byte-identical CSV (checked by rebuilding: md5 `db4d2d1d…`) |

Uniform per 시군구, not per km²: area weighting would put most assets in large rural
counties. Points are **not** steered towards flooded cells — that would make the coverage
figures below meaningless.

```bash
./.climada-env/bin/python scripts/build_sample_portfolio.py
./.climada-env/bin/python scripts/physical_risk_batch.py tests/fixtures/sample_portfolio_1000/portfolio.csv --hazards flood,typhoon --models recommended --scenario rcp85 --year 2040 --country KOR --output sample1000.xlsx --json sample1000.json
```

## 2. Run (2026-10-14, Mac mini, 26 s)

Recommended data scope = Korea country dataset (Data API) for both perils; 2,000 rows, all
`FULL`. Datasets: `river_flood_150arcsec_rcp85_KOR_2030_2050` v3 (480 events) and
`tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040` v2.1. All 42 curves (2 perils ×
portfolio, 3 classes, 17 시도) are in
[`docs/evidence/sample1000_loss_curves.csv`](evidence/sample1000_loss_curves.csv).

### 2.1 River flood

| group | assets | with modeled flood | value | EAL | EAL / value | 10-yr | 100-yr | 250-yr | Σ standalone 100-yr | diversification (100-yr) |
|---|---|---|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | **285** (28.5 %) | $34.21 B | $43.36 M | 0.127 % | $146.0 M | $790.2 M | $1,088.8 M | $1,342.3 M | 41.1 % |
| Commercial | 334 | 103 (30.8 %) | $14.18 B | $20.34 M | 0.143 % | $61.0 M | $390.1 M | $524.1 M | $660.7 M | 41.0 % |
| Industrial | 333 | **99** (29.7 %) | $12.22 B | $15.07 M | 0.123 % | $41.2 M | $282.0 M | $335.0 M | $418.3 M | 32.6 % |
| Residential | 333 | 83 (24.9 %) | $7.80 B | $7.95 M | 0.102 % | $14.3 M | $178.9 M | $235.3 M | $263.3 M | 32.1 % |

With a balanced sample the industrial class carries a real flood loss: **99 of 333**
industrial assets sit in cells the river-flood set floods, and their 100-year loss is
$282 M. The validation fixture's "Industrial = $0" was one asset (the Busan warehouse) in a
dry cell — a sample-size and coverage artefact, not a property of the class.

EAL ÷ value spans 0.10–0.14 % across the classes. The JRC Asia sector curves are close at
the same depth (1 m: residential 49 %, commercial 54 %, industrial 48 % damage), so most of
that spread comes from where each class's assets happened to land (30.8 / 29.7 / 24.9 % in
flooded cells), not from the curves.

### 2.2 Tropical cyclone

| group | assets | with modeled wind | EAL | EAL / value | 100-yr | Σ standalone 100-yr | diversification |
|---|---|---|---|---|---|---|---|
| Portfolio | 1,000 | 1,000 | $3.56 M | 0.010 % | $32.2 M | $87.7 M | 63.3 % |
| Commercial | 334 | 334 | $1.48 M | 0.010 % | $12.8 M | $35.8 M | 64.3 % |
| Industrial | 333 | 333 | $1.26 M | 0.010 % | $11.9 M | $31.3 M | 61.9 % |
| Residential | 333 | 333 | $0.84 M | 0.011 % | $7.9 M | $20.6 M | 61.7 % |

Wind reaches every asset; one regional function (North West Pacific, Eberenz 2021) applies to
all classes, so EAL ÷ value is flat across classes. Diversification is larger than for flood
because a single track hits a small part of the country.

## 3. Coverage by 시도 — what the river-flood set does not see

Assets with a non-zero flood cell, of those placed in each 시도:

| 시도 | assets | with flood | 시도 | assets | with flood |
|---|---|---|---|---|---|
| 서울특별시 | 106 | 77 | 경상남도 | 88 | 33 |
| 경기도 | 165 | 35 | 경상북도 | 92 | 21 |
| 충청북도 | 50 | 22 | 강원특별자치도 | 70 | 17 |
| 대구광역시 | 35 | 17 | 전라남도 | 90 | 15 |
| 전북특별자치도 | 69 | 11 | 충청남도 | 52 | 10 |
| 광주광역시 | 18 | 9 | 대전광역시 | 24 | 8 |
| 세종특별자치시 | 6 | 4 | **부산광역시** | **70** | **4** |
| **인천광역시** | **39** | **1** | **제주특별자치도** | **8** | **1** |
| **울산광역시** | **18** | **0** | | | |

The coastal metropolitan cities are almost invisible to the river-flood set: 부산 4 / 70,
인천 1 / 39, 울산 0 / 18, 제주 1 / 8. Their flood exposure is coastal, storm-surge and pluvial
— none of which the ISIMIP river-flood product carries (GAP_ANALYSIS G7; SCOPE `tc_surge`,
`coastal_flood`). These zeros are coverage gaps, and the sheet now says so per line
("Assets with Modeled Hazard"). This is the evidence for adding a coastal-flood data scope
to the product model next.

## 4. Caveats

- Synthetic values and locations: use the numbers to read the method, never as a market or
  portfolio estimate.
- Tier: river flood and tropical cyclone are SCOPE tier **I** (indicative) — see
  [SCOPE.md](SCOPE.md). No domestic loss validation backs these figures.
- 150-arcsec (~4.6 km) grids, nearest-centroid reading: two assets in one cell share its
  depth; small rivers, levees and urban drainage are not represented.
- Losses are in valuation-year values (no exposure growth applied).
