"""Board step 3–4 gate: 시군구 panel, resolution ladder, representative-point copy
(reviewer-written; agents may not edit it).

Module ``src/climaterisk/board/store.py`` (additions):
- ``regions(layer)`` -> ``[{"code", "name", "n_pieces"}]`` one row per 시군구 code present in
  the layer, sorted by code; ``n_pieces`` equals ``region(layer, code)["n_pieces"]``.
- ``headline_key(layer)`` -> the key shown for a layer in the all-layer 시군구 panel:
  metric layers ``meta.default_key`` when it is one of the keys, else the first key;
  return-period layers ``"100"`` when 100 is kept, else the largest kept period.
- ``region_all(code)`` -> one row per layer of ``list_layers()`` that has pieces with
  ``code``, in ``list_layers()`` order (never sorted by value — no ranking):
  ``{"layer", "label", "peril", "quantity", "headline_key", "n_pieces", "headline"}`` with
  ``headline`` = ``region(layer, code)["by_key"][headline_key]`` (``{"min","max"}`` or None),
  ``label`` = ``meta.label`` (or the layer id), ``quantity`` = ``key_quantity`` of the
  headline key (None if the file names none). No mean anywhere. ``KeyError`` when no layer has
  the code.

Router (``/api/board``): ``GET /regions`` -> union over layers of ``{"code", "name"}`` sorted by
code (no duplicates); ``GET /regions/{code}`` -> ``{"code", "name", "layers": region_all}``
(404 when unknown); ``GET /layers/{name}/regions`` -> ``regions(layer)``.

Config ``assets/libraries/visual_board.json``: ``resolution_ladder`` = rungs with ids
``["national", "sigungu", "point", "case"]`` in that order, each with non-empty ``label_ko``,
``label_en``, ``note_ko``.

Copy: ``climaterisk.physical_risk.display_copy.BOARD_POINTER`` (non-empty, names "시각 보드"
and says min–max / no mean); ``docs/municipality-physical-risk.md`` has a section
``## 11.`` that names the visual board pieces (조각) and says the representative point is a
single-point screen, not a 시군구 value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

store = pytest.importorskip("climaterisk.board.store")

ROOT = Path(__file__).resolve().parents[1]


def _sq(x0: float, y0: float, s: float) -> list[list[float]]:
    return [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]


def _piece(i: int, code: str, name: str, x: float, values: dict) -> dict:
    return {
        "id": i,
        "centroid": i,
        "code": code,
        "name": name,
        "polygons": [[_sq(x, 37.0, 0.05)]],
        "values": values,
    }


RP = {
    "meta": {
        "layer": "a_rp",
        "label": "홍수 테스트",
        "peril": "river_flood",
        "quantity": "water_depth_m",
        "units": "m",
        "periods_kept": [10.0, 50.0],
        "max_supported": 60.0,
        "record_years": 120.0,
        "resolution_arcsec": 150.0,
        "cell_km_ns": 4.64,
        "cell_km_ew": 3.73,
        "hazard_source": "t",
        "boundary_source": "t",
        "use_tier": "I",
        "use_tier_reason": "t",
    },
    "pieces": [
        _piece(0, "B", "east", 127.05, {"10": 0.0, "50": 2.0}),
        _piece(1, "A", "west", 127.0, {"10": None, "50": 0.5}),
        _piece(2, "A", "west", 127.1, {"10": 0.2, "50": 0.9}),
    ],
}

METRIC = {
    "meta": {
        "layer": "b_metric",
        "peril": "heatwave",
        "key_kind": "metric",
        "default_key": "k2",
        "keys": [
            {"key": "k1", "quantity": "heat_days"},
            {"key": "k2", "quantity": "heat_days"},
        ],
        "units": "days",
        "resolution_arcsec": 180.0,
        "cell_km_ns": 5.5,
        "cell_km_ew": 4.4,
        "hazard_source": "t",
        "boundary_source": "t",
        "use_tier": "I",
        "use_tier_reason": "t",
    },
    "pieces": [
        _piece(0, "A", "west", 127.0, {"k1": 1.0, "k2": 3.0}),
        _piece(1, "C", "north", 127.2, {"k1": None, "k2": None}),
    ],
}


@pytest.fixture
def board_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "a_rp.board.json").write_text(json.dumps(RP), encoding="utf-8")
    (tmp_path / "b_metric.board.json").write_text(json.dumps(METRIC), encoding="utf-8")
    monkeypatch.setattr(store, "board_dir", lambda: tmp_path)
    return tmp_path


def test_regions_and_headline(board_dir: Path) -> None:
    rp = store.load_layer("a_rp")
    rows = store.regions(rp)
    assert [r["code"] for r in rows] == ["A", "B"]
    assert rows[0] == {"code": "A", "name": "west", "n_pieces": 2}
    assert store.headline_key(rp) == "50"  # 100 not kept -> largest kept
    m = store.load_layer("b_metric")
    assert store.headline_key(m) == "k2"


def test_headline_key_rules() -> None:
    rp100 = {"meta": {"periods_kept": [10.0, 100.0, 250.0]}, "pieces": []}
    assert store.headline_key(rp100) == "100"
    bad_default = {
        "meta": {"key_kind": "metric", "default_key": "zz", "keys": [{"key": "x"}, {"key": "y"}]},
        "pieces": [],
    }
    assert store.headline_key(bad_default) == "x"


def test_region_all(board_dir: Path) -> None:
    rows = store.region_all("A")
    assert [r["layer"] for r in rows] == ["a_rp", "b_metric"]
    a, b = rows
    assert a["label"] == "홍수 테스트" and b["label"] == "b_metric"
    assert a["headline_key"] == "50" and a["headline"] == {"min": 0.5, "max": 0.9}
    assert a["n_pieces"] == 2 and a["quantity"] == "water_depth_m" and a["peril"] == "river_flood"
    assert b["headline"] == {"min": 3.0, "max": 3.0} and b["quantity"] == "heat_days"
    c = store.region_all("C")
    assert len(c) == 1 and c[0]["headline"] is None  # all null stays None, not 0
    assert "mean" not in json.dumps(rows)
    with pytest.raises(KeyError):
        store.region_all("Z")


def test_region_routes(board_dir: Path, client: TestClient) -> None:
    r = client.get("/api/board/regions")
    assert r.status_code == 200
    assert r.json() == [
        {"code": "A", "name": "west"},
        {"code": "B", "name": "east"},
        {"code": "C", "name": "north"},
    ]
    one = client.get("/api/board/regions/A")
    assert one.status_code == 200
    body = one.json()
    assert body["code"] == "A" and body["name"] == "west" and len(body["layers"]) == 2
    assert client.get("/api/board/regions/Z").status_code == 404
    per = client.get("/api/board/layers/a_rp/regions")
    assert per.status_code == 200 and [x["code"] for x in per.json()] == ["A", "B"]


def test_resolution_ladder_config() -> None:
    cfg = json.loads((ROOT / "assets/libraries/visual_board.json").read_text(encoding="utf-8"))
    ladder = cfg["resolution_ladder"]
    assert [r["id"] for r in ladder] == ["national", "sigungu", "point", "case"]
    assert all(r["label_ko"] and r["label_en"] and r["note_ko"] for r in ladder)


def test_representative_point_copy() -> None:
    dc = pytest.importorskip("climaterisk.physical_risk.display_copy")
    assert "시각 보드" in dc.BOARD_POINTER and "평균" in dc.BOARD_POINTER
    doc = (ROOT / "docs/municipality-physical-risk.md").read_text(encoding="utf-8")
    sec = doc.split("\n## 11.", 1)
    assert len(sec) == 2, "section ## 11. missing"
    body = sec[1]
    assert "조각" in body and "시각 보드" in body and "대표점" in body
