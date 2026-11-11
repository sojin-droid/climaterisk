#!/usr/bin/env python
"""Figure pack of the Pohang Naengcheon case twin (published-study style, 미검증).

Reads the existing run (``summary.json``, ``hydrograph_mouth.csv``, ``points_depth.csv``,
``max_depth.tif``) from the case folder, rebuilds the terrain for the channel profile and the
catchment outline (no solver run) and writes eight PNGs, ``figures_pack.json`` and the
``literature_check`` (plus assumptions A27 / A28) into ``summary.json``. The literature check
(Hwang et al. 2024) is diagnostic only: nothing is tuned to it.

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/case_pohang_figures.py [--out DIR]

Algorithm:
    See :mod:`climaterisk_worker.twin.case_pohang_figures` (clock anchoring
    $t_0 = (a+b)/2 - c$; ASCII: t0 = midpoint(obs) - model_centre).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker.twin import case_pohang_figures  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Build the figure pack and print the literature check."""
    from climaterisk import paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None, help="case folder (default <DATA_ROOT>/derived/...)")
    ap.add_argument(
        "--obs", default=str(case_pohang_figures.ASSETS_OBS), help="observation config JSON"
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = Path(args.out) if args.out else paths.derived_dir("case_twins", "pohang")
    pack = case_pohang_figures.build_pack(out, Path(args.obs))
    print(f"clock start of model t = 0: {pack['clock_start']}  [{pack['badge']}]")
    for r in pack["validation"]:
        print(
            f"  {r['id']}: observed {r['observed']}, model {r['model']}, "
            f"diff {r['diff']}, ok {r['ok']}"
        )
    for c in pack["depth_classes"]:
        print(f"  class {c['lo']}-{c['hi']} m: {c['area_km2']:.3f} km2")
    print(f"figures -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
