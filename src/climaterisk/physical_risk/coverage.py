"""Five-hazard coverage and spatial-resolution registry — generated, not typed into a table.

Pure and stdlib-only. Two registries and one generator:

* :data:`SPATIAL_RESOLUTION` — the declared grid spacing of every hazard dataset a model
  reads, with the metadata field it was read from. The worker measures the same quantity on
  the loaded hazard (centroid spacing / the Data API ``res_arcsec`` property) and writes it
  on every row; ``tests/test_municipality_worker.py`` asserts the measurement equals the
  declaration, so this table cannot drift from the data.
* :data:`EXTENDED_HAZARDS` — the two hazards outside the validated financial chain
  (drought, sea-level rise), each with the evidence gathered by importing, instantiating
  and inspecting the installed CLIMADA (2026-09-27) and the reason they are ``NOT_READY``.
* :func:`coverage_table` — the *Hazard Coverage* rows for the UI and the Excel sheet, built
  from ``models.READINESS`` for the three core hazards and from ``EXTENDED_HAZARDS`` for the
  other two. No cell is hand-written.

Vocabulary (spec §17): *grid*, *grid cell*, *hazard centroid*, *spatial resolution* — not
"pixel". Resolution is the size of the modeled hazard grid; it is **not** model accuracy
(:data:`RESOLUTION_NOTE`).
"""

from __future__ import annotations

from typing import Any

from climaterisk.physical_risk.metrics import ModelId
from climaterisk.physical_risk.models import HAZARD_KEYS, READINESS

_G, _C, _K = (
    ModelId.GLOBAL_BASELINE.value,
    ModelId.DATA_API_COUNTRY.value,
    ModelId.KOREA_LOCAL.value,
)

#: Every hazard the coverage table lists, in display order.
COVERAGE_KEYS: tuple[str, ...] = (*HAZARD_KEYS, "DROUGHT", "SLR")

#: Shown wherever a resolution is shown. Do not soften.
RESOLUTION_NOTE = (
    "Spatial resolution describes the size of the modeled hazard grid. A finer grid does not "
    "by itself mean higher model accuracy."
)

#: Approximate ground size of one grid step at Korean latitudes (35–38 N), for the copy.
#: 1 arcsec of latitude ≈ 30.9 m; 1° of longitude ≈ 111 km × cos(36.5°) ≈ 89 km.
_KM_PER_DEG_LAT = 111.2
_KM_PER_DEG_LON_KOREA = 89.4


def approx_km(value: float, unit: str) -> tuple[float, float] | None:
    """``(north-south km, east-west km)`` of one grid step in Korea; None when not angular."""
    if unit == "arcsec":
        deg = value / 3600.0
    elif unit == "degree":
        deg = value
    else:
        return None
    return deg * _KM_PER_DEG_LAT, deg * _KM_PER_DEG_LON_KOREA


def km_class(value: float, unit: str) -> str:
    """Plain-language size class, e.g. ``~4–5 km``; empty when there is no grid."""
    km = approx_km(value, unit)
    if km is None:
        return ""
    lo, hi = sorted(km)
    if hi < 2:
        return f"~{lo:.1f}–{hi:.1f} km"
    lo_r, hi_r = round(lo), round(hi)
    return f"~{hi_r} km" if lo_r == hi_r else f"~{lo_r}–{hi_r} km"


def _dataapi(hazard: str, model: str) -> dict[str, Any]:
    return {
        "hazard": hazard,
        "model_id": model,
        "value": 150.0,
        "unit": "arcsec",
        "unit_type": "grid cell",
        "description": f"150 arcsec grid ({km_class(150.0, 'arcsec')} class in Korea)",
        "metadata_field": "CLIMADA Data API dataset property res_arcsec (verified on load)",
        "dataset_family": (
            "river_flood_150arcsec (ISIMIP)"
            if hazard == "RF"
            else "tropical_cyclone_10synth_tracks_150arcsec"
        ),
    }


#: Declared resolution per (hazard key x model). Missing = that cell has no dataset.
SPATIAL_RESOLUTION: dict[str, dict[str, dict[str, Any]]] = {
    "RF": {_G: _dataapi("RF", _G), _C: _dataapi("RF", _C)},
    "TC": {_G: _dataapi("TC", _G), _C: _dataapi("TC", _C)},
    "HEAT": {
        _K: {
            "hazard": "HEAT",
            "model_id": _K,
            "value": 0.05,
            "unit": "degree",
            "unit_type": "grid",
            "description": (
                f"0.05 degree grid ({km_class(0.05, 'degree')} class) — KMA 남한상세 source "
                "grids (observations 0.005°, SSP scenarios 0.01°) block-averaged to 0.05° "
                "when the catalog layer is built"
            ),
            "metadata_field": (
                "catalog layer centroid spacing (measured on load); source grid from "
                "kma_scenario.GRID_RES_DEG"
            ),
            "dataset_family": "KOR heatwave layer (season p95 TAMAX)",
        }
    },
    "DROUGHT": {},  # no dataset connected — see EXTENDED_HAZARDS
    "SLR": {},  # no spatial grid — see EXTENDED_HAZARDS
}

#: Investigation record for the two hazards outside the validated chain (spec §2, §22, §23).
#: ``evidence`` lists what was actually imported / instantiated / inspected.
EXTENDED_HAZARDS: dict[str, dict[str, Any]] = {
    "DROUGHT": {
        "label": "Drought",
        "status": "NOT_READY",
        "hazard_data": "Not connected",
        "financial_loss": "Not available",
        "risk_level": "Not available",
        "resolution": None,
        "resolution_text": "N/A — no drought hazard dataset is connected",
        "why": (
            "A legacy drought result exists, but the current validated financial-risk "
            "pipeline has not been implemented for this hazard."
        ),
        "detail": (
            "The legacy peril definition (perils.json 'drought', SPEI ramp, 'productivity') "
            "needs an SPEI hazard to be ingested and no ingester exists, so no drought grid is "
            "held in this repository. CLIMADA Petals ships ImpfDrought (SPEI step function, "
            "intensity unit 'NA', haz_type DR) and a Drought hazard that reads the global "
            "SPEIbase file spei06.nc (0.5° grid, Europe demo extent by default); the function "
            "is not a real-estate damage curve and no Korean SPEI product is connected. Nothing "
            "is invented to fill the gap."
        ),
        "evidence": {
            "checked": "2026-09-27, installed climada 6.1.0 / climada_petals 6.2.0",
            "ImpfDrought.from_default()": (
                "haz_type=DR, intensity [-6.5, -4, -1, 0], mdd/paa [1, 1, 0, 0], unit 'NA'"
            ),
            "climada_petals.hazard.drought.Drought()": (
                "instantiates; SPEI_FILE_URL digital.csic.es spei06.nc; default extent "
                "lat 44.5–50, lon 5–12"
            ),
            "Data API hazard types": "no drought / SPEI type in the live type list",
            "repository": "no SPEI ingester; no catalog entry; no Open-Meteo / ERA5 client",
        },
        "candidate": (
            "SPEIbase (0.5°) or a KMA-derived SPEI would supply a DR hazard; pricing still "
            "needs an impact function whose exposure is a priced asset, which ImpfDrought is "
            "not — a separate, evidence-backed phase"
        ),
    },
    "SLR": {
        "label": "Sea-level rise",
        "status": "NOT_READY",
        "hazard_data": "Legacy / static only",
        "financial_loss": "Not available",
        "risk_level": "Not available",
        "resolution": None,
        "resolution_text": "N/A — no spatial grid",
        "why": (
            "The current implementation is configuration-based rather than a spatial CLIMADA "
            "hazard-loss calculation."
        ),
        "detail": (
            "Sea-level rise exists only as the sea_level_rise_m option of the legacy "
            "tropical-cyclone surge runner (a static offset added to the bathtub surge "
            "height), not as a hazard grid with events and frequencies. CLIMADA has no "
            "sea-level-rise hazard class; Petals' CoastalFlood.from_aqueduct_tif (Aqueduct "
            "coastal inundation, RCP 4.5/8.5, 2030/2050/2080, subsidence option) and "
            "TCSurgeBathtub are the nearest existing hazards, and neither is connected to the "
            "validated chain."
        ),
        "evidence": {
            "checked": "2026-09-27, installed climada 6.1.0 / climada_petals 6.2.0",
            "climada.hazard modules": "no sea-level-rise class (trop_cyclone, storm_europe, …)",
            "climada_petals.hazard.coastal_flood.CoastalFlood.from_aqueduct_tif": (
                "signature (rcp, target_year, return_periods, subsidence='wtsub', "
                "percentile='95', countries, boundaries)"
            ),
            "Data API hazard types": (
                "no coastal_flood type in the live type list (the client config names "
                "aqueduct_coastal_flood, the server does not serve it)"
            ),
            "repository": "physical.py _run_tc_surge: sea_level_rise_m static option only",
        },
        "candidate": (
            "Aqueduct coastal flood through CoastalFlood + the JRC coastal depth-damage "
            "function would be a spatial hazard-loss chain — a separate phase with its own "
            "licence and validation evidence"
        ),
    },
}


def resolution_for(hazard_key: str, model_id: str) -> dict[str, Any] | None:
    """Declared resolution of one cell, or None when that cell has no dataset."""
    return SPATIAL_RESOLUTION.get(hazard_key, {}).get(model_id)


def resolution_registry() -> dict[str, Any]:
    """The whole registry plus the note, for the API."""
    return {
        "note": RESOLUTION_NOTE,
        "cells": {
            k: {m: dict(v) for m, v in cells.items()} for k, cells in SPATIAL_RESOLUTION.items()
        },
        "extended": {
            k: {"status": v["status"], "resolution_text": v["resolution_text"]}
            for k, v in EXTENDED_HAZARDS.items()
        },
    }


def _core_resolution_text(key: str) -> str:
    cells = SPATIAL_RESOLUTION.get(key, {})
    if not cells:
        return "N/A"
    classes = sorted({km_class(c["value"], c["unit"]) for c in cells.values()})
    values = sorted({f"{c['value']:g} {c['unit']}" for c in cells.values()})
    return f"{' / '.join(values)} ({' / '.join(classes)})"


def coverage_table(
    readiness: dict[str, dict[str, dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    """One line per hazard: what exists today, generated from the registries.

    Columns: Hazard · Hazard Data · Financial Loss · Risk Level · Resolution · Status · Why.
    The status of a core hazard is the best readiness status among its models
    (``READY`` > ``HAZARD_ONLY`` > nothing); the two extended hazards are ``NOT_READY``.
    """
    from climaterisk.physical_risk.display_copy import HAZARD_LABEL

    table = readiness or READINESS
    out: list[dict[str, Any]] = []
    for key in COVERAGE_KEYS:
        if key in table:
            statuses = {m: c["status"] for m, c in table[key].items()}
            ready = [m for m, s in statuses.items() if s == "READY"]
            hazard_only = [m for m, s in statuses.items() if s == "HAZARD_ONLY"]
            if ready:
                status, hazard_data, money = "READY", "Available", "Available"
                why = (
                    "Financial loss is calculated with a published CLIMADA Impact Function "
                    f"({', '.join(ready)})."
                )
            elif hazard_only:
                status, hazard_data, money = "HAZARD_ONLY", "Available", "Not available"
                why = (
                    "Hazard data is available, but no applicable CLIMADA Impact Function is "
                    "currently available for financial loss calculation."
                )
            else:  # pragma: no cover - every core hazard has a runnable cell today
                status, hazard_data, money = "NOT_READY", "Not available", "Not available"
                why = "No runnable data scope."
            out.append(
                {
                    "hazard_key": key,
                    "Hazard": HAZARD_LABEL.get(key, key),
                    "Hazard Data": hazard_data,
                    "Financial Loss": money,
                    "Risk Level": money,
                    "Resolution": _core_resolution_text(key),
                    "Status": status,
                    "Why": why,
                    "models": statuses,
                }
            )
        else:
            ext = EXTENDED_HAZARDS[key]
            out.append(
                {
                    "hazard_key": key,
                    "Hazard": ext["label"],
                    "Hazard Data": ext["hazard_data"],
                    "Financial Loss": ext["financial_loss"],
                    "Risk Level": ext["risk_level"],
                    "Resolution": ext["resolution_text"],
                    "Status": ext["status"],
                    "Why": ext["why"],
                    "models": {},
                }
            )
    return out


def coverage_summary_lines() -> list[str]:
    """The "What this tool covers today" lines (spec §33), from the table."""
    lines = []
    for row in coverage_table():
        if row["Status"] == "READY":
            lines.append(f"✓ {row['Hazard']} — financial risk")
        elif row["Status"] == "HAZARD_ONLY":
            lines.append(f"✓ {row['Hazard']} — hazard only")
        else:
            lines.append(f"○ {row['Hazard']} — not ready")
    return lines
