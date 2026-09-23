"""Model readiness registry — which (hazard x model) cells can produce what, today.

Pure and stdlib-only so both the backend API and the CLIMADA worker read the same table
(the worker adapters assert against it in ``tests/test_physical_risk_adapters.py``). A
cell's status is a statement about the platform, not a forecast:

* ``READY`` — a hazard adapter exists and the published impact function prices it.
* ``HAZARD_ONLY`` — a hazard adapter exists but no CLIMADA impact function does (heat);
  rows carry hazard intensity and null financial fields.
* ``NO_HAZARD_DATA`` — no dataset of this kind exists in the source (the Data API has no
  heat hazard type; the platform has no global heat layer).
* ``NOT_IMPLEMENTED`` — the adapter interface exists but no domestic dataset has been
  connected (licence / access unresolved). No row is priced and nothing is estimated.
"""

from __future__ import annotations

from typing import Any

from climaterisk.physical_risk.metrics import MODEL_DEFINITIONS, ModelId

#: Hazard keys of the readiness table. ``HEAT`` covers both CLIMADA tags the platform uses
#: for heat (``HW`` season-p95 Tmax, ``HM`` exceedance degree-days).
HAZARD_KEYS: tuple[str, ...] = ("RF", "TC", "HEAT")

#: CLIMADA hazard tags each key maps to.
HAZARD_TAGS: dict[str, tuple[str, ...]] = {"RF": ("RF",), "TC": ("TC",), "HEAT": ("HW", "HM")}

_G, _C, _K = (
    ModelId.GLOBAL_BASELINE.value,
    ModelId.DATA_API_COUNTRY.value,
    ModelId.KOREA_LOCAL.value,
)

#: The table. Each cell: status, the hazard source it would use, and why.
READINESS: dict[str, dict[str, dict[str, str]]] = {
    "RF": {
        _G: {
            "status": "READY",
            "hazard_source": (
                "CLIMADA Data API river_flood, spatial_coverage=global (ISIMIP, 150 arcsec)"
            ),
            "detail": "global reference; priced with ImpfRiverFlood.from_jrc_region_sector",
        },
        _C: {
            "status": "READY",
            "hazard_source": (
                "CLIMADA Data API river_flood, spatial_coverage=country, country_iso3alpha=KOR"
            ),
            "detail": (
                "country cut of the same Data API product (13 KOR datasets: historical, "
                "rcp26/60/85 x four windows); same impact function. Not Korea-local data."
            ),
        },
        _K: {
            "status": "NOT_IMPLEMENTED",
            "hazard_source": "환경부 홍수위험지도 (adapter interface only)",
            "detail": (
                "adapter defined, no dataset connected: licence (공공누리 제4유형) and access "
                "unresolved; nothing is downloaded or computed until they are"
            ),
        },
    },
    "TC": {
        _G: {
            "status": "READY",
            "hazard_source": (
                "CLIMADA Data API tropical_cyclone, spatial_coverage=global "
                "(synthetic random_walk tracks)"
            ),
            "detail": (
                "global reference; priced with the Eberenz 2021 regional function CLIMADA "
                "assigns to the country"
            ),
        },
        _C: {
            "status": "READY",
            "hazard_source": (
                "CLIMADA Data API tropical_cyclone, spatial_coverage=country, country_iso3alpha=KOR"
            ),
            "detail": (
                "country cut of the same Data API product; same impact function. "
                "Not Korea-local data."
            ),
        },
        _K: {
            "status": "NOT_IMPLEMENTED",
            "hazard_source": "국내 공식 태풍 자료 (adapter interface only)",
            "detail": "adapter defined, no domestic track/wind dataset connected",
        },
    },
    "HEAT": {
        _G: {
            "status": "NO_HAZARD_DATA",
            "hazard_source": "none — the platform has no global heat hazard layer",
            "detail": (
                "and CLIMADA ships no heat impact function, so even with data the cell "
                "would be HAZARD_ONLY"
            ),
        },
        _C: {
            "status": "NO_HAZARD_DATA",
            "hazard_source": "none — the CLIMADA Data API has no heat/heatwave hazard type",
            "detail": "verified against the Data API type list; nothing to fetch",
        },
        _K: {
            "status": "HAZARD_ONLY",
            "hazard_source": (
                "KMA 남한상세 (MK-PRISM v3.1 / AR6 SSP 5ENSMN) via the local catalog KOR "
                "heatwave / heat_mortality layers"
            ),
            "detail": (
                "hazard intensity is reported; no CLIMADA impact function exists, so "
                "financial fields stay null"
            ),
        },
    },
}


def readiness() -> dict[str, Any]:
    """The readiness table plus the model definitions, for the API and the exports."""
    return {
        "models": [m.value for m in ModelId],
        "definitions": dict(MODEL_DEFINITIONS),
        "hazards": {
            key: {model: dict(cell) for model, cell in cells.items()}
            for key, cells in READINESS.items()
        },
        "summary": {
            key: {m: c["status"] for m, c in cells.items()} for key, cells in READINESS.items()
        },
        "rule": (
            "READY cells are priced with the same published impact function; HAZARD_ONLY and "
            "NO_HAZARD_DATA cells carry null financial fields; NOT_IMPLEMENTED cells produce no "
            "result row values — never an estimate"
        ),
    }


def hazard_key_for_tag(haz_type: str) -> str | None:
    """``RF`` / ``TC`` / ``HEAT`` for a CLIMADA hazard tag, or None if the tag is not covered."""
    for key, tags in HAZARD_TAGS.items():
        if haz_type in tags:
            return key
    return None


def cell_status(haz_type: str, model_id: str) -> str | None:
    """Readiness status of one (hazard tag x model) cell, or None when unknown."""
    key = hazard_key_for_tag(haz_type)
    if key is None:
        return None
    cell = READINESS[key].get(model_id)
    return None if cell is None else cell["status"]
