"""Board gate: the Pohang case page lists the paper-style figure pack (reviewer-written;
agents may not edit it).

The twin branch writes eight ``fig_*.png`` (figure pack) plus ``fig_propagation.png`` and
``fig_flooded_area_time.png`` (depth at clock times, flooded area per minute) into the case
folder. The board serves only files
listed in ``assets/libraries/board_cases.json``, so the config lists them (after the two
original figures, which stay first). Captions say what the figure is, in Korean; the
long-profile caption says suspect cells are marked, not removed.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "assets/libraries/board_cases.json").read_text(encoding="utf-8"))

PACK = [
    "fig_study_area.png",
    "fig_rain.png",
    "fig_hyeto_hydrograph.png",
    "fig_lower_reach_depth.png",
    "fig_point_series.png",
    "fig_long_profile.png",
    "fig_depth_classes.png",
    "fig_model_chain.png",
]
PROPAGATION = ["fig_propagation.png", "fig_flooded_area_time.png"]


def test_pohang_lists_figure_pack() -> None:
    pohang = next(c for c in CFG["cases"] if c["id"] == "pohang")
    files = [f["file"] for f in pohang["figures"]]
    assert files[:2] == ["max_depth_map.png", "hydrograph_mouth.png"]
    assert files[2:10] == PACK and files[10:] == PROPAGATION and len(files) == len(set(files))
    cap = {f["file"]: f["caption_ko"] for f in pohang["figures"]}
    assert all(len(cap[f]) >= 8 for f in PACK + PROPAGATION)
    # clock times rest on the anchoring assumption; the captions say so
    assert all("가정" in cap[f] for f in PROPAGATION)
    assert "표시" in cap["fig_long_profile.png"]
    # no money wording in any caption
    assert not any(w in c for c in cap.values() for w in ("원", "USD", "피해액"))
