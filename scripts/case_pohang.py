#!/usr/bin/env python
"""Case twin: Pohang Naengcheon, Typhoon Hinnamnor 2022-09-06 (rain-on-grid, 30 m).

Builds the Naengcheon catchment from GLO-30 and OSM, burns the channel to about 700 m3/s,
runs the published Pohang ASOS depth-duration storm (nested hyetograph) on the Gangnam twin's
local-inertial solver and, as a diagnostic only, the Seoul-shape check storm. Badge "미검증",
no money. See ``docs/reports/case_pohang_ko.md`` and ``assets/libraries/case_pohang_spec.json``.

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/case_pohang.py [--out DIR] [--cell-m 30] [--no-check]

Outputs (default ``<DATA_ROOT>/derived/case_twins/pohang``): ``summary.json``,
``max_depth.tif``, ``max_depth_map.png``, ``hydrograph_mouth.csv``, ``hydrograph_mouth.png``
and ``points_depth.csv``.

Algorithm:
    See :mod:`climaterisk_worker.twin.case_pohang` (nested alternating block, D8 catchment,
    Manning channel burn calibrated by a backwater profile, local-inertial rain-on-grid).
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

from climaterisk_worker.twin import case_pohang  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run the Pohang case and print the key numbers."""
    from climaterisk import paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None, help="output folder (default <DATA_ROOT>/derived/...)")
    ap.add_argument("--spec", default=str(case_pohang.ASSETS_SPEC), help="case spec JSON")
    ap.add_argument("--cell-m", type=float, default=None, help="cell size (m); spec default 30")
    ap.add_argument("--no-check", action="store_true", help="skip the Seoul-shape check run")
    ap.add_argument(
        "--t-scale", type=float, default=1.0, help="shorten the simulation (quick tests only)"
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    spec = case_pohang.load_case_spec(args.spec)
    out = Path(args.out) if args.out else paths.derived_dir("case_twins", "pohang")
    s = case_pohang.run_case(
        spec, out, cell_m=args.cell_m, with_check=not args.no_check, t_scale=args.t_scale
    )
    d = s["discharge_mouth"]
    print(f"{s['name']}  [{s['badge']}]  assumptions: {s['n_assumptions']}")
    print(
        f"catchment {s['catchment_area_km2']} km2 (ref {s['catchment_ref_km2']}, "
        f"{100 * s['catchment_rel_diff']:+.1f} %)"
    )
    print(
        f"grid {s['grid']['cell_m']} m, {s['grid']['n_domain_cells']} cells, "
        f"runtime {s['runtime_s']['total']} s"
    )
    print(
        f"peak at the mouth {d['peak_10min_mean_m3s']} m3/s at "
        f"{d['peak_time_h_after_rain_start']} h "
        f"(capacity {d['channel_capacity_ref_m3s']}, 100-yr {d['design_100yr_m3s']}, "
        f"500-yr {d['design_500yr_m3s']})"
    )
    for name, p in s["points"].items():
        t_over = p.get("overtopping_time_h_after_rain_start")
        print(f"  {name}: max depth {p['max_depth_m']} m, overtopping {t_over}")
    print(f"flooded area {s['flooded_area']}")
    print(f"check {s['check']}")
    print(f"outputs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
