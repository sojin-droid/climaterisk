"""Render the visual-board colour chart from assets/libraries/board_palettes.json.

Usage (needs matplotlib, e.g. the project's .climada-env)::

    python scripts/board_color_chart.py \
        assets/libraries/board_palettes.json docs/img/board_colors.png

Reads every colour and break from the spec (no colour is defined here) so the chart in
docs/BOARD_COLORS_KO.md cannot drift from what the board draws.
"""

from __future__ import annotations

import argparse
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

PERIL_NAMES_KO = {
    "river_flood": "하천홍수",
    "coastal_flood": "해안침수",
    "tc_surge": "해일",
    "tc_rain": "태풍 강우",
    "pluvial_flood": "내수침수(트윈)",
    "tropical_cyclone": "태풍 바람",
    "european_windstorm": "유럽폭풍",
    "hail": "우박",
    "heatwave": "폭염",
    "heat_mortality": "열 사망",
    "drought": "가뭄",
    "low_flow": "저유량",
    "crop_yield": "작물 수확",
    "wildfire": "산불",
    "landslide": "산사태",
    "earthquake": "지진",
}
FONT = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"


def _fmt(x: float, percent: bool) -> str:
    return f"{x * 100:g}%" if percent else f"{x:g}"


def swatches(spec: dict[str, Any], name: str) -> list[tuple[str, str]]:
    """(colour, label) per legend entry of one quantity, lowest first, incl. the zero swatch."""
    q = spec["quantities"][name]
    zero = spec["shared"]["zero"]["color"]
    family = "water" if q["family"] == "peril" else q["family"]
    ramp = spec["families"][family]["ramp"]
    if "stages" in q:
        staged = [(ramp[s["step"]], s["name"]) for s in q["stages"]]
        return [(zero, q["above_last"]["name"]), *staged[::-1]]
    br, pct = q["breaks"], name == "loss_ratio"
    labels = [f"≤{_fmt(br[0], pct)}"]
    labels += [f"{_fmt(a, pct)}–{_fmt(b, pct)}" for a, b in pairwise(br)]
    labels.append(f">{_fmt(br[-1], pct)}")
    items = [(ramp[s], text) for s, text in zip(q["steps"], labels, strict=True)]
    return [(zero, "0"), *items] if q["zero_means_none"] else items


def render(spec: dict[str, Any], out: Path) -> None:
    """Draw every family, its quantities, the shared states and the change palette."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    if Path(FONT).exists():
        font_manager.fontManager.addfont(FONT)
        plt.rcParams["font.family"] = "AppleGothic"
    shared, quantities = spec["shared"], spec["quantities"]
    rows: list[tuple[str, str]] = []
    for fam in spec["families"]:
        rows.append(("family", fam))
        rows += [("quantity", k) for k, q in quantities.items() if q["family"] == fam]
    rows += [("family", "peril"), ("quantity", "loss_ratio"), ("change", "")]
    fig, ax = plt.subplots(figsize=(15, 0.42 * len(rows) + 1.6))
    ax.set_xlim(0, 15)
    ax.set_ylim(len(rows) + 1.5, -1.2)
    ax.axis("off")
    title = "시각 보드 색 체계 — 색 = 재해 계열, 진하기 = 크기, 구간은 수치별 고정"
    ax.text(0, -0.7, title, fontsize=15, weight="bold")
    ax.text(0, -0.12, "공통:", fontsize=10)
    x = 0.6
    for key in ("null", "zero"):
        ax.add_patch(plt.Rectangle((x, -0.4), 0.5, 0.4, color=shared[key]["color"], ec="#999"))
        ax.text(x + 0.6, -0.12, shared[key]["label"], fontsize=10)
        x += 3.2
    ax.text(x, -0.12, "계산 불가(재현주기 > 기록 ½) = 칠하지 않고 버튼 끔", fontsize=10)
    y = 0.8
    for kind, key in rows:
        if kind == "family" and key == "peril":
            head = "손실 (모든 재해 공통 구간, 색은 그 재해 계열 — 예: 물)"
            ax.text(0, y, head, fontsize=12, weight="bold")
        elif kind == "family":
            ax.text(0, y, spec["families"][key]["label"], fontsize=12, weight="bold")
            names = [PERIL_NAMES_KO.get(p, p) for p, f in spec["perils"].items() if f == key]
            ax.text(4.2, y, " · ".join(names), fontsize=10, color="#444")
        elif kind == "change":
            ch = spec["change"]
            ax.text(0, y, "변화 지도 (모든 재해 공통, 미래 ÷ 기준)", fontsize=12, weight="bold")
            y += 1
            ax.text(0.2, y, "위험 감소 ←  → 위험 증가", fontsize=9)
            for i, (c, text) in enumerate(zip(ch["colors"], ch["labels"], strict=True)):
                ax.add_patch(plt.Rectangle((2.6 + i * 1.75, y - 0.35), 1.7, 0.5, color=c))
                ax.text(2.65 + i * 1.75, y + 0.42, text, fontsize=7.5)
        else:
            q = quantities[key]
            tag = "" if q["status"] == "in_use" else " (잠정)"
            ax.text(0.2, y, f"{q['label']} [{q['units']}]{tag}", fontsize=9.5)
            for i, (c, text) in enumerate(swatches(spec, key)):
                edge = "#ccc" if c == shared["zero"]["color"] else c
                ax.add_patch(plt.Rectangle((4.2 + i * 1.5, y - 0.32), 1.45, 0.45, color=c, ec=edge))
                ax.text(4.25 + i * 1.5, y + 0.42, text, fontsize=7.3, color="#333")
        y += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    render(json.loads(args.spec.read_text(encoding="utf-8")), args.out)


if __name__ == "__main__":
    main()
