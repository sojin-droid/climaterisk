# 1,000-asset OSM-building test portfolio

`portfolio.csv` lists 1,000 real buildings drawn from OpenStreetMap (334 commercial,
333 residential, 333 industrial) by `scripts/build_sample_portfolio.py` from the spec in
`assets/libraries/sample_portfolio_spec.json` (v2.1: only buildings whose OSM record carries
a floor count). Locations, footprints, use tags and `building:levels` are OpenStreetMap's;
`asset_value_usd` is an **assumption** (footprint × floors × class unit value), not a
valuation. `build_env.json` records the GDAL / GEOS / PROJ build that drew it — the CSV is
byte-reproducible on that build only. See `docs/sample-portfolio-1000.md`.

Contains information from OpenStreetMap, © OpenStreetMap contributors, made available under
the Open Database License (ODbL) 1.0 — https://www.openstreetmap.org/copyright. This file is
a derived database and is likewise made available under the ODbL; the OSM ids (`osm_type`,
`osm_id`) identify each source object.
