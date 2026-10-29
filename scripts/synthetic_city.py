#!/usr/bin/env python
"""Synthetic virtual city -> CLIMADA Exposures + river-flood Hazard -> the repo's engine.

No real building or GIS data is used: the city, its terrain, its asset values and its flood
are generated from ``assets/libraries/synthetic_city_spec.json`` with a pinned seed. The
pricing is not re-implemented — every asset goes through
:func:`climaterisk_worker.physical_risk.engine.calculate` (published JRC curve from
``registry``, sector from ``physical_risk_config.json``, ``ImpactCalc`` for the arithmetic),
exactly as a real facility does in ``physical_risk_batch.py``.

This is the thin CLI. The generator (city graph, terrain, assets) and the CLIMADA
hazard/pricing both live in :mod:`climaterisk_worker.twin`.

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/synthetic_city.py [--out DIR] [--protection-rp 50]

Outputs (default ``<DATA_ROOT>/derived/synthetic_city``): ``assets.csv``, ``assets.gpkg``,
``results.csv`` (one engine ResultRow per asset), ``network_by_rp.csv``, ``summary.json``
and ``city_map.png``.

Algorithm:
    Ground and flood depth at a point at distance $y$ (m) inland from the river:

    $$z = \\max(0,\\; z_b + s\\,y + \\varepsilon),\\quad \\varepsilon \\sim N(0, \\sigma^2)$$
    $$h(T) = h_2 + b\\,\\ln(T / 2),\\qquad d(T) = \\max(0,\\; h(T) - z)$$
    ASCII: z = max(0, z_b + s*y + eps); h(T) = h_2 + b*ln(T/2); d(T) = max(0, h(T) - z)

    $z$ ground elevation (m above river datum), $z_b$ bank height (m), $s$ slope (m/m),
    $\\sigma$ terrain noise (m), $T$ return period (years), $h$ river stage (m),
    $b$ stage growth (m per ln-year), $d$ flood depth (m).

    One event per return period with layered frequency $f_i = 1/T_i - 1/T_{i+1}$
    (the rarest keeps $1/T_n$), so CLIMADA's $\\mathrm{EAL} = \\sum_i f_i\\,v\\,
    \\mathrm{MDR}(d_i)$ is the trapezoid-free step integral of the loss-exceedance curve.

    Network service loss (an indicator, not money): an asset node fails when
    $d > d_{fail}$; a residential lot is cut off when, in the street graph without failed
    nodes, it reaches no working hospital or no working substation.
    $E[\\text{cut-off value}] = \\sum_i f_i\\,V_{cut}(T_i)$ (USD/yr).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker.twin.pipeline import (  # noqa: E402
    compare_defences,
    run,
)
from climaterisk_worker.twin.spec import SPEC_PATH, load_spec  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Generate the synthetic city and print the EAL summary."""
    from climaterisk import paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--out", default=None, help="output folder (default <DATA_ROOT>/derived/synthetic_city)"
    )
    ap.add_argument("--spec", default=str(SPEC_PATH), help="city specification JSON")
    ap.add_argument(
        "--protection-rp",
        type=float,
        default=None,
        help="uniform flood-defence design return period (years)",
    )
    ap.add_argument(
        "--levee",
        action="store_true",
        help="apply the local bank levee (spec['levee']) instead of a uniform standard",
    )
    ap.add_argument(
        "--compare",
        action="store_true",
        help="run all defence scenarios and write a comparison table (defence_scenarios.csv)",
    )
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else paths.derived_dir("synthetic_city")
    spec = load_spec(args.spec)
    if args.compare:
        table = compare_defences(spec, out)
        kinds = sorted(table[0]["by_kind"])
        print(f"Defence scenarios (seed {spec['seed']}) — EAL as % of total asset value:")
        print(f"  {'scenario':<20}{'protection':<12}{'EAL %':>9}{'cut-off $/yr':>18}")
        for t in table:
            if t["levee"]:
                prot = "levee"
            elif t["protection_rp_years"]:
                prot = f"{int(t['protection_rp_years'])}y"
            else:
                prot = "none"
            cut = t["expected_cut_off_value_usd_per_year"]
            print(f"  {t['scenario']:<20}{prot:<12}{t['eal_pct']:>9.3f}{cut:>18,.0f}")
        print("  by-kind EAL %:")
        for t in table:
            parts = " ".join(
                f"{k}={t['by_kind'].get(k, {}).get('eal_pct', 0.0):.3f}" for k in kinds
            )
            print(f"  {t['scenario']:<20}{parts}")
        print(f"  outputs -> {out}")
        return 0
    summary = run(out, spec, args.protection_rp, levee=args.levee)
    print(f"assets {summary['n_assets']} (nodes {summary['n_nodes']}, roads {summary['n_edges']})")
    print(f"total value  {summary['total_value_usd']:,.0f} USD")
    print(f"EAL          {summary['eal_usd']:,.0f} USD/yr ({summary['eal_pct']:.3f} %)")
    for rp, v in summary["loss_by_return_period_usd"].items():
        print(f"  {rp:>4}-yr loss {v:,.0f} USD")
    for k, v in sorted(summary["by_kind"].items()):
        print(f"  {k:<12} n={v['n']:<4} EAL {v['eal_usd']:>12,.0f} USD/yr ({v['eal_pct']:.3f} %)")
    print(
        "expected cut-off residential value "
        f"{summary['expected_cut_off_value_usd_per_year']:,.0f} USD/yr"
    )
    print(f"status {summary['status_counts']}")
    print(f"outputs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
