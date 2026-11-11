"""Pohang propagation gate (reviewer-written; agents may not edit it): flood maps at clock
times, the time-step figure the published studies show (Hwang et al. 2024 show inundation at
successive times).

The main storm is rerun (no change to inputs or solver) with snapshots of the depth field
at chosen clock times. The rerun must reproduce the stored maximum depth: that is the check
that the snapshots belong to the same run as every other figure.

Module ``climaterisk_worker.twin.case_pohang_propagation``:
- ``model_seconds(clock_times, start)`` -> list[float]: seconds after model t = 0 for ISO
  clock strings; ``ValueError`` when a time is before ``start`` or the list is not strictly
  increasing.
- ``Snapshots(times_s)``: ``offer(t, h)`` keeps, for every requested time T not yet taken,
  a float32 copy of ``h`` at the FIRST offered t >= T (a later offer never overwrites);
  ``taken`` -> list of (T, t_actual, array) in the order of ``times_s``; ``missing`` -> the
  requested times never reached.
- ``flooded_area_km2(h, cell_m, mask, threshold_m)`` -> float: cells inside ``mask`` with
  depth strictly above the threshold, times cell area.
  $$ A = n\\{ h_{ij} > \\theta,\\ (i,j) \\in M \\} \\cdot \\Delta x^2 / 10^6 $$
  (ASCII: count of masked cells deeper than theta, times cell area in km2).
- ``climaterisk_worker.twin.case_pohang.run_storm(..., snapshot_s=None)``: optional snapshot
  times (s); ``None`` keeps the old behaviour exactly; the result then has ``snapshots``.

Config ``assets/libraries/case_pohang_spec.json`` ``figures``: ``propagation_clock`` (ISO
strings, strictly increasing, >= 4 and <= 12, all on 2022-09-06), ``propagation_threshold_m``
(> 0), ``propagation_reproduce_atol_m`` (> 0).

Built output (skipped when absent): ``propagation.json`` {"badge": "미검증", "clock_start"
(equal to figures_pack.json), "threshold_m", "mask_ko", "frames": [{"clock", "model_h",
"actual_h", "flooded_km2"}] (one per configured clock, actual_h >= model_h, flooded_km2 >= 0),
"reproduce": {"max_abs_diff_m", "atol_m", "ok"}, "figures": [{"file", "caption_ko"}]} with
``fig_propagation.png`` and ``fig_flooded_area_time.png`` (each > 5 kB). ``reproduce.ok`` must
be true (else the snapshots are not from the stored run). No key containing krw / usd / loss.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
SPEC = json.loads((ROOT / "assets/libraries/case_pohang_spec.json").read_text(encoding="utf-8"))


def test_config() -> None:
    fg = SPEC["figures"]
    clk = [datetime.fromisoformat(c) for c in fg["propagation_clock"]]
    assert 4 <= len(clk) <= 12 and all(a < b for a, b in pairwise(clk))
    assert all(c.date().isoformat() == "2022-09-06" for c in clk)
    assert fg["propagation_threshold_m"] > 0 and fg["propagation_reproduce_atol_m"] > 0


def test_model_seconds() -> None:
    p = pytest.importorskip("climaterisk_worker.twin.case_pohang_propagation")
    start = datetime.fromisoformat("2022-09-05T22:30")
    assert p.model_seconds(["2022-09-06T00:00", "2022-09-06T05:15"], start) == [5400.0, 24300.0]
    for bad in (["2022-09-05T22:00"], ["2022-09-06T01:00", "2022-09-06T01:00"]):
        with pytest.raises(ValueError):
            p.model_seconds(bad, start)


def test_snapshots() -> None:
    p = pytest.importorskip("climaterisk_worker.twin.case_pohang_propagation")
    s = p.Snapshots([10.0, 20.0, 99.0])
    s.offer(5.0, np.full((2, 2), 1.0))
    s.offer(12.0, np.full((2, 2), 2.0))
    s.offer(25.0, np.full((2, 2), 3.0))  # takes T=20 (first t >= 20)
    s.offer(30.0, np.full((2, 2), 4.0))  # must not overwrite
    taken = s.taken
    assert [(T, t) for T, t, _ in taken] == [(10.0, 12.0), (20.0, 25.0)]
    assert taken[0][2].dtype == np.float32 and float(taken[1][2][0, 0]) == 3.0
    assert s.missing == [99.0]
    h = np.full((2, 2), 9.0)
    s2 = p.Snapshots([0.0])
    s2.offer(0.0, h)
    h[:] = 0.0  # the snapshot is a copy
    assert float(s2.taken[0][2][0, 0]) == 9.0


def test_flooded_area() -> None:
    p = pytest.importorskip("climaterisk_worker.twin.case_pohang_propagation")
    h = np.array([[0.0, 0.3, 0.31], [1.0, 2.0, 5.0]])
    m = np.array([[True, True, True], [True, False, True]])
    # 0.31, 1.0, 5.0 inside the mask and > 0.3 (0.3 itself is not) -> 3 cells of 30 m
    np.testing.assert_allclose(p.flooded_area_km2(h, 30.0, m, 0.3), 3 * 900 / 1e6, rtol=1e-12)


CASE = Path(os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk")))
CASE = CASE / "derived/case_twins/pohang"


def _walk_keys(o: object) -> list[str]:
    if isinstance(o, dict):
        return [*o.keys(), *[k for v in o.values() for k in _walk_keys(v)]]
    if isinstance(o, list):
        return [k for v in o for k in _walk_keys(v)]
    return []


def test_built_propagation() -> None:
    f = CASE / "propagation.json"
    if not f.exists():
        pytest.skip("propagation not built here")
    d = json.loads(f.read_text(encoding="utf-8"))
    pack = json.loads((CASE / "figures_pack.json").read_text(encoding="utf-8"))
    fg = SPEC["figures"]
    assert d["badge"] == "미검증" and d["clock_start"] == pack["clock_start"]
    assert d["threshold_m"] == fg["propagation_threshold_m"] and d["mask_ko"]
    assert [x["clock"] for x in d["frames"]] == fg["propagation_clock"]
    for x in d["frames"]:
        assert x["actual_h"] >= x["model_h"] and x["flooded_km2"] >= 0
    rp = d["reproduce"]
    assert rp["atol_m"] == fg["propagation_reproduce_atol_m"]
    assert rp["ok"] is True and rp["max_abs_diff_m"] <= rp["atol_m"]
    assert [x["file"] for x in d["figures"]] == ["fig_propagation.png", "fig_flooded_area_time.png"]
    for x in d["figures"]:
        assert x["caption_ko"] and (CASE / x["file"]).stat().st_size > 5000
    assert not [k for k in _walk_keys(d) if any(w in k.lower() for w in ("krw", "usd", "loss"))]
