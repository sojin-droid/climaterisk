"""Visual board colour gate (reviewer-written; agents may not edit it).

``assets/libraries/board_palettes.json`` is the only colour authority for board maps and
legends (docs/BOARD_COLORS_KO.md). Colour = peril family, darkness = magnitude; class
breaks are fixed per quantity, never taken from the data on screen.

Python side, ``climaterisk.board.palette``:
- ``load_palettes()`` -> the spec dict (the JSON file as is).
- ``classes(quantity)`` -> list of ``{"up_to": float | None, "color", "label"}`` in class
  order (``up_to`` None for the open last class; spei_stage: driest first, then "없음").
- ``color(quantity, value)`` -> hex colour by ``_meta.class_rule``; ``quantity="change"``
  colours a ratio with the change palette. Unknown quantity -> ``KeyError``.
Store/API:
- every layer meta names its quantity: return-period layers ``meta.quantity``; metric
  layers ``meta.keys[i].quantity``. ``store.key_quantity(layer, key)`` returns it and raises
  ``ValueError`` when the file does not name one (no silent default from units or scale).
- ``GET /api/board/palettes`` returns the spec unchanged.
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

palette = pytest.importorskip("climaterisk.board.palette")
store = pytest.importorskip("climaterisk.board.store")

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "assets/libraries/board_palettes.json").read_text(encoding="utf-8"))


def _oklab(h: str) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(h[i : i + 2], 16)) for i in (1, 3, 5))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _de(a: str, b: str) -> float:
    return 100 * math.dist(_oklab(a), _oklab(b))


FAM = SPEC["families"]


def test_ramps_light_to_dark_and_families_apart() -> None:
    for name, fam in FAM.items():
        L = [_oklab(c)[0] for c in fam["ramp"]]
        assert all(a > b for a, b in zip(L, L[1:])), name
        assert _de(SPEC["shared"]["zero"]["color"], fam["ramp"][0]) >= 8, name
    for a, b in itertools.combinations(FAM, 2):
        d = min(_de(FAM[a]["ramp"][i], FAM[b]["ramp"][i]) for i in (2, 3, 4))
        assert d >= 10, (a, b, round(d, 1))


def test_every_peril_has_a_family_and_quantities_are_well_formed() -> None:
    perils = json.loads((ROOT / "assets/libraries/perils.json").read_text(encoding="utf-8"))
    ids = [p["id"] if isinstance(p, dict) else p for p in perils.get("perils", perils)]
    for pid in ids:
        assert SPEC["perils"].get(pid) in FAM, pid
    for name, q in SPEC["quantities"].items():
        assert q["status"] in {"in_use", "provisional"} and q.get("basis"), name
        if "breaks" in q:
            br = q["breaks"]
            assert all(a < b for a, b in zip(br, br[1:])), name
            assert len(q["steps"]) == len(br) + 1, name
            assert q["steps"] == sorted(set(q["steps"])), name
            assert isinstance(q["zero_means_none"], bool), name
    ch = SPEC["change"]
    assert len(ch["colors"]) == len(ch["breaks"]) + 1 == len(ch["labels"])
    mid = ch["colors"][len(ch["colors"]) // 2]
    assert math.hypot(*_oklab(mid)[1:]) < 0.01  # neutral midpoint
    assert ch["breaks"][len(ch["breaks"]) // 2 - 1] < 1 < ch["breaks"][len(ch["breaks"]) // 2]


def test_color_rule() -> None:
    ramp = FAM["water"]["ramp"]
    null, zero = SPEC["shared"]["null"]["color"], SPEC["shared"]["zero"]["color"]
    assert palette.load_palettes() == SPEC
    assert palette.color("water_depth_m", None) == null
    assert palette.color("water_depth_m", float("nan")) == null
    assert palette.color("water_depth_m", 0.0) == zero
    assert palette.color("water_depth_m", 0.25) == ramp[0]  # upper bound inclusive
    assert palette.color("water_depth_m", 0.26) == ramp[1]
    assert palette.color("water_depth_m", 9.0) == ramp[5]
    # zero is not "none" for temperature: 0 °C is just the lowest class
    assert palette.color("tmax_degC", 0.0) == FAM["heat"]["ramp"][0]
    # steps pick ramp positions: share_of_years starts at step 1
    assert palette.color("share_of_years", 0.05) == FAM["dryness"]["ramp"][1]
    assert palette.color("share_of_years", 0.0) == zero
    dry = FAM["dryness"]["ramp"]
    assert palette.color("spei_stage", -2.11) == dry[5]
    assert palette.color("spei_stage", -1.7) == dry[3]
    assert palette.color("spei_stage", -1.0) == dry[1]
    assert palette.color("spei_stage", -0.3) == zero
    assert palette.color("change", 1.0) == SPEC["change"]["colors"][3]
    assert palette.color("change", 3.0) == SPEC["change"]["colors"][-1]
    with pytest.raises(KeyError):
        palette.color("nope", 1.0)
    cl = palette.classes("water_depth_m")
    assert [c["color"] for c in cl] == ramp and cl[-1]["up_to"] is None and cl[0]["up_to"] == 0.25
    st = palette.classes("spei_stage")
    assert [c["color"] for c in st] == [dry[5], dry[3], dry[1], zero]


def _sq(x0: float, y0: float, s: float) -> list[list[float]]:
    return [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]


def _piece(values: dict) -> dict:
    return {"id": 0, "centroid": 0, "code": "A", "name": "a", "polygons": [[_sq(127, 37, 0.05)]], "values": values}


BASE = {
    "units": "m",
    "resolution_arcsec": 150.0,
    "cell_km_ns": 4.64,
    "cell_km_ew": 3.73,
    "hazard_source": "t",
    "boundary_source": "t",
    "use_tier": "S",
    "use_tier_reason": "t",
}


@pytest.fixture
def board_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    rp = {"meta": {**BASE, "layer": "rp_q", "quantity": "water_depth_m", "periods_kept": [10.0]}, "pieces": [_piece({"10": 1.0})]}
    rp_bare = {"meta": {**BASE, "layer": "rp_bare", "periods_kept": [10.0]}, "pieces": [_piece({"10": 1.0})]}
    keys = [
        {"key": "freq_ge2", "label": "f", "units": "연 비율", "scale": "share", "quantity": "share_of_years"},
        {"key": "y2022", "label": "y", "units": "-", "scale": "spei", "quantity": "spei_stage"},
    ]
    mt = {"meta": {**BASE, "layer": "mt_q", "key_kind": "metric", "keys": keys}, "pieces": [_piece({"freq_ge2": 0.2, "y2022": -2.1})]}
    for d in (rp, rp_bare, mt):
        (tmp_path / f"{d['meta']['layer']}.board.json").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(store, "board_dir", lambda: tmp_path)
    return tmp_path


def test_store_names_the_quantity(board_dir: Path, client: TestClient) -> None:
    assert store.key_quantity(store.load_layer("rp_q"), 10) == "water_depth_m"
    assert store.key_quantity(store.load_layer("mt_q"), "y2022") == "spei_stage"
    assert store.key_quantity(store.load_layer("mt_q"), "freq_ge2") == "share_of_years"
    with pytest.raises(ValueError):
        store.key_quantity(store.load_layer("rp_bare"), 10)
    resp = client.get("/api/board/palettes")
    assert resp.status_code == 200 and resp.json() == SPEC


@pytest.mark.skipif(not store.board_dir().exists(), reason="no exported board layers on this machine")
def test_exported_layers_name_known_quantities() -> None:
    for meta in store.list_layers():
        layer = store.load_layer(meta["layer"])
        for k in store.layer_keys(layer):
            assert store.key_quantity(layer, k) in SPEC["quantities"], (meta["layer"], k)
