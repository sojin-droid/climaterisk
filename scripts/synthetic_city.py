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
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker.twin.pipeline import (  # noqa: E402
    compare_climate,
    compare_defences,
    compare_hazard_models,
    run,
)
from climaterisk_worker.twin.spec import SPEC_PATH, load_spec  # noqa: E402


def _print_climate(spec: dict[str, Any], out: Path) -> int:
    """Print the present-vs-future EAL % and climate-change multiplier table."""
    results = compare_climate(spec, out)
    print(
        f"Climate change (seed {spec['seed']}) - EAL as % of total asset value, "
        "defences keep present height:"
    )
    for r in results:
        print(
            f"  future scenario: {r['scenario']}  "
            f"(+{r['stage_2y_m_shift']:.2f} m stage, "
            f"slope +{r['stage_slope_m_shift']:.2f} m/ln-yr)"
        )
        print(
            f"  {'defence':<24}{'present %':>10}{'future %':>10}"
            f"{'multiplier':>12}{'cut-off $/yr':>18}"
        )
        for t in r["defence_results"]:
            prot = (
                "levee"
                if t["levee"]
                else (f"{int(t['protection_rp_years'])}y" if t["protection_rp_years"] else "none")
            )
            print(
                f"  {t['scenario'] + ' (' + prot + ')':<24}"
                f"{t['present_eal_pct']:>10.3f}"
                f"{t['future_eal_pct']:>10.3f}"
                f"{t['climate_change_multiplier']:>12.3f}"
                f"{t['future_cut_off_value_usd_per_year']:>18,.0f}"
            )
        print(f"  outputs -> {out}")
    return 0


def _print_hazard_models(spec: dict[str, Any], out: Path) -> int:
    """Print the stage-law vs 2D-model EAL table and the 2D run diagnostics."""
    result = compare_hazard_models(spec, out)
    print(f"Hazard models (seed {spec['seed']}) - EAL as % of total asset value:")
    print(f"  {'model':<12}{'defence':<18}{'EAL %':>8}{'vs stage law':>14}")
    for r in result["rows"]:
        ratio = r["ratio_to_stage_law"]
        shown = f"{ratio:.3f}" if ratio is not None else "-"
        print(f"  {r['model']:<12}{r['defence']:<18}{r['eal_pct']:>8.3f}{shown:>14}")
    for name, diag in result["diagnostics"].items():
        err = max(
            abs(d["stored_volume_m3"] - d["boundary_volume_m3"]) / max(d["boundary_volume_m3"], 1.0)
            for d in diag["events"]
        )
        flag = "ok" if diag["within_validity"] else "OUTSIDE VALIDITY - do not use"
        print(
            f"  {name}: max Froude {diag['max_froude']:.2f} ({flag}), "
            f"worst volume imbalance {err:.1e}"
        )
    print(f"  outputs -> {out}")
    return 0


def _print_typhoon(spec: dict[str, Any], out: Path) -> int:
    """Price the synthetic typhoons through the engine and print EAL as % of asset value."""
    from climaterisk_worker.twin.city import build_city
    from climaterisk_worker.twin.typhoon import build_tc_hazard, price_tc

    _, assets = build_city(spec)
    hazard = build_tc_hazard(assets, spec)
    rows = price_tc(assets, hazard)
    eal = [r.eal_usd or 0.0 for r in rows]
    total_value = sum(a["asset_value_usd"] for a in assets)
    ty = spec["typhoon"]
    print(
        f"Typhoon (seed {spec['seed']}) - {ty['n_tracks']} tracks, "
        f"{ty['annual_rate_per_year']} /yr, vmax {ty['vmax_ms']} m/s; EAL as % of asset value:"
    )
    print(f"  total        EAL {sum(eal):>12,.0f} USD/yr ({100 * sum(eal) / total_value:.3f} %)")
    by_kind: dict[str, list[float]] = {}
    for a, e in zip(assets, eal, strict=True):
        v = by_kind.setdefault(a["kind"], [0.0, 0.0, 0])
        v[0] += e
        v[1] += a["asset_value_usd"]
        v[2] += 1
    for k, (e, val, n) in sorted(by_kind.items()):
        print(f"  {k:<12} n={n:<4} EAL {e:>12,.0f} USD/yr ({100 * e / val:.3f} %)")
    out.mkdir(parents=True, exist_ok=True)
    print(f"  max wind at any asset {float(hazard.intensity.max()):.1f} m/s")
    return 0


def _print_coastal(spec: dict[str, Any], out: Path) -> int:
    """Storm-surge bathtub on a synthetic coast: write the DEM, price CF, print the losses."""
    import copy

    import numpy as np
    from climaterisk_worker.twin.city import build_city
    from climaterisk_worker.twin.coastal import price_surge, surge_hazard, write_dem

    graph, assets = build_city(spec)
    out.mkdir(parents=True, exist_ok=True)
    dem = write_dem(graph, spec, out / "coast_dem.tif")
    total_value = sum(a["asset_value_usd"] for a in assets)

    def run(sp: dict[str, Any]) -> tuple[Any, list[float]]:
        hz = surge_hazard(assets, graph, sp, dem)
        return hz, [r.eal_usd or 0.0 for r in price_surge(assets, hz)]

    hazard, eal = run(spec)
    co = spec["coast"]
    depth = hazard.intensity.toarray()
    print(
        f"Coastal surge bathtub (seed {spec['seed']}) - coast {co['coastline_offset_m']} m south "
        f"of row 0, sea level rise {co['sea_level_rise_m']} m; DEM -> {dem}"
    )
    print(f"  max surge depth {float(depth.max()):.2f} m")
    print(f"  assets flooded  {int(np.count_nonzero(depth.max(axis=0) > 0))} of {len(assets)}")
    print(f"  total        EAL {sum(eal):>12,.0f} USD/yr ({100 * sum(eal) / total_value:.3f} %)")
    by_kind: dict[str, list[float]] = {}
    for a, e in zip(assets, eal, strict=True):
        v = by_kind.setdefault(a["kind"], [0.0, 0.0, 0])
        v[0] += e
        v[1] += a["asset_value_usd"]
        v[2] += 1
    for k, (e, val, n) in sorted(by_kind.items()):
        print(f"  {k:<12} n={n:<4} EAL {e:>12,.0f} USD/yr ({100 * e / val:.3f} %)")
    risen = copy.deepcopy(spec)
    risen["coast"]["sea_level_rise_m"] = co["sea_level_rise_m"] + 0.5
    _, eal_slr = run(risen)
    print(
        f"  with +0.5 m sea level rise: EAL {sum(eal_slr):,.0f} USD/yr "
        f"({100 * sum(eal_slr) / total_value:.3f} %)"
    )
    return 0


def _print_coastal2d(spec: dict[str, Any], out: Path) -> int:
    """Price Petals' surge bathtub and the 2D surge model side by side; save snapshots."""
    import numpy as np
    from climaterisk_worker.twin.city import build_city
    from climaterisk_worker.twin.coastal import price_surge, surge_hazard, write_dem
    from climaterisk_worker.twin.inundation import city_raster
    from climaterisk_worker.twin.surge2d import surge_depths_2d, surge_hazard_2d

    graph, assets = build_city(spec)
    out.mkdir(parents=True, exist_ok=True)
    dem = write_dem(graph, spec, out / "coast_dem.tif")
    total_value = sum(a["asset_value_usd"] for a in assets)

    def by_kind(eal: list[float]) -> dict[str, list[float]]:
        out_k: dict[str, list[float]] = {}
        for a, e in zip(assets, eal, strict=True):
            v = out_k.setdefault(a["kind"], [0.0, 0.0])
            v[0] += e
            v[1] += a["asset_value_usd"]
        return out_k

    eal_bath = [
        r.eal_usd or 0.0 for r in price_surge(assets, surge_hazard(assets, graph, spec, dem))
    ]
    eal_2d = [r.eal_usd or 0.0 for r in price_surge(assets, surge_hazard_2d(graph, assets, spec))]
    co = spec["coast"]
    print(
        f"Coastal surge, bathtub vs 2D (seed {spec['seed']}) - surge {co['surge_duration_h']} h "
        f"+ {co['surge_recession_h']} h recession, sea level rise {co['sea_level_rise_m']} m"
    )
    for label, eal in (("bathtub (Petals)", eal_bath), ("2D hydraulic", eal_2d)):
        print(f"  {label}: total EAL {sum(eal):,.0f} USD/yr ({100 * sum(eal) / total_value:.3f} %)")
        for k, (e, val) in sorted(by_kind(eal).items()):
            print(f"    {k:<12} EAL {e:>12,.0f} USD/yr ({100 * e / val:.3f} %)")
    ratio = sum(eal_2d) / sum(eal_bath) if sum(eal_bath) else float("nan")
    print(f"  EAL 2D / bathtub = {ratio:.3f}")

    depth, diags = surge_depths_2d(graph, assets, spec, snapshot_every_s=600.0)
    fr = max(d["max_froude"] for d in diags)
    limit = float(spec["hydraulic"]["max_valid_froude"])
    flag = "  OUTSIDE VALIDITY" if fr >= limit else ""
    print(f"  max Froude number of the 2D runs {fr:.2f} (valid below {limit}){flag}")
    imbalance = max(
        abs(d["stored_volume_m3"] - d["boundary_volume_m3"])
        / max(abs(d["boundary_volume_m3"]), 1.0)
        for d in diags
    )
    print(f"  worst volume imbalance {imbalance:.2e} (relative to boundary volume)")
    worst = diags[int(np.argmax(depth.sum(axis=1)))]
    bed, cell, _ = city_raster(graph, spec)
    bed = bed.copy()
    bed[0, :] = float(co["sea_bed_m"])
    snaps = worst["snapshots"]
    path = out / "surge_snapshots.npz"
    np.savez_compressed(
        path,
        times_s=np.array([t for t, _ in snaps]),
        depth=np.stack([h for _, h in snaps]),
        bed=bed,
        cell_m=cell,
    )
    print(
        f"  worst event peak stage {worst['peak_stage_m']:.2f} m; {len(snaps)} snapshots -> {path}"
    )
    return 0


def _print_heat(spec: dict[str, Any], out: Path) -> int:
    """Price street-level UTCI (canyon MRT approximation) and print productivity loss."""
    import numpy as np
    from climaterisk_worker.twin.city import build_city
    from climaterisk_worker.twin.heat import build_heat_hazard, price_productivity

    _, assets = build_city(spec)
    hazard = build_heat_hazard(assets, spec)
    rows = price_productivity(assets, hazard, spec)
    feel = hazard.intensity.toarray()
    hp = spec["heat"]
    print(
        f"Heatwave (seed {spec['seed']}) - street-level UTCI "
        f"(MRT from a street-canyon approximation); {hp['heatwave_days']} heatwave days/yr:"
    )
    print("  peak UTCI per event (degC, min..max over assets):")
    for name, row in zip(hazard.event_name, feel, strict=True):
        print(f"    {name:<7}{float(np.min(row)):>6.1f} .. {float(np.max(row)):>5.1f}")
    exposure = sum(r["exposure_usd"] for r in rows)
    eal = sum(r["eal_usd"] for r in rows)
    pct = 100 * eal / exposure if exposure else 0.0
    print(
        f"  total expected annual productivity loss {eal:>12,.0f} USD/yr "
        f"({pct:.2f} % of {exposure:,.0f} USD annual exposure)"
    )
    by_kind: dict[str, list[float]] = {}
    for r in rows:
        v = by_kind.setdefault(r["kind"], [0.0, 0.0, 0])
        v[0] += r["eal_usd"]
        v[1] += r["exposure_usd"]
        v[2] += 1
    for k, (e, ex, n) in sorted(by_kind.items()):
        share = f"{100 * e / ex:.2f} %" if ex else "no workers"
        print(f"  {k:<12} n={n:<4} loss {e:>12,.0f} USD/yr ({share})")
    out.mkdir(parents=True, exist_ok=True)
    return 0


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
        "--map",
        action="store_true",
        help="also write an interactive Folium HTML map (city_map.html) with a "
        "return-period depth slider and out-of-service nodes",
    )
    ap.add_argument(
        "--map3d",
        action="store_true",
        help="also write an interactive deck.gl 3D HTML page (city_3d.html) with "
        "extruded buildings coloured by EAL % and a selectable-return-period "
        "water surface (storeys are geometry only; no EAL change)",
    )
    ap.add_argument(
        "--compare",
        action="store_true",
        help="run all defence scenarios and write a comparison table (defence_scenarios.csv)",
    )
    ap.add_argument(
        "--climate",
        action="store_true",
        help="present vs future river stage for each defence scenario "
        "(climate_scenarios.csv); defences keep their present height",
    )
    ap.add_argument(
        "--hydraulic",
        action="store_true",
        help="compare the bathtub stage law with the 2D hydraulic model (hazard_models.csv)",
    )
    ap.add_argument(
        "--typhoon",
        action="store_true",
        help="synthetic typhoon tracks -> CLIMADA wind -> engine TC pricing; prints EAL %%",
    )
    ap.add_argument(
        "--coastal",
        action="store_true",
        help="synthetic coast: typhoon storm surge (Petals bathtub) -> engine CF pricing",
    )
    ap.add_argument(
        "--coastal2d",
        action="store_true",
        help="storm surge on the 2D hydraulic model vs the Petals bathtub; saves "
        "surge_snapshots.npz",
    )
    ap.add_argument(
        "--heat",
        action="store_true",
        help="street-level UTCI (canyon MRT approximation) -> productivity loss",
    )
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else paths.derived_dir("synthetic_city")
    spec = load_spec(args.spec)
    if args.coastal2d:
        return _print_coastal2d(spec, out)
    if args.coastal:
        return _print_coastal(spec, out)
    if args.heat:
        return _print_heat(spec, out)
    if args.typhoon:
        return _print_typhoon(spec, out)
    if args.climate:
        return _print_climate(spec, out)
    if args.hydraulic:
        return _print_hazard_models(spec, out)
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
    summary = run(
        out,
        spec,
        args.protection_rp,
        levee=args.levee,
        map_path=out / "city_map.html" if args.map else None,
        map3d_path=out / "city_3d.html" if args.map3d else None,
    )
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
