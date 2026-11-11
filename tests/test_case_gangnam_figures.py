"""Gangnam case figure pack gate (reviewer-written; agents may not edit it).

Figures and a literature check for the Gangnam station rain-flood twin (fixed 95 mm/h state,
R6), in the style of the published Gangnam studies. The twin itself is not changed: the pack
reruns its existing design storm (``real_rebuild.storm_for_total``) at a few 2-hour totals.
Everything stays "미검증", no money, no flood trace and no rain-gauge series on any figure.
The published values come from one open paper and nothing is tuned to them:

    Keum, Seo, Choo (2022) "Evaluation of Inundation Probability and Inundation Depth through
    Rainfall-Runoff Analysis", Water 14(24), 4076, doi:10.3390/w14244076 (CC BY 4.0).
    Table 1 rainfall by duration and return period (Seoul observatory 1961-2015); Table 2
    flooded area (2D, 739.21 ha basin); 2-hour rain above 113 mm floods the cell in front of
    강남역 Exit 6 deeper than 11 cm.

Config ``assets/libraries/case_gangnam_literature.json``: ``source`` {"citation", "doi"
"10.3390/w14244076", "license"}; ``conditions_ko``; ``rain_table_mm`` with
``return_periods_years`` and the "60", "120", "180" rows (same length); ``judged_return_periods_
years`` [10, 30, 100]; ``flooded_area_2h_km2`` {basin_area_km2, return_periods_years, area_km2}
(reference only); ``exit6`` {name_ko, lat, lon inside the twin bbox, location_source,
onset_rain_2h_mm 113, depth_m 0.11, stair_heights_m, search_radius_m, paper_claim_ko};
``sweep_rain_2h_mm`` (increasing, contains 113); ``not_compared`` (>= 2 {what_ko, why_ko});
``assumptions``: two strings starting "A20 " and "A21 ", each ending with "]".

Module ``climaterisk_worker.twin.case_gangnam_figures`` (pure numpy for these):
- ``log_interp_rp(rps, values, t)`` -> value at return period ``t`` by linear interpolation in
  ln(T) (exact at the given periods; ``ValueError`` outside their range).
- ``rain_rows(paper_rps, paper_mm, our_rps, central, band_low, band_high, judged)`` -> one row
  per judged T: {"id": f"rain_2h_T{T}", "kind": "rain_mm", "label_ko", "unit": "mm",
  "observed": paper value, "model": our central (log-interpolated), "band": [low, high],
  "diff_pct": 100 * (model / observed - 1), "ok": low <= observed <= high}.
- ``max_near(h, mask, rows, cols, r_cells)`` -> max of ``h`` over cells with ``mask`` true
  within Chebyshev distance ``r_cells`` of (row, col); NaN if none.
- ``onset_rain(mm, depth, level)`` -> first total (linear interpolation between sweep points)
  at which ``depth`` reaches ``level``; None if never; the first total if it starts at/above.
- ``exit6_row(depth_at_onset, level)`` -> {"id": "exit6_depth_at_113mm", "kind":
  "depth_at_rain", "label_ko", "unit": "m", "observed": level, "model": depth, "ok": depth >=
  level}.

Script ``scripts/case_gangnam_figures.py`` (CLIMADA worker env, offline) builds the R6 twin
with the fixed 95 mm/h sewer exactly as ``scripts/twin_fixed95.py``, runs the design storm at
every ``sweep_rain_2h_mm`` total and at our central 10/30/100/500-year totals from
``derived/synthetic_city/rain_frequency.json`` (no rain-gauge file is read), and writes into
``derived/case_twins/gangnam``: the PNGs in ``FIGS``, ``figures_pack.json`` {"badge": "미검증",
"exit6": {"row", "sweep": [{"rain_mm", "depth_m"}], "onset_rain_mm"}, "rain_rows",
"flooded_area": {"ours": [{"return_period_years", "rain_mm", "flooded_km2", "site_km2"}],
"paper": ...}, "not_compared", "figures": [{"file", "caption_ko", "idea_from"}]}; it writes
``literature_check`` {"source", "conditions_ko", "rows" (three rain rows then the exit6 row),
"n_ok", "n_rows", "warning"} into ``summary.json`` and appends A20/A21 to its assumptions
(idempotent; ``n_assumptions`` follows). Existing summary keys are kept. No key containing
krw / usd / loss. The check is diagnostic only.
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
LIT = json.loads(
    (ROOT / "assets/libraries/case_gangnam_literature.json").read_text(encoding="utf-8")
)
CITY = json.loads((ROOT / "assets/libraries/synthetic_city_spec.json").read_text(encoding="utf-8"))
FIGS = [
    "fig_study_area.png",
    "fig_rain_frequency.png",
    "fig_ladder_depth.png",
    "fig_exit6_curve.png",
    "fig_flooded_area.png",
    "fig_validation.png",
    "fig_model_chain.png",
]


def test_config() -> None:
    assert LIT["source"]["doi"] == "10.3390/w14244076" and LIT["source"]["license"]
    assert LIT["conditions_ko"]
    rt = LIT["rain_table_mm"]
    n = len(rt["return_periods_years"])
    assert all(len(rt[k]) == n for k in ("60", "120", "180"))
    assert rt["120"][rt["return_periods_years"].index(10)] == 111.2
    assert LIT["judged_return_periods_years"] == [10, 30, 100]
    fa = LIT["flooded_area_2h_km2"]
    assert len(fa["area_km2"]) == len(fa["return_periods_years"]) and fa["basin_area_km2"] > 7
    e = LIT["exit6"]
    w, s, east, north = CITY["site"]["bbox"]
    assert w < e["lon"] < east and s < e["lat"] < north
    assert e["onset_rain_2h_mm"] == 113.0 and e["depth_m"] == 0.11 and e["location_source"]
    sw = LIT["sweep_rain_2h_mm"]
    assert sw == sorted(sw) and 113 in sw
    assert len(LIT["not_compared"]) >= 2
    a = LIT["assumptions"]
    assert [x[:4] for x in a] == ["A20 ", "A21 "] and all(x.endswith("]") for x in a)


def test_log_interp_and_rain_rows() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_gangnam_figures")
    rps = [10.0, 100.0]
    assert f.log_interp_rp(rps, [100.0, 200.0], 10.0) == pytest.approx(100.0)
    # ln-midpoint of 10 and 100 is T = sqrt(1000)
    assert f.log_interp_rp(rps, [100.0, 200.0], 1000**0.5) == pytest.approx(150.0)
    with pytest.raises(ValueError):
        f.log_interp_rp(rps, [100.0, 200.0], 500.0)
    rows = f.rain_rows(
        [10, 30, 100],
        [111.2, 135.9, 162.5],
        [10.0, 30.0, 100.0],
        [101.5, 120.4, 138.6],
        [92.3, 105.3, 115.1],
        [109.9, 140.0, 162.2],
        [10, 30, 100],
    )
    assert [r["id"] for r in rows] == ["rain_2h_T10", "rain_2h_T30", "rain_2h_T100"]
    assert [r["ok"] for r in rows] == [False, True, False]
    r = rows[0]
    assert r["kind"] == "rain_mm" and r["unit"] == "mm" and r["label_ko"]
    assert r["observed"] == 111.2 and r["model"] == pytest.approx(101.5)
    assert r["band"] == pytest.approx([92.3, 109.9])
    assert r["diff_pct"] == pytest.approx(100 * (101.5 / 111.2 - 1))


def test_max_near_onset_and_exit_row() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_gangnam_figures")
    h = np.arange(25, dtype=float).reshape(5, 5)
    mask = np.ones((5, 5), bool)
    mask[3, 3] = False
    assert f.max_near(h, mask, 2, 2, 1) == 17.0  # (3, 3) = 18 is a wall
    assert f.max_near(h, mask, 0, 0, 0) == 0.0
    assert np.isnan(f.max_near(h, np.zeros((5, 5), bool), 2, 2, 1))
    mm = [60.0, 100.0, 140.0]
    assert f.onset_rain(mm, [0.0, 0.05, 0.15], 0.11) == pytest.approx(124.0)
    assert f.onset_rain(mm, [0.0, 0.05, 0.10], 0.11) is None
    assert f.onset_rain(mm, [0.2, 0.3, 0.4], 0.11) == 60.0
    row = f.exit6_row(0.08, 0.11)
    assert row["id"] == "exit6_depth_at_113mm" and row["kind"] == "depth_at_rain"
    assert row["observed"] == 0.11 and row["model"] == 0.08 and row["ok"] is False
    assert row["unit"] == "m" and row["label_ko"]
    assert f.exit6_row(0.11, 0.11)["ok"] is True


DIR = (
    Path(os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk")))
    / "derived/case_twins/gangnam"
)


def _no_money(obj: object) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert not any(t in k.lower() for t in ("krw", "usd", "loss")), k
            _no_money(v)
    elif isinstance(obj, list):
        for v in obj:
            _no_money(v)


@pytest.mark.skipif(not (DIR / "figures_pack.json").exists(), reason="figure pack not built")
def test_built_pack() -> None:
    for name in FIGS:
        assert (DIR / name).stat().st_size > 10_000, name
    pack = json.loads((DIR / "figures_pack.json").read_text(encoding="utf-8"))
    s = json.loads((DIR / "summary.json").read_text(encoding="utf-8"))
    _no_money(pack)
    _no_money(s)
    assert pack["badge"] == "미검증" and s["badge"] == "미검증"
    assert [f["file"] for f in pack["figures"]] == FIGS
    assert all(f["caption_ko"] and f["idea_from"] for f in pack["figures"])
    sweep = pack["exit6"]["sweep"]
    assert [p["rain_mm"] for p in sweep] == [float(x) for x in LIT["sweep_rain_2h_mm"]]
    d113 = next(p["depth_m"] for p in sweep if p["rain_mm"] == 113.0)
    lc = s["literature_check"]
    ids = [r["id"] for r in lc["rows"]]
    assert ids == ["rain_2h_T10", "rain_2h_T30", "rain_2h_T100", "exit6_depth_at_113mm"]
    assert lc["rows"][3]["model"] == pytest.approx(d113)
    assert lc["n_rows"] == 4 and lc["n_ok"] == sum(r["ok"] for r in lc["rows"])
    assert lc["warning"] is (lc["n_ok"] < lc["n_rows"])
    assert lc["source"]["doi"] == "10.3390/w14244076"
    # rain rows use our stored frequency curve (no gauge file is read here)
    rf = json.loads(
        (DIR.parents[1] / "synthetic_city/rain_frequency.json").read_text(encoding="utf-8")
    )
    central = dict(zip(rf["return_periods_years"], rf["return_level_mm"]["central"], strict=True))
    for r in lc["rows"][:3]:
        t = float(r["id"].split("T")[1])
        assert r["model"] == pytest.approx(central[t], rel=1e-9)
    assert sum(a.startswith("A20 ") for a in s["assumptions"]) == 1
    assert sum(a.startswith("A21 ") for a in s["assumptions"]) == 1
    assert s["n_assumptions"] == len(s["assumptions"])
    assert all(a.endswith("]") for a in s["assumptions"])
    # the sweep is monotone in rain (same storm shape, more water) within solver noise
    d = np.array([p["depth_m"] for p in sweep])
    assert np.all(np.diff(d) >= -0.01)
