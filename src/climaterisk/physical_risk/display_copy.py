"""Display copy for non-experts — words only, no arithmetic.

Everything here is *presentation*: hazard names, plain-language explanations of the risk
bands and calculation statuses, the three data-scope labels, and the "recommended" model
choice per hazard. None of it changes a number. The thresholds quoted in the band copy are
read from the same configuration the calculation uses, so the words cannot drift from the
code (``test_display_copy_quotes_the_configured_thresholds``).

The one piece of logic is :func:`recommended_models`: pick, per hazard, the most local
model that the readiness registry says can actually run. It never selects a
``NOT_IMPLEMENTED`` or ``NO_HAZARD_DATA`` cell.
"""

from __future__ import annotations

from typing import Any

from climaterisk.physical_risk.coverage import (
    EXTENDED_HAZARDS,
    RESOLUTION_NOTE,
    coverage_summary_lines,
    coverage_table,
    resolution_registry,
)
from climaterisk.physical_risk.metrics import CalculationStatus, ModelId, load_config
from climaterisk.physical_risk.models import HAZARD_KEYS, READINESS
from climaterisk.physical_risk.municipalities import (
    REPRESENTATIVE_POINT_LABEL,
    REPRESENTATIVE_POINT_WARNING,
    dataset_summary,
)

#: User-facing hazard names. ``HEAT`` is the readiness key; ``HW`` the CLIMADA tag.
HAZARD_LABEL: dict[str, str] = {
    "RF": "Flood",
    "TC": "Tropical Cyclone",
    "HEAT": "Heatwave",
    "HW": "Heatwave",
    "HM": "Heat mortality",
    "DROUGHT": "Drought",
    "SLR": "Sea-level rise",
}

#: One-line, non-expert descriptions shown under each hazard checkbox.
HAZARD_COPY: dict[str, str] = {
    "RF": "Modeled river flooding and inundation risk.",
    "TC": "Modeled wind-related physical damage from tropical cyclones.",
    "HEAT": (
        "Extreme heat exposure. Financial impact is shown only where an applicable "
        "CLIMADA Impact Function is available — today none is, so heat is hazard-only."
    ),
}

#: Data-scope labels. The internal model ids stay; these are what the user reads.
SCOPE_LABEL: dict[str, str] = {
    ModelId.GLOBAL_BASELINE.value: "Global reference",
    ModelId.DATA_API_COUNTRY.value: "Korea country dataset",
    ModelId.KOREA_LOCAL.value: "Korea local / official data",
}
SCOPE_SHORT: dict[str, str] = {
    ModelId.GLOBAL_BASELINE.value: "Global",
    ModelId.DATA_API_COUNTRY.value: "Country",
    ModelId.KOREA_LOCAL.value: "Local",
}
SCOPE_COPY: dict[str, str] = {
    ModelId.GLOBAL_BASELINE.value: "A global CLIMADA reference calculation.",
    ModelId.DATA_API_COUNTRY.value: (
        "A Korea subset from the same CLIMADA Data API hazard family — not domestic source data."
    ),
    ModelId.KOREA_LOCAL.value: "Domestic source data. Available only for supported hazards.",
}

#: Plain-language meaning of each calculation status.
STATUS_COPY: dict[str, str] = {
    CalculationStatus.FULL.value: (
        "Financial loss calculated with a published CLIMADA Impact Function."
    ),
    CalculationStatus.HAZARD_ONLY.value: (
        "Hazard exposure is available, but financial loss is not calculated because no "
        "applicable CLIMADA Impact Function is available."
    ),
    CalculationStatus.NO_IMPACT_FUNCTION.value: (
        "No published CLIMADA Impact Function exists for this hazard; no loss is calculated."
    ),
    CalculationStatus.NO_HAZARD_DATA.value: (
        "No hazard dataset of this kind exists for this data scope; nothing was calculated."
    ),
    CalculationStatus.NO_EXPOSURE_DATA.value: (
        "The asset has no positive value, so a loss cannot be expressed."
    ),
    CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value: (
        "Expected annual loss is calculated; the 100-year potential loss is not, because the "
        "hazard data does not reach a 100-year event."
    ),
    CalculationStatus.SCENARIO_MISMATCH.value: (
        "The dataset serves a different climate scenario than the one requested; the loss is "
        "not reported under the requested scenario."
    ),
    CalculationStatus.NOT_IMPLEMENTED.value: (
        "No domestic dataset is connected for this hazard yet; nothing was calculated."
    ),
    CalculationStatus.ERROR.value: "The calculation failed; see the status detail.",
}

#: Short labels for the matrix and summary cells.
STATUS_SHORT: dict[str, str] = {
    CalculationStatus.FULL.value: "Calculated",
    CalculationStatus.HAZARD_ONLY.value: "Hazard only",
    CalculationStatus.NO_IMPACT_FUNCTION.value: "N/A",
    CalculationStatus.NO_HAZARD_DATA.value: "N/A",
    CalculationStatus.NO_EXPOSURE_DATA.value: "N/A",
    CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value: "Calculated (EAL only)",
    CalculationStatus.SCENARIO_MISMATCH.value: "N/A (scenario)",
    CalculationStatus.NOT_IMPLEMENTED.value: "Unavailable",
    CalculationStatus.ERROR.value: "Error",
}

#: What the tool does and does not cover today (shown on the results screen and in Excel).
LIMITATIONS: tuple[str, ...] = (
    "Flood: Korea-local domestic source not yet implemented; financial loss is available "
    "where the status is FULL (Data API hazard).",
    "Tropical cyclone: Korea-local domestic source not yet implemented; financial loss is "
    "available where the status is FULL (Data API hazard).",
    "Heatwave: hazard available from KMA TAMAX; financial impact unavailable because no "
    "applicable CLIMADA Impact Function exists.",
    "Korea-specific Impact Functions: not used — published CLIMADA functions only.",
    "Korea asset-value exposure: not yet implemented unless supplied through the user portfolio.",
    "All values are modeled — expected annual loss under the selected CLIMADA hazard and "
    "impact-function assumptions, not observed damage.",
    "No overall portfolio risk score is computed; risk levels are per hazard.",
)

#: Municipality-mode wording for the one status that means something different there:
#: a municipality point never has a value unless the user supplied one, so "no exposure
#: data" is the *normal* screening case, not a data defect.
MUNICIPALITY_STATUS_COPY: dict[str, str] = {
    CalculationStatus.NO_EXPOSURE_DATA.value: (
        "No asset value was supplied for this municipality, so this is hazard screening only: "
        "the modeled hazard intensity and its data source are reported and no loss is "
        "estimated. Supply an asset value to obtain a financial assessment."
    ),
}
MUNICIPALITY_STATUS_SHORT: dict[str, str] = {
    CalculationStatus.NO_EXPOSURE_DATA.value: "No asset value",
}

#: Shown wherever a municipality result is shown (spec §37) — never reworded.
MUNICIPALITY_COPY: dict[str, str] = {
    "target_municipalities": "Municipalities",
    "target_facilities": "My Assets",
    "warning": REPRESENTATIVE_POINT_WARNING,
    "point_label": REPRESENTATIVE_POINT_LABEL,
    "screening_vs_financial": (
        "Municipality point → hazard screening. Municipality point + supplied asset value → "
        "financial risk assessment. No asset value is ever estimated for a municipality."
    ),
    "financial_unavailable": "Financial assessment: Not available — asset value not supplied.",
    "point_definition": (
        "The representative point is an interior point of the municipality's official "
        "boundary (Statistics Korea SGIS 2025 2Q), not the city-hall building."
    ),
}

#: Under the summary counts, always.
SUMMARY_NOTE = (
    "Risk counts summarize individual hazard assessments. They are not an overall portfolio "
    "risk score."
)

#: Under the Global vs Country table, always.
COMPARISON_PURPOSE = (
    "This comparison is a pipeline-consistency check, not a measure of Korea localization accuracy."
)


def risk_level_copy(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Plain-language meaning of each band, quoting the configured thresholds.

    Built from the configuration rather than typed in, so the copy is always the rule.
    """
    cfg = config or load_config()
    out: dict[str, str] = {}
    for band in cfg["risk_levels"]["bands"]:
        lo, hi = band["min_pct"], band["max_pct"]
        if lo is None:
            text = f"Annual expected loss is below {hi:.2f}% of asset value."
        elif hi is None:
            text = f"Annual expected loss is at least {lo:.2f}% of asset value."
        else:
            text = f"Annual expected loss is between {lo:.2f}% and {hi:.2f}% of asset value."
        out[str(band["level"])] = text
    return out


def method_copy(hazard_tag: str) -> str:
    """The one-line "why" chain for a hazard, for the detail panel's Method section."""
    if hazard_tag == "RF":
        return "Flood risk is based on: Hazard → CLIMADA JRC Impact Function → Asset Value → EAL."
    if hazard_tag == "TC":
        return (
            "Tropical cyclone risk is based on: Wind hazard → CLIMADA regional Impact Function "
            "(Eberenz et al. 2021) → Asset Value → EAL."
        )
    return (
        "Heatwave: Hazard intensity (season p95 daily maximum temperature, KMA) is reported; "
        "no CLIMADA Impact Function exists, so no financial loss is derived."
    )


#: Preference order when choosing a model per hazard: most local first.
_LOCAL_FIRST: tuple[str, ...] = (
    ModelId.KOREA_LOCAL.value,
    ModelId.DATA_API_COUNTRY.value,
    ModelId.GLOBAL_BASELINE.value,
)


def recommended_models(
    hazards: list[str] | None = None, readiness: dict[str, dict[str, dict[str, str]]] | None = None
) -> dict[str, str]:
    """Per hazard key, the most local model whose readiness cell can run.

    ``READY`` beats ``HAZARD_ONLY``; ``NOT_IMPLEMENTED`` and ``NO_HAZARD_DATA`` are never
    chosen. For Korea today: RF → DATA_API_COUNTRY, TC → DATA_API_COUNTRY, HEAT → KOREA_LOCAL.
    A hazard with no runnable cell is omitted.
    """
    table = readiness or READINESS
    out: dict[str, str] = {}
    for key in hazards or list(HAZARD_KEYS):
        cells = table.get(key, {})
        for wanted in ("READY", "HAZARD_ONLY"):
            pick = next((m for m in _LOCAL_FIRST if cells.get(m, {}).get("status") == wanted), None)
            if pick:
                out[key] = pick
                break
    return out


def scope_availability(
    readiness: dict[str, dict[str, dict[str, str]]] | None = None,
) -> dict[str, dict[str, str]]:
    """``{model_id: {hazard_key: 'available' | 'hazard only' | 'unavailable'}}`` for the UI."""
    table = readiness or READINESS
    out: dict[str, dict[str, str]] = {}
    for model in (m.value for m in ModelId):
        out[model] = {}
        for key in HAZARD_KEYS:
            status = table.get(key, {}).get(model, {}).get("status")
            out[model][key] = (
                "available"
                if status == "READY"
                else "hazard only"
                if status == "HAZARD_ONLY"
                else "unavailable"
            )
    return out


def why_unavailable() -> dict[str, str]:
    """The "Why is this unavailable?" sentence per non-priced hazard (spec §25).

    Read from the coverage table, so the reason and the status cannot disagree.
    """
    out: dict[str, str] = {}
    for row in coverage_table():
        if row["Status"] != "READY":
            out[str(row["hazard_key"])] = str(row["Why"])
    return out


def municipality_bundle() -> dict[str, Any]:
    """Municipality-mode copy plus the dataset provenance (for the UI and the Excel run info)."""
    return {
        **MUNICIPALITY_COPY,
        "status_copy": dict(MUNICIPALITY_STATUS_COPY),
        "status_short": dict(MUNICIPALITY_STATUS_SHORT),
        "dataset": dataset_summary(),
    }


def display_bundle() -> dict[str, Any]:
    """Everything the UI needs in one payload (served with the readiness table)."""
    return {
        # V0.2 — five-hazard coverage, resolution and municipality copy
        "coverage_table": coverage_table(),
        "coverage_lines": coverage_summary_lines(),
        "why_unavailable": why_unavailable(),
        "extended_hazards": {
            k: {kk: vv for kk, vv in v.items() if kk != "evidence"}
            | {"evidence": dict(v["evidence"])}
            for k, v in EXTENDED_HAZARDS.items()
        },
        "resolution_note": RESOLUTION_NOTE,
        "resolution": resolution_registry(),
        "municipality": municipality_bundle(),
        "hazard_label": dict(HAZARD_LABEL),  # readiness keys and CLIMADA tags alike
        "hazard_copy": dict(HAZARD_COPY),
        "scope_label": dict(SCOPE_LABEL),
        "scope_short": dict(SCOPE_SHORT),
        "scope_copy": dict(SCOPE_COPY),
        "status_copy": dict(STATUS_COPY),
        "status_short": dict(STATUS_SHORT),
        "risk_level_copy": risk_level_copy(),
        "method_copy": {k: method_copy(k) for k in ("RF", "TC", "HW")},
        "limitations": list(LIMITATIONS),
        "summary_note": SUMMARY_NOTE,
        "comparison_purpose": COMPARISON_PURPOSE,
        "recommended_models": recommended_models(),
        "scope_availability": scope_availability(),
        "comparison_note": (
            "Global and country results use the same CLIMADA Impact Function. The current "
            "Korea country dataset is a spatial subset of the same Data API hazard family, so "
            "a near-zero difference is expected."
        ),
    }
