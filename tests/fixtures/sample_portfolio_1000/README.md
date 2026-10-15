# 1,000-asset OSM-building test portfolio

`portfolio.csv` lists 1,000 real buildings drawn from OpenStreetMap (405 commercial,
405 residential, 190 industrial) by `scripts/build_sample_portfolio.py` from the spec in
`assets/libraries/sample_portfolio_spec.json` (v2.2: only buildings whose OSM record carries
a floor count, and at most one building of a class per hazard grid cell — `hazard_centroid`
is the CLIMADA centroid each row is read from). Locations, footprints, use tags and
`building:levels` are OpenStreetMap's;
`asset_value_usd` is an **assumption** (footprint × floors × class unit value), not a
valuation. `build_env.json` records the GDAL / GEOS / PROJ build that drew it — the CSV is
byte-reproducible on that build only. See `docs/sample-portfolio-1000.md`.

Contains information from OpenStreetMap, © OpenStreetMap contributors, made available under
the Open Database License (ODbL) 1.0 — https://www.openstreetmap.org/copyright. This file is
a derived database and is likewise made available under the ODbL; the OSM ids (`osm_type`,
`osm_id`) identify each source object.
