"""No-damage threshold report: the two baseline curves and the drawn thresholds.

The engine's flood curve (CLIMADA ``ImpfRiverFlood``, JRC Huizinga 2017) already has
``MDD = 0`` at 0 m and 0.05 m, so the sheet's 5 cm default reproduces it exactly. The
report's ``pure`` baseline drops that point (the published JRC table, linear from 0 m to
0.5 m); against it the 5 cm step removes exactly the damage below 5 cm.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")

import numpy as np  # noqa: E402


def _report():
    spec = importlib.util.spec_from_file_location(
        "no_damage_threshold_report", REPO / "scripts" / "no_damage_threshold_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _curve():
    from climaterisk_worker.physical_risk import registry

    return registry.flood_impact_function("Asia", "commercial")


def test_config_has_the_threshold_block() -> None:
    rep = _report()
    cfg = rep.threshold_config()
    assert cfg["depth_m"] == pytest.approx(0.05)
    assert cfg["enabled"] is False
    grid = rep.t_grid(cfg)
    assert grid[0] == pytest.approx(cfg["depth_m"])
    assert grid[-1] == pytest.approx(cfg["report_grid_max_m"])
    assert set(cfg["report_random"]["by_property_type"]) == set(cfg["report_property_types"])


def test_pure_jrc_drops_only_the_inserted_zero_point() -> None:
    f = _curve()
    p = _report().pure_jrc(f)
    assert 0.05 in list(f.intensity) and 0.05 not in list(p.intensity)
    assert p.intensity.size == f.intensity.size - 1
    # linear from (0, 0) to the first JRC damage at 0.5 m
    mdr_05 = float(f.calc_mdr(np.array([0.5]))[0])
    np.testing.assert_allclose(p.calc_mdr(np.array([0.03])), [mdr_05 * 0.03 / 0.5], rtol=1e-9)
    deep = np.array([0.5, 1.0, 3.0, 6.0, 12.0])
    np.testing.assert_allclose(p.calc_mdr(deep), f.calc_mdr(deep), rtol=1e-12)


def test_five_cm_step_is_the_climada_curve_and_cuts_the_pure_one() -> None:
    from climaterisk_worker.physical_risk import registry

    f = _curve()
    p = _report().pure_jrc(f)
    d = np.array([0.01, 0.03, 0.05, 0.08, 0.3, 1.5])
    # the engine's own step at T = 0.05 returns the CLIMADA curve untouched
    assert registry.thresholded_flood_impact_function(f, 0.05) is f
    # on the pure table the step zeroes d <= 0.05 and leaves the rest
    step = registry.thresholded_flood_impact_function(p, 0.05)
    want = np.where(d > 0.05, p.calc_mdr(d), 0.0)
    np.testing.assert_allclose(step.calc_mdr(d), want, atol=1e-6)
    # at or below 5 cm the stepped pure table and the CLIMADA curve are both zero
    np.testing.assert_allclose(step.calc_mdr(d[d <= 0.05]), f.calc_mdr(d[d <= 0.05]), atol=0)


def test_drawn_thresholds_follow_the_sheet_rule() -> None:
    rep = _report()
    cfg = rep.threshold_config()
    dist = cfg["report_random"]["by_property_type"]
    rng = np.random.default_rng(7)
    t = {k: np.array([rep.draw_threshold(k, dist, rng) for _ in range(4000)]) for k in dist}
    for k, v in t.items():
        assert np.all(v >= cfg["depth_m"]), k  # never below the sheet's default
    # industrial: a board installed with probability 0.5 lifts T to at least board_m
    ind = dist["industrial"]
    assert np.mean(t["industrial"] >= ind["board_m"]) >= ind["board_installed"] - 0.03
    # office: no wall; T is the default, the board, or the first floor when no opening
    assert set(np.round(t["office"], 6)) <= {0.05, 0.3, 0.6}
