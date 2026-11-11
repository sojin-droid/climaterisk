"""Yeongdeungpo case figure pack gate (reviewer-written; agents may not edit it).

Figures and a literature check in the style of published Seoul street-heat studies, made from
the EXISTING heat twin (same model, rerun only to keep its mean radiant temperature, sky view
factor and sun masks next to the UTCI maps). Everything stays "미검증", no money. The published
values come from one open paper and nothing is tuned to them:

    Kim, Yun, Park, Heo, Lee (2022) "Estimation of Mean Radiant Temperature in Urban Canyons
    Using Google Street View: A Case Study on Seoul", Remote Sens. 14(2), 260,
    doi:10.3390/rs14020260 (CC BY 4.0). City map of street MRT on 2018-06-19 10:00; MRT risk
    class edges 47.6 / 55.5 / 59.4 degC; high MRT (> 59.4) where SVF is 0.6-1.0 (open space,
    compact low-rise), low MRT (< 47.6) in high-density / tree areas; Table A2 lists point
    values (urban form, SVF, MRT), one of them in 문래동 4가, Yeongdeungpo-gu.

The paper's conditions (June, 10:00) are cooler than ours (hottest August afternoons), so only
claims that a hotter day cannot fake are judged; the rest go to ``not_compared`` with a reason.

Spec ``assets/libraries/case_yeongdeungpo_spec.json``:
- ``literature``: ``source`` {"citation", "doi" "10.3390/rs14020260", "license"};
  ``conditions_ko`` (says 2018-06-19 and 10시); ``mrt_class_edges_c`` [47.6, 55.5, 59.4];
  ``svf_high`` 0.6, ``svf_low`` 0.4; ``reference_points``: Table A2 rows
  {"place", "urban_form", "svf", "mrt_c"} (at least 8, one place containing "문래");
  ``not_compared``: list of {"what_ko", "why_ko"} (at least 2).
- ``figures``: {"utci_edges_c": [32, 38, 46]} (Bröde et al. 2012 strong / very strong /
  extreme heat stress).
- ``assumptions`` contains exactly one string starting "A17 " (the paper's MRT class edges are
  used as fixed reference values under different weather; never tuned).

Module ``climaterisk_worker.twin.case_heat_figures`` (pure numpy for these):
- ``class_shares(values, edges, mask=None)`` -> [{"lo", "hi", "share"}] for classes
  (-inf, e0], (e0, e1], ..., (e_last, inf): lo of the first and hi of the last are None;
  shares over finite values inside ``mask``; they sum to 1 (all 0 when nothing is counted).
- ``binned_stats(x, y, edges, min_n=1)`` -> {"centre", "median", "p25", "p75", "n"} lists, one
  per bin [e_i, e_i+1) with the last bin closed; NaN y ignored; stats NaN when n < min_n.
- ``svf_contrast(mrt, svf, mask, svf_high, svf_low)`` -> median MRT over mask & svf >= svf_high
  minus median over mask & svf < svf_low; NaN when either side is empty.
- ``literature_rows(days, svf, open_site, lit)`` with ``days[date] = {"mrt", "sunlit"}`` ->
  exactly two rows, in order:
  {"id": "mrt_svf_contrast", "kind": "contrast_c", "label_ko", "unit": "°C",
   "observed": "> 0", "model": min over days of svf_contrast over ``open_site``,
   "per_day": {date: value}, "ok": every day > 0};
  {"id": "mrt_sunlit_open", "kind": "above_c", "label_ko", "unit": "°C",
   "observed": 59.4 (the last class edge), "model": min over days of the median MRT over
   open_site & sunlit & svf >= svf_high, "per_day", "ok": model > observed,
   "note_ko" saying a hotter day favours agreement}.

Twin script ``scripts/case_yeongdeungpo.py`` additionally saves into ``maps.npz``: ``svf``,
``height_m`` (main run), ``surface_class`` (0 other, 1 road, 2 green), ``site_cells``,
``lat``, ``lon`` (cell centres) and per date ``mrt_<date>``, ``sunlit_<date>``.

Figure script ``scripts/case_yeongdeungpo_figures.py`` writes into the case folder the PNGs in
``FIGS`` and ``figures_pack.json`` {"badge": "미검증", "literature": rows, "mrt_classes",
"utci_classes", "reference_points", "not_compared", "figures": [{"file", "caption_ko",
"idea_from"}]}; it writes ``literature_check`` {"source", "conditions_ko", "rows", "n_ok",
"n_rows", "warning"} into ``summary.json`` (warning = any row not ok). It never changes the
assumptions (A17 comes from the spec through the twin script). No key containing krw / usd /
loss. The check is diagnostic only.
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
SPEC = json.loads(
    (ROOT / "assets/libraries/case_yeongdeungpo_spec.json").read_text(encoding="utf-8")
)
FIGS = [
    "fig_study_area.png",
    "fig_ring_days.png",
    "fig_height_svf.png",
    "fig_mrt_classes.png",
    "fig_mrt_vs_svf.png",
    "fig_utci_days.png",
    "fig_utci_classes.png",
    "fig_sensor_check.png",
    "fig_model_chain.png",
]


def test_spec_literature() -> None:
    lit = SPEC["literature"]
    assert lit["source"]["doi"] == "10.3390/rs14020260" and lit["source"]["citation"]
    assert lit["source"]["license"]
    assert "2018-06-19" in lit["conditions_ko"] and "10시" in lit["conditions_ko"]
    assert lit["mrt_class_edges_c"] == [47.6, 55.5, 59.4]
    assert lit["svf_high"] == 0.6 and lit["svf_low"] == 0.4
    pts = lit["reference_points"]
    assert len(pts) >= 8 and any("문래" in p["place"] for p in pts)
    for p in pts:
        assert p["urban_form"] and 0 <= p["svf"] <= 1 and 20 < p["mrt_c"] < 80
    assert len(lit["not_compared"]) >= 2
    assert all(n["what_ko"] and n["why_ko"] for n in lit["not_compared"])
    assert SPEC["figures"]["utci_edges_c"] == [32, 38, 46]
    a17 = [a for a in SPEC["assumptions"] if a.startswith("A17 ")]
    assert len(a17) == 1


def test_class_shares() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_heat_figures")
    v = np.array([10.0, 47.6, 50.0, 56.0, 60.0, 70.0, np.nan])
    r = f.class_shares(v, [47.6, 55.5, 59.4])
    assert [c["lo"] for c in r] == [None, 47.6, 55.5, 59.4]
    assert [c["hi"] for c in r] == [47.6, 55.5, 59.4, None]
    np.testing.assert_allclose([c["share"] for c in r], [2 / 6, 1 / 6, 1 / 6, 2 / 6], atol=1e-12)
    mask = np.array([True, True, False, False, False, False, True])
    np.testing.assert_allclose(
        [c["share"] for c in f.class_shares(v, [47.6, 55.5, 59.4], mask)], [1, 0, 0, 0]
    )
    empty = f.class_shares(v, [47.6], np.zeros(7, bool))
    assert [c["share"] for c in empty] == [0.0, 0.0]


def test_binned_stats() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_heat_figures")
    x = np.array([0.05, 0.15, 0.15, 0.15, 1.0, 0.5])
    y = np.array([1.0, 2.0, 4.0, 6.0, 9.0, np.nan])
    r = f.binned_stats(x, y, [0.0, 0.1, 0.2, 0.5, 1.0], min_n=1)
    np.testing.assert_allclose(r["centre"], [0.05, 0.15, 0.35, 0.75])
    assert r["n"] == [1, 3, 0, 1]
    assert r["median"][1] == 4.0 and r["median"][3] == 9.0 and np.isnan(r["median"][2])
    np.testing.assert_allclose([r["p25"][1], r["p75"][1]], [3.0, 5.0])
    r2 = f.binned_stats(x, y, [0.0, 0.1, 0.2, 0.5, 1.0], min_n=2)
    assert np.isnan(r2["median"][0]) and r2["median"][1] == 4.0


def test_svf_contrast_and_rows() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_heat_figures")
    svf = np.array([[0.9, 0.7, 0.3], [0.2, 0.5, 0.95]])
    mask = np.ones_like(svf, bool)
    mrt = np.array([[66.0, 62.0, 50.0], [46.0, 55.0, 70.0]])
    assert f.svf_contrast(mrt, svf, mask, 0.6, 0.4) == pytest.approx(66.0 - 48.0)
    assert np.isnan(f.svf_contrast(mrt, svf, svf > 0.6, 0.6, 0.4))
    lit = SPEC["literature"]
    sun = np.array([[True, True, False], [False, True, False]])
    days = {
        "d1": {"mrt": mrt, "sunlit": sun},
        "d2": {"mrt": mrt - 10.0, "sunlit": sun},
    }
    rows = f.literature_rows(days, svf, mask, lit)
    assert [r["id"] for r in rows] == ["mrt_svf_contrast", "mrt_sunlit_open"]
    c, a = rows
    assert c["kind"] == "contrast_c" and c["unit"] == "°C" and c["observed"] == "> 0"
    assert c["model"] == pytest.approx(18.0) and c["ok"] is True
    assert set(c["per_day"]) == {"d1", "d2"} and c["label_ko"]
    assert a["kind"] == "above_c" and a["observed"] == 59.4
    # sunlit & svf >= 0.6: d1 {66, 62} -> 64; d2 {56, 52} -> 54; min 54 < 59.4
    assert a["model"] == pytest.approx(54.0) and a["ok"] is False
    assert a["per_day"]["d1"] == pytest.approx(64.0) and a["note_ko"] and a["label_ko"]


DIR = (
    Path(os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk")))
    / "derived/case_twins/yeongdeungpo"
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
    lc = s["literature_check"]
    assert [r["id"] for r in lc["rows"]] == ["mrt_svf_contrast", "mrt_sunlit_open"]
    assert lc["n_rows"] == 2 and lc["n_ok"] == sum(r["ok"] for r in lc["rows"])
    assert lc["warning"] is (lc["n_ok"] < lc["n_rows"])
    assert lc["source"]["doi"] == "10.3390/rs14020260" and "2018-06-19" in lc["conditions_ko"]
    assert s["n_assumptions"] == len(s["assumptions"]) == len(SPEC["assumptions"])
    assert sum(a.startswith("A17 ") for a in s["assumptions"]) == 1
    for key in ("mrt_classes", "utci_classes"):
        for day in pack[key].values():
            assert sum(c["share"] for c in day) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.skipif(not (DIR / "maps.npz").exists(), reason="twin not built")
def test_maps_consistent() -> None:
    pytest.importorskip("pythermalcomfort")
    ch = pytest.importorskip("climaterisk_worker.twin.case_heat")
    z = np.load(DIR / "maps.npz")
    s = json.loads((DIR / "summary.json").read_text(encoding="utf-8"))
    need = {"svf", "height_m", "surface_class", "site_cells", "lat", "lon", "open_mask"}
    assert need <= set(z.files)
    site = z["site_cells"].astype(bool)
    assert site.any() and not (site & ~z["open_mask"].astype(bool)).any()
    assert set(np.unique(z["surface_class"])) <= {0, 1, 2}
    wind = float(SPEC["model"]["wind_10m_ms"])
    rng = np.random.default_rng(0)
    for d in s["days"]:
        date = d["date"]
        u, mrt, lit = z[f"utci_{date}"], z[f"mrt_{date}"], z[f"sunlit_{date}"].astype(bool)
        # the stored UTCI mean is what the summary reports
        np.testing.assert_allclose(
            float(np.nanmean(u[site])), s["main"]["per_day"][date]["utci_mean_c"], atol=0.002
        )
        np.testing.assert_allclose(
            float(np.mean(lit[site])), s["main"]["per_day"][date]["sunlit_share"], atol=0.002
        )
        # UTCI follows from the stored MRT with the ring air temperature, wind and humidity
        idx = rng.choice(np.flatnonzero(site.ravel()), size=50, replace=False)
        back = np.asarray(ch.utci(d["t_ring"], mrt.ravel()[idx].astype(float), wind, d["rh_ring"]))
        np.testing.assert_allclose(back, u.ravel()[idx], atol=0.05)
