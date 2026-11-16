"""Gate tests: heat T2 = open-field UTCI from KMA ASOS hourly observations.

Design: ``<DATA_ROOT>/derived/design_utci/DESIGN_UTCI_T2.md`` (2026-11-15), §12.5, with
these changes found while writing the gate:

* fp (projected-area factor) takes the solar elevation in **degrees** and the whole cosine
  argument is converted to radians, exactly as ECMWF ``thermofeel``
  (``fp = 0.308 * cos(to_radians * gamma * (0.998 - gamma**2 / 50000))``). thermofeel returns
  MRT in Kelvin; this module returns °C.
* Station coordinates and anemometer heights come from the KMA 관측지점정보 history table
  (``asos_meta/kma_asos_stations_history.csv``, 148 rows), not the NOAA ISD table, which put
  127 and 104 80–100 km away from their real sites.
* ``mean_radiant_temperature_open`` takes the already split irradiance and the cloud fraction
  as arrays, so each step is testable on its own; ``open_field_utci`` composes them.

Verified coefficients (2026-11-15, source code read verbatim):
* Erbs, Klein, Duffie 1982 (Solar Energy 28(4):293–302, eq. 1) — pvlib ``irradiance.erbs``:
  ``kt <= 0.22: 1 - 0.09 kt``; ``0.22 < kt <= 0.8: 0.9511 - 0.1604 kt + 4.388 kt² - 16.638 kt³
  + 12.336 kt⁴``; ``kt > 0.8: 0.165``.
* Di Napoli et al. 2020 MRT — thermofeel ``calculate_mean_radiant_temperature``: weights 0.5,
  0.5, ratio 0.7/0.97, fp as above.
Not verified against the original: Crawford & Duchon 1999 cloud fraction ``1 - GHI/GHI_clear``
[미확인], Oke 1987 albedo range [미확인].

These run in the CLIMADA worker environment (pythermalcomfort 4.8.0 lives there).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("pythermalcomfort")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from climaterisk_worker import heat_utci as hu  # noqa: E402
from climaterisk_worker.physical_risk import adapters, engine  # noqa: E402
from climaterisk_worker.twin import case_heat  # noqa: E402
from numpy.testing import assert_allclose  # noqa: E402

from climaterisk import paths  # noqa: E402
from climaterisk.physical_risk import models  # noqa: E402

SIGMA = 5.670374419e-8


# =========================================================================== #
# 1. Pure physics — analytical or hand-computed values                         #
# =========================================================================== #
def test_saturation_vapour_pressure_magnus() -> None:
    assert hu.saturation_vapour_pressure(0.0) == pytest.approx(6.112, rel=1e-12)
    expected = 6.112 * math.exp(17.62 * 20.0 / (243.12 + 20.0))
    assert_allclose(hu.saturation_vapour_pressure(np.array([20.0])), [expected], rtol=1e-12)


def test_shift_air_temperature_identity_when_no_delta() -> None:
    ta, rh = np.array([25.0, 31.0]), np.array([60.0, 80.0])
    t2, r2 = hu.shift_air_temperature(ta, rh, 0.0)
    assert_allclose(t2, ta, rtol=0, atol=1e-12)
    assert_allclose(r2, rh, rtol=0, atol=1e-12)


def test_shift_air_temperature_keeps_vapour_pressure() -> None:
    t2, r2 = hu.shift_air_temperature(np.array([25.0]), np.array([60.0]), 2.0)
    assert_allclose(t2, [27.0], atol=1e-12)
    e_before = 0.60 * hu.saturation_vapour_pressure(25.0)
    e_after = r2[0] / 100.0 * hu.saturation_vapour_pressure(27.0)
    assert e_after == pytest.approx(e_before, rel=1e-12)


def test_shift_air_temperature_caps_humidity_at_100() -> None:
    _, r2 = hu.shift_air_temperature(np.array([25.0]), np.array([95.0]), -5.0)
    assert r2[0] == pytest.approx(100.0)


def test_shift_air_temperature_per_hour_delta_array() -> None:
    t2, _ = hu.shift_air_temperature(
        np.array([20.0, 21.0]), np.array([50.0, 50.0]), np.array([1.0, -1.0])
    )
    assert_allclose(t2, [21.0, 20.0], atol=1e-12)


def test_wind_to_10m_log_profile() -> None:
    assert hu.wind_to_10m(np.array([3.0]), 10.0)[0] == pytest.approx(3.0, rel=1e-12)
    # ln(10/0.01) / ln(20/0.01) = ln(1000) / ln(2000) = 0.908807...
    assert hu.wind_to_10m(np.array([1.0]), 20.0)[0] == pytest.approx(
        math.log(1000.0) / math.log(2000.0), rel=1e-12
    )
    assert hu.wind_to_10m(np.array([1.0]), 20.0)[0] == pytest.approx(0.9088, abs=1e-4)


def test_clip_wind_to_utci_range_and_reports_mask() -> None:
    v, clipped = hu.clip_wind(np.array([0.2, 5.0, 20.0]))
    assert_allclose(v, [0.5, 5.0, 17.0])
    assert clipped.tolist() == [True, False, True]


def test_solar_sin_elevation_matches_case_heat() -> None:
    when = dt.datetime(2018, 8, 1, 12, 30)
    elev_deg, _ = case_heat.solar_position(37.5714, 126.9658, when)
    got = hu.solar_sin_elevation(37.5714, 126.9658, np.array([np.datetime64(when)]))
    assert_allclose(got, [math.sin(math.radians(elev_deg))], rtol=0, atol=1e-12)


def test_solar_sin_elevation_negative_at_night() -> None:
    got = hu.solar_sin_elevation(37.5714, 126.9658, np.array([np.datetime64("2018-08-01T00:30")]))
    assert got[0] < 0


def test_extraterrestrial_normal() -> None:
    n = np.array([1, 183])
    expected = 1361.0 * (1.0 + 0.033 * np.cos(2.0 * np.pi * n / 365.0))
    assert_allclose(hu.extraterrestrial_normal(n), expected, rtol=1e-12)


def test_erbs_diffuse_fraction_values_and_breakpoints() -> None:
    def poly(k: float) -> float:
        return 0.9511 - 0.1604 * k + 4.388 * k**2 - 16.638 * k**3 + 12.336 * k**4

    kt = np.array([0.1, 0.22, 0.5, 0.8, 0.9])
    got = hu.erbs_diffuse_fraction(kt)
    assert_allclose(got, [0.991, 0.9802, poly(0.5), poly(0.8), 0.165], rtol=1e-12)
    # Breakpoints follow pvlib: 0.22 belongs to the linear branch, 0.8 to the polynomial.
    jump_022 = poly(0.22 + 1e-12) - (1 - 0.09 * 0.22)
    jump_080 = 0.165 - poly(0.8)
    assert abs(jump_022) < 0.01 and abs(jump_080) < 0.01  # recorded: the fit is near-continuous


def test_split_irradiance_daytime() -> None:
    ghi, sinb, i0 = np.array([600.0]), np.array([0.8]), np.array([1361.0])
    s_dif, i_dn = hu.split_irradiance(ghi, sinb, i0)
    kt = 600.0 / (1361.0 * 0.8)
    fd = float(hu.erbs_diffuse_fraction(np.array([kt]))[0])
    assert s_dif[0] == pytest.approx(fd * 600.0, rel=1e-12)
    assert i_dn[0] == pytest.approx((600.0 - fd * 600.0) / 0.8, rel=1e-12)


def test_split_irradiance_low_sun_is_all_diffuse_and_beam_is_capped() -> None:
    s_dif, i_dn = hu.split_irradiance(np.array([50.0]), np.array([0.03]), np.array([1361.0]))
    assert s_dif[0] == pytest.approx(50.0) and i_dn[0] == 0.0  # sin(beta) <= 0.05
    s_dif, i_dn = hu.split_irradiance(np.array([1000.0]), np.array([0.06]), np.array([1361.0]))
    assert i_dn[0] == pytest.approx(1361.0)  # (GHI - S_dif)/sin(beta) would exceed I0
    s_dif, i_dn = hu.split_irradiance(np.array([0.0]), np.array([-0.2]), np.array([1361.0]))
    assert s_dif[0] == 0.0 and i_dn[0] == 0.0


def test_projected_area_factor_degrees() -> None:
    got = hu.projected_area_factor(np.array([0.0, 90.0, 45.0]))
    expected_45 = 0.308 * math.cos(math.radians(45.0 * (0.998 - 45.0**2 / 50000.0)))
    assert_allclose(got, [0.308, 0.0785, expected_45], atol=1e-4)
    assert got[1] == pytest.approx(
        0.308 * math.cos(math.radians(90 * (0.998 - 8100 / 50000))), rel=1e-12
    )


def test_clear_sky_ghi_matches_case_heat() -> None:
    sinb = math.sin(math.radians(60.0))
    g, _, _ = case_heat.clear_sky_irradiance(60.0, 0.0)
    assert hu.clear_sky_ghi(np.array([sinb]))[0] == pytest.approx(g, rel=1e-12)
    assert hu.clear_sky_ghi(np.array([-0.1]))[0] == 0.0


def test_cloud_fraction_daytime_and_night_fill() -> None:
    ghi = np.array([0.0, 500.0, 900.0, 0.0, 0.0, 1200.0, 0.0])
    clear = np.array([0.0, 1000.0, 1000.0, 0.0, 0.0, 1000.0, 0.0])
    sinb = np.array([-0.2, 0.5, 0.7, -0.3, -0.2, 0.6, -0.4])
    day = np.array([0, 0, 0, 0, 1, 1, 1])
    c = hu.cloud_fraction(ghi, clear, sinb, day)
    # day 0: daytime 0.5 and 0.1 -> night hours take the mean 0.3; day 1: GHI > clear -> 0
    assert_allclose(c, [0.3, 0.5, 0.1, 0.3, 0.0, 0.0, 0.0], atol=1e-12)


def test_sky_emissivity_cloud_limits() -> None:
    ta, rh = 30.0, 60.0
    e = rh / 100.0 * hu.saturation_vapour_pressure(ta)
    clear = hu.sky_emissivity(np.array([ta]), np.array([e]), np.array([0.0]))[0]
    assert clear == pytest.approx(case_heat.sky_emissivity(ta, rh), rel=1e-12)  # Prata 1996
    assert hu.sky_emissivity(np.array([ta]), np.array([e]), np.array([1.0]))[0] == pytest.approx(
        1.0
    )
    half = hu.sky_emissivity(np.array([ta]), np.array([e]), np.array([0.5]))[0]
    assert half == pytest.approx(0.5 + 0.5 * clear, rel=1e-12)


def test_fit_ground_excess_recovers_synthetic_line() -> None:
    rng = np.random.default_rng(7)
    ghi = rng.uniform(0, 900, 2000)
    ta = rng.uniform(15, 35, 2000)
    ts = ta + 1.0 + 0.02 * ghi
    ts[:50] = np.nan  # missing observations are ignored
    fit = hu.fit_ground_excess(ts, ta, ghi, min_hours=1000)
    assert fit["a"] == pytest.approx(1.0, abs=1e-9)
    assert fit["b"] == pytest.approx(0.02, abs=1e-12)
    assert fit["n"] == 1950
    assert fit["rmse"] == pytest.approx(0.0, abs=1e-9)


def test_fit_ground_excess_too_few_hours_is_none() -> None:
    x = np.ones(10)
    assert hu.fit_ground_excess(x, x, x, min_hours=1000) is None


def _mrt(**over: object) -> np.ndarray:
    kw: dict[str, object] = {
        "ta_c": np.array([30.0]),
        "e_hpa": np.array([20.0]),
        "ghi": np.array([0.0]),
        "sin_beta": np.array([-0.1]),
        "s_dif": np.array([0.0]),
        "i_dn": np.array([0.0]),
        "cloud": np.array([1.0]),
        "ground_fit": {"a": 0.0, "b": 0.0},
        "albedo": 0.2,
        "eps_ground": 1.0,
    }
    kw.update(over)
    return hu.mean_radiant_temperature_open(**kw)  # type: ignore[arg-type]


def test_mrt_equals_air_temperature_for_black_sky_and_ground_at_night() -> None:
    assert_allclose(_mrt(), [30.0], rtol=0, atol=1e-9)


def test_mrt_daytime_matches_hand_formula() -> None:
    ta, e, ghi, sinb, s_dif, i_dn, cloud = 30.0, 20.0, 800.0, 0.8, 200.0, 750.0, 0.0
    a_r, b_r, alb, eps_g = -1.0, 0.02, 0.2, 0.95
    got = _mrt(
        ta_c=np.array([ta]),
        e_hpa=np.array([e]),
        ghi=np.array([ghi]),
        sin_beta=np.array([sinb]),
        s_dif=np.array([s_dif]),
        i_dn=np.array([i_dn]),
        cloud=np.array([cloud]),
        ground_fit={"a": a_r, "b": b_r},
        albedo=alb,
        eps_ground=eps_g,
    )[0]
    tk = ta + 273.15
    w = 46.5 * e / tk
    eps_sky = 1 - (1 + w) * math.exp(-math.sqrt(1.2 + 3 * w))
    ldn = eps_sky * SIGMA * tk**4
    tg = ta + a_r + b_r * ghi
    lup = eps_g * SIGMA * (tg + 273.15) ** 4 + (1 - eps_g) * ldn
    gamma = math.degrees(math.asin(sinb))
    fp = 0.308 * math.cos(math.radians(gamma * (0.998 - gamma**2 / 50000)))
    s_up = alb * ghi
    flux = 0.5 * ldn + 0.5 * lup + (0.7 / 0.97) * (0.5 * s_dif + 0.5 * s_up + fp * i_dn)
    expected = (flux / SIGMA) ** 0.25 - 273.15
    assert got == pytest.approx(expected, rel=1e-10)
    assert got > ta + 20  # full sun on open ground


def test_mrt_rises_with_albedo_in_sun() -> None:
    sun = {
        "ghi": np.array([800.0]),
        "sin_beta": np.array([0.8]),
        "s_dif": np.array([200.0]),
        "i_dn": np.array([750.0]),
        "cloud": np.array([0.0]),
    }
    lo, mid, hi = (_mrt(albedo=a, **sun)[0] for a in (0.15, 0.20, 0.25))
    assert lo < mid < hi


def test_utci_reference_environment_captured() -> None:
    """Captured from pythermalcomfort 4.8.0 (Tmrt = Ta, v = 0.5 m/s, RH 50 %)."""
    got = hu.utci_hourly(
        np.array([20.0, 30.0]), np.array([20.0, 30.0]), np.array([0.5, 0.5]), np.array([50.0, 50.0])
    )
    assert_allclose(got, [19.8476, 30.4094], atol=1e-3)


def test_utci_out_of_range_wind_is_nan_until_clipped() -> None:
    raw = hu.utci_hourly(np.array([30.0]), np.array([30.0]), np.array([0.3]), np.array([50.0]))
    assert np.isnan(raw[0])
    v, _ = hu.clip_wind(np.array([0.3]))
    assert np.isfinite(hu.utci_hourly(np.array([30.0]), np.array([30.0]), v, np.array([50.0]))[0])


# =========================================================================== #
# 2. Annual metrics and the missing-data rule                                  #
# =========================================================================== #
def _july_hours(year: int) -> np.ndarray:
    start = np.datetime64(f"{year}-07-01T00", "h")
    return start + np.arange(31 * 24).astype("timedelta64[h]")


def _three_year_series() -> tuple[np.ndarray, np.ndarray]:
    t = np.concatenate([_july_hours(y) for y in (2001, 2002, 2003)])
    u = np.full(t.size, np.nan)
    n = 31 * 24
    y1 = np.full(n, 30.0)
    y1[13:16] = 40.0  # day 1: three hours at 40
    y1[24 + 14] = 38.0  # day 2: one hour exactly at 38 (>= counts)
    y2 = np.full(n, 25.0)
    for d in range(5):
        y2[d * 24 + 15] = 33.0  # five days with one hour at 33
    y3 = np.full(n, 35.0)
    y3[: int(0.1 * n)] = np.nan  # 10 % missing -> invalid year
    u[:n], u[n : 2 * n], u[2 * n :] = y1, y2, y3
    return u, t


def test_annual_metrics_hand_series() -> None:
    u, t = _three_year_series()
    m = hu.annual_metrics(
        u,
        t,
        years=(2001, 2003),
        months=[7],
        thresholds_c=[32, 38, 46],
        headline_c=38,
        min_hour_fraction=0.95,
        min_valid_years=2,
    )
    assert m["n_valid_years"] == 2
    assert m["utci_days_ge38"] == pytest.approx(1.0)  # (2 + 0) / 2
    assert m["utci_hours_ge38"] == pytest.approx(2.0)  # (4 + 0) / 2
    assert m["utci_days_ge32"] == pytest.approx(3.5)  # (2 + 5) / 2
    assert m["utci_hours_ge32"] == pytest.approx(4.5)  # (4 + 5) / 2
    assert m["utci_days_ge46"] == 0.0
    assert m["utci_annual_max_mean"] == pytest.approx(36.5)
    assert m["utci_days_ge38_by_year"] == {2001: 2, 2002: 0, 2003: None}


def test_annual_metrics_too_few_valid_years_is_none_not_zero() -> None:
    u, t = _three_year_series()
    m = hu.annual_metrics(
        u,
        t,
        years=(2001, 2003),
        months=[7],
        thresholds_c=[32, 38, 46],
        headline_c=38,
        min_hour_fraction=0.95,
        min_valid_years=3,
    )
    assert m["utci_days_ge38"] is None and m["utci_annual_max_mean"] is None
    assert m["n_valid_years"] == 2


def test_annual_metrics_zero_days_is_zero() -> None:
    t = np.concatenate([_july_hours(y) for y in (2001, 2002)])
    m = hu.annual_metrics(
        np.full(t.size, 20.0),
        t,
        years=(2001, 2002),
        months=[7],
        thresholds_c=[38],
        headline_c=38,
        min_hour_fraction=0.95,
        min_valid_years=2,
    )
    assert m["utci_days_ge38"] == 0.0


def test_annual_metrics_missing_year_entirely_is_invalid() -> None:
    t = _july_hours(2001)  # 2002 has no rows at all (e.g. an empty file)
    m = hu.annual_metrics(
        np.full(t.size, 40.0),
        t,
        years=(2001, 2002),
        months=[7],
        thresholds_c=[38],
        headline_c=38,
        min_hour_fraction=0.95,
        min_valid_years=1,
    )
    assert m["utci_days_ge38_by_year"] == {2001: 31, 2002: None}
    assert m["utci_days_ge38"] == pytest.approx(31.0)


def test_heat_days_from_hourly() -> None:
    t = np.concatenate([_july_hours(y) for y in (2001, 2002)])
    ta = np.full(t.size, 30.0)
    ta[15] = 33.0  # 2001 day 1
    ta[31 * 24 + 15] = 34.0  # 2002 day 1
    ta[31 * 24 + 24 + 15] = 35.0  # 2002 day 2
    out = hu.heat_days_from_hourly(
        ta, t, years=(2001, 2002), months=[7], threshold_c=33.0, min_hour_fraction=0.95
    )
    assert out["by_year"] == {2001: 1, 2002: 2}
    assert out["mean"] == pytest.approx(1.5)


# =========================================================================== #
# 3. Sub check (diagnostic only)                                               #
# =========================================================================== #
CHECK = {
    "min_spearman_years": 0.5,
    "warn_rel_heat_days": 0.3,
    "warn_abs_heat_days": 2.0,
    "max_station_km": 50.0,
    "max_radiation_station_km": 75.0,
    "min_assets_for_rank": 10,
    "min_spearman_assets": 0.5,
}


def _asset(**over: object) -> dict:
    a: dict = {
        "utci_days_ge38": 15.0,
        "utci_days_ge38_by_year": {2000 + i: float(i) for i in range(20)},
        "heat_days_grid_by_year": {2000 + i: float(2 * i) for i in range(20)},
        "heat_days_shift": 11.0,
        "heat_days_grid": 10.0,
        "station_distance_km": 10.0,
        "radiation_distance_km": 20.0,
    }
    a.update(over)
    return a


def test_asset_check_all_agree() -> None:
    c = hu.asset_check(_asset(), CHECK)
    assert c["C1_rho"] == pytest.approx(1.0)
    assert c["C1_ok"] is True and c["C3_ok"] is True
    assert "C2_ok" not in c  # C2 is per station, not in the asset stamp
    assert c["agree"] == "2/2" and c["warn"] is False


def test_asset_check_far_station_warns() -> None:
    c = hu.asset_check(_asset(station_distance_km=60.0), CHECK)
    assert c["C3_ok"] is False and c["agree"] == "1/2" and c["warn"] is True
    c = hu.asset_check(_asset(radiation_distance_km=80.0), CHECK)
    assert c["C3_ok"] is False


def test_asset_check_constant_series_cannot_be_checked() -> None:
    c = hu.asset_check(_asset(heat_days_grid_by_year={2000 + i: 0.0 for i in range(20)}), CHECK)
    assert c["C1_ok"] is None and c["agree"] == "1/1"


def test_asset_check_ignores_shift_vs_grid_gap() -> None:
    # a large hourly-vs-grid gap no longer warns the asset (it is a station memo)
    c = hu.asset_check(_asset(heat_days_shift=1.0, heat_days_grid=20.0), CHECK)
    assert c["warn"] is False


def test_station_check_heat_day_floor() -> None:
    out = hu.station_check(
        {
            108: {"name": "서울", "n_assets": 5, "heat_days_hourly": 4.5, "heat_days_grid": 2.0},
            143: {"name": "대구", "n_assets": 2, "heat_days_hourly": 12.9, "heat_days_grid": 10.0},
            90: {"name": "속초", "n_assets": 1, "heat_days_hourly": None, "heat_days_grid": 3.0},
        },
        CHECK,
    )
    assert [o["station_id"] for o in out] == [90, 108, 143]  # sorted by station id
    by = {o["station_id"]: o for o in out}
    # |4.5 - 2.0| = 2.5 > max(2, 0.3 * 2) = 2  -> fail
    assert by[108]["ok"] is False and by[108]["diff_days"] == pytest.approx(2.5)
    # |12.9 - 10| = 2.9 <= max(2, 3) = 3  -> ok
    assert by[143]["ok"] is True
    assert by[90]["ok"] is None and by[90]["diff_days"] is None


def test_sub_check_aggregates_and_reports_asset_rank() -> None:
    assets = [
        _asset(
            utci_days_ge38=float(i),
            heat_days_grid=float(i) * 0.7 + 1,
            heat_days_shift=float(i) * 0.7 + 1,
        )
        for i in range(12)
    ]
    assets[3]["station_distance_km"] = 70.0
    s = hu.sub_check(assets, CHECK)
    assert s["n_assets"] == 12 and s["n_warn_any"] == 1 and s["warn"] is True
    assert "C3" in s["warn_reason"]
    assert s["asset_rank_spearman"] == pytest.approx(1.0)
    assert s["agree"] == "1/2"  # C3 failed for one asset
    assert s["station_c2"] == [] and s["station_c2_note"] is None


def test_sub_check_station_c2_is_a_memo_only() -> None:
    assets = [_asset(utci_days_ge38=float(i)) for i in range(3)]
    days = {108: {"name": "서울", "n_assets": 3, "heat_days_hourly": 5.0, "heat_days_grid": 12.0}}
    s = hu.sub_check(assets, CHECK, days)
    assert s["station_c2"][0]["ok"] is False
    assert "108 (-7.0)" in s["station_c2_note"]
    # the memo does not change the stamp
    assert s["warn"] is False and s["agree"] == "2/2" and s["n_warn_any"] == 0


def test_sub_check_skips_assets_without_data() -> None:
    s = hu.sub_check([_asset(), _asset(utci_days_ge38=None)], CHECK)
    assert s["n_assets"] == 1 and s["n_no_data"] == 1
    assert s["asset_rank_spearman"] is None  # fewer than min_assets_for_rank


# =========================================================================== #
# 4. Stations, files, hazard object                                            #
# =========================================================================== #
_TABLE = """stnId,start,end,name,addr,office,lat,lon,elev_m,h_pa_m,h_ta_m,h_wd_m,h_rn_m
108,2010-08-16,,서울,a,o,37.5714,126.9658,85.67,86.670,1.500,10.000,0.500
108,1907-10-01,2010-08-16,서울,a,o,37.5714,126.9658,85.5,86.500,1.500,10.000,0.600
143,2017-08-25,,대구,a,o,35.878,128.653,54.27,55.520,1.800,10.000,0.600
143,1907-01-31,2017-08-25,대구,a,o,35.8282,128.6522,53.4,54.900,1.800,10.000,0.600
133,1969-01-01,,대전,a,o,36.372,127.3721,67.79,68.490,1.400,23.700,1.000
258,2010-02-08,2010-03-11,보성군,a,o,34.7633,127.2123,2.8,,,,
296,2025-04-25,,북부산,a,o,35.2178,128.9602,3,,,10.000,
"""


def _table(tmp_path: Path) -> Path:
    p = tmp_path / "stations.csv"
    p.write_text(_TABLE, encoding="utf-8")
    return p


def test_station_locations_follow_moves_by_date(tmp_path: Path) -> None:
    t = hu.read_station_table(_table(tmp_path))
    early = hu.station_locations(t, "2010-06-30").set_index("stnId")
    late = hu.station_locations(t, "2019-09-30").set_index("stnId")
    assert early.loc[143, "lat"] == pytest.approx(35.8282)
    assert late.loc[143, "lat"] == pytest.approx(35.878)
    assert 296 not in late.index  # opened 2025
    assert 258 not in late.index  # closed row
    assert late.loc[133, "h_wd_m"] == pytest.approx(23.7)


def test_anemometer_height_default_when_missing(tmp_path: Path) -> None:
    t = hu.read_station_table(_table(tmp_path))
    assert hu.anemometer_height(t, 133, "2019-09-30", default_m=10.0) == (23.7, False)
    assert hu.anemometer_height(t, 258, "2010-03-01", default_m=10.0) == (10.0, True)
    assert hu.anemometer_height(t, 999, "2019-09-30", default_m=10.0) == (10.0, True)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def test_pick_station_nearest_by_haversine() -> None:
    st = pd.DataFrame(
        {
            "stnId": [108, 112, 133],
            "lat": [37.5714, 37.4777, 36.372],
            "lon": [126.9658, 126.6249, 127.3721],
        }
    )
    sid, km = hu.pick_station(37.48, 126.70, st)
    assert sid == 112
    assert km == pytest.approx(_haversine_km(37.48, 126.70, 37.4777, 126.6249), rel=1e-3)


_COLS = (
    "tm,rnum,stnId,stnNm,ta,taQcflg,rn,rnQcflg,ws,wsQcflg,wd,wdQcflg,hm,hmQcflg,"
    "pv,td,pa,paQcflg,ps,psQcflg,ss,ssQcflg,icsr,dsnw,hr3Fhsc,dc10Tca,dc10LmcsCa,"
    "clfmAbbrCd,lcsCh,vs,gndSttCd,dmstMtphNo,ts,tsQcflg,m005Te,m01Te,m02Te,m03Te"
)


def _row(tm: str, ta: str, ws: str, hm: str, icsr: str, ts: str) -> str:
    vals = dict.fromkeys(_COLS.split(","), "")
    vals.update(tm=tm, rnum="1", stnId="999", stnNm="x", ta=ta, ws=ws, hm=hm, icsr=icsr, ts=ts)
    return ",".join(vals[c] for c in _COLS.split(","))


def test_load_asos_station_both_file_forms_and_empty_files(tmp_path: Path) -> None:
    d = tmp_path / "asos_hourly" / "999"
    d.mkdir(parents=True)
    (d / "2001_MJJAS.csv").write_text(
        "\n".join(
            [
                _COLS,
                _row("2001-07-01 12:00", "30.5", "2.1", "60", "3.1", "45.2"),
                _row("2001-07-01 13:00", "31.0", "", "58", "", ""),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (d / "2002_MJJAS.csv").write_text("", encoding="utf-8")  # empty file = missing year
    (d / "2003.csv").write_text(
        "\n".join(
            [
                _COLS,
                _row("2003-01-05 12:00", "1.0", "1", "50", "", ""),
                _row("2003-08-01 12:00", "33.0", "1.5", "55", "2.5", "50.0"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    df = hu.load_asos_station(999, years=(2001, 2003), months=[5, 6, 7, 8, 9], root=tmp_path)
    assert list(df.columns)[:5] == ["ta", "ws", "hm", "icsr", "ts"]
    assert len(df) == 3  # January row dropped, empty 2002 contributes nothing
    assert df.index.dtype.kind == "M"
    assert np.isnan(df.loc[pd.Timestamp("2001-07-01 13:00"), "ws"])
    assert np.isnan(df.loc[pd.Timestamp("2001-07-01 13:00"), "icsr"])  # blank, never 0
    assert df.loc[pd.Timestamp("2003-08-01 12:00"), "ta"] == pytest.approx(33.0)
    assert not (df.index.year == 2002).any()


def test_build_hazard_keeps_missing_as_nan_never_zero() -> None:
    haz = hu.build_hazard([(37.5, 127.0), (33.0, 124.0)], [15.0, None])
    assert haz.haz_type == "HW" and haz.units == "days/yr"
    assert_allclose(haz.frequency, [1.0])
    assert engine._hazard_intensity_at(haz, 37.5, 127.0) == pytest.approx(15.0)
    assert engine._hazard_intensity_at(haz, 33.0, 124.0) is None


def test_build_hazard_zero_stays_zero() -> None:
    haz = hu.build_hazard([(37.5, 127.0)], [0.0])
    assert engine._hazard_intensity_at(haz, 37.5, 127.0) == 0.0


# =========================================================================== #
# 5. Config, adapter, readiness                                                #
# =========================================================================== #
def _config() -> dict:
    path = ROOT / "assets" / "libraries" / "physical_risk_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_config_section_present_and_switch_is_the_user_decision() -> None:
    c = _config()["heat_utci"]
    # on since 2026-11-16 by user decision (UTCI = felt-heat index); the KDCA comparison is a
    # supplementary result, not the switch rule (user 2026-11-16)
    assert c["enabled"] is True
    assert "사용자 결정 2026-11-16" in c["basis"]
    assert "보조 결과" in c["validation"]["result"]["label"]
    for key in (
        "obs_years",
        "months",
        "thresholds_c",
        "headline_threshold_c",
        "grid_cache",
        "station_table",
        "albedo",
        "albedo_range",
        "emissivity_ground",
        "absorptivity_shortwave",
        "emissivity_person",
        "angle_factor",
        "roughness_length_m",
        "anemometer_height_default_m",
        "wind_clip_ms",
        "min_sin_elevation",
        "ground_fit_years",
        "ground_fit_min_hours",
        "min_hour_fraction",
        "min_valid_years",
        "heat_day_threshold_c",
        "check",
        "note",
    ):
        assert key in c, key
    assert c["headline_threshold_c"] == 38 and c["albedo_range"] == [0.15, 0.25]
    assert set(CHECK) <= set(c["check"])


def test_utci_adapter_describes_present_only() -> None:
    # rcp26 has no future grid (phase 2: rcp45/rcp85 from 2040 are served as ssp245/ssp585)
    d = adapters.KoreaLocalUtciAdapter("rcp26", 2050).describe()
    assert d.hazard_type == "HW" and d.status == "HAZARD_ONLY"
    assert d.hazard_dataset == "kma_asos_open_field_utci"
    assert d.requested_scenario == "rcp26" and d.served_scenario == "historical"
    assert d.time_horizon == "2000-2019 May-Sep"
    assert "historical" in (d.detail or "").lower()


def test_adapter_for_switches_on_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from climaterisk.physical_risk import metrics

    base = metrics.load_config()
    cfgs = {}
    for flag in (False, True):
        cfgs[flag] = json.loads(json.dumps(base))
        cfgs[flag]["heat_utci"]["enabled"] = flag
    monkeypatch.setattr(metrics, "load_config", lambda *a, **k: cfgs[False])
    off = adapters.adapter_for("HW", "KOREA_LOCAL", "historical", 2050, "KOR")
    assert isinstance(off, adapters.KoreaLocalHeatAdapter)
    monkeypatch.setattr(metrics, "load_config", lambda *a, **k: cfgs[True])
    on = adapters.adapter_for("HW", "KOREA_LOCAL", "historical", 2050, "KOR")
    assert isinstance(on, adapters.KoreaLocalUtciAdapter)
    hm = adapters.adapter_for("HM", "KOREA_LOCAL", "historical", 2050, "KOR")
    assert isinstance(hm, adapters.KoreaLocalHeatAdapter)  # heat mortality untouched


def test_readiness_unchanged() -> None:
    assert models.READINESS["HEAT"]["KOREA_LOCAL"]["status"] == "HAZARD_ONLY"
    assert adapters.readiness_matches_backend()


# =========================================================================== #
# 6. Real data (skipped when the KMA files are not on disk)                    #
# =========================================================================== #
# conftest.py isolates CLIMATERISK_DATA_ROOT in a temp folder; real-data tests opt in through
# CLIMATERISK_REAL_DATA_ROOT (default ~/Data/climaterisk), as the other real-data tests do. The
# heat module only reads from the data root.
REAL_ROOT = Path(
    os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk"))
)


def _real_data() -> bool:
    kma = REAL_ROOT / "external" / "kma"
    return (
        (kma / "asos_hourly" / "108").is_dir()
        and (kma / "asos_meta" / "kma_asos_stations_history.csv").is_file()
        and (REAL_ROOT / "cache" / "heat_days" / "obs_2000_2019_m5-9_b0.05.npz").is_file()
    )


real = pytest.mark.skipif(not _real_data(), reason="KMA ASOS / grid cache not on disk")
SEOUL = (37.5714, 126.9658)
BASELINE = ROOT / "tests" / "baselines" / "heat_utci_seoul108.json"


def _point_at_real_root(mp: pytest.MonkeyPatch) -> None:
    mp.setenv("CLIMATERISK_DATA_ROOT", str(REAL_ROOT))
    mp.delenv("CLIMATERISK_KMA_DIR", raising=False)


@pytest.fixture
def real_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    _point_at_real_root(monkeypatch)
    return REAL_ROOT


@pytest.fixture(scope="module")
def seoul_run() -> dict:
    if not _real_data():
        pytest.skip("KMA data not on disk")
    with pytest.MonkeyPatch.context() as mp:
        _point_at_real_root(mp)
        return hu.open_field_utci([SEOUL], _config()["heat_utci"])


def test_real_data_is_found_when_present() -> None:
    """Guard against the silent skip: on a machine with the data, the real tests must run."""
    if not (REAL_ROOT / "external" / "kma" / "asos_hourly").is_dir():
        pytest.skip("no KMA folder on this machine (CI)")
    assert _real_data(), f"KMA ASOS present under {REAL_ROOT} but a required file is missing"


@real
def test_seoul_station_point_plausible(seoul_run: dict) -> None:
    a = seoul_run["per_asset"][0]
    assert a["station_id"] == 108 and a["station_distance_km"] < 1.0
    assert a["radiation_station_id"] == 108
    assert a["wind_height_assumed"] is False
    assert a["n_valid_years"] >= 15
    assert len(a["utci_days_ge38_by_year"]) == 20
    # Probe (design §5.2, regression ground temperature, no grid shift): D38 16.7, D32 89.0
    assert 10.0 <= a["utci_days_ge38"] <= 25.0
    assert 75.0 <= a["utci_days_ge32"] <= 100.0
    assert a["utci_days_ge38_low"] <= a["utci_days_ge38"] <= a["utci_days_ge38_high"]
    assert 0.0 <= a["calm_clipped_frac"] < 0.2
    assert len(a["hottest_day_hourly"]["utci"]) == 24
    assert set(seoul_run["record"]) >= {"ground_fit", "stations_used", "hazard_data_version"}


@real
def test_seoul_station_c2_matches_the_asset_at_the_station(seoul_run: dict) -> None:
    """At the station's own cell the shift is 0, so the station memo equals the asset values."""
    a = seoul_run["per_asset"][0]
    (st,) = seoul_run["sub_check"]["station_c2"]
    assert st["station_id"] == 108 and st["n_assets"] == 1
    assert st["heat_days_hourly"] == pytest.approx(a["heat_days_shift"], rel=1e-9)
    assert st["heat_days_grid"] == pytest.approx(a["heat_days_grid"], rel=1e-9)
    assert "check_C2_diff_days" not in a


@real
def test_seoul_captured_baseline(seoul_run: dict) -> None:
    """Captured once after implementation (tests/baselines/heat_utci_seoul108.json)."""
    assert BASELINE.is_file(), "capture the Seoul 108 baseline after the first green run"
    want = json.loads(BASELINE.read_text(encoding="utf-8"))
    a = seoul_run["per_asset"][0]
    for key in ("utci_days_ge38", "utci_hours_ge38", "utci_days_ge32", "utci_annual_max_mean"):
        assert a[key] == pytest.approx(want[key], rel=1e-6), key
    h = a["hottest_day_hourly"]
    assert a["hottest_day"] == want["hottest_day"]
    assert_allclose(h["tmrt"], want["hottest_day_hourly"]["tmrt"], rtol=1e-6)
    assert_allclose(h["utci"], want["hottest_day_hourly"]["utci"], rtol=1e-6)


@real
def test_station_without_radiation_borrows_one(real_root: Path) -> None:
    kma = paths.kma_dir()
    f = kma / "asos_hourly" / "140" / "2018_MJJAS.csv"
    if not f.is_file():
        pytest.skip("140 not on disk")
    if pd.read_csv(f)["icsr"].notna().any():
        pytest.skip("140 has radiation in this copy")
    t = hu.read_station_table(kma / "asos_meta" / "kma_asos_stations_history.csv")
    loc = hu.station_locations(t, "2019-09-30").set_index("stnId").loc[140]
    out = hu.open_field_utci([(float(loc["lat"]), float(loc["lon"]))], _config()["heat_utci"])
    a = out["per_asset"][0]
    assert a["station_id"] == 140
    assert a["radiation_station_id"] != 140 and a["radiation_distance_km"] > 0


@real
def test_sea_point_has_no_data_not_zero(real_root: Path) -> None:
    out = hu.open_field_utci([(33.0, 124.0)], _config()["heat_utci"])
    a = out["per_asset"][0]
    assert a["utci_days_ge38"] is None and a["status"] == "NO_HAZARD_DATA"


@real
def test_adapter_load_intensity_is_the_headline_and_check_never_changes_it(real_root: Path) -> None:
    pts = [SEOUL, (35.1729, 126.8916), (33.0, 124.0)]
    ad = adapters.KoreaLocalUtciAdapter("historical", 2050)
    haz = ad.load(points=pts)
    per = ad.diagnostics["per_asset"]
    for (lat, lon), a in zip(pts, per, strict=True):
        got = engine._hazard_intensity_at(haz, lat, lon)
        if a["utci_days_ge38"] is None:
            assert got is None
        else:
            assert got == pytest.approx(a["utci_days_ge38"], rel=1e-12)
    assert "sub_check" in ad.diagnostics and "agree" in ad.diagnostics["sub_check"]


@real
def test_twenty_assets_run_fast(real_root: Path) -> None:
    rng = np.random.default_rng(42)
    pts = list(zip(rng.uniform(35.0, 37.8, 20), rng.uniform(126.6, 129.2, 20), strict=True))
    t0 = time.perf_counter()
    out = hu.open_field_utci(pts, _config()["heat_utci"])
    assert len(out["per_asset"]) == 20
    assert time.perf_counter() - t0 < 60.0  # design §9: ~1 min per 1,000 assets
