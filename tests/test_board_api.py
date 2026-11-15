"""Visual board step 2 gate (written by the reviewer before the step; agents may not edit it).

Backend side of the board (no CLIMADA, no geopandas: the API env has neither). The worker
CLI (step 1) additionally exports a compact layer file ``<board_dir>/<layer>.board.json``:

    {"meta": {"layer", "units", "periods_kept", "max_supported", "record_years",
              "resolution_arcsec", "cell_km_ns", "cell_km_ew", "hazard_source",
              "boundary_source", "use_tier", "use_tier_reason"},
     "pieces": [{"id", "centroid", "code", "name",
                 "polygons": [[exterior_ring, hole_ring, ...], ...],   # rings of [lon, lat]
                 "values": {"<rp>": float | null, ...}}]}

Module ``src/climaterisk/board/store.py``:
- ``board_dir()`` -> Path (``paths.derived_board_dir()``), looked up at call time.
- ``list_layers()`` -> list of meta dicts of the ``*.board.json`` files in ``board_dir()``.
- ``load_layer(name)`` -> the parsed file; ``KeyError`` when absent.
- ``geojson(layer, rp)`` -> FeatureCollection; one Feature per piece, geometry Polygon or
  MultiPolygon from ``polygons``, properties ``id, centroid, code, name, value`` where
  ``value`` is ``values[rp]`` with null kept as None (never 0). ``ValueError`` if ``rp`` is
  not in ``periods_kept`` (the message names ``max_supported``).
- ``lookup(layer, lon, lat)`` -> ``{"id", "code", "name", "centroid", "values",
  "cell_km_ns", "cell_km_ew"}`` of the piece containing the point (even-odd rule, holes
  excluded), or None.
- ``region(layer, code)`` -> ``{"code", "name", "n_pieces", "by_rp": {rp: {"min", "max"}}}``
  over the region's pieces, nulls ignored (all null -> None). No mean. ``KeyError`` if absent.

Router ``src/climaterisk/api/routers/board.py`` mounted at ``/api/board``:
``GET /layers``, ``GET /layers/{name}`` (404), ``GET /layers/{name}/geojson?rp=`` (404/400),
``GET /layers/{name}/lookup?lon=&lat=`` (404 outside), ``GET /layers/{name}/regions/{code}`` (404).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

store = pytest.importorskip("climaterisk.board.store")


def _sq(x0: float, y0: float, s: float) -> list[list[float]]:
    return [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]


LAYER = {
    "meta": {
        "layer": "rf_test",
        "units": "m",
        "periods_kept": [10.0, 100.0],
        "max_supported": 120.0,
        "record_years": 240.0,
        "resolution_arcsec": 150.0,
        "cell_km_ns": 4.64,
        "cell_km_ew": 3.73,
        "hazard_source": "test",
        "boundary_source": "test",
        "use_tier": "I",
        "use_tier_reason": "test reason",
    },
    "pieces": [
        # cell 0 split in two regions A / B; region A piece has a hole
        {
            "id": 0,
            "centroid": 0,
            "code": "A",
            "name": "west",
            "polygons": [[_sq(127.0, 37.0, 0.05), _sq(127.01, 37.01, 0.01)]],
            "values": {"10": 0.0, "100": 1.5},
        },
        {
            "id": 1,
            "centroid": 0,
            "code": "B",
            "name": "east",
            "polygons": [[_sq(127.05, 37.0, 0.05)]],
            "values": {"10": 0.0, "100": 1.5},
        },
        {
            "id": 2,
            "centroid": 1,
            "code": "A",
            "name": "west",
            "polygons": [[_sq(127.0, 37.05, 0.05)], [_sq(127.2, 37.2, 0.01)]],
            "values": {"10": None, "100": 0.4},
        },
    ],
}


@pytest.fixture
def board_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "rf_test.board.json").write_text(json.dumps(LAYER), encoding="utf-8")
    monkeypatch.setattr(store, "board_dir", lambda: tmp_path)
    return tmp_path


def test_store_functions(board_dir: Path) -> None:
    assert [m["layer"] for m in store.list_layers()] == ["rf_test"]
    layer = store.load_layer("rf_test")
    with pytest.raises(KeyError):
        store.load_layer("nope")

    fc = store.geojson(layer, 100)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 3
    by_id = {f["properties"]["id"]: f for f in fc["features"]}
    assert by_id[2]["geometry"]["type"] == "MultiPolygon"
    assert by_id[0]["geometry"]["type"] == "Polygon"
    assert len(by_id[0]["geometry"]["coordinates"]) == 2  # exterior + hole
    assert store.geojson(layer, 10)["features"][2]["properties"]["value"] is None  # null kept
    assert store.geojson(layer, 10)["features"][0]["properties"]["value"] == 0.0  # zero kept
    with pytest.raises(ValueError, match="120"):
        store.geojson(layer, 500)

    hit = store.lookup(layer, 127.07, 37.02)
    assert hit is not None and hit["code"] == "B" and hit["centroid"] == 0
    assert hit["values"] == {"10": 0.0, "100": 1.5}
    assert (hit["cell_km_ns"], hit["cell_km_ew"]) == (4.64, 3.73)
    assert store.lookup(layer, 127.015, 37.015) is None  # inside the hole
    assert store.lookup(layer, 127.205, 37.205)["id"] == 2  # second polygon of a multipolygon
    assert store.lookup(layer, 130.0, 40.0) is None

    a = store.region(layer, "A")
    assert a["n_pieces"] == 2 and a["name"] == "west"
    assert a["by_rp"]["10"] == {"min": 0.0, "max": 0.0}
    assert a["by_rp"]["100"] == {"min": 0.4, "max": 1.5}
    assert "mean" not in json.dumps(a)
    with pytest.raises(KeyError):
        store.region(layer, "Z")


def test_board_routes(board_dir: Path, client: TestClient) -> None:
    layers = client.get("/api/board/layers")
    assert layers.status_code == 200 and layers.json()[0]["layer"] == "rf_test"
    assert client.get("/api/board/layers/nope").status_code == 404
    meta = client.get("/api/board/layers/rf_test").json()
    assert meta["cell_km_ew"] == 3.73 and meta["use_tier"] == "I"
    gj = client.get("/api/board/layers/rf_test/geojson", params={"rp": 100})
    assert gj.status_code == 200 and len(gj.json()["features"]) == 3
    assert client.get("/api/board/layers/rf_test/geojson", params={"rp": 500}).status_code == 400
    assert client.get("/api/board/layers/nope/geojson", params={"rp": 10}).status_code == 404
    ok = client.get("/api/board/layers/rf_test/lookup", params={"lon": 127.07, "lat": 37.02})
    assert ok.status_code == 200 and ok.json()["code"] == "B"
    out = client.get("/api/board/layers/rf_test/lookup", params={"lon": 130, "lat": 40})
    assert out.status_code == 404
    reg = client.get("/api/board/layers/rf_test/regions/A")
    assert reg.status_code == 200 and reg.json()["n_pieces"] == 2
    assert client.get("/api/board/layers/rf_test/regions/Z").status_code == 404
