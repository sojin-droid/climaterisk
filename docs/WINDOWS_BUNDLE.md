# Windows bundle — build and ship a no-install package

The EIOPA CLIMADA-App is distributed as a zip with a frozen conda environment and an exe:
unzip into a short path, double-click. This is the same shape for climaterisk, built by
`scripts/make_windows_bundle.ps1` on a Windows machine that has the development stack.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_windows_bundle.ps1          # clones .climada-env
powershell -ExecutionPolicy Bypass -File scripts\make_windows_bundle.ps1 -FromYml # fresh env (~20 min)
```

Output: `build\climaterisk-win64-<version>.zip` (about 1 GB; `build\` is git-ignored).

## What the bundle is

| part | content | why |
|---|---|---|
| `env\` | one conda-pack environment: CLIMADA (+ petals) **and** the backend's dependencies from `pyproject.toml` | neither uv nor conda on the target; `conda-unpack` fixes the absolute paths on the first start |
| `app\` | `src`, `worker`, `assets`, `scripts`, `docs`, `pyproject.toml`, `.env.example`, `BUNDLE.txt` | the program, run with `PYTHONPATH=app\src` so `paths.REPO_ROOT` is `app\` (no pip install, hence relocatable) |
| `ui\` | `vite build` output | served by the backend (`CLIMATERISK_UI_DIR`; `StaticFiles` mounted after `/api`), so Node is a build-time dependency only |
| launcher | `Start climaterisk.bat` → `start.ps1` | first start runs `conda-unpack`, copies `.env.example` to `.env`, sets `CLIMATERISK_WORKER_PYTHON` to `env\python.exe` and `CLIMATERISK_UI_DIR` to `ui\`, starts uvicorn, waits for `/api/health`, opens the browser; closing the window stops the server |

Both processes — the FastAPI backend and the CLIMADA worker subprocess — run from the same
`env\python.exe`. The GPL boundary is unchanged: the backend package still imports no CLIMADA
code (`tests/test_gpl_boundary.py`); sharing an interpreter directory is a packaging choice,
not an import.

## Constraints, stated

- **Short path.** Windows limits paths to 260 characters; the env is deep (170+ characters
  under `env\` alone). The launcher warns above 60 characters. EIOPA's documentation carries
  the same warning.
- **Not an installer and not signed.** SmartScreen may ask once for the `.bat`. There is no
  Start-menu entry; the folder is the installation.
- **Map tiles stay online.** OpenStreetMap's tile policy does not allow bundling them;
  `CLIMATERISK_OFFLINE=1` draws maps without a basemap (docs/USER_GUIDE.md §7a).
- **Data is not in the zip.** Hazard layers, WorldPop, KMA archives live in the data root
  (`%USERPROFILE%\Data\climaterisk`); prepare it while online (Data tab → *Offline readiness*)
  or copy a prepared data root alongside the bundle.

## Smoke test after building

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_windows_bundle.ps1 -SkipZip
$env:CLIMATERISK_BACKEND_PORT = "8299"; & build\bundle\start.ps1      # opens the browser on 8299
```

`tests/test_ui_serving.py` checks the backend serves a built UI under the API and that the
launcher sets only settings the backend reads.
