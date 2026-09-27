"""Library routes — expose the bundled methodology libraries to the UI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from climaterisk.config import get_settings
from climaterisk.data.libraries import load_libraries

router = APIRouter(prefix="/libraries", tags=["libraries"])

# Methodology figures produced by the analysis scripts, per peril. These are *method*
# artifacts (how a peril is modelled), not per-run outputs, so they live under the data
# directory and are served read-only to the Method/Results views.
_METHOD_FIGURES: dict[str, tuple[str, str]] = {
    "heat_mortality": (
        "heatwave_europe/heatwave_europe.png",
        "scripts/heatwave_europe.py",
    ),
}


@router.get("")
def get_all_libraries() -> dict[str, dict[str, Any]]:
    """Return every bundled library (sectors, perils, scenarios, impact_functions)."""
    return load_libraries()


@router.get("/method-figure/{peril}.png")
def get_method_figure(peril: str) -> FileResponse:
    """Serve a peril's methodology figure, or 404 when it has not been generated yet.

    The figure is written by the peril's analysis script (see ``_METHOD_FIGURES``) rather
    than by a run, so a 404 simply means "run that script first" — callers hide the panel.
    """
    entry = _METHOD_FIGURES.get(peril)
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"no methodology figure for peril '{peril}'"
        )
    rel, script = entry
    png = get_settings().data_path / rel
    if not png.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"figure not generated yet — run `{script}` to produce {rel}",
        )
    return FileResponse(png, media_type="image/png")


@router.get("/physical-risk-models")
def get_physical_risk_models() -> dict[str, Any]:
    """The three hazard models, their definitions and the per-hazard readiness table.

    Static: it describes what the platform can compute, read from
    ``climaterisk.physical_risk.models`` (shared with the CLIMADA worker, which asserts
    its adapters agree with it).
    """
    from climaterisk.physical_risk.display_copy import display_bundle
    from climaterisk.physical_risk.models import readiness

    return {**readiness(), "display": display_bundle()}


@router.get("/municipalities")
def get_municipalities(level: str | None = None) -> dict[str, Any]:
    """The bundled Korea municipality representative points (V0.2), with provenance.

    Each point is an interior point of the official boundary (Statistics Korea SGIS), used
    as a spatial anchor for hazard screening — not a city hall, not an area aggregate, and
    never carrying an asset value of its own. ``level`` filters (e.g. ``METROPOLITAN``).
    """
    from climaterisk.physical_risk.municipalities import (
        LEVELS,
        dataset_summary,
        load_municipalities,
    )

    if level is not None and level not in LEVELS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"unknown level {level!r}; expected one of {LEVELS}"
        )
    items = [
        m.to_dict() for m in load_municipalities() if level is None or m.municipality_level == level
    ]
    from climaterisk.physical_risk.official_offices import (
        NOT_AVAILABLE_LABEL,
        PROVENANCE_LABEL,
        load_offices,
    )
    from climaterisk.physical_risk.official_offices import dataset_summary as office_summary

    return {
        "municipalities": items,
        "summary": dataset_summary(),
        "levels": list(LEVELS),
        # V0.3 POC — the 7 metropolitan city halls; coordinates only where government-published
        "official_offices": [o.to_dict() for o in load_offices()],
        "official_office_summary": {
            **office_summary(),
            "not_available_label": NOT_AVAILABLE_LABEL,
            "provenance_label": PROVENANCE_LABEL,
        },
    }


@router.get("/{name}")
def get_library(name: str) -> dict[str, Any]:
    """Return a single named library, or 404 if it does not exist."""
    libraries = load_libraries()
    if name not in libraries:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown library: {name}")
    return libraries[name]
