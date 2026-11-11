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


def _animate(spec: dict[str, Any], out: Path) -> int:
    """Write the animated flood page (flood_time.html) from the surge snapshots."""
    from climaterisk_worker.twin.viewer_time import render_time_view

    snap = out / "surge_snapshots.npz"
    if not snap.exists():
        print(f"error: {snap} not found - run with --coastal2d first to write it", file=sys.stderr)
        return 1
    page = render_time_view(
        snap, spec, out / "flood_time.html", "Sea surge over the synthetic city"
    )
    print(f"Wrote {page}")
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


def _print_site(spec: dict[str, Any], out: Path) -> int:
    """Build the real site (OSM buildings + Copernicus DEM) and write its ground and footprints."""
    import numpy as np
    import rasterio
    from climaterisk_worker.twin.site import build_site
    from rasterio.transform import from_origin

    site = build_site(spec)
    grid, buildings, mask, ground = site["grid"], site["buildings"], site["mask"], site["ground"]
    cfg = spec["site"]
    print(f"Site: {cfg['name']}  bbox {cfg['bbox']}")
    print(f"  grid {grid.nx} x {grid.ny} cells of {grid.cell_m:g} m  ({mask.sum()} building cells)")
    print(f"  buildings: {len(buildings)}")
    shares = buildings["height_source"].value_counts(normalize=True)
    for source in ("height", "levels", "typology"):
        print(f"    height_source {source:<9}{100.0 * float(shares.get(source, 0.0)):6.1f} %")
    heights = buildings["height_m"].to_numpy()
    print(
        f"  height range {heights.min():.1f} - {heights.max():.1f} m "
        f"(median {np.median(heights):.1f})"
    )
    print(f"  ground elevation {ground.min():.1f} - {ground.max():.1f} m")
    print(f"  data: {cfg['attribution']['osm']}; {cfg['attribution']['dem']}")
    out.mkdir(parents=True, exist_ok=True)
    cell_deg_y = grid.cell_m / 111320.0
    cell_deg_x = grid.cell_m / grid.lon_m_per_deg
    transform = from_origin(grid.west, grid.south + grid.ny * cell_deg_y, cell_deg_x, cell_deg_y)
    with rasterio.open(
        out / "site_ground.tif",
        "w",
        driver="GTiff",
        height=grid.ny,
        width=grid.nx,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as ds:
        ds.write(ground[::-1].astype("float32"), 1)  # file rows run north to south
    buildings.to_file(out / "site_buildings.gpkg", driver="GPKG")
    print(f"  wrote {out / 'site_ground.tif'} and {out / 'site_buildings.gpkg'}")
    return 0


def _print_pluvial(spec: dict[str, Any], out: Path) -> int:
    """Design-storm rain on the real site, with and without buildings as walls."""
    import numpy as np
    import rasterio
    from climaterisk_worker.twin import pluvial
    from climaterisk_worker.twin.site import build_site
    from rasterio.transform import from_origin

    site = build_site(spec)
    grid, walls, ground = site["grid"], site["mask"], site["ground"]
    cell_m = float(spec["site"]["cell_m"])
    r = spec["rain"]
    max_fr_ok = float(spec["hydraulic"]["max_valid_froude"])
    _, lon = grid.centres()
    west = (lon < 127.0255) & ~walls  # Seocho basin
    east = (lon > 127.0300) & ~walls  # Yeoksam slope
    print("Pluvial flood, Gangnam site — repo-specific pluvial method (design storm, NRCS CN,")
    print("  uniform sewer sink); storm shape and sewer capacity are assumptions, not calibrated.")
    print(
        f"  storm {r['total_mm']:g} mm in {r['duration_min']:g} min "
        f"(CN {r['cn_paved']:g}, sewer {r['sewer_capacity_mm_h']:g} mm/h), "
        f"{r['recession_min']:g} min recession"
    )
    runs = {}
    for label, w, snap in (
        ("with buildings", walls, 300.0),
        ("no buildings", np.zeros_like(walls), 300.0),
    ):
        res = pluvial.run_pluvial(ground, w, cell_m, spec, snapshot_every_s=snap)
        runs[label] = res
        open_cells = ~w
        h_max = res["h_max"]
        flooded_km2 = float(((h_max > 0.10) & open_cells).sum()) * cell_m**2 / 1e6
        stored = float(res["h"].sum()) * cell_m**2
        fr = res["max_froude"]
        flag = "  OUTSIDE VALIDITY" if fr >= max_fr_ok else ""
        print(f"  [{label}]")
        print(f"    flooded area (h_max > 0.10 m)  {flooded_km2:8.4f} km2")
        print(f"    max depth                      {float(h_max[open_cells].max()):8.2f} m")
        print(
            f"    volumes (m3): rain {res['rain_volume_m3']:,.0f}  sewer "
            f"{res['sink_volume_m3']:,.0f}  outflow {res['outflow_volume_m3']:,.0f}  "
            f"stored {stored:,.0f}"
        )
        print(f"    max Froude {fr:.2f}{flag}")
        print(
            f"    mean peak depth: Seocho basin (W of 127.0255) "
            f"{float(h_max[west & open_cells].mean()):.3f} m, Yeoksam slope (E of 127.0300) "
            f"{float(h_max[east & open_cells].mean()):.3f} m"
        )
    out.mkdir(parents=True, exist_ok=True)
    for name, label, w in (
        ("pluvial_snapshots.npz", "with buildings", walls),
        ("pluvial_snapshots_nowalls.npz", "no buildings", np.zeros_like(walls)),
    ):
        run_snaps = runs[label]["snapshots"]
        np.savez_compressed(
            out / name,
            times_s=np.array([t for t, _ in run_snaps], dtype=float),
            depth=np.stack([h for _, h in run_snaps]).astype("float32"),
            bed=ground,
            cell_m=cell_m,
            walls=w,
            max_froude=float(runs[label]["max_froude"]),
            h_max=runs[label]["h_max"].astype("float32"),
        )
    cell_deg_y = grid.cell_m / 111320.0
    cell_deg_x = grid.cell_m / grid.lon_m_per_deg
    transform = from_origin(grid.west, grid.south + grid.ny * cell_deg_y, cell_deg_x, cell_deg_y)
    with rasterio.open(
        out / "pluvial_hmax.tif",
        "w",
        driver="GTiff",
        height=grid.ny,
        width=grid.nx,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as ds:
        ds.write(runs["with buildings"]["h_max"][::-1].astype("float32"), 1)
    print(f"  {spec['site']['attribution']['osm']}; {spec['site']['attribution']['dem']}")
    print(f"  wrote {out / 'pluvial_snapshots.npz'}, {out / 'pluvial_snapshots_nowalls.npz'}")
    print(f"  and {out / 'pluvial_hmax.tif'}")
    return 0


def _print_site_loss(spec: dict[str, Any], out: Path) -> int:
    """Per-building loss of the --pluvial design storm, with and without walls."""
    import numpy as np
    import pandas as pd
    from climaterisk_worker.twin import site_loss
    from climaterisk_worker.twin.site import build_site

    files = {
        "with": out / "pluvial_snapshots.npz",
        "without": out / "pluvial_snapshots_nowalls.npz",
    }
    stored = {}
    for key, f in files.items():
        if not f.exists():
            print(f"error: {f} not found - run with --pluvial first to write it", file=sys.stderr)
            return 1
        with np.load(f) as z:
            if "h_max" not in z.files:
                print(
                    f"error: {f} has no h_max - re-run with --pluvial to write it",
                    file=sys.stderr,
                )
                return 1
            stored[key] = (np.asarray(z["h_max"], dtype=float), np.asarray(z["walls"], dtype=bool))
    site = build_site(spec)
    grid, b = site["grid"], site["buildings"]
    value = site_loss.exposed_value_usd(b, spec)
    depth, loss = {}, {}
    for key, (h_max, w) in stored.items():
        depth[key] = site_loss.entry_depths(grid, b, h_max, w, spec)
        loss[key] = site_loss.price_buildings(b, depth[key], value, spec)["loss_usd"]
    under = b["underground"].to_numpy(dtype=bool)
    print("Loss of ONE design storm (not an expected annual loss); building values are")
    print("  assumptions (footprint x cost/m2), not client data.")
    print(f"  {'':<34}{'with buildings':>18}{'no buildings':>18}")
    total_value = float(value.sum())
    print(f"  {'total exposed value (USD)':<34}{total_value:>18,.0f}{total_value:>18,.0f}")
    row = {
        "buildings with loss > 0": [int((loss[k] > 0).sum()) for k in ("with", "without")],
        "total loss (USD)": [float(loss[k].sum()) for k in ("with", "without")],
        "loss, % of exposed value": [
            100.0 * float(loss[k].sum()) / total_value for k in ("with", "without")
        ],
        "underground mall loss (USD)": [float(loss[k][under].sum()) for k in ("with", "without")],
    }
    fmt = {
        "buildings with loss > 0": "{:>18,d}",
        "total loss (USD)": "{:>18,.0f}",
        "loss, % of exposed value": "{:>18.3f}",
        "underground mall loss (USD)": "{:>18,.0f}",
    }
    for name, vals in row.items():
        print(f"  {name:<34}" + "".join(fmt[name].format(v) for v in vals))
    for key, title in (("with", "with buildings"), ("without", "no buildings")):
        print(f"  top 5 buildings by loss [{title}]")
        for i in np.argsort(-loss[key])[:5]:
            print(
                f"    #{int(i):<4} {b['building'].iloc[i]!s:<12}"
                f"{'(underground) ' if under[i] else ''}"
                f"height {float(b['height_m'].iloc[i]):5.1f} m"
                f"  depth {depth[key][i]:.2f} m  loss {loss[key][i]:>12,.0f} USD"
            )
    pts = [g.representative_point() for g in b.geometry]
    kinds = [
        spec["loss"]["underground_property_type"] if u else site_loss.property_type(k, spec)
        for k, u in zip(b["building"], under, strict=True)
    ]
    frame = pd.DataFrame(
        {
            "idx": np.arange(len(b)),
            "building": b["building"].to_numpy(),
            "property_type": kinds,
            "underground": under,
            "is_wall": b["is_wall"].to_numpy(),
            "height_m": b["height_m"].to_numpy(),
            "storeys": site_loss.storeys(b, spec),
            "area_m2": site_loss.footprint_area_m2(b),
            "exposed_value_usd": value,
            "depth_with_m": depth["with"],
            "depth_without_m": depth["without"],
            "loss_with_usd": loss["with"],
            "loss_without_usd": loss["without"],
            "lat": [p.y for p in pts],
            "lon": [p.x for p in pts],
        }
    )
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "site_losses.csv", index=False)
    print(f"  {spec['site']['attribution']['osm']}; {spec['site']['attribution']['dem']}")
    print(f"  wrote {out / 'site_losses.csv'}")
    return 0


def _animate_site(spec: dict[str, Any], out: Path) -> int:
    """Write the real-site rain page (gangnam_rain.html) from the --pluvial snapshots."""
    from climaterisk_worker.twin.site import build_site
    from climaterisk_worker.twin.viewer_site import render_site_view

    with_npz = out / "pluvial_snapshots.npz"
    without_npz = out / "pluvial_snapshots_nowalls.npz"
    for f in (with_npz, without_npz):
        if not f.exists():
            print(f"error: {f} not found - run with --pluvial first to write it", file=sys.stderr)
            return 1
    site = build_site(spec)
    page = render_site_view(
        with_npz,
        without_npz,
        site["grid"],
        site["buildings"],
        spec,
        out / "gangnam_rain.html",
        "Heavy rain on Gangnam station",
    )
    print(f"Wrote {page}")
    return 0


def _coupled_storm(
    site: dict[str, Any],
    spec: dict[str, Any],
    walls: Any,
    snapshot_every_s: float | None = 300.0,
) -> dict[str, Any]:
    """One coupled storm (street flow + basement sinks) on the site; shared by --basement
    and --rain-eal.

    Builds the basement sink, runs the 2D pluvial model with it, and derives ground entry
    depths, basement volumes, per-storey basement depths and street statistics.

    Args:
        site: Result of ``build_site``.
        spec: City spec (``rain.total_mm`` is the storm depth).
        walls: Boolean wall raster of this run (all False for the no-walls run).
        snapshot_every_s: Snapshot interval (s) or None.

    Returns:
        Dict with ``run`` (``run_pluvial`` result), ``ground_depth`` (n,), ``volume_m3`` (n,),
        ``basement_depth`` (n, max_levels), ``street_km2`` (flooded area > 10 cm),
        ``street_max_m``, ``n_levels``, ``area_m2``, ``capacity_m3`` and ``seconds``.
    """
    import time

    import numpy as np
    from climaterisk_worker.twin import basement, pluvial, site_loss

    grid, b, ground = site["grid"], site["buildings"], site["ground"]
    cell_m = float(spec["site"]["cell_m"])
    ff = float(spec["site"]["floor_to_floor_m"])
    n_lv = basement.basement_levels(b, spec)
    area = site_loss.footprint_area_m2(b)
    capacity = n_lv * area * ff
    t_end = (float(spec["rain"]["duration_min"]) + float(spec["rain"]["recession_min"])) * 60.0
    w = np.asarray(walls, dtype=bool)
    entr_r, entr_c = basement.entrance_cells(grid, b, ground, w, n_lv)
    sink = basement.BasementSink(entr_r, entr_c, capacity, cell_m, spec)
    t0 = time.perf_counter()
    r = pluvial.run_pluvial(
        ground, w, cell_m, spec, sink_fn=sink, snapshot_every_s=snapshot_every_s
    )
    seconds = time.perf_counter() - t0
    vol = sink.final_volume_m3(t_end)
    open_cells = ~w
    return {
        "run": r,
        "ground_depth": site_loss.entry_depths(grid, b, r["h_max"], w, spec),
        "volume_m3": vol,
        "basement_depth": basement.basement_depths(vol, area, n_lv, spec),
        "street_km2": float(((r["h_max"] > 0.10) & open_cells).sum()) * cell_m**2 / 1e6,
        "street_max_m": float(r["h_max"][open_cells].max()),
        "n_levels": n_lv,
        "area_m2": area,
        "capacity_m3": capacity,
        "seconds": seconds,
    }


def _print_validate(spec: dict[str, Any], out: Path) -> int:
    """Building-level validation against the Seoul flood-trace maps (step R4).

    Algorithm:
        Per event: model flag $m_b = \\mathbb{1}[d_b > T]$ with $d_b$ the entry depth of the
        loss steps and $T$ the no-damage threshold; observed flag $o_b$ = buffered footprint
        touches a trace polygon. Scores over wall buildings: POD, FAR, CSI, bias. Area-matched
        run: storm total $P^*$ with $A_{mod}(P^*) \\approx A_{trace}$ by log-space bisection.
        ASCII: m = depth > T; o = footprint+buffer intersects trace; CSI = h/(h+fa+miss).
    """
    import json

    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from climaterisk_worker.twin import rain_eal, site_loss, validate
    from climaterisk_worker.twin.site import build_site

    v = spec["validation"]
    thr = float(spec["loss"]["entry_threshold_m"])
    buf = float(v["buffer_m"])
    step = float(v["step_min"])
    flood_h = float(v["flooded_depth_m"])
    lo_mm, hi_mm = (float(x) for x in v["match_range_mm"])
    max_runs = int(v["match_max_runs"])
    site = build_site(spec)
    grid, b, ground = site["grid"], site["buildings"], site["ground"]
    walls_site = site["mask"]
    max_fr_ok = float(spec["hydraulic"]["max_valid_froude"])
    is_wall = b["is_wall"].to_numpy(dtype=bool)
    under = b["underground"].to_numpy(dtype=bool)
    score_set = is_wall & ~under
    bbox = tuple(float(x) for x in spec["site"]["bbox"])
    print("Validation against the Seoul flood-trace maps (서울시 침수흔적도, 공공누리 제1유형).")
    print("  Trace polygons are only queried (footprint + buffer intersects), never shipped.")
    print(f"  building observed-flooded = footprint buffered {buf:g} m touches a trace;")
    print(
        f"  model flooded = entry depth > {thr:g} m (loss-step rule); scored over wall buildings;"
    )
    print(f"  street area = open cells with h_max > {flood_h:g} m.")

    def runs_for(spec_run: dict[str, Any]) -> dict[str, Any]:
        return {
            "with": _coupled_storm(site, spec_run, walls_site, None),
            "without": _coupled_storm(site, spec_run, np.zeros_like(walls_site), None),
        }

    # per-building geometry, lowest-ground baseline
    ring_min = np.full(len(b), np.nan)
    for i, geom in enumerate(b.geometry):
        rows, cols = site_loss._cells(grid, geom)
        if is_wall[i]:
            rr, cc = site_loss.ring_cells(grid, rows, cols, walls_site)
            if rr.size:
                ring_min[i] = float(ground[rr, cc].min())
                continue
        ring_min[i] = float(ground[rows, cols].min())
    rep = b.geometry.representative_point()

    from shapely.geometry import box as shp_box

    site_box = gpd.GeoSeries([shp_box(*bbox)], crs="EPSG:4326").to_crs("EPSG:5179").iloc[0]

    def scores(flags: Any, obs: Any) -> dict[str, Any]:
        return validate.contingency(flags[score_set], obs[score_set])

    obs_by_year: dict[str, Any] = {}
    traced_km2: dict[str, float] = {}
    for year in v["events"]:
        tr = validate.read_traces(year, bbox)
        obs_by_year[year] = validate.trace_flags(b, tr, buf)
        clip = tr.geometry.intersection(site_box)
        traced_km2[year] = float(clip.union_all().area) / 1e6 if len(tr) else 0.0
        crs_name = tr.crs.to_string() if tr.crs else "none"
        print(
            f"  traces {year}: {len(tr)} polygons near the site, CRS {crs_name},"
            f" traced area in the site box {traced_km2[year]:.4f} km2,"
            f" observed-flooded buildings {int(obs_by_year[year][score_set].sum())}"
            f" of {int(score_set.sum())} wall buildings"
        )

    def match_total(year: str) -> tuple[float, dict[str, Any], int]:
        target = traced_km2[year]
        tried: list[tuple[float, float, dict[str, Any]]] = []

        def evaluate(mm: float) -> float:
            st = _coupled_storm(site, rain_eal.spec_for_rain(spec, mm), walls_site, None)
            tried.append((mm, st["street_km2"], st))
            return float(st["street_km2"])

        a_lo, a_hi = evaluate(lo_mm), evaluate(hi_mm)
        lo, hi = lo_mm, hi_mm
        if a_lo < target < a_hi:
            while len(tried) < max_runs:
                mid = float(np.sqrt(lo * hi))
                a = evaluate(mid)
                if a < target:
                    lo = mid
                else:
                    hi = mid
        best = min(tried, key=lambda r: abs(r[1] - target))
        return best[0], best[2], len(tried)

    events: dict[str, Any] = {}
    cols: dict[str, Any] = {}
    for year, ev in v["events"].items():
        obs = obs_by_year[year]
        n_obs = int(obs[score_set].sum())
        rec: dict[str, Any] = {
            "name": ev["name"],
            "n_wall_buildings": int(score_set.sum()),
            "observed_flooded_wall_buildings": n_obs,
            "traced_area_km2": traced_km2[year],
            "runs": {},
        }
        # naive lowest-ground baseline
        order = np.argsort(np.where(score_set, ring_min, np.inf))
        naive = np.zeros(len(b), dtype=bool)
        naive[order[:n_obs]] = True
        rec["lowest_ground_baseline"] = scores(naive, obs)
        cols[f"obs_{year}"] = obs
        cols[f"naive_lowest_{year}"] = naive
        planned: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        if "gauges" in ev:
            df = pd.concat(
                [
                    validate.read_gauge_file(pd.Timestamp(ev["start"]).year, m)
                    for m in sorted(
                        {pd.Timestamp(ev["start"]).month, pd.Timestamp(ev["end"]).month}
                    )
                ]
            )
            series = validate.gauge_series(
                df, ev["gauges"], pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"]), step
            )
            s_obs = validate.observed_rain_spec(spec, series.to_numpy(), step)
            peak10 = float(series.max())
            print(
                f"[{year}] {ev['name']}: observed rain, gauges {list(ev['gauges'])}, "
                f"{float(series.sum()):.1f} mm in {s_obs['rain']['duration_min'] / 60:.0f} h "
                f"(peak {peak10:.1f} mm / {step:g} min)"
            )
            st = runs_for(s_obs)
            planned.append(("observed_rain", {"total_mm": float(series.sum())}, st, s_obs))
        print(f"[{year}] area-matched storm (design-storm shape, total searched):")
        mm, _, n_runs = match_total(year)
        s_m = rain_eal.spec_for_rain(spec, mm)
        stm = runs_for(s_m)
        planned.append(
            (
                "area_matched",
                {
                    "total_mm": mm,
                    "search_runs": n_runs,
                    "note": "checks WHERE water goes, not how much rain causes it",
                },
                stm,
                s_m,
            )
        )
        print(f"    search used {n_runs} coupled runs (cap {max_runs}); best total {mm:.0f} mm")
        for label, info, st, _ in planned:
            rr: dict[str, Any] = dict(info)
            for key in ("with", "without"):
                s = st[key]
                flags = validate.model_flags(s["ground_depth"], thr)
                cols[f"model_{label}_{year}_{key}"] = flags
                fr = float(s["run"]["max_froude"])
                rr[key] = {
                    "street_flooded_km2": s["street_km2"],
                    "max_froude": fr,
                    "outside_validity": fr >= max_fr_ok,
                    "model_flooded_wall_buildings": int(flags[score_set].sum()),
                    "scores": scores(flags, obs),
                }
            rec["runs"][label] = rr
        events[year] = rec
        print(
            f"  {'run':<30}{'walls':<9}{'street km2':>11}{'traced':>9}{'model n':>9}{'obs n':>7}"
            f"{'POD':>7}{'FAR':>7}{'CSI':>7}{'bias':>7}{'Fr':>6}"
        )
        for label, rr in rec["runs"].items():
            for key in ("with", "without"):
                x = rr[key]
                sc = x["scores"]
                flag = " OUT" if x["outside_validity"] else ""
                print(
                    f"  {label + ' ' + format(rr['total_mm'], '.0f') + ' mm':<30}{key:<9}"
                    f"{x['street_flooded_km2']:>11.4f}{traced_km2[year]:>9.4f}"
                    f"{x['model_flooded_wall_buildings']:>9d}{n_obs:>7d}"
                    f"{sc['pod']:>7.2f}{sc['far']:>7.2f}{sc['csi']:>7.2f}{sc['bias']:>7.2f}"
                    f"{x['max_froude']:>6.2f}{flag}"
                )
        nb = rec["lowest_ground_baseline"]
        print(
            f"  {'baseline: N lowest ground':<30}{'-':<9}{'':>11}{'':>9}{n_obs:>9d}{n_obs:>7d}"
            f"{nb['pod']:>7.2f}{nb['far']:>7.2f}{nb['csi']:>7.2f}{nb['bias']:>7.2f}"
        )
    print("  2010/2011: no gauge rain; the area-matched runs check WHERE water goes, not how")
    print("  much rain causes it. Output holds per-building booleans and scores only.")
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_summary.json").write_text(
        json.dumps(
            {"buffer_m": buf, "entry_threshold_m": thr, "events": events}, indent=2, allow_nan=True
        ),
        encoding="utf-8",
    )
    table = pd.DataFrame(
        {
            "building": np.arange(len(b)),
            "lat": rep.y.to_numpy(),
            "lon": rep.x.to_numpy(),
            "is_wall": is_wall,
            "underground": under,
            **cols,
        }
    )
    table.to_csv(out / "validation_buildings.csv", index=False)
    print(f"  wrote {out / 'validation_summary.json'} and {out / 'validation_buildings.csv'}")
    return 0


def _print_basement(spec: dict[str, Any], out: Path) -> int:
    """Basements coupled both ways to the 2D street flow of the design storm, with/without walls."""

    import numpy as np
    import pandas as pd
    from climaterisk_worker.twin import basement, site_loss
    from climaterisk_worker.twin.site import build_site

    site = build_site(spec)
    b, ground = site["buildings"], site["ground"]
    walls_site = site["mask"]
    cell_m = float(spec["site"]["cell_m"])
    max_fr_ok = float(spec["hydraulic"]["max_valid_froude"])
    ff = float(spec["site"]["floor_to_floor_m"])
    n_lv = basement.basement_levels(b, spec)
    area = site_loss.footprint_area_m2(b)
    capacity = n_lv * area * ff
    has = n_lv > 0
    ground_value = float(site_loss.exposed_value_usd(b, spec).sum())
    keys = ("with", "without")
    res: dict[str, Any] = {}
    vol: dict[str, Any] = {}
    priced: dict[str, Any] = {}
    street: dict[str, tuple[float, float]] = {}
    out.mkdir(parents=True, exist_ok=True)
    for key, w, fname in (
        ("with", walls_site, "pluvial_basement_snapshots.npz"),
        ("without", np.zeros_like(walls_site), "pluvial_basement_snapshots_nowalls.npz"),
    ):
        storm = _coupled_storm(site, spec, w)
        r = storm["run"]
        print(f"  [{key} walls] coupled run {storm['seconds']:.0f} s, {r['n_steps']} steps")
        res[key] = r
        vol[key] = storm["volume_m3"]
        priced[key] = basement.price_with_basements(
            b, storm["ground_depth"], storm["basement_depth"], spec
        )
        street[key] = (storm["street_km2"], storm["street_max_m"])
        snaps = r["snapshots"]
        np.savez_compressed(
            out / fname,
            times_s=np.array([t for t, _ in snaps], dtype=float),
            depth=np.stack([h for _, h in snaps]).astype("float32"),
            bed=ground,
            cell_m=cell_m,
            walls=w,
            max_froude=float(r["max_froude"]),
            h_max=r["h_max"].astype("float32"),
            basement_m3=vol[key],
        )
    value = float(priced["with"]["basement_value_usd"].sum())
    print("Basements coupled both ways to the 2D street flow of ONE design storm")
    print("  (ramp weir Q = C B (h - T)^1.5 removed from the street every solver step).")
    print("  loss of ONE design storm; basement storeys, ramps and pumps are assumptions")
    print("  (no survey data); basements coupled both ways to the street flow.")
    print(f"  {'':<42}{'with walls':>18}{'without walls':>18}")

    def pct_rain(k: str) -> float:
        return 100.0 * float(res[k]["sink_fn_volume_m3"]) / float(res[k]["rain_volume_m3"])

    for k in keys:
        assert pct_rain(k) < 100.0, f"basements took {pct_rain(k):.1f} % of the rain ({k})"
    stored = {k: float(res[k]["h"].sum()) * cell_m**2 for k in keys}
    froude = {k: float(res[k]["max_froude"]) for k in keys}
    table: list[tuple[str, list[Any], str]] = [
        ("buildings with basements", [int(has.sum())] * 2, "{:>18,d}"),
        ("total basement value (USD)", [value] * 2, "{:>18,.0f}"),
        (
            "flooded basements (volume > 0)",
            [int((vol[k] > 0).sum()) for k in keys],
            "{:>18,d}",
        ),
        (
            "basement volume, taken from street (m3)",
            [float(res[k]["sink_fn_volume_m3"]) for k in keys],
            "{:>18,.0f}",
        ),
        ("  as % of storm rain volume", [pct_rain(k) for k in keys], "{:>18.3f}"),
        ("basement volume after pumps (m3)", [float(vol[k].sum()) for k in keys], "{:>18,.0f}"),
        ("volume balance (m3):", [], ""),
        ("  rain", [float(res[k]["rain_volume_m3"]) for k in keys], "{:>18,.0f}"),
        ("  sewer", [float(res[k]["sink_volume_m3"]) for k in keys], "{:>18,.0f}"),
        ("  basements", [float(res[k]["sink_fn_volume_m3"]) for k in keys], "{:>18,.0f}"),
        ("  outflow at edges", [float(res[k]["outflow_volume_m3"]) for k in keys], "{:>18,.0f}"),
        ("  stored in streets", [stored[k] for k in keys], "{:>18,.0f}"),
        ("max Froude", [froude[k] for k in keys], "{:>18.2f}"),
        (
            "street flooded area >10 cm (km2)",
            [street[k][0] for k in keys],
            "{:>18.4f}",
        ),
        ("street max depth (m)", [street[k][1] for k in keys], "{:>18.2f}"),
        (
            "ground-storey loss (USD)",
            [float(priced[k]["ground_loss_usd"].sum()) for k in keys],
            "{:>18,.0f}",
        ),
        (
            "basement loss (USD)",
            [float(priced[k]["basement_loss_usd"].sum()) for k in keys],
            "{:>18,.0f}",
        ),
        (
            "total loss (USD)",
            [float(priced[k]["loss_usd"].sum()) for k in keys],
            "{:>18,.0f}",
        ),
        (
            "total loss, % of ground + basement value",
            [100.0 * float(priced[k]["loss_usd"].sum()) / (ground_value + value) for k in keys],
            "{:>18.3f}",
        ),
    ]
    for name, vals, f in table:
        print(f"  {name:<42}" + "".join(f.format(v) for v in vals))
    for k, title in zip(keys, ("with walls", "without walls"), strict=True):
        if froude[k] >= max_fr_ok:
            print(f"  max Froude {froude[k]:.2f} [{title}]: OUTSIDE VALIDITY - indicative only")
    print("  uncoupled --pluvial street numbers for comparison: flooded area 0.076 / 0.061 km2,")
    print("  max depth 1.96 / 2.49 m (with / without walls)")
    for k, title in zip(keys, ("with walls", "without walls"), strict=True):
        loss = priced[k]["basement_loss_usd"]
        print(f"  top 5 buildings by basement loss [{title}]")
        for i in np.argsort(-loss)[:5]:
            fill = 100.0 * vol[k][i] / capacity[i] if capacity[i] > 0 else 0.0
            print(
                f"    #{int(i):<4} {b['building'].iloc[i]!s:<12}"
                f"height {float(b['height_m'].iloc[i]):5.1f} m  levels {int(n_lv[i])}"
                f"  volume {vol[k][i]:>9,.0f} m3  fill {fill:5.1f} %  loss {loss[i]:>12,.0f} USD"
            )
    pts = [g.representative_point() for g in b.geometry]
    kinds = [
        spec["loss"]["underground_property_type"] if u else site_loss.property_type(k, spec)
        for k, u in zip(b["building"], b["underground"], strict=True)
    ]
    frame = pd.DataFrame(
        {
            "idx": np.arange(len(b)),
            "building": b["building"].to_numpy(),
            "property_type": kinds,
            "is_wall": b["is_wall"].to_numpy(),
            "height_m": b["height_m"].to_numpy(),
            "storeys": site_loss.storeys(b, spec),
            "basement_levels": n_lv,
            "area_m2": area,
            "inflow_with_m3": vol["with"],
            "inflow_without_m3": vol["without"],
            "ground_loss_with_usd": priced["with"]["ground_loss_usd"],
            "basement_loss_with_usd": priced["with"]["basement_loss_usd"],
            "ground_loss_without_usd": priced["without"]["ground_loss_usd"],
            "basement_loss_without_usd": priced["without"]["basement_loss_usd"],
            "lat": [p.y for p in pts],
            "lon": [p.x for p in pts],
        }
    )
    frame.to_csv(out / "site_losses_basement.csv", index=False)
    print(f"  {spec['site']['attribution']['osm']}; {spec['site']['attribution']['dem']}")
    print(f"  wrote {out / 'site_losses_basement.csv'}")
    print(f"  and {out / 'pluvial_basement_snapshots.npz'},")
    print(f"      {out / 'pluvial_basement_snapshots_nowalls.npz'}")
    return 0


def _read_asos_rain(station: str) -> Any:
    """Hourly rain (mm, blank = 0) of one ASOS station from ``kma_dir()/asos_hourly/<station>``."""
    import pandas as pd

    from climaterisk import paths

    folder = paths.kma_dir() / "asos_hourly" / str(station)
    files = sorted(folder.glob("*.csv"))
    if not files:
        raise SystemExit(f"ASOS hourly files not found: {folder}")
    df = pd.concat([pd.read_csv(f, usecols=["tm", "rn"]) for f in files])
    rain = pd.Series(
        pd.to_numeric(df["rn"], errors="coerce").fillna(0.0).to_numpy(),
        index=pd.to_datetime(df["tm"]),
    )
    return rain, len(files)


def _price_storm_ladder(
    storms: list[dict[str, Any]],
    b: Any,
    spec: dict[str, Any],
    rps: list[float],
    unit_value: Any,
    ground_value: Any,
) -> dict[str, Any]:
    """Price K coupled storms (ground storeys and every basement storey) as ONE ladder each."""
    import numpy as np
    from climaterisk_worker.twin import rain_eal

    n = len(b)
    n_lv = storms[0]["n_levels"]
    g = rain_eal.price_ladder(
        b, np.stack([s["ground_depth"] for s in storms]), ground_value, rps, spec
    )
    bdepth = np.stack([s["basement_depth"] for s in storms])  # (K, n, L)
    base_event = np.zeros((len(storms), n))
    base_eal = np.zeros(n)
    base_portfolio = 0.0
    for k in range(bdepth.shape[2]):
        idx = np.nonzero(n_lv > k)[0]
        if idx.size == 0:
            continue
        lad = rain_eal.price_ladder(b.iloc[idx], bdepth[:, idx, k], unit_value[idx], rps, spec)
        base_event[:, idx] += lad["event_loss_usd"]
        base_eal[idx] += lad["eal_usd"]
        base_portfolio += lad["portfolio_eal_usd"]
    return {
        "ground_eal": g["eal_usd"],
        "basement_eal": base_eal,
        "ground_event": g["event_loss_usd"].sum(axis=1),
        "basement_event": base_event.sum(axis=1),
        "eal_total": float(g["portfolio_eal_usd"] + base_portfolio),
        "eal_ground_total": float(g["portfolio_eal_usd"]),
        "eal_basement_total": float(base_portfolio),
    }


def _print_rain_eal(spec: dict[str, Any], out: Path) -> int:
    """EAL of the Gangnam site over a ladder of 2-hour storms from the KMA ASOS record."""
    import json

    import numpy as np
    import pandas as pd
    from climaterisk_worker.twin import rain_eal, rain_frequency, site_loss
    from climaterisk_worker.twin.site import build_site

    rf = spec["rain_frequency"]
    dist = str(rf["distribution"])
    rps = [float(t) for t in rf["return_periods"]]
    t_arr = np.array(rps)
    rain, n_files = _read_asos_rain(str(rf["station"]))
    am = rain_frequency.annual_maxima(rain, int(rf["window_h"]))
    x = am.to_numpy()
    fit_g = rain_frequency.fit_gumbel(x)
    fit_v = rain_frequency.fit_gev(x)
    params = fit_v if dist == "gev" else fit_g
    central = rain_frequency.quantile(dist, params, t_arr)
    gumbel_levels = rain_frequency.quantile("gumbel", fit_g, t_arr)
    band = rain_frequency.bootstrap_quantiles(
        x, t_arr, dist, int(rf["bootstrap_n"]), int(rf["bootstrap_seed"]), tuple(rf["band"])
    )
    cases = {"central": central, "low": band[0], "high": band[1]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "rain_frequency.json").write_text(
        json.dumps(
            {
                "station": rf["station"],
                "window_h": rf["window_h"],
                "distribution": dist,
                "n_years": int(am.size),
                "annual_maxima_mm": {int(k): float(v) for k, v in am.items()},
                "gumbel": {"u_mm": fit_g[0], "alpha_mm": fit_g[1]},
                "gev": {"xi_mm": fit_v[0], "alpha_mm": fit_v[1], "k": fit_v[2]},
                "return_periods_years": rps,
                "return_level_mm": {
                    "central": central.tolist(),
                    "band_low": band[0].tolist(),
                    "band_high": band[1].tolist(),
                    "band_quantiles": list(rf["band"]),
                    "gumbel": gumbel_levels.tolist(),
                },
            },
            indent=2,
        )
    )

    site = build_site(spec)
    b, walls_site = site["buildings"], site["mask"]
    max_fr_ok = float(spec["hydraulic"]["max_valid_froude"])
    cost: dict[str, float] = spec["loss"]["cost_usd_per_m2"]
    area = site_loss.footprint_area_m2(b)
    unit = np.array([area[i] * float(cost[k]) for i, k in enumerate(site_loss._types(b, spec))])
    ground_value = site_loss.exposed_value_usd(b, spec)
    walls_by_key = {"with": walls_site, "without": np.zeros_like(walls_site)}

    storms: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case, levels in cases.items():
        for key, w in walls_by_key.items():
            runs = []
            for t, mm in zip(rps, levels, strict=True):
                st = _coupled_storm(site, rain_eal.spec_for_rain(spec, float(mm)), w, None)
                runs.append(st)
                print(
                    f"  [{case} {key} walls] T={t:g} y  {mm:6.1f} mm  {st['seconds']:.1f} s",
                    file=sys.stderr,
                )
            storms[(case, key)] = runs
    priced = {
        ck: _price_storm_ladder(runs, b, spec, rps, unit, ground_value)
        for ck, runs in storms.items()
    }

    pts = [g.representative_point() for g in b.geometry]
    c = priced
    frame = pd.DataFrame(
        {
            "idx": np.arange(len(b)),
            "building": b["building"].to_numpy(),
            "property_type": site_loss._types(b, spec),
            "basement_levels": storms[("central", "with")][0]["n_levels"],
            "area_m2": area,
            "eal_ground_with_usd": c[("central", "with")]["ground_eal"],
            "eal_basement_with_usd": c[("central", "with")]["basement_eal"],
            "eal_ground_without_usd": c[("central", "without")]["ground_eal"],
            "eal_basement_without_usd": c[("central", "without")]["basement_eal"],
            "lat": [p.y for p in pts],
            "lon": [p.x for p in pts],
        }
    )
    frame.to_csv(out / "rain_eal_buildings.csv", index=False)

    basement_value = float((storms[("central", "with")][0]["n_levels"] * unit).sum())
    total_value = float(ground_value.sum()) + basement_value
    per_rp = []
    for i, t in enumerate(rps):
        row: dict[str, Any] = {"return_period_years": t, "rain_mm": float(central[i])}
        for key in ("with", "without"):
            st = storms[("central", key)][i]
            pr = priced[("central", key)]
            row[key] = {
                "portfolio_storm_loss_ground_usd": float(pr["ground_event"][i]),
                "portfolio_storm_loss_basement_usd": float(pr["basement_event"][i]),
                "flooded_street_area_gt_10cm_km2": st["street_km2"],
                "basement_volume_m3": float(st["volume_m3"].sum()),
                "max_froude": float(st["run"]["max_froude"]),
            }
        per_rp.append(row)
    eal_totals = {
        case: {
            key: {
                "ground_usd": priced[(case, key)]["eal_ground_total"],
                "basement_usd": priced[(case, key)]["eal_basement_total"],
                "total_usd": priced[(case, key)]["eal_total"],
                "total_pct_of_value": 100.0 * priced[(case, key)]["eal_total"] / total_value,
            }
            for key in ("with", "without")
        }
        for case in cases
    }
    (out / "rain_eal_summary.json").write_text(
        json.dumps(
            {
                "note": "EAL over the return-period ladder; storms more frequent than the "
                "first rung cause no loss; rain from one station.",
                "station": rf["station"],
                "total_value_usd": total_value,
                "per_return_period": per_rp,
                "eal": eal_totals,
            },
            indent=2,
        )
    )

    print(
        "Expected annual loss (EAL) of the Gangnam site over the ladder of "
        f"{int(rf['window_h'])}-hour storms"
    )
    print(
        f"  rain: KMA ASOS station {rf['station']} ({n_files} yearly files), {am.size} annual "
        f"maxima {int(am.index.min())}-{int(am.index.max())}, {dist.upper()} by L-moments, "
        f"band = bootstrap {rf['band'][0]:.0%}-{rf['band'][1]:.0%} ({rf['bootstrap_n']} resamples)"
    )
    print(
        f"  GEV xi {fit_v[0]:.1f} mm alpha {fit_v[1]:.1f} mm k {fit_v[2]:.3f};  "
        f"Gumbel u {fit_g[0]:.1f} mm alpha {fit_g[1]:.1f} mm"
    )
    print(
        "  Storms more frequent than the first rung "
        f"(T < {rps[0]:g} y) are taken as no loss; the rain is ONE station ~7 km from the site."
    )
    print(
        f"  {'T (y)':>6}{'mm':>8}{'band low':>10}{'band high':>10}{'Gumbel':>8}"
        f"{'loss with (M USD)':>19}{'without':>9}{'street km2 w/wo':>17}{'bsmt m3 w/wo':>17}"
    )
    for i, row in enumerate(per_rp):
        w, wo = row["with"], row["without"]
        lw = (w["portfolio_storm_loss_ground_usd"] + w["portfolio_storm_loss_basement_usd"]) / 1e6
        lo = (wo["portfolio_storm_loss_ground_usd"] + wo["portfolio_storm_loss_basement_usd"]) / 1e6
        print(
            f"  {rps[i]:>6g}{central[i]:>8.1f}{band[0][i]:>10.1f}{band[1][i]:>10.1f}"
            f"{gumbel_levels[i]:>8.1f}{lw:>19.2f}{lo:>9.2f}"
            f"{w['flooded_street_area_gt_10cm_km2']:>9.3f}/{wo['flooded_street_area_gt_10cm_km2']:.3f}"
            f"{w['basement_volume_m3']:>9,.0f}/{wo['basement_volume_m3']:,.0f}"
        )
    print(f"  EAL (USD/yr) of {total_value / 1e9:.2f} bn ground + basement value:")
    print(f"  {'':<10}{'walls':<9}{'ground':>14}{'basement':>14}{'total':>14}{'% value':>9}")
    for case in cases:
        for key in ("with", "without"):
            e = eal_totals[case][key]
            print(
                f"  {case:<10}{key:<9}{e['ground_usd']:>14,.0f}{e['basement_usd']:>14,.0f}"
                f"{e['total_usd']:>14,.0f}{e['total_pct_of_value']:>9.3f}"
            )
    over = max(float(st["run"]["max_froude"]) for runs in storms.values() for st in runs)
    if over >= max_fr_ok:
        print(f"  max Froude {over:.2f}: OUTSIDE VALIDITY in some runs - indicative only")
    print("  Loss with basements coupled to the street flow; basements, ramps and pumps are")
    print("  assumptions (spec basement). Repo-specific pluvial method; not calibrated.")
    print(f"  {spec['site']['attribution']['osm']}; {spec['site']['attribution']['dem']}")
    print(f"  wrote {out / 'rain_frequency.json'}, {out / 'rain_eal_buildings.csv'},")
    print(f"        {out / 'rain_eal_summary.json'}")
    return 0


def _print_threshold_compare(spec: dict[str, Any], out: Path) -> int:
    """Effect of the no-damage threshold on the REAL buildings of known use (step R5).

    Runs the R3b central rain ladder through the coupled model with walls, takes the ground
    entry depths of the buildings whose use is known from OSM, and prices them with and
    without each threshold on the published JRC table (``pure``) and on CLIMADA's curve.

    Algorithm:
        $$\\Delta_c(T) = 100\\,\\frac{\\mathrm{EAL}_c(T) - \\mathrm{EAL}_c(\\mathrm{none})}
        {\\mathrm{EAL}_c(\\mathrm{none})}$$
        ASCII: delta_c(T) = 100 * (EAL_c(T) - EAL_c(none)) / EAL_c(none), per use and curve.

        $c$ curve (pure JRC table or CLIMADA), $T$ threshold (m), EAL in USD per year.
    """
    import copy
    import json

    import numpy as np
    import pandas as pd
    from climaterisk_worker.twin import rain_eal, rain_frequency, site_loss, threshold_compare
    from climaterisk_worker.twin.site import build_site

    tc = spec["threshold_compare"]
    rf = spec["rain_frequency"]
    dist = str(rf["distribution"])
    rps = [float(t) for t in rf["return_periods"]]
    rain, _ = _read_asos_rain(str(rf["station"]))
    am = rain_frequency.annual_maxima(rain, int(rf["window_h"]))
    x = am.to_numpy()
    params = rain_frequency.fit_gev(x) if dist == "gev" else rain_frequency.fit_gumbel(x)
    central = rain_frequency.quantile(dist, params, np.array(rps))

    site = build_site(spec)
    b, walls_site = site["buildings"], site["mask"]
    uses = threshold_compare.known_use(b, spec)
    sel = np.array([i for i, u in enumerate(uses) if u is not None], dtype=int)
    use_sel = np.array([uses[i] for i in sel])
    spec_raw = copy.deepcopy(spec)
    spec_raw["loss"]["entry_threshold_m"] = 0.0  # raw ring maximum: the threshold is the variable

    depth_rows = []
    for t, mm in zip(rps, central, strict=True):
        st = _coupled_storm(site, rain_eal.spec_for_rain(spec, float(mm)), walls_site, None)
        raw = site_loss.entry_depths(site["grid"], b, st["run"]["h_max"], walls_site, spec_raw)
        depth_rows.append(raw)
        print(
            f"  [central with walls] T={t:g} y  {mm:6.1f} mm  {st['seconds']:.1f} s",
            file=sys.stderr,
        )
    depth_all = np.stack(depth_rows)  # (K, n_all)
    b_sel = b.iloc[sel].reset_index(drop=True)
    depth = depth_all[:, sel]
    value = site_loss.exposed_value_usd(b, spec)[sel]
    thresholds: list[float | None] = [None, *[float(t) for t in tc["thresholds_m"]]]
    shown = [float(t) for t in tc["storms_shown"]]
    shown_idx = [rps.index(t) for t in shown]
    priced = {
        c: threshold_compare.price_thresholds(b_sel, depth, value, rps, thresholds, spec, c)
        for c in ("pure", "climada")
    }

    def pct(new: float, base: float) -> float | None:
        return None if base <= 0 else 100.0 * (new - base) / base

    groups = [
        ("residential", use_sel == "residential"),
        ("commercial", use_sel == "commercial"),
        ("industrial", use_sel == "industrial"),
        ("all known use", np.ones(len(sel), bool)),
    ]
    result: dict[str, Any] = {}
    for gname, mask in groups:
        g: dict[str, Any] = {"n_buildings": int(mask.sum()), "value_usd": float(value[mask].sum())}
        for c in ("pure", "climada"):
            base = priced[c][None]
            g[c] = {
                "n_with_loss": int((base["eal_usd"][mask] > 0).sum()),
                "eal_none_usd": float(base["eal_usd"][mask].sum()),
                "by_threshold": {},
            }
            for t in thresholds[1:]:
                r = priced[c][t]
                ent: dict[str, Any] = {
                    "eal_usd": float(r["eal_usd"][mask].sum()),
                    "eal_pct_change": pct(
                        float(r["eal_usd"][mask].sum()), float(base["eal_usd"][mask].sum())
                    ),
                    "storm_pct_change": {},
                }
                for k, rp in zip(shown_idx, shown, strict=True):
                    ent["storm_pct_change"][f"{rp:g}"] = pct(
                        float(r["event_loss_usd"][k][mask].sum()),
                        float(base["event_loss_usd"][k][mask].sum()),
                    )
                g[c]["by_threshold"][f"{t:g}"] = ent
            g[c]["storm_loss_none_usd"] = {
                f"{rp:g}": float(base["event_loss_usd"][k][mask].sum())
                for k, rp in zip(shown_idx, shown, strict=True)
            }
        result[gname] = g

    edges = [0.0, 0.05, 0.15, 0.30, 0.60]
    labels = ["0-5", "5-15", "15-30", "30-60", ">60"]
    dist_rows: dict[str, Any] = {}
    for k, rp in zip(shown_idx, shown, strict=True):
        d = depth[k]
        shares = {"dry (0)": float((d <= 0).mean() * 100)}
        for j, lab in enumerate(labels):
            hi = edges[j + 1] if j + 1 < len(edges) else np.inf
            shares[lab] = float(((d > edges[j]) & (d <= hi)).mean() * 100)
        dist_rows[f"{rp:g}"] = {"rain_mm": float(central[k]), "share_pct_by_depth_cm": shares}

    pts = [g.representative_point() for g in b_sel.geometry]
    frame = pd.DataFrame(
        {
            "idx": sel,
            "use": use_sel,
            "lat": [p.y for p in pts],
            "lon": [p.x for p in pts],
        }
    )
    for c in ("pure", "climada"):
        for t in thresholds:
            col = f"eal_{c}_{'none' if t is None else f'T{t:g}'}_usd"
            frame[col] = priced[c][t]["eal_usd"]
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "threshold_compare_buildings.csv", index=False)
    (out / "threshold_compare.json").write_text(
        json.dumps(
            {
                "return_periods_years": rps,
                "rain_mm_central": central.tolist(),
                "thresholds_m": tc["thresholds_m"],
                "storms_shown": shown,
                "n_buildings_total": len(b),
                "n_known_use": len(sel),
                "groups": result,
                "entry_depth_distribution": dist_rows,
                "note": "Ground storey only, buildings of OSM-known use only; money is "
                "indicative (assumed cost per m2), the percentages are the result.",
            },
            indent=2,
        )
    )

    print("Effect of the no-damage threshold T on the loss of the real Gangnam buildings")
    print(
        f"  {len(sel)} of {len(b)} OSM buildings have a known use "
        "(apartments/house/dormitory..., office/retail/hotel..., industrial/warehouse); "
        "'yes', schools, churches etc. are left out, not guessed."
    )
    print(
        "  Rain: central ladder, KMA ASOS 108; with walls; ground storey only (no basements); "
        f"ladder {rps[0]:g}-{rps[-1]:g} y, {central[0]:.0f}-{central[-1]:.0f} mm / 2 h."
    )
    print("  'pure' = published JRC table, linear from 0 m (no zero-damage point): the baseline")
    print("  of the user's sheet. The CLIMADA curve already has zero damage up to 0.05 m, so on it")
    print("  T = 0.05 m changes nothing.")
    print("  Entry depth = highest street depth around the building (no sill, raw, so that the")
    print("  threshold is the only cut-off).")
    t_cols = "".join(f"{'T=' + format(t, 'g') + ' m':>10}" for t in thresholds[1:])
    for gname, _ in groups:
        g = result[gname]
        print(
            f"\n  == {gname}: {g['n_buildings']} buildings, ground-storey value "
            f"{g['value_usd'] / 1e6:,.1f} M USD (indicative)"
        )
        for c in ("pure", "climada"):
            e = g[c]
            print(
                f"   curve {c}: {e['n_with_loss']} buildings with any loss, EAL no threshold "
                f"{e['eal_none_usd']:,.0f} USD/yr (indicative)"
            )
            print(f"     {'EAL change vs none':<28}{t_cols}")

            def cell(v: float | None) -> str:
                return f"{'n/a':>10}" if v is None else f"{v:>+9.1f}%"

            bt = e["by_threshold"]
            row = "".join(cell(bt[f"{t:g}"]["eal_pct_change"]) for t in thresholds[1:])
            print(f"     {'annual':<28}{row}")
            for rp in shown:
                row = "".join(
                    cell(bt[f"{t:g}"]["storm_pct_change"][f"{rp:g}"]) for t in thresholds[1:]
                )
                loss_m = e["storm_loss_none_usd"][f"{rp:g}"] / 1e6
                label = f"storm {rp:g} y (loss {loss_m:.2f} M)"
                print(f"     {label:<28}{row}")
    print("\n  Ground entry depth of the known-use buildings (share of buildings, %):")
    print(f"   {'storm':<14}{'mm':>6}" + "".join(f"{k + ' cm':>11}" for k in ["dry", *labels]))
    for rp in shown:
        r = dist_rows[f"{rp:g}"]
        sh = r["share_pct_by_depth_cm"]
        print(
            f"   {format(rp, 'g') + ' y':<14}{r['rain_mm']:>6.0f}"
            + "".join(f"{sh[k]:>11.1f}" for k in ["dry (0)", *labels])
        )
    print("\n  ASSUMPTIONS (everything that is not real):")
    for line in [
        "rain ladder from ONE station (ASOS 108), ~7 km from the site, GEV on annual maxima",
        "sewer capacity (spec rain/sewer) is assumed, uniform over the site",
        "basement sinks (spec basement) take water out of the streets and shape street depths",
        "threshold levels are illustrative (0.05 = sheet default; 0.15/0.30 raised sill; "
        "0.60 flood board), not surveyed",
        "cost per m2 only scales money; it does not change a percentage within a use",
        "flood depths are NOT validated: R4 against the Seoul flood traces showed the model "
        "under-floods (0.027 vs 0.252 km2), so the real share of shallow depths is uncertain",
        "ground entry depth = highest street cell touching the building (no per-building sill)",
    ]:
        print(f"   - {line}")
    print(
        "  REAL: building footprints and use (OSM) of the selected buildings; terrain "
        "(Copernicus GLO-30)."
    )
    print(f"  {spec['site']['attribution']['osm']}; {spec['site']['attribution']['dem']}")
    print(f"  wrote {out / 'threshold_compare.json'}, {out / 'threshold_compare_buildings.csv'}")
    return 0


REAL_ASSUMPTIONS = [
    "sewer capacity: ONE uniform value for every open cell (no pipe data on disk), chosen on ONE "
    "event (2022-08-08); it can over-fit that event and absorbs every other model error",
    "curve number of green cells = 74 (NRCS TR-55, open space good condition) assumes hydrologic "
    "soil group C; no soil map is on disk",
    "every cell outside an OSM green polygon is treated as paved (CN 98), roofs included; bare "
    "soil, unmapped lawns and trees are not pervious here",
    "the sewer drains green cells like streets (uniform sink on every open cell)",
    "upstream area (padded box) is assumed fully paved (CN 98), drained by the same sewer, "
    "delivers its excess to the receiving edge cell with NO travel-time lag, and is cut off at "
    "the pad distance; D8 uses the 30 m ground with no pit filling, no sewers, culverts or walls",
    "terrain: 30 m GLO-30 surface model resampled bilinearly to 10 m, buildings removed by OSM "
    "footprints and the ground interpolated underneath (vertical error of metres; a building "
    "missing in OSM stays in the ground)",
    "buildings are solid walls without doors, windows or openings; entry depth = highest street "
    "depth in the ring around the building, with no per-building sill or floor height",
    "no basements, no underground mall loss (basement storeys and ramps would be assumptions)",
    "rain 2022: mean of 3 Seoul gauges 1.5-3 km away (assumed representative of the site, "
    "uniform over it); 2010 and 2011 have no gauge rain, so the storm TOTAL is fitted to the "
    "traced area (spatial check only)",
    "design hyetograph shape (spec cumulative_fraction) for 2010/2011 and the ladder is assumed",
    "rain ladder from ONE station (ASOS 108, ~7 km away), GEV on annual maxima, central estimate",
    "uniform Manning n and open-edge slope (spec hydraulic / rain); flood depths with Froude "
    ">= max_valid_froude are outside the solver's validity",
    "traces are digitised at street scale: a building counts as flooded if a trace lies within "
    "the buffer (10 m, assumed); traced extents may be incomplete",
    "threshold levels are illustrative (0.05 = sheet default, 0.15/0.30 raised sill, 0.60 flood "
    "board), not surveyed; threshold 0.05 m in the pass/fail flag is the sheet default",
    "cost per m2 only scales money (assumed unit costs); exposed value = footprint x ONE storey "
    "(ground floor only), so no storey count or height is used",
]
REAL_DATA = [
    "building footprints and tags (OSM, 2026-10-13 extract); a building gets a loss only when its "
    "OSM 'building' value names a use (residential, commercial or industrial classes of spec "
    "threshold_compare.known_uses); all others are walls only",
    "terrain (Copernicus GLO-30)",
    "green polygons (OSM leisure / landuse / natural tags of spec landcover)",
    "observed 10-min rain of Seoul gauges 2401/101/2402 on 2022-08-08/09 (internal use)",
    "observed flood traces 2010, 2011, 2022 (Seoul flood-trace maps; polygons only queried)",
    "ASOS Seoul (108) hourly rain record 1961-2026 for the ladder",
    "published JRC Asia depth-damage curves through the repo engine (ImpactCalc)",
]


def _json_default(o: Any) -> Any:
    """JSON encoder for NumPy scalars and arrays."""
    import numpy as np

    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not serialisable: {type(o)}")


def _r4_reference(out: Path) -> dict[str, Any]:
    """The R4 (old twin) 2022 and spatial-check scores from ``validation_summary.json``."""
    import json

    f = out / "validation_summary.json"
    if not f.exists():
        return {}
    ev = json.loads(f.read_text(encoding="utf-8")).get("events", {})
    ref: dict[str, Any] = {}
    try:
        obs = ev["2022"]["runs"]["observed_rain"]["with"]
        ref["2022"] = {"scores": obs["scores"], "street_km2": obs["street_flooded_km2"]}
        for y in ("2011", "2010"):
            m = ev[y]["runs"]["area_matched"]["with"]
            ref[y] = {"scores": m["scores"], "street_km2": m["street_flooded_km2"]}
    except KeyError:
        return {}
    return ref


def _print_real(spec: dict[str, Any], out: Path) -> int:
    """Rebuilt Gangnam twin from real data only (step R6): calibrate, validate, price.

    Algorithm:
        Sewer capacity $s^* = \\arg\\max_s CSI(s)$ on the 2022-08-08 event over the wall
        buildings; 2010/2011 area-matched spatial checks and the R3b ladder / R5 threshold
        comparison are then run at $s^*$. See ``twin/real_rebuild.py``.
        ASCII: s* = argmax CSI(s); then spatial checks, ladder and thresholds at s*.
    """
    import copy
    import json

    import numpy as np
    import pandas as pd
    from climaterisk_worker.twin import (
        rain_frequency,
        site_loss,
        threshold_compare,
        validate,
    )
    from climaterisk_worker.twin import real_rebuild as rr

    v = spec["validation"]
    thr = float(spec["loss"]["entry_threshold_m"])
    buf = float(v["buffer_m"])
    step = float(v["step_min"])
    flood_h = float(v["flooded_depth_m"])
    lo_mm, hi_mm = (float(x) for x in v["match_range_mm"])
    max_runs = int(v["match_max_runs"])
    max_fr_ok = float(spec["hydraulic"]["max_valid_froude"])
    sewers = [float(s) for s in spec["calibration"]["sewer_mm_h"]]
    bbox = tuple(float(x) for x in spec["site"]["bbox"])
    cell_m = float(spec["site"]["cell_m"])

    print("Rebuilt Gangnam twin from real data only (step R6)")
    print("  user rule: a twin replicates real space; no building attribute is assumed.")
    twin = rr.build_real_twin(spec)
    site = twin["site"]
    grid, b, walls = site["grid"], site["buildings"], site["mask"]
    is_wall = b["is_wall"].to_numpy(dtype=bool)
    under = b["underground"].to_numpy(dtype=bool)
    score_set = is_wall & ~under
    uses = threshold_compare.known_use(b, spec)
    known = np.array([u is not None for u in uses])
    st = twin["runon_stats"]
    ro_area = twin["inflow"][2]
    print(
        f"  grid {grid.nx} x {grid.ny} cells of {cell_m:g} m; {len(b)} OSM buildings "
        f"({int(score_set.sum())} wall buildings, {int(known.sum())} of known use, "
        f"{int(under.sum())} underground); wall cells {int(walls.sum())} "
        f"({100 * walls.mean():.0f} % of the site)"
    )
    print(
        f"  green: {twin['n_green_polygons']} OSM polygons -> {int(twin['green'].sum())} cells "
        f"({100 * twin['green'].mean():.1f} % of the site), CN {spec['landcover']['cn_green']:g} "
        f"vs {spec['rain']['cn_paved']:g} elsewhere"
    )
    print(
        f"  run-on: ground padded by {spec['runon']['pad_m']:g} m -> {twin['padded_shape'][1]} x "
        f"{twin['padded_shape'][0]} cells ({twin['padded_buildings']} OSM buildings masked); "
        f"{st['n_edge_cells']:.0f} site edge cells receive flow from outside, total upstream "
        f"area {st['upstream_area_km2']:.3f} km2 (site {grid.nx * grid.ny * cell_m**2 / 1e6:.2f} "
        f"km2, padded box {twin['padded_shape'][0] * twin['padded_shape'][1] * cell_m**2 / 1e6:.1f}"
        f" km2); largest single entry {ro_area.max() / 1e6:.3f} km2"
    )
    print(
        f"  D8 pits (no lower neighbour, interior of the padded ground): "
        f"{st['pits_interior']:.0f} cells ({100 * st['pits_share']:.2f} %), largest pit basin "
        f"{st['largest_pit_km2']:.3f} km2 (no pit filling: water routed into a pit stays there)"
    )

    obs_by_year: dict[str, Any] = {}
    traced_km2: dict[str, float] = {}
    for year in v["events"]:
        obs_by_year[year], traced_km2[year] = rr.observed_flags(b, year, bbox, buf)  # type: ignore[arg-type]
        print(
            f"  traces {year}: traced area in the site box {traced_km2[year]:.4f} km2, "
            f"observed-flooded wall buildings {int(obs_by_year[year][score_set].sum())} "
            f"of {int(score_set.sum())}"
        )

    def scores(flags: Any, obs: Any) -> dict[str, Any]:
        return validate.contingency(flags[score_set], obs[score_set])

    ring_min = rr.ring_minimum(twin)

    # --- 1. calibration on 2022 -------------------------------------------------------------
    ev22 = v["events"]["2022"]
    start, end = pd.Timestamp(ev22["start"]), pd.Timestamp(ev22["end"])
    df = pd.concat(
        [validate.read_gauge_file(start.year, m) for m in sorted({start.month, end.month})]
    )
    series = validate.gauge_series(df, ev22["gauges"], start, end, step)
    obs22 = obs_by_year["2022"]
    n_obs22 = int(obs22[score_set].sum())
    print(
        f"\nCalibration of the uniform sewer capacity on [2022] {ev22['name']}: observed rain, "
        f"gauges {list(ev22['gauges'])}, {float(series.sum()):.0f} mm in "
        f"{len(series) * step / 60:.0f} h (peak {float(series.max()):.1f} mm / {step:g} min)"
    )
    print(
        f"  score = CSI over {int(score_set.sum())} wall buildings; model flooded = entry depth > "
        f"{thr:g} m; observed = footprint + {buf:g} m touches a trace ({n_obs22} buildings);"
    )
    print(f"  street area = open cells with h_max > {flood_h:g} m, traced {traced_km2['2022']:.4f}")
    hdr = (
        f"  {'sewer mm/h':>10}{'CSI':>7}{'POD':>7}{'FAR':>7}{'bias':>7}{'hits':>6}{'fa':>5}"
        f"{'miss':>6}{'model n':>9}{'street km2':>11}{'traced':>8}{'inflow/rain':>12}"
        f"{'balance':>9}{'Fr':>6}{'s':>6}"
    )
    print(hdr)
    cal: list[dict[str, Any]] = []
    for s_mm in sewers:
        s_obs = validate.observed_rain_spec(rr.with_sewer(spec, s_mm), series.to_numpy(), step)
        res = rr.run_storm(twin, s_obs)
        flags = validate.model_flags(res["entry_depth"], thr)
        sc = scores(flags, obs22)
        rec = {
            "sewer_mm_h": s_mm,
            "scores": sc,
            "street_km2": res["street_km2"],
            "traced_km2": traced_km2["2022"],
            "model_flooded_wall_buildings": int(flags[score_set].sum()),
            "inflow_over_rain": res["run"]["inflow_volume_m3"] / res["run"]["rain_volume_m3"],
            "balance_error": res["balance"]["relative_error"],
            "max_froude": res["max_froude"],
            "outside_validity": res["max_froude"] >= max_fr_ok,
            "seconds": res["seconds"],
            "flags": flags,
            "depth": res["entry_depth"],
        }
        cal.append(rec)
        print(
            f"  {s_mm:>10g}{sc['csi']:>7.3f}{sc['pod']:>7.2f}{sc['far']:>7.2f}{sc['bias']:>7.2f}"
            f"{sc['hits']:>6d}{sc['false_alarms']:>5d}{sc['misses']:>6d}"
            f"{rec['model_flooded_wall_buildings']:>9d}{res['street_km2']:>11.4f}"
            f"{traced_km2['2022']:>8.4f}{rec['inflow_over_rain']:>12.3f}"
            f"{rec['balance_error']:>9.1e}{res['max_froude']:>6.2f}{res['seconds']:>6.0f}"
            + (" OUT" if rec["outside_validity"] else "")
        )
    naive22 = rr.lowest_ground_flags(ring_min, score_set, n_obs22)
    nb22 = scores(naive22, obs22)
    print(
        f"  baseline: the {n_obs22} lowest-ground wall buildings flagged -> CSI {nb22['csi']:.3f} "
        f"(POD {nb22['pod']:.2f}, FAR {nb22['far']:.2f})"
    )
    best = max(range(len(cal)), key=lambda i: (cal[i]["scores"]["csi"], -i))
    s_star = float(cal[best]["sewer_mm_h"])
    csi_curve = [c["scores"]["csi"] for c in cal]
    edge = best in (0, len(cal) - 1)
    print(
        f"  -> calibrated sewer capacity {s_star:g} mm/h (CSI {csi_curve[best]:.3f})"
        + (
            "; the maximum is at the edge of the grid, so the optimum is not bracketed"
            if edge
            else ""
        )
    )
    spec_cal = rr.with_sewer(spec, s_star)

    # --- 2. spatial checks 2011, 2010 (independent of the calibration) ----------------------
    spatial: dict[str, Any] = {}
    print(
        "\nArea-matched spatial checks at the calibrated sewer (design-storm shape, storm total "
        "searched so the modelled street area matches the traced area; where, not how much):"
    )
    print(
        f"  {'year':<6}{'total mm':>9}{'runs':>6}{'street km2':>11}{'traced':>8}{'POD':>7}"
        f"{'FAR':>7}{'CSI':>7}{'bias':>7}{'naive CSI':>10}{'Fr':>6}"
    )
    for year in ("2011", "2010"):
        obs = obs_by_year[year]
        n_obs = int(obs[score_set].sum())
        mm, st_m, n_runs = rr.match_total(
            lambda m: ((r := rr.storm_for_total(twin, spec_cal, m))["street_km2"], r),
            traced_km2[year],
            lo_mm,
            hi_mm,
            max_runs,
        )
        flags = validate.model_flags(st_m["entry_depth"], thr)
        sc = scores(flags, obs)
        nv = rr.lowest_ground_flags(ring_min, score_set, n_obs)
        nsc = scores(nv, obs)
        spatial[year] = {
            "name": v["events"][year]["name"],
            "total_mm": mm,
            "search_runs": n_runs,
            "street_km2": st_m["street_km2"],
            "traced_km2": traced_km2[year],
            "scores": sc,
            "lowest_ground_baseline": nsc,
            "max_froude": st_m["max_froude"],
            "outside_validity": st_m["max_froude"] >= max_fr_ok,
            "balance_error": st_m["balance"]["relative_error"],
            "flags": flags,
            "naive": nv,
            "note": "checks WHERE water goes, not how much rain causes it",
        }
        print(
            f"  {year:<6}{mm:>9.0f}{n_runs:>6d}{st_m['street_km2']:>11.4f}"
            f"{traced_km2[year]:>8.4f}{sc['pod']:>7.2f}{sc['far']:>7.2f}{sc['csi']:>7.2f}"
            f"{sc['bias']:>7.2f}{nsc['csi']:>10.2f}{st_m['max_froude']:>6.2f}"
            + (" OUT" if spatial[year]["outside_validity"] else "")
        )

    # --- 3. before / after ------------------------------------------------------------------
    r4 = _r4_reference(out)
    at95 = next((c for c in cal if c["sewer_mm_h"] == 95.0), None)
    print("\nBefore / after (R4 old twin: walls, no run-on, no green, basements, sewer 95 mm/h):")
    print(f"  {'':<34}{'R4':>9}{'R6 @95':>9}{'R6 calib':>10}")

    def cellv(x: float | None, fmt: str = ".3f") -> str:
        return f"{'n/a':>9}" if x is None else f"{format(x, fmt):>9}"

    c6 = cal[best]
    rows_ba = [
        (
            "2022 CSI (observed rain)",
            r4.get("2022", {}).get("scores", {}).get("csi"),
            at95["scores"]["csi"] if at95 else None,
            c6["scores"]["csi"],
            ".3f",
        ),
        (
            "2022 POD",
            r4.get("2022", {}).get("scores", {}).get("pod"),
            at95["scores"]["pod"] if at95 else None,
            c6["scores"]["pod"],
            ".3f",
        ),
        (
            "2022 FAR",
            r4.get("2022", {}).get("scores", {}).get("far"),
            at95["scores"]["far"] if at95 else None,
            c6["scores"]["far"],
            ".3f",
        ),
        (
            f"2022 street area km2 (traced {traced_km2['2022']:.3f})",
            r4.get("2022", {}).get("street_km2"),
            at95["street_km2"] if at95 else None,
            c6["street_km2"],
            ".4f",
        ),
        (
            "2011 CSI (area-matched)",
            r4.get("2011", {}).get("scores", {}).get("csi"),
            None,
            spatial["2011"]["scores"]["csi"],
            ".3f",
        ),
        (
            "2010 CSI (area-matched)",
            r4.get("2010", {}).get("scores", {}).get("csi"),
            None,
            spatial["2010"]["scores"]["csi"],
            ".3f",
        ),
    ]
    for label, a, b95, c, fmt in rows_ba:
        print(f"  {label:<34}{cellv(a, fmt)}{cellv(b95, fmt)}{c:>10{fmt}}")
    print(f"  naive N-lowest-ground baseline CSI 2022: {nb22['csi']:.3f}")
    if not r4:
        print("  (R4 reference not found: run --validate first for the R4 column)")

    # --- 4. ladder (R3b central) and thresholds (R5) at the calibrated sewer -----------------
    rf = spec["rain_frequency"]
    dist = str(rf["distribution"])
    rps = [float(t) for t in rf["return_periods"]]
    rain, _ = _read_asos_rain(str(rf["station"]))
    am = rain_frequency.annual_maxima(rain, int(rf["window_h"]))
    params = (
        rain_frequency.fit_gev(am.to_numpy())
        if dist == "gev"
        else rain_frequency.fit_gumbel(am.to_numpy())
    )
    central = rain_frequency.quantile(dist, params, np.array(rps))
    spec_cal_raw = copy.deepcopy(spec_cal)
    spec_cal_raw["loss"]["entry_threshold_m"] = 0.0  # raw ring maximum: threshold is the variable
    ladder: list[dict[str, Any]] = []
    depth_rows = []
    print(
        f"\nRain ladder (central, {int(rf['window_h'])} h, ASOS {rf['station']}, "
        f"{dist.upper()}) on the R6 twin at sewer {s_star:g} mm/h, walls, no basements:"
    )
    print(
        f"  {'T (y)':>6}{'mm':>8}{'street km2':>11}{'max street m':>13}{'inflow/rain':>12}"
        f"{'balance':>9}{'Fr':>6}{'s':>5}"
    )
    for t, mm in zip(rps, central, strict=True):
        res = rr.storm_for_total(twin, spec_cal_raw, float(mm))
        depth_rows.append(res["entry_depth"])
        run = res["run"]
        rec = {
            "return_period_years": t,
            "rain_mm": float(mm),
            "street_km2": res["street_km2"],
            "street_max_m": float(run["h_max"][~walls].max()),
            "inflow_over_rain": run["inflow_volume_m3"] / run["rain_volume_m3"],
            "balance_error": res["balance"]["relative_error"],
            "max_froude": res["max_froude"],
            "outside_validity": res["max_froude"] >= max_fr_ok,
        }
        ladder.append(rec)
        print(
            f"  {t:>6g}{mm:>8.1f}{rec['street_km2']:>11.4f}{rec['street_max_m']:>13.2f}"
            f"{rec['inflow_over_rain']:>12.3f}{rec['balance_error']:>9.1e}"
            f"{rec['max_froude']:>6.2f}{res['seconds']:>5.0f}"
            + (" OUT" if rec["outside_validity"] else ""),
        )
    depth_all = np.stack(depth_rows)
    sel = np.flatnonzero(known)
    use_sel = np.array([uses[i] for i in sel])
    b_sel = b.iloc[sel].reset_index(drop=True)
    depth = depth_all[:, sel]
    value = site_loss.exposed_value_usd(b, spec)[sel]
    tc = spec["threshold_compare"]
    thresholds: list[float | None] = [None, *[float(t) for t in tc["thresholds_m"]]]
    shown = [float(t) for t in tc["storms_shown"]]
    priced = {
        c: threshold_compare.price_thresholds(b_sel, depth, value, rps, thresholds, spec, c)
        for c in ("pure", "climada")
    }
    result = rr.threshold_tables(depth, use_sel, value, priced, thresholds, rps, shown)
    dist_rows = rr.depth_distribution(depth, rps, shown, central)

    print(
        f"\nEffect of the no-damage threshold T on the {len(sel)} buildings of KNOWN OSM use "
        f"(of {len(b)}; the rest are walls only, never given a use):"
    )
    print("  ground storey only, no basements; 'pure' = published JRC table (linear from 0 m),")
    print("  'climada' = engine curve (already zero up to 0.05 m); entry depth = raw ring maximum.")
    t_cols = "".join(f"{'T=' + format(t, 'g') + ' m':>10}" for t in thresholds[1:])

    def cell(x: float | None) -> str:
        return f"{'n/a':>10}" if x is None else f"{x:>+9.1f}%"

    for gname, g in result.items():
        print(
            f"\n  == {gname}: {g['n_buildings']} buildings, ground-storey value "
            f"{g['value_usd'] / 1e6:,.1f} M USD (indicative)"
        )
        for c in ("pure", "climada"):
            e = g[c]
            print(
                f"   curve {c}: {e['n_with_loss']} buildings with any loss, EAL no threshold "
                f"{e['eal_none_usd']:,.0f} USD/yr (indicative)"
            )
            print(f"     {'EAL change vs none':<28}{t_cols}")
            bt = e["by_threshold"]
            print(
                f"     {'annual':<28}"
                + "".join(cell(bt[f"{t:g}"]["eal_pct_change"]) for t in thresholds[1:])
            )
            for rp in shown:
                row = "".join(
                    cell(bt[f"{t:g}"]["storm_pct_change"][f"{rp:g}"]) for t in thresholds[1:]
                )
                label = f"storm {rp:g} y (loss {e['storm_loss_none_usd'][f'{rp:g}'] / 1e6:.2f} M)"
                print(f"     {label:<28}{row}")
    labels = ["dry", "0-5", "5-15", "15-30", "30-60", ">60"]
    print("\n  Ground entry depth of the known-use buildings (share of buildings, %):")
    print(f"   {'storm':<10}{'mm':>6}" + "".join(f"{k + ' cm':>11}" for k in labels))
    for rp in shown:
        r = dist_rows[f"{rp:g}"]
        sh = r["share_pct_by_depth_cm"]
        print(
            f"   {format(rp, 'g') + ' y':<10}{r['rain_mm']:>6.0f}"
            + "".join(
                f"{sh[k]:>11.1f}" for k in ["dry (0)", "0-5", "5-15", "15-30", "30-60", ">60"]
            )
        )

    froude_out = [x for x in [*cal, *ladder, *spatial.values()] if x.get("outside_validity")]
    print(
        f"\n  Froude: {len(froude_out)} of {len(cal) + len(ladder) + len(spatial)} runs reach "
        f"Fr >= {max_fr_ok:g} (flagged OUT above): indicative only there."
    )
    print("\nASSUMPTIONS (everything in this result that is not real data):")
    for line in REAL_ASSUMPTIONS:
        print(f"  - {line}")
    print("\nREAL (taken from data, not assumed):")
    for line in REAL_DATA:
        print(f"  - {line}")
    print(f"  {spec['site']['attribution']['osm']}; {spec['site']['attribution']['dem']}")

    # --- outputs ----------------------------------------------------------------------------
    out.mkdir(parents=True, exist_ok=True)
    strip = lambda d: {k: x for k, x in d.items() if k not in ("flags", "depth", "naive")}  # noqa: E731
    summary = {
        "note": "R6 rebuild: real buildings as walls, OSM green CN, D8 run-on, no basements; "
        "sewer calibrated on one event. Booleans and depths per building are in "
        "real_buildings.csv; no trace geometry or gauge series is stored.",
        "grid": {"nx": grid.nx, "ny": grid.ny, "cell_m": cell_m},
        "n_buildings": len(b),
        "n_wall_buildings": int(score_set.sum()),
        "n_known_use": int(known.sum()),
        "green": {
            "polygons": twin["n_green_polygons"],
            "cells": int(twin["green"].sum()),
            "share": float(twin["green"].mean()),
        },
        "runon": {**st, "pad_m": spec["runon"]["pad_m"], "total_area_m2": float(ro_area.sum())},
        "calibration": {
            "event": ev22["name"],
            "sewer_mm_h_chosen": s_star,
            "optimum_at_grid_edge": edge,
            "curve": [strip(c) for c in cal],
            "lowest_ground_baseline": nb22,
        },
        "spatial_checks": {y: strip(x) for y, x in spatial.items()},
        "r4_reference": r4,
        "ladder": ladder,
        "threshold_compare": {
            "thresholds_m": tc["thresholds_m"],
            "storms_shown": shown,
            "groups": result,
            "entry_depth_distribution": dist_rows,
        },
        "assumptions": REAL_ASSUMPTIONS,
        "real": REAL_DATA,
    }
    (out / "real_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=True, default=_json_default), encoding="utf-8"
    )
    pts = b.geometry.representative_point()
    frame = pd.DataFrame(
        {
            "building": np.arange(len(b)),
            "osm_building": b["building"].to_numpy(),
            "known_use": [u if u is not None else "" for u in uses],
            "lat": pts.y.to_numpy(),
            "lon": pts.x.to_numpy(),
            "is_wall": is_wall,
            "underground": under,
            "obs_2022": obs22,
            "model_2022": c6["flags"],
            "entry_depth_2022_m": c6["depth"],
            "lowest_ground_2022": naive22,
        }
    )
    for year in ("2011", "2010"):
        frame[f"obs_{year}"] = obs_by_year[year]
        frame[f"model_{year}_matched"] = spatial[year]["flags"]
    for rp in shown:
        frame[f"entry_depth_T{rp:g}y_m"] = depth_all[rps.index(rp)]
    for c in ("pure", "climada"):
        for t in thresholds:
            col = f"eal_{c}_{'none' if t is None else f'T{t:g}'}_usd"
            vals = np.full(len(b), np.nan)
            vals[sel] = priced[c][t]["eal_usd"]
            frame[col] = vals
    frame.to_csv(out / "real_buildings.csv", index=False)
    print(f"  wrote {out / 'real_summary.json'} and {out / 'real_buildings.csv'}")
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
        "--animate",
        action="store_true",
        help="animated 3D page (flood_time.html) from the surge_snapshots.npz of --coastal2d",
    )
    ap.add_argument(
        "--heat",
        action="store_true",
        help="street-level UTCI (canyon MRT approximation) -> productivity loss",
    )
    ap.add_argument(
        "--site",
        action="store_true",
        help="real neighbourhood: OSM buildings + Copernicus DEM -> site_ground.tif, "
        "site_buildings.gpkg",
    )
    ap.add_argument(
        "--pluvial",
        action="store_true",
        help="design-storm rain on the real site, with and without buildings -> "
        "pluvial_snapshots.npz, pluvial_hmax.tif",
    )
    ap.add_argument(
        "--animate-site",
        action="store_true",
        help="animated 3D page (gangnam_rain.html) from the snapshots of --pluvial",
    )
    ap.add_argument(
        "--site-loss",
        action="store_true",
        help="per-building loss of the --pluvial design storm (site_losses.csv)",
    )
    ap.add_argument(
        "--basement",
        action="store_true",
        help="basements coupled both ways to the 2D street flow of the design storm "
        "(site_losses_basement.csv, pluvial_basement_snapshots*.npz)",
    )
    ap.add_argument(
        "--rain-eal",
        action="store_true",
        help="EAL of the site over a return-period ladder of storms from the KMA ASOS record "
        "(rain_frequency.json, rain_eal_buildings.csv, rain_eal_summary.json)",
    )
    ap.add_argument(
        "--threshold-compare",
        action="store_true",
        help="effect of the no-damage threshold on the real buildings of known use "
        "(threshold_compare.json, threshold_compare_buildings.csv)",
    )
    ap.add_argument(
        "--validate",
        action="store_true",
        help="building-level validation against the Seoul flood-trace maps "
        "(validation_summary.json, validation_buildings.csv)",
    )
    ap.add_argument(
        "--real",
        action="store_true",
        help="rebuilt twin from real data only (R6): OSM green + run-on, no basements; "
        "calibrates the sewer on 2022 (real_summary.json, real_buildings.csv)",
    )
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else paths.derived_dir("synthetic_city")
    spec = load_spec(args.spec)
    if args.site:
        return _print_site(spec, out)
    if args.pluvial:
        return _print_pluvial(spec, out)
    if args.site_loss:
        return _print_site_loss(spec, out)
    if args.basement:
        return _print_basement(spec, out)
    if args.real:
        return _print_real(spec, out)
    if args.validate:
        return _print_validate(spec, out)
    if args.rain_eal:
        return _print_rain_eal(spec, out)
    if args.threshold_compare:
        return _print_threshold_compare(spec, out)
    if args.animate_site:
        return _animate_site(spec, out)
    if args.animate:
        return _animate(spec, out)
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
