# AGENTS.md — city digital twin (Hermes)

This worktree (`~/Developer/climaterisk-twin`, branch `feat/city-digital-twin`) is where an
autonomous agent builds a **synthetic city digital twin** on top of this repository's CLIMADA
engine. Read `CLAUDE.md` too: its conventions (type hints, docstrings with `Algorithm:`,
seeds, tests against analytical solutions, conventional commits) apply here unchanged.

The aim is a simulation tool in the spirit of EnergyPlus / ENVI-met / CFD: physical
equations stepped in time on an imaginary city. **Purpose (user, 2026-11-05):** see, mathematically
and visually, how a physical environment (terrain, buildings, flow, shade) changes what the
CLIMADA engine reports. So every physical model should also leave something to look at:
time snapshots, maps, cross-sections — not only a final number. It cannot be validated against
measurements, so every model must be **verified** — against analytical solutions,
published benchmarks and conservation laws — before its numbers are used.

## Where the code lives

- `worker/climaterisk_worker/twin/` — everything: `spec`, `city`, `flood` (generator),
  `hazard`, `pricing`, `network`, `plot`, `pipeline`. Not `src/`: the backend environment
  has no NumPy/NetworkX and `mypy src/` runs in CI.
- `scripts/synthetic_city.py` — thin CLI only.
- `assets/libraries/synthetic_city_spec.json` — every assumption, each with a `_note`.

## Steps (in order; finish, test and commit one before starting the next)

1. ~~Twin package~~ — done (`8d560da`, `064d60a`).
2. ~~Defence scenarios~~ — done (`91a15cf`, `063266f`): EAL 1.303 / 0.749 / 0.313 / 0.186 %
   for none / 10 / 50 / 100 y, local 100-y levee on cols 5..14 = 0.739 %. Present-day
   numbers to compare step 3 against.
3. ~~Climate change~~ — done (`3524894`): +0.5 m stage, levees at present height.
   Multiplier none 1.845 / 10 y 2.111 / 50 y 2.010 / 100 y 2.054 / levee 1.872 (the
   re-derived-levee trap would give 1.22 at 50 y). `protection.protect_with_threshold`
   applies a present design depth to a future hazard.
4. ~~Map viewer~~ — done (`46a146b`, `d0ec7cd`): `twin/viewer.py`, `--map` → `city_map.html`.
   Page scripts that read Folium objects must run on `window` `load`, not `setTimeout(0)`.
5. ~~3D buildings~~ — done: `twin/viewer3d.py`, `--map3d` → `city_3d.html` (deck.gl 9.4.1
   from jsDelivr, terrain + extruded buildings + flat water plane at the RP stage).
   Storeys are geometry only; feeding them into ground-floor/basement exposure is a later,
   separate change that WILL move EAL and needs its own expected values.
6. **2D hydraulic model.** Local inertial shallow-water scheme on a raster (Bates,
   Horritt & Fewtrell 2010, J. Hydrol.; stability per de Almeida et al. 2012, WRR),
   written from the equations in NumPy — do not copy code from LISFLOOD-FP or others.
   Gate: before any city run it must pass, as tests,
   - 1D dam break vs the Ritter analytical solution,
   - Thacker's parabolic bowl (wetting/drying),
   - mass balance: inflow + rain − infiltration − drainage − storage < 0.1 %,
   - qualitative agreement with the UK Environment Agency 2D benchmarks
     (Néelz & Pender 2013) where reproducible.
   Its peak depths then replace the stage law as the RF hazard (same engine pricing).
   **Part A done** (`twin/hydraulics.py`, `tests/test_hydraulics.py`): Stoker wet dam break
   L1 2.4 % (tol 5 %), Thacker bowl Fr 0.07 L1 1.1 % (tol 2 %), mass 1e-15 (tol 1e-3),
   lake at rest exact. Errors are flat under refinement and grow with Froude (Thacker
   1 / 4 / 9 % at Fr 0.07 / 0.14 / 0.28): that is the dropped advection term, a model limit.
   Keep city runs at Fr < 0.5 and report the max Froude number of every run.
   **Part B done** (`twin/inundation.py`, `tests/test_inundation.py`, `--hydraulic`):
   25 m raster, river row on a raised-cosine stage wave peaking at h(T), Manning 0.05.
   EAL 2D / stage law = 0.997 (24 h wave), volume closure 1e-11, max Fr 0.42. The city is
   2 km deep and fills in minutes, so the bathtub is a good approximation *here*; the 2D
   model differs in enclosed hollows (tested) and will matter for big floodplains, levee
   breaches and rain (step 10). A 6 h wave reaches Fr 0.93: outside validity, flagged.
   The stage law stays the default hazard (2D costs ~40 s per wave duration).
   Levees are still applied after the run (`protection.protect`), not as walls in 2D.
7. **Typhoon.** Synthetic tracks → CLIMADA `TropCyclone` wind field (Holland profile),
   priced with the repo's TC regional impact function. **Gate: `tests/test_typhoon.py`**
   (protected) — its docstring is the API to implement in `twin/typhoon.py`. Add a
   `typhoon` section to the spec (seeded tracks passing near the city; every value with a
   `_note`) and a `--typhoon` CLI flag printing EAL % by asset kind. Wind needs no download:
   pass `ignore_distance_to_coast=True` to `TropCyclone.from_tracks`.
   **Done** (`d649a43` by Sonnet, fixed in review): EAL 0.045 % of value, uniform across
   asset kinds (the city is ~2 km, storms ~100 km wide, one WP4 curve for all types).
8. **Heatwave.** Hazard-only in money terms (no published damage curve). Street-level
   mean radiant temperature / UTCI and the repo's labour-productivity and heat-mortality
   modules on a synthetic population. EnergyPlus for building cooling load is optional and
   needs the user's approval to install.
   **Gate: `tests/test_heat.py`** (protected) — its docstring is the API and every formula
   for `twin/heat.py`. Heat index = official UTCI (`pythermalcomfort==4.8.0`, installed in
   `.climada-env` with the user's approval 2026-11-05); MRT from a street-canyon
   approximation (SVF, sunlit fraction) — the MRT, not the UTCI, is the repo-specific part.
   **Done** (`0b11414` proxy by Sonnet, UTCI in review): UTCI 39-52 degC at noon,
   productivity loss 20 % of the heatwave-day wage bill. That is high because the noon peak
   is applied to whole working days — an hourly UTCI cycle is the next refinement.
   Add a `heat` section to the spec (every value with a `_note`) and a `--heat` CLI flag.
   The heat-mortality module is calibrated on European reference cities; leave it out.
9. **Coastal flood / surge.** Add a coastline to the spec; bathtub first (CLIMADA Petals
   `TCSurgeBathtub`), then the step-6 model with a coastal boundary stage.
   **Gate 9A: `tests/test_coastal.py`** (protected) — its docstring is the API for
   `twin/coastal.py`. The coast is synthetic: CLIMADA's distance-to-coast comes from a NASA
   download (blocked, and meaningless for an imaginary city), so pass centroids whose
   `get_dist_coast` is overridden and re-attach them to the wind hazard before calling
   `TCSurgeBathtub.from_tc_winds` (TropCyclone replaces its centroids). DEM = city terrain
   written as a GeoTIFF. Add a `coast` section to the spec and a `--coastal` CLI flag.
   **9A done** (`f1baac7` by Sonnet, verified): max surge 4.4 m, 566/1160 assets wet,
   EAL 7.9 % (10.6 % with +0.5 m sea level). High because the bank is 0.5 m above datum
   with no sea wall, and Petals' Xu (2010) line floors surge at 1.83 m for ANY wind above
   CLIMADA's 17.5 m/s threshold (19 of 20 tracks). Treat 9A as an upper bound.
   **Gate 9B: `tests/test_surge2d.py`** (protected) — 2D surge in `twin/surge2d.py` plus
   `hydraulics.run(snapshot_every_s=...)` time snapshots for animation. Add
   `surge_duration_h` / `surge_recession_h` to the spec `coast` section and a
   `--coastal2d` CLI flag comparing bathtub (Petals) and 2D EAL.
   **9B done** (`a76d9de` by Sonnet, reviewed): EAL Petals 7.95 % (inland decay 0.2 m/km,
   local wind) vs 2D 8.68 % (one coast stage from the strongest coastline wind, no decay)
   — not like-for-like forcing. Against the same-stage bathtub the 2D depth is 1.005x on
   average, up to +6.7 cm (momentum overshoot; above the 2 cm small-city tolerance).
   Max Fr 1.9 is at 5 cm wetting fronts; bulk flow h > 0.3 m peaks at Fr 0.51 and at most
   49 of 6006 cells exceed 0.5 at once. Flag kept as OUTSIDE VALIDITY (not re-thresholded
   after seeing results). surge_duration_h = 2 h was chosen so the weak gate test (short <
   long on a 500 m test city) passes; 2 h is still a plausible surge peak duration.
9b. **Time viewer.** Animate the 2D snapshots (river flood, surge) over the 3D city in the
   deck.gl page: time slider + play, water depth on the raster, buildings coloured by
   depth reached so far, a panel showing the governing equations and live numbers.
   **Gate: `tests/test_viewer_time.py`** (protected) — API in its docstring
   (`twin/viewer_time.py`, `--animate` CLI flag reading `surge_snapshots.npz`).
R. **Real neighbourhood (user, 2026-11-05: public data allowed).** Gangnam station area,
   bbox ~127.022-127.034 E, 37.492-37.502 N: OpenStreetMap buildings from
   `paths.osm_dir("south-korea-261013.osm.pbf")` (ODbL — credit "© OpenStreetMap
   contributors") and the Copernicus GLO-30 surface model in
   `paths.external_dir("copernicus_dem")` (credit "Copernicus DEM, ESA"). GLO-30 is a
   surface model: building footprints are masked and the ground interpolated.
   R1 site extraction + buildings as walls in the 2D model — **gate `tests/test_site.py`**.
   **R1 done** (`f876274` by Sonnet + review): 105 x 111 cells of 10 m, 880 buildings
   (height tag 30 %, levels 12 %, typology 58 %), ground 7-72 m (Seocho basin west of the
   station, Yeoksam hill east). Review fix: OSM marks the Gangnam Station Underground
   Shopping Center as a building — underground features (`layer` < 0, `location=underground`,
   `tunnel=yes`) and canopies (`building=roof`) are not walls; keep the underground mall as
   an exposure for R3 (water enters it).
   R2 rain on the real site (this replaces step 10 on the synthetic city), time viewer with
   real footprints, buildings-as-walls vs no-buildings comparison. R3 pricing.
10. **Pluvial (rain) flood.** On the step-6 model: Huff design-storm hyetograph, NRCS
    curve-number infiltration, manhole sinks at a sewer capacity. Repo-specific method —
    label it as such in outputs.

## Known traps

- **Service cut-off indicator.** `network_failure` counts a lot as cut off only if it is
  *not* itself flooded, so the indicator can fall as floods get worse (e.g. 0 at +0.5 m
  with a 50 y defence). Report flooded + cut-off together, and never compare scenarios on
  the cut-off count alone.
- **Seed sensitivity.** Seeds 1–3 move EAL by about ±7 % and the cut-off value by 4×.
  Compare scenarios on the same seed, and show a seed spread before claiming a difference.
- **A defence protects the land behind it.** A levee band covers the floodplain it fronts,
  not just the bank cells (step 2 first defended row 0 only and the city still flooded).
- **Tolerances come before results.** Pick a test tolerance from the method's known error
  and say why; never widen it to whatever the code produced (step 6A first shipped a
  2 m dam break with a 1.6 m tolerance and a wrong Ritter formula).
- **CLIMADA TC wind comes from central pressure**, not `max_sustained_wind`. Solve the
  pressure for the wanted wind (`typhoon.pressure_for_vmax`); a fixed pressure-wind formula
  mislabelled storms by 10-24 % and cut EAL by ~2.8x.
- **Petals surge bathtub floors at 1.83 m** for every wind >= 17.5 m/s (Xu 2010 intercept),
  so weak storms still flood a low coast. Report it as an upper bound.
- **Never add ruff per-file ignores** to silence long lines; wrap them.
- **Tooling versions.** Use `uv sync --locked --all-extras`; CI pins ruff 0.15.18. A newer
  ruff reformats Markdown code blocks — never commit changes to files outside your step.

## Hard rules

- **Pricing:** never re-implement damage arithmetic. Use
  `climaterisk_worker.physical_risk.engine.calculate` / `registry` curves / `ImpactCalc`.
  Keep the per-asset vs portfolio `aai_agg` check (rtol 1e-9).
- **Data:** synthetic only. Do **not** use client survey data, real building registers, or
  the Korean flood-risk map (공공누리 제4유형). No downloads or new installs without asking.
- **Environment:** CLIMADA runs only in
  `/Users/junsojin/Developer/climaterisk/.climada-env/bin/python`. Do not install into it
  or create another CLIMADA env. `uv run` is for lint/format/type-check.
- **Scope:** write only inside this worktree. Do not touch `~/Developer/climaterisk`
  (the user's main checkout has uncommitted work), `~/.ssh`, or shell rc files.
- **Git:** commit on `feat/city-digital-twin` with conventional commits. Do not push,
  open PRs, merge, or force-push — the user reviews first.

## Check before every commit

```bash
git add -A && ./check.sh
```

`check.sh` runs lint, format, type-check, both test suites and a city run, and refuses the
commit if a **protected gate test** (listed in `.protected-tests`) changed. Gate tests are
written by the reviewer before a step starts; make the code pass them, never edit them.
If you think a gate test is wrong, stop and say why in your report.

Report EAL % by asset kind after each step and say whether it moved in the expected
direction (more defence → lower EAL; higher stage → higher EAL).
