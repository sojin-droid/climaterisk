#!/usr/bin/env python
"""Print the dataset catalog with one presence flag per dataset (stat/listing only).

Columns: id | label_ko | role | coverage | on_disk. Nothing is opened, read or hashed (iCloud
placeholders would start downloading); ``on_disk`` is ``exists()`` or a directory listing.

    python scripts/dataset_catalog_status.py              # all datasets
    python scripts/dataset_catalog_status.py --role hazard_main --missing
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk.data import catalog  # noqa: E402


def _width(text: str) -> int:
    """Terminal columns of ``text`` (Hangul and other wide characters count 2)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--role", help="only this role (hazard_main, exposure, ...)")
    ap.add_argument("--missing", action="store_true", help="only datasets not on disk")
    args = ap.parse_args(argv)

    rows = []
    for dataset_id in catalog.ids():
        entry = catalog.dataset(dataset_id)
        if args.role and entry.role != args.role:
            continue
        present = catalog.on_disk(dataset_id)
        if args.missing and present:
            continue
        rows.append(
            (
                dataset_id,
                _clip(entry.label_ko, 22),
                entry.role,
                _clip(entry.coverage, 40),
                "yes" if present else "no",
            )
        )
    header = ("id", "label_ko", "role", "coverage", "on_disk")
    widths = [max(_width(r[i]) for r in [header, *rows]) for i in range(5)]
    for r in [header, *rows]:
        print("  ".join(_pad(r[i], widths[i]) for i in range(5)).rstrip())
    n_yes = sum(1 for r in rows if r[4] == "yes")
    print(
        f"\n{len(rows)} datasets listed, {n_yes} on disk (data root: {catalog.paths.data_root()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
