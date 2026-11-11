"""Pohang case figure pack gate (reviewer-written; agents may not edit it).

Figures and tables in the style of the published Naengcheon studies, made from the EXISTING
30 m run (no new solver run) plus a terrain rebuild for the channel profile. Everything stays
"미검증", no money. Published observations come from one open paper and are never tuned to:

    Hwang, Kang, Jin, Lee (2024) "Simulation of compound flooding using TUFLOW's 1D-2D coupling
    features", J. Korea Water Resour. Assoc. 57(7) 451-460, doi:10.3741/JKWRA.2024.57.7.451
    (CC BY): peak clock hour of rain 04:00-05:00 on 2022-09-06 (79.3 mm/h), 인덕교 right bank
    already overtopped at 06:20 (CCTV), apartment just upstream of 인덕교 on the right bank
    0.3 m at 06:30 and 0.5 m at 06:45 (dash-cam, read to 0.1 m).

Config ``assets/libraries/case_pohang_observations.json``:
- ``source`` {"citation", "doi", "license"}; ``rain_peak_hour`` {"start", "end", "mm"} (naive
  local ISO times); ``observations``: list of
  {"id", "kind": "depth_at_time", "point", "when", "value_m", "tol_m", "note_ko"} or
  {"id", "kind": "overtopped_by", "point", "when", "note_ko"}; ids include
  "indeok_overtopped", "apt_0630", "apt_0645"; every ``point`` is a column of
  ``points_depth.csv``; ``not_compared``: list of {"what_ko", "why_ko"} (the three surveyed
  depths C/G/H have no coordinates); ``other_models``: list of {"name", "grid_m", "inputs_ko",
  "max_depth_ko", "validation_ko", "source"} with at least the TUFLOW and the HEC-RAS 2D study;
  ``assumptions``: exactly two strings starting "A27 " (clock anchoring) and "A28 " (which
  apartment the paper means).

Module ``climaterisk_worker.twin.case_pohang_figures`` (pure numpy / stdlib for these):
- ``peak_window_center_h(increments_mm, dt_min, window_min=60)`` -> centre (hours after the
  start) of the ``window_min`` moving window with the largest sum (first one on ties).
- ``anchor_start(model_center_h, obs_start, obs_end)`` -> ``datetime`` clock time of model
  t = 0 so that the model's peak-hour centre falls on the middle of the observed peak hour:
  $$ t_0 = \\tfrac{1}{2}(a + b) - c $$  (a, b observed hour ends; c model centre, h).
  ASCII: t0 = midpoint(obs) - model_center.
- ``to_clock(t_h, start)`` -> datetime of ``start + t_h`` hours.
- ``depth_at_clock(t_h, values, start, when)`` -> linear interpolation at the clock time
  ``when`` (ISO string or datetime); ``nan`` outside the series.
- ``first_crossing_h(t_h, values, level=0.0)`` -> first time (linear interpolation between
  samples) the series rises above ``level``; ``None`` if never; the first sample's time if it
  already starts above.
- ``validation_rows(observations, series, start)`` with ``series[point] = {"t_h", "depth",
  "above_bank"}`` -> one row per observation, in order:
  depth_at_time: {"id", "kind", "observed", "model", "diff", "ok"} (m; diff = model -
  observed; ok = |diff| <= tol_m); overtopped_by: "observed" = the ISO time, "model" = ISO
  clock time of the first crossing of ``above_bank`` (None if never), "diff" = hours (model -
  observed, negative = earlier), ok = model is not None and diff <= 0.
- ``depth_class_areas(h_max, cell_m, edges, mask=None)`` -> [{"lo", "hi", "area_km2"}] for
  classes (lo, hi], the last one open (hi None); NaN and cells outside ``mask`` ignored.
- ``long_profile(cells_rc, bed, bank, h_max, cell_m)`` -> {"distance_km", "bed_m", "bank_m",
  "max_surface_m"} lists along the ordered cells; distance 0 at the first cell, steps of
  cell_m (4-neighbour) or cell_m * sqrt(2) (diagonal); max_surface = bed + h_max.

Script ``scripts/case_pohang_figures.py`` writes into the case folder: ``fig_study_area.png``,
``fig_rain.png``, ``fig_hyeto_hydrograph.png``, ``fig_lower_reach_depth.png``,
``fig_point_series.png``, ``fig_long_profile.png``, ``fig_depth_classes.png``,
``fig_model_chain.png`` and ``figures_pack.json`` {"badge": "미검증", "clock_start",
"clock_anchor_note", "validation": rows, "depth_classes", "other_models", "not_compared",
"figures": [{"file", "caption_ko", "idea_from"}]}; it also writes ``literature_check``
{"source", "clock_start", "rows", "n_ok", "n_rows", "warning"} into ``summary.json`` and
appends A27/A28 to its assumptions (idempotent; ``n_assumptions`` follows). The check is
diagnostic only: nothing is tuned to it. No key containing krw / usd / loss.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

OBS_PATH = ROOT / "assets/libraries/case_pohang_observations.json"
FIGS = [
    "fig_study_area.png",
    "fig_rain.png",
    "fig_hyeto_hydrograph.png",
    "fig_lower_reach_depth.png",
    "fig_point_series.png",
    "fig_long_profile.png",
    "fig_depth_classes.png",
    "fig_model_chain.png",
]


def _obs() -> dict:
    return json.loads(OBS_PATH.read_text(encoding="utf-8"))


def test_observation_config() -> None:
    o = _obs()
    assert "10.3741/JKWRA.2024.57.7.451" in o["source"]["doi"]
    assert o["source"]["citation"] and o["source"]["license"]
    pk = o["rain_peak_hour"]
    assert pk["start"] == "2022-09-06T04:00" and pk["end"] == "2022-09-06T05:00"
    assert pk["mm"] == pytest.approx(79.3)
    ids = [x["id"] for x in o["observations"]]
    assert {"indeok_overtopped", "apt_0630", "apt_0645"} <= set(ids) and len(ids) == len(set(ids))
    for x in o["observations"]:
        assert x["kind"] in ("depth_at_time", "overtopped_by") and x["point"] and x["note_ko"]
        datetime.fromisoformat(x["when"])
        if x["kind"] == "depth_at_time":
            assert x["value_m"] > 0 and x["tol_m"] > 0
    by = {x["id"]: x for x in o["observations"]}
    assert by["apt_0630"]["value_m"] == pytest.approx(0.3)
    assert by["apt_0645"]["value_m"] == pytest.approx(0.5)
    assert by["indeok_overtopped"]["when"] == "2022-09-06T06:20"
    assert o["not_compared"] and all(x["what_ko"] and x["why_ko"] for x in o["not_compared"])
    names = " ".join(m["name"] for m in o["other_models"])
    assert "TUFLOW" in names and "HEC-RAS" in names
    for m in o["other_models"]:
        assert m["grid_m"] > 0 and m["inputs_ko"] and m["max_depth_ko"] and m["validation_ko"]
        assert m["source"]
    a = o["assumptions"]
    assert len(a) == 2 and a[0].startswith("A27 ") and a[1].startswith("A28 ")


def test_peak_window_and_anchor() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_pohang_figures")
    inc = np.array([1.0, 1.0, 5.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 2.0, 1.0, 1.0])  # 10-min steps
    # best 60-min window = steps 3..8 -> 0.5 h .. 1.5 h -> centre 1.0 h
    assert f.peak_window_center_h(inc, 10.0) == pytest.approx(1.0)
    assert f.peak_window_center_h(np.ones(12), 10.0) == pytest.approx(0.5)  # first on ties
    start = f.anchor_start(6.0, "2022-09-06T04:00", "2022-09-06T05:00")
    assert start == datetime(2022, 9, 5, 22, 30)
    assert f.to_clock(7.5, start) == datetime(2022, 9, 6, 6, 0)


def test_series_helpers() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_pohang_figures")
    start = datetime(2022, 9, 6, 0, 0)
    t = np.array([0.0, 1.0, 2.0, 3.0])
    d = np.array([0.0, 0.0, 1.0, 0.5])
    assert f.depth_at_clock(t, d, start, "2022-09-06T01:30") == pytest.approx(0.5)
    assert f.depth_at_clock(t, d, start, datetime(2022, 9, 6, 2, 30)) == pytest.approx(0.75)
    assert math.isnan(f.depth_at_clock(t, d, start, "2022-09-06T04:00"))
    assert math.isnan(f.depth_at_clock(t, d, start, "2022-09-05T23:00"))
    above = np.array([-2.0, -1.0, 1.0, 0.5])
    assert f.first_crossing_h(t, above) == pytest.approx(1.5)
    assert f.first_crossing_h(t, above, level=5.0) is None
    assert f.first_crossing_h(t, np.array([1.0, 2.0, 3.0, 4.0])) == pytest.approx(0.0)


def test_validation_rows() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_pohang_figures")
    start = datetime(2022, 9, 6, 0, 0)
    t = np.array([0.0, 1.0, 2.0, 3.0])
    series = {
        "apt": {"t_h": t, "depth": np.array([0.0, 0.0, 1.0, 0.5]), "above_bank": None},
        "bridge": {
            "t_h": t,
            "depth": np.array([1.0, 2.0, 3.0, 2.0]),
            "above_bank": np.array([-2.0, -1.0, 1.0, 0.5]),
        },
        "dry": {"t_h": t, "depth": np.zeros(4), "above_bank": np.full(4, -1.0)},
    }
    obs = [
        {"id": "a", "kind": "depth_at_time", "point": "apt", "when": "2022-09-06T01:30",
         "value_m": 0.45, "tol_m": 0.1},
        {"id": "b", "kind": "depth_at_time", "point": "apt", "when": "2022-09-06T02:00",
         "value_m": 0.5, "tol_m": 0.1},
        {"id": "c", "kind": "overtopped_by", "point": "bridge", "when": "2022-09-06T02:00"},
        {"id": "d", "kind": "overtopped_by", "point": "bridge", "when": "2022-09-06T01:00"},
        {"id": "e", "kind": "overtopped_by", "point": "dry", "when": "2022-09-06T02:00"},
    ]  # fmt: skip
    rows = f.validation_rows(obs, series, start)
    assert [r["id"] for r in rows] == ["a", "b", "c", "d", "e"]
    a, b, c, d, e = rows
    assert a["model"] == pytest.approx(0.5) and a["diff"] == pytest.approx(0.05) and a["ok"]
    assert b["model"] == pytest.approx(1.0) and b["diff"] == pytest.approx(0.5) and not b["ok"]
    assert c["model"] == "2022-09-06T01:30" and c["diff"] == pytest.approx(-0.5) and c["ok"]
    assert d["diff"] == pytest.approx(0.5) and d["ok"] is False
    assert e["model"] is None and e["ok"] is False
    json.dumps(rows)  # serialisable


def test_depth_classes_and_profile() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_pohang_figures")
    h = np.array([[0.0, 0.3, 0.5], [0.6, 2.5, np.nan]])
    out = f.depth_class_areas(h, 1000.0, [0.25, 0.5, 2.0])
    assert [(r["lo"], r["hi"]) for r in out] == [(0.25, 0.5), (0.5, 2.0), (2.0, None)]
    assert [r["area_km2"] for r in out] == pytest.approx([2.0, 1.0, 1.0])
    mask = np.array([[True, False, True], [True, True, True]])
    out2 = f.depth_class_areas(h, 1000.0, [0.25, 0.5, 2.0], mask=mask)
    assert [r["area_km2"] for r in out2] == pytest.approx([1.0, 1.0, 1.0])

    bed = np.array([[5.0, 4.0, 9.0], [9.0, 9.0, 3.0]])
    bank = bed + 2.0
    hm = np.array([[1.0, 3.0, 0.0], [0.0, 0.0, 0.5]])
    p = f.long_profile([(0, 0), (0, 1), (1, 2)], bed, bank, hm, 30.0)
    np.testing.assert_allclose(p["distance_km"], [0.0, 0.03, 0.03 + 0.03 * math.sqrt(2)])
    assert p["bed_m"] == pytest.approx([5.0, 4.0, 3.0])
    assert p["bank_m"] == pytest.approx([7.0, 6.0, 5.0])
    assert p["max_surface_m"] == pytest.approx([6.0, 7.0, 3.5])


CASE = Path(os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk")))
CASE = CASE / "derived/case_twins/pohang"


def _walk_keys(o: object) -> list[str]:
    if isinstance(o, dict):
        return [*o.keys(), *[k for v in o.values() for k in _walk_keys(v)]]
    if isinstance(o, list):
        return [k for v in o for k in _walk_keys(v)]
    return []


def test_built_pack() -> None:
    pack_file = CASE / "figures_pack.json"
    if not pack_file.exists():
        pytest.skip("figure pack not built here")
    pack = json.loads(pack_file.read_text(encoding="utf-8"))
    assert pack["badge"] == "미검증"
    datetime.fromisoformat(pack["clock_start"])
    assert pack["clock_anchor_note"]
    assert [x["file"] for x in pack["figures"]] == FIGS
    for x in pack["figures"]:
        assert x["caption_ko"] and x["idea_from"]
        assert (CASE / x["file"]).stat().st_size > 5000
    ids = {r["id"] for r in pack["validation"]}
    assert {"indeok_overtopped", "apt_0630", "apt_0645"} <= ids
    assert pack["depth_classes"] and pack["other_models"] and pack["not_compared"]
    assert not [k for k in _walk_keys(pack) if any(w in k.lower() for w in ("krw", "usd", "loss"))]

    s = json.loads((CASE / "summary.json").read_text(encoding="utf-8"))
    lc = s["literature_check"]
    assert lc["n_rows"] == len(lc["rows"]) and 0 <= lc["n_ok"] <= lc["n_rows"]
    assert isinstance(lc["warning"], bool) and lc["warning"] == (lc["n_ok"] < lc["n_rows"])
    assert lc["clock_start"] == pack["clock_start"]
    assert s["n_assumptions"] == len(s["assumptions"])
    assert sum(a.startswith(("A27 ", "A28 ")) for a in s["assumptions"]) == 2
