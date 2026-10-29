# AGENTS.md — city digital twin (Hermes)

This worktree (`~/Developer/climaterisk-twin`, branch `feat/city-digital-twin`) is where an
autonomous agent builds a **synthetic city digital twin** on top of this repository's CLIMADA
engine. Read `CLAUDE.md` too: its conventions (type hints, docstrings with `Algorithm:`,
seeds, tests against analytical solutions, conventional commits) apply here unchanged.

The aim is a simulation tool in the spirit of EnergyPlus / ENVI-met / CFD: physical
equations stepped in time on an imaginary city. It cannot be validated against
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
5. **3D buildings.** Synthetic footprints and storey counts per zone (spec JSON); a
   deck.gl / Three.js page with extruded buildings and a water surface. Storeys feed the
   ground-floor/basement exposure.
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
7. **Typhoon.** Synthetic tracks → CLIMADA `TropCyclone` wind field (Holland profile),
   priced with the repo's TC regional impact function.
8. **Heatwave.** Hazard-only in money terms (no published damage curve). Street-level
   mean radiant temperature / UTCI and the repo's labour-productivity and heat-mortality
   modules on a synthetic population. EnergyPlus for building cooling load is optional and
   needs the user's approval to install.
9. **Coastal flood / surge.** Add a coastline to the spec; bathtub first (CLIMADA Petals
   `TCSurgeBathtub`), then the step-6 model with a coastal boundary stage.
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
/Users/junsojin/Developer/climaterisk/.climada-env/bin/python -m pytest tests/test_synthetic_city.py
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
uv run pytest
/Users/junsojin/Developer/climaterisk/.climada-env/bin/python scripts/synthetic_city.py --out /tmp/twin_check
```

Report EAL % by asset kind after each step and say whether it moved in the expected
direction (more defence → lower EAL; higher stage → higher EAL).
