"""Visual-board routes: national hazard pieces, point lookup, 시군구 summary (read-only)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from climaterisk.board import cases, palette, store
from climaterisk.config import get_settings

router = APIRouter(prefix="/board", tags=["board"])

_VISUAL_BOARD_FILE = "visual_board.json"


def _layer(name: str) -> dict[str, Any]:
    try:
        return store.load_layer(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown board layer {name!r}") from None


@router.get("/palettes")
def palettes() -> dict[str, Any]:
    """The colour spec (``assets/libraries/board_palettes.json``) unchanged."""
    return palette.load_palettes()


@router.get("/layers")
def layers() -> list[dict[str, Any]]:
    """Meta of every available layer."""
    return store.list_layers()


@router.get("/layers/{name}")
def layer_meta(name: str) -> dict[str, Any]:
    """Meta of one layer."""
    meta: dict[str, Any] = _layer(name)["meta"]
    return meta


@router.get("/layers/{name}/geojson")
def layer_geojson(name: str, rp: float | None = None, key: str | None = None) -> dict[str, Any]:
    """Pieces as GeoJSON with ``value`` for ``key`` (metric layers) or ``rp`` (return period).

    400 if the key / return period is unknown or not computable, or neither is given.
    """
    layer = _layer(name)
    chosen: float | str | None = key if key is not None else rp
    if chosen is None:
        raise HTTPException(status_code=400, detail="give key=<k> (or rp=<years>)")
    try:
        return store.geojson(layer, chosen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/layers/{name}/lookup")
def layer_lookup(name: str, lon: float, lat: float) -> dict[str, Any]:
    """Value of the grid-cell piece containing the point (404 outside every piece)."""
    hit = store.lookup(_layer(name), lon, lat)
    if hit is None:
        raise HTTPException(status_code=404, detail="no hazard piece at this point")
    return hit


@router.get("/layers/{name}/regions/{code}")
def layer_region(name: str, code: str) -> dict[str, Any]:
    """Piece count and min/max per key (and exposure sums) of one 시군구 (no mean)."""
    try:
        return store.region(_layer(name), code)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown region {code!r}") from None


@router.get("/layers/{name}/regions")
def layer_regions(name: str) -> list[dict[str, Any]]:
    """Every 시군구 of one layer: code, name, piece count (sorted by code)."""
    return store.regions(_layer(name))


@router.get("/regions")
def regions() -> list[dict[str, str]]:
    """Union over layers of ``{code, name}`` sorted by code (for the 시군구 search)."""
    return store.all_regions()


@router.get("/regions/{code}")
def region_all(code: str) -> dict[str, Any]:
    """One row per layer for a 시군구: piece count and headline min/max (no mean, no ranking)."""
    try:
        rows = store.region_all(code)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown region {code!r}") from None
    name = next(r["name"] for r in store.all_regions() if r["code"] == code)
    return {"code": code, "name": name, "layers": rows}


@router.get("/ladder")
def ladder() -> list[dict[str, Any]]:
    """The resolution ladder (national / sigungu / point / case) from ``visual_board.json``."""
    cfg = json.loads((get_settings().library_path / _VISUAL_BOARD_FILE).read_text(encoding="utf-8"))
    rungs: list[dict[str, Any]] = cfg["resolution_ladder"]
    return rungs


@router.get("/cases")
def cases_list() -> list[dict[str, Any]]:
    """Configured case twins with availability and badge."""
    return cases.list_cases()


@router.get("/cases/{case_id}")
def case_one(case_id: str) -> dict[str, Any]:
    """One case twin's page data (404 when unknown or not built)."""
    try:
        return cases.load_case(case_id)
    except (KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail=f"unknown case {case_id!r}") from None


@router.get("/cases/{case_id}/figures/{file}")
def case_figure(case_id: str, file: str) -> FileResponse:
    """A figure listed in the case config (PNG); anything else is 404."""
    try:
        path = cases.figure_path(case_id, file)
    except (KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="no such figure") from None
    return FileResponse(path, media_type="image/png")
