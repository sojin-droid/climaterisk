#!/usr/bin/env python
"""List which 홍수위험지도 zips exist (by file name) and which are iCloud placeholders.

Reads only the folder listing and ``stat`` flags; no zip is opened and no geometry is read.
Writes ``<DATA_ROOT>/derived/floodmap/coverage.json`` (rows ``family/basin/rp/state`` only)
and prints the counts per family, return period and state.

    python scripts/floodmap_coverage.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "src", REPO / "worker"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker import floodmap_depth as fd  # noqa: E402

from climaterisk import paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.parse_args(argv)
    root = paths.floodmap_dir()
    if not root.is_dir():
        print(f"floodmap folder not found: {root}", file=sys.stderr)
        return 1
    rows = fd.coverage_table(root, {**fd.FAMILIES, **fd.URBAN_FAMILIES})
    out = paths.derived_dir("floodmap") / "coverage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = Counter((r["family"], r["rp"], r["state"]) for r in rows)
    for (family, rp, state), n in sorted(counts.items()):
        print(f"{family:9s} {rp:>3d} y  {state:9s} {n}")
    print(f"total {len(rows)} zips -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
