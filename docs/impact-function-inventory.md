# Impact-function inventory — what the installed CLIMADA actually provides

Generated 2026-09-22 by instantiating every function in the worker
environment (`climada 6.1.0`, `climada_petals 6.2.0`). Every number below
was read off a live object; nothing is transcribed from documentation. Regenerate with:

```bash
PYTHONPATH=worker:src ./.climada-env/bin/python -c \
  "from climaterisk_worker.physical_risk import registry, json; print(json.dumps(registry.build_registry()))"
```

The engine calls these functions directly (`registry.flood_impact_function`,
`registry.tc_impact_function`). No damage curve is authored in this repository.

## RF — river flood (CLIMADA Petals, JRC)

`climada_petals.entity.impact_funcs.river_flood.ImpfRiverFlood.from_jrc_region_sector`.
**The hazard tag is `RF`, not `FL`.** Region `Asia` shown; the same six sectors exist for
Africa, Asia, Europe, North America, Oceania, South America.

| id | name | sector | intensity | pts | MDD | PAA |
|---|---|---|---|---|---|---|
| 21 | Flood Asia JRC Residential noPAA | residential | 0–12 m | 11 | 0–1 | 1 (flat) |
| 22 | Flood Asia JRC Commercial noPAA | commercial | 0–12 m | 11 | 0–1 | 1 (flat) |
| 23 | Flood Asia JRC Industrial noPAA | industrial | 0–12 m | 11 | 0–1 | 1 (flat) |
| 24 | Flood Asia JRC Transport noPAA | transport | 0–12 m | 11 | 0–1 | 1 (flat) |
| 25 | Flood Asia JRC Infrastructure noPAA | infrastructure | 0–12 m | 11 | 0–1 | 1 (flat) |
| 26 | Flood Asia JRC Agriculture noPAA | agriculture | 0–12 m | 11 | 0–1 | 1 (flat) |

Reference: Huizinga, J., de Moel, H., Szewczyk, W. (2017) Global flood depth-damage functions: methodology and the database with guidelines, JRC105688

The `noPAA` in the names is CLIMADA's own: PAA is 1 across the whole range, so all of the
damage is carried by MDD.

## TC — tropical cyclone (CLIMADA core)

`ImpfTropCyclone.from_emanuel_usa` and the ten regionally calibrated functions of
`ImpfSetTropCyclone.from_calibrated_regional_ImpfSet`.

| id | name | intensity | pts | MDD max | class |
|---|---|---|---|---|---|
| 1 | Emanuel 2011 | 0–120 m/s | 25 | 0.8770 | `ImpfTropCyclone.from_emanuel_usa` |
| 1 | Caribbean and Mexico | 0–120 m/s | 25 | 0.9261 | `ImpfSetTropCyclone` |
| 2 | USA and Canada | 0–120 m/s | 25 | 0.7661 | `ImpfSetTropCyclone` |
| 3 | North Indian | 0–120 m/s | 25 | 0.9014 | `ImpfSetTropCyclone` |
| 4 | Oceania | 0–120 m/s | 25 | 0.9367 | `ImpfSetTropCyclone` |
| 5 | South Indian | 0–120 m/s | 25 | 0.9778 | `ImpfSetTropCyclone` |
| 6 | South East Asia | 0–120 m/s | 25 | 0.9256 | `ImpfSetTropCyclone` |
| 7 | Philippines | 0–120 m/s | 25 | 0.1630 | `ImpfSetTropCyclone` |
| 8 | China Mainland | 0–120 m/s | 25 | 0.5593 | `ImpfSetTropCyclone` |
| 9 | North West Pacific | 0–120 m/s | 25 | 0.1578 | `ImpfSetTropCyclone` |
| 10 | Rest of The World | 0–120 m/s | 25 | 0.5824 | `ImpfSetTropCyclone` |

References: Emanuel, K. (2011) Global warming effects on U.S. hurricane damage, Weather, Climate and Society 3:261-268; Eberenz, S., Lüthi, S., Bresch, D.N. (2021) Regional tropical cyclone impact functions for globally consistent risk assessments, NHESS 21:393-415

### Which function a country gets

From CLIMADA's own table (`ImpfSetTropCyclone.get_impf_id_regions_per_countries`), not by
reading the region label:

| ISO3 | impf id | region code | region name |
|---|---|---|---|
| KOR | 9 | WP4 | North West Pacific |
| JPN | 9 | WP4 | North West Pacific |
| CHN | 8 | WP3 | China Mainland |
| PHL | 7 | WP2 | Philippines |
| USA | 2 | NA2 | USA and Canada |
| IND | 3 | NI | North Indian |

Calibrated `v_half` (m/s), `v_thresh` 25.7 throughout: `NA1` 66.3, `NA2` 89.2, `NI` 70.8, `OC` 64.1, `SI` 52.4, `WP1` 66.4, `WP2` 188.4, `WP3` 112.8, `WP4` 190.5, `ROW` 110.1.

## Heat / heatwave — none

`status: NO_IMPACT_FUNCTION`. The impact-function packages contain:

* `climada.entity.impact_funcs` → `base`, `impact_func_set`, `storm_europe`, `test`, `trop_cyclone`
* `climada_petals.entity.impact_funcs` → `drought`, `relative_cropyield`, `river_flood`, `test`, `wildfire`

Nothing heat-, thermal-, mortality- or UTCI-related. CLIMADA core and Petals ship no heat/heatwave impact function; the engine therefore reports heat hazards as HAZARD_ONLY with null financial fields rather than authoring a curve (docs/physical-risk-methodology.md 7).

Consequence for the engine: heat hazards produce a row with `calculation_status`
`HAZARD_ONLY`, the hazard intensity reported, and every financial field null — never `0`.

## Not used by this engine

Petals also ships `drought` (`ImpfDrought`), `relative_cropyield`
(`ImpfRelativeCropyield`) and `wildfire` (`ImpfWildfire`); core ships `storm_europe`
(`ImpfStormEurope`). They are outside the three hazards in scope and are listed here only
so the inventory is complete.

