"""Gate tests for heat phase 2 — future open-field UTCI (design 2026-11-16 §4.1, §6).

Written before the implementation (Opus gate). The implementation lives in
``worker/climaterisk_worker/heat_utci.py`` and the adapter in
``worker/climaterisk_worker/physical_risk/adapters.py``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("pythermalcomfort")

import numpy as np  # noqa: E402
from climaterisk_worker import heat_utci as hu  # noqa: E402
from climaterisk_worker.physical_risk import adapters  # noqa: E402
from numpy.testing import assert_allclose  # noqa: E402


def _config() -> dict:
    path = ROOT / "assets" / "libraries" / "physical_risk_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _hours(*months: int) -> np.ndarray:
    """Two hours in each given month of 2001 (KST datetime64[h])."""
    out = []
    for m in months:
        out += [
            np.datetime64(f"2001-{m:02d}-10T12", "h"),
            np.datetime64(f"2001-{m:02d}-11T03", "h"),
        ]
    return np.array(out)


# --------------------------------------------------------------------------- #
# monthly_delta                                                                 #
# --------------------------------------------------------------------------- #
def test_monthly_delta_is_future_minus_base_monthly_mean() -> None:
    # 2 x 2 grid; base: May 20, Jun 25 everywhere; future: May 21, Jun 27
    base = np.concatenate([np.full((3, 2, 2), 20.0), np.full((3, 2, 2), 25.0)])
    fut = np.concatenate([np.full((3, 2, 2), 21.0), np.full((3, 2, 2), 27.0)])
    months = np.array([5, 5, 5, 6, 6, 6])
    d = hu.monthly_delta(base, months, fut, months, [5, 6])
    assert d.shape == (2, 2, 2)
    assert_allclose(d[0], 1.0, atol=1e-12)
    assert_allclose(d[1], 2.0, atol=1e-12)


def test_monthly_delta_missing_cell_is_nan_not_zero() -> None:
    base = np.full((2, 1, 2), 20.0)
    fut = np.full((2, 1, 2), 22.0)
    base[:, 0, 1] = np.nan  # sea cell in the base period
    months = np.array([7, 7])
    d = hu.monthly_delta(base, months, fut, months, [7])
    assert d[0, 0, 0] == pytest.approx(2.0)
    assert np.isnan(d[0, 0, 1])


def test_monthly_delta_rejects_mismatched_shapes() -> None:
    with pytest.raises(ValueError):
        hu.monthly_delta(
            np.zeros((2, 2, 2)), np.array([5, 5]), np.zeros((2, 3, 2)), np.array([5, 5]), [5]
        )


# --------------------------------------------------------------------------- #
# apply_future                                                                  #
# --------------------------------------------------------------------------- #
def test_apply_future_adds_the_delta_of_each_hour_month() -> None:
    t = _hours(5, 6)
    ta = np.array([20.0, 15.0, 30.0, 22.0])
    out = hu.apply_future(ta, t, {5: 1.0, 6: 2.5})
    assert_allclose(out, [21.0, 16.0, 32.5, 24.5])


def test_apply_future_constant_delta_shifts_every_hour() -> None:
    t = _hours(5, 6, 7, 8, 9)
    ta = np.linspace(10, 30, t.size)
    out = hu.apply_future(ta, t, dict.fromkeys(range(5, 10), 1.7))
    assert_allclose(out - ta, 1.7, atol=1e-12)


def test_apply_future_missing_month_delta_gives_nan_hours_not_unchanged() -> None:
    t = _hours(5, 6)
    out = hu.apply_future(np.array([20.0, 20.0, 20.0, 20.0]), t, {5: 1.0, 6: float("nan")})
    assert_allclose(out[:2], 21.0)
    assert np.isnan(out[2:]).all()
    out = hu.apply_future(np.array([20.0, 20.0, 20.0, 20.0]), t, {5: 1.0})  # June absent
    assert np.isnan(out[2:]).all()


def test_apply_future_keeps_missing_hours_missing() -> None:
    t = _hours(7)
    out = hu.apply_future(np.array([np.nan, 25.0]), t, {7: 1.0})
    assert np.isnan(out[0]) and out[1] == pytest.approx(26.0)


# --------------------------------------------------------------------------- #
# scenario_window                                                               #
# --------------------------------------------------------------------------- #
FUT = {
    "scenario_keys": {"rcp45": "ssp245", "rcp85": "ssp585"},
    "base_years": [2021, 2030],
    "future_years": [2051, 2060],
    "min_target_year": 2040,
    "caches": {
        "ssp245": [
            "heat_days/ssp245_2021_2030_m5-9_b0.05.npz",
            "heat_days/ssp245_2051_2060_m5-9_b0.05.npz",
        ],
        "ssp585": [
            "heat_days/ssp585_2021_2030_m5-9_b0.05.npz",
            "heat_days/ssp585_2051_2060_m5-9_b0.05.npz",
        ],
    },
}


def test_scenario_window_rcp85_2050_is_ssp585_2051_2060() -> None:
    w = hu.scenario_window("rcp85", 2050, FUT)
    assert w["served_scenario"] == "ssp585"
    assert w["window"] == [2051, 2060]
    assert w["reason"] is None


def test_scenario_window_rcp45_2040_is_ssp245() -> None:
    assert hu.scenario_window("rcp45", 2040, FUT)["served_scenario"] == "ssp245"


@pytest.mark.parametrize(
    ("scenario", "year"),
    [("rcp26", 2050), ("rcp60", 2050), ("rcp85", 2030), ("historical", 2020), ("rcp85", 2039)],
)
def test_scenario_window_falls_back_to_historical_with_a_reason(scenario: str, year: int) -> None:
    w = hu.scenario_window(scenario, year, FUT)
    assert w["served_scenario"] == "historical"
    assert w["window"] is None
    if scenario != "historical":
        assert isinstance(w["reason"], str) and w["reason"]


def test_config_future_section() -> None:
    c = _config()["heat_utci"]
    f = c["future"]
    assert f["scenario_keys"] == {"rcp45": "ssp245", "rcp85": "ssp585"}
    assert f["base_years"] == [2021, 2030] and f["future_years"] == [2051, 2060]
    assert f["min_target_year"] == 2040
    assert set(f["caches"]) == {"ssp245", "ssp585"}


# --------------------------------------------------------------------------- #
# Adapter                                                                       #
# --------------------------------------------------------------------------- #
def test_adapter_describes_served_future() -> None:
    d = adapters.KoreaLocalUtciAdapter("rcp85", 2050).describe()
    assert d.served_scenario == "ssp585"
    assert "2051-2060" in d.time_horizon
    assert "A7" in d.detail  # humidity, wind and radiation held at present values


def test_adapter_describes_historical_fallback() -> None:
    d = adapters.KoreaLocalUtciAdapter("rcp26", 2050).describe()
    assert d.served_scenario == "historical"
    assert "2000-2019" in d.time_horizon


# --------------------------------------------------------------------------- #
# Real data (opt-in: CLIMATERISK_REAL_DATA_ROOT, default ~/Data/climaterisk)    #
# --------------------------------------------------------------------------- #
REAL_ROOT = Path(
    os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk"))
)


def _real_data() -> bool:
    kma = REAL_ROOT / "external" / "kma"
    cache = REAL_ROOT / "cache" / "heat_days"
    return (
        (kma / "asos_hourly" / "108").is_dir()
        and (cache / "obs_2000_2019_m5-9_b0.05.npz").is_file()
        and (cache / "ssp585_2051_2060_m5-9_b0.05.npz").is_file()
    )


real = pytest.mark.skipif(not _real_data(), reason="KMA data / future caches not on disk")
SEOUL = (37.5714, 126.9658)


@pytest.fixture(scope="module")
def seoul_future() -> dict:
    if not _real_data():
        pytest.skip("KMA data not on disk")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CLIMATERISK_DATA_ROOT", str(REAL_ROOT))
        mp.delenv("CLIMATERISK_KMA_DIR", raising=False)
        return hu.open_field_utci([SEOUL], _config()["heat_utci"], future="ssp585")


@real
def test_seoul_future_is_at_least_present(seoul_future: dict) -> None:
    a = seoul_future["per_asset"][0]
    assert a["future_scenario"] == "ssp585" and a["future_years"] == [2051, 2060]
    assert a["utci_days_ge38_future"] >= a["utci_days_ge38"]
    assert (
        a["utci_days_ge38_future_low"]
        <= a["utci_days_ge38_future"]
        <= a["utci_days_ge38_future_high"]
    )
    assert 0.0 < a["future_delta_t_mean_c"] < 6.0  # warming, 5ENSMN SSP5-8.5 May-Sep


@real
def test_seoul_present_numbers_unchanged_by_future_run(seoul_future: dict) -> None:
    want = json.loads(
        (ROOT / "tests" / "baselines" / "heat_utci_seoul108.json").read_text(encoding="utf-8")
    )
    assert seoul_future["per_asset"][0]["utci_days_ge38"] == pytest.approx(
        want["utci_days_ge38"], rel=1e-6
    )
