"""Dataset catalog: one stable id (the "variable name") per dataset the platform uses.

The catalog is ``assets/libraries/dataset_catalog.json``. Each entry names a dataset, its
role (main hazard, check, exposure, ...), where it lives, its coverage and licence. The id
is a snake_case Python identifier and is the string code already uses as ``dataset`` /
``hazard_dataset`` / ``dataset_family``; load a dataset's folder with :func:`dataset_path`.

Presence is tested with :func:`on_disk` using ``exists()`` and directory listings only. It
never opens, reads or hashes a data file: many files are iCloud placeholders and reading
them would trigger mass downloads.

Where a ``path`` is rooted:

* no prefix: relative to :func:`climaterisk.paths.data_root`;
* ``repo:``: relative to the repository root (committed files);
* ``climada:``: relative to :func:`climaterisk.paths.climada_data_dir`.

``CLIMATERISK_KMA_DIR`` (an override of the KMA folder) is not applied here; entries stay at
their default ``external/kma`` location.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from climaterisk import paths
from climaterisk.config import get_settings
from climaterisk.logger import get_logger

logger = get_logger(__name__)

_CATALOG_FILE = "dataset_catalog.json"
_GLOB_CHARS = frozenset("*?[")
REPO_PREFIX = "repo:"
CLIMADA_PREFIX = "climada:"


@dataclass(frozen=True)
class DatasetEntry:
    """One catalog row. List fields are tuples so the entry is hashable and immutable."""

    id: str
    label_ko: str
    label_en: str
    perils: tuple[str, ...]
    role: str
    used_by: tuple[str, ...]
    path: str
    path_fn: str | None
    format: str
    spatial_resolution: str
    temporal: str
    coverage: str
    source: str
    licence: str
    storage_rule: str
    data_sources_id: str | None
    notes: str

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> DatasetEntry:
        """Build an entry from a JSON row (list fields become tuples)."""
        values = dict(row)
        values["perils"] = tuple(values["perils"])
        values["used_by"] = tuple(values["used_by"])
        return cls(**values)


def _catalog_file() -> Path:
    return get_settings().library_path / _CATALOG_FILE


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, DatasetEntry]:
    """Load and cache the catalog, keyed by dataset id (file order is kept)."""
    with _catalog_file().open(encoding="utf-8") as fh:
        raw = json.load(fh)
    entries: dict[str, DatasetEntry] = {}
    for row in raw["datasets"]:
        entry = DatasetEntry.from_dict(row)
        if entry.id in entries:
            raise ValueError(f"duplicate dataset id in catalog: {entry.id!r}")
        entries[entry.id] = entry
    logger.info("loaded dataset catalog: %d entries", len(entries))
    return entries


def ids() -> list[str]:
    """All dataset ids in catalog order."""
    return list(load_catalog())


def dataset(id: str) -> DatasetEntry:
    """Return the entry for ``id``.

    Raises:
        KeyError: unknown id; the message suggests the closest ids.
    """
    catalog = load_catalog()
    try:
        return catalog[id]
    except KeyError:
        close = difflib.get_close_matches(id, catalog, n=3, cutoff=0.5)
        hint = f"; did you mean {', '.join(close)}?" if close else ""
        raise KeyError(f"unknown dataset id {id!r}{hint}") from None


def _root_and_relative(entry: DatasetEntry) -> tuple[Path, str]:
    if entry.path.startswith(REPO_PREFIX):
        return paths.REPO_ROOT, entry.path[len(REPO_PREFIX) :]
    if entry.path.startswith(CLIMADA_PREFIX):
        return paths.climada_data_dir(), entry.path[len(CLIMADA_PREFIX) :]
    return paths.data_root(), entry.path


def _split_glob(relative: str) -> tuple[Path, str | None]:
    """Split a relative path at its first glob component: (literal prefix, glob rest or None)."""
    parts = Path(relative).parts
    for i, part in enumerate(parts):
        if _GLOB_CHARS & set(part):
            return Path(*parts[:i]), str(Path(*parts[i:]))
    return Path(*parts), None


def dataset_path(id: str) -> Path:
    """Folder or file of the dataset, resolved against the data root, repo or CLIMADA dir.

    A path with a glob resolves to the directory before the first glob component (the
    glob's parent), e.g. ``external/kma/MKPRISM_*.nc`` -> ``<DATA_ROOT>/external/kma``.
    """
    root, relative = _root_and_relative(dataset(id))
    prefix, _ = _split_glob(relative)
    return root / prefix


def on_disk(id: str) -> bool:
    """True when the dataset's path (or any glob match) exists.

    Uses ``exists()`` and a lazy directory listing only; no file is opened, read or hashed.
    """
    root, relative = _root_and_relative(dataset(id))
    prefix, pattern = _split_glob(relative)
    base = root / prefix
    try:
        if pattern is None:
            return base.exists()
        if not base.is_dir():
            return False
        return next(base.glob(pattern), None) is not None
    except OSError:
        logger.warning("dataset %s: listing failed under %s", id, base)
        return False
