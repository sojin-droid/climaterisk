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
    if args.rain_eal:
        return _print_rain_eal(spec, out)
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
