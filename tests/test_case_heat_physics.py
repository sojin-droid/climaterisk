"""Analytical checks for the street-heat twin physics (SVF, shadow, sun, irradiance)."""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
ch = pytest.importorskip("climaterisk_worker.twin.case_heat")


def _canyon(height_m: float, width_cells: int, cell_m: float = 1.0) -> np.ndarray:
    """Two long N-S walls of ``height_m`` separated by ``width_cells`` open cells."""
    h = np.zeros((401, 80 + width_cells))
    h[:, 40 - 1 : 40] = height_m
    h[:, 40 + width_cells : 40 + width_cells + 1] = height_m
    return h


def test_svf_matches_infinite_canyon() -> None:
    # open cells 40..59 (W = 21 m from wall centre to wall centre, centre cell 49.5 -> use 49)
    h = _canyon(21.0, 20)
    svf = ch.sky_view_factor_grid(h, 1.0, 150.0)
    # canyon centre between wall cells 39 and 60 -> W = 21 m; cell 49/50 are the centre.
    expected = 1.0 / math.sqrt(1.0 + (2 * 21.0 / 21.0) ** 2)
    assert svf[200, 49:51].mean() == pytest.approx(expected, abs=0.04)
    assert svf[200, 0] > 0.9  # open ground far from the walls


def test_sunlit_mask_wall_shadow_length() -> None:
    h = np.zeros((60, 60))
    h[:, 30] = 10.0  # N-S wall, sun due west at 45 deg: shadow 10 m to the east
    lit = ch.sunlit_mask(h, 1.0, 45.0, 270.0, 100.0)
    assert not lit[30, 33] and not lit[30, 39]
    assert lit[30, 45] and lit[30, 20]


def test_solar_position_seoul_noon_summer() -> None:
    elev, az = ch.solar_position(37.5, 126.9, dt.datetime(2026, 7, 21, 12, 30))
    assert 70.0 < elev < 77.0  # 90 - 37.5 + 20.5 = 73
    assert 150.0 < az < 210.0
    elev_n, _ = ch.solar_position(37.5, 126.9, dt.datetime(2026, 7, 21, 0, 30))
    assert elev_n < 0


def test_clear_sky_irradiance_and_emissivity() -> None:
    g, d, dn = ch.clear_sky_irradiance(70.0, 0.2)
    assert 900 < g < 1100 and d == pytest.approx(0.2 * g)
    assert dn * math.sin(math.radians(70.0)) == pytest.approx(g - d)
    assert ch.clear_sky_irradiance(-5.0, 0.2) == (0.0, 0.0, 0.0)
    assert 0.7 < ch.sky_emissivity(32.0, 50.0) < 0.9


def test_mrt_sun_exceeds_shade() -> None:
    p = {
        "diffuse_fraction": 0.2,
        "abs_short": 0.7,
        "abs_long": 0.97,
        "emissivity_surface": 0.95,
        "wall_excess_c": 4.0,
    }
    one = np.ones((1, 1))
    args = (one * 32.0, one, None, one * 0.2, one * 14.0, one * 2.0, 70.0, 50.0, p)
    sun = ch.mean_radiant_temperature_grid(args[0], args[1], np.ones((1, 1), bool), *args[3:])
    shade = ch.mean_radiant_temperature_grid(args[0], args[1], np.zeros((1, 1), bool), *args[3:])
    assert sun[0, 0] - shade[0, 0] > 15.0 and 45.0 < sun[0, 0] < 75.0
