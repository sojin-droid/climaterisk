"""Orchestration: generate the city, build the hazard, price, cross-check, write outputs.

The pipeline ties the no-CLIMADA city generator (``twin.city``, ``twin.flood``) to the
CLIMADA hazard and pricing (``twin.hazard``, ``twin.pricing``).
Every loss number comes from :func:`climaterisk_worker.physical_risk.engine.calculate`
and CLIMADA's ``ImpactCalc`` — never re-implemented. The per-asset EAL sum is checked
against the portfolio ``aai_agg`` (rtol 1e-9).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from climaterisk_worker.twin.city import build_city
from climaterisk_worker.twin.hazard import build_hazard
from climaterisk_worker.twin.network import network_failure
from climaterisk_worker.twin.plot import plot_city
from climaterisk_worker.twin.pricing import _defences, portfolio_impact, price_assets
from climaterisk_worker.twin.spec import MODEL_ID  # noqa: F401  (re-exported for CLI/tests)

__all__ = ["run"]


def run(
    out_dir: Path,
    spec: dict[str, Any],
    protection_rp: float | None = None,
) -> dict[str, Any]:
    """Generate, price, cross-check and write every output; returns the summary.

    Algorithm:
        Ground elevation, depths and frequencies come from the spec JSON
        (:mod:`climaterisk_worker.twin`). CLIMADA assembles the hazard; the engine prices each
        asset; the portfolio ``aai_agg`` is the identity check::

            \\mathrm{EAL} = \\sum_i f_i\\,v\\,\\mathrm{MDR}(d_i)

        ASCII: EAL = sum_i f_i * v * MDR(d_i)

        $f_i$ event frequency (1/year), $v$ asset value (USD), $\\mathrm{MDR}$
        mean damage ratio at depth $d_i$ (m) from the JRC curve.

    Args:
        out_dir: Output directory (created if missing).
        spec: City specification.
        protection_rp: When set, flood defences to this uniform design return period
            (years) before pricing.

    Returns:
        The summary dict that is also written to ``summary.json``.
    """
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
    summary: dict[str, Any] = {
        "assumption": "synthetic city - every input assumed, see synthetic_city_spec.json",
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
