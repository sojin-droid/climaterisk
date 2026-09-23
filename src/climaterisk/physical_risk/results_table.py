"""The batch result table and the export-compatible frames — pure, stdlib-only.

One canonical column order (``CANONICAL_COLUMNS``) is used by the API, the UI table and
the Excel export, so the same result never appears under two shapes. Frames are lists of
dicts — ``pandas.DataFrame(frame)`` or ``openpyxl`` can consume them directly — and the
five sheets of the final export (``hazard_results``, ``asset_summary``,
``global_vs_country``, ``global_vs_korea_local``, ``methodology``) are produced here so
Phase 7/8 only has to write cells.

Nothing here computes a loss; it arranges rows the engine produced. A model that produced
no row for a facility/hazard is a missing comparison, reported as such, not zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from climaterisk.physical_risk.metrics import (
    MODEL_DEFINITIONS,
    ModelId,
    ResultRow,
    compare_models,
    load_config,
    relabel_comparison,
)

#: The batch table, in the order the UI and the export show it.
CANONICAL_COLUMNS: tuple[str, ...] = (
    "facility_id",
    "facility_name",
    "hazard_type",
    "model_id",
    "risk_level",
    "potential_loss_usd",
    "eal_usd",
    "eal_as_pct_of_assets",
    "hazard_source",
    "impact_function_id",
    "scenario",
    "time_horizon",
    "calculation_status",
)

#: Every column of the full ``hazard_results`` sheet — the canonical ones first, then the
#: complete provenance and status detail.
HAZARD_RESULTS_COLUMNS: tuple[str, ...] = (
    *CANONICAL_COLUMNS,
    "data_source",
    "requested_scenario",
    "served_scenario",
    "hazard_dataset",
    "hazard_data_version",
    "hazard_country",
    "impact_function_name",
    "impact_function_source",
    "exposure_source",
    "exposure_version",
    "hazard_intensity",
    "hazard_intensity_unit",
    "probability",
    "return_period_years",
    "potential_loss_return_period_years",
    "asset_value_usd",
    "asset_value_currency",
    "risk_level_criteria",
    "climate_change_multiplier",
    "status_detail",
    "confidence",
    "property_type",
)

#: Filter keys the batch table supports, mapped to the row attribute they test.
FILTER_FIELDS: dict[str, str] = {
    "hazard": "hazard_type",
    "model": "model_id",
    "risk_level": "risk_level",
    "scenario": "scenario",
}


def _as_rows(rows: Iterable[ResultRow | dict[str, Any]]) -> list[ResultRow]:
    return [r if isinstance(r, ResultRow) else ResultRow.from_dict(r) for r in rows]


def table_rows(rows: Iterable[ResultRow | dict[str, Any]]) -> list[dict[str, Any]]:
    """The batch table: one dict per row, canonical columns only, canonical order."""
    return [{c: getattr(r, c) for c in CANONICAL_COLUMNS} for r in _as_rows(rows)]


def filter_rows(
    rows: Iterable[ResultRow | dict[str, Any]],
    *,
    hazard: str | Iterable[str] | None = None,
    model: str | Iterable[str] | None = None,
    risk_level: str | Iterable[str] | None = None,
    scenario: str | Iterable[str] | None = None,
) -> list[ResultRow]:
    """Rows matching every given filter (each accepts one value or several)."""

    def wanted(value: str | Iterable[str] | None) -> set[str] | None:
        if value is None:
            return None
        return {value} if isinstance(value, str) else set(value)

    criteria = {
        FILTER_FIELDS["hazard"]: wanted(hazard),
        FILTER_FIELDS["model"]: wanted(model),
        FILTER_FIELDS["risk_level"]: wanted(risk_level),
        FILTER_FIELDS["scenario"]: wanted(scenario),
    }
    out = []
    for r in _as_rows(rows):
        if all(want is None or getattr(r, attr) in want for attr, want in criteria.items()):
            out.append(r)
    return out


def hazard_results_frame(rows: Iterable[ResultRow | dict[str, Any]]) -> list[dict[str, Any]]:
    """The ``hazard_results`` sheet — every row, full provenance, plus the data-scope label."""
    out = []
    for r in _as_rows(rows):
        line = {c: getattr(r, c) for c in HAZARD_RESULTS_COLUMNS if c != "data_source"}
        line["data_source"] = _scope_short(r.model_id)
        out.append({c: line.get(c) for c in HAZARD_RESULTS_COLUMNS})
    return out


_LEVEL_ORDER = {"Low": 0, "Medium": 1, "High": 2}


def asset_summary_frame(rows: Iterable[ResultRow | dict[str, Any]]) -> list[dict[str, Any]]:
    """The ``asset_summary`` sheet — one line per (facility x model).

    ``eal_usd`` is the sum over hazards that were actually priced; ``priced_hazards`` and
    ``unpriced_hazards`` say which were and were not, so a small total is never mistaken
    for a complete one. ``max_risk_level`` is the highest band among priced hazards.
    """
    groups: dict[tuple[str, str], list[ResultRow]] = {}
    for r in _as_rows(rows):
        groups.setdefault((r.facility_id, r.model_id), []).append(r)
    out = []
    for (fid, model), items in sorted(groups.items()):
        priced = [r for r in items if r.eal_usd is not None]
        unpriced = [r for r in items if r.eal_usd is None]
        value = next((r.asset_value_usd for r in items if r.asset_value_usd is not None), None)
        total = sum(r.eal_usd for r in priced if r.eal_usd is not None) if priced else None
        pct = (total / value * 100.0) if (total is not None and value) else None
        levels = [r.risk_level for r in priced if r.risk_level]
        out.append(
            {
                "facility_id": fid,
                "facility_name": items[0].facility_name,
                "model_id": model,
                "asset_value_usd": value,
                "eal_usd": total,
                "eal_as_pct_of_assets": pct,
                "max_risk_level": max(levels, key=lambda s: _LEVEL_ORDER.get(s, -1))
                if levels
                else None,
                "priced_hazards": ",".join(sorted(r.hazard_type for r in priced)),
                "unpriced_hazards": ",".join(
                    sorted(f"{r.hazard_type}:{r.calculation_status}" for r in unpriced)
                ),
                "n_hazards": len(items),
            }
        )
    return out


def comparison_frame(
    rows: Iterable[ResultRow | dict[str, Any]],
    baseline_model: str,
    comparison_model: str,
    label: str,
) -> list[dict[str, Any]]:
    """One comparison line per (facility x hazard).

    ``available`` is True only when **both** models priced the pair (both EALs present).
    A pair where the comparison model produced no row, or a row without numbers
    (``KOREA_LOCAL`` = NOT_IMPLEMENTED, heat = HAZARD_ONLY), is listed with
    ``available = False`` and the two statuses, so the sheet says "not available" instead
    of being silently shorter or showing zeros.
    """
    rs = _as_rows(rows)
    pairs = sorted({(r.facility_id, r.hazard_type) for r in rs})
    names = {r.facility_id: r.facility_name for r in rs}
    out = []
    for fid, haz in pairs:
        cmp = compare_models(rs, baseline_model, comparison_model, fid, haz)
        if cmp is None:
            have = {r.model_id: r for r in rs if r.facility_id == fid and r.hazard_type == haz}
            parts = []
            for m in (baseline_model, comparison_model):
                r = have.get(m)
                parts.append(f"{m}: {r.calculation_status}" if r else f"{m}: not run")
            out.append(
                {
                    "facility_id": fid,
                    "facility_name": names[fid],
                    "hazard_type": haz,
                    "baseline_model_id": baseline_model,
                    f"{label}_model_id": comparison_model,
                    "baseline_calculation_status": have[baseline_model].calculation_status
                    if baseline_model in have
                    else None,
                    f"{label}_calculation_status": have[comparison_model].calculation_status
                    if comparison_model in have
                    else None,
                    "available": False,
                    "detail": "not available — " + "; ".join(parts),
                }
            )
            continue
        relabelled = relabel_comparison(cmp, label)
        priced = cmp["baseline_eal_usd"] is not None and cmp["comparison_eal_usd"] is not None
        relabelled["available"] = priced
        relabelled["detail"] = (
            None
            if priced
            else (
                f"not available — {baseline_model} is {cmp['baseline_calculation_status']}, "
                f"{comparison_model} is {cmp['comparison_calculation_status']}"
            )
        )
        out.append(relabelled)
    return out


def methodology_frame(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The ``methodology`` sheet — key/value lines that make the workbook self-describing."""
    cfg = config or load_config()
    lines: list[tuple[str, str]] = [("model", f"{m} = {d}") for m, d in MODEL_DEFINITIONS.items()]
    lines += [
        (
            "impact_function",
            "identical across models: ImpfRiverFlood.from_jrc_region_sector (RF), "
            "ImpfSetTropCyclone regional calibration (TC); none exists for heat",
        ),
        ("eal", cfg["definitions"]["eal"]),
        ("potential_loss", cfg["definitions"]["potential_loss"]),
        ("probability", cfg["definitions"]["probability"]),
        ("return_period", cfg["definitions"]["return_period"]),
        ("risk_levels", cfg["risk_levels"]["attribution"]),
    ]
    lines += [(f"risk_band_{b['level']}", str(b["criteria"])) for b in cfg["risk_levels"]["bands"]]
    lines += [
        (
            "change_pct",
            "(comparison / baseline - 1) x 100; null when the baseline is 0 or missing",
        ),
        ("change_usd", "comparison - baseline, USD"),
        (
            "change_pp",
            "comparison_pct - baseline_pct, percentage points; distinct from the relative "
            "change_pct",
        ),
        (
            "climate_change_multiplier",
            "future_EAL / baseline_EAL within one model_id only; a model-vs-model ratio is a "
            "hazard-source comparison, not a climate multiplier",
        ),
        (
            "scenario",
            "requested_scenario and served_scenario are recorded separately; a mismatch is "
            "SCENARIO_MISMATCH with null financial fields",
        ),
        (
            "exposure",
            "user portfolio (lat, lon, asset_value) or LitPop when stated; population is never "
            "used as an asset-value proxy",
        ),
    ]
    return [{"key": k, "value": v} for k, v in lines]


def export_frames(
    rows: Iterable[ResultRow | dict[str, Any]], config: dict[str, Any] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Every export frame, keyed by frame name (the two non-expert views first)."""
    rs = _as_rows(rows)
    from climaterisk.physical_risk.display_copy import recommended_models

    preferred = recommended_models()
    return {
        "portfolio_summary": portfolio_summary_frame(rs, preferred),
        "asset_risk_matrix": asset_risk_matrix_frame(rs, preferred),
        "hazard_results": hazard_results_frame(rs),
        "asset_summary": asset_summary_frame(rs),
        "global_vs_country": comparison_frame(
            rs, ModelId.GLOBAL_BASELINE.value, ModelId.DATA_API_COUNTRY.value, "country"
        ),
        "global_vs_korea_local": comparison_frame(
            rs, ModelId.GLOBAL_BASELINE.value, ModelId.KOREA_LOCAL.value, "korea_local"
        ),
        "methodology": methodology_frame(config),
    }


# --------------------------------------------------------------------------- #
# Non-expert views — one primary row per (facility x hazard)                   #
# --------------------------------------------------------------------------- #
#: Hazard tags the portfolio views show, in column order, with their display names.
PORTFOLIO_HAZARDS: tuple[tuple[str, str], ...] = (
    ("RF", "Flood"),
    ("TC", "Typhoon"),
    ("HW", "Heatwave"),
)

#: Preference when several models priced the same facility x hazard: most local first.
_MODEL_PREFERENCE: tuple[str, ...] = (
    ModelId.KOREA_LOCAL.value,
    ModelId.DATA_API_COUNTRY.value,
    ModelId.GLOBAL_BASELINE.value,
)

_UNAVAILABLE_STATUSES = frozenset(
    {
        "NOT_IMPLEMENTED",
        "NO_HAZARD_DATA",
        "NO_IMPACT_FUNCTION",
        "NO_EXPOSURE_DATA",
        "SCENARIO_MISMATCH",
    }
)


def primary_row(
    candidates: list[ResultRow], preferred_model: str | None = None
) -> ResultRow | None:
    """The row that represents a facility x hazard when several models produced one.

    The recommended model wins when it produced a row with numbers or hazard intensity;
    otherwise the most local model that did; otherwise the most local row of any status —
    so an unavailable model never hides a priced one.
    """
    if not candidates:
        return None
    by_model = {r.model_id: r for r in candidates}

    def informative(r: ResultRow) -> bool:
        return r.eal_usd is not None or r.calculation_status == "HAZARD_ONLY"

    order = ([preferred_model] if preferred_model else []) + list(_MODEL_PREFERENCE)
    for model in order:
        r = by_model.get(model)
        if r is not None and informative(r):
            return r
    for model in order:
        if model in by_model:
            return by_model[model]
    return candidates[0]


def primary_rows(
    rows: Iterable[ResultRow | dict[str, Any]], preferred: dict[str, str] | None = None
) -> dict[tuple[str, str], ResultRow]:
    """``{(facility_id, hazard_type): primary row}`` (see :func:`primary_row`).

    ``preferred`` maps a hazard tag (``RF`` / ``TC`` / ``HW``) or readiness key (``HEAT``)
    to the model id to prefer — normally ``display_copy.recommended_models()``.
    """
    pref = dict(preferred or {})
    if "HEAT" in pref:
        pref.setdefault("HW", pref["HEAT"])
        pref.setdefault("HM", pref["HEAT"])
    groups: dict[tuple[str, str], list[ResultRow]] = {}
    for r in _as_rows(rows):
        groups.setdefault((r.facility_id, r.hazard_type), []).append(r)
    out: dict[tuple[str, str], ResultRow] = {}
    for key, cands in groups.items():
        chosen = primary_row(cands, pref.get(key[1]))
        if chosen is not None:
            out[key] = chosen
    return out


def _scope_short(model_id: str | None) -> str | None:
    from climaterisk.physical_risk.display_copy import SCOPE_SHORT

    return SCOPE_SHORT.get(model_id or "", model_id)


def _status_short(status: str) -> str:
    from climaterisk.physical_risk.display_copy import STATUS_SHORT

    return STATUS_SHORT.get(status, status)


def portfolio_summary_frame(
    rows: Iterable[ResultRow | dict[str, Any]], preferred: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """The non-expert first sheet: one line per facility, one primary row per hazard.

    Money and ratios are the engine's numbers, untouched; an unpriced hazard leaves its
    money cells ``None`` and says why in its ``Status`` column. ``Overall EAL`` is the plain
    sum of the priced hazards' EAL (an aggregation, not a risk score) and is ``None`` when
    nothing was priced. No overall risk level is produced (methodology decision pending).
    """
    prim = primary_rows(rows, preferred)
    facilities: dict[str, tuple[str, float | None]] = {}
    for row in _as_rows(rows):
        facilities.setdefault(row.facility_id, (row.facility_name, row.asset_value_usd))
    out = []
    for fid, (name, value) in facilities.items():
        line: dict[str, Any] = {"Facility ID": fid, "Facility": name, "Asset Value": value}
        total: float | None = None
        priced = hazard_only = not_available = 0
        for tag, label in PORTFOLIO_HAZARDS:
            r = prim.get((fid, tag))
            if r is not None:
                if r.eal_usd is not None:
                    priced += 1
                elif r.calculation_status == "HAZARD_ONLY":
                    hazard_only += 1
                elif (
                    r.calculation_status in _UNAVAILABLE_STATUSES or r.calculation_status == "ERROR"
                ):
                    not_available += 1
            if r is None:
                line[f"{label} Status"] = "Not assessed"
                if tag != "HW":
                    line[f"{label} Risk"] = None
                    line[f"{label} EAL"] = None
                    line[f"{label} EAL / Assets"] = None
                else:
                    line["Heatwave Tmax p95 (°C)"] = None
                line[f"{label} Data Source"] = None
                continue
            line[f"{label} Status"] = _status_short(r.calculation_status)
            if tag != "HW":
                line[f"{label} Risk"] = r.risk_level
                line[f"{label} EAL"] = r.eal_usd
                line[f"{label} EAL / Assets"] = r.eal_as_pct_of_assets
                if r.eal_usd is not None:
                    total = (total or 0.0) + float(r.eal_usd)
            else:
                line["Heatwave Tmax p95 (°C)"] = r.hazard_intensity
            line[f"{label} Data Source"] = _scope_short(r.model_id)
        line["Priced Hazard Count"] = priced
        line["Hazard-only Count"] = hazard_only
        line["Not Available Count"] = not_available
        line["Overall EAL"] = total  # plain sum of priced hazards — not a composite score
        line["Overall EAL / Assets"] = (
            total / value * 100.0 if (total is not None and value) else None
        )
        out.append(line)
    return out


def asset_risk_matrix_frame(
    rows: Iterable[ResultRow | dict[str, Any]], preferred: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Facility x hazard cells: the risk level where priced, else a short status label.

    ``Overall`` counts calculated hazards ("2 of 3 calculated") — it is not a risk score.
    """
    prim = primary_rows(rows, preferred)
    names = {r.facility_id: r.facility_name for r in _as_rows(rows)}
    out = []
    for fid, name in names.items():
        line: dict[str, Any] = {"Facility ID": fid, "Facility": name}
        priced = 0
        for tag, label in PORTFOLIO_HAZARDS:
            r = prim.get((fid, tag))
            if r is None:
                line[label] = "Not assessed"
            elif r.risk_level is not None:
                line[label] = r.risk_level
                priced += 1
            else:
                line[label] = _status_short(r.calculation_status)
        line["Overall"] = f"{priced} of {len(PORTFOLIO_HAZARDS)} calculated"
        out.append(line)
    return out


def summary_counts(
    rows: Iterable[ResultRow | dict[str, Any]], preferred: dict[str, str] | None = None
) -> dict[str, int]:
    """Counts for the results header, over primary rows only — no score, no ranking."""
    prim = primary_rows(rows, preferred)
    counts = {
        "assets_analyzed": len({fid for fid, _ in prim}),
        "high_risk": 0,
        "medium_risk": 0,
        "low_risk": 0,
        "hazard_only": 0,
        "not_available": 0,
        "errors": 0,
    }
    for r in prim.values():
        if r.risk_level == "High":
            counts["high_risk"] += 1
        elif r.risk_level == "Medium":
            counts["medium_risk"] += 1
        elif r.risk_level == "Low":
            counts["low_risk"] += 1
        elif r.calculation_status == "HAZARD_ONLY":
            counts["hazard_only"] += 1
        elif r.calculation_status == "ERROR":
            counts["errors"] += 1
        elif r.calculation_status in _UNAVAILABLE_STATUSES:
            counts["not_available"] += 1
    return counts
