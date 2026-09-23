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

from climaterisk.physical_risk.metrics import CalculationStatus, ModelId, load_config
from climaterisk.physical_risk.models import HAZARD_KEYS, READINESS

#: User-facing hazard names. ``HEAT`` is the readiness key; ``HW`` the CLIMADA tag.
HAZARD_LABEL: dict[str, str] = {
    "RF": "Flood",
    "TC": "Tropical Cyclone",
    "HEAT": "Heatwave",
    "HW": "Heatwave",
    "HM": "Heat mortality",
}

#: One-line, non-expert descriptions shown under each hazard checkbox.
HAZARD_COPY: dict[str, str] = {
    "RF": "River flooding and modeled inundation risk.",
    "TC": "Wind-related physical damage from tropical cyclones.",
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
    "Flood and tropical cyclone: financial loss is available where the status is FULL.",
    "Heatwave: hazard-only — no applicable CLIMADA Impact Function, so no financial loss.",
    "Korea local flood: unavailable (no domestic dataset connected; licence pending).",
    "Korea local tropical cyclone: unavailable (no domestic dataset connected).",
    "Korea-specific Impact Functions: not used — published CLIMADA functions only.",
    "Heatwave financial Impact Function: not available.",
    "No overall portfolio risk score is computed; risk levels are per hazard.",
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


def display_bundle() -> dict[str, Any]:
    """Everything the UI needs in one payload (served with the readiness table)."""
    return {
        "hazard_label": {k: HAZARD_LABEL[k] for k in ("RF", "TC", "HEAT")},
        "hazard_copy": dict(HAZARD_COPY),
        "scope_label": dict(SCOPE_LABEL),
        "scope_short": dict(SCOPE_SHORT),
        "scope_copy": dict(SCOPE_COPY),
        "status_copy": dict(STATUS_COPY),
        "status_short": dict(STATUS_SHORT),
        "risk_level_copy": risk_level_copy(),
        "method_copy": {k: method_copy(k) for k in ("RF", "TC", "HW")},
        "limitations": list(LIMITATIONS),
        "recommended_models": recommended_models(),
        "scope_availability": scope_availability(),
        "comparison_note": (
            "Global and country results use the same CLIMADA Impact Function. The current "
            "Korea country dataset is a spatial subset of the same Data API hazard family, so "
            "a near-zero difference is expected."
        ),
    }
