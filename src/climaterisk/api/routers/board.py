"""Visual-board routes: national hazard pieces, point lookup, 시군구 summary (read-only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from climaterisk.board import palette, store

router = APIRouter(prefix="/board", tags=["board"])


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
