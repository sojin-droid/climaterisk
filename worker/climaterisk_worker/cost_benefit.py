"""Adaptation cost-benefit analysis (CLIMADA ``CostBenefit`` + ``MeasureSet``).

Given a portfolio, a peril, and one or more adaptation *measures*, this computes the
NPV-discounted averted damage (benefit), the cost, and the cost/benefit ratio of each
measure, plus the total (unaverted) climate risk — present vs a future horizon.

A measure maps to a CLIMADA ``Measure``:
  - ``damage_reduction`` r in [0,1]  -> ``mdd_impact = (1 - r, 0)`` (scales mean damage)
  - ``hazard_freq_cutoff``           -> drop the most frequent events above this frequency
  - ``risk_transf_attach`` / ``risk_transf_cover`` -> insurance layer (deductible / limit)

Peril scope: the request's ``peril`` is honoured — ``tropical_cyclone`` is computed;
any other peril returns a structured ``status="error"`` (no silent fallback to TC). The
contract/UI are peril-generic; extending ``_SUPPORTED_PERILS`` requires the matching
hazard resolver and impact-function family for that peril.
"""

from __future__ import annotations

from typing import Any

from climaterisk_worker import catalog, vulnerability
from climaterisk_worker._dataapi import resilient_get_hazard
from climaterisk_worker.physical import (
    _TC_REF_YEARS,
    _build_exposures,
    _nearest,
    _per_asset_iso3,
    _single_country_iso3,
)


def _tc_hazard(iso3: str | None, climate_scenario: str, ref_year: int | None):  # type: ignore[no-untyped-def]
    """Tropical-cyclone hazard: local catalog first (future only), then Data API."""
    from climada.util.api_client import Client

    if ref_year is not None:
        cat = catalog.load_hazard("tropical_cyclone", climate_scenario, iso3 or "global", ref_year)
        if cat is not None:
            return cat
    client = Client()
    props: dict[str, str] = {
        "event_type": "synthetic",
        "model_name": "random_walk",
        "climate_scenario": climate_scenario,
    }
    if ref_year is not None:
        props["ref_year"] = str(ref_year)
    if iso3 is not None:
        try:
            return resilient_get_hazard(
                client,
                "tropical_cyclone",
                properties={**props, "spatial_coverage": "country", "country_iso3alpha": iso3},
            )
        except Exception:
            pass
    return resilient_get_hazard(
        client, "tropical_cyclone", properties={**props, "spatial_coverage": "global"}
    )


# Perils with an adaptation model wired end-to-end (hazard resolver + impact-function
# family + Measure haz_type). Others are rejected explicitly.
_SUPPORTED_PERILS: dict[str, str] = {"tropical_cyclone": "TC"}


def _build_measure(spec: dict[str, Any], haz_type: str = "TC"):  # type: ignore[no-untyped-def]
    import numpy as np
    from climada.entity import Measure

    r = float(spec.get("damage_reduction", 0.0))
    return Measure(
        name=spec["name"],
        haz_type=haz_type,
        cost=float(spec.get("cost", 0.0)),
        mdd_impact=(1.0 - r, 0.0),
        paa_impact=(1.0, 0.0),
        hazard_freq_cutoff=float(spec.get("hazard_freq_cutoff", 0.0)),
        risk_transf_attach=float(spec.get("risk_transf_attach", 0.0)),
        risk_transf_cover=float(spec.get("risk_transf_cover", 0.0)),
        color_rgb=np.array([0.2, 0.5, 0.8]),
    )


def compute_cost_benefit(request: dict[str, Any]) -> dict[str, Any]:
    """Run an adaptation cost-benefit analysis; return a CostBenefitResult dict.

    The requested ``peril`` decides the hazard/impact-function family. Perils without an
    adaptation model return a structured error *before* any CLIMADA import, so the caller
    sees "unsupported" rather than a tropical-cyclone number.
    """
    peril = str(request.get("peril") or "tropical_cyclone")
    measures: list[dict[str, Any]] = request.get("measures", [])
    if peril not in _SUPPORTED_PERILS:
        return {
            "status": "error",
            "peril": peril,
            "measures": [],
            "detail": (
                f"cost-benefit is implemented for {sorted(_SUPPORTED_PERILS)} only; "
                f"peril '{peril}' has no adaptation model (hazard resolver + Measure haz_type "
                "+ impact-function family) wired yet. Not computed."
            ),
        }
    if not measures:
        return {
            "status": "error",
            "peril": peril,
            "detail": "no adaptation measures provided",
            "measures": [],
        }

    import numpy as np
    from climada.engine import CostBenefit
    from climada.entity import DiscRates, Entity, ImpactFuncSet, MeasureSet
    from climada.entity.impact_funcs.trop_cyclone import ImpfTropCyclone

    haz_type = _SUPPORTED_PERILS[peril]
    assets: list[dict[str, Any]] = request["assets"]
    scenario: str = request["climate_scenario"]
    anchor_years: list[int] = request["anchor_years"]
    discount_rate = float(request.get("discount_rate", 0.05))

    ref_year = _nearest(_TC_REF_YEARS, max(anchor_years) if anchor_years else _TC_REF_YEARS[0])
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)

    # Exposures + per-asset Emanuel impact functions (mirrors the impact run, including the
    # geography-aware v_half default — vulnerability.resolve_tc_vhalf).
    vh_per_asset, _vh_src, vh_note = vulnerability.resolve_tc_vhalf(
        assets, iso3s, request.get("options")
    )
    v_halves = sorted({round(v, 1) for v in vh_per_asset})
    id_by_v = {v: i + 1 for i, v in enumerate(v_halves)}
    impf_set = ImpactFuncSet(
        [ImpfTropCyclone.from_emanuel_usa(impf_id=i + 1, v_half=v) for i, v in enumerate(v_halves)]
    )
    exp, _ = _build_exposures(assets, "impf_TC", [id_by_v[round(v, 1)] for v in vh_per_asset])

    present = _tc_hazard(iso3, "None", None)
    future = _tc_hazard(iso3, scenario, ref_year)

    measure_set = MeasureSet(measure_list=[_build_measure(m, haz_type) for m in measures])
    # Year-varying discount rates: a {year: rate} schedule (linearly interpolated over the
    # horizon) if supplied, else the flat discount_rate. CLIMADA DiscRates entity component.
    # Horizon spans the analysis/schedule years inside a 2000–2100 default envelope, so NPV
    # stays well-defined for near-term anchors and is not silently truncated for far ones.
    schedule = request.get("discount_schedule") or {}
    sched_years = [int(y) for y in schedule]
    horizon_start = min([2000, *anchor_years, *sched_years])
    horizon_end = max([2100, *anchor_years, *sched_years])
    years = np.arange(horizon_start, horizon_end + 1)
    if schedule:
        pts = sorted((int(y), float(r)) for y, r in schedule.items())
        rates = np.interp(years, [y for y, _ in pts], [r for _, r in pts])
    else:
        rates = np.full(len(years), discount_rate)
    disc = DiscRates(years=years, rates=rates)
    entity = Entity(
        exposures=exp, impact_func_set=impf_set, measure_set=measure_set, disc_rates=disc
    )

    cb = CostBenefit()
    cb.calc(present, entity, haz_future=future, future_year=ref_year, save_imp=False)

    out_measures = []
    for m in measures:
        name = m["name"]
        benefit = float(cb.benefit.get(name, 0.0))
        cost = float(m.get("cost", 0.0))
        out_measures.append(
            {
                "name": name,
                "cost": cost,
                "benefit": benefit,
                "benefit_cost_ratio": (benefit / cost) if cost > 0 else None,
            }
        )
    return {
        "status": "ok",
        "peril": peril,
        "future_year": ref_year,
        "discount_rate": discount_rate,
        "currency": assets[0]["currency"] if assets else "USD",
        "tot_climate_risk": float(cb.tot_climate_risk),
        "measures": out_measures,
        "detail": (
            f"{iso3 or 'global'} TC; present vs {ref_year}, discount {discount_rate:.1%}; {vh_note}"
        ),
    }
