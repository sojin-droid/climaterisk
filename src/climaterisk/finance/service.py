"""Glue between a completed run and the finance core: resolve profiles (override →
portfolio → cited defaults), turn per-asset AAI + transition cost into the annual climate
loss, and run :func:`core.assess` at the portfolio level and per overridden asset."""

from __future__ import annotations

from typing import Any

from climaterisk.core.entities import FinancialProfile, Portfolio
from climaterisk.finance import core, models


def _defaults(ref: dict[str, Any]) -> dict[str, float]:
    return {k: float(v["value"]) for k, v in ref["financing_defaults"].items()}


def resolve_profile(
    primary: FinancialProfile | None,
    fallback: FinancialProfile | None,
    ref: dict[str, Any],
) -> core.FinancialProfile:
    """Field-by-field: per-asset override → portfolio default → cited financing defaults."""
    d = _defaults(ref)

    def pick(attr: str, default: float) -> float:
        for src in (primary, fallback):
            v = getattr(src, attr, None) if src else None
            if v is not None:
                return float(v)
        return float(default)

    return core.FinancialProfile(
        capex=pick("capex", 0.0),
        annual_ebitda=pick("annual_ebitda", 0.0),
        horizon_years=int(pick("horizon_years", d["horizon_years"])),
        debt_fraction=pick("debt_fraction", d["debt_fraction"]),
        debt_tenor_years=int(pick("debt_tenor_years", d["debt_tenor_years"])),
        risk_free_rate=pick("risk_free_rate", d["risk_free_rate"]),
        baseline_spread_bps=pick("baseline_spread_bps", d["baseline_spread_bps"]),
        baseline_equity_rate=pick("baseline_equity_rate", d["baseline_equity_rate"]),
    )


def _pick(attr: str, *sources: FinancialProfile | None) -> Any:
    """First non-None value of ``attr`` across the profiles (override → fallback)."""
    for s in sources:
        v = getattr(s, attr, None) if s else None
        if v is not None:
            return v
    return None


def resolve_generation(
    primary: FinancialProfile | None,
    fallback: FinancialProfile | None,
    channels_ref: dict[str, Any],
) -> models.GenerationInputs | None:
    """Build generation economics (override → fallback → fuel/library defaults). Returns None
    if capacity, price or capacity factor cannot be resolved (so the caller falls back)."""
    gd = channels_ref.get("generation_defaults", {})
    cap = _pick("capacity_mw", primary, fallback)
    price = _pick("power_price", primary, fallback)
    cf = _pick("capacity_factor", primary, fallback)
    if cf is None:
        fuel = _pick("plant_fuel", primary, fallback)
        if fuel:
            cf = gd.get("capacity_factor_by_fuel", {}).get(fuel)
    if cap is None or price is None or cf is None:
        return None
    var = _pick("opex_per_mwh", primary, fallback)
    if var is None:
        var = float(gd.get("opex_per_mwh", {}).get("value", 0.0))
    return models.GenerationInputs(
        capacity_mw=float(cap),
        power_price=float(price),
        capacity_factor=float(cf),
        fixed_opex=float(_pick("fixed_opex", primary, fallback) or 0.0),
        opex_per_mwh=float(var),
    )


def resolve_channels(
    primary: FinancialProfile | None,
    fallback: FinancialProfile | None,
    channels_ref: dict[str, Any],
) -> models.ChannelMagnitudes:
    """Resolve stressed-scenario channel magnitudes (override → fallback → cited defaults)."""
    ch = channels_ref.get("channels", {})

    def resolve(attr: str, group: str, key: str) -> float:
        v = _pick(attr, primary, fallback)
        if v is not None:
            return float(v)
        return float(ch.get(group, {}).get(key, 0.0))

    return models.ChannelMagnitudes(
        dispatch_penalty=resolve("dispatch_penalty", "dispatch", "default_penalty"),
        outage_rate=resolve("outage_rate", "outage", "default_rate"),
        capacity_derate=resolve("capacity_derate", "water_derate", "default_derate"),
        efficiency_loss=resolve("efficiency_loss", "efficiency", "default_loss"),
    )


def ebitda_pair(
    primary: FinancialProfile | None,
    fallback: FinancialProfile | None,
    core_profile: core.FinancialProfile,
    annual_aai: float,
    carbon_cost: float,
    channels_ref: dict[str, Any],
) -> models.EbitdaPair:
    """Produce the (baseline, stressed) EBITDA pair via the selected asset financial model.

    ``power_gen`` builds EBITDA from generation and stresses the capacity factor; any other
    model (or incomplete generation inputs) falls back to ``generic`` (EBITDA − AAI − carbon)."""
    model = _pick("financial_model", primary, fallback) or models.GENERIC
    if model == models.POWER_GEN:
        gen = resolve_generation(primary, fallback, channels_ref)
        if gen is not None:
            ch = resolve_channels(primary, fallback, channels_ref)
            return models.power_gen_pair(gen, ch, annual_aai=annual_aai, carbon_cost=carbon_cost)
    return models.generic_pair(core_profile.annual_ebitda, annual_aai + carbon_cost)


def selected_method_ids(profile: FinancialProfile | None, ref: dict[str, Any]) -> list[str]:
    """Ordered list of methodology ids to compare. Prefers the multi-select ``rating_methods``,
    falls back to the single ``rating_method`` (back-compat), else the library default."""
    default_id = str(ref.get("default_rating_method", "moodys_sp"))
    if profile and profile.rating_methods:
        ids = [m for m in profile.rating_methods if m]
        if ids:
            return ids
    if profile and profile.rating_method:
        return [profile.rating_method]
    return [default_id]


def resolve_rating_method(
    profile: FinancialProfile | None, ref: dict[str, Any], method_id: str
) -> dict[str, Any]:
    """Resolve one methodology id to its DSCR→rating grid plus display metadata
    (id/label/code/source). 'custom' uses the profile's editable grid; an unknown id falls
    back to the library default."""
    methods: dict[str, Any] = ref.get("rating_methods", {})
    default_id = str(ref.get("default_rating_method", "moodys_sp"))

    if method_id == "custom":
        thresholds = (
            [t.model_dump() for t in profile.custom_rating_thresholds]
            if profile and profile.custom_rating_thresholds
            else list(ref.get("rating_dscr_thresholds", []))
        )
        return {
            "method": "custom",
            "label": "Custom (user-defined)",
            "code": "Custom",
            "source": "User-defined DSCR→rating grid",
            "thresholds": thresholds,
        }

    method = methods.get(method_id) or methods.get(default_id)
    if method is not None:
        resolved_id = method_id if method_id in methods else default_id
        return {
            "method": resolved_id,
            "label": method.get("label", resolved_id),
            "code": method.get("code", method.get("short", resolved_id)),
            "source": method.get("source", ""),
            "thresholds": method["thresholds"],
        }
    # Last-resort fallback for older libraries without rating_methods.
    return {
        "method": "moodys_sp",
        "label": "Moody's / S&P",
        "code": "Agency",
        "source": "",
        "thresholds": ref["rating_dscr_thresholds"],
    }


def per_asset_aai(run_output: dict[str, Any]) -> dict[str, float]:
    """Sum each asset's expected annual **monetary** impact across all OK perils.

    Only ``result_kind == "monetary"`` perils are summed. Non-damage perils report in
    other units entirely — a fraction (``productivity``, ``yield``) or persons
    (``mortality``) — so adding them into a currency total would be meaningless and, for
    deaths, actively misleading downstream (EBITDA / DSCR / rating).
    """
    loss: dict[str, float] = {}
    for r in run_output.get("results", []):
        if r.get("status") != "ok":
            continue
        if (r.get("result_kind") or "monetary") != "monetary":
            continue
        for pa in r.get("per_asset", []):
            loss[pa["id"]] = loss.get(pa["id"], 0.0) + float(pa.get("eai", 0.0) or 0.0)
    return loss


def compute_finance(
    portfolio: Portfolio,
    run_output: dict[str, Any],
    transition_annual_cost: float,
    ref: dict[str, Any],
    channels_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Portfolio-level + per-asset-override climate-risk-premium assessment for a run.

    ``channels_ref`` is the ``finance_channels`` library, needed only by the power-generation
    model; the generic model ignores it."""
    channels_ref = channels_ref or {}
    aai = per_asset_aai(run_output)
    total_physical = sum(aai.values())
    port_ep = portfolio.run_config.financial_profile
    transition = max(0.0, transition_annual_cost)
    port_profile = resolve_profile(port_ep, None, ref)

    # The asset financial model decides the (baseline, stressed) EBITDA pair — generic
    # (EBITDA − AAI − carbon) or power_gen (generation through the operational channels).
    # Compute it once; the rating methodology only changes the DSCR→rating grid downstream.
    port_pair = ebitda_pair(port_ep, None, port_profile, total_physical, transition, channels_ref)

    # The user may select several DSCR→rating "house views" to compare. Assess the portfolio
    # under each (swapping the grid into an effective ref — core reads rating_dscr_thresholds);
    # the first selected method is the primary used for the headline and per-asset ratings.
    method_ids = selected_method_ids(port_ep, ref)
    methods_compared: list[dict[str, Any]] = []
    for mid in method_ids:
        r = resolve_rating_method(port_ep, ref, mid)
        eff = {**ref, "rating_dscr_thresholds": r["thresholds"]}
        methods_compared.append(
            {
                "method": r["method"],
                "label": r["label"],
                "code": r["code"],
                "source": r["source"],
                "scenario": core.assess_ebitda(
                    port_profile, port_pair.baseline, port_pair.stressed, eff
                ),
            }
        )

    primary = resolve_rating_method(port_ep, ref, method_ids[0])
    eff_ref = {**ref, "rating_dscr_thresholds": primary["thresholds"]}
    portfolio_result = methods_compared[0]["scenario"]

    per_asset: list[dict[str, Any]] = []
    for a in portfolio.assets:
        if a.financial_profile is None:
            continue  # only assets with their own profile get a per-asset CRP (under the primary)
        a_profile = resolve_profile(a.financial_profile, port_ep, ref)
        # Per-asset carbon is not split out of the portfolio total yet → 0 here.
        a_pair = ebitda_pair(
            a.financial_profile, port_ep, a_profile, aai.get(a.id, 0.0), 0.0, channels_ref
        )
        res = core.assess_ebitda(a_profile, a_pair.baseline, a_pair.stressed, eff_ref)
        per_asset.append(
            {"id": a.id, "name": a.name, "model": a_pair.breakdown.get("model"), **res}
        )

    cur = portfolio.assets[0].currency if portfolio.assets else "USD"
    return {
        "currency": cur,
        "total_physical_aai": total_physical,
        "transition_annual_cost": transition,
        "rating_method": primary["method"],
        "rating_method_label": primary["label"],
        "rating_method_source": primary["source"],
        "rating_thresholds": primary["thresholds"],
        "methods_compared": methods_compared,
        "financial_model": port_pair.breakdown.get("model"),
        "portfolio_breakdown": port_pair.breakdown,
        "portfolio": portfolio_result,
        "per_asset": per_asset,
        "detail": (
            f"Climate risk premium {portfolio_result['crp_bps']:+.0f} bps "
            f"({portfolio_result['baseline']['rating']} → {portfolio_result['stressed']['rating']})"
        ),
    }
