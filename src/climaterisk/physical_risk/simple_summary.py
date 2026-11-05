"""The simple-mode summary of one physical-risk run — the numbers the one-screen view shows.

Pure: takes a finished ``physical_risk_models`` run output (``rows``, ``baseline_rows``,
``portfolio_loss_curves``, ``baseline_portfolio_loss_curves``, ``climate_change_multipliers``)
and returns headline figures, the per-시도 table, the return-period table, the largest
assets and coverage notes. Nothing is recomputed with CLIMADA; every number is a sum,
ratio or lookup over what the engine already produced, so the summary can never disagree
with the rows or the Excel export.

Algorithm:
    $$\\mathrm{EAL}_h = \\sum_i \\mathrm{EAL}_{i,h}, \\qquad
      m_h = \\frac{\\sum_i \\mathrm{EAL}_{i,h}^{\\text{future}}}
               {\\sum_i \\mathrm{EAL}_{i,h}^{\\text{baseline}}},
      \\qquad s_{k} = \\frac{\\sum_{i \\in \\text{top } k} \\mathrm{EAL}_{i,h}}{\\mathrm{EAL}_h}$$
    ASCII: portfolio EAL per hazard h = sum of the facilities' EAL (EAL is additive); the
    portfolio climate multiplier m_h = future EAL sum / baseline EAL sum (same model, only
    when no per-asset pair was refused for a model-chain mismatch); concentration s_k = share
    of the hazard's EAL held by its k largest assets. Return-period losses are never summed:
    they are read from the engine's event-summed portfolio curves.

    ``EAL_{i,h}``: expected annual loss of facility i for hazard h (USD/yr); ``m_h``:
    portfolio climate-change multiplier (dimensionless); ``s_k``: concentration share (0-1).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from climaterisk.physical_risk.metrics import ResultRow, jrc_sector_for, load_config
from climaterisk.physical_risk.results_table import primary_rows

__all__ = ["HAZARD_LABELS_KO", "build_simple_summary"]

#: Screen labels (display copy, not tunables).
HAZARD_LABELS_KO: dict[str, str] = {
    "RF": "하천홍수",
    "CF": "해안침수",
    "TC": "태풍",
    "HW": "폭염",
    "HM": "폭염 사망",
}
_PRICED = ("FULL", "RETURN_PERIOD_NOT_RESOLVABLE")


def _primary(rows: Iterable[dict[str, Any]], preferred: dict[str, str] | None) -> list[ResultRow]:
    return list(primary_rows(rows, preferred).values())


def _eal(r: ResultRow) -> float:
    return float(r.eal_usd or 0.0) if r.calculation_status in _PRICED else 0.0


def _portfolio_curve(
    curves: list[dict[str, Any]], hazard: str, model: str | None
) -> dict[int, float | None]:
    for c in curves:
        if (
            c.get("hazard_type") == hazard
            and c.get("group_level") == "PORTFOLIO"
            and (model is None or c.get("model_id") == model)
        ):
            return {
                int(loss["return_period_years"]): (
                    None if loss.get("loss") is None else float(loss["loss"])
                )
                for loss in c.get("losses") or []
            }
    return {}


def _change(future: float, baseline: float | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """Present-to-future change of a group, with the kind the screen colours by."""
    if baseline is None:
        return {"kind": "no_baseline", "pct": None}
    eps = float(cfg["zero_usd"])
    if future <= eps and baseline <= eps:
        return {"kind": "none", "pct": None}
    if baseline <= eps:
        return {"kind": "new", "pct": None}
    pct = (future / baseline - 1.0) * 100.0
    if pct >= float(cfg["increase_flag_pct"]):
        kind = "up"
    elif pct <= float(cfg["decrease_flag_pct"]):
        kind = "down"
    else:
        kind = "flat"
    return {"kind": kind, "pct": pct}


def build_simple_summary(
    output: dict[str, Any],
    *,
    krw_per_usd: float,
    preferred_models: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The simple-mode summary of a finished physical-risk run (see the module docstring).

    Args:
        output: The worker output of a ``physical_risk_models`` run.
        krw_per_usd: Display exchange rate (KRW per USD) — the screen shows 억 원; amounts in
            the result stay USD, so the conversion is the viewer's only step.
        preferred_models: Hazard tag → model id to read when several models were run
            (normally ``display_copy.recommended_models()``).
        config: The physical-risk configuration (defaults to the library file).

    Returns:
        ``scope``, ``currency``, ``portfolio_value_usd``, ``total``, ``hazards`` (per hazard:
        EAL, baseline EAL, multiplier, return periods, risk counts and value shares,
        concentration), ``provinces``, ``top_assets`` and ``notes``.
    """
    cfg_all = config or load_config()
    cfg = cfg_all["simple_summary"]
    future = _primary(output.get("rows") or [], preferred_models)
    baseline = _primary(output.get("baseline_rows") or [], preferred_models)
    base_by = {(r.facility_id, r.hazard_type): r for r in baseline}
    has_baseline = bool(baseline)

    values: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}
    for r in future:
        if r.asset_value_usd is not None:
            values.setdefault(r.facility_id, float(r.asset_value_usd))
        meta.setdefault(
            r.facility_id,
            {
                "facility_name": r.facility_name,
                "province_name": r.province_name,
                "municipality_name": r.municipality_name,
                "asset_class": jrc_sector_for(r.property_type) if r.property_type else None,
            },
        )
    portfolio_value = sum(values.values())
    hazards = sorted(
        {r.hazard_type for r in future},
        key=lambda h: list(HAZARD_LABELS_KO).index(h) if h in HAZARD_LABELS_KO else 99,
    )

    refused_chain = {
        str(m.get("hazard_type"))
        for m in output.get("climate_change_multipliers") or []
        if m.get("climate_change_multiplier") is None
        and "model-chain" in str(m.get("detail") or "")
    }

    per_hazard: dict[str, Any] = {}
    for h in hazards:
        rows_h = [r for r in future if r.hazard_type == h]
        priced = [r for r in rows_h if r.calculation_status in _PRICED]
        eal = sum(_eal(r) for r in rows_h)
        base_rows_h = [base_by[(r.facility_id, h)] for r in rows_h if (r.facility_id, h) in base_by]
        base_eal = sum(_eal(r) for r in base_rows_h) if base_rows_h else None
        multiplier: float | None = None
        multiplier_note: str | None = None
        if not has_baseline:
            multiplier_note = "현재기후 기준 실행 없음"
        elif h in refused_chain:
            multiplier_note = (
                "기준 자료의 모델 체인이 달라 배수를 내지 않음 "
                "(현재기후 기준 'present'로 다시 실행)"
            )
        elif not base_eal:
            multiplier_note = "현재기후 손실이 0 — 배수 정의 안 됨"
        else:
            multiplier = eal / base_eal
        model = rows_h[0].model_id if rows_h else None
        fut_rp = _portfolio_curve(output.get("portfolio_loss_curves") or [], h, model)
        base_rp = _portfolio_curve(output.get("baseline_portfolio_loss_curves") or [], h, model)
        risk_counts = {
            lv: sum(1 for r in priced if r.risk_level == lv) for lv in ("High", "Medium", "Low")
        }
        risk_value_share = {
            lv: (
                sum(values.get(r.facility_id, 0.0) for r in priced if r.risk_level == lv)
                / portfolio_value
                if portfolio_value
                else None
            )
            for lv in ("High", "Medium", "Low")
        }
        ranked = sorted(priced, key=lambda r: -_eal(r))
        top_k = int(cfg["concentration_top_k"])
        top1 = ranked[0] if ranked and _eal(ranked[0]) > 0 else None
        top1_share = (_eal(top1) / eal) if top1 and eal else None
        conc: dict[str, Any] = {
            "top1_facility_id": top1.facility_id if top1 else None,
            "top1_facility_name": top1.facility_name if top1 else None,
            "top1_share": top1_share,
            f"top{top_k}_share": (sum(_eal(r) for r in ranked[:top_k]) / eal) if eal else None,
            "flag": top1_share is not None and top1_share >= float(cfg["concentration_flag_share"]),
        }
        protection = sorted(
            {
                r.flood_protection_return_period_years
                for r in rows_h
                if r.flood_protection_return_period_years
            }
        )
        per_hazard[h] = {
            "label": HAZARD_LABELS_KO.get(h, h),
            "model_id": model,
            "n_assets": len(rows_h),
            "n_priced": len(priced),
            "n_with_loss": sum(1 for r in priced if _eal(r) > 0),
            "eal_usd": eal,
            "eal_pct_of_value": (eal / portfolio_value) if portfolio_value else None,
            "baseline_eal_usd": base_eal,
            "climate_multiplier": multiplier,
            "climate_multiplier_note": multiplier_note,
            "eal_undefended_usd": (
                sum(float(r.eal_undefended_usd or 0.0) for r in priced) if protection else None
            ),
            "flood_protection_return_periods_years": protection or None,
            "levee_protected_assets": sum(1 for r in rows_h if r.levee_protected)
            if protection
            else None,
            "return_periods": [
                {
                    "return_period_years": rp,
                    "future_usd": fut_rp.get(rp),
                    "baseline_usd": base_rp.get(rp) if base_rp else None,
                }
                for rp in sorted(fut_rp)
            ],
            "risk_counts": risk_counts,
            "risk_value_share": risk_value_share,
            "concentration": conc,
        }

    # 시도 table: EAL is additive, so a 시도 total is the sum of its assets
    provinces: dict[str, dict[str, Any]] = {}
    for fid, m in meta.items():
        key = m["province_name"] or "(시도 미상)"
        p = provinces.setdefault(
            key, {"province_name": key, "n_assets": 0, "value_usd": 0.0, "per_hazard": {}}
        )
        p["n_assets"] += 1
        p["value_usd"] += values.get(fid, 0.0)
    for r in future:
        key = meta[r.facility_id]["province_name"] or "(시도 미상)"
        ph = provinces[key]["per_hazard"].setdefault(
            r.hazard_type, {"future_usd": 0.0, "baseline_usd": 0.0 if has_baseline else None}
        )
        ph["future_usd"] += _eal(r)
        b = base_by.get((r.facility_id, r.hazard_type))
        if has_baseline and b is not None:
            ph["baseline_usd"] += _eal(b)
    prov_list = []
    for p in provinces.values():
        fut_t = sum(v["future_usd"] for v in p["per_hazard"].values())
        base_t = sum(v["baseline_usd"] for v in p["per_hazard"].values()) if has_baseline else None
        for h, v in p["per_hazard"].items():
            v["change"] = (
                _change(v["future_usd"], v["baseline_usd"], cfg)
                if h not in refused_chain
                else {"kind": "no_baseline", "pct": None}
            )
        p["total"] = {
            "future_usd": fut_t,
            "baseline_usd": base_t,
            "change": _change(fut_t, base_t, cfg)
            if not refused_chain
            else {"kind": "no_baseline", "pct": None},
        }
        prov_list.append(p)
    prov_list.sort(key=lambda p: -p["total"]["future_usd"])

    # largest assets by total EAL across hazards
    totals: dict[str, float] = {}
    for r in future:
        totals[r.facility_id] = totals.get(r.facility_id, 0.0) + _eal(r)
    grand = sum(totals.values())
    top_n = int(cfg["top_assets"])
    top_assets = []
    for fid in sorted(totals, key=lambda f: -totals[f])[:top_n]:
        if totals[fid] <= 0:
            break
        rows_f = {r.hazard_type: r for r in future if r.facility_id == fid}
        main_h = max(rows_f, key=lambda h: _eal(rows_f[h]))
        b = base_by.get((fid, main_h))
        top_assets.append(
            {
                "facility_id": fid,
                **meta[fid],
                "value_usd": values.get(fid),
                "eal_usd": totals[fid],
                "share_of_portfolio_eal": totals[fid] / grand if grand else None,
                "main_hazard": main_h,
                "main_hazard_eal_usd": _eal(rows_f[main_h]),
                "main_hazard_baseline_eal_usd": _eal(b) if b is not None else None,
                "main_hazard_risk_level": rows_f[main_h].risk_level,
            }
        )

    notes = []
    for h in cfg["expected_hazards"]:
        if h not in per_hazard:
            notes.append(
                {
                    "kind": "not_assessed",
                    "hazard": h,
                    "text": (
                        f"{HAZARD_LABELS_KO.get(h, h)}: 계산하지 않음 — 이 재해의 손실이 빠져 "
                        "해당 지역 손실이 작게 보일 수 있음"
                    ),
                }
            )
    for h, v in per_hazard.items():
        if v["n_priced"] < v["n_assets"]:
            notes.append(
                {
                    "kind": "not_priced",
                    "hazard": h,
                    "text": (
                        f"{v['label']}: {v['n_assets'] - v['n_priced']}개 자산은 손실을 계산하지 "
                        "못함 (계산 상태 참고)"
                    ),
                }
            )
        if v["flood_protection_return_periods_years"]:
            rps = " · ".join(f"{x:g}년" for x in v["flood_protection_return_periods_years"])
            notes.append(
                {
                    "kind": "defences",
                    "hazard": h,
                    "text": (
                        f"{v['label']}: 방어 기준 적용 ({rps}; 배수펌프장 인근 제방 "
                        f"{v['levee_protected_assets']}개 자산)"
                    ),
                }
            )
    notes.append(
        {
            "kind": "tier",
            "hazard": None,
            "text": "참고용 추정입니다 — 국내 실제 피해 자료로 검증되지 않았습니다",
        }
    )

    total_eal = sum(v["eal_usd"] for v in per_hazard.values())
    return {
        "scope": {
            "n_assets": len(meta),
            "hazards": hazards,
            "climate_scenario": output.get("climate_scenario"),
            "target_year": output.get("target_year"),
            "baseline_scenario": output.get("baseline_scenario"),
            "assessment_target": output.get("assessment_target"),
        },
        "currency": {
            "amounts": "USD",
            "display": {
                "code": "KRW",
                "krw_per_usd": float(krw_per_usd),
                "unit_krw": float(cfg["display_unit_krw"]),
                "unit_label": cfg["display_unit_label"],
            },
        },
        "portfolio_value_usd": portfolio_value,
        "total": {
            "eal_usd": total_eal,
            "eal_pct_of_value": (total_eal / portfolio_value) if portfolio_value else None,
        },
        "hazards": per_hazard,
        "provinces": prov_list,
        "top_assets": top_assets,
        "notes": notes,
    }
