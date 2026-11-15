"""Visual board national step 2 gate (reviewer-written; agents may not edit it).

A3 coastal flood RP layer, A7 wildfire RP layer, A5 heat days, A4 heat health.

Config:
- ``visual_board.json`` layers ``coastal_flood_hist_kor`` (quantity ``water_depth_m``,
  use_tier "S", reason mentions 방어) and ``wildfire_hist_kor`` (``fire_intensity_K``, "S").
- ``assets/libraries/heat_days.json``: ``threshold_degC`` 33, ``months`` [5..9],
  ``obs_years`` [2000, 2019], ``model_base_years`` [2021, 2030], ``model_future_years``
  [2051, 2060], ``scenarios`` ["ssp245", "ssp585"], ``block_deg`` 0.05,
  ``check.warn_rel_bias`` in (0, 1).
- ``assets/libraries/heat_health.json``: ``years`` [2000, 2019], ``check`` with
  ``reference_year``, ``reference_rate_per_100k`` (number), ``reference_source`` (non-empty)
  and ``warn_ratio`` [lo, hi] with 0 < lo < 1 < hi.

Worker ``climaterisk_worker.heat_days``:
- ``count_days(tmax, years, threshold)``: tmax ``(n_days, ...)`` degC, years ``(n_days,)``;
  mean over distinct years of the number of days with tmax >= threshold; NaN where a cell
  has no finite day.
- ``delta_change(obs, obs_months, base, base_months, fut, fut_months)``: obs + (mean of fut
  in the same calendar month - mean of base in that month), per cell; months are ints 1-12.
- ``bias_check(model, obs, warn_rel)`` -> ``{"rel_bias", "warning"}``: rel_bias =
  (nanmean(model) - nanmean(obs)) / nanmean(obs) over cells finite in both; warning when
  |rel_bias| > warn_rel.
Worker ``climaterisk_worker.heat_health``:
- ``share_over65(bands)``: ``bands`` maps WorldPop age-band labels ("0", "1", "5", ...,
  "80") to arrays of persons; returns persons aged >= 65 / all persons (NaN where 0).
- ``rate_per_100k(deaths, population)``: 1e5 * deaths / population, NaN where population 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
LIB = ROOT / "assets/libraries"


def _json(name: str) -> dict:
    return json.loads((LIB / name).read_text(encoding="utf-8"))


def test_config() -> None:
    layers = _json("visual_board.json")["layers"]
    cf, wf = layers["coastal_flood_hist_kor"], layers["wildfire_hist_kor"]
    assert cf["quantity"] == "water_depth_m" and cf["use_tier"] == "S"
    assert "방어" in cf["use_tier_reason"]
    assert wf["quantity"] == "fire_intensity_K" and wf["use_tier"] == "S"
    hd = _json("heat_days.json")
    assert hd["threshold_degC"] == 33 and hd["months"] == [5, 6, 7, 8, 9]
    assert hd["obs_years"] == [2000, 2019] and hd["model_base_years"] == [2021, 2030]
    assert hd["model_future_years"] == [2051, 2060]
    assert hd["scenarios"] == ["ssp245", "ssp585"] and hd["block_deg"] == 0.05
    assert 0 < hd["check"]["warn_rel_bias"] < 1
    hh = _json("heat_health.json")
    assert hh["years"] == [2000, 2019]
    ck = hh["check"]
    assert isinstance(ck["reference_rate_per_100k"], (int, float)) and ck["reference_source"]
    lo, hi = ck["warn_ratio"]
    assert 0 < lo < 1 < hi and isinstance(ck["reference_year"], int)


def test_count_days_and_delta() -> None:
    hd = pytest.importorskip("climaterisk_worker.heat_days")
    # 2 years x 3 days, 2 cells; cell 1 all NaN
    t = np.array(
        [[34, np.nan], [33, np.nan], [20, np.nan], [35, np.nan], [10, np.nan], [10, np.nan]]
    )
    years = np.array([2000, 2000, 2000, 2001, 2001, 2001])
    out = hd.count_days(t, years, 33.0)
    np.testing.assert_allclose(out[0], 1.5, rtol=0, atol=1e-12)  # (2 + 1) / 2
    assert np.isnan(out[1])
    obs = np.array([[30.0], [31.0]])
    obs_m = np.array([7, 8])
    base = np.array([[28.0], [29.0], [26.0]])
    base_m = np.array([7, 7, 8])
    fut = np.array([[30.0], [31.0], [27.5]])
    fut_m = np.array([7, 7, 8])
    d = hd.delta_change(obs, obs_m, base, base_m, fut, fut_m)
    # July delta = 30.5 - 28.5 = 2.0; August delta = 27.5 - 26 = 1.5
    np.testing.assert_allclose(d[:, 0], [32.0, 32.5], rtol=0, atol=1e-12)
    b = hd.bias_check(np.array([5.0, 7.0, np.nan]), np.array([10.0, 10.0, 3.0]), 0.3)
    assert b["rel_bias"] == pytest.approx(-0.4) and b["warning"] is True


def test_heat_health_math() -> None:
    hh = pytest.importorskip("climaterisk_worker.heat_health")
    bands = {"0": np.array([60.0, 0.0]), "60": np.array([20.0, 0.0]), "65": np.array([15.0, 0.0])}
    bands["80"] = np.array([5.0, 0.0])
    s = hh.share_over65(bands)
    np.testing.assert_allclose(s[0], 0.2, rtol=0, atol=1e-12)
    assert np.isnan(s[1])
    r = hh.rate_per_100k(np.array([2.0, 1.0]), np.array([1e5, 0.0]))
    np.testing.assert_allclose(r[0], 2.0, rtol=0, atol=1e-12)
    assert np.isnan(r[1])
