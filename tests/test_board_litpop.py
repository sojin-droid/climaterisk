"""Board step 7 gate: LitPop exposure weighting (option C, default off)
(reviewer-written; agents may not edit it).

Source: CLIMADA Data API ``LitPop_150arcsec_KOR`` v3 (exponents (1,1), fin_mode pc, USD),
read offline from CLIMADA's download cache. 150 arcsec, NOT 1 km: every text says so.

Hazard values stay the grid values. Each LitPop point is assigned to the board piece that
contains it (piece = hazard cell x 시군구, so the point reads exactly its cell's value); piece
and 시군구 amounts are SUMS, never means.

Main method (the result): CLIMADA ``ImpactCalc(exposures, impfset, hazard).impact()`` per
point ``eai_exp`` (event-based expected annual impact), with the same impact functions the
asset engine uses (``physical_risk.registry``: JRC Asia curve for river and coastal flood,
Eberenz regional TC function for KOR), summed per piece.
Sub check (diagnostic only, ``meta.litpop.check``): per piece, EAL re-estimated from the
layer's kept return-period intensities: loss_T = sum over the piece's points of
value x MDR(intensity_T), then ``eal_from_rp_losses``. Pieces whose relative difference
exceeds ``rel_tol`` count; share > ``warn_share`` -> ``warning`` true and a build-log
warning. Never shown as a second result.

Worker ``climaterisk_worker.board`` (pure numpy, no CLIMADA needed for these):
- ``assign_points(lon, lat, pieces)`` -> int array, index into ``pieces`` (layer-file piece
  dicts with ``polygons``) of the piece containing each point (even-odd rule, holes
  excluded), -1 when none.
- ``eal_from_rp_losses(periods, losses)`` -> float. With f_i = 1/T_i sorted ascending:
  $$ EAL = \\sum_i \\tfrac{1}{2}(L_i + L_{i+1})(f_{i+1} - f_i) + L(T_{max}) f_{min} $$
  (trapezoid between kept periods + rare tail at the largest loss; events more frequent than
  the shortest kept period contribute nothing). ASCII: trapezoid over 1/T, plus f_min * L_max.
- ``litpop_check(eal_main, eal_check, rel_tol, warn_share, min_eal)`` -> {"n_compared",
  "disagree_share", "warning"} over pieces with eal_main > min_eal.

Config ``assets/libraries/visual_board.json`` ``litpop``: ``dataset`` (names
LitPop_150arcsec_KOR), ``res_arcsec`` 150, ``default_on`` false, ``layers`` (only river
flood, tropical cyclone and coastal flood absolute layers — no change, metric or wildfire
layers), ``banner_ko`` (contains "150″" and "LitPop은 공개 모델 추정값(야간 조명 × 인구)"),
``size_legend_usd`` (increasing positive list), ``check`` {rel_tol, warn_share, min_eal}.

Built layer files (skipped when absent; read from the real data root directly): every
configured layer has pieces with ``exposure`` keys ``litpop_value_usd`` and
``litpop_eal_usd`` (>= 0) and ``meta.litpop`` with ``n_points``, ``n_assigned``,
``value_assigned_usd`` equal to the piece sum (rtol 1e-6), and ``check`` with ``warning``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
CFG = json.loads((ROOT / "assets/libraries/visual_board.json").read_text(encoding="utf-8"))


def _sq(x0: float, y0: float, s: float) -> list[list[float]]:
    return [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]


def test_config() -> None:
    lp = CFG["litpop"]
    assert "LitPop_150arcsec_KOR" in lp["dataset"] and lp["res_arcsec"] == 150
    assert lp["default_on"] is False
    assert "150″" in lp["banner_ko"]
    assert "LitPop은 공개 모델 추정값(야간 조명 × 인구)" in lp["banner_ko"]
    assert lp["layers"]
    for name in lp["layers"]:
        assert name in CFG["layers"], name
        assert name.startswith(("river_flood_", "tropical_cyclone_", "coastal_flood_")), name
        assert "change" not in name
    sizes = lp["size_legend_usd"]
    assert sizes and all(a > 0 for a in sizes) and sizes == sorted(sizes)
    ck = lp["check"]
    assert 0 < ck["rel_tol"] < 1 and 0 < ck["warn_share"] < 1 and ck["min_eal"] >= 0


def test_assign_points() -> None:
    board = pytest.importorskip("climaterisk_worker.board")
    pieces = [
        {"polygons": [[_sq(0.0, 0.0, 1.0), _sq(0.4, 0.4, 0.2)]]},  # square with a hole
        {"polygons": [[_sq(1.0, 0.0, 1.0)], [_sq(5.0, 5.0, 1.0)]]},  # multipolygon
    ]
    lon = np.array([0.1, 0.5, 1.5, 5.5, 9.0])
    lat = np.array([0.1, 0.5, 0.5, 5.5, 9.0])
    idx = np.asarray(board.assign_points(lon, lat, pieces))
    assert idx.tolist() == [0, -1, 1, 1, -1]


def test_eal_from_rp_losses() -> None:
    board = pytest.importorskip("climaterisk_worker.board")
    # constant loss c at every kept period -> c * f_max = c / T_min
    np.testing.assert_allclose(
        board.eal_from_rp_losses([10.0, 100.0, 1000.0], [5.0, 5.0, 5.0]), 0.5, rtol=1e-12
    )
    # two periods: trapezoid + tail = 0.5*(1+3)*(0.1-0.01) + 3*0.01
    np.testing.assert_allclose(
        board.eal_from_rp_losses([100.0, 10.0], [3.0, 1.0]), 0.21, rtol=1e-12
    )
    assert board.eal_from_rp_losses([10.0, 100.0], [0.0, 0.0]) == 0.0


def test_litpop_check() -> None:
    board = pytest.importorskip("climaterisk_worker.board")
    r = board.litpop_check(
        np.array([100.0, 100.0, 100.0, 0.0]), np.array([100.0, 110.0, 200.0, 50.0]), 0.25, 0.2, 0.0
    )
    assert r["n_compared"] == 3 and r["disagree_share"] == pytest.approx(1 / 3)
    assert r["warning"] is True
    ok = board.litpop_check(np.array([10.0, 10.0]), np.array([10.0, 11.0]), 0.25, 0.2, 0.0)
    assert ok["warning"] is False and ok["disagree_share"] == 0.0


BOARD = Path(os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk")))
BOARD = BOARD / "derived/board"


@pytest.mark.parametrize("name", CFG["litpop"]["layers"])
def test_built_layers(name: str) -> None:
    f = BOARD / f"{name}.board.json"
    if not f.exists():
        pytest.skip("layer not built here")
    data = json.loads(f.read_text(encoding="utf-8"))
    lp = data["meta"].get("litpop")
    assert lp is not None, f"{name}: meta.litpop missing (rebuild with LitPop)"
    total = 0.0
    for pc in data["pieces"]:
        ex = pc.get("exposure") or {}
        assert ex.get("litpop_value_usd", 0) >= 0 and ex.get("litpop_eal_usd", 0) >= 0
        total += ex.get("litpop_value_usd", 0.0)
    assert lp["n_assigned"] <= lp["n_points"]
    np.testing.assert_allclose(total, lp["value_assigned_usd"], rtol=1e-6)
    assert isinstance(lp["check"]["warning"], bool)
