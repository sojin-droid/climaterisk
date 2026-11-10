"""Case twin gate: Pohang Naengcheon, Typhoon Hinnamnor 2022-09-06 (reviewer-written; agents
may not edit it). Mission: docs/reports/twin_case_sites_mission_ko.md section A.

Spec ``assets/libraries/case_pohang_spec.json``:
- ``outlet``: {lon, lat, note} near 냉천교 (the mouth).
- ``catchment_ref``: {area_km2: 79.4, source}; ``design_flood_m3s`` {"100": 866, "500": 1189},
  ``channel_capacity_m3s`` 700, each with a source string.
- ``rain.main``: {"depth_duration_mm": {"60": 77.0, "120": 147.9, "180": 203.2,
  "360": 314.5, "720": 378.7}, "dt_min": 10, source} (Jung et al. 2025, published values).
- ``rain.check``: Seoul 2022-08-08 gauge shape scaled to ``total_mm`` 359.8 over
  ``duration_min`` 540, with source; diagnostic only.
- ``check.warn_peak_rel`` in (0, 1).
- ``assumptions``: non-empty list of strings (every assumption, shown with its count).

Module ``climaterisk_worker.twin.case_pohang``:
- ``nested_hyetograph(depth_duration_mm, dt_min)`` -> increments (mm per dt step) over the
  longest duration, alternating-block (peak in the middle, next blocks alternate right,
  left): for each listed duration D the maximum running sum over any window of D minutes
  equals depth(D) (to 1e-9); total = depth(longest).
- ``scaled_shape(cum_fraction, total_mm)`` -> cumulative mm (cum_fraction scaled to end at
  total_mm; cum_fraction must be non-decreasing from 0 to 1, else ValueError).
- ``delineate(dem, outlet_rc)`` -> boolean mask of cells whose D8 path reaches outlet_rc
  (outlet included), D8 as in ``twin.runon``.
Outputs (``$CLIMATERISK_DATA_ROOT/derived/case_twins/pohang/summary.json``, test skipped if
absent): ``catchment_area_km2`` within 15 % of 79.4 (terrain sanity check),
``check`` with ``peak_rel_diff`` and ``warning``, ``n_assumptions == len(assumptions)``,
``badge == "미검증"``, and no monetary key (no "krw", "usd", "loss" in the keys).
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
SPEC = json.loads((ROOT / "assets/libraries/case_pohang_spec.json").read_text(encoding="utf-8"))


def test_spec() -> None:
    assert SPEC["catchment_ref"]["area_km2"] == 79.4 and SPEC["catchment_ref"]["source"]
    assert SPEC["design_flood_m3s"]["100"] == 866 and SPEC["design_flood_m3s"]["500"] == 1189
    assert SPEC["channel_capacity_m3s"] == 700
    dd = SPEC["rain"]["main"]["depth_duration_mm"]
    assert dd == {"60": 77.0, "120": 147.9, "180": 203.2, "360": 314.5, "720": 378.7}
    assert SPEC["rain"]["main"]["source"] and SPEC["rain"]["check"]["source"]
    assert SPEC["rain"]["check"]["total_mm"] == 359.8
    assert SPEC["rain"]["check"]["duration_min"] == 540
    assert 0 < SPEC["check"]["warn_peak_rel"] < 1
    assert SPEC["assumptions"] and all(isinstance(a, str) and a for a in SPEC["assumptions"])
    o = SPEC["outlet"]
    assert 129.2 < o["lon"] < 129.5 and 35.9 < o["lat"] < 36.1


def test_nested_hyetograph() -> None:
    cp = pytest.importorskip("climaterisk_worker.twin.case_pohang")
    dd = {"60": 77.0, "120": 147.9, "180": 203.2, "360": 314.5, "720": 378.7}
    inc = np.asarray(cp.nested_hyetograph(dd, 10))
    assert inc.shape == (72,) and (inc >= 0).all()
    for d, depth in dd.items():
        w = int(d) // 10
        run = np.convolve(inc, np.ones(w), mode="valid").max()
        np.testing.assert_allclose(run, depth, rtol=0, atol=1e-9)
    np.testing.assert_allclose(inc.sum(), 378.7, rtol=0, atol=1e-9)


def test_scaled_shape() -> None:
    cp = pytest.importorskip("climaterisk_worker.twin.case_pohang")
    out = cp.scaled_shape(np.array([0.0, 0.25, 1.0]), 359.8)
    np.testing.assert_allclose(out, [0.0, 89.95, 359.8], rtol=0, atol=1e-9)
    with pytest.raises(ValueError):
        cp.scaled_shape(np.array([0.0, 0.6, 0.5]), 10.0)


def test_delineate_valley() -> None:
    cp = pytest.importorskip("climaterisk_worker.twin.case_pohang")
    # V valley draining south; a ridge column on the right drains east, off the outlet
    y, x = np.mgrid[0:6, 0:5].astype(float)
    dem = 10.0 + (5 - y) * 1.0 + np.abs(x - 2) * 0.5
    dem[:, 4] = 0.0  # east column is a separate low channel
    mask = cp.delineate(dem, (5, 2))
    assert mask[5, 2] and mask[0, 2] and mask[0, 0]
    assert not mask[:, 4].any()


SUMMARY = Path(os.environ.get("CLIMATERISK_DATA_ROOT", "/nonexistent")) / (
    "derived/case_twins/pohang/summary.json"
)


@pytest.mark.skipif(not SUMMARY.exists(), reason="Pohang twin not built here")
def test_summary() -> None:
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert abs(s["catchment_area_km2"] - 79.4) / 79.4 <= 0.15
    assert isinstance(s["check"]["warning"], bool) and "peak_rel_diff" in s["check"]
    assert s["n_assumptions"] == len(SPEC["assumptions"]) and s["badge"] == "미검증"
    flat = json.dumps(s).lower()
    assert not any(k in flat for k in ('"krw', '"usd', 'loss"'))
