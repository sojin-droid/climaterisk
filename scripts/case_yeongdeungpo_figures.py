#!/usr/bin/env python
"""Figure pack of the Yeongdeungpo street-heat case twin (published-study style, 미검증).

Reads the existing twin run (``maps.npz``, ``summary.json``) and the S-DoT cache from the case
folder, writes nine PNGs, ``figures_pack.json`` and ``literature_check`` into ``summary.json``.
The model is not rerun; the literature check (Kim et al. 2022) is diagnostic only.

    CLIMATERISK_DATA_ROOT=~/Data/climaterisk ./.climada-env/bin/python \\
        scripts/case_yeongdeungpo_figures.py [--out DIR]

Algorithm:
    See :mod:`climaterisk_worker.twin.case_heat_plots` (binned median of mean radiant
    temperature over sky view factor; ASCII: Tmr_med(b) = median(Tmr[svf in bin b])).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker.twin import case_heat_plots  # noqa: E402

from climaterisk import paths  # noqa: E402

SPEC_PATH = REPO / "assets" / "libraries" / "case_yeongdeungpo_spec.json"


def main(argv: list[str] | None = None) -> int:
    """Build the figure pack and print the literature check."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None, help="case folder (default <DATA_ROOT>/derived/...)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    out = Path(args.out) if args.out else paths.derived_dir("case_twins", "yeongdeungpo")
    osm = paths.external_dir("osm", "south-korea-261013.osm.pbf")
    pack = case_heat_plots.build_pack(out, SPEC_PATH, paths.data_root(), osm)
    for r in pack["literature"]:
        print(f"  {r['id']}: model {r['model']}, per day {r['per_day']}, ok {r['ok']}")
    print(f"figures -> {out}  [{pack['badge']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
