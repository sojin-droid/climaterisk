#!/usr/bin/env python
"""Read-only case card for the Gangnam station rain-flood twin (fixed 95 mm/h state, R6).

Nothing is simulated here. The card only reads the existing results of
``scripts/twin_fixed95.py`` (``real_summary.json`` and ``real_buildings.csv``), checks every
percentage it recomputes against the tables of
``docs/reports/twin_threshold_fixed95_limits_ko.md`` and writes, for the visual board::

    <DATA_ROOT>/derived/case_twins/gangnam/{summary.json, threshold_map.png, threshold_effect.png}

Hard rules: no money (percent / ratio only, no key with usd, krw or loss), no flood trace on
a figure and no per-building observed flag, no rain-gauge series, only the 243 buildings of
known OSM use are evaluated (the other 637 are outlines labelled "용도 미상 · 평가 제외").

Runs in the CLIMADA worker environment::

    ./.climada-env/bin/python scripts/case_gangnam_card.py [--palettes PATH] [--out DIR]

Algorithm:
    Site EAL change for threshold $T$ (dimensionless, shown in %):
    $$\\Delta(T) = \\frac{\\sum_i EAL_i(T)}{\\sum_i EAL_i(\\text{none})} - 1$$
    per building the map colours the ratio $r_i = EAL_i(T)/EAL_i(\\text{none})$ by the fixed
    ``change`` classes of the board palette. $i$ runs over the 243 evaluated buildings;
    $EAL_i$ is only used as a ratio, so its unit cancels.
    ASCII: delta(T) = sum(EAL(T)) / sum(EAL(none)) - 1 ; r_i = EAL_i(T) / EAL_i(none).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

logger = logging.getLogger(__name__)

NAME = "강남역 일대 · 집중호우 (2022-08-08 보정, 빈도 강우)"
BADGE = "미검증"
THRESHOLDS = (0.05, 0.15, 0.30, 0.60)
MAP_THRESHOLD = 0.15
RETURN_PERIODS = (10, 30, 100, 500)
DEPTH_BANDS = (
    ("0-5", 0.0, 0.05),
    ("5-15", 0.05, 0.15),
    ("15-30", 0.15, 0.30),
    ("30-60", 0.30, 0.60),
    (">60", 0.60, math.inf),
)
USE_ORDER = ("residential", "commercial", "industrial")
DEFAULT_PALETTES = Path(
    "/Users/junsojin/Developer/climaterisk-board/assets/libraries/board_palettes.json"
)
REPORT = REPO / "docs" / "reports" / "twin_threshold_fixed95_limits_ko.md"
RESULT_DIR = Path.home() / "Data/climaterisk/derived/synthetic_city/fixed95"
OUT_DIR = Path.home() / "Data/climaterisk/derived/case_twins/gangnam"
R = "twin_threshold_fixed95_limits_ko.md"
S = "scripts/synthetic_city.py REAL_ASSUMPTIONS"

ASSUMPTIONS = [
    f"하수 용량 95 mm/h(서울 설계 수준, 보정 안 함), 대상지 전체에 한 값으로 균일하게 뺌 [{R} §1, §4-2]",
    f"하수관망(관 위치·크기·경사·빗물받이·펌프장) 자료 없음; 녹지도 도로와 똑같이 하수로 빠지고 역류·막힘은 없다고 봄 [{R} §4-2]",
    f"지형은 30 m 위성 표면모델(GLO-30)을 10 m로 보간하고 OSM 건물 자리를 지워 주변 값으로 채움; OSM에 없는 건물은 지형에 남음(수직 오차 m 단위) [{R} §4-1]",
    f"건물은 문·창이 없는 벽으로 둠 [{S}]",
    f"건물 앞 침수 깊이 = 건물 둘레 도로의 최대 수심; 1층 바닥 높이·출입구 문턱·차수판·출입구 위치 없음 [{R} §4-6]",
    f"OSM 녹지 곡선번호(CN) 74는 토양군 C 가정(토양도 없음), 녹지 밖은 전부 포장면(지붕 포함)으로 봄 [{S}]",
    f"R6 상태: 지하층 없음, 지하 피해는 평가에서 제외(가정하지 않기 위해) [{R} §3, §4-6]",
    f"빈도별 강우: 대상지에서 약 7 km 떨어진 서울 ASOS 108 한 지점의 2시간 강우, 연최대값 GEV 분포의 중앙값 [{R} §1, §4-4]",
    f"정해진 시간 분포(설계 우량주상도) 하나를 쓰고 대상지 전체에 같은 비가 온다고 봄; 신뢰구간(부트스트랩)은 이 비교에 반영하지 않음 [{R} §4-4]",
    f"상류 유입: 대상지 바깥 1.5 km는 전부 포장면, 도착 시간 지연 없음, 같은 하수 용량 적용 [{R} §4-4]",
    f"2022-08-08 강우는 1.5~3 km 떨어진 서울시 강우량계 3곳의 평균을 대표값으로 봄(내부 검증용, 값은 싣지 않음) [{R} §4-4]",
    f"2010·2011년은 강우 자료가 없어 사건 총량을 침수 면적에 맞춰 정함(어디로 물이 가는지만 보는 공간 점검) [{R} §4-3]",
    f"10 m 격자, 거칠기(Manning n)는 대상지 전체에 한 값; 침수가 생긴 계산 10회 모두 계산법 적용 범위(Froude 수 0.5 이상)를 넘어 참고값임 [{R} §4-5]",
    f"용도가 확인된 243동만 평가하고 용도를 가정하지 않음; 1층만 피해를 입는다고 봄 [{R} §1, §4-6]",
    f"건물 가치는 바닥면적 × 가정한 단가; 금액은 싣지 않고 % 비교만 사용 [{R} §3, §4-6]",
    f"피해곡선은 공개된 JRC 아시아 표(한국 자료 아님), 용도는 주거·상업·산업 세 가지만 구분 [{R} §1, §4-7]",
    f"pure 곡선은 0 m부터 피해가 생기는 '문턱 미반영' 기준선, climada 곡선은 0.05 m까지 피해 0이라 5 cm 문턱이 사실상 내장됨 [{R} §1, §3]",
    f"무피해문턱 0.05/0.15/0.30/0.60 m는 예시값(실측 아님)이고 모든 건물에 같은 값을 적용함 [{R} §4-7]",
    f"침수흔적 대조: 건물 10 m 안에 흔적이 있으면 침수로 침(버퍼는 가정), 흔적이 불완전할 수 있음 [{S}]",
]


# --------------------------------------------------------------------------- helpers
def eal_ratio(eal_with: Any, eal_none: Any) -> np.ndarray:
    """Per-building EAL ratio ``with / none``; NaN where the base is 0 (undefined).

    Args:
        eal_with: EAL with the threshold (any unit; only the ratio is used).
        eal_none: EAL without the threshold, same unit.

    Returns:
        Array of ratios, NaN where ``eal_none`` is 0 or not finite.
    """
    num = np.asarray(eal_with, dtype=float)
    den = np.asarray(eal_none, dtype=float)
    out = np.full(np.broadcast(num, den).shape, np.nan)
    ok = np.isfinite(den) & (den > 0) & np.isfinite(num)
    np.divide(num, den, out=out, where=ok)
    return out


def site_change_pct(eal_with: Any, eal_none: Any) -> float:
    """Site EAL change in percent: ``100 * (sum(with) / sum(none) - 1)``.

    Args:
        eal_with: Per-building EAL with the threshold.
        eal_none: Per-building EAL without it.

    Returns:
        Percentage change (negative = smaller EAL).
    """
    return float(100.0 * (np.nansum(eal_with) / np.nansum(eal_none) - 1.0))


def class_index(value: float, breaks: list[float]) -> int:
    """Palette class of a ratio: ``breaks[i-1] < v <= breaks[i]``, last class above all."""
    for i, b in enumerate(breaks):
        if value <= b:
            return i
    return len(breaks)


def _num(cell: str) -> tuple[float, int]:
    """Parse a report table cell like ``**−10.6 %**`` -> (-10.6, 1 decimal)."""
    m = re.search(r"[−-]?\s?\d+(?:\.(\d+))?", cell.replace("−", "-").replace("**", ""))
    if m is None:
        raise ValueError(f"no number in {cell!r}")
    text = m.group(0).replace(" ", "")
    return float(text), len(m.group(1) or "")


def report_tables(path: Path) -> dict[str, list[list[str]]]:
    """Cells of the markdown tables under the ``### 2-n`` headings of the report."""
    tables: dict[str, list[list[str]]] = {}
    key = None
    for line in path.read_text(encoding="utf-8").splitlines():
        h = re.match(r"###\s+(2-\d)", line)
        if h:
            key = h.group(1)
            tables[key] = []
        elif line.startswith("## "):
            key = None
        elif key and line.startswith("|") and not re.match(r"\|[-| ]+\|$", line.strip()):
            tables[key].append([c.strip() for c in line.strip().strip("|").split("|")])
    return tables


def _check(label: str, mine: float, cell: str, mismatches: list[str]) -> None:
    shown, decimals = _num(cell)
    if abs(mine - shown) > 0.5 * 10 ** (-decimals) + 1e-9:
        mismatches.append(f"{label}: recomputed {mine:.4f} vs report {cell!r}")


# --------------------------------------------------------------------------- data
def load_inputs() -> tuple[dict[str, Any], Any]:
    """``real_summary.json`` and the evaluated rows of ``real_buildings.csv``."""
    import pandas as pd

    summary = json.loads((RESULT_DIR / "real_summary.json").read_text(encoding="utf-8"))
    frame = pd.read_csv(RESULT_DIR / "real_buildings.csv", keep_default_na=True)
    return summary, frame


def compute(
    summary: dict[str, Any], frame: Any, tables: dict[str, list[list[str]]]
) -> dict[str, Any]:
    """Recompute every percentage, check it against the report, return the card pieces."""
    known = frame["known_use"].notna() & (frame["known_use"] != "")
    ev = frame[known]
    mism: list[str] = []
    cols = {t: f"{t:g}" for t in THRESHOLDS}

    def col(curve: str, t: float | None) -> str:
        return f"eal_{curve}_{'none' if t is None else f'T{t:g}'}_usd"

    effect: dict[str, Any] = {"thresholds_m": list(THRESHOLDS)}
    for ci, curve in enumerate(("pure", "climada")):
        vals = [site_change_pct(ev[col(curve, t)], ev[col(curve, None)]) for t in THRESHOLDS]
        effect[f"{curve}_eal_change_pct"] = [round(v, 3) for v in vals]
        for v, cell in zip(vals, tables["2-1"][1 + ci][1:], strict=True):
            _check(f"2-1 {curve}", v, cell, mism)

    by_use: dict[str, list[float]] = {}
    for i, use in enumerate(USE_ORDER):
        sub = ev[ev["known_use"] == use]
        vals = [site_change_pct(sub[col("pure", t)], sub[col("pure", None)]) for t in THRESHOLDS]
        by_use[use] = [round(v, 3) for v in vals]
        row = tables["2-3"][1 + i]
        for v, cell in zip(vals, row[2:], strict=True):
            _check(f"2-3 {use}", v, cell, mism)
        if int(row[1]) != len(sub):
            mism.append(f"2-3 {use}: n {len(sub)} vs {row[1]}")
    effect["pure_eal_change_pct_by_use"] = by_use

    # per return period (pure): from the groups of real_summary (per-use storm changes and base
    # storm values are combined as base-weighted sums; the base values never leave this function)
    groups = summary["threshold_compare"]["groups"]
    per_rp: dict[str, list[float]] = {}
    for r_i, rp in enumerate(RETURN_PERIODS):
        out: list[float] = []
        for t_i, t in enumerate(THRESHOLDS):
            allg = groups["all known use"]["pure"]["by_threshold"][cols[t]]["storm_pct_change"]
            out.append(float(allg[str(rp)]))
            _check(f"2-2 {rp}y T{t}", out[-1], tables["2-2"][1 + r_i][1 + t_i], mism)
        per_rp[str(rp)] = [round(v, 3) for v in out]
    effect["pure_change_pct_by_return_period"] = per_rp

    share: dict[str, dict[str, float]] = {}
    for r_i, rp in enumerate(RETURN_PERIODS):
        d = ev[f"entry_depth_T{rp}y_m"].to_numpy(dtype=float)
        share[str(rp)] = {}
        for b_i, (name, lo, hi) in enumerate(DEPTH_BANDS):
            inside = (d <= hi) if b_i == 0 else (d > lo) & (d <= hi)
            share[str(rp)][name] = round(float(100.0 * inside.mean()), 3)
            _check(f"2-4 {rp}y {name}", share[str(rp)][name], tables["2-4"][1 + r_i][1 + b_i], mism)
    n_known = int(known.sum())
    return {
        "effect": effect,
        "share": share,
        "n_evaluated": n_known,
        "by_use_counts": {u: int((ev["known_use"] == u).sum()) for u in USE_ORDER},
        "mismatches": mism,
    }


def model_state_table(tables: dict[str, list[list[str]]]) -> list[dict[str, Any]]:
    """The §2-5 table exactly as stated (integer percents of pure-curve EAL change)."""
    rows = []
    for row in tables["2-5"][1:]:
        rows.append(
            {
                "state": row[0].replace("**", ""),
                "eal_change_pct": [_num(c)[0] for c in row[1:]],
            }
        )
    return rows


def assemble(
    summary: dict[str, Any], calc: dict[str, Any], states: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the card summary (no money, no per-building flags, no gauge values)."""
    cal = summary["calibration"]
    csi_m = float(cal["curve"][0]["scores"]["csi"])
    csi_b = float(cal["lowest_ground_baseline"]["csi"])
    spatial = {}
    for year, sc in summary["spatial_checks"].items():
        spatial[year] = {
            "event": sc["name"],
            "csi_model": round(float(sc["scores"]["csi"]), 4),
            "csi_lowest_ground": round(float(sc["lowest_ground_baseline"]["csi"]), 4),
            "note": "강우 자료 없음: 침수 면적에 맞춘 공간 점검(어디로 물이 가는지만 봄)",
        }
    n_total = int(summary["n_buildings"])
    n_eval = calc["n_evaluated"]
    out: dict[str, Any] = {
        "name": NAME,
        "badge": BADGE,
        "badge_reason": (
            f"건물 단위 검증(2022-08-08)에서 트윈 CSI {csi_m:.2f}가 단순 규칙 '가장 낮은 건물이 "
            f"잠긴다' CSI {csi_b:.2f}보다 낮음; 하수 용량은 보정하지 않은 설계 수준 값"
        ),
        "cell_m": int(summary["grid"]["cell_m"]),
        "n_buildings": n_total,
        "n_evaluated": n_eval,
        "n_evaluated_by_use": calc["by_use_counts"],
        "n_excluded": n_total - n_eval,
        "excluded_label": "용도 미상 · 평가 제외",
        "check": {
            "csi_model": round(csi_m, 4),
            "csi_lowest_ground": round(csi_b, 4),
            "pod_model": round(float(cal["curve"][0]["scores"]["pod"]), 4),
            "far_model": round(float(cal["curve"][0]["scores"]["far"]), 4),
            "event": "2022-08-08 집중호우",
            "warning": True,
            "note": (
                "트윈이 단순 규칙보다 못함(적중 51 %, 오경보 59 %): 어느 건물이 잠기는지는 "
                "아직 맞히지 못하므로 문턱 비교는 방향만 참고"
            ),
        },
        "spatial_checks": spatial,
        "threshold_effect": {
            "basis": "문턱 없음 대비 용도 확인 243동 연평균 손실 변화(%), 다른 조건은 고정",
            **calc["effect"],
        },
        "entry_depth_share": {
            "unit": "243동 중 비율(%)",
            "bands_cm": [b[0] for b in DEPTH_BANDS],
            "by_return_period_years": calc["share"],
        },
        "model_state_sensitivity": {
            "basis": f"{R} §2-5, 표에 적힌 값 그대로(pure 곡선, 연평균 손실 변화 %)",
            "thresholds_m": list(THRESHOLDS),
            "rows": states,
        },
        "assumptions": ASSUMPTIONS,
        "n_assumptions": len(ASSUMPTIONS),
    }
    return out


# --------------------------------------------------------------------------- figures
def _font() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "Apple SD Gothic Neo",
        "AppleGothic",
        "Malgun Gothic",
        "NanumGothic",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def footprints(frame: Any) -> Any:
    """OSM footprints in site metres, row-aligned with ``real_buildings.csv``."""
    from climaterisk_worker.twin import site
    from climaterisk_worker.twin.spec import load_spec

    from climaterisk import paths

    spec = load_spec()
    bbox = tuple(float(v) for v in spec["site"]["bbox"])
    gdf = site.read_buildings(paths.osm_dir(spec["site"]["osm_file"]), bbox, spec)  # type: ignore[arg-type]
    if (
        len(gdf) != len(frame)
        or not (gdf["building"].to_numpy() == frame["osm_building"].to_numpy()).all()
    ):
        raise SystemExit("OSM footprints do not align row-by-row with real_buildings.csv")
    return gdf.to_crs("EPSG:5179")


def draw_map(frame: Any, gdf: Any, pal: dict[str, Any], out: Path) -> dict[str, int]:
    """Threshold map: EAL(T=0.15)/EAL(none), pure curve, palette ``change`` classes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    _font()
    ch, sh = pal["change"], pal["shared"]
    known = (frame["known_use"].notna() & (frame["known_use"] != "")).to_numpy()
    ratio = eal_ratio(
        frame[f"eal_pure_T{MAP_THRESHOLD:g}_usd"].to_numpy(), frame["eal_pure_none_usd"].to_numpy()
    )
    fig, ax = plt.subplots(figsize=(9.5, 9.0), dpi=150)
    gdf[~known].plot(ax=ax, facecolor="none", edgecolor=sh["null"]["color"], linewidth=0.5)
    counts = {"zero": 0, **{f"c{i}": 0 for i in range(len(ch["colors"]))}}
    for geom, r, is_known in zip(gdf.geometry, ratio, known, strict=True):
        if not is_known:
            continue
        if not np.isfinite(r):
            color, key = sh["zero"]["color"], "zero"
        else:
            i = class_index(float(r), ch["breaks"])
            color, key = ch["colors"][i], f"c{i}"
        counts[key] += 1
        gpd_one = type(gdf)(geometry=[geom], crs=gdf.crs)
        gpd_one.plot(
            ax=ax,
            facecolor=color,
            edgecolor=sh["region_outline"]["color"],
            linewidth=0.3,
            alpha=sh["fill_opacity"],
        )
    ax.set_aspect("equal")
    minx, miny, maxx, maxy = gdf.total_bounds
    pad = 15.0
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad - 30, maxy + pad)
    ax.set_axis_off()
    # scale bar 100 m
    x0, y0 = minx + 10, miny - 20
    ax.plot(
        [x0, x0 + 100], [y0, y0], color=sh["region_outline"]["color"], lw=3, solid_capstyle="butt"
    )
    ax.text(x0 + 50, y0 - 13, "100 m", ha="center", va="top", fontsize=9)
    handles = [
        Patch(facecolor=c, edgecolor=sh["region_outline"]["color"], linewidth=0.4, label=lab)
        for c, lab in zip(ch["colors"], ch["labels"], strict=True)
    ]
    handles.append(
        Patch(
            facecolor=sh["zero"]["color"],
            edgecolor=sh["region_outline"]["color"],
            linewidth=0.4,
            label="문턱 없음 기준 손실 0 · 비율 없음",
        )
    )
    handles.append(
        Patch(
            facecolor="none",
            edgecolor=sh["null"]["color"],
            linewidth=0.8,
            label=f"용도 미상 · 평가 제외 ({int((~known).sum())}동)",
        )
    )
    ax.legend(
        handles=handles,
        title="문턱 0.15 m 적용 / 문턱 없음 (연평균 손실 배율)",
        loc="upper left",
        bbox_to_anchor=(1.0, 1.0),
        fontsize=8.5,
        title_fontsize=9,
        frameon=False,
    )
    ax.set_title(
        f"강남역 일대 · 무피해문턱 0.15 m 효과(pure 곡선) — {BADGE}\n"
        f"평가 {int(known.sum())}동만 색칠, 모델 CSI 0.29 < 단순 규칙 0.57 (건물별 침수 위치는 신뢰 낮음)",
        fontsize=11,
    )
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return counts


def draw_effect(effect: dict[str, Any], pal: dict[str, Any], out: Path) -> None:
    """Grouped bars: site EAL change (%) per threshold, pure vs climada."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _font()
    ramp = pal["families"]["water"]["ramp"]
    t = effect["thresholds_m"]
    x = np.arange(len(t))
    w = 0.36
    fig, ax = plt.subplots(figsize=(7.5, 4.8), dpi=150)
    for k, (key, label, color) in enumerate(
        (
            ("pure_eal_change_pct", "pure (문턱 미반영 기준선)", ramp[4]),
            ("climada_eal_change_pct", "climada (5 cm 내장)", ramp[1]),
        )
    ):
        vals = effect[key]
        bars = ax.bar(x + (k - 0.5) * w, vals, w, color=color, label=label)
        for b, v in zip(bars, vals, strict=True):
            ax.annotate(
                f"{v:.1f} %",
                (b.get_x() + b.get_width() / 2, v),
                ha="center",
                va="top" if v < 0 else "bottom",
                xytext=(0, -3 if v < 0 else 3),
                textcoords="offset points",
                fontsize=8.5,
            )
    ax.axhline(0, color=pal["shared"]["region_outline"]["color"], lw=0.8)
    ax.set_xticks(x, [f"{v:g}" for v in t])
    ax.set_xlabel("무피해문턱 T (m)")
    ax.set_ylabel("연평균 손실 변화 (문턱 없음 대비, %)")
    ax.set_ylim(-100, 8)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f} %")
    ax.legend(frameon=False, loc="lower left")
    ax.set_title(
        f"문턱을 넣으면 연평균 손실이 얼마나 줄어드나 — {BADGE}\n"
        "용도 확인 243동 합계, 방향은 믿을 수 있으나 크기는 모델 상태에 따라 몇 배 달라짐",
        fontsize=10.5,
    )
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    """Check the numbers against the report, then write the card."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--palettes", type=Path, default=DEFAULT_PALETTES)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    pal = json.loads(args.palettes.read_text(encoding="utf-8"))
    summary, frame = load_inputs()
    tables = report_tables(REPORT)
    calc = compute(summary, frame, tables)
    if calc["mismatches"]:
        print("MISMATCH between recomputed values and the report; nothing written:")
        for m in calc["mismatches"]:
            print("  -", m)
        return 1
    card = assemble(summary, calc, model_state_table(tables))
    gdf = footprints(frame)
    args.out.mkdir(parents=True, exist_ok=True)
    counts = draw_map(frame, gdf, pal, args.out / "threshold_map.png")
    draw_effect(card["threshold_effect"], pal, args.out / "threshold_effect.png")
    card["threshold_map_class_counts"] = counts
    (args.out / "summary.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"wrote {args.out} (CSI model {card['check']['csi_model']}, "
        f"baseline {card['check']['csi_lowest_ground']}, {card['n_assumptions']} assumptions)"
    )
    print("pure  :", card["threshold_effect"]["pure_eal_change_pct"])
    print("climada:", card["threshold_effect"]["climada_eal_change_pct"])
    print("map classes:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
