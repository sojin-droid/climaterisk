"""Visual board step 3 gate (reviewer-written; agents may not edit it): chronic "metric" layers.

Drought (SPEI-3) has no return periods. A ``.board.json`` may therefore declare
``meta.key_kind = "metric"`` (default ``"return_period"`` keeps step 2 behaviour) and list
``meta.keys``: ``[{"key", "label", "units", "scale"}]`` where ``scale`` is ``"share"``
(0..1) or ``"spei"``. Pieces may carry ``exposure``: ``{name: number}`` (e.g. farmland ha).

``climaterisk.board.store``:
- ``layer_keys(layer)`` -> list of key strings: for metric layers ``[k["key"] ...]`` in file
  order; for return-period layers the ``periods_kept`` keys as in ``values`` ("100").
- ``geojson(layer, key)`` accepts the key as a string for metric layers (``ValueError`` for
  an unknown key, message lists the valid keys); return-period layers keep accepting
  numbers. Feature properties add ``exposure`` (dict, or None when absent).
- ``lookup`` adds ``exposure`` (dict or None) and ``key_kind``.
- ``region`` adds ``exposure_sum``: per exposure name the SUM over the region's pieces
  (sums only; there is still no mean anywhere) and ``by_key`` (same content as ``by_rp``).
Routes: ``GET /api/board/layers/{name}/geojson?key=<k>`` (``rp=`` still works for
return-period layers); 400 for an unknown key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

store = pytest.importorskip("climaterisk.board.store")


def _sq(x0: float, y0: float, s: float) -> list[list[float]]:
    return [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]


METRIC = {
    "meta": {
        "layer": "dr_test",
        "key_kind": "metric",
        "keys": [
            {
                "key": "freq_ge2",
                "label": "주의 이상 빈도",
                "units": "share of years",
                "scale": "share",
            },
            {"key": "y2022", "label": "2022 최소 SPEI-3", "units": "-", "scale": "spei"},
        ],
        "units": "-",
        "resolution_arcsec": 150.0,
        "cell_km_ns": 4.63,
        "cell_km_ew": 3.68,
        "hazard_source": "test",
        "boundary_source": "test",
        "use_tier": "S",
        "use_tier_reason": "test",
    },
    "pieces": [
        {
            "id": 0,
            "centroid": 0,
            "code": "A",
            "name": "west",
            "polygons": [[_sq(127.0, 37.0, 0.05)]],
            "values": {"freq_ge2": 0.2, "y2022": -1.7},
            "exposure": {"upland_ha": 10.0, "orchard_ha": 2.0, "rice_ha": 5.0},
        },
        {
            "id": 1,
            "centroid": 1,
            "code": "A",
            "name": "west",
            "polygons": [[_sq(127.05, 37.0, 0.05)]],
            "values": {"freq_ge2": None, "y2022": -0.3},
            "exposure": {"upland_ha": 1.5, "orchard_ha": 0.0, "rice_ha": 0.0},
        },
    ],
}

RP = {
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
        "use_tier_reason": "test",
    },
    "pieces": [
        {
            "id": 0,
            "centroid": 0,
            "code": "A",
            "name": "west",
            "polygons": [[_sq(127.0, 37.0, 0.05)]],
            "values": {"10": 0.0, "100": 1.5},
        }
    ],
}


@pytest.fixture
def board_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dr_test.board.json").write_text(json.dumps(METRIC), encoding="utf-8")
    (tmp_path / "rf_test.board.json").write_text(json.dumps(RP), encoding="utf-8")
    monkeypatch.setattr(store, "board_dir", lambda: tmp_path)
    return tmp_path


def test_metric_layer_store(board_dir: Path) -> None:
    dr = store.load_layer("dr_test")
    assert store.layer_keys(dr) == ["freq_ge2", "y2022"]
    fc = store.geojson(dr, "y2022")
    assert [f["properties"]["value"] for f in fc["features"]] == [-1.7, -0.3]
    assert fc["features"][0]["properties"]["exposure"]["upland_ha"] == 10.0
    assert store.geojson(dr, "freq_ge2")["features"][1]["properties"]["value"] is None
    with pytest.raises(ValueError, match="freq_ge2"):
        store.geojson(dr, "y1990")
    hit = store.lookup(dr, 127.06, 37.01)
    assert hit["id"] == 1 and hit["key_kind"] == "metric"
    assert hit["exposure"] == {"upland_ha": 1.5, "orchard_ha": 0.0, "rice_ha": 0.0}
    reg = store.region(dr, "A")
    assert reg["exposure_sum"] == {"upland_ha": 11.5, "orchard_ha": 2.0, "rice_ha": 5.0}
    assert reg["by_key"]["freq_ge2"] == {"min": 0.2, "max": 0.2}
    assert reg["by_key"]["y2022"] == {"min": -1.7, "max": -0.3}
    assert "mean" not in json.dumps(reg)


def test_return_period_layer_unchanged(board_dir: Path) -> None:
    rf = store.load_layer("rf_test")
    assert store.layer_keys(rf) == ["10", "100"]
    assert store.geojson(rf, 100)["features"][0]["properties"]["value"] == 1.5
    assert store.geojson(rf, 100)["features"][0]["properties"]["exposure"] is None
    assert store.lookup(rf, 127.01, 37.01)["key_kind"] == "return_period"


def test_metric_routes(board_dir: Path, client: TestClient) -> None:
    ok = client.get("/api/board/layers/dr_test/geojson", params={"key": "y2022"})
    assert ok.status_code == 200 and len(ok.json()["features"]) == 2
    bad = client.get("/api/board/layers/dr_test/geojson", params={"key": "nope"})
    assert bad.status_code == 400
    rp = client.get("/api/board/layers/rf_test/geojson", params={"rp": 100})
    assert rp.status_code == 200
    reg = client.get("/api/board/layers/dr_test/regions/A").json()
    assert reg["exposure_sum"]["upland_ha"] == 11.5
