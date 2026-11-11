"""Figures and literature check of the Gangnam station rain-flood twin (fixed 95 mm/h, R6).

The twin is not changed: ``scripts/case_gangnam_figures.py`` reruns its design storm
(:func:`climaterisk_worker.twin.real_rebuild.storm_for_total`) at several 2 h totals and this
module turns the results into numbers (the pure numpy functions) and into figures (the plotting
functions, matplotlib imported lazily). Everything carries the badge "미검증", no money, no
flood trace and no rain-gauge series. The published values come from one open paper and nothing
is tuned to them: Keum, Seo, Choo (2022) Water 14(24), 4076, doi:10.3390/w14244076 (CC BY 4.0);
see ``assets/libraries/case_gangnam_literature.json``.

Algorithm:
    Frequency curves are interpolated linearly in $\\ln T$ (exact at the tabulated periods):
    $$ v(T) = v_k + (v_{k+1} - v_k)\\,\\frac{\\ln T - \\ln T_k}{\\ln T_{k+1} - \\ln T_k},
       \\quad T_k \\le T \\le T_{k+1} $$
    The relative difference of our central rain $v_{us}$ to the paper's $v_{p}$ is
    $d = 100\\,(v_{us}/v_{p} - 1)$ and the rain row is "ok" when the paper value lies in our
    90 % band, $v_{low} \\le v_p \\le v_{high}$. The depth in front of the exit is the maximum of
    the depth raster $h$ over open cells within Chebyshev distance $r$ of the exit cell,
    $$ h_{exit} = \\max_{|i-i_0|\\le r,\\;|j-j_0|\\le r,\\;\\text{open}(i,j)} h_{ij} $$
    and the onset rain is the first total $P$ with $h_{exit}(P) \\ge h^*$, interpolated linearly
    between sweep points. Plotting position of the annual maxima (Weibull): the $k$-th largest of
    $n$ values has return period $T_k = (n+1)/k$.
    ASCII: v(T) = interp(ln T); d = 100*(v_us/v_p - 1); ok = low <= v_p <= high;
    h_exit = max(h[open cells within r of the exit cell]); T_k = (n+1)/k.

    $v$ rain depth (mm, 2 h), $T$ return period (years), $h$ depth (m), $r$ search radius
    (cells), $h^*$ stair level (m), $P$ 2 h storm total (mm), $n$ number of years.
"""

from __future__ import annotations

import logging
import math
import textwrap
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BADGE = "미검증"
FIGURE_FILES = (
    "fig_study_area.png",
    "fig_rain_frequency.png",
    "fig_ladder_depth.png",
    "fig_exit6_curve.png",
    "fig_flooded_area.png",
    "fig_validation.png",
    "fig_model_chain.png",
)
DPI = 150
DEPTH_BOUNDS = (0.05, 0.15, 0.30, 0.60)
DEPTH_RAMP = ("#c6dbef", "#6baed6", "#2171b5", "#08306b")
USE_COLORS = {"residential": "#e6a86b", "commercial": "#c9645b", "industrial": "#8e7cc3"}
USE_LABELS = {"residential": "주거", "commercial": "상업", "industrial": "산업"}
OURS = "#1f6fb2"
PAPER = "#d95f02"
GREY = "#9a9a9a"
INK = "#222222"


# --------------------------------------------------------------------------- pure numbers
def log_interp_rp(rps: Sequence[float], values: Sequence[float], t: float) -> float:
    """Value at return period ``t`` by linear interpolation in ``ln(T)``.

    Algorithm:
        $$ v(T) = v_k + (v_{k+1}-v_k)\\frac{\\ln T-\\ln T_k}{\\ln T_{k+1}-\\ln T_k} $$
        ASCII: v(T) = interp(ln T, ln T_k, v_k).

        $v$ value (any unit), $T$ return period (years).

    Args:
        rps: Increasing return periods (years).
        values: Value at each return period (same length).
        t: Return period (years) inside ``[rps[0], rps[-1]]``.

    Returns:
        The interpolated value (exact at the given periods).

    Raises:
        ValueError: If ``t`` lies outside the tabulated periods.
    """
    x = np.log(np.asarray(rps, dtype=float))
    y = np.asarray(values, dtype=float)
    lt = math.log(float(t))
    if lt < x[0] - 1e-9 or lt > x[-1] + 1e-9:
        raise ValueError(f"return period {t} outside [{rps[0]}, {rps[-1]}]")
    return float(np.interp(lt, x, y))


def rain_rows(
    paper_rps: Sequence[float],
    paper_mm: Sequence[float],
    our_rps: Sequence[float],
    central: Sequence[float],
    band_low: Sequence[float],
    band_high: Sequence[float],
    judged: Sequence[float],
) -> list[dict[str, Any]]:
    """Literature rows for the 2 h frequency rain: paper value vs our GEV central and band.

    Algorithm:
        $d = 100\\,(v_{us}/v_{p} - 1)$ and ok $\\iff v_{low} \\le v_p \\le v_{high}$.
        ASCII: diff_pct = 100*(model/observed - 1); ok = low <= observed <= high.

        $v_p$ paper depth (mm), $v_{us}$ our central depth (mm), $v_{low}, v_{high}$ our 90 %
        band (mm).

    Args:
        paper_rps: Paper return periods (years).
        paper_mm: Paper 2 h rain (mm) at those periods.
        our_rps: Our return periods (years).
        central: Our central rain (mm).
        band_low: Lower end of our band (mm).
        band_high: Upper end of our band (mm).
        judged: Return periods (years) to compare.

    Returns:
        One dict per judged period: ``id``, ``kind``, ``label_ko``, ``unit``, ``observed``,
        ``model``, ``band``, ``diff_pct``, ``ok``.
    """
    rows: list[dict[str, Any]] = []
    for t in judged:
        obs = log_interp_rp(paper_rps, paper_mm, float(t))
        mod = log_interp_rp(our_rps, central, float(t))
        lo = log_interp_rp(our_rps, band_low, float(t))
        hi = log_interp_rp(our_rps, band_high, float(t))
        rows.append(
            {
                "id": f"rain_2h_T{t:g}",
                "kind": "rain_mm",
                "label_ko": f"2시간 강우 {t:g}년 빈도 (논문 표 1 vs 우리 GEV)",
                "unit": "mm",
                "observed": obs,
                "model": mod,
                "band": [lo, hi],
                "diff_pct": 100.0 * (mod / obs - 1.0),
                "ok": bool(lo <= obs <= hi),
            }
        )
    return rows


def max_near(h: np.ndarray, mask: np.ndarray, rows: int, cols: int, r_cells: int) -> float:
    """Maximum of ``h`` over ``mask`` cells within Chebyshev distance ``r_cells``.

    Algorithm:
        $$ \\max_{|i-i_0|\\le r,\\,|j-j_0|\\le r,\\,m_{ij}} h_{ij} $$
        ASCII: max(h[window][mask[window]]).

        $h$ depth (m), $m$ mask of admissible (open) cells, $r$ radius (cells).

    Args:
        h: Raster ``(rows, cols)``.
        mask: Boolean raster, true where a cell may count.
        rows: Row index of the centre cell.
        cols: Column index of the centre cell.
        r_cells: Radius in cells (Chebyshev).

    Returns:
        The maximum, or NaN when no admissible cell lies in the window.
    """
    r0, r1 = max(rows - r_cells, 0), min(rows + r_cells + 1, h.shape[0])
    c0, c1 = max(cols - r_cells, 0), min(cols + r_cells + 1, h.shape[1])
    win = h[r0:r1, c0:c1]
    ok = np.asarray(mask, dtype=bool)[r0:r1, c0:c1]
    if not ok.any():
        return float("nan")
    return float(win[ok].max())


def onset_rain(mm: Sequence[float], depth: Sequence[float], level: float) -> float | None:
    """First rain total at which ``depth`` reaches ``level`` (linear between sweep points).

    Algorithm:
        With $d_k < h^* \\le d_{k+1}$: $$P^* = P_k + (P_{k+1}-P_k)\\frac{h^*-d_k}{d_{k+1}-d_k}$$
        ASCII: P* = P_k + (P_k1 - P_k) * (level - d_k) / (d_k1 - d_k).

        $P$ rain total (mm), $d$ depth (m), $h^*$ level (m).

    Args:
        mm: Increasing rain totals (mm).
        depth: Depth at each total (m).
        level: Depth to reach (m).

    Returns:
        The total (mm), the first total when the depth starts at or above ``level``, ``None``
        if the level is never reached.
    """
    p = [float(v) for v in mm]
    d = [float(v) for v in depth]
    if d[0] >= level:
        return p[0]
    for k in range(1, len(p)):
        if d[k] >= level:
            frac = (level - d[k - 1]) / (d[k] - d[k - 1])
            return p[k - 1] + (p[k] - p[k - 1]) * frac
    return None


def exit6_row(depth_at_onset: float, level: float) -> dict[str, Any]:
    """Literature row for the depth in front of Exit 6 at the paper's onset rain (113 mm).

    Args:
        depth_at_onset: Our depth (m) at the paper's onset rain.
        level: The paper's depth (m) at that rain.

    Returns:
        Dict with ``id``, ``kind``, ``label_ko``, ``unit``, ``observed`` (paper), ``model``
        (ours) and ``ok`` (ours reaches the paper's depth).
    """
    return {
        "id": "exit6_depth_at_113mm",
        "kind": "depth_at_rain",
        "label_ko": "강남역 6번 출구 앞 수심, 2시간 113 mm",
        "unit": "m",
        "observed": float(level),
        "model": float(depth_at_onset),
        "ok": bool(depth_at_onset >= level),
    }


def exit_cell(
    west: float, south: float, cell_m: float, lon_m_per_deg: float, lon: float, lat: float
) -> tuple[int, int]:
    """Grid ``(row, col)`` of a point; row 0 is the SOUTH row (rows run northward).

    Algorithm:
        $$ i = \\lfloor (\\varphi-\\varphi_s)\\,111320 / \\Delta \\rfloor,\\quad
           j = \\lfloor (\\lambda-\\lambda_w)\\,m_\\lambda / \\Delta \\rfloor $$
        ASCII: i = floor((lat-south)*111320/cell); j = floor((lon-west)*m_lon/cell).

        $\\varphi,\\lambda$ latitude / longitude (deg), $\\Delta$ cell size (m), $m_\\lambda$
        metres per degree of longitude.

    Args:
        west: West edge of the grid (deg).
        south: South edge (deg).
        cell_m: Cell size (m).
        lon_m_per_deg: Metres per degree of longitude.
        lon: Point longitude (deg).
        lat: Point latitude (deg).

    Returns:
        ``(row, col)``.
    """
    row = math.floor((lat - south) * 111320.0 / cell_m)
    col = math.floor((lon - west) * lon_m_per_deg / cell_m)
    return row, col


def plotting_positions(annual_maxima: Sequence[float]) -> tuple[list[float], list[float]]:
    """Weibull plotting positions ``T_k = (n + 1) / k`` of the annual maxima (descending order).

    Args:
        annual_maxima: Annual maximum rain (mm), any order.

    Returns:
        ``(return_periods, values)``, values sorted from the largest.
    """
    v = sorted((float(x) for x in annual_maxima), reverse=True)
    n = len(v)
    return [(n + 1) / (k + 1) for k in range(n)], v


def flooded_share_pct(area_km2: Sequence[float], total_km2: float) -> list[float]:
    """Flooded share (%) of an area: ``100 * flooded / total``."""
    return [100.0 * float(a) / float(total_km2) for a in area_km2]


# --------------------------------------------------------------------------- plotting
def _plt() -> Any:
    """pyplot with the Agg backend and a Hangul font."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from climaterisk_worker.twin import plot as plot_mod

    plot_mod._korean_font(plt)
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _save(fig: Any, path: Path) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    logger.info("figure %s", path.name)


def _clean(ax: Any) -> None:
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _map_frame(ax: Any, bbox: Sequence[float], lon_m: float, with_ticks: bool = True) -> None:
    """Lon/lat map axes: equal metric aspect, 100 m scale bar and north arrow."""
    west, south, east, north = bbox
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_aspect(111320.0 / lon_m)
    sx = west + 0.04 * (east - west)
    sy = south + 0.035 * (north - south)
    dx = 100.0 / lon_m
    ax.plot([sx, sx + dx], [sy, sy], color=INK, lw=3, solid_capstyle="butt", zorder=9)
    ax.text(sx + dx / 2, sy + 0.012 * (north - south), "100 m", ha="center", fontsize=8, zorder=9)
    ax.annotate(
        "N",
        xy=(east - 0.05 * (east - west), north - 0.02 * (north - south)),
        xytext=(east - 0.05 * (east - west), north - 0.10 * (north - south)),
        ha="center",
        va="center",
        fontsize=10,
        fontweight="bold",
        arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.5},
        zorder=9,
    )
    if with_ticks:
        ax.set_xlabel("경도 (°E)", fontsize=8)
        ax.set_ylabel("위도 (°N)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.ticklabel_format(useOffset=False, style="plain")
        ax.set_xticks(np.round(np.linspace(west, east, 4), 3))
        ax.set_yticks(np.round(np.linspace(south, north, 4), 3))
    else:
        ax.set_xticks([])
        ax.set_yticks([])


def plot_study_area(
    path: Path,
    bbox: Sequence[float],
    lon_m: float,
    buildings: Any,
    uses: Sequence[str | None],
    green: Any,
    exits: Sequence[Mapping[str, Any]],
    exit6: Mapping[str, Any],
    title: str,
) -> None:
    """Buildings by use (unknown use outlined only), OSM green, site box and station exits."""
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle

    fig, ax = plt.subplots(figsize=(8.2, 8.6))
    if green is not None and len(green):
        green.plot(ax=ax, facecolor="#cfe8c4", edgecolor="#8fbf7f", linewidth=0.4, zorder=1)
    known = np.array([u is not None for u in uses])
    if (~known).any():
        buildings[~known].plot(ax=ax, facecolor="none", edgecolor=GREY, linewidth=0.5, zorder=2)
    for use, color in USE_COLORS.items():
        sel = np.array([u == use for u in uses])
        if sel.any():
            buildings[sel].plot(
                ax=ax, facecolor=color, edgecolor="#555555", linewidth=0.3, zorder=3
            )
    ax.add_patch(
        Rectangle(
            (bbox[0], bbox[1]),
            bbox[2] - bbox[0],
            bbox[3] - bbox[1],
            fill=False,
            ec=INK,
            lw=1.6,
            ls="--",
            zorder=5,
        )
    )
    for e in exits:
        is6 = int(e["num"]) == int(exit6["num"])
        ax.scatter(
            [e["lon"]],
            [e["lat"]],
            s=120 if is6 else 34,
            marker="*" if is6 else "o",
            c="#d62728" if is6 else "#ffffff",
            edgecolors=INK,
            linewidths=0.9,
            zorder=7,
        )
        if not is6:
            ax.annotate(
                f"{int(e['num'])}",
                (e["lon"], e["lat"]),
                xytext=(4, 3),
                textcoords="offset points",
                fontsize=7,
                zorder=8,
            )
    ax.annotate(
        exit6["name_ko"],
        (exit6["lon"], exit6["lat"]),
        xytext=(34, -26),
        textcoords="offset points",
        fontsize=9.5,
        fontweight="bold",
        color="#b01e1e",
        arrowprops={"arrowstyle": "-", "color": "#b01e1e", "lw": 1.0},
        bbox={"fc": "white", "ec": "#b01e1e", "lw": 0.8, "pad": 2},
        zorder=9,
    )
    _map_frame(ax, bbox, lon_m)
    handles = [
        Patch(fc=c, ec="#555555", lw=0.4, label=f"{USE_LABELS[u]} 건물")
        for u, c in USE_COLORS.items()
    ]
    handles += [
        Patch(fc="none", ec=GREY, lw=0.8, label="용도 미상 · 평가 제외"),
        Patch(fc="#cfe8c4", ec="#8fbf7f", lw=0.4, label="OSM 녹지"),
        Line2D([], [], color=INK, ls="--", lw=1.4, label="모의 대상 상자"),
        Line2D([], [], marker="o", ls="", mfc="white", mec=INK, ms=6, label="강남역 출구 (번호)"),
        Line2D([], [], marker="*", ls="", mfc="#d62728", mec=INK, ms=11, label="6번 출구"),
    ]
    ax.legend(
        handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False
    )
    ax.set_title(title, fontsize=11)
    _save(fig, path)


def plot_rain_frequency(
    path: Path,
    our_rps: Sequence[float],
    central: Sequence[float],
    band_low: Sequence[float],
    band_high: Sequence[float],
    annual_maxima: Sequence[float],
    paper_rps: Sequence[float],
    paper_mm: Sequence[float],
    rows: Sequence[Mapping[str, Any]],
    title: str,
) -> None:
    """2 h frequency rain: our GEV central + 90 % band, annual maxima dots, paper Table 1."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    grid_t = np.exp(np.linspace(math.log(our_rps[0]), math.log(our_rps[-1]), 100))
    xs = np.log(np.asarray(our_rps, dtype=float))
    ctr = np.interp(np.log(grid_t), xs, central)
    lo = np.interp(np.log(grid_t), xs, band_low)
    hi = np.interp(np.log(grid_t), xs, band_high)
    ax.fill_between(grid_t, lo, hi, color=OURS, alpha=0.18, lw=0, label="우리 GEV 90 % 범위")
    ax.plot(grid_t, ctr, color=OURS, lw=2.2, label="우리 GEV 중앙값 (ASOS 108, 1961-2026)")
    tp, vals = plotting_positions(annual_maxima)
    ax.scatter(
        tp, vals, s=14, color=OURS, alpha=0.55, zorder=3, label="우리 연최대 (Weibull 도시점)"
    )
    ax.plot(
        paper_rps,
        paper_mm,
        marker="D",
        ls="--",
        lw=1.0,
        ms=6,
        color=PAPER,
        mfc="white",
        mew=1.6,
        zorder=4,
        label="논문 표 1 (서울 관측소 1961-2015)",
    )
    for k, r in enumerate(rows):
        t = float(str(r["id"]).split("T")[1])
        ax.annotate(
            f"{t:g}년: 논문 {r['observed']:.0f} / 우리 {r['model']:.0f} mm\n"
            f"차이 {r['diff_pct']:+.1f} %, {'범위 안' if r['ok'] else '범위 밖'}",
            (t, r["observed"]),
            xytext=(8 + 26 * k, -62 - 10 * (k % 2) - 8 * k),
            textcoords="offset points",
            fontsize=8,
            arrowprops={"arrowstyle": "-", "color": GREY, "lw": 0.8},
            bbox={"fc": "white", "ec": GREY, "lw": 0.6, "pad": 2},
            zorder=6,
        )
    ax.set_xscale("log")
    ax.set_xticks([2, 5, 10, 20, 30, 50, 100, 200, 500])
    ax.get_xaxis().set_major_formatter(lambda v, _: f"{v:g}")
    ax.set_xlabel("재현기간 (년, 로그 눈금)")
    ax.set_ylabel("2시간 강우량 (mm)")
    ax.grid(color="#e6e6e6", lw=0.6)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False)
    ax.set_title(title, fontsize=11)
    _clean(ax)
    _save(fig, path)


def _depth_cmap() -> tuple[Any, Any]:
    from matplotlib import colors as mcolors

    cmap = mcolors.ListedColormap(["#ffffff00", *DEPTH_RAMP])
    cmap.set_bad("#bdbdbd")
    return cmap, mcolors.BoundaryNorm([-1.0, *DEPTH_BOUNDS, 99.0], cmap.N)


def plot_ladder(
    path: Path,
    panels: Sequence[tuple[str, np.ndarray]],
    walls: np.ndarray,
    bbox: Sequence[float],
    lon_m: float,
    exit_lonlat: tuple[float, float],
    exit_name: str,
    title: str,
) -> None:
    """Maximum-depth maps (walls grey) for several storms with one shared depth colour bar."""
    plt = _plt()
    from matplotlib.colors import ListedColormap
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    cmap, norm = _depth_cmap()
    n = len(panels)
    cols = 3
    nrows = math.ceil(n / cols)
    fig, axes = plt.subplots(nrows, cols, figsize=(4.6 * cols, 4.9 * nrows))
    axes = np.atleast_1d(axes).ravel()
    extent = (bbox[0], bbox[2], bbox[1], bbox[3])
    for ax, (label, h) in zip(axes, panels, strict=False):
        shown = np.ma.masked_where(walls, h)
        ax.set_facecolor("white")
        ax.imshow(
            walls.astype(float),
            origin="lower",
            extent=extent,
            cmap=ListedColormap(["#ffffff", "#9a9a9a"]),
            vmin=0,
            vmax=1,
            aspect="auto",
        )
        ax.imshow(shown, origin="lower", extent=extent, cmap=cmap, norm=norm, aspect="auto")
        ax.scatter(
            [exit_lonlat[0]],
            [exit_lonlat[1]],
            s=110,
            marker="*",
            c="#d62728",
            edgecolors=INK,
            linewidths=0.8,
            zorder=7,
        )
        _map_frame(ax, bbox, lon_m, with_ticks=False)
        ax.set_title(label, fontsize=10)
    for ax in axes[n:]:
        ax.axis("off")
    handles = [Patch(fc="#9a9a9a", label="건물(벽)"), Patch(fc="#ffffff", ec=GREY, label="< 5 cm")]
    names = ["5-15 cm", "15-30 cm", "30-60 cm", "> 60 cm"]
    handles += [Patch(fc=c, label=nm) for c, nm in zip(DEPTH_RAMP, names, strict=True)]
    handles.append(
        Line2D([], [], marker="*", ls="", mfc="#d62728", mec=INK, ms=11, label=exit_name)
    )
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=len(handles),
        fontsize=9,
        frameon=False,
        title="최대 수심 (열린 칸)",
        bbox_to_anchor=(0.5, -0.01),
    )
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    _save(fig, path)


def plot_exit6_curve(
    path: Path,
    sweep: Sequence[Mapping[str, float]],
    stairs: Sequence[float],
    paper_point: tuple[float, float],
    onset_mm: float | None,
    our_totals: Mapping[str, float],
    title: str,
) -> None:
    """Depth in front of Exit 6 vs the 2 h total, with stair heights, paper point and onset."""
    plt = _plt()
    mm = [p["rain_mm"] for p in sweep]
    d = [100.0 * p["depth_m"] for p in sweep]
    fig, ax = plt.subplots(figsize=(8.6, 5.6))
    ax.plot(mm, d, marker="o", ms=4, lw=2.0, color=OURS, label="우리 트윈 (출구 10 m 안 최대 수심)")
    for k, s in enumerate(stairs):
        ax.axhline(100 * s, color=GREY, ls=":", lw=1.2)
        ax.text(
            mm[-1],
            100 * s + (-0.15 if k == 0 else 0.15),
            f"계단 높이 {100 * s:.0f} cm",
            fontsize=8,
            color="#555555",
            ha="right",
            va="bottom" if k else "top",
        )
    ax.scatter(
        [paper_point[0]],
        [100 * paper_point[1]],
        marker="D",
        s=70,
        c="white",
        edgecolors=PAPER,
        linewidths=2,
        zorder=6,
        label="논문: 113 mm에서 11 cm 넘게 잠김",
    )
    top = max(max(d), 100 * max(stairs)) * 1.12 + 0.5
    for k, (name, val) in enumerate(our_totals.items()):
        ax.axvline(val, color=OURS, alpha=0.35, lw=1.0)
        ax.text(
            val,
            top * (0.98 - 0.07 * (k % 2)),
            name,
            rotation=90,
            fontsize=8,
            ha="right",
            va="top",
            color=OURS,
        )
    if onset_mm is not None:
        ax.annotate(
            f"우리 11 cm 도달 {onset_mm:.0f} mm\n(논문 113 mm)",
            (onset_mm, 100 * stairs[0]),
            xytext=(-95, 38),
            textcoords="offset points",
            fontsize=9,
            arrowprops={"arrowstyle": "->", "color": INK},
            bbox={"fc": "white", "ec": OURS, "lw": 0.8, "pad": 2},
        )
    else:
        ax.text(
            0.98,
            0.25,
            "우리 트윈은 11 cm에 도달하지 않음",
            transform=ax.transAxes,
            ha="right",
            fontsize=9,
            bbox={"fc": "white", "ec": OURS, "pad": 2},
        )
    ax.set_ylim(0, top)
    ax.set_xlabel("2시간 강우 총량 (mm, 같은 설계 우량주상도)")
    ax.set_ylabel("6번 출구 앞 최대 수심 (cm)")
    ax.grid(color="#e6e6e6", lw=0.6)
    ax.legend(loc="center left", fontsize=8.5, frameon=False, bbox_to_anchor=(0.0, 0.45))
    ax.set_title(title, fontsize=11)
    _clean(ax)
    _save(fig, path)


def plot_flooded_area(
    path: Path,
    ours: Sequence[Mapping[str, float]],
    paper_rps: Sequence[float],
    paper_share: Sequence[float],
    reason_ko: str,
    title: str,
) -> None:
    """Flooded share of the area vs return period: ours (site box) and the paper's (basin)."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8.6, 5.6))
    ox = [r["return_period_years"] for r in ours]
    oy = [100.0 * r["flooded_km2"] / r["site_km2"] for r in ours]
    ax.plot(ox, oy, marker="o", lw=2.0, color=OURS, label="우리: 대상지 상자 (열린 칸 수심 > 문턱)")
    ax.plot(
        paper_rps,
        paper_share,
        marker="D",
        mfc="white",
        mew=1.6,
        lw=1.2,
        ls="--",
        color=PAPER,
        label="논문: 7.39 km² 배수구역 (표 2, 2시간)",
    )
    ax.set_xscale("log")
    ax.set_xticks([2, 5, 10, 20, 30, 50, 100, 200, 500])
    ax.get_xaxis().set_major_formatter(lambda v, _: f"{v:g}")
    ax.set_xlabel("재현기간 (년, 로그 눈금)")
    ax.set_ylabel("침수 면적 비율 (%)")
    ax.grid(color="#e6e6e6", lw=0.6)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False)
    ax.text(
        0.02,
        0.80,
        "참고, 판정 안 함\n" + textwrap.fill(reason_ko, 30),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        bbox={"fc": "#fff8e5", "ec": "#d9b44a", "lw": 0.8, "pad": 3},
    )
    ax.set_title(title, fontsize=11)
    _clean(ax)
    _save(fig, path)


def plot_validation(
    path: Path, check: Mapping[str, Any], spatial: Mapping[str, Mapping[str, Any]], title: str
) -> None:
    """Building-level scores (CSI, POD, FAR) of the twin vs lowest-ground, per event."""
    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 5.0), gridspec_kw={"width_ratios": [2.1, 1]})
    ax = axes[0]
    events = ["2022", "2011", "2010"]
    twin = [check["csi_model"], *(spatial[y]["csi_model"] for y in events[1:])]
    base = [check["csi_lowest_ground"], *(spatial[y]["csi_lowest_ground"] for y in events[1:])]
    x = np.arange(3)
    w = 0.36
    for off, vals, col, lab in (
        (-w / 2, twin, OURS, "트윈"),
        (w / 2, base, GREY, "가장 낮은 건물이 잠긴다 (단순 규칙)"),
    ):
        bars = ax.bar(x + off, vals, w, color=col, label=lab)
        for b, v in zip(bars, vals, strict=True):
            ax.annotate(
                f"{v:.2f}",
                (b.get_x() + b.get_width() / 2, v),
                ha="center",
                xytext=(0, 3),
                textcoords="offset points",
                fontsize=9,
            )
    ax.set_xticks(x, ["2022-08-08\n(관측 강우)", "2011-07\n(면적 맞춤)", "2010-09\n(면적 맞춤)"])
    ax.set_ylabel("CSI (1에 가까울수록 일치)")
    ax.set_ylim(0, 0.75)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.set_title("건물 단위 CSI: 트윈 vs 단순 규칙", fontsize=10)
    _clean(ax)
    ax2 = axes[1]
    labs = ["POD\n(적중)", "FAR\n(오경보)"]
    vals2 = [check["pod_model"], check["far_model"]]
    bars = ax2.bar([0, 1], vals2, 0.5, color=[OURS, "#d95f02"])
    for b, v in zip(bars, vals2, strict=True):
        ax2.annotate(
            f"{v:.2f}",
            (b.get_x() + b.get_width() / 2, v),
            ha="center",
            xytext=(0, 3),
            textcoords="offset points",
            fontsize=9,
        )
    ax2.set_xticks([0, 1], labs)
    ax2.set_ylim(0, 1)
    ax2.set_title("2022 트윈 POD·FAR", fontsize=10)
    _clean(ax2)
    fig.suptitle(title, fontsize=11.5)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    _save(fig, path)


def plot_model_chain(path: Path, title: str) -> None:
    """Box-and-arrow diagram of the model chain with the two side checks."""
    plt = _plt()
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    fig, ax = plt.subplots(figsize=(13.0, 7.4))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 62)
    ax.axis("off")

    def box(
        x: float, y: float, w: float, h: float, head: str, sub: str, fc: str, ec: str, ls: str = "-"
    ) -> None:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.25,rounding_size=0.8",
                fc=fc,
                ec=ec,
                lw=1.5,
                ls=ls,
            )
        )
        ax.text(
            x + w / 2,
            y + h * 0.68,
            head,
            ha="center",
            va="center",
            fontsize=10.5,
            fontweight="bold",
        )
        ax.text(
            x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=8.5, color="#333333"
        )

    def arrow(p: tuple[float, float], q: tuple[float, float], dashed: bool = False) -> None:
        ax.add_patch(
            FancyArrowPatch(
                p,
                q,
                arrowstyle="-|>",
                mutation_scale=14,
                lw=1.4,
                color=INK,
                ls="--" if dashed else "-",
            )
        )

    ins = [
        ("OSM 건물·녹지", "건물 = 벽, 녹지 CN 74"),
        ("GLO-30 지형", "건물 자리 지우고 10 m로 보간"),
        ("ASOS 108 2시간 빈도", "GEV 중앙값 (10·30·100·500년)"),
        ("하수 95 mm/h (가정)", "서울 설계 수준, 균일, 보정 안 함"),
        ("상류 유입 (가정)", "바깥 1.5 km 포장면, 지연 없음"),
    ]
    for k, (h, s) in enumerate(ins):
        box(2, 53 - k * 11.2, 25, 8.4, h, s, "#eef3f8", "#4c78a8")
        arrow((27.4, 57.2 - k * 11.2), (33.5, 33))
    main = [
        ("설계 강우", "정해진 우량주상도 하나, 총량만 바꿈"),
        ("2D 얕은물 계산", "10 m 격자, 건물 벽, 하수·침투 손실"),
        ("최대 수심 지도", "열린 칸의 h_max"),
        ("건물 앞 침수 수심", "건물 둘레 도로의 최대 수심"),
        ("출력", "수심 지도 · 6번 출구 수심 · 침수 면적(%)"),
    ]
    bx, bw = 34.0, 30.0
    ys = [46.0, 35.5, 25.0, 14.5, 4.0]
    for (h, s), y in zip(main, ys, strict=True):
        box(bx, y, bw, 8.4, h, s, "#deebf7", "#2171b5")
    for a, b in pairwise(ys):
        arrow((bx + bw / 2, a - 0.4), (bx + bw / 2, b + 8.8))
    box(
        70,
        40,
        28,
        12,
        "2022 보정 없는 점검",
        "건물 단위 CSI·POD·FAR\n단순 규칙과 비교 (2022·2011·2010)",
        "white",
        "#555555",
        "--",
    )
    box(
        70,
        14,
        28,
        12,
        "문헌 점검 (진단 전용)",
        "Keum 외 2022: 2시간 빈도 강우,\n6번 출구 113 mm·11 cm",
        "white",
        "#d95f02",
        "--",
    )
    arrow((64.4, 29.2), (69.6, 45), dashed=True)
    arrow((64.4, 29.2), (69.6, 20), dashed=True)
    ax.text(50, 60, title, ha="center", fontsize=12)
    _save(fig, path)
