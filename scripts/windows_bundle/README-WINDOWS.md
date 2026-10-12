# climaterisk — Windows bundle

No installation. Unzip, then double-click **Start climaterisk.bat**. A console window stays
open while the app runs; closing it stops the server. The browser opens at
`http://127.0.0.1:8099/`.

**Keep the folder in a short path**, e.g. `C:\climaterisk`. Windows limits paths to 260
characters and the bundled Python environment is deep; from a long path (Downloads,
OneDrive, Desktop with a long user name) the first start may fail. The launcher warns.

## What is in the folder

| folder / file | what it is |
|---|---|
| `Start climaterisk.bat`, `start.ps1` | the launcher (fixes up the environment on the first start, then starts the server) |
| `app\` | the program: backend, CLIMADA worker, methodology libraries, scripts, documentation |
| `ui\` | the web interface (built), served by the backend |
| `env\` | one Python environment with CLIMADA and the backend's dependencies (conda-pack) |
| `app\.env` | settings; created from `app\.env.example` on the first start |
| `app\BUNDLE.txt` | version and build information |

Runtime data (sessions, run results) goes to `app\data\`; large inputs and the local
hazard catalog go to the data root, `%USERPROFILE%\Data\climaterisk` by default
(`CLIMATERISK_DATA_ROOT` in `app\.env` to change it).

## First use

1. Start the app, open the **Data** tab and press **Check** under *Offline readiness*: it
   lists which hazard layers are cached locally and which files are missing.
2. While online, use **Fetch & ingest** for the hazards you need (tropical cyclone, river
   flood, …). The first fetch of a global set can take a while; later runs reuse the
   local catalog.
3. **Map** → place assets or **Import assets** from a template; **Models** → select
   municipalities or assets → **RUN ASSESSMENT** → **Download Excel**.

## Closed network (no internet)

Set `CLIMATERISK_OFFLINE=1` in `app\.env`. The app then uses only the local hazard catalog,
refuses downloads, and draws maps without basemap tiles (OpenStreetMap tiles cannot be
bundled). Prepare the data root while online — *Offline readiness* on the Data tab says
what to cache — or copy a prepared data root from another machine.

## Troubleshooting

- *The server did not answer* — a long path (see above), or port 8099 is taken: set
  `CLIMATERISK_BACKEND_PORT=8199` in `app\.env`.
- *A run says `engine_not_ready`* — `env\python.exe` could not import CLIMADA; the bundle is
  incomplete or was moved while running. Unzip again into a short path.
- Logs: the console window, and `app\data\runs\<run id>\worker.log` per run.

Documentation: `app\docs\USER_GUIDE.md` (한국어 요약: `USER_GUIDE_KO.md`), physical-risk
guide `app\docs\PHYSICAL_RISK_USER_GUIDE.md`, use tiers `app\docs\SCOPE.md`.
