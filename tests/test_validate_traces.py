"""Step R4 gate (written by the reviewer before the step; agents may not edit it).

Building-level validation of the twin against the Seoul flood-trace maps (서울시 침수흔적도,
공공누리 제1유형). The trace polygons are only QUERIED (does a building touch one?); they
are never rasterised, copied into the repo or shipped. Module
``worker/climaterisk_worker/twin/validate.py``; every number from ``spec["validation"]``:

- ``observed_rain_spec(spec, rain_mm, step_min)`` -> deep copy of ``spec`` whose ``rain``
  reproduces an observed series: ``rain_mm`` (depth per step, mm, length N) becomes
  ``total_mm = sum``, ``duration_min = N * step_min`` and
  ``cumulative_fraction = [0, cumsum(rain_mm) / total]`` (length N + 1), so that
  ``pluvial.hyetograph`` returns the observed cumulative rain at every step boundary.
  ``spec`` itself is unchanged. A series with zero total raises ``ValueError``.
- ``gauge_series(df, codes, start, end, step_min)`` -> ``pandas.Series`` of the MEAN depth
  per step (mm) over the gauges in ``codes``, on the full regular grid ``[start, end)`` with
  ``step_min`` spacing. ``df`` has the Seoul file columns ``"강우량계 코드"``,
  ``"10분우량"`` and ``"자료수집 시각"`` (strings like ``2022-08-08 21:09`` or
  ``2022-08-08 21:09:59``); a reading belongs to the step its time FLOORS to; duplicate
  readings of one gauge in one step keep the maximum; a gauge with no reading in a step
  counts as 0 there.
- ``trace_flags(buildings, traces, buffer_m)`` -> bool array, one per building: whether the
  footprint, buffered by ``buffer_m`` metres in the traces' projected CRS (EPSG:5179 when
  the traces have none), intersects any trace polygon.
- ``model_flags(depth_m, threshold_m)`` -> ``depth_m > threshold_m``.
- ``contingency(pred, obs)`` -> dict with ints ``hits``, ``false_alarms``, ``misses``,
  ``correct_negatives``, ``n`` and floats ``pod = h/(h+m)``, ``far = fa/(h+fa)``,
  ``csi = h/(h+fa+m)``, ``bias = (h+fa)/(h+m)`` — NaN where a denominator is 0.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("geopandas")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

validate = pytest.importorskip("climaterisk_worker.twin.validate")

M = 111320.0
LON_M = M * math.cos(math.radians(37.5))


def test_observed_rain_reproduces_the_series() -> None:
    from climaterisk_worker.twin import pluvial

    spec = load_spec()
    before = dict(spec["rain"])
    rain = np.array([0.0, 2.0, 10.0, 25.5, 4.0, 0.5])
    s2 = validate.observed_rain_spec(spec, rain, 10.0)
    assert spec["rain"] == before
    assert s2["rain"]["total_mm"] == pytest.approx(42.0)
    assert s2["rain"]["duration_min"] == pytest.approx(60.0)
    t, cum = pluvial.hyetograph(s2)
    np.testing.assert_allclose(t, np.arange(7) * 600.0)
    np.testing.assert_allclose(cum, np.concatenate([[0.0], np.cumsum(rain)]), atol=1e-9)
    with pytest.raises(ValueError):
        validate.observed_rain_spec(spec, np.zeros(4), 10.0)


def test_gauge_series_means_floors_and_fills() -> None:
    df = pd.DataFrame(
        {
            "강우량계 코드": [1, 2, 1, 1, 2, 3],
            "10분우량": [4.0, 6.0, 3.0, 5.0, 1.0, 99.0],
            "자료수집 시각": [
                "2022-08-08 21:09",
                "2022-08-08 21:09:59",
                "2022-08-08 21:19",
                "2022-08-08 21:12",  # same step as 21:19 for gauge 1: keep the max (5)
                "2022-08-08 21:29",
                "2022-08-08 21:09",  # gauge 3 is not asked for
            ],
        }
    )
    s = validate.gauge_series(
        df, [1, 2], pd.Timestamp("2022-08-08 21:00"), pd.Timestamp("2022-08-08 21:40"), 10.0
    )
    assert list(s.index) == list(pd.date_range("2022-08-08 21:00", periods=4, freq="10min"))
    np.testing.assert_allclose(s.to_numpy(), [(4 + 6) / 2, (5 + 0) / 2, (0 + 1) / 2, 0.0])


def _box(x0: float, y0: float, s: float):
    from shapely.geometry import box

    return box(127.0 + x0 / LON_M, 37.5 + y0 / M, 127.0 + (x0 + s) / LON_M, 37.5 + (y0 + s) / M)


def test_trace_flags_use_a_metric_buffer() -> None:
    import geopandas as gpd

    b = gpd.GeoDataFrame(
        {"building": ["a", "b", "c"]},
        geometry=[_box(0, 0, 20), _box(100, 0, 20), _box(300, 0, 20)],
        crs="EPSG:4326",
    )
    traces = gpd.GeoDataFrame(
        {"F_AREA": [1.0, 1.0]},
        geometry=[_box(25, 0, 10), _box(135, 0, 10)],  # 5 m east of a, 15 m east of b
        crs="EPSG:4326",
    ).to_crs(5179)
    flags = validate.trace_flags(b, traces, 10.0)
    assert flags.dtype == bool and list(flags) == [True, False, False]
    assert list(validate.trace_flags(b, traces, 20.0)) == [True, True, False]
    assert list(validate.trace_flags(b, traces.set_crs(None, allow_override=True), 10.0)) == [
        True,
        False,
        False,
    ]


def test_contingency_scores() -> None:
    pred = np.array([1, 1, 1, 0, 0, 0, 0, 1], dtype=bool)
    obs = np.array([1, 1, 0, 1, 0, 0, 0, 0], dtype=bool)
    c = validate.contingency(pred, obs)
    assert (c["hits"], c["false_alarms"], c["misses"], c["correct_negatives"], c["n"]) == (
        2,
        2,
        1,
        3,
        8,
    )
    assert c["pod"] == pytest.approx(2 / 3)
    assert c["far"] == pytest.approx(0.5)
    assert c["csi"] == pytest.approx(0.4)
    assert c["bias"] == pytest.approx(4 / 3)
    none = validate.contingency(np.zeros(3, bool), np.zeros(3, bool))
    assert math.isnan(none["csi"]) and math.isnan(none["pod"]) and none["correct_negatives"] == 3
    np.testing.assert_array_equal(
        validate.model_flags(np.array([0.05, 0.051, 0.0]), 0.05), [False, True, False]
    )


def test_spec_validation_section() -> None:
    v = load_spec()["validation"]
    assert v["buffer_m"] > 0 and v["step_min"] == 10
    assert set(v["events"]) >= {"2010", "2011", "2022"}
    e = v["events"]["2022"]
    assert e["gauges"] and pd.Timestamp(e["start"]) < pd.Timestamp(e["end"])
