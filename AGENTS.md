# AGENTS.md — city digital twin (Hermes)

This worktree (`~/Developer/climaterisk-twin`, branch `feat/city-digital-twin`) is where an
autonomous agent builds a **synthetic city digital twin** on top of this repository's CLIMADA
engine. Read `CLAUDE.md` too: its conventions (type hints, docstrings with `Algorithm:`,
seeds, tests against analytical solutions, conventional commits) apply here unchanged.

## Goal

Grow `scripts/synthetic_city.py` (PR #48) into a digital twin of an imaginary city that a
person can open and explore, while every loss number still comes from the repo's engine.

Work in this order; finish and test one step before starting the next:

1. **Twin package.** Move the generator out of the script into `src/climaterisk/twin/`
   (city graph, terrain, assets) and `worker/climaterisk_worker/twin/` (CLIMADA hazard,
   pricing). Keep `scripts/synthetic_city.py` as a thin CLI. Existing tests must still pass.
2. **Scenarios.** Compare, for the same city: no defence, uniform design return period
   (10/50/100 y), and a local levee along part of the river bank. Output one table.
3. **Time dimension.** Present vs future flood stage (a `stage_slope_m` / `stage_2y_m`
   shift per scenario in the spec JSON), reported as `climate_change_multiplier`
   = future EAL / present EAL of the same city.
4. **Viewer.** A static HTML map (Folium or Leaflet, no server) from `assets.gpkg`:
   lots coloured by EAL %, a return-period slider for depth and out-of-service nodes.

## Hard rules

- **Pricing:** never re-implement damage arithmetic. Use
  `climaterisk_worker.physical_risk.engine.calculate` / `registry` curves / `ImpactCalc`.
  Keep the per-asset vs portfolio `aai_agg` check (rtol 1e-9).
- **Data:** synthetic only. Every assumption lives in
  `assets/libraries/synthetic_city_spec.json` (or a new spec JSON next to it) with a
  `_note`. Do **not** use client survey data, real building registers, or the Korean
  flood-risk map (공공누리 제4유형). No downloads of new datasets without asking.
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
uv run ruff check . && uv run ruff format --check .
/Users/junsojin/Developer/climaterisk/.climada-env/bin/python scripts/synthetic_city.py --out /tmp/twin_check
```

Report EAL % by asset kind after each step and say whether it moved in the expected
direction (more defence → lower EAL; higher stage → higher EAL).
