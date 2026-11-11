#!/usr/bin/env python
"""Flood maps at successive clock times of the Pohang Naengcheon case twin (미검증).

Reruns ONLY the main storm (about 200 s) with depth snapshots at the clock times of the spec
(``figures.propagation_clock``), checks that the rerun reproduces ``max_depth.tif`` and writes
``propagation.json``, ``fig_propagation.png`` and ``fig_flooded_area_time.png`` into the case
folder. Needs ``figures_pack.json`` (run ``case_pohang_figures.py`` first).

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/case_pohang_propagation.py [--out DIR]

Algorithm:
    See :mod:`climaterisk_worker.twin.case_pohang_propagation` (flooded area
    $A = n\\{h > \\theta, M\\}\\,\\Delta x^2 / 10^6$;
    ASCII: A = count(h > theta in mask) * cell^2 / 1e6).
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

from climaterisk_worker.twin import case_pohang_propagation  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run the propagation build and print the frames and the reproduce check."""
    from climaterisk import paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None, help="case folder (default <DATA_ROOT>/derived/...)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = Path(args.out) if args.out else paths.derived_dir("case_twins", "pohang")
    res = case_pohang_propagation.build_propagation(out)
    print(f"clock start of model t = 0: {res['clock_start']}  [{res['badge']}]")
    print(f"flooded = non-channel land below 20 m, depth > {res['threshold_m']} m")
    for f in res["frames"]:
        print(
            f"  {f['clock']}  model {f['model_h']:.3f} h  actual {f['actual_h']:.3f} h  "
            f"flooded {f['flooded_km2']:.3f} km2"
        )
    rp = res["reproduce"]
    print(f"reproduce: max abs diff {rp['max_abs_diff_m']} m (atol {rp['atol_m']} m) ok={rp['ok']}")
    print(f"runtime {res['runtime_s']} s; figures -> {out}")
    return 0 if rp["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
