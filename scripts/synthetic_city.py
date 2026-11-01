#!/usr/bin/env python
"""Synthetic virtual city -> CLIMADA Exposures + river-flood Hazard -> the repo's engine.

No real building or GIS data is used: the city, its terrain, its asset values and its flood
are generated from ``assets/libraries/synthetic_city_spec.json`` with a pinned seed. The
pricing is not re-implemented — every asset goes through
:func:`climaterisk_worker.physical_risk.engine.calculate` (published JRC curve from
``registry``, sector from ``physical_risk_config.json``, ``ImpactCalc`` for the arithmetic),
exactly as a real facility does in ``physical_risk_batch.py``.

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/synthetic_city.py [--out DIR] [--protection-rp 50]

Outputs (default ``<DATA_ROOT>/derived/synthetic_city``): ``assets.csv``, ``assets.gpkg``,
``results.csv`` (one engine ResultRow per asset), ``network_by_rp.csv``, ``summary.json``
and ``city_map.png``.

Algorithm:
    Ground and flood depth at a point at distance $y$ (m) inland from the river:

    $$z = \\max(0,\\; z_b + s\\,y + \\varepsilon),\\quad \\varepsilon \\sim N(0, \\sigma^2)$$
    $$h(T) = h_2 + b \\ln(T / 2),\\qquad d(T) = \\max(0,\\; h(T) - z)$$

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
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SPEC_PATH = REPO / "assets" / "libraries" / "synthetic_city_spec.json"
MODEL_ID = "SYNTHETIC_CITY"
_M_PER_DEG_LAT = 111_320.0


def load_spec(path: str | Path = SPEC_PATH) -> dict[str, Any]:
    """The city specification (every assumption lives in this JSON)."""
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


def _to_latlon(x_m: float, y_m: float, grid: dict[str, Any]) -> tuple[float, float]:
    """Local metres east/north of the origin -> (lat, lon) on a flat-earth approximation."""
    lat0, lon0 = float(grid["origin_lat"]), float(grid["origin_lon"])
    lat = lat0 + y_m / _M_PER_DEG_LAT
    lon = lon0 + x_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return lat, lon


def _band(value: int, band: list[int]) -> bool:
    return band[0] <= value <= band[1]


def node_kind(rc: tuple[int, int], spec: dict[str, Any]) -> str:
    """Land use of the intersection at (row, col)."""
    fac, zone = spec["facilities"], spec["zoning"]
    if [rc[0], rc[1]] in fac["substations"]:
        return "substation"
    if [rc[0], rc[1]] == fac["hospital"]:
        return "hospital"
    if _band(rc[0], zone["industrial_rows"]):
        return "industrial"
    if _band(rc[0], zone["commercial_rows"]) and _band(rc[1], zone["commercial_cols"]):
        return "commercial"
    return "residential"


def build_city(spec: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """Street graph (NetworkX) and the asset list: one lot per node, one road per edge.

    Returns:
        ``(graph, assets)``. Graph nodes are ``(row, col)`` with ``kind``, ``x_m``, ``y_m``
        and ``z_m``; each asset dict carries the engine's facility fields plus ``kind``,
        ``node`` (or ``edge``) and ``ground_m``.
    """
    import networkx as nx
    import numpy as np

    rng = np.random.default_rng(int(spec["seed"]))
    grid, terr, vals, ptype = (
        spec["grid"],
        spec["terrain"],
        spec["values_usd"],
        spec["property_type"],
    )
    rows, cols, dx = int(grid["rows"]), int(grid["cols"]), float(grid["spacing_m"])

    graph = nx.grid_2d_graph(rows, cols)
    noise = rng.normal(0.0, float(terr["noise_sd_m"]), size=(rows, cols))
    for r, c in graph.nodes:
        y = r * dx
        z = float(terr["bank_m"]) + float(terr["slope_m_per_m"]) * y + float(noise[r, c])
        graph.nodes[(r, c)].update(kind=node_kind((r, c), spec), x_m=c * dx, y_m=y, z_m=max(0.0, z))

    sigma = float(vals["lognormal_sigma"])
    assets: list[dict[str, Any]] = []
    for r, c in sorted(graph.nodes):
        attrs = graph.nodes[(r, c)]
        kind = attrs["kind"]
        lat, lon = _to_latlon(attrs["x_m"], attrs["y_m"], grid)
        value = float(vals[kind]) * float(rng.lognormal(0.0, sigma))
        assets.append(
            {
                "facility_id": f"N{r:02d}_{c:02d}",
                "facility_name": f"{kind} lot ({r},{c})",
                "latitude": lat,
                "longitude": lon,
                "asset_value_usd": round(value, 2),
                "asset_value_currency": "USD",
                "property_type": ptype[kind],
                "kind": kind,
                "node": (r, c),
                "ground_m": attrs["z_m"],
            }
        )
    for a, b in sorted(graph.edges):
        na, nb = graph.nodes[a], graph.nodes[b]
        lat, lon = _to_latlon((na["x_m"] + nb["x_m"]) / 2, (na["y_m"] + nb["y_m"]) / 2, grid)
        assets.append(
            {
                "facility_id": f"E{a[0]:02d}_{a[1]:02d}-{b[0]:02d}_{b[1]:02d}",
                "facility_name": f"road {a}-{b}",
                "latitude": lat,
                "longitude": lon,
                "asset_value_usd": round(float(vals["road_per_m"]) * dx, 2),
                "asset_value_currency": "USD",
                "property_type": ptype["road"],
                "kind": "road",
                "edge": (a, b),
                "ground_m": (na["z_m"] + nb["z_m"]) / 2,
            }
        )
    return graph, assets


def layered_frequencies(return_periods: list[float]) -> Any:
    """f_i = 1/T_i - 1/T_{i+1}; the rarest event keeps 1/T_n (same convention as Aqueduct)."""
    import numpy as np

    t = np.asarray(sorted(return_periods), dtype=float)
    f = 1.0 / t
    f[:-1] -= 1.0 / t[1:]
    return f


def flood_depths(ground_m: Any, spec: dict[str, Any]) -> Any:
    """Depth matrix (n_events x n_points) from the stage law h(T) = h_2 + b ln(T/2)."""
    import numpy as np

    fl = spec["flood"]
    t = np.asarray(sorted(fl["return_periods"]), dtype=float)
    stage = float(fl["stage_2y_m"]) + float(fl["stage_slope_m"]) * np.log(t / 2.0)
    return np.maximum(0.0, stage[:, None] - np.asarray(ground_m, dtype=float)[None, :])


def build_hazard(assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """A CLIMADA river-flood ``Hazard`` (``RF``, metres) with one centroid per asset."""
    import numpy as np
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    rps = sorted(spec["flood"]["return_periods"])
    depth = flood_depths([a["ground_m"] for a in assets], spec)
    return Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(
            lat=np.array([a["latitude"] for a in assets]),
            lon=np.array([a["longitude"] for a in assets]),
        ),
        event_id=np.arange(1, len(rps) + 1),
        event_name=[f"rp{int(r)}" for r in rps],
        date=np.zeros(len(rps), dtype=int),
        frequency=layered_frequencies(rps),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(depth),
        fraction=sparse.csr_matrix((depth > 0).astype(float)),
    )


def _defences(hazard: Any, rp: float) -> tuple[Any, dict[str, Any]]:
    """Protected hazard and the engine's ``defences`` dict for a uniform design RP."""
    import numpy as np
    from climaterisk_worker.physical_risk import protection

    n = hazard.centroids.size
    rps = np.full(n, float(rp))
    # floods already at the most frequent layer (the engine's undefended_frequent_flooding)
    frequent = protection.local_intensity(hazard, 1.0 / float(np.max(hazard.frequency))) > 0
    return protection.protect(hazard, rps), {
        "undefended": hazard,
        "return_period": rps,
        "national_return_period": float(rp),
        "frequent": frequent,
    }


def price_assets(
    assets: list[dict[str, Any]], hazard: Any, protection_rp: float | None = None
) -> list[Any]:
    """One engine ``ResultRow`` per asset — the same call the batch runner makes."""
    from climaterisk_worker.physical_risk import engine

    priced_hazard, defences = hazard, None
    if protection_rp:
        priced_hazard, defences = _defences(hazard, protection_rp)
    rows = []
    for a in assets:
        facility = {k: v for k, v in a.items() if k not in ("node", "edge", "ground_m", "kind")}
        rows.append(
            engine.calculate(
                facility,
                priced_hazard,
                "RF",
                model_id=MODEL_ID,
                hazard_source="synthetic stage law (synthetic_city_spec.json)",
                hazard_dataset="synthetic_city",
                exposure_source="synthetic virtual city (NetworkX grid)",
                defences=defences,
            )
        )
    return rows


def portfolio_impact(assets: list[dict[str, Any]], hazard: Any) -> Any:
    """All assets in one ``Exposures`` priced in one ``ImpactCalc`` (cross-check + curve)."""
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    funcs: dict[str, Any] = {}
    ids = []
    for a in assets:
        sector = jrc_sector_for(a["property_type"])
        if sector not in funcs:
            funcs[sector] = registry.flood_impact_function("Asia", sector)
        ids.append(int(funcs[sector].id))
    frame = pd.DataFrame(
        {
            "latitude": [a["latitude"] for a in assets],
            "longitude": [a["longitude"] for a in assets],
            "value": [a["asset_value_usd"] for a in assets],
            "impf_RF": ids,
        }
    )
    exp = Exposures(frame, value_unit="USD")
    impact = ImpactCalc(exp, ImpactFuncSet(list(funcs.values())), hazard).impact(
        save_mat=False, assign_centroids=True
    )
    return exp, impact


def network_failure(
    graph: Any, assets: list[dict[str, Any]], hazard: Any, spec: dict[str, Any]
) -> list[dict[str, Any]]:
    """Per return period: failed nodes and residential value cut off from services."""
    import networkx as nx
    import numpy as np

    limit = float(spec["network"]["failure_depth_m"])
    node_idx = {a["node"]: i for i, a in enumerate(assets) if "node" in a}
    value = {a["node"]: a["asset_value_usd"] for a in assets if "node" in a}
    kind = nx.get_node_attributes(graph, "kind")
    depth = hazard.intensity.toarray()
    out = []
    for e, (name, freq) in enumerate(zip(hazard.event_name, hazard.frequency, strict=True)):
        failed = {n for n, i in node_idx.items() if depth[e, i] > limit}
        alive = graph.subgraph([n for n in graph.nodes if n not in failed])
        reach_h: set[Any] = set()
        reach_s: set[Any] = set()
        for comp in nx.connected_components(alive):
            kinds = {kind[n] for n in comp}
            if "hospital" in kinds:
                reach_h |= comp
            if "substation" in kinds:
                reach_s |= comp
        homes = [n for n in graph.nodes if kind[n] == "residential"]
        cut = [n for n in homes if n not in failed and (n not in reach_h or n not in reach_s)]
        out.append(
            {
                "event": name,
                "return_period_years": float(name[2:]),
                "frequency_per_year": float(freq),
                "failed_nodes": len(failed),
                "substations_failed": sum(kind[n] == "substation" for n in failed),
                "hospital_failed": any(kind[n] == "hospital" for n in failed),
                "residential_flooded": sum(kind[n] == "residential" for n in failed),
                "residential_cut_off": len(cut),
                "residential_cut_off_value_usd": float(np.sum([value[n] for n in cut])),
            }
        )
    return out


def plot_city(
    graph: Any,
    assets: list[dict[str, Any]],
    hazard: Any,
    rows: list[Any],
    spec: dict[str, Any],
    path: Path,
) -> None:
    """Two panels: 100-year depth with failed nodes, and per-lot EAL as % of value."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    names = list(hazard.event_name)
    e = names.index("rp100") if "rp100" in names else len(names) - 1
    depth = hazard.intensity.toarray()[e]
    limit = float(spec["network"]["failure_depth_m"])
    pos = {n: (graph.nodes[n]["x_m"], graph.nodes[n]["y_m"]) for n in graph.nodes}
    nodes = [(i, a) for i, a in enumerate(assets) if "node" in a]
    xy = np.array([pos[a["node"]] for _, a in nodes])
    pct = np.array([(rows[i].eal_as_pct_of_assets or 0.0) for i, _ in nodes])
    d = np.array([depth[i] for i, _ in nodes])

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), constrained_layout=True)
    for ax in axes:
        for a, b in graph.edges:
            ax.plot(*zip(pos[a], pos[b], strict=True), color="#bbbbbb", lw=0.6, zorder=1)
        ax.axhspan(-120, -20, color="#6baed6", alpha=0.6)
        ax.set_aspect("equal")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("distance inland (m)")
    sc = axes[0].scatter(
        xy[:, 0], xy[:, 1], c=d, cmap="Blues", s=40, zorder=2, edgecolor="k", lw=0.3
    )
    fail = d > limit
    axes[0].scatter(
        xy[fail, 0],
        xy[fail, 1],
        marker="x",
        color="red",
        s=25,
        zorder=3,
        label=f"out of service (> {limit} m)",
    )
    for kind, mk in (("substation", "s"), ("hospital", "P")):
        sel = np.array([a["kind"] == kind for _, a in nodes])
        axes[0].scatter(
            xy[sel, 0],
            xy[sel, 1],
            marker=mk,
            facecolor="none",
            edgecolor="orange",
            s=160,
            lw=2,
            zorder=4,
            label=kind,
        )
    axes[0].set_title("100-year flood depth (m) — synthetic river at the bottom")
    axes[0].legend(loc="upper right", fontsize=8)
    fig.colorbar(sc, ax=axes[0], shrink=0.8, label="depth (m)")
    sc2 = axes[1].scatter(
        xy[:, 0], xy[:, 1], c=pct, cmap="OrRd", s=40, zorder=2, edgecolor="k", lw=0.3
    )
    axes[1].set_title("EAL as % of lot value (engine ResultRow)")
    fig.colorbar(sc2, ax=axes[1], shrink=0.8, label="EAL %")
    fig.suptitle("Synthetic virtual city — all inputs assumed (synthetic_city_spec.json)")
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(out_dir: Path, spec: dict[str, Any], protection_rp: float | None = None) -> dict[str, Any]:
    """Generate, price, cross-check and write every output; returns the summary."""
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    graph, assets = build_city(spec)
    hazard = build_hazard(assets, spec)
    rows = price_assets(assets, hazard, protection_rp)

    priced = hazard
    if protection_rp:
        priced, _ = _defences(hazard, protection_rp)
    exp, impact = portfolio_impact(assets, priced)
    per_asset = np.array([r.eal_usd or 0.0 for r in rows])
    np.testing.assert_allclose(per_asset.sum(), impact.aai_agg, rtol=1e-9)

    net = network_failure(graph, assets, hazard if not protection_rp else priced, spec)
    total = float(sum(a["asset_value_usd"] for a in assets))
    by_kind: dict[str, dict[str, float]] = {}
    for a, r in zip(assets, rows, strict=True):
        k = by_kind.setdefault(a["kind"], {"n": 0, "value_usd": 0.0, "eal_usd": 0.0})
        k["n"] += 1
        k["value_usd"] += a["asset_value_usd"]
        k["eal_usd"] += r.eal_usd or 0.0
    for k in by_kind.values():
        k["eal_pct"] = 100.0 * k["eal_usd"] / k["value_usd"]
    curve = impact.calc_freq_curve([10, 50, 100, 500])
    summary = {
        "assumption": "synthetic city — every input assumed, see synthetic_city_spec.json",
        "seed": spec["seed"],
        "protection_rp_years": protection_rp,
        "n_assets": len(assets),
        "n_nodes": graph.number_of_nodes(),
        "n_edges": graph.number_of_edges(),
        "total_value_usd": total,
        "eal_usd": float(impact.aai_agg),
        "eal_pct": 100.0 * float(impact.aai_agg) / total,
        "loss_by_return_period_usd": {
            str(int(rp)): float(v) for rp, v in zip(curve.return_per, curve.impact, strict=True)
        },
        "by_kind": by_kind,
        "status_counts": {
            s: sum(r.calculation_status == s for r in rows)
            for s in {r.calculation_status for r in rows}
        },
        "expected_cut_off_value_usd_per_year": float(
            sum(n["frequency_per_year"] * n["residential_cut_off_value_usd"] for n in net)
        ),
        "engine_vs_portfolio_check": (
            "sum of per-asset engine EAL == portfolio ImpactCalc aai_agg (rtol 1e-9)"
        ),
    }

    keys = [
        "facility_id",
        "facility_name",
        "kind",
        "property_type",
        "latitude",
        "longitude",
        "ground_m",
        "asset_value_usd",
    ]
    with (out_dir / "assets.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(assets)
    gdf = exp.gdf.copy()
    gdf["facility_id"] = [a["facility_id"] for a in assets]
    gdf["kind"] = [a["kind"] for a in assets]
    gdf["eal_usd"] = per_asset
    gdf.to_file(out_dir / "assets.gpkg", driver="GPKG")
    dicts = [r.to_dict() for r in rows]
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dicts[0]), extrasaction="ignore")
        w.writeheader()
        for d in dicts:
            w.writerow(
                {
                    k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v)
                    for k, v in d.items()
                }
            )
    with (out_dir / "network_by_rp.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(net[0]))
        w.writeheader()
        w.writerows(net)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    plot_city(graph, assets, hazard, rows, spec, out_dir / "city_map.png")
    return summary


def main(argv: list[str] | None = None) -> int:
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
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else paths.derived_dir("synthetic_city")
    summary = run(out, load_spec(args.spec), args.protection_rp)
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
