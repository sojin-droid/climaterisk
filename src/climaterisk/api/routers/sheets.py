"""단면 sheet routes — read-only list and fetch of asset sheets."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from climaterisk.sheets import list_sheets, load_sheet

router = APIRouter(prefix="/sheets", tags=["sheets"])


@router.get("")
def get_sheets() -> list[dict[str, str]]:
    """Every available sheet as ``{sheet_id, title, place, hazard, date}``."""
    return list_sheets()


@router.get("/{sheet_id}")
def get_sheet(sheet_id: str) -> dict[str, Any]:
    """One sheet, or 404 for an unknown or malformed id."""
    sheet = load_sheet(sheet_id)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown sheet: {sheet_id}")
    return sheet
