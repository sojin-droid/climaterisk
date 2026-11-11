"""Board gate: the Yeongdeungpo and Gangnam case pages list their paper-style figure packs
(reviewer-written; agents may not edit it).

The twin branch writes nine ``fig_*.png`` for Yeongdeungpo (heat, literature check against
Kim et al. 2022) and seven for Gangnam (rain flood, literature check against Keum et al. 2022)
into the case folders. The board serves only files listed in
``assets/libraries/board_cases.json``, so the config lists them after the original figures,
which stay first. Captions are Korean, say what the figure is, never mention money, and say
"참고" / name the paper where the figure only shows published values for reference.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "assets/libraries/board_cases.json").read_text(encoding="utf-8"))

YD_ORIG = ["utci_map.png", "sensor_check.png"]
YD_PACK = [
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
GN_ORIG = ["threshold_map.png", "threshold_effect.png"]
GN_PACK = [
    "fig_study_area.png",
    "fig_rain_frequency.png",
    "fig_ladder_depth.png",
    "fig_exit6_curve.png",
    "fig_flooded_area.png",
    "fig_validation.png",
    "fig_model_chain.png",
]
MONEY = ("원", "USD", "피해액", "손실액")


def _case(cid: str) -> dict:
    return next(c for c in CFG["cases"] if c["id"] == cid)


def _check(cid: str, orig: list[str], pack: list[str]) -> dict[str, str]:
    c = _case(cid)
    files = [f["file"] for f in c["figures"]]
    assert files[: len(orig)] == orig and files[len(orig) :] == pack
    assert len(files) == len(set(files))
    cap = {f["file"]: f["caption_ko"] for f in c["figures"]}
    assert all(len(cap[f]) >= 8 for f in pack)
    assert not any(w in v for v in cap.values() for w in MONEY)
    return cap


def test_yeongdeungpo_lists_figure_pack() -> None:
    cap = _check("yeongdeungpo", YD_ORIG, YD_PACK)
    assert "Kim" in cap["fig_mrt_classes.png"] and "Kim" in cap["fig_mrt_vs_svf.png"]
    assert "진단" in cap["fig_sensor_check.png"]
    assert "가정" in cap["fig_utci_days.png"]


def test_gangnam_lists_figure_pack() -> None:
    cap = _check("gangnam", GN_ORIG, GN_PACK)
    assert "Keum" in cap["fig_rain_frequency.png"]
    assert "6번 출구" in cap["fig_exit6_curve.png"]
    assert "참고" in cap["fig_flooded_area.png"]
    # no flood-trace map on the board: the validation figure is scores only
    assert "흔적도" not in cap["fig_validation.png"]
