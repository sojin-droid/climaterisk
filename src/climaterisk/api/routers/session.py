"""Session routes — create / read / update (full-model sync) / delete.

The frontend persists only the session id and PUTs the whole portfolio document
on a debounce. This mirrors the backend-owns-the-model pattern.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from climaterisk.api.deps import get_session_store
from climaterisk.core.entities import Portfolio
from climaterisk.data.session_store import SessionStore

router = APIRouter(prefix="/session", tags=["session"])

StoreDep = Annotated[SessionStore, Depends(get_session_store)]


@router.post("", response_model=Portfolio, status_code=status.HTTP_201_CREATED)
def create_session(store: StoreDep) -> Portfolio:
    """Create a fresh, empty session and return its portfolio (id == session id)."""
    return store.create()


@router.get("/{session_id}", response_model=Portfolio)
def get_session(session_id: str, store: StoreDep) -> Portfolio:
    """Return the portfolio for a session, or 404 if unknown."""
    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    return portfolio


@router.put("/{session_id}", response_model=Portfolio)
def save_session(session_id: str, portfolio: Portfolio, store: StoreDep) -> Portfolio:
    """Replace the stored model for a session (full-model sync). 404 if unknown."""
    # The path id is authoritative; keep the document id aligned with the session.
    portfolio.id = session_id
    saved = store.save(session_id, portfolio)
    if saved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    return saved


@router.post("/{session_id}/assets/import")
async def import_assets(
    session_id: str,
    store: StoreDep,
    file: Annotated[UploadFile, File(description="xlsx or csv in one of the asset templates")],
    kind: str = "latlon",
    mode: str = "merge",
) -> dict[str, Any]:
    """Import assets from a template file (``kind`` = latlon | sigungu | sido).

    ``mode=merge`` (default) replaces assets whose id is in the file and keeps the rest;
    ``mode=replace`` swaps the whole portfolio for the file. The file is refused as a whole
    (HTTP 422, line-numbered ``errors``) when any row is wrong, so a half-imported
    portfolio never exists. Returns ``{"portfolio": …, "report": …}``.
    """
    from climaterisk.physical_risk.asset_import import (
        IMPORT_MODES,
        TEMPLATE_KINDS,
        AssetImportError,
        merge_assets,
        parse_assets,
    )

    portfolio = store.get(session_id)
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    if kind not in TEMPLATE_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"kind must be one of {TEMPLATE_KINDS}"
        )
    if mode not in IMPORT_MODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"mode must be one of {IMPORT_MODES}"
        )
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="the uploaded file is empty")
    try:
        incoming, report = parse_assets(
            file.filename or "", content, kind, {a.id for a in portfolio.assets}
        )
    except AssetImportError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "file refused — fix the listed rows", "errors": exc.errors},
        ) from exc
    report.mode = mode
    portfolio.assets, report.removed = merge_assets(portfolio.assets, incoming, mode)
    saved = store.save(session_id, portfolio)
    if saved is None:  # the session vanished between the read and the write
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    return {"portfolio": saved.model_dump(mode="json"), "report": report.to_dict()}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, store: StoreDep) -> Response:
    """Delete a session. 404 if it did not exist."""
    if not store.delete(session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
