"""The adaptation axis of the heat-mortality hazard: whose comfort band a future window uses.

A future window must not re-derive the minimum-mortality band from its own (warmer)
climatology — the fitted slope is ``b = 1.078 > 1``, so that makes warming *reduce* the
exceedance load. The band belongs to the baseline window; adaptation is an explicit absolute
shift in degC, matching the threshold-shift scenarios of Lee et al. (2019, IJERPH 16:1026).
See ``docs/HEAT_ADAPTATION_KR.md``.

CLIMADA-free: only the degree-day reduction is exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import heat_mortality as hm  # noqa: E402
from climaterisk_worker.eobs import SEASON_DAYS, SummerDailyMean  # noqa: E402

WARMING_C = 3.0


def _stack(
    offset_c: float = 0.0, n_cells: int = 6, n_years: int = 3, seed: int = 7
) -> SummerDailyMean:
    """A synthetic Jun-Sep stack: same weather in every call, shifted by ``offset_c``."""
    rng = np.random.default_rng(seed)
    season = 6.0 * np.sin(np.linspace(0.0, np.pi, SEASON_DAYS))  # peaks mid-season
    tmax = (
        28.0
        + offset_c
        + season[None, None, :]
        + rng.normal(0.0, 1.0, (n_cells, n_years, SEASON_DAYS))
    )
    return SummerDailyMean(
        lat=37.0 + 0.05 * np.arange(n_cells),
        lon=127.0 + 0.05 * np.arange(n_cells),
        years=np.arange(2000, 2000 + n_years),
        tmax=tmax,
        source="synthetic unit-test fixture",
    )


def _baseline() -> tuple[tuple[hm.RefCity, ...], np.ndarray]:
    cells, dd, _ = hm.grid_from_summer_tmax(_stack(), "KOR", tag="kma", land_mask=False)
    return cells, dd


def test_the_published_slope_is_below_one_to_one_with_climate() -> None:
    """Tobías et al. (2021): MMT rises 0.8 degC per degC — so warming always adds load."""
    _, b = hm.adaptation_fit()
    assert b == pytest.approx(0.8)
    assert b < 1.0


def test_a_fixed_band_makes_warming_increase_the_heat_load() -> None:
    base_cells, base_dd = _baseline()
    _, warm_dd, _ = hm.grid_from_summer_tmax(
        _stack(offset_c=WARMING_C), "KOR", tag="kma", land_mask=False, reference_band=base_cells
    )
    assert warm_dd.mean() > base_dd.mean()
    assert (warm_dd >= base_dd).all(), "a warmer season cannot lower any cell's exceedance"


def test_recomputing_the_threshold_can_at_most_cancel_the_warming() -> None:
    """The percentile construction caps full adaptation at 1:1 — never protective.

    A uniformly warmer distribution has its percentile shifted by the same amount, so
    recomputing the threshold on the future window leaves the load unchanged. Under the
    old fitted slope (1.078) the same operation *reduced* the load.
    """
    _, base_dd = _baseline()
    _, refit_dd, _ = hm.grid_from_summer_tmax(
        _stack(offset_c=WARMING_C), "KOR", tag="kma", land_mask=False
    )
    np.testing.assert_allclose(refit_dd, base_dd, rtol=0.0, atol=1e-9)


def test_an_equal_band_shift_cancels_the_warming_exactly() -> None:
    """Analytical check: ``max(0, (T+d) - (H+d)) == max(0, T-H)`` for any shift ``d``."""
    base_cells, base_dd = _baseline()
    _, shifted_dd, _ = hm.grid_from_summer_tmax(
        _stack(offset_c=WARMING_C),
        "KOR",
        tag="kma",
        land_mask=False,
        reference_band=base_cells,
        band_shift_c=WARMING_C,
    )
    np.testing.assert_allclose(shifted_dd, base_dd, rtol=0.0, atol=1e-9)


def test_the_shift_is_monotone_over_the_published_scenario_set() -> None:
    """Lee et al. (2019) report +1, +2, +3 degC against a 0 degC reference."""
    base_cells, _ = _baseline()
    warm = _stack(offset_c=WARMING_C)
    loads = [
        hm.grid_from_summer_tmax(
            warm, "KOR", tag="kma", land_mask=False, reference_band=base_cells, band_shift_c=s
        )[1].mean()
        for s in (0.0, 1.0, 2.0, 3.0)
    ]
    assert loads == sorted(loads, reverse=True), f"adaptation must not raise the load: {loads}"
    assert loads[0] > loads[-1]


def test_the_reference_band_is_reused_not_refitted() -> None:
    base_cells, _ = _baseline()
    warm_cells, _, _ = hm.grid_from_summer_tmax(
        _stack(offset_c=WARMING_C),
        "KOR",
        tag="kma",
        land_mask=False,
        reference_band=base_cells,
        band_shift_c=1.5,
    )
    for base, warm in zip(base_cells, warm_cells, strict=True):
        assert warm.mmt_high == pytest.approx(base.mmt_high + 1.5)
        # the window still reports its own climatology, only the band is inherited
        assert warm.tmax_jja_mean > base.tmax_jja_mean


def test_a_mismatched_reference_band_is_refused() -> None:
    base_cells, _ = _baseline()
    with pytest.raises(ValueError, match="cells but this window has"):
        hm.grid_from_summer_tmax(
            _stack(offset_c=WARMING_C, n_cells=4),
            "KOR",
            tag="kma",
            land_mask=False,
            reference_band=base_cells,
        )
    moved = tuple(
        hm.RefCity(**{**c.__dict__, "lat": c.lat + 0.5}) for c in base_cells
    )  # same count, wrong places
    with pytest.raises(ValueError, match="do not line up"):
        hm.grid_from_summer_tmax(
            _stack(offset_c=WARMING_C), "KOR", tag="kma", land_mask=False, reference_band=moved
        )


def test_register_window_refuses_a_future_window_without_a_baseline() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("heat_korea", REPO / "scripts" / "heat_korea.py")
    assert spec is not None and spec.loader is not None
    hk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hk)
    with pytest.raises(ValueError, match="needs reference_cells"):
        hk.register_window("rcp45", 2050, 2041, 2060, 5, REPO / "data" / "hazard_db")
