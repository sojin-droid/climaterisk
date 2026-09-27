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
    other/floodmap/                   환경부 홍수위험지도 (licence-restricted, see below)
  derived/
    hazard_db/                        local hazard catalogue: catalog.json + HDF5
    calibrations/                     persisted impact-function calibrations (next to hazard_db)
    municipality/                     derived municipality products (V0.3 office points)
  cache/                              regenerable caches
  evidence/                           large reproducibility artefacts

<CLIMADA data directory>              intentionally external — owned by CLIMADA (climada.conf);
                                      CLIMADA's default is ~/climada/data. Data API cache,
                                      LitPop / GPW / WorldPop / E-OBS / IBTrACS / OSM drop-ins.
                                      Override for this project: CLIMATERISK_CLIMADA_DATA_DIR.
```

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
python scripts/migrate_data_root.py                                   # plan: sizes + commands
python scripts/migrate_data_root.py --apply --method symlink --only kma
python scripts/migrate_data_root.py --apply --method copy --only hazard_db,municipality_sgis
```

`copy` duplicates, `symlink` points the canonical path at the legacy folder; neither deletes or
moves the original. An item assembled from several legacy parts (SGIS: the extracted folder and
the zip) becomes a real canonical folder of per-file links, so nothing is ever written into the
legacy tree; a step whose parent resolves into the legacy tree is refused. Re-running is a no-op.
The flood-map archives are never migrated by the tool.

### State on the Mac mini after migration (2026-09-27)

Applied with `--method symlink --only kma,municipality_sgis` and `--method copy --only hazard_db`;
the local (git-ignored) `.env` sets `CLIMATERISK_DATA_ROOT=~/Data/climaterisk` and
`CLIMATERISK_CLIMADA_DATA_DIR=~/climada/data`.

| canonical path | link type | resolves to | size | content check |
|---|---|---|---|---|
| `external/kma` | symlink (folder) | legacy `kma/` | 23.6 GB | 52 files: TA / TAMAX × MK-PRISM v3.1 (42), SSP245 (5), SSP585 (5) |
| `external/municipality/sgis` | real folder, 16 file symlinks | legacy `municipality_src/extracted/*` + zip | 0.43 GB | rebuild → 269 rows, byte-identical to `assets/libraries/korea_municipalities.csv` |
| `derived/hazard_db` | real copy | — | ≈60 MB | `diff -r` identical to the repo catalogue; now the served catalogue |
| `raw/`, `external/other/`, `cache/`, `evidence/` | empty folders | — | — | — |

Verified from `/tmp` in a clean shell: `.env` loading, both roots, KMA discovery (backend status
and `heat_korea.py files`), SGIS rebuild, catalogue, Data API cache (no new downloads), backend and
worker suites (the data root is byte-for-byte unchanged by the tests), both CLIs (asset fixture
30/30, municipality run 42/42 against the pre-migration run), browser E2E.

Legacy independence was checked with `HOME` pointed at an empty directory and only
`CLIMATERISK_DATA_ROOT` / `CLIMATERISK_CLIMADA_DATA_DIR` set: the project resolved everything
from the canonical root and the configured CLIMADA directory, with identical results. CLIMADA the
library locates its own directory through `climada.conf` (searched in `~/climada/conf`,
`~/.config` and the working directory), so relocating CLIMADA needs a `climada.conf` with
`local_data.system` and the `data_api` cache paths — `CLIMATERISK_CLIMADA_DATA_DIR` only tells the
project where that directory is.

## Path inventory (measured 2026-09-27 on the Mac mini)

| path | type | current_location | canonical_location | source | required? | size | git-tracked? | migration_status |
|---|---|---|---|---|---|---|---|---|
| KMA 남한상세 TA / TAMAX (52 files) | B | `~/climada/data/kma` (physical) | `<DATA_ROOT>/external/kma` | 기후변화 상황지도 (login) | heat layer **rebuilds** and the 현황 card; runs use the baked catalogue | 23.6 GB | no | MIGRATED — folder symlink |
| SGIS 2025_2Q boundaries (zip + extracted) | B | `~/climada/data/municipality_src` (physical) | `<DATA_ROOT>/external/municipality/sgis` | data.go.kr 15129688 | only to rebuild `korea_municipalities.csv` | 0.43 GB | no | MIGRATED — real folder of file symlinks |
| investigation debris in `municipality_src` (`cj.txt`, `page.html`, `meta.json`, `codego_page.html`, `download.log`) | D | same | — | agent session files | no | < 1 MB | no | not migrated (obsolete) |
| 환경부 홍수위험지도 SHP 100/200/500-yr (254 zips; file list `docs/evidence/floodmap_legacy_inventory.csv`) | E (restricted) | `~/climada/data/floodmap` | `<DATA_ROOT>/external/other/floodmap` | data.floodmap.go.kr, externally downloaded 2026-09-02 18:37–18:48 by `fetch_floodmap_kor.py` | **no** — no production code reads it (only the fetch script, the path definition and status reporting reference the folder); 공공누리 제4유형 | 4.91 GB | no | PRESERVED IN PLACE — licence-sensitive; owner decides keep/delete; never migrated by the tool |
| local hazard catalogue | B | `<DATA_ROOT>/derived/hazard_db` (copy) | `<DATA_ROOT>/derived/hazard_db` | `build_hazard.py`, `heat_korea.py`, Data API cache | yes (KOR heat layers, cached RF/TC) | ≈60 MB | no | MIGRATED — copied; the repo-local `data/hazard_db` is kept, no longer served |
| calibrations | B | `<catalogue>/../calibrations` | `<DATA_ROOT>/derived/calibrations` | calibration runs | optional | 0 | no | follows the catalogue |
| app runtime state (`app.db`, `runs/`, `downloads/`, `heatwave_europe/`) | A | `<REPO>/data` | unchanged (`CLIMATERISK_DATA_DIR`) | the app | yes | 4 MB | no (ignored) | NONE NEEDED |
| CLIMADA data directory (Data API cache `hazard/` 5.9 GB, NatEarth centroids, IBTrACS, WorldPop ESP/KOR/JPN, E-OBS, OSM, MRIOT, …) | E | `~/climada/data` (minus the three project folders above) | unchanged — CLIMADA's own | CLIMADA / Data tab | yes | ≈ 8.8 GB | no | NONE — configurable via `CLIMATERISK_CLIMADA_DATA_DIR` |
| `assets/libraries/korea_municipalities.csv` | A | repo | unchanged | built from SGIS | yes | 80 KB | yes | NONE |
| `docs/evidence/*.csv` (V0.3 source investigation) | A | repo | unchanged | investigation | documentation | 108 KB | yes | NONE |
| `tests/fixtures/asset_level_validation/` | A | repo | unchanged | frozen evidence | tests | 84 KB | yes | NONE (untouched) |
| generated exports (Excel, JSON) | C | wherever `--output` points | caller-chosen | CLIs / API | — | — | no | NONE |
| `~/Downloads/climaterisk-main` | E | symlink → this checkout | — | earlier session path | no | 0 | — | harmless alias |
| `C:\Users\user\새 폴더\…` (Windows-era pipeline, V5 geocoder) | D | not on this machine | — | old PC | — | — | — | OBSOLETE — referenced only in historical docs |

Types: A repository-relative · B DATA_ROOT-relative · C environment/configurable · D obsolete ·
E intentionally external.

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
