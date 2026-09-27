"""Local hazard catalog — the platform's CLIMADA-ready perils database.

A catalog is a directory (``<DATA_ROOT>/derived/hazard_db`` — see ``climaterisk.paths``) with a ``catalog.json``
manifest indexing HDF5 hazard files by ``(peril, climate_scenario, region, year)``.
Entries are produced by ``scripts/build_hazard.py`` (converting standardized grids
from real ingestion, or caching Data-API hazards for offline/reproducible runs).

The worker resolves hazards from this catalog FIRST and falls back to the live
CLIMADA Data API — this is how the platform serves perils/regions the Data API
does not cover (custom or locally-ingested sources).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def catalog_dir() -> Path:
    """Resolve the hazard-catalog directory via ``climaterisk.paths.hazard_db_dir``.

    ``CLIMATERISK_HAZARD_DB`` (the backend always injects it) → ``<DATA_ROOT>/derived/hazard_db``
    → the transitional repo-local ``data/hazard_db``. The catalog holds large HDF5 binaries
    rebuilt from real sources via ``scripts/build_hazard.py``; it never lives in Git.
    """
    from climaterisk_worker._paths import paths

    return paths.hazard_db_dir()


def _manifest_path() -> Path:
    return catalog_dir() / "catalog.json"


def load_manifest() -> list[dict[str, Any]]:
    """Return all catalog entries (empty list if the catalog does not exist yet)."""
    path = _manifest_path()
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data.get("entries", [])
    return entries


def lookup(
    peril: str, climate_scenario: str, region: str, year: int | None
) -> dict[str, Any] | None:
    """Find the best catalog entry for a peril/scenario/region (nearest year)."""
    matches = [
        e
        for e in load_manifest()
        if e["peril"] == peril
        and e["climate_scenario"] == climate_scenario
        and e["region"] == region
    ]
    if not matches:
        return None
    if year is None:
        return matches[0]
    return min(matches, key=lambda e: abs((e.get("year") or 0) - year))


def load_hazard(peril: str, climate_scenario: str, region: str, year: int | None):  # type: ignore[no-untyped-def]
    """Return a CLIMADA ``Hazard`` from the catalog, or ``None`` if no entry matches."""
    entry = lookup(peril, climate_scenario, region, year)
    if entry is None:
        return None
    from climada.hazard import Hazard

    haz_file = catalog_dir() / entry["file"]
    if not haz_file.is_file():
        return None
    return Hazard.from_hdf5(str(haz_file))


def register(entry: dict[str, Any]) -> None:
    """Add or replace a catalog entry (deduped by ``file``) in the manifest."""
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = load_manifest()
    entries = [e for e in entries if e.get("file") != entry.get("file")]
    entries.append(entry)
    path.write_text(json.dumps({"entries": entries}, indent=2) + "\n", encoding="utf-8")
