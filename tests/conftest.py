"""Test fixtures — isolate runtime data into a temp dir and build a fresh app."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# Isolate the external data root before any test module is collected: test modules import the
# worker package at collection time, and its CLIMADA bootstrap writes the project-scoped
# climada.conf under <DATA_ROOT>/cache. The real ~/Data/climaterisk is never written by tests.
# CLIMATERISK_CLIMADA_DATA_DIR still resolves from .env, so tests read the real CLIMADA cache.
_TEST_DATA_ROOT = tempfile.mkdtemp(prefix="climaterisk-dataroot-")
atexit.register(shutil.rmtree, _TEST_DATA_ROOT, ignore_errors=True)
os.environ["CLIMATERISK_DATA_ROOT"] = _TEST_DATA_ROOT
_WORKER = str(Path(__file__).resolve().parents[1] / "worker")
if _WORKER not in sys.path:
    sys.path.insert(0, _WORKER)
# Tests load scripts as modules by file path; the scripts import their sibling helper
# ``_console`` by bare name (``python scripts/x.py`` puts scripts/ first on sys.path).
_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import climaterisk_worker  # noqa: E402,F401  (CLIMADA bootstrap; no-op without CLIMADA)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def _isolate_data(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point the data dir and the external data root at temp locations; clear singletons.

    ``CLIMATERISK_DATA_ROOT`` is isolated in **both** environments (backend and CLIMADA
    worker) so no test can write into the real ``~/Data/climaterisk``. Tests that read the
    real cached hazard layers still find them through the transitional repo-local
    catalogue (``climaterisk.paths.hazard_db_dir``). The backend data dir is isolated only
    when the backend package is importable.
    """
    os.environ["CLIMATERISK_DATA_ROOT"] = _TEST_DATA_ROOT
    try:
        from climaterisk.api.deps import get_session_store
        from climaterisk.config import get_settings
        from climaterisk.data.libraries import load_libraries
    except ModuleNotFoundError:
        yield
        return

    data_dir = tmp_path_factory.mktemp("climaterisk-data")
    os.environ["CLIMATERISK_DATA_DIR"] = str(data_dir)
    get_settings.cache_clear()
    get_session_store.cache_clear()
    load_libraries.cache_clear()
    yield


@pytest.fixture
def client(_isolate_data: None) -> TestClient:
    """A TestClient backed by a freshly built app (after data isolation).

    Imports are local so this conftest stays importable in the CLIMADA worker env
    (which has no FastAPI/backend deps) when running the engine regression test.
    """
    from fastapi.testclient import TestClient

    from climaterisk.api.main import create_app

    return TestClient(create_app())


#: Opt-in switch for the tests that read the real licensed 홍수위험지도 (never on by default).
REAL_MAPS_ENV = "CLIMATERISK_REAL_MAPS"


@pytest.fixture
def real_floodmaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real 홍수위험지도 folder, only when ``CLIMATERISK_REAL_MAPS=1``; else the test skips.

    The tests run under an isolated temporary data root, so the real root is resolved
    explicitly: ``CLIMATERISK_REAL_DATA_ROOT``, else the repo ``.env`` value of
    ``CLIMATERISK_DATA_ROOT``, else ``~/Data/climaterisk``. Only ``floodmap_dir`` is pointed
    there. The real index caches are **copied** into ``tmp_path`` (the real cache is never
    written); without them the test skips, since a fresh index would open every zip.
    Offline is forced so no DEM tile is downloaded.
    """
    import shutil

    if os.environ.get(REAL_MAPS_ENV, "").strip() != "1":
        pytest.skip(f"reads the real licensed 홍수위험지도: opt-in, set {REAL_MAPS_ENV}=1")
    from climaterisk import paths

    value = os.environ.get("CLIMATERISK_REAL_DATA_ROOT") or paths._dotenv(
        str(paths.env_file())
    ).get(paths.ENV_DATA_ROOT)
    real_root = paths._as_path(value) if value else paths.default_data_root()
    maps = real_root / "external" / "other" / "floodmap"
    if not maps.is_dir():
        pytest.skip(f"{REAL_MAPS_ENV}=1 but no 홍수위험지도 under {real_root}")
    cache = tmp_path / "cache"
    for name in ("rfm_index.json", "cfm_index.json"):
        src = real_root / "cache" / "floodmap" / name
        if not src.is_file():
            pytest.skip(f"no real index cache {src} (a fresh index would open every zip)")
        (cache / "floodmap").mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, cache / "floodmap" / name)
    monkeypatch.setattr(paths, "floodmap_dir", lambda: maps)
    monkeypatch.setattr(paths, "cache_dir", lambda *p: cache.joinpath(*p))
    monkeypatch.setenv("CLIMATERISK_OFFLINE", "1")
    return maps
