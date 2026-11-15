#!/usr/bin/env python
"""Build a 단면 asset sheet from the real maps and DEMs and save it for the app.

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/build_sheet.py --lat 37.547 --lon 127.047 --id DM-12 \\
        [--bearing 90] [--half-km 2.0] [--step-km 0.03] [--no-global]

Writes ``<DATA_ROOT>/derived/sheets/<id>.json`` after ``validate_sheet`` passes. Only class
codes and heights are written, never map polygons.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "src", REPO / "worker"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker.sheet_builder import build_sheet  # noqa: E402

from climaterisk import paths  # noqa: E402
from climaterisk.sheets import validate_sheet  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--id", required=True, dest="sheet_id")
    ap.add_argument("--bearing", type=float, default=90.0)
    ap.add_argument("--half-km", type=float, default=2.0)
    ap.add_argument("--step-km", type=float, default=0.03)
    ap.add_argument("--no-global", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    sheet = build_sheet(
        a.lat, a.lon, a.sheet_id, a.bearing, a.half_km, a.step_km, with_global=not a.no_global
    )
    errs = validate_sheet(sheet)
    if errs:
        print("invalid sheet:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    out = paths.derived_dir("sheets") / f"{a.sheet_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sheet, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
