"""Run routes — submit a physical-risk analysis and poll for results.

POST submits a run (spawns the CLIMADA worker); GET polls until it is done/error.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from climaterisk.api.deps import get_run_manager, get_session_store
from climaterisk.config import get_settings
from climaterisk.data.session_store import SessionStore
from climaterisk.engines.base import OBSERVED_SOURCES, MeasureSpec
from climaterisk.runs.manager import RunManager
from climaterisk.runs.store import Run

router = APIRouter(prefix="/session", tags=["run"])

StoreDep = Annotated[SessionStore, Depends(get_session_store)]
ManagerDep = Annotated[RunManager, Depends(get_run_manager)]


@router.post("/{session_id}/run", response_model=Run)
def submit_run(session_id: str, store: StoreDep, manager: ManagerDep) -> Run:
    """Submit an analysis run for the session's portfolio."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not portfolio.assets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="portfolio has no assets")
    if not portfolio.run_config.perils:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="no perils selected")
    return manager.submit(portfolio)


@router.post("/{session_id}/cost-benefit", response_model=Run)
def submit_cost_benefit(
    session_id: str, measures: list[MeasureSpec], store: StoreDep, manager: ManagerDep
) -> Run:
    """Submit an adaptation cost-benefit run (polled via the run-status route)."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not portfolio.assets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="portfolio has no assets")
    if not measures:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="no adaptation measures provided")
    return manager.submit_cost_benefit(portfolio, measures)


@router.post("/{session_id}/uncertainty", response_model=Run)
def submit_uncertainty(
    session_id: str, store: StoreDep, manager: ManagerDep, n_samples: int = 50
) -> Run:
    """Submit a Monte-Carlo uncertainty run (polled via the run-status route)."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not portfolio.assets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="portfolio has no assets")
    return manager.submit_uncertainty(portfolio, max(10, min(n_samples, 200)))


@router.post("/{session_id}/litpop", response_model=Run)
def submit_litpop(
    session_id: str,
    country: str,
    store: StoreDep,
    manager: ManagerDep,
    source: str = "litpop",
    peril: str = "tropical_cyclone",
) -> Run:
    """Submit a modeled-exposure run (source × peril) for a country; polled via run-status."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not country or len(country) != 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="country must be an ISO3 code")
    return manager.submit_litpop(portfolio, country.upper(), source, peril)


@router.post("/{session_id}/hazard-preview", response_model=Run)
def submit_hazard_preview(
    session_id: str,
    peril: str,
    store: StoreDep,
    manager: ManagerDep,
    scenario: str = "historical",
    region: str = "global",
    year: int | None = None,
    bbox: str | None = None,
) -> Run:
    """Render a local-catalog hazard's intensity field to a map raster (polled via run-status).

    ``bbox`` optionally crops the render to "south,west,north,east" (degrees) — e.g. a
    window around the portfolio's assets — with the color scale normalized to that area.
    """
    if store.get(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    bbox_vals: list[float] | None = None
    if bbox:
        try:
            bbox_vals = [float(x) for x in bbox.split(",")]
        except ValueError:
            bbox_vals = []
        if len(bbox_vals) != 4 or bbox_vals[0] >= bbox_vals[2] or bbox_vals[1] >= bbox_vals[3]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="bbox must be 'south,west,north,east' with south<north and west<east",
            )
    return manager.submit_hazard_preview(session_id, peril, scenario, region, year, bbox_vals)


@router.get("/{session_id}/run/{run_id}/preview.png")
def get_hazard_preview_image(session_id: str, run_id: str) -> FileResponse:
    """Serve the rendered hazard-preview PNG for a finished hazard-preview run."""
    png = get_settings().runs_path / run_id / "preview.png"
    if not png.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no preview image for this run")
    return FileResponse(png, media_type="image/png")


@router.post("/{session_id}/forecast", response_model=Run)
def submit_forecast(session_id: str, store: StoreDep, manager: ManagerDep) -> Run:
    """Submit an operational TC-forecast run (latest ECMWF ensemble tracks)."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not portfolio.assets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="portfolio has no assets")
    return manager.submit_forecast(portfolio)


@router.post("/{session_id}/calibration", response_model=Run)
def submit_calibration(
    session_id: str,
    store: StoreDep,
    manager: ManagerDep,
    observed_source: str = "emdat",
) -> Run:
    """Submit an impact-function calibration run (fit TC v_half to an observed loss series).

    ``observed_source`` (``emdat`` | ``disaster_yearbook``) selects the observed series and
    defaults to ``emdat``, so a client that does not send it behaves exactly as before.
    ``disaster_yearbook`` has no loader yet and the worker says so explicitly rather than
    substituting another series (docs/OBSERVED_LOSSES_KR_SPEC.md §9).
    """
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not portfolio.assets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="portfolio has no assets")
    if observed_source not in OBSERVED_SOURCES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"unknown observed_source '{observed_source}' (expected {OBSERVED_SOURCES})",
        )
    return manager.submit_calibration(portfolio, observed_source)


@router.post("/{session_id}/supplychain", response_model=Run)
def submit_supplychain(
    session_id: str,
    store: StoreDep,
    manager: ManagerDep,
    mriot_type: str = "WIOD16",
    mriot_year: int = 2010,
) -> Run:
    """Submit a supply-chain indirect-impact run (polled via the run-status route)."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if not portfolio.assets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="portfolio has no assets")
    return manager.submit_supplychain(portfolio, mriot_type, mriot_year)


# Ingest sources the worker implements (``climaterisk_worker.ingest._REFINERS``) and the Data
# tab advertises (``assets/libraries/data_sources.json`` ``fetch.source``). Keep all three in
# step — tests/test_ingest_sources.py checks it.
INGEST_SOURCES: tuple[str, ...] = ("dataapi", "aqueduct", "copdem", "tctracks", "tcrain")


class IngestBody(BaseModel):
    """Request body for a data-ingest run (scenario/year default to the session)."""

    source: str  # one of INGEST_SOURCES
    peril: str = "river_flood"  # dataapi: tropical_cyclone | river_flood | wildfire | earthquake
    scenario: str | None = None
    year: int | None = None


@router.post("/{session_id}/ingest", response_model=Run)
def submit_ingest(session_id: str, body: IngestBody, store: StoreDep, manager: ManagerDep) -> Run:
    """Download + refine a data source into the local CLIMADA catalog (polled via run-status).

    Scoped to the session's assets: the worker bounds the download to their bbox and
    keys the catalog entry to their region, so the matching runner picks it up.
    """
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if body.source not in INGEST_SOURCES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown source '{body.source}'")
    if not portfolio.assets:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="place at least one asset first — ingestion is scoped to the portfolio region",
        )
    scenario = body.scenario or portfolio.scenario.climate
    anchor = portfolio.scenario.anchor_years
    year = body.year or (max(anchor) if anchor else 2050)
    return manager.submit_ingest(portfolio, body.source, body.peril, scenario, year)


@router.get("/{session_id}/latest-runs", response_model=dict[str, Run])
def get_latest_runs(session_id: str, store: StoreDep, manager: ManagerDep) -> dict[str, Run]:
    """The most recent finished run of each kind, so a reloaded page can re-attach.

    Runs are persisted, but the browser only remembers the ids it submitted — after a
    refresh the Results view had nothing to show even though the analysis was done. The
    keys are the kinds from ``runs.store.run_kind`` (``physical``, ``litpop``,
    ``uncertainty``, ``cost_benefit``, …).
    """
    if store.get(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    return manager.latest_runs(session_id)


@router.get("/{session_id}/run/{run_id}", response_model=Run)
def get_run(session_id: str, run_id: str, manager: ManagerDep) -> Run:
    """Poll a run; finalizes it once the worker subprocess has exited."""
    run = manager.poll(run_id)
    if run is None or run.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@router.post("/{session_id}/finance")
def compute_finance_route(
    session_id: str,
    run_id: str,
    store: StoreDep,
    manager: ManagerDep,
    transition_cost: float = 0.0,
) -> dict[str, Any]:
    """Climate Risk Premium for a completed physical run: cashflow → NPV/IRR/DSCR →
    rating → CRP, baseline vs climate-stressed. Synchronous (pure finance math, no worker)."""
    from climaterisk.data.libraries import load_libraries
    from climaterisk.finance.service import compute_finance

    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    run = manager.poll(run_id)
    if run is None or run.session_id != session_id or run.status != "done" or not run.output:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="run not finished")
    prof = portfolio.run_config.financial_profile
    is_power = prof is not None and prof.financial_model == "power_gen"
    if prof is None or not prof.capex:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="set a financial profile (CAPEX) in the Finance tab first",
        )
    if is_power and not (
        prof.capacity_mw and prof.power_price and (prof.capacity_factor or prof.plant_fuel)
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="power-generation model needs capacity (MW), price (per MWh) and a "
            "capacity factor (or fuel type)",
        )
    if not is_power and not prof.annual_ebitda:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="set a financial profile (CAPEX + annual EBITDA) in the Finance tab first",
        )
    libs = load_libraries()
    return compute_finance(
        portfolio, run.output, transition_cost, libs["finance_reference"], libs["finance_channels"]
    )


def _per_asset_rows(output: dict) -> list[dict]:  # type: ignore[type-arg]
    """Flatten a run's per-asset impacts (physical per-peril, or forecast/litpop)."""
    rows: list[dict] = []  # type: ignore[type-arg]
    for r in output.get("results", []) or []:  # physical: one block per peril
        for a in r.get("per_asset", []):
            rows.append({"peril": r.get("peril"), **a})
    if not rows:  # forecast / litpop carry per_asset / per_point at the top level
        for a in output.get("per_asset", []) or output.get("per_point", []):
            rows.append(dict(a))
    return rows


@router.get("/{session_id}/run/{run_id}/export")
def export_run(session_id: str, run_id: str, manager: ManagerDep, fmt: str = "csv") -> Response:
    """Export a finished run's per-asset impacts as CSV or GeoJSON (download)."""
    run = manager.poll(run_id)
    if run is None or run.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    rows = _per_asset_rows(run.output or {})
    if fmt == "geojson":
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [a.get("lon"), a.get("lat")]},
                "properties": {k: v for k, v in a.items() if k not in ("lat", "lon")},
            }
            for a in rows
            if a.get("lat") is not None and a.get("lon") is not None
        ]
        body = json.dumps({"type": "FeatureCollection", "features": features}, indent=2)
        return Response(
            body,
            media_type="application/geo+json",
            headers={"Content-Disposition": f'attachment; filename="run_{run_id}.geojson"'},
        )
    buf = io.StringIO()
    if rows:
        fields = list({k for row in rows for k in row})
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id}.csv"'},
    )
