# climaterisk — user guide

How to install, start and use the platform, tab by tab, plus the command-line tools. For the
science behind each number see `METHODOLOGY.md`; for the HTTP API see `API.md`; for a fresh-clone
reproduction of the published analyses see the README ("Reproduce the analyses from a fresh clone").

## 1. What you need

| Requirement | Why | Install |
|---|---|---|
| Python 3.11+ and `uv` | orchestration backend | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (Windows: `winget install astral-sh.uv`) |
| Node.js 18+ and `npm` | frontend | nodejs.org (Windows: `winget install OpenJS.NodeJS.LTS`) |
| conda or mamba | CLIMADA worker (GDAL/PROJ stack, not pip-installable) | miniforge / miniconda |
| ~10 GB disk, internet on first runs | conda env (~1.6 GB), hazard caches, optional 0.9 GB E-OBS / 0.9 GB MRIOT | — |

No accounts or API keys are needed for the default workflow. Optional, login-gated inputs are listed in §7.

## 2. Install (once)

```bash
git clone https://github.com/sojin-droid/climaterisk.git && cd climaterisk
uv sync --all-extras                                  # backend
npm --prefix frontend/climaterisk install             # frontend
conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./.climada-env   # worker, ~20–25 min
cp .env.example .env                                  # optional: ports, paths
```

## 3. Start

```bash
./run.command        # macOS/Linux — starts backend (8099) + frontend (5174), opens the browser
```

Windows: double-click `run.bat`. Both launchers stop stale servers, install missing dependencies,
wait for the backend health check and open `http://localhost:5174`. Stop with Ctrl-C.

**As a macOS app.** `./scripts/make_macos_app.command` builds `Physical Risk.app` into
`/Applications` (or `~/Applications` when that is not writable). It is a stay-open AppleScript
applet wrapping the same launcher: clicking the icon starts the backend and the frontend and
opens the UI, and quitting the app — Cmd-Q, or the Dock icon's Quit — shuts both down. Output
goes to `~/Library/Logs/PhysicalRisk.log` instead of a terminal.

`--name` sets the label the Dock and Spotlight show (`--name "CLIMADA (physical risk tool)"`);
`--dest` sets where the bundle is written. The bundle identifier stays
`institute.planit.climaterisk` whatever the label is, so a rename does not read as a different
app to macOS.

Two constraints come from macOS itself, not from this project:

- The app records the absolute path of the checkout it was built from. Move the repository →
  rebuild the app.
- Keep the checkout out of `~/Downloads`, `~/Desktop` and `~/Documents`. Those are
  privacy-protected: a terminal you have already granted access to can read them, a freshly
  built app cannot, and the servers fail with `Operation not permitted` (`uv_cwd` / `.env`).
  `~/Developer/climaterisk` or any other unprotected folder works. Granting the app Full Disk
  Access in System Settings is the alternative, but it has to be repeated per machine.

The icon is generated from `assets/app_icon.png` — replace that file and rebuild to change it.

If the CLIMADA worker env is missing the app still starts; physical-risk runs then fail with an
explicit message telling you to build the env.

## 4. Analyse a portfolio (the tabs, left to right)

**Map** — build the exposure.
- Click the map to place a facility (a *point* asset). Select it to set name, sector, value,
  currency, and **headcount** (people on site — the exposure for the heat-mortality peril).
- *Polygon* / *Line* draw footprint assets; rivers-adjacent sites should be footprints so a flood
  layer can intersect them.
- **Modeled exposure** replaces hand-placed assets with a whole country's grid: *Reference-city
  population* (no data needed), *WorldPop raster* (free download), *LitPop* (needs the GPW raster,
  login-gated). Pick the country code, then *Model exposure*.
- **Map layer (preview)** colours the map with a peril's hazard footprint before any run, so you can
  see what data exists where.
- *Search location* finds a place by name.

**Scenarios** — pick the climate pathway (RCP2.6/4.5/6.0/8.5, mapped to SSP where a source needs
it), the transition pathway (NGFS, e.g. Net Zero 2050) and anchor years (2030–2090; runners use the
latest year, snapped to what the hazard source offers). Every result is a *scenario result*, not a
forecast. Two vulnerability-default selectors sit here: **TC v½** (regional Eberenz preset by
country — default; indicative class value; or a persisted calibration) and **flood-family curve**
(regional JRC preset by continent — default; or the generic class curve).

**Vuln** — per-asset vulnerability class overrides (damage curves). With no override, an asset gets
the bundled *regional* preset for its country (Eberenz 2021 for TC, JRC 2017 for floods) when one
lists the country, otherwise the generic class curve; the result card's detail line says which. An
override you set here always wins. See §7 for calibrating on national loss data.

**Results** — choose perils, press **Run analysis**. Each peril card shows expected annual impact,
present→future delta, a per-asset map, the return-period curve and warning bands. Return periods stop
at half the length of the underlying record instead of extrapolating. Monetary perils (typhoon,
flood, wildfire, earthquake, windstorm, hail, surge, landslide) aggregate to the currency total;
non-monetary ones (heat mortality in deaths, heatwave/drought in productivity) are reported in their
own units and **excluded** from currency totals. Export CSV / GeoJSON / PDF report from the top bar.
Results are restored after a page reload.

**Adapt** — cost-benefit of adaptation measures (retrofit, drainage, insurance) against the run's
physical risk, with NPV and benefit/cost ratios at a stated discount rate.

**Finance** — the financial chain: baseline vs stressed EBITDA, DSCR, rating threshold, credit risk
premium and NPV loss, driven by the physical and transition results.

**Supply** — indirect losses through input-output supply chains (WIOD16 Leontief); the first run
downloads the ~900 MB MRIOT table.

**Forecast** — near-term hazard forecasts (requires external forecast feeds; may be unavailable).

**Data** — the local hazard catalog: what is cached, fetch open data sources (Natural Earth,
WorldPop, …), ingest hazards (CLIMADA Data API, WRI Aqueduct) into the catalog.

**Method** — how each peril is modelled, with the method figure where one exists.

## 5. Typical workflows

| Goal | Steps |
|---|---|
| Physical risk for a few sites | Map: place assets, set value → Scenarios: RCP4.5, 2040 → Results: tick typhoon/flood/wildfire/earthquake → Run |
| Heat mortality for a country | Data or CLI: register a heat hazard (README step 3 for Spain; `scripts/heat_korea.py register` for Korea) → Map: *Modeled exposure → Reference-city population* or *WorldPop* → Results: `heat_mortality` → Run |
| Portfolio to credit metrics | After a physical run: Finance tab → set debt/EBITDA → read DSCR, rating, NPV loss |
| Which input drives uncertainty | Results → *Uncertainty* (Sobol) — vulnerability vs exposure vs hazard shares |
| Is a retrofit worth it | Adapt → choose measures, cost, discount rate → B/C and NPV |
| Batch many assets | Upload via the API (`PUT /api/session/{id}` with the asset list) then `POST /api/session/{id}/run`; see `API.md` |

## 6. Command-line tools (run in the worker env)

```bash
P=./.climada-env/bin/python
$P scripts/build_hazard.py list                                  # what is in the local catalog
$P scripts/build_hazard.py cache --data-type tropical_cyclone --peril tropical_cyclone \
     --scenario rcp45 --region KOR --year 2040 \
     --props '{"spatial_coverage":"country","country_iso3alpha":"KOR","climate_scenario":"rcp45","ref_year":"2040"}'
                                                                 # pre-cache a Data API hazard (props = Data API filters)
$P scripts/heatwave_europe.py --country ESP --seasons 300 --seed 42 \
     --register --decompose --warming-c 1.5 --demography-year 2050   # Spain heat: hazard + figure inputs
$P scripts/heatwave_poster_figure.py --recompute                 # poster figure
$P scripts/heat_korea.py files|register|check|run                # Korea heat from KMA 1 km scenarios
python3 scripts/fetch_floodmap_kor.py                            # 환경부 flood-risk SHP (no login; non-commercial licence)
```

## 7. Data: what downloads itself, what needs you

| Data | Access | Where it goes |
|---|---|---|
| CLIMADA hazards (typhoon, river flood, wildfire, earthquake) | automatic from the CLIMADA Data API on first run | `data/hazard_db/` |
| Natural Earth boundaries, WorldPop population | free, Data tab or `curl` (README) | `data/downloads/`, `~/climada/data/` |
| E-OBS daily Tmax (Europe heat) | free, 0.9 GB `curl` (README) | `~/climada/data/` |
| WIOD16 MRIOT (supply chain) | automatic, ~900 MB | CLIMADA cache |
| KMA 남한상세 1 km scenarios (Korea heat) | **account** at 기후변화 상황지도 — see `KMA_DOWNLOAD_LIST.md` | `~/climada/data/kma/` |
| GPW v4 population (LitPop) | **NASA Earthdata login** | `~/climada/data/` |
| Korean loss statistics (재해연보), KOSIS age structure | **API key** — intended for vulnerability calibration; the current `POST /api/session/{id}/calibration` reads only an EM-DAT CSV (`CLIMATERISK_EMDAT_PATH`), so a 재해연보 loader is still to be written | (not wired yet) |

Vulnerability curves are global defaults until calibrated on national loss records; results for a
new country should be read as *relative* until then (`RISK_REGISTER.md` §C).

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "worker env not found" on a physical run | build `./.climada-env` (§2) or set `CLIMATERISK_WORKER_ENV_DIR` |
| `heat_mortality has no local hazard` | register one first (README step 3 or `heat_korea.py register`) |
| LitPop asks for GPW | login-gated; use *WorldPop raster* or *Reference-city population* instead |
| Result is 0 | read the card's interpretation: asset outside the hazard footprint vs below threshold vs no data |
| Results vanished after reload | they are per browser session; the app restores the latest runs of the current session — a new browser profile starts a new session |
| Forecast tab errors | external forecast feed unavailable (e.g. NASA endpoint 403); not needed for risk runs |
| Page frozen / cannot scroll | reload (Cmd+Shift+R); a dev-server error overlay blocks input until reload |
| Ports in use | set `CLIMATERISK_BACKEND_PORT` / `CLIMATERISK_FRONTEND_PORT` in `.env` |

## 9. Where to read next

`METHODOLOGY.md` (equations per peril) · `CLIMADA_COVERAGE.md` (which CLIMADA features are wired) ·
`ARCHITECTURE.md` (three-process design, GPL boundary) · `API.md` · `HEATWAVE_EUROPE.md` (heat
mortality in depth) · `KOREA_ASSET_MANAGER_GUIDE.md` · `RISK_REGISTER.md` (known limits and their
status) · `poster/` (conference poster).
