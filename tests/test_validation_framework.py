"""Validation framework arithmetic — **synthetic numbers only**.

These tests check that the observed-vs-modelled metrics are computed correctly. They are
NOT an empirical validation of the model: no observed Korean (or any) loss series is used.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import validation as val  # noqa: E402


def test_annual_comparison_on_synthetic_series() -> None:
    observed = {2000: 100.0, 2001: 200.0, 2002: 300.0, 2003: 50.0}
    modelled = {2000: 110.0, 2001: 180.0, 2002: 330.0, 2010: 1.0}  # 2003 missing, 2010 extra
    out = val.annual_comparison(observed, modelled)
    assert out["years"] == [2000, 2001, 2002] and out["n"] == 3
    assert out["bias"] == pytest.approx((10 - 20 + 30) / 3)
    assert out["mae"] == pytest.approx(20.0)
    assert out["rmse"] == pytest.approx(math.sqrt((100 + 400 + 900) / 3))
    assert out["ratio"] == pytest.approx(620 / 600)
    assert out["coverage"] == pytest.approx(3 / 4)


def test_annual_comparison_with_no_overlap_is_nan_not_zero() -> None:
    out = val.annual_comparison({2000: 1.0}, {2001: 1.0})
    assert out["n"] == 0 and math.isnan(out["bias"]) and out["coverage"] == 0.0


def test_event_comparison_reports_tdr_style_total_and_edr_style_ratios() -> None:
    out = val.event_comparison([("Maemi 2003", 4.0, 2.0), ("Rusa 2002", 6.0, 3.0)])
    assert out["n"] == 2
    assert out["total_ratio"] == pytest.approx(0.5)
    assert [e["ratio"] for e in out["events"]] == [pytest.approx(0.5), pytest.approx(0.5)]
    assert out["bias"] == pytest.approx(-2.5)


def test_modelled_annual_losses_sum_events_by_calendar_year() -> None:
    d = [
        dt.date(2003, 9, 12).toordinal(),
        dt.date(2003, 7, 1).toordinal(),
        dt.date(2002, 8, 31).toordinal(),
        0,
    ]
    out = val.modelled_annual_losses([1.0, 2.0, 5.0, 99.0], d)
    assert out == {2003: 3.0, 2002: 5.0}  # undated event skipped


def test_observed_series_requires_a_source_string() -> None:
    s = val.ObservedSeries(source="synthetic unit-test numbers", unit="KRW", losses={2001: 1.0})
    assert s.years() == [2001] and "synthetic" in s.source
