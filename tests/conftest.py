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
