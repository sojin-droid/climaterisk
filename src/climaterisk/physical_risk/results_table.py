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
    """The ``hazard_results`` sheet — every row, full provenance."""
    return [{c: getattr(r, c) for c in HAZARD_RESULTS_COLUMNS} for r in _as_rows(rows)]


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
            out.append(
                {
                    "facility_id": fid,
                    "facility_name": names[fid],
                    "hazard_type": haz,
                    "baseline_model_id": baseline_model,
                    f"{label}_model_id": comparison_model,
                    "available": False,
                    "detail": (
                        f"{comparison_model} produced no row for this facility/hazard — "
                        "not available"
                    ),
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
    """All five export sheets, keyed by sheet name."""
    rs = _as_rows(rows)
    return {
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
