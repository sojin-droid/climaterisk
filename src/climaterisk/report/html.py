"""Build a self-contained TCFD/ISSB-aligned HTML report for a portfolio run.

The report covers Strategy (scenario analysis) and Metrics & Targets (physical AAI,
transition carbon cost / NPV) per the TCFD/ISSB IFRS S2 structure, plus a method &
data-provenance appendix. It is a single HTML string (inline CSS) the user can open
or print to PDF.
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from climaterisk.core.entities import Portfolio
from climaterisk.runs.store import Run
from climaterisk.transition.carbon import TransitionResult


def _money(v: float, cur: str = "USD") -> str:
    return f"{cur}&nbsp;{v:,.0f}"


def _esc(s: object) -> str:
    return html.escape(str(s))


def _cost_benefit_section(cb: dict[str, Any] | None, cur: str) -> str:
    if not cb or cb.get("status") != "ok":
        return ""
    rows = "".join(
        f"<tr><td>{_esc(m['name'])}</td><td class='num'>{_money(m['cost'], cur)}</td>"
        f"<td class='num'>{_money(m['benefit'], cur)}</td>"
        f"<td class='num'>{'—' if m.get('benefit_cost_ratio') is None else round(m['benefit_cost_ratio'], 2)}</td></tr>"
        for m in cb.get("measures", [])
    )
    return (
        "<h2>Adaptation — cost-benefit</h2>"
        f"<p>Total climate risk (NPV, unaverted): <strong>{_money(cb.get('tot_climate_risk', 0), cur)}</strong>"
        f" · discount {cb.get('discount_rate', 0):.1%} · horizon {_esc(cb.get('future_year'))}.</p>"
        "<table><thead><tr><th>Measure</th><th>Cost</th><th>Benefit (NPV averted)</th>"
        f"<th>Benefit/cost</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _uncertainty_section(unc: dict[str, Any] | None, cur: str) -> str:
    if not unc or unc.get("status") != "ok":
        return ""
    sens = "".join(
        f"<tr><td>{_esc(k.replace('_', ' '))}</td><td class='num'>{round(v, 2)}</td></tr>"
        for k, v in sorted(unc.get("sensitivity", {}).items(), key=lambda kv: -kv[1])
    )
    return (
        "<h2>Uncertainty &amp; sensitivity</h2>"
        f"<p>Monte-Carlo ({unc.get('n_samples')} samples): mean AAI "
        f"<strong>{_money(unc.get('aai_mean', 0), cur)}/yr</strong>, "
        f"P5–P95 {_money(unc.get('aai_p5', 0), cur)} – {_money(unc.get('aai_p95', 0), cur)} "
        f"(σ {_money(unc.get('aai_std', 0), cur)}).</p>"
        "<table><thead><tr><th>Input</th><th>Sensitivity (|corr|)</th></tr></thead>"
        f"<tbody>{sens}</tbody></table>"
    )


def _supplychain_section(sc: dict[str, Any] | None, cur: str) -> str:
    if not sc or sc.get("status") != "ok":
        return ""
    rows = "".join(
        f"<tr><td>{_esc(s.get('sector'))}</td><td class='num'>{_money(s.get('indirect', 0), cur)}</td></tr>"
        for s in sc.get("by_sector", [])[:10]
    )
    return (
        "<h2>Supply-chain (indirect impact)</h2>"
        f"<p>Direct AAI <strong>{_money(sc.get('total_direct', 0), cur)}</strong>; "
        f"gross indirect production change <strong>{_money(sc.get('total_indirect', 0), cur)}</strong> "
        f"via the {_esc(sc.get('mriot'))} input-output table (Leontief).</p>"
        "<table><thead><tr><th>Sector</th><th>Indirect</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _forecast_section(fc: dict[str, Any] | None, cur: str) -> str:
    if not fc or fc.get("status") != "ok":
        return ""
    if not fc.get("n_tracks"):
        return "<h2>Operational forecast</h2><p>No active TC tracks in the latest ECMWF feed.</p>"
    return (
        "<h2>Operational forecast</h2>"
        f"<p>ECMWF ensemble: <strong>{fc.get('n_tracks')}</strong> tracks; "
        f"forecast impact <strong>{_money(fc.get('total_impact', 0), cur)}</strong> over the portfolio.</p>"
    )


def _calibration_section(cal: dict[str, Any] | None) -> str:
    if not cal or cal.get("status") != "ok":
        return ""
    return (
        "<h2>Impact-function calibration</h2>"
        f"<p>{_esc(cal.get('country'))} TC <code>{_esc(cal.get('param'))}</code> calibrated to "
        f"EM-DAT losses: {round(cal.get('initial', 0), 1)} → "
        f"<strong>{round(cal.get('calibrated', 0), 1)} m/s</strong>.</p>"
    )


def build_html_report(
    portfolio: Portfolio,
    run: Run | None,
    transition: TransitionResult,
    cost_benefit: dict[str, Any] | None = None,
    uncertainty: dict[str, Any] | None = None,
    supplychain: dict[str, Any] | None = None,
    forecast: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
) -> str:
    cur = portfolio.assets[0].currency if portfolio.assets else "USD"
    sector_of = {a.id: str(a.sector) for a in portfolio.assets}
    name_of = {a.id: a.name for a in portfolio.assets}
    value_of = {a.id: a.value for a in portfolio.assets}

    phys_of: dict[str, float] = defaultdict(float)
    country_of: dict[str, str] = {}
    peril_results: list[dict[str, Any]] = []
    if run is not None and run.output:
        peril_results = run.output.get("results", [])
        for r in peril_results:
            if r.get("status") == "ok":
                for a in r.get("per_asset", []):
                    phys_of[a["id"]] += a["eai"]
                    if a.get("country"):
                        country_of[a["id"]] = a["country"]
    trans_of = {a.id: a.npv for a in transition.per_asset}
    total_value = sum(value_of.values())
    total_phys = sum(phys_of.values())
    total_trans = transition.total_npv

    def agg(key_of) -> list[tuple[str, int, float, float, float]]:  # type: ignore[no-untyped-def]
        g: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
        for a in portfolio.assets:
            k = key_of(a) or "—"
            g[k][0] += 1
            g[k][1] += value_of[a.id]
            g[k][2] += phys_of.get(a.id, 0.0)
            g[k][3] += trans_of.get(a.id, 0.0)
        rows = [(k, int(v[0]), v[1], v[2], v[3]) for k, v in g.items()]
        return sorted(rows, key=lambda r: r[3] + r[4], reverse=True)

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # --- physical peril rows ---
    phys_rows = ""
    for r in peril_results:
        interp = r.get("interpretation") or r.get("detail") or ""
        if r.get("status") != "ok":
            phys_rows += (
                f"<tr><td>{_esc(r['peril'].replace('_', ' '))}</td>"
                f"<td colspan='4'>{_esc(interp)}</td></tr>"
            )
            continue
        delta = r.get("delta_pct")
        delta_s = f"{delta:+.1f}%" if delta is not None else "—"
        present = r.get("present_aai_agg")
        phys_rows += (
            f"<tr><td>{_esc(r['peril'].replace('_', ' '))}</td>"
            f"<td>{_esc(r.get('target_year'))}</td>"
            f"<td class='num'>{_money(present, cur) if present is not None else '—'}</td>"
            f"<td class='num'>{_money(r['aai_agg'], cur)}</td>"
            f"<td class='num'>{delta_s}</td></tr>"
        )
        if interp:  # plain-language meaning beneath the row (esp. when AAI is 0)
            phys_rows += (
                f"<tr><td></td><td colspan='4' style='color:#8b98a5;font-size:12px'>"
                f"{_esc(interp)}</td></tr>"
            )

    def agg_table(title: str, rows, key_label: str) -> str:  # type: ignore[no-untyped-def]
        body = "".join(
            f"<tr><td>{_esc(k.replace('_', ' '))}</td><td class='num'>{n}</td>"
            f"<td class='num'>{_money(val, cur)}</td>"
            f"<td class='num'>{_money(phys, cur)}</td>"
            f"<td class='num'>{_money(tr, cur)}</td></tr>"
            for (k, n, val, phys, tr) in rows
        )
        return (
            f"<h3>{_esc(title)}</h3><table><thead><tr><th>{_esc(key_label)}</th><th>#</th>"
            "<th>Exposed value</th><th>Physical AAI/yr</th><th>Transition NPV</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )

    asset_rows = "".join(
        f"<tr><td>{_esc(name_of[a.id])}</td><td>{_esc(sector_of[a.id].replace('_', ' '))}</td>"
        f"<td>{_esc(country_of.get(a.id, '—'))}</td>"
        f"<td class='num'>{_money(value_of[a.id], cur)}</td>"
        f"<td class='num'>{_money(phys_of.get(a.id, 0.0), cur)}</td>"
        f"<td class='num'>{_money(trans_of.get(a.id, 0.0), cur)}</td></tr>"
        for a in portfolio.assets
    )

    by_sector = agg_table("By sector", agg(lambda a: str(a.sector)), "Sector")
    by_country = agg_table(
        "By country (national)", agg(lambda a: country_of.get(a.id, "—")), "Country"
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>climaterisk report — {_esc(portfolio.name)}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; color: #1a2128; max-width: 900px;
          margin: 0 auto; padding: 32px; line-height: 1.5; }}
  h1 {{ margin-bottom: 2px; }} .sub {{ color: #667; margin-top: 0; }}
  h2 {{ border-bottom: 2px solid #2f9e8f; padding-bottom: 4px; margin-top: 28px; }}
  h3 {{ margin: 16px 0 6px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin: 6px 0 12px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #dde; }}
  th {{ color: #667; font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .kpis {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 12px 0; }}
  .kpi {{ background: #f4f7f6; border: 1px solid #dde; border-radius: 8px; padding: 12px 16px; }}
  .kpi .v {{ font-size: 22px; font-weight: 700; }} .kpi .l {{ font-size: 11px; color: #667; text-transform: uppercase; }}
  .caveat {{ background: #fdf1f0; border: 1px solid #e5534b; border-radius: 6px; padding: 12px; font-size: 12px; }}
  .pill {{ background: #eef; border: 1px solid #dde; border-radius: 999px; padding: 1px 8px; font-size: 12px; }}
  footer {{ margin-top: 28px; color: #889; font-size: 11px; }}
</style></head><body>
<h1>Climate Risk Report</h1>
<p class="sub">{_esc(portfolio.name)} · generated {generated} · TCFD / ISSB (IFRS&nbsp;S2) aligned</p>

<h2>Portfolio overview</h2>
<div class="kpis">
  <div class="kpi"><div class="v">{len(portfolio.assets)}</div><div class="l">Facilities</div></div>
  <div class="kpi"><div class="v">{_money(total_value, cur)}</div><div class="l">Exposed value</div></div>
  <div class="kpi"><div class="v">{_money(total_phys, cur)}/yr</div><div class="l">Physical AAI</div></div>
  <div class="kpi"><div class="v">{_money(total_trans, cur)}</div><div class="l">Transition NPV</div></div>
</div>
<p>Climate scenario <span class="pill">{_esc(portfolio.scenario.climate)}</span> ·
   transition scenario <span class="pill">{_esc(portfolio.scenario.transition)}</span> ·
   horizon anchors {_esc(portfolio.scenario.anchor_years)}.</p>

<h2>Strategy — scenario analysis</h2>
<p>Physical risk under the selected climate pathway, present-day baseline vs future horizon
(probabilistic CLIMADA hazard × per-asset vulnerability × exposure):</p>
<table><thead><tr><th>Peril</th><th>Horizon</th><th>Present AAI/yr</th><th>Future AAI/yr</th>
<th>Δ</th></tr></thead><tbody>{phys_rows or "<tr><td colspan='5'>No physical run.</td></tr>"}</tbody></table>

<h2>Metrics &amp; targets — transition risk</h2>
<p>Carbon-cost passthrough under NGFS <span class="pill">{_esc(transition.scenario)}</span>
(discount rate {transition.discount_rate:.1%}): NPV {_money(total_trans, cur)} over
{_esc(transition.base_year)}–{_esc(transition.years[-1] if transition.years else "—")}.</p>

{_cost_benefit_section(cost_benefit, cur)}
{_uncertainty_section(uncertainty, cur)}
{_supplychain_section(supplychain, cur)}
{_forecast_section(forecast, cur)}
{_calibration_section(calibration)}

<h2>Aggregation</h2>
{by_sector}
{by_country if any(country_of.values()) else ""}

<h2>Per-facility detail</h2>
<table><thead><tr><th>Facility</th><th>Sector</th><th>Country</th><th>Value</th>
<th>Physical AAI/yr</th><th>Transition NPV</th></tr></thead><tbody>{asset_rows}</tbody></table>

<h2>Method &amp; data provenance</h2>
<p>Physical risk = Hazard × Exposure × Vulnerability (CLIMADA): AAI = Σ events (frequency × damage).
Hazard from the CLIMADA Data API (tropical cyclone: synthetic IBTrACS-perturbed tracks, future
RCP × reference year; river flood: ISIMIP-derived depth, RCP × year-range). Vulnerability: Emanuel
(2011) TC wind-damage with per-class v_half; Huizinga-style flood depth-damage. Transition risk =
emissions × NGFS Phase-5 carbon price (REMIND-MAgPIE, US$2010/t, via pyam), NPV-discounted.</p>
<div class="caveat"><strong>Provenance.</strong> NGFS carbon prices are real (frozen Phase-5
snapshot). Sector emission intensities (used only where Scope-1 is not reported) and the
vulnerability-curve values are indicative MVP heuristics, not yet calibrated. CLIMADA is GPL-3.0
and is run as a separate process. Do not present these figures as authoritative without calibration.</div>

<footer>climaterisk · framework that orchestrates open-source climate-risk engines (CLIMADA, NGFS).
This report is auto-generated; figures depend on inputs and the bundled methodology libraries.</footer>
</body></html>"""
