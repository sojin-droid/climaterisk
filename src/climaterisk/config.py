"""Centralized configuration loaded from environment / ``.env``.

All tunables live here (no hardcoded values scattered through the code, per
``CLAUDE.md``). Mirror every field into ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from this file: src/climaterisk/config.py -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings, populated from ``CLIMATERISK_*`` env vars or ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="CLIMATERISK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Backend
    backend_host: str = "127.0.0.1"
    backend_port: int = 8099

    # Frontend
    frontend_port: int = 5174

    # Storage (paths relative to the repo root unless absolute)
    data_dir: Path = Path("data")
    library_dir: Path = Path("assets/libraries")
    # Local hazard catalog (HDF5 + catalog.json). Defaults to ``<data_dir>/hazard_db``;
    # set CLIMATERISK_HAZARD_DB to relocate. The backend injects this into the worker
    # env on spawn, so both sides resolve the catalog to the same single location.
    hazard_db_dir: Path | None = None

    # CLIMADA worker (Phase 2).
    # The worker runs in a PROJECT-LOCAL conda env (a prefix env inside the repo) —
    # never a global/named conda env. ``worker_env_dir`` is that env's directory
    # (relative to the repo root unless absolute); build it with:
    #   conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./.climada-env
    worker_env_dir: Path = Path(".climada-env")
    worker_module: str = "climaterisk_worker.run_job"
    # Explicit override for the worker interpreter; if unset, derived from worker_env_dir.
    worker_python: str | None = None
    # Ceiling on concurrent worker subprocesses. CLIMADA runs are slow (each fetches
    # hazard data over ~a minute), so a single analyst routinely has several engines in
    # flight at once — keep this high enough that interactive use never hits it; it only
    # guards against a runaway script. The max_run_seconds cap reaps any stuck run.
    max_workers: int = Field(default=8, ge=1)
    # Wall-clock cap per worker run; exceeded runs are killed and reported as timed out
    # (guards against intractable jobs, e.g. a full-MRIO supply-chain solve).
    max_run_seconds: int = Field(default=900, ge=30)
    # Optional Copernicus DEM GeoTIFF (topography) for TC storm-surge (TCSurgeBathtub).
    # A manual drop-in; injected into the worker env as CLIMATERISK_DEM_PATH when set.
    dem_path: str | None = None
    # Optional EM-DAT disaster-loss CSV (login-gated) for impact-function calibration.
    # Injected into the worker env as CLIMATERISK_EMDAT_PATH when set.
    emdat_path: str | None = None

    # Open-data fetcher (Data tab → "Download here"): per-request HTTP read timeout.
    download_timeout_seconds: int = Field(default=180, ge=10)

    # Logging
    log_level: str = "INFO"

    def _abspath(self, p: Path) -> Path:
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def data_path(self) -> Path:
        """Absolute path to the runtime data directory (created on access)."""
        path = self._abspath(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sessions_path(self) -> Path:
        """Absolute path to the per-session storage directory."""
        path = self.data_path / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def library_path(self) -> Path:
        """Absolute path to the bundled methodology-library directory."""
        return self._abspath(self.library_dir)

    @property
    def runs_path(self) -> Path:
        """Absolute path to the per-run artifact directory (created on access)."""
        path = self.data_path / "runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def hazard_db_path(self) -> Path:
        """Absolute path to the local hazard catalog (``<data_dir>/hazard_db`` by default).

        Single source of truth for the catalog directory: the backend reads the
        manifest here and injects this path into the worker (``CLIMATERISK_HAZARD_DB``)
        so both processes agree even when ``data_dir`` is relocated.
        """
        if self.hazard_db_dir is not None:
            return self._abspath(self.hazard_db_dir)
        return self.data_path / "hazard_db"

    @property
    def worker_dir(self) -> Path:
        """Directory that is the import root for the ``climaterisk_worker`` package."""
        return REPO_ROOT / "worker"

    @property
    def worker_env_path(self) -> Path:
        """Absolute path to the project-local CLIMADA conda (prefix) env directory."""
        return self._abspath(self.worker_env_dir)

    def resolve_worker_python(self) -> str:
        """Return the CLIMADA worker's python interpreter.

        Always the PROJECT-LOCAL conda env — never a global/named conda env. The
        explicit ``worker_python`` setting wins if given; otherwise it is the
        interpreter inside ``worker_env_dir`` (``bin/python`` on POSIX,
        ``python.exe`` at the env root on Windows conda layouts).
        """
        if self.worker_python:
            return self.worker_python
        posix = self.worker_env_path / "bin" / "python"
        windows = self.worker_env_path / "python.exe"
        if not posix.exists() and windows.exists():
            return str(windows)
        return str(posix)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
