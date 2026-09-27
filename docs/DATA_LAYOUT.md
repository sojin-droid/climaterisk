# Data layout — repository vs data root

The checkout and the data are separate and both are relocatable. One module decides every
location: [`src/climaterisk/paths.py`](../src/climaterisk/paths.py) (stdlib only, imported by the
backend, the CLIMADA worker, the scripts and the tests). No other code builds a data path.

```text
<REPO_ROOT>/                          any folder — found from the code, never from the cwd
  assets/libraries/                   small committed inputs (korea_municipalities.csv, configs)
  docs/evidence/                      small human-readable evidence tables (committed)
  tests/fixtures/                     frozen regression fixtures (committed)
  data/                               app runtime state: app.db, runs/, downloads/ (git-ignored)

<DATA_ROOT>/                          CLIMATERISK_DATA_ROOT, default ~/Data/climaterisk
  raw/                                untouched downloads with no better home
  external/
    kma/                              KMA 남한상세 archives            (CLIMATERISK_KMA_DIR overrides)
    municipality/
      sgis/                           SGIS boundary package (raw government download)
      source/                         official office-address sources (V0.3 input, raw)
      office/                         office layers built from them (V0.3)
    climada/                          CLIMADA's data directory (CLIMATERISK_CLIMADA_DATA_DIR):
                                      Data API cache hazard/ + .downloads.db + .apicache,
                                      MRIOT, OSM, E-OBS, IBTrACS, WorldPop, CLIMADA system files
    other/floodmap/                   환경부 홍수위험지도 (licence-restricted, see below)
  derived/
    hazard_db/                        local hazard catalogue: catalog.json + HDF5
    calibrations/                     persisted impact-function calibrations (next to hazard_db)
    municipality/                     derived municipality products (V0.3 office points)
  cache/                              regenerable caches
    climada_conf/climada.conf         project-scoped CLIMADA config (generated)
  evidence/                           large reproducibility artefacts
    migration/                        per-file sha256 manifests of the data migration
```

### CLIMADA's data directory

`CLIMATERISK_CLIMADA_DATA_DIR` names it (recommended `<DATA_ROOT>/external/climada`; unset →
CLIMADA's own default `~/climada/data`). CLIMADA has no environment variable for its location:
it reads `climada.conf` from its package, `~/climada/conf`, `~/.config` and the working directory
**at import time**, and derives every data path (Data API ledger and cache, MRIOT, OSM, …) from
`local_data.system`. So when the variable is set, `paths.write_climada_conf()` writes
`<DATA_ROOT>/cache/climada_conf/climada.conf` (`local_data.system` = the directory,
`local_data.demo` = `<dir>/demo`) and the worker package imports CLIMADA and climada_petals once
from that folder (`climaterisk_worker._bootstrap_climada`), then restores the working directory.
Every worker module, the worker subprocess, the scripts and the worker-env tests import the
package first. No user-global `climada.conf` is written — other projects on the machine keep
CLIMADA's default directory.

The Data API ledger (`.downloads.db`) stores **absolute** file paths; a copied cache without a
rewritten ledger is re-downloaded. The migration tool rewrites the copy's ledger (the legacy one
is untouched).

## Resolution rules

* Every path variable resolves **process environment → the repo's `.env` → default**, in every
  process. A value set only in `.env` therefore reaches the worker and the scripts too; the backend
  also injects the resolved `CLIMATERISK_DATA_ROOT`, `CLIMATERISK_KMA_DIR`,
  `CLIMATERISK_CLIMADA_DATA_DIR` and `CLIMATERISK_HAZARD_DB` into each worker it spawns.
* `~` is expanded; a relative value is anchored at the repo root, never at the working directory.
* `Settings` reads `<REPO_ROOT>/.env` by absolute path, so starting the backend or a script from
  any directory loads the same configuration.
* Legacy locations (the pre-2026-09 layout inside CLIMADA's directory) are **detected and reported,
  never read** — with one transitional exception: the hazard catalogue keeps serving from the
  repo-local `data/hazard_db` until `<DATA_ROOT>/derived/hazard_db/catalog.json` exists
  (`paths.status()` reports `LEGACY_REPO_LOCATION`).
* Missing optional data never raises: `paths.status()` and `GET /api/status/korea` report
  `present` / `missing` / `LEGACY_LOCATION` with a migration hint.

## Migration (nothing moves by itself)

```bash
python scripts/migrate_data_root.py                                   # plan: free space, sizes, commands
python scripts/migrate_data_root.py --preflight --only kma            # sha256 inventory, no changes
python scripts/migrate_data_root.py --apply --method clone --only kma,municipality_sgis,climada
python scripts/migrate_data_root.py --verify --only kma               # re-check canonical vs legacy
```

Methods: `clone` (APFS `cp -c`: real, independent files that share storage blocks until either
side changes — falls back to `copy` elsewhere), `copy`, `symlink`. None deletes or moves the
original. A physical method replaces a symlink left by an earlier `symlink` run only after the
physical copy's sha256 matches the source, and writes a manifest (path, bytes, sha256, match) to
`<DATA_ROOT>/evidence/migration/`. The `climada` item copies only what this application reads
(`CLIMADA_INCLUDE` in the script; left behind: `rsmc/` — not read by any code — CLIMADA's empty
on-demand folders, and the project folders that have their own items) and rewrites the copied
Data API ledger. A step whose parent resolves into the legacy tree is refused; re-running
re-verifies and changes nothing. The flood-map archives are never migrated by the tool.

### State on the Mac mini after the physical migration (2026-09-27)

Applied with `--method clone --only kma,municipality_sgis,climada` (the earlier `symlink` state was
replaced after verification) and `--method copy --only hazard_db`. The local (git-ignored) `.env`
sets `CLIMATERISK_DATA_ROOT=~/Data/climaterisk` and
`CLIMATERISK_CLIMADA_DATA_DIR=~/Data/climaterisk/external/climada`.

| canonical path | storage | files | size | content check |
|---|---|---|---|---|
| `external/kma` | physical (APFS clone) | 52 | 23.6 GB | sha256 = legacy for all 52: TA / TAMAX × MK-PRISM v3.1 (42), SSP245 (5), SSP585 (5) |
| `external/municipality/sgis` | physical (APFS clone) | 14 (13 extracted + zip) | 0.43 GB | sha256 = legacy; rebuild → 269 rows, byte-identical to `assets/libraries/korea_municipalities.csv` |
| `external/climada` | physical (APFS clone) | 124 | 8.8 GB | sha256 = legacy (ledger: 16 rows rewritten, all resolve to existing canonical files) |
| `derived/hazard_db` | physical copy | — | ≈60 MB | identical to the repo catalogue; the served catalogue |
| `cache/climada_conf/` | generated | 1 | < 1 KB | `local_data.system` = `external/climada` |

`find ~/Data/climaterisk -type l` → nothing. Legacy folders under `~/climada/data` are kept
untouched as backup (the tree was compared entry by entry before and after: unchanged).

Verified from `/tmp` in a clean shell with the legacy `~/climada` folder **temporarily renamed
away** (restored afterwards): CLIMADA's `SYSTEM_DIR`, Data API ledger, API cache and MRIOT resolve
inside `external/climada`; backend and worker suites (same results as before; tests add nothing
to the data root); asset fixture 30/30 and municipality run 42/42 against the frozen results;
SGIS rebuild byte-identical; KMA discovery from `external/kma`; browser E2E (municipality run +
Excel) after a backend restart; no Data API hazard download (ledger 16 → 16 rows, 16 hazard files;
only the small `.apicache` metadata entries are refreshed online, as before). `~/climada` was not
recreated. A second run with `HOME` pointed at an empty folder gave identical results and created
only third-party caches (matplotlib font list, cartopy) — no `climada` folder.

## Path inventory (measured 2026-09-27 on the Mac mini)

| path | type | current_location | canonical_location | source | required? | size | git-tracked? | migration_status |
|---|---|---|---|---|---|---|---|---|
| KMA 남한상세 TA / TAMAX (52 files) | B | `<DATA_ROOT>/external/kma` (physical); legacy backup `~/climada/data/kma` | `<DATA_ROOT>/external/kma` | 기후변화 상황지도 (login) | heat layer **rebuilds** and the 현황 card; runs use the baked catalogue | 23.6 GB | no | MIGRATED — physical (APFS clone), sha256-verified |
| SGIS 2025_2Q boundaries (zip + 13 extracted files) | B | `<DATA_ROOT>/external/municipality/sgis` (physical); legacy backup `~/climada/data/municipality_src` | `<DATA_ROOT>/external/municipality/sgis` | data.go.kr 15129688 | only to rebuild `korea_municipalities.csv` | 0.43 GB | no | MIGRATED — physical (APFS clone), sha256-verified |
| investigation debris in `municipality_src` (`cj.txt`, `page.html`, `meta.json`, `codego_page.html`, `download.log`) | D | same | — | agent session files | no | < 1 MB | no | not migrated (obsolete) |
| 환경부 홍수위험지도 SHP 100/200/500-yr (254 zips; file list `docs/evidence/floodmap_legacy_inventory.csv`) | E (restricted) | `~/climada/data/floodmap` | `<DATA_ROOT>/external/other/floodmap` | data.floodmap.go.kr, externally downloaded 2026-09-02 18:37–18:48 by `fetch_floodmap_kor.py` | **no** — no production code reads it (only the fetch script, the path definition and status reporting reference the folder); 공공누리 제4유형 | 4.91 GB | no | NON-RUNTIME ARCHIVAL — preserved in place at the legacy path, not migrated (licence-sensitive; owner decides keep/delete) |
| local hazard catalogue | B | `<DATA_ROOT>/derived/hazard_db` (copy) | `<DATA_ROOT>/derived/hazard_db` | `build_hazard.py`, `heat_korea.py`, Data API cache | yes (KOR heat layers, cached RF/TC) | ≈60 MB | no | MIGRATED — copied; the repo-local `data/hazard_db` is kept, no longer served |
| calibrations | B | `<catalogue>/../calibrations` | `<DATA_ROOT>/derived/calibrations` | calibration runs | optional | 0 | no | follows the catalogue |
| app runtime state (`app.db`, `runs/`, `downloads/`, `heatwave_europe/`) | A | `<REPO>/data` | unchanged (`CLIMATERISK_DATA_DIR`) | the app | yes | 4 MB | no (ignored) | NONE NEEDED |
| CLIMADA data directory — the parts this application reads (Data API cache `hazard/` 5.9 GB + ledger + `.apicache`, MRIOT, OSM, E-OBS, IBTrACS, WorldPop ESP/KOR/JPN, CLIMADA system files) | B | `<DATA_ROOT>/external/climada` (physical); legacy backup `~/climada/data` | `<DATA_ROOT>/external/climada` (`CLIMATERISK_CLIMADA_DATA_DIR`) | CLIMADA / Data tab | yes | 8.8 GB | no | MIGRATED — physical (APFS clone), sha256-verified, ledger rewritten; `rsmc/` (unused) left in the legacy folder |
| `assets/libraries/korea_municipalities.csv` | A | repo | unchanged | built from SGIS | yes | 80 KB | yes | NONE |
| `docs/evidence/*.csv` (V0.3 source investigation) | A | repo | unchanged | investigation | documentation | 108 KB | yes | NONE |
| `tests/fixtures/asset_level_validation/` | A | repo | unchanged | frozen evidence | tests | 84 KB | yes | NONE (untouched) |
| generated exports (Excel, JSON) | C | wherever `--output` points | caller-chosen | CLIs / API | — | — | no | NONE |
| `~/Downloads/climaterisk-main` | E | symlink → this checkout | — | earlier session path | no | 0 | — | harmless alias |
| `C:\Users\user\새 폴더\…` (Windows-era pipeline, V5 geocoder) | D | not on this machine | — | old PC | — | — | — | OBSOLETE — referenced only in historical docs |

Types: A repository-relative · B DATA_ROOT-relative · C environment/configurable · D obsolete ·
E external / restricted.

## Documentation that still names the old layout

Operational instructions (README, USER_GUIDE, USER_GUIDE_KO, KMA_DOWNLOAD_LIST, HEATWAVE_EUROPE,
DATA_SOURCES) were updated to the layout above. Investigation and audit records —
`KOREA_1KM_IMPLEMENTATION_GAP.md`, `KOREA_1KM_PHYSICAL_RISK_COVERAGE.md`, `RISK_REGISTER.md`,
`GAP_ANALYSIS_KO.md`, `OBSERVED_LOSSES_KR_SPEC.md`, `korea-hazard-replacement.md`,
`CLIMADA_METHODS.md` and the V0.3 sections of `municipality-physical-risk.md` — keep the paths they
recorded at the time; they describe where data *was*, not where the code looks.

## V0.3 preparation

The future official-office feature reads and writes only:

```text
<DATA_ROOT>/external/municipality/source/   official office-address sources (raw)
<DATA_ROOT>/external/municipality/office/   office layers built from them
<DATA_ROOT>/derived/municipality/           derived office points
```

and records per row: `office_source`, `office_source_date`, `coordinate_source`,
`coordinate_method` (`GOVERNMENT_PUBLISHED` | `ADDRESS_GEOCODING`). It is not implemented: no
approved, storable coordinate source exists yet (`municipality-physical-risk.md` §9).
