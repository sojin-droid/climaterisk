"""Read-side of the visual board: layer files, GeoJSON, point lookup, region summary.

A layer file (``<layer>.board.json``, written by ``scripts/board_layer.py``) holds the pieces
(hazard grid cell x 시군구) with their rings and a value per kept return period. Nothing is
averaged here: a lookup returns the value of the piece containing the point, a region summary
gives piece count, minimum and maximum only. ``None`` (no data) is never turned into 0.

Algorithm:
    Point in polygon, even-odd rule: a ray from $(x, y)$ towards $+x$ crossing the ring edges
    $(x_i,y_i)\\to(x_j,y_j)$ with $(y_i>y)\\ne(y_j>y)$ and
    $x < x_i + (y-y_i)(x_j-x_i)/(y_j-y_i)$ toggles inside/outside. A polygon contains the
    point when the exterior ring does and no hole ring does. ASCII: count crossings of a
    horizontal ray; odd = inside; holes subtract.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from climaterisk import paths

_Ring = list[list[float]]
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def board_dir() -> Path:
    """Directory of the layer files, resolved at call time (``paths.derived_board_dir``)."""
    return paths.derived_board_dir()


def _file(name: str) -> Path:
    return board_dir() / f"{name}.board.json"


def _prepare(data: dict[str, Any]) -> dict[str, Any]:
    """Add a bbox per polygon (``_boxes``: per piece, per polygon) for the point pre-filter."""
    for piece in data["pieces"]:
        piece["_boxes"] = [_bbox(poly[0]) for poly in piece["polygons"]]
    return data


def _bbox(ring: _Ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def list_layers() -> list[dict[str, Any]]:
    """Meta dicts of every ``*.board.json`` file in ``board_dir()``, sorted by name."""
    out = []
    for path in sorted(board_dir().glob("*.board.json")):
        name = path.name[: -len(".board.json")]
        try:
            out.append(load_layer(name)["meta"])
        except (KeyError, ValueError, OSError):
            continue
    return out


def load_layer(name: str) -> dict[str, Any]:
    """Parsed layer file, cached by modification time.

    Raises:
        KeyError: No such layer (also for names that are not plain file stems).
    """
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise KeyError(name)
    path = _file(name)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        raise KeyError(name) from None
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    data = _prepare(json.loads(path.read_text(encoding="utf-8")))
    with _lock:
        _cache[key] = (mtime, data)
    return data


def period_key(rp: float) -> str:
    """String key of a return period in ``values`` (``100.0`` -> ``"100"``)."""
    return f"{float(rp):g}"


def is_metric(layer: dict[str, Any]) -> bool:
    """True for a chronic metric layer (``meta.key_kind == "metric"``)."""
    return bool(layer["meta"].get("key_kind", "return_period") == "metric")


def layer_keys(layer: dict[str, Any]) -> list[str]:
    """Value keys of a layer: metric keys in file order, or the kept return periods."""
    meta = layer["meta"]
    if is_metric(layer):
        return [str(k["key"]) for k in meta["keys"]]
    return [period_key(p) for p in meta["periods_kept"]]


def _resolve_key(layer: dict[str, Any], key: float | str) -> str:
    """Validated ``values`` key (metric: the string as is; return period: ``%g`` form).

    Raises:
        ValueError: Unknown key (message lists the valid keys; return-period layers also
            name ``max_supported``).
    """
    meta = layer["meta"]
    if is_metric(layer):
        valid = layer_keys(layer)
        if str(key) not in valid:
            raise ValueError(f"unknown key {key!r}; valid keys {valid}")
        return str(key)
    try:
        rp = float(key)
    except (TypeError, ValueError):
        raise ValueError(f"return period {key!r} is not a number") from None
    if rp not in [float(p) for p in meta["periods_kept"]]:
        raise ValueError(
            f"return period {period_key(rp)} y not available; kept {meta['periods_kept']}, "
            f"max_supported {meta['max_supported']:g} y"
        )
    return period_key(rp)


def key_quantity(layer: dict[str, Any], key: float | str) -> str:
    """Quantity (key of ``board_palettes.json`` ``quantities``) that colours ``key``.

    Metric layers: ``meta.keys[i].quantity``; return-period layers: ``meta.quantity``. There
    is no fallback from units or scale.

    Raises:
        ValueError: Unknown key, or the file does not name a quantity (re-export the layer).
    """
    vkey = _resolve_key(layer, key)
    meta = layer["meta"]
    quantity = None
    if is_metric(layer):
        quantity = next((k.get("quantity") for k in meta["keys"] if str(k["key"]) == vkey), None)
    else:
        quantity = meta.get("quantity")
    if not quantity:
        raise ValueError(
            f"layer {meta.get('layer')!r} key {vkey!r} names no quantity; re-export the layer"
        )
    return str(quantity)


def geojson(layer: dict[str, Any], key: float | str) -> dict[str, Any]:
    """FeatureCollection of the pieces coloured by ``key`` (return period or metric key).

    Raises:
        ValueError: Unknown key (see ``_resolve_key``).
    """
    vkey = _resolve_key(layer, key)
    features = []
    for piece in layer["pieces"]:
        polys = piece["polygons"]
        geometry = (
            {"type": "Polygon", "coordinates": polys[0]}
            if len(polys) == 1
            else {"type": "MultiPolygon", "coordinates": polys}
        )
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "id": piece["id"],
                    "centroid": piece["centroid"],
                    "code": piece["code"],
                    "name": piece["name"],
                    "value": piece["values"].get(vkey),
                    "exposure": piece.get("exposure"),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _in_ring(x: float, y: float, ring: _Ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            inside = not inside
        j = i
    return inside


def _in_polygon(x: float, y: float, rings: list[_Ring]) -> bool:
    return _in_ring(x, y, rings[0]) and not any(_in_ring(x, y, h) for h in rings[1:])


def lookup(layer: dict[str, Any], lon: float, lat: float) -> dict[str, Any] | None:
    """The piece containing ``(lon, lat)`` (even-odd, holes excluded) or ``None``."""
    meta = layer["meta"]
    for piece in layer["pieces"]:
        for poly, (x0, y0, x1, y1) in zip(piece["polygons"], piece["_boxes"], strict=True):
            if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_polygon(lon, lat, poly):
                return {
                    "id": piece["id"],
                    "code": piece["code"],
                    "name": piece["name"],
                    "centroid": piece["centroid"],
                    "values": piece["values"],
                    "exposure": piece.get("exposure"),
                    "key_kind": meta.get("key_kind", "return_period"),
                    "cell_km_ns": meta["cell_km_ns"],
                    "cell_km_ew": meta["cell_km_ew"],
                }
    return None


def region(layer: dict[str, Any], code: str) -> dict[str, Any]:
    """Piece count, per-key min/max (nulls ignored; all null -> ``None``) and exposure sums.

    Sums only for exposure (``exposure_sum``); there is no mean anywhere. ``by_rp`` and
    ``by_key`` carry the same content.

    Raises:
        KeyError: No piece carries ``code``.
    """
    pieces = [p for p in layer["pieces"] if p["code"] == code]
    if not pieces:
        raise KeyError(code)
    by_key: dict[str, dict[str, float] | None] = {}
    for key in layer_keys(layer):
        vals = [pc["values"].get(key) for pc in pieces]
        vals = [v for v in vals if v is not None]
        by_key[key] = {"min": min(vals), "max": max(vals)} if vals else None
    exposure_sum: dict[str, float] = {}
    for pc in pieces:
        for name, amount in (pc.get("exposure") or {}).items():
            exposure_sum[name] = exposure_sum.get(name, 0.0) + float(amount)
    return {
        "code": code,
        "name": pieces[0]["name"],
        "n_pieces": len(pieces),
        "by_rp": by_key,
        "by_key": by_key,
        "exposure_sum": exposure_sum,
    }
