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
from climaterisk_worker.twin.climate import build_future_hazard, future_scenarios
from climaterisk_worker.twin.hazard import build_hazard
from climaterisk_worker.twin.inundation import hydraulic_depths
from climaterisk_worker.twin.network import network_failure
from climaterisk_worker.twin.plot import plot_city
from climaterisk_worker.twin.pricing import (
    _levee_rp_array,
    defences_for,
    portfolio_impact,
    price_assets,
)
from climaterisk_worker.twin.spec import MODEL_ID  # noqa: F401  (re-exported for CLI/tests)
from climaterisk_worker.twin.viewer import render_map
from climaterisk_worker.twin.viewer3d import render_3d

__all__ = ["compare_climate", "compare_defences", "compare_hazard_models", "run"]

#: The five defence scenarios shared by the present-day and climate-change comparisons:
#: no defence, a uniform design return period, and the local bank levee.
_DEFENCE_SCENARIOS: list[tuple[str, float | None, bool]] = [
    ("none", None, False),
    ("uniform_10y", 10.0, False),
    ("uniform_50y", 50.0, False),
    ("uniform_100y", 100.0, False),
    ("levee_bank_100y", None, True),
]


def _by_kind(assets: list[dict[str, Any]], rows: list[Any]) -> dict[str, dict[str, float]]:
    """Per-kind asset counts, value and EAL% from a per-asset ResultRow list."""
    out: dict[str, dict[str, float]] = {}
    for a, r in zip(assets, rows, strict=True):
        k = out.setdefault(a["kind"], {"n": 0, "value_usd": 0.0, "eal_usd": 0.0})
        k["n"] += 1
        k["value_usd"] += a["asset_value_usd"]
        k["eal_usd"] += r.eal_usd or 0.0
    for k in out.values():
        k["eal_pct"] = 100.0 * k["eal_usd"] / k["value_usd"]
    return out


def _scenario_rows(
    assets: list[dict[str, Any]],
    hazard: Any,
    spec: dict[str, Any],
    protection_rp: float | None,
    levee: bool,
    threshold_hazard: Any | None = None,
) -> tuple[list[Any], Any, Any]:
    """Price one defence scenario: returns (rows, priced_hazard, portfolio_impact).

    The per-asset ``price_assets`` call and the portfolio ``portfolio_impact`` call both see the
    same protected hazard, so the per-asset EAL sum must equal the portfolio ``aai_agg``
    (rtol 1e-9) — asserted by the caller.

    Args:
        threshold_hazard: When set (the present hazard), defences derive their design depth
            from it and apply it fixed to ``hazard`` — defences keep their present height
            under climate change (see :func:`twin.pricing.defences_for`).
    """
    import numpy as np

    priced, _ = defences_for(hazard, assets, spec, protection_rp, levee, threshold_hazard)
    rows = price_assets(
        assets,
        hazard,
        protection_rp=protection_rp,
        levee=levee,
        spec=spec,
        threshold_hazard=threshold_hazard,
    )
    _, impact = portfolio_impact(assets, priced)
    per_asset = np.array([r.eal_usd or 0.0 for r in rows])
    np.testing.assert_allclose(per_asset.sum(), impact.aai_agg, rtol=1e-9)
    return rows, priced, impact


def run(
    out_dir: Path,
    spec: dict[str, Any],
    protection_rp: float | None = None,
    levee: bool = False,
    map_path: Path | None = None,
    map3d_path: Path | None = None,
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
        levee: When true, apply the local bank levee from ``spec["levee"]`` (a per-centroid
            design return period on part of the bank; the rest stays undefended) instead of a
            uniform protection standard.
        map_path: When set, also write an interactive Folium HTML map
            (lots coloured by EAL %, return-period depth slider, out-of-service
            nodes) to this path.
        map3d_path: When set, also write a deck.gl 3D HTML page
            (``city_3d.html``) with extruded buildings coloured by EAL % and a
            selectable-return-period water surface. Storeys are geometry only
            (see :mod:`climaterisk_worker.twin.viewer3d`); no EAL number changes.

    Returns:
        The summary dict that is also written to ``summary.json``.
    """
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    graph, assets = build_city(spec)
    hazard = build_hazard(assets, spec)
    priced_hazard, _ = defences_for(hazard, assets, spec, protection_rp, levee)
    rows = price_assets(assets, hazard, protection_rp=protection_rp, levee=levee, spec=spec)
    exp, impact = portfolio_impact(assets, priced_hazard)
    per_asset = np.array([r.eal_usd or 0.0 for r in rows])
    np.testing.assert_allclose(per_asset.sum(), impact.aai_agg, rtol=1e-9)

    net = network_failure(graph, assets, priced_hazard, spec)
    total = float(sum(a["asset_value_usd"] for a in assets))
    by_kind = _by_kind(assets, rows)
    curve = impact.calc_freq_curve([10, 50, 100, 500])
    levee_cells = int(np.count_nonzero(_levee_rp_array(hazard, assets, spec) > 0)) if levee else 0
    summary: dict[str, Any] = {
        "assumption": "synthetic city - every input assumed, see synthetic_city_spec.json",
        "seed": spec["seed"],
        "protection_rp_years": protection_rp,
        "levee": levee,
        "levee_protected_cells": levee_cells,
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
    if map_path is not None:
        render_map(graph, assets, priced_hazard, rows, net, spec, map_path)
    if map3d_path is not None:
        # Storeys are geometry only (step 5); pricing/rows are unchanged.
        render_3d(assets, rows, spec, map3d_path)
    return summary


def compare_defences(spec: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Run the defence scenarios and write one comparison table.

    Scenarios (same city, seed from the spec): no defence, uniform design return period
    10/50/100 years, and a local levee along part of the bank. The city and hazard are
    generated once; each scenario is priced through the engine (:func:`price_assets`) and
    cross-checked against the portfolio ``aai_agg`` (rtol 1e-9).

    The known-trap service cut-off indicator is reported together with EAL: a defence that
    floods out nodes can *reduce* the cut-off count, so the two move together here rather
    than being compared on the cut-off value alone.

    Algorithm:
        Same step-EAL identity as :func:`run`::
            $$\\mathrm{EAL} = \\sum_i f_i\\,v\\,\\mathrm{MDR}(d_i)$$
        ASCII: EAL = sum_i f_i * v * MDR(d_i)

        $f_i$ event frequency (1/year), $v$ asset value (USD), $\\mathrm{MDR}$ mean damage
        ratio at depth $d_i$ (m) from the JRC curve.

    Args:
        spec: City specification (uses the ``levee`` section for the levee scenario).
        out_dir: Output directory (created if missing). Writes ``defence_scenarios.csv``
            (headline + per-kind EAL%) and ``defence_summary.json``.

    Returns:
        One record per scenario with ``scenario``, ``protection_rp_years``, ``levee``,
        ``eal_usd``, ``eal_pct``, ``expected_cut_off_value_usd_per_year`` and ``by_kind``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    graph, assets = build_city(spec)
    hazard = build_hazard(assets, spec)
    total = float(sum(a["asset_value_usd"] for a in assets))
    scenarios: list[tuple[str, float | None, bool]] = _DEFENCE_SCENARIOS
    table: list[dict[str, Any]] = []
    for name, rp, lev in scenarios:
        rows, priced, impact = _scenario_rows(assets, hazard, spec, rp, lev)
        net = network_failure(graph, assets, priced, spec)
        cut_off = float(
            sum(n["frequency_per_year"] * n["residential_cut_off_value_usd"] for n in net)
        )
        table.append(
            {
                "scenario": name,
                "protection_rp_years": rp,
                "levee": lev,
                "eal_usd": float(impact.aai_agg),
                "eal_pct": 100.0 * float(impact.aai_agg) / total,
                "expected_cut_off_value_usd_per_year": cut_off,
                "by_kind": _by_kind(assets, rows),
            }
        )
    kinds = sorted({a["kind"] for a in assets})
    headline = [
        "scenario",
        "protection_rp_years",
        "levee",
        "eal_pct",
        "eal_usd",
        "expected_cut_off_value_usd_per_year",
    ]
    fieldnames = headline + [f"{k}_eal_pct" for k in kinds]
    with (out_dir / "defence_scenarios.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for t in table:
            row: dict[str, Any] = {k: t[k] for k in headline}
            for k in kinds:
                row[f"{k}_eal_pct"] = t["by_kind"].get(k, {}).get("eal_pct", 0.0)
            w.writerow(row)
    (out_dir / "defence_summary.json").write_text(
        json.dumps(
            {"seed": spec["seed"], "total_value_usd": total, "scenarios": table},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return table


def _scenario_cut_off(
    graph: Any,
    assets: list[dict[str, Any]],
    priced: Any,
    spec: dict[str, Any],
) -> float:
    """Frequency-weighted residential value cut off from the hospital/substation (USD/yr).

    Reported alongside EAL per the known-trap rule: a defence that floods out nodes can
    *reduce* the cut-off count, so the two are never compared on the cut-off value alone.
    """
    net = network_failure(graph, assets, priced, spec)
    return float(sum(n["frequency_per_year"] * n["residential_cut_off_value_usd"] for n in net))


def compare_climate(spec: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Present vs future river stage for each defence scenario (levees keep present height).

    For every future scenario in the spec (`climate.future_scenarios`) and every defence
    scenario (none / 10 / 50 / 100 y / levee) the present and future EAL are priced, and the
    climate-change multiplier is future EAL / present EAL.

    **Levees keep their present height.** The per-centroid design depth is derived from the
    PRESENT hazard and applied fixed to the future hazard (``threshold_hazard=present``);
    re-deriving it from the future hazard would silently raise the levee and understate the
    multiplier — the AGENTS.md trap (≈1.22× instead of a larger value at +0.5 m). The future
    ``defences["undefended"]`` is the future hazard, so the undefended baseline is the future
    loss; the *defended* loss keeps the present design depth.

    The per-asset EAL sum is cross-checked against the portfolio ``aai_agg`` (rtol 1e-9) for
    both present and future, and the known-trap cut-off value is reported alongside EAL for
    both periods.

    Algorithm:
        $$m = \\frac{\\mathrm{EAL}^{future}}{\\mathrm{EAL}^{present}},\\\\qquad
          \\mathrm{EAL} = \\sum_i f_i\\,v\\,\\mathrm{MDR}(d_i)$$
        ASCII: m = EAL_future / EAL_present; EAL = sum_i f_i * v * MDR(d_i)

        $f_i$ event frequency (1/year), $v$ asset value (USD), $\\mathrm{MDR}$ mean damage
        ratio at depth $d_i$ (m) from the JRC curve, $m$ the climate-change multiplier
        (future / present, same defence, same model).

    Args:
        spec: City specification (uses the ``flood`` and ``climate`` sections).
        out_dir: Output directory (created if missing). Writes ``climate_scenarios.csv``
            (headline + per-kind present/future EAL%) and ``climate_summary.json``.

    Returns:
        One record per future scenario, each with ``scenario``, the stage shifts, and a
        ``defence_results`` table keyed by defence scenario name with ``present_eal_pct``,
        ``future_eal_pct``, ``climate_change_multiplier``, the present/future cut-off values
        and ``by_kind`` present/future EAL%.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    graph, assets = build_city(spec)
    total = float(sum(a["asset_value_usd"] for a in assets))
    present = build_hazard(assets, spec)
    results: list[dict[str, Any]] = []
    kinds = sorted({a["kind"] for a in assets})
    for scen in future_scenarios(spec):
        future = build_future_hazard(assets, spec, scen)
        rows: list[dict[str, Any]] = []
        for name, rp, lev in _DEFENCE_SCENARIOS:
            p_rows, p_priced, p_impact = _scenario_rows(assets, present, spec, rp, lev)
            f_rows, f_priced, f_impact = _scenario_rows(
                assets, future, spec, rp, lev, threshold_hazard=present
            )
            p_pct = 100.0 * float(p_impact.aai_agg) / total
            f_pct = 100.0 * float(f_impact.aai_agg) / total
            mult = float(f_impact.aai_agg) / float(p_impact.aai_agg) if p_impact.aai_agg else None
            rows.append(
                {
                    "scenario": name,
                    "protection_rp_years": rp,
                    "levee": lev,
                    "present_eal_pct": p_pct,
                    "future_eal_pct": f_pct,
                    "climate_change_multiplier": mult,
                    "present_cut_off_value_usd_per_year": _scenario_cut_off(
                        graph, assets, p_priced, spec
                    ),
                    "future_cut_off_value_usd_per_year": _scenario_cut_off(
                        graph, assets, f_priced, spec
                    ),
                    "present_by_kind": _by_kind(assets, p_rows),
                    "future_by_kind": _by_kind(assets, f_rows),
                }
            )
        results.append(
            {
                "scenario": scen["name"],
                "stage_2y_m_shift": float(scen["stage_2y_m_shift"]),
                "stage_slope_m_shift": float(scen["stage_slope_m_shift"]),
                "defence_results": rows,
            }
        )

    headline = [
        "scenario",
        "defence",
        "protection_rp_years",
        "levee",
        "present_eal_pct",
        "future_eal_pct",
        "climate_change_multiplier",
        "present_cut_off_value_usd_per_year",
        "future_cut_off_value_usd_per_year",
    ]
    fieldnames = headline + [
        f"{k}_{tag}" for k in kinds for tag in ("present_eal_pct", "future_eal_pct")
    ]
    with (out_dir / "climate_scenarios.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            for t in r["defence_results"]:
                row: dict[str, Any] = {
                    "scenario": r["scenario"],
                    "defence": t["scenario"],
                    "protection_rp_years": t["protection_rp_years"],
                    "levee": t["levee"],
                    "present_eal_pct": t["present_eal_pct"],
                    "future_eal_pct": t["future_eal_pct"],
                    "climate_change_multiplier": t["climate_change_multiplier"],
                    "present_cut_off_value_usd_per_year": t["present_cut_off_value_usd_per_year"],
                    "future_cut_off_value_usd_per_year": t["future_cut_off_value_usd_per_year"],
                }
                for k in kinds:
                    row[f"{k}_present_eal_pct"] = (
                        t["present_by_kind"].get(k, {}).get("eal_pct", 0.0)
                    )
                    row[f"{k}_future_eal_pct"] = t["future_by_kind"].get(k, {}).get("eal_pct", 0.0)
                w.writerow(row)
    (out_dir / "climate_summary.json").write_text(
        json.dumps(
            {"seed": spec["seed"], "total_value_usd": total, "scenarios": results},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return results


def compare_hazard_models(
    spec: dict[str, Any], out_dir: Path, durations_h: list[float] | None = None
) -> dict[str, Any]:
    """Bathtub stage law vs the 2D hydraulic model, priced through the same engine.

    For each flood-wave duration the 2D model (:func:`hydraulic_depths`) gives a peak
    depth per asset and return period; that matrix becomes a CLIMADA ``RF`` hazard and
    every defence scenario is priced exactly as for the stage law (per-asset engine rows,
    cross-checked against the portfolio ``aai_agg`` at rtol 1e-9). The stage law is the
    long-wave limit of the 2D model (water has time to reach every connected cell), so a
    shorter wave should give equal or lower EAL.

    Algorithm:
        $$r = \\frac{\\mathrm{EAL}^{2D}}{\\mathrm{EAL}^{stage}},\\qquad
          \\mathrm{EAL} = \\sum_i f_i\\,v\\,\\mathrm{MDR}(d_i)$$
        ASCII: r = EAL_2D / EAL_stage_law, same defence scenario and engine.

    Args:
        spec: City specification (uses the ``flood`` and ``hydraulic`` sections).
        out_dir: Output directory; writes ``hazard_models.csv`` and ``hazard_models.json``.
        durations_h: Flood-wave durations (h) to run; default the spec's
            ``flood_duration_h`` and a 6-hour flashy wave.

    Returns:
        ``{"rows": [...], "diagnostics": {...}}`` with one row per (model, defence) and, per
        2D run, the per-event Froude number and volume balance, the run's ``max_froude`` and
        ``within_validity`` (``max_froude < hydraulic.max_valid_froude``). A run outside
        validity breaks the local-inertial assumption; do not use its numbers.
    """
    import copy

    out_dir.mkdir(parents=True, exist_ok=True)
    graph, assets = build_city(spec)
    total = float(sum(a["asset_value_usd"] for a in assets))
    if durations_h is None:
        durations_h = [float(spec["hydraulic"]["flood_duration_h"]), 6.0]
    models: list[tuple[str, Any]] = [("stage_law", build_hazard(assets, spec))]
    diagnostics: dict[str, Any] = {}
    for dur in durations_h:
        s = copy.deepcopy(spec)
        s["hydraulic"]["flood_duration_h"] = float(dur)
        depth, diag = hydraulic_depths(graph, assets, s)
        name = f"2d_{dur:g}h"
        models.append((name, build_hazard(assets, spec, depth=depth)))
        max_fr = max(d["max_froude"] for d in diag)
        diagnostics[name] = {
            "events": diag,
            "max_froude": max_fr,
            "within_validity": max_fr < float(spec["hydraulic"]["max_valid_froude"]),
        }
    rows: list[dict[str, Any]] = []
    base: dict[str, float] = {}
    for model, hazard in models:
        for name, rp, lev in _DEFENCE_SCENARIOS:
            _, _, impact = _scenario_rows(assets, hazard, spec, rp, lev)
            eal = float(impact.aai_agg)
            if model == "stage_law":
                base[name] = eal
            rows.append(
                {
                    "model": model,
                    "defence": name,
                    "eal_pct": 100.0 * eal / total,
                    "ratio_to_stage_law": eal / base[name] if base[name] else None,
                }
            )
    with (out_dir / "hazard_models.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["model", "defence", "eal_pct", "ratio_to_stage_law"])
        w.writeheader()
        w.writerows(rows)
    result = {"seed": spec["seed"], "rows": rows, "diagnostics": diagnostics}
    (out_dir / "hazard_models.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
