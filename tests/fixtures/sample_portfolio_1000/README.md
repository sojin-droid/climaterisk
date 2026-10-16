# 1,000-asset OSM-building test portfolio

`portfolio.csv` lists 1,000 real buildings drawn from OpenStreetMap (405 commercial,
405 residential, 190 industrial) by `scripts/build_sample_portfolio.py` from the spec in
`assets/libraries/sample_portfolio_spec.json` (v2.3: only buildings whose OSM record carries
a floor count, and at most one building of a class per hazard grid cell — `hazard_centroid`
is the CLIMADA centroid each row is read from). Locations, footprints, use tags and
`building:levels` are OpenStreetMap's. `asset_value_usd` is footprint × floors × the 2026
건물신축가격기준액 for the building's use (행정안전부; 주거·상업 860,000, 공업 840,000 원/㎡)
at the Bank of Korea 2025 average 1,422.22 KRW/USD — a replacement-cost proxy without
depreciation and with no lower or upper bound, not a valuation. `build_env.json` records the GDAL / GEOS / PROJ build that drew it — the CSV is
byte-reproducible on that build only. See `docs/sample-portfolio-1000.md`.

Contains information from OpenStreetMap, © OpenStreetMap contributors, made available under
the Open Database License (ODbL) 1.0 — https://www.openstreetmap.org/copyright. This file is
a derived database and is likewise made available under the ODbL; the OSM ids (`osm_type`,
`osm_id`) identify each source object.
