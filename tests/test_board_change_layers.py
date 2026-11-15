"""Visual board step A1/A2 gate (reviewer-written; agents may not edit it).

Future-change layers (river flood, tropical cyclone) and the return-period sub check.

Worker (``climaterisk_worker.board``):
- ``rp_intensity_check(hazard, return_periods)`` -> same dict shape as ``rp_intensity``
  (same kept periods, cap, record) but computed by the empirical step rule: for period $T$
  the intensity is the largest event intensity $I_e$ at the centroid whose exceedance
  frequency $F(I_e) = \\sum_{k: I_k \\ge I_e} f_k$ is $\\ge 1/T$; 0 if none. No
  interpolation. It is the sub check of the main CLIMADA method (``rp_intensity``).
- ``rp_disagreement(main, check, rel_tol)`` -> ``{period_key: share}``: per kept period, the
  share of centroids with main > 0 or check > 0 where |main - check| > rel_tol * max(main,
  check). (period_key as in ``values``: "100").
- ``change_values(base, future)`` -> ``(ratio, status)`` element-wise on equal-shape arrays:
  both 0 -> ratio NaN, status "both_zero"; base 0 and future > 0 -> NaN, "base_zero";
  either NaN -> NaN, "null"; else future / base, status "".
Config (``assets/libraries/visual_board.json``):
- every layer has ``quantity`` naming a palette quantity; tropical cyclone layers use
  ``wind_ms``.
- ``change_layers``: ``{name: {"base": layer, "future": layer, "label"}}``; both named
  layers exist, have the same peril; river flood: same ``climate_scenario`` and base
  ``year_range`` "2010_2030" (like-for-like, physical.py docstring); tropical cyclone: base
  ``climate_scenario`` "historical" with the same number of synthetic tracks as the future.
- ``rp_check``: ``{"rel_tol", "warn_share"}``.
Store: change layer files have ``meta.quantity == "change"``, ``meta.base_layer`` and
``meta.future_layer``; pieces may carry ``status: {key: str}``; GeoJSON feature properties
add ``status`` (the key's status string, or None).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
CFG = json.loads((ROOT / "assets/libraries/visual_board.json").read_text(encoding="utf-8"))
PAL = json.loads((ROOT / "assets/libraries/board_palettes.json").read_text(encoding="utf-8"))


def test_config_like_for_like() -> None:
    layers = CFG["layers"]
    for name, spec in layers.items():
        assert spec["quantity"] in PAL["quantities"], name
        if spec["peril"] == "tropical_cyclone":
            assert spec["quantity"] == "wind_ms", name
    assert CFG["change_layers"], "at least one change layer"
    perils = set()
    for name, ch in CFG["change_layers"].items():
        base, fut = layers[ch["base"]], layers[ch["future"]]
        assert base["peril"] == fut["peril"], name
        perils.add(base["peril"])
        assert ch["label"], name
        if base["peril"] == "river_flood":
            assert base["climate_scenario"] == fut["climate_scenario"], name
            assert base["year_range"] == "2010_2030", name
        if base["peril"] == "tropical_cyclone":
            assert base["climate_scenario"] == "historical", name
            assert base["nb_synth_tracks"] == fut["nb_synth_tracks"], name
    assert {"river_flood", "tropical_cyclone"} <= perils
    assert 0 < CFG["rp_check"]["rel_tol"] < 1 and 0 < CFG["rp_check"]["warn_share"] < 1


def test_change_values() -> None:
    board = pytest.importorskip("climaterisk_worker.board")
    base = np.array([0.0, 0.0, 2.0, np.nan, 1.0])
    fut = np.array([0.0, 1.0, 3.0, 1.0, 0.0])
    ratio, status = board.change_values(base, fut)
    assert list(status) == ["both_zero", "base_zero", "", "null", ""]
    assert np.isnan(ratio[:2]).all() and np.isnan(ratio[3])
    np.testing.assert_allclose(ratio[[2, 4]], [1.5, 0.0], rtol=0, atol=1e-12)


def _hazard(intensities: list[float], freq: float):  # type: ignore[no-untyped-def]
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    n = len(intensities)
    return Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=np.array([37.0]), lon=np.array([127.0])),
        event_id=np.arange(1, n + 1),
        frequency=np.full(n, freq),
        intensity=sparse.csr_matrix(np.array(intensities, dtype=float).reshape(n, 1)),
        fraction=sparse.csr_matrix(np.ones((n, 1))),
    )


def test_rp_check_analytical() -> None:
    pytest.importorskip("climada")
    board = pytest.importorskip("climaterisk_worker.board")
    haz = _hazard([1.0, 2.0, 3.0], 0.1)  # record 10 years -> cap 5 years
    chk = board.rp_intensity_check(haz, [2, 4, 5, 10])
    main = board.rp_intensity(haz, [2, 4, 5, 10])
    assert chk["periods"] == main["periods"] == [2, 4, 5]
    # F(3)=0.1, F(2)=0.2, F(1)=0.3: T=2 -> none >= 0.5 -> 0; T=4 -> 1; T=5 -> 2
    np.testing.assert_allclose(chk["intensity"][:, 0], [0.0, 1.0, 2.0], rtol=0, atol=1e-12)
    dis = board.rp_disagreement(main, chk, rel_tol=0.25)
    assert set(dis) == {"2", "4", "5"} and all(0.0 <= v <= 1.0 for v in dis.values())
    same = board.rp_disagreement(chk, chk, rel_tol=0.25)
    assert all(v == 0.0 for v in same.values())


def _sq(x0: float, y0: float, s: float) -> list[list[float]]:
    return [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]


def test_store_change_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = pytest.importorskip("climaterisk.board.store")
    meta = {
        "layer": "ch_test",
        "units": "ratio",
        "quantity": "change",
        "base_layer": "a",
        "future_layer": "b",
        "periods_kept": [10.0, 100.0],
        "resolution_arcsec": 150.0,
        "cell_km_ns": 4.64,
        "cell_km_ew": 3.73,
        "hazard_source": "t",
        "boundary_source": "t",
        "use_tier": "I",
        "use_tier_reason": "t",
    }
    piece = {
        "id": 0,
        "centroid": 0,
        "code": "A",
        "name": "a",
        "polygons": [[_sq(127, 37, 0.05)]],
        "values": {"10": None, "100": 1.4},
        "status": {"10": "base_zero"},
    }
    (tmp_path / "ch_test.board.json").write_text(
        json.dumps({"meta": meta, "pieces": [piece]}), encoding="utf-8"
    )
    monkeypatch.setattr(store, "board_dir", lambda: tmp_path)
    layer = store.load_layer("ch_test")
    assert store.key_quantity(layer, 10) == "change"
    f10 = store.geojson(layer, 10)["features"][0]["properties"]
    f100 = store.geojson(layer, 100)["features"][0]["properties"]
    assert f10["value"] is None and f10["status"] == "base_zero"
    assert f100["value"] == 1.4 and f100["status"] is None
