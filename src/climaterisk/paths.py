"""The one place that decides where the project's files live — stdlib only.

Imported by the backend, the CLIMADA worker (GPL boundary: no pydantic here), the scripts and
the tests, so every process resolves the same locations. Nothing else in the code base may
build its own data-root path.

Two roots:

* ``REPO_ROOT`` — this checkout, found from this file's location. The repository is
  relocatable; nothing assumes it sits in a particular folder or that the working directory
  is the repo root.
* ``data_root()`` — large external inputs, derived products and caches that never belong in
  Git. ``CLIMATERISK_DATA_ROOT``; default ``~/Data/climaterisk``::

      <DATA_ROOT>/
        raw/                    untouched downloads that have no better home
        external/
          kma/                  KMA 남한상세 archives (CLIMATERISK_KMA_DIR overrides)
          municipality/
            sgis/               SGIS boundary packages (raw government downloads)
            source/             official office-address sources (raw)
            office/             office layers built from them
            derived/            (reserved)
          other/                anything else external (e.g. floodmap/)
        derived/                processed datasets, baked catalogues (hazard_db/), model files
          municipality/         derived municipality products (V0.3 office points)
        cache/                  regenerable caches
        evidence/               large reproducibility artefacts (small tables stay in docs/evidence)

CLIMADA's own system data directory (Data API cache, LitPop / GPW / WorldPop / E-OBS / OSM /
MRIOT inputs) is named by ``climada_data_dir()`` alone: ``CLIMATERISK_CLIMADA_DATA_DIR``
(recommended ``<DATA_ROOT>/external/climada``), else CLIMADA's default. When the variable is
set, the worker relocates the CLIMADA library there through a project-scoped ``climada.conf``
(:func:`write_climada_conf`) — never through the user's global CLIMADA configuration.

Resolution rule for every path variable: process environment → the repo's ``.env`` →
default. The same rule in every process, so a value set only in ``.env`` reaches the worker
and the scripts too.

Legacy locations (the pre-2026-09 layout under CLIMADA's data directory, and the repo-local
``data/hazard_db``) are *detected* for migration reporting and never read for KMA or
municipality data. The only transitional read is the hazard catalogue: while
``<DATA_ROOT>/derived/hazard_db`` has no ``catalog.json`` yet, an existing repo-local
catalogue keeps serving (status ``LEGACY_REPO_LOCATION``) until it is migrated.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

#: This checkout: src/climaterisk/paths.py → parents[2].
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

#: Environment variables this module owns.
ENV_DATA_ROOT = "CLIMATERISK_DATA_ROOT"
ENV_KMA_DIR = "CLIMATERISK_KMA_DIR"
ENV_CLIMADA_DATA_DIR = "CLIMATERISK_CLIMADA_DATA_DIR"
ENV_HAZARD_DB = "CLIMATERISK_HAZARD_DB"

#: Names handed to a spawned worker so it resolves the same locations as its parent.
WORKER_ENV_VARS: tuple[str, ...] = (ENV_DATA_ROOT, ENV_KMA_DIR, ENV_CLIMADA_DATA_DIR)


# --------------------------------------------------------------------------- #
# Environment                                                                  #
# --------------------------------------------------------------------------- #
def env_file() -> Path:
    """The repo's ``.env`` — found from ``REPO_ROOT``, never from the working directory."""
    return REPO_ROOT / ".env"


@lru_cache(maxsize=4)
def _dotenv(path: str) -> dict[str, str]:
    """``KEY=VALUE`` lines of a ``.env`` (comments and blanks skipped, quotes stripped)."""
    p = Path(path)
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def setting(name: str) -> str | None:
    """A path setting: process environment → ``.env`` → None. Empty values count as unset."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        value = _dotenv(str(env_file())).get(name)
    return value.strip() if value and value.strip() else None


def _as_path(value: str) -> Path:
    """``~`` expanded; a relative value is taken relative to ``REPO_ROOT``, not the cwd."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


def clear_cache() -> None:
    """Forget the parsed ``.env`` (tests that swap it)."""
    _dotenv.cache_clear()


# --------------------------------------------------------------------------- #
# Roots                                                                        #
# --------------------------------------------------------------------------- #
def default_data_root() -> Path:
    """``~/Data/climaterisk`` — the only place the default is written."""
    return Path.home() / "Data" / "climaterisk"


def data_root() -> Path:
    """``CLIMATERISK_DATA_ROOT`` (env → .env) or ``~/Data/climaterisk``. Not created here."""
    value = setting(ENV_DATA_ROOT)
    return _as_path(value) if value else default_data_root()


def raw_dir() -> Path:
    return data_root() / "raw"


def external_dir(*parts: str) -> Path:
    return data_root().joinpath("external", *parts)


def derived_dir(*parts: str) -> Path:
    return data_root().joinpath("derived", *parts)


def cache_dir(*parts: str) -> Path:
    return data_root().joinpath("cache", *parts)


def evidence_dir(*parts: str) -> Path:
    return data_root().joinpath("evidence", *parts)


# --------------------------------------------------------------------------- #
# Named locations                                                              #
# --------------------------------------------------------------------------- #
def kma_dir() -> Path:
    """KMA 남한상세 drop folder: ``CLIMATERISK_KMA_DIR`` or ``<DATA_ROOT>/external/kma``."""
    value = setting(ENV_KMA_DIR)
    return _as_path(value) if value else external_dir("kma")


def municipality_dir(*parts: str) -> Path:
    """``<DATA_ROOT>/external/municipality[/…]`` — raw government downloads (sgis/, source/)."""
    return external_dir("municipality", *parts)


def municipality_sgis_dir() -> Path:
    return municipality_dir("sgis")


def municipality_source_dir() -> Path:
    """Official office-address sources (V0.3 input; raw, as downloaded)."""
    return municipality_dir("source")


def municipality_office_dir() -> Path:
    """Office layers built from the sources (V0.3; provenance recorded per row)."""
    return municipality_dir("office")


def derived_municipality_dir() -> Path:
    """Derived municipality products (V0.3 office points)."""
    return derived_dir("municipality")


def floodmap_dir() -> Path:
    """환경부 홍수위험지도 downloads (licence-blocked; the script is kept for the record)."""
    return external_dir("other", "floodmap")


def _climada_default() -> Path:
    """CLIMADA's documented default system directory — written only here."""
    return Path.home() / "climada" / "data"


def climada_data_dir() -> Path:
    """CLIMADA's system data directory as used by this project.

    ``CLIMATERISK_CLIMADA_DATA_DIR`` when set (the worker then points the CLIMADA library at
    it, see :func:`write_climada_conf`); otherwise CLIMADA's documented default
    (home/climada/data), where CLIMADA's own configuration decides.
    """
    value = setting(ENV_CLIMADA_DATA_DIR)
    return _as_path(value) if value else _climada_default()


def climada_conf_dir() -> Path:
    """Folder of the project-scoped ``climada.conf``: ``<DATA_ROOT>/cache/climada_conf``."""
    return cache_dir("climada_conf")


def climada_conf() -> dict[str, Any] | None:
    """The ``climada.conf`` content that relocates CLIMADA to :func:`climada_data_dir`.

    ``None`` when ``CLIMATERISK_CLIMADA_DATA_DIR`` is unset — CLIMADA's own configuration is
    then left alone. Every CLIMADA / petals data path (Data API ledger and cache, MRIOT, OSM,
    …) is written relative to ``local_data.system`` in CLIMADA's defaults, so setting it (plus
    ``local_data.demo``) moves all of them.
    """
    if setting(ENV_CLIMADA_DATA_DIR) is None:
        return None
    system = climada_data_dir()
    return {"local_data": {"system": str(system), "demo": str(system / "demo")}}


def write_climada_conf() -> Path | None:
    """Write the project-scoped ``climada.conf`` (only when its content changed).

    Returns its folder, or ``None`` when no relocation is configured. CLIMADA reads a
    ``climada.conf`` from the working directory **at import time**; the worker package imports
    CLIMADA from this folder once (``climaterisk_worker._bootstrap_climada``). Nothing is
    written to the user's global CLIMADA configuration (home .config or climada/conf), so
    other projects that rely on CLIMADA's default directory are unaffected.
    """
    conf = climada_conf()
    if conf is None:
        return None
    folder = climada_conf_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "climada.conf"
    text = json.dumps(conf, indent=2) + "\n"
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    return folder


def legacy_repo_hazard_db() -> Path:
    """The pre-2026-09 catalogue location inside the checkout (git-ignored ``data/``)."""
    return REPO_ROOT / "data" / "hazard_db"


def canonical_hazard_db() -> Path:
    return derived_dir("hazard_db")


def hazard_db_dir() -> Path:
    """The local hazard catalogue.

    ``CLIMATERISK_HAZARD_DB`` wins. Otherwise ``<DATA_ROOT>/derived/hazard_db`` once it holds
    a ``catalog.json``; until then an existing repo-local catalogue keeps serving
    (transitional, reported as ``LEGACY_REPO_LOCATION``); a fresh machine gets the canonical
    location.
    """
    value = setting(ENV_HAZARD_DB)
    if value:
        return _as_path(value)
    canonical = canonical_hazard_db()
    if (canonical / "catalog.json").is_file():
        return canonical
    legacy = legacy_repo_hazard_db()
    if (legacy / "catalog.json").is_file():
        return legacy
    return canonical


def legacy_locations() -> dict[str, Path]:
    """Where data lived before the ``DATA_ROOT`` layout — detected, never read (except the
    transitional hazard catalogue, see :func:`hazard_db_dir`)."""
    base = _climada_default()
    return {
        "climada": base,
        "kma": base / "kma",
        "municipality_sgis": base / "municipality_src",
        "floodmap": base / "floodmap",
        "hazard_db": legacy_repo_hazard_db(),
    }


def worker_env() -> dict[str, str]:
    """Resolved roots to inject into a spawned worker (explicit, cwd-independent)."""
    return {
        ENV_DATA_ROOT: str(data_root()),
        ENV_KMA_DIR: str(kma_dir()),
        ENV_CLIMADA_DATA_DIR: str(climada_data_dir()),
    }


# --------------------------------------------------------------------------- #
# Status                                                                       #
# --------------------------------------------------------------------------- #
def _dir_size(path: Path, limit: int = 200_000) -> int | None:
    """Total bytes under ``path`` (None when absent; stops counting after ``limit`` files)."""
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_size
    total = 0
    for n, f in enumerate(path.rglob("*")):
        if n >= limit:
            break
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def _has_files(path: Path) -> bool:
    try:
        return path.is_dir() and any(p.is_file() for p in path.iterdir())
    except OSError:
        return False


def status(with_sizes: bool = False) -> dict[str, Any]:
    """Where everything resolves, whether it exists, and what still sits in a legacy place.

    Never raises for missing optional data — each entry says ``present`` / ``missing`` /
    ``LEGACY_LOCATION`` with the migration hint.
    """

    def entry(path: Path, legacy: Path | None = None) -> dict[str, Any]:
        present = path.exists() and (_has_files(path) or path.is_file() or any(path.glob("*")))
        e: dict[str, Any] = {"path": str(path), "present": bool(present)}
        if with_sizes:
            e["bytes"] = _dir_size(path)
        if legacy is not None and legacy != path and legacy.exists():
            e["legacy_path"] = str(legacy)
            if with_sizes:
                e["legacy_bytes"] = _dir_size(legacy)
            if not present:
                e["status"] = "LEGACY_LOCATION"
                e["hint"] = (
                    f"data found at the legacy location {legacy}; it is not read. "
                    "Run `python scripts/migrate_data_root.py` to see the migration plan."
                )
        e.setdefault("status", "present" if present else "missing")
        return e

    legacy = legacy_locations()
    hz = hazard_db_dir()
    hz_status = (
        "LEGACY_REPO_LOCATION"
        if hz == legacy_repo_hazard_db() and setting(ENV_HAZARD_DB) is None
        else ("present" if (hz / "catalog.json").is_file() else "missing")
    )
    return {
        "repo_root": str(REPO_ROOT),
        "data_root": str(data_root()),
        "data_root_source": "env/.env" if setting(ENV_DATA_ROOT) else "default",
        "env_file": str(env_file()),
        "locations": {
            "kma": entry(kma_dir(), legacy["kma"]),
            "municipality_sgis": entry(municipality_sgis_dir(), legacy["municipality_sgis"]),
            "municipality_source": entry(municipality_source_dir()),
            "municipality_office": entry(municipality_office_dir()),
            "derived_municipality": entry(derived_municipality_dir()),
            "floodmap": entry(floodmap_dir(), legacy["floodmap"]),
            "climada_data_dir": entry(climada_data_dir(), legacy.get("climada")),
            "cache": entry(cache_dir()),
            "evidence": entry(evidence_dir()),
        },
        "hazard_db": {
            "path": str(hz),
            "status": hz_status,
            "canonical_path": str(canonical_hazard_db()),
        },
    }
