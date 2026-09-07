# climaterisk

**Map-first, web-based climate-risk analysis platform.** Place facilities on a map, pick climate
(RCP/SSP) and policy (NGFS) scenarios and a horizon, and assess **physical risk** (CLIMADA-backed)
and **transition / policy risk** (NGFS carbon-cost) at the **asset → portfolio → national** levels.

![Physical-risk results for a Tokyo facility — tropical cyclone](docs/img/results-tropical-cyclone.png)

*Results for a single $10M Tokyo facility under RCP4.5 to 2040: expected annual impact, the
present→future climate delta, a per-asset map overlay, and the return-period loss curve — computed
by CLIMADA and rendered in the browser.*

The platform is a **framework + UI that orchestrates open-source risk engines** — it does not invent
climate science. Physical risk is computed by **CLIMADA** (run as a separate conda worker process);
transition risk uses bundled **NGFS** scenarios + **EDGAR** emission factors.

## Quick start

```bash
git clone https://github.com/sojin-droid/climaterisk.git && cd climaterisk
uv sync --all-extras && npm --prefix frontend/climaterisk install
conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./.climada-env
./run.command          # Windows: run.bat
```

Then in the browser: **Map** (place assets or model a country's exposure) → **Scenarios** →
**Results → Run analysis**. Full walkthrough of every tab, the CLI tools, data access and
troubleshooting: **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** (한국어 요약:
[docs/USER_GUIDE_KO.md](docs/USER_GUIDE_KO.md)).

## Architecture (three processes)

```
Frontend (Vite+React+TS, react-leaflet)  ──HTTP/JSON──▶  Orchestration API (FastAPI, uv; owns the model)
                                                              │  writes a job + spawns the worker
                                                              ▼
                                                  CLIMADA worker (project-local conda env)
```

The orchestration backend imports **no** geospatial / CLIMADA code (GPL-3.0 boundary); it invokes the
worker as a separate process over a JSON file contract (`src/climaterisk/engines/base.py`). The
worker lives in a **project-local conda env** (`./.climada-env`) and never imports the backend
package. See `docs/ARCHITECTURE.md` and the algorithm/method notes in `docs/`.

## Setup

```bash
# 1. Backend (uv) + frontend (npm) deps
uv sync --all-extras
npm --prefix frontend/climaterisk install

# 2. CLIMADA worker — a project-local conda env (never a global/named env).
#    Needs conda/mamba (the GDAL/PROJ/rasterio stack is not pip-installable).
conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./.climada-env
./.climada-env/bin/python -c "import climada; print('climada ok')"
```

The backend finds the worker interpreter via `CLIMATERISK_WORKER_ENV_DIR` (default `.climada-env`);
all paths and ports live in `.env` / `config.py` (copy `.env.example` → `.env` to override).

## Run the app

```bash
./run.command          # macOS/Linux: backend (uvicorn) + frontend (Vite); checks the worker env, opens the app
```

On Windows, double-click `run.bat` (or run `powershell -ExecutionPolicy Bypass -File run.ps1`
from a terminal) — same behavior: stops stale servers, installs missing deps, starts both
servers, waits for the backend health check, then opens the app. On Windows the worker env
check looks for `.climada-env\python.exe`.

Backend defaults to `http://127.0.0.1:8099`, frontend to `http://localhost:5174`.

## How hazard data is managed

CLIMADA needs `Hazard` / `Exposures` objects, not raw downloads — so the platform has an **import
layer** that converts each source into a CLIMADA-ready hazard and files it in a **local catalog**.

- **Local catalog** — `data/hazard_db/` with a `catalog.json` manifest keyed by
  `(peril, climate_scenario, region, year)`. Physical runners resolve hazards from here **first** and
  fall back to the live CLIMADA Data API. Add data with `scripts/build_hazard.py` or the UI's
  *Data → Fetch & ingest* — both write the HDF5 **and** register it, so the matching runner picks it
  up automatically (no manual wiring).
- **Importers** (`worker/climaterisk_worker/ingest.py`) — one refiner per source. Wired today:
  CLIMADA Data API (tropical cyclone, river flood, wildfire, earthquake), WRI Aqueduct (river and
  coastal flood), Copernicus DEM (for TC surge) and IBTrACS TCTracks; a TCRain refiner exists in
  the worker but is not yet accepted by the API. New formats map onto the standardized-grid
  on-ramp (`hazard_convert.py`).
- **CLIMADA's own cache** — `~/climada/data/` (managed by CLIMADA). The only manual drop-in is the
  GPW population raster for LitPop (login-gated). See `assets/libraries/data_sources.json`.

## Reproduce the analyses from a fresh clone

Everything needed is public and scripted except the login-gated Korean files. Seeds are pinned
(`--seed 42`), so the numbers come out identical. Times are rough, on a laptop with a normal
connection.

```bash
# 0. Environments (once; ~15 min, several GB for the conda env) — see Setup above
uv sync --all-extras && npm --prefix frontend/climaterisk install
conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./.climada-env

# 1. Observed climate for the heat perils: E-OBS daily Tmax, 0.25° ensemble mean (843 MB, no login)
mkdir -p ~/climada/data
curl -sSfL -o ~/climada/data/tx_ens_mean_0.25deg_reg_v31.0e.nc \
  https://knmi-ecad-assets-prd.s3.amazonaws.com/ensembles/data/Grid_0.25deg_reg_ensemble/tx_ens_mean_0.25deg_reg_v31.0e.nc

# 2. Country boundaries + population (both login-free). Either use the app's Data tab
#    (Natural Earth 110m, WorldPop) or fetch them directly. The Natural Earth layer is what
#    `--register` uses to clip a country's grid to its land — without it the clip silently
#    falls back to the bounding box (neighbouring countries' cells leak in).
mkdir -p data/downloads && curl -sSfL -o data/downloads/ne_110m_admin_0_countries.geojson \
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson
for iso in ESP KOR; do lo=$(echo $iso | tr A-Z a-z); \
  curl -sSfL -o ~/climada/data/${lo}_ppp_2020_1km_Aggregated.tif \
  https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2020/${iso}/${lo}_ppp_2020_1km_Aggregated.tif; done

# 3. Spain heat mortality, one run (~4 min, reads E-OBS): files the observed hazard
#    (827 cells × 45 summers) in the local catalog, runs the synthetic reference-city ensemble,
#    and attributes the 2020→2050 increase to warming (+1.5 °C) vs ageing. Then the poster figure.
./.climada-env/bin/python scripts/heatwave_europe.py --country ESP --seasons 300 --seed 42 \
  --register --decompose --warming-c 1.5 --demography-year 2050
./.climada-env/bin/python scripts/heatwave_poster_figure.py --recompute            # docs/poster figure
#    (Run them in this order and as one command: every heatwave_europe.py run rewrites
#     data/heatwave_europe/results.json, which the figure script reads.)

# 4. Run everything through the platform
./run.command      # then: Map → place assets (or "Modeled exposure" → population) → Results → Run analysis
```

Check your setup against these numbers (a `heat_mortality` run, RCP4.5 / 2020, two point assets
with 1,000 people each, ~90 s including the worker start-up):

| Asset | Expected annual heat-attributable deaths | per 100,000 |
|---|---:|---:|
| Madrid (40.42 N, 3.70 W) | 0.085 | 8.5 |
| Zamora (41.50 N, 5.75 W) | 0.167 | 16.7 |

Return periods stop at 22.5 years (half of the 45-summer record). These came out identical on a
fresh clone with a freshly built worker env (2026-09-04).

What each step reproduces:

| Result | Where it comes from | Deterministic? |
|---|---|---|
| Spain heat mortality on observed summers (3,522 deaths/yr, 2022 ranked first) | step 3, `--register`, then a `heat_mortality` run in the app on a population exposure | yes (observed input) |
| Reference-city figure, comfort bands, 2020→2050 warming/ageing split (baseline 2,117 → 6,065 deaths/yr: +1,620 warming, +1,318 ageing, +1,010 joint) | step 3, `--decompose --warming-c 1.5 --demography-year 2050` + `heatwave_poster_figure.py` | yes (seed 42) |
| Korean typhoon / river flood / wildfire / earthquake runs | no step needed — runners fetch from the CLIMADA Data API when the local catalog has no entry (CLIMADA keeps its own download cache under `~/climada/data/`); to run offline, pre-cache into `data/hazard_db/` via `scripts/build_hazard.py cache …` or the Data tab | yes |
| Supply-chain (WIOD16, ~900 MB), uncertainty, cost-benefit, finance, transition | first run downloads what it needs | yes (seeded) |

Not reproducible without a human step (all free, but gated):

| Data | Gate | Where it plugs in |
|---|---|---|
| KMA 남한상세 1 km scenarios (Korean heat) | 기후변화 상황지도 account | drop under `~/climada/data/kma/`, then `scripts/heat_korea.py register` — file list in `docs/KMA_DOWNLOAD_LIST.md` |
| GPW v4 population (LitPop) | NASA Earthdata login | `~/climada/data/` — WorldPop above is the login-free alternative |
| 재해연보 loss statistics, KOSIS age structure | 공공데이터포털 / KOSIS API key | vulnerability calibration (`docs/RISK_REGISTER.md` §C) |
| 홍수위험지도 SHP (4.9 GB) | none, but non-commercial licence | `scripts/fetch_floodmap_kor.py` |

Run results themselves (`data/app.db`, `data/runs/`) are not committed: re-run them; they come out the same.

## Develop & test

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest

# Engine + heat-peril tests (need CLIMADA — run in the worker env; backend-only tests skip themselves there):
./.climada-env/bin/python -m pytest tests/test_physical_regression.py tests/test_heat_mortality.py tests/test_kma_scenario.py
```

The backend suite runs CLIMADA-free (the engine regression is auto-skipped); it is pinned against a
captured CLIMADA baseline and run in the worker env.

## Status

Asset-level **physical risk** (CLIMADA — 15 perils incl. TC + surge + rainfall, river/coastal
flood, wildfire, earthquake, European windstorm, heat mortality; cost-benefit, **Sobol**
uncertainty, ECMWF TC forecast, 7 exposure sources, EM-DAT calibration runner) and
**transition risk** (NGFS Phase-5 carbon-cost passthrough) plus the TCFD/ISSB report and the
finance CRP view are working end-to-end — the capability-by-capability record (each with the
implementing module) is **[docs/CLIMADA_COVERAGE.md](docs/CLIMADA_COVERAGE.md)**, audited
against the code 2026-09-06.

What each peril computes, and with which vulnerability defaults:
**[docs/METHODOLOGY.md](docs/METHODOLOGY.md)**. Which of that is CLIMADA-native, which is
platform-custom, and which is missing — per peril and per component, with a doc-vs-code
consistency audit: **[docs/CLIMADA_METHODS.md](docs/CLIMADA_METHODS.md)** (2026-09-07). Known methodology gaps (uncalibrated
defaults, missing bands, sub-peril combination) with severity and fixes:
**[docs/GAP_ANALYSIS_KO.md](docs/GAP_ANALYSIS_KO.md)** (한국어). Korea data localization
plan and source licences: **[docs/RISK_REGISTER.md](docs/RISK_REGISTER.md)**.


## Conference poster

A0 poster on heat mortality as a CLIMADA peril (Spain, MoMo-validated) and the platform's other perils: https://sojin-droid.github.io/climaterisk/poster/ — source under `docs/poster/`, figure regenerated by `scripts/heatwave_poster_figure.py`.
