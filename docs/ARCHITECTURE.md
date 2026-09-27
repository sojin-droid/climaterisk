# Architecture

climaterisk is a **map-first orchestration platform**: it wires located facilities and bundled
methodology libraries into open-source risk engines, then visualizes the results. It runs as
**three processes**.

```
Frontend (Vite + React + TS, react-leaflet)
    │  HTTP/JSON  (Vite proxies /api → backend)
    ▼
Orchestration API (FastAPI, uv-managed)   ── owns the model (SQLite), serves libraries,
    │                                         runs light transition math, orchestrates runs
    │  writes data/runs/<id>/request.json  +  spawns `./.climada-env/bin/python -m …`
    ▼
CLIMADA worker (project-local conda env)   ── reads request.json, runs ImpactCalc,
                                              writes result.json   [Phase 2]
```

## Why three processes

- **CLIMADA needs a heavy conda/GDAL stack** (not pip-installable) and is CPU/memory-heavy. Running
  it inside the web request would block the API and force geo deps into the backend. It is installed
  as a **project-local conda prefix env** (`./.climada-env`, built from
  `worker/climaterisk_worker/env_climada.yml`) — never a global/named conda env — so it travels with
  the repo. The backend resolves its interpreter via `CLIMATERISK_WORKER_ENV_DIR` (see `config.py`).
- **GPL-3.0 boundary.** CLIMADA is GPL-3.0. The orchestration backend (`src/climaterisk`) imports
  **no** CLIMADA code; it communicates with the worker only via a JSON file contract
  (`src/climaterisk/engines/base.py`). The worker (`worker/climaterisk_worker`) lives in its own
  conda env and never imports the backend package.

## Backend owns the model

The browser persists only a `sessionId` (localStorage). The whole `Portfolio` document lives
server-side in `data/app.db` (SQLite, WAL). Edits are synced back with a debounced full-model PUT.

## Layers (backend)

| Package | Responsibility |
|---|---|
| `core/` | Domain entities + enums (pure data, no I/O) |
| `data/` | SQLite session store, bundled-library loaders |
| `paths.py` | the single path authority: repo root, `CLIMATERISK_DATA_ROOT` layout, CLIMADA data dir — see [DATA_LAYOUT.md](DATA_LAYOUT.md) |
| `engines/` | The physical-engine request/result JSON contract |
| `transition/` | Light transition-risk math (Phase 3) |
| `api/` | FastAPI app + routers (thin) |

## Perils database (local hazard catalog)

The platform can build and use its **own** CLIMADA-ready hazard library instead of
depending only on the live CLIMADA Data API (which lacks coastal flood / heat /
drought and only has historical wildfire). The catalog lives in `<DATA_ROOT>/derived/hazard_db/` (transitionally the repo-local `data/hazard_db/`)
(git-ignored; rebuilt from real sources) with a `catalog.json` manifest indexing
HDF5 hazards by `(peril, climate_scenario, region, year)`.

```
real source ──ingest──▶ standardized grid (cells × years × intensity)
                              │  scripts/build_hazard.py convert
                              ▼
                        CLIMADA Hazard.write_hdf5()  ──▶  data/hazard_db/<peril>/*.hdf5
                                                          data/hazard_db/catalog.json
  (or: build_hazard.py cache  ──▶  cache a Data-API hazard for offline/reproducible runs)
```

- **Converter** (`worker/climaterisk_worker/hazard_convert.py`): a standardized
  observation grid → `Hazard`. The mapping is faithful — grid cell → centroid,
  year → event, intensity → `intensity[event, centroid]`, `1/n_years` → frequency —
  so per-cell exceedance frequency equals the source pipeline's annual exceedance
  probability (same event-frequency model).
- **Catalog** (`worker/climaterisk_worker/catalog.py`): manifest lookup + `from_hdf5`.
- **Resolver:** each worker peril runner calls `catalog.load_hazard(...)` first and
  falls back to the Data API. Result `detail` reports which source was used.
- **CLI** (`scripts/build_hazard.py`): `convert <grid.json>` (real ingestion output)
  or `cache --data-type … --props …` (cache a Data-API hazard); `list` shows entries.
- **API:** `GET /api/hazard-catalog` exposes the manifest to the UI.

This is the on-ramp for filling Data-API gaps: produce a standardized grid from any
real source and `convert` it; the platform then serves that peril/region locally.

## Dimensional model

Analysis is organized on three axes: **depth** (asset → portfolio → national, the build ladder),
**sector** (vulnerability + transition profiles), and **geographic scale** (point → footprint →
regional → national).
