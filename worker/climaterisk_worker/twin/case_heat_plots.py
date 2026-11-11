# ruff: noqa: E501  (Korean captions count double width)
"""Figure pack of the Yeongdeungpo heat case twin (published-study style, 미검증).

Reads the existing run (``maps.npz``, ``summary.json``), the spec and the S-DoT subset cache,
draws nine PNGs and writes ``figures_pack.json`` plus ``literature_check`` into
``summary.json``. The model is not rerun here; nothing is tuned to the paper (Kim et al. 2022,
doi:10.3390/rs14020260) and the literature check is diagnostic only. matplotlib is imported
inside the plotting functions.

Algorithm:
    Sky view factor binning of the mean radiant temperature (see
    :mod:`climaterisk_worker.twin.case_heat_figures`):
    $$\\tilde T_{mr}(b) = \\mathrm{median}\\{T_{mr,i}: \\psi_i \\in [\\psi_b, \\psi_{b+1})\\}$$
    ASCII: Tmr_med(b) = median(Tmr[svf in bin b]); psi sky view factor (-), Tmr in degC.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from climaterisk_worker.twin import case_heat as ch
from climaterisk_worker.twin.case_heat_figures import (
    binned_stats,
    class_shares,
    literature_rows,
)
from climaterisk_worker.twin.site import M_PER_DEG, SiteGrid

logger = logging.getLogger("case_heat_plots")

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
# sequential single-hue family (light -> dark), no rainbow, no money meaning
CLASS_COLOURS = ["#fee5d9", "#fcae91", "#fb6a4a", "#a50f15"]
GREY, BLUE = "#7f7f7f", "#2b6ca3"
FORM_MARKERS = {
    "compact low-rise": "o",
    "compact mid-rise": "s",
    "high density": "D",
    "dense tree": "^",
    "low plants": "v",
}
FORM_KO = {
    "compact low-rise": "밀집 저층",
    "compact mid-rise": "밀집 중층",
    "high density": "고밀 고층",
    "dense tree": "울창한 수목",
    "low plants": "낮은 식생(개방)",
}


def _plt() -> Any:
    """pyplot with the Agg backend and a Hangul font."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from climaterisk_worker.twin import plot as plot_mod

    plot_mod._korean_font(plt)
    return plt


def _save(fig: Any, path: Path) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grid_from(z: Mapping[str, Any], cell_m: float) -> SiteGrid:
    """Rebuild the twin grid from stored cell centres."""
    lat, lon = z["lat"], z["lon"]
    ny, nx = lat.shape
    south = float(lat[0, 0]) - 0.5 * cell_m / M_PER_DEG
    lon_m = M_PER_DEG * math.cos(math.radians(south))
    return SiteGrid(float(lon[0, 0]) - 0.5 * cell_m / lon_m, south, cell_m, nx, ny)


def _extent(lat: np.ndarray, lon: np.ndarray) -> tuple[float, float, float, float]:
    dlon = float(lon[0, 1] - lon[0, 0])
    dlat = float(lat[1, 0] - lat[0, 0])
    return (
        float(lon[0, 0]) - dlon / 2,
        float(lon[0, -1]) + dlon / 2,
        float(lat[0, 0]) - dlat / 2,
        float(lat[-1, 0]) + dlat / 2,
    )


def _map_axes(
    ax: Any, xlim: tuple[float, float], ylim: tuple[float, float], scale: bool = True
) -> None:
    """Lat/lon axes, north arrow and a 100 m scale bar."""
    import matplotlib.patheffects as pe

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    lat0 = 0.5 * (ylim[0] + ylim[1])
    ax.set_aspect(1.0 / math.cos(math.radians(lat0)))
    ax.xaxis.set_major_locator(__import__("matplotlib.ticker").ticker.MaxNLocator(4))
    ax.yaxis.set_major_locator(__import__("matplotlib.ticker").ticker.MaxNLocator(5))
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=8)
    ax.set_xlabel("경도 (°E)", fontsize=9)
    ax.set_ylabel("위도 (°N)", fontsize=9)
    ax.annotate(
        "",
        xy=(0.94, 0.96),
        xytext=(0.94, 0.84),
        xycoords="axes fraction",
        arrowprops={"arrowstyle": "-|>", "color": "k", "lw": 1.6},
    )
    ax.text(
        0.94,
        0.975,
        "N",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=11,
        fontweight="bold",
    )
    if scale:
        length = 100.0 / (M_PER_DEG * math.cos(math.radians(lat0)))
        x0 = xlim[0] + 0.04 * (xlim[1] - xlim[0])
        y0 = ylim[0] + 0.05 * (ylim[1] - ylim[0])
        ax.plot(
            [x0, x0 + length],
            [y0, y0],
            color="k",
            lw=3,
            solid_capstyle="butt",
            path_effects=[pe.withStroke(linewidth=5, foreground="white")],
            zorder=9,
        )
        ax.text(
            x0 + length / 2,
            y0 + 0.012 * (ylim[1] - ylim[0]),
            "100 m",
            ha="center",
            va="bottom",
            fontsize=9,
            zorder=9,
            path_effects=[pe.withStroke(linewidth=3, foreground="white")],
        )


def _site_view(
    spec: Mapping[str, Any], pad: float = 0.0
) -> tuple[tuple[float, float], tuple[float, float]]:
    w, s, e, n = spec["site"]["bbox"]
    return (w - pad, e + pad), (s - pad, n + pad)


def _draw_masked(ax: Any, arr: np.ndarray, extent: Sequence[float], **kw: Any) -> Any:
    return ax.imshow(arr, origin="lower", extent=extent, interpolation="nearest", **kw)


def _buildings_grey(ax: Any, open_mask: np.ndarray, extent: Sequence[float]) -> None:
    layer = np.ma.masked_where(open_mask, np.ones(open_mask.shape))
    _draw_masked(ax, layer, extent, cmap="Greys", vmin=0, vmax=2.2, zorder=3)


def _sensor_marks(ax: Any, site_s: Any, label: bool = False) -> None:
    ax.scatter(
        site_s["lon"],
        site_s["lat"],
        marker="^",
        s=70,
        c="white",
        edgecolors="k",
        linewidths=1.2,
        zorder=6,
    )
    if label:
        for r in site_s.itertuples():
            ax.annotate(
                str(r.serial)[-3:],
                (r.lon, r.lat),
                xytext=(4, 4),
                textcoords="offset points",
                fontsize=8,
                zorder=7,
                bbox={"fc": "white", "ec": "none", "alpha": 0.75, "pad": 0.8},
            )


# --------------------------------------------------------------------------- figures


def plot_study_area(path: Path, ctx: Mapping[str, Any]) -> None:
    """Buildings by height source, roads, green, site box, site sensors; inset of the ring."""
    plt = _plt()
    from matplotlib.colors import ListedColormap
    from matplotlib.gridspec import GridSpec
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle

    z, spec, site_s, sensors = ctx["z"], ctx["spec"], ctx["site_s"], ctx["sensors"]
    ext = ctx["extent"]
    bld = ctx["buildings"]
    w, s, e, n = spec["site"]["bbox"]
    fig = plt.figure(figsize=(14, 8))
    gs = GridSpec(1, 2, width_ratios=[2.0, 1.0], wspace=0.28, figure=fig)
    ax = fig.add_subplot(gs[0])
    surf = ListedColormap(["#ffffff", "#d0d0d0", "#b7dfb0"])
    _draw_masked(ax, z["surface_class"], ext, cmap=surf, vmin=0, vmax=2, zorder=1)
    src = bld["height_source"]
    ax.add_collection(_poly_coll(bld[src == "height"], "#2b6ca3", None))
    ax.add_collection(_poly_coll(bld[src == "levels"], "#8fb8de", None))
    ax.add_collection(_poly_coll(bld[src == "unknown"], "#f2f2f2", "////"))
    ax.add_patch(Rectangle((w, s), e - w, n - s, fill=False, ec="#d62728", lw=2.2, zorder=8))
    _sensor_marks(ax, site_s, label=True)
    _map_axes(ax, (ext[0], ext[1]), (ext[2], ext[3]))
    handles = [
        Patch(fc="#2b6ca3", ec="0.3", label="건물: OSM 높이 태그"),
        Patch(fc="#8fb8de", ec="0.3", label="건물: 층수 × 3.3 m (가정)"),
        Patch(fc="#f2f2f2", ec="0.3", hatch="////", label="건물: 높이 미상 (그림자 0 가정)"),
        Patch(fc="#d0d0d0", ec="0.5", label="도로 (OSM 폭 가정)"),
        Patch(fc="#b7dfb0", ec="0.5", label="녹지 (OSM)"),
        Patch(fc="none", ec="#d62728", lw=2, label="연구 범위(상자)"),
        Line2D(
            [], [], marker="^", ls="", mfc="white", mec="k", ms=9, label="S-DoT 센서(1 km 이내)"
        ),
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.1),
        ncol=2,
        fontsize=9,
        framealpha=0.92,
    )
    ax.set_title("연구 지역: 영등포 쪽방촌 일대 (영등포역 북동쪽)", fontsize=12)
    # inset: 1-3 km ring sensors around the site
    ax2 = fig.add_subplot(gs[1])
    ring = sensors[sensors["zone"] == "ring"]
    ax2.scatter(ring["lon"], ring["lat"], s=22, c=BLUE, label=f"고리 센서 1–3 km (n={len(ring)})")
    ax2.scatter(
        site_s["lon"],
        site_s["lat"],
        marker="^",
        s=50,
        c="white",
        edgecolors="k",
        label=f"지역 센서 (n={len(site_s)})",
        zorder=4,
    )
    ax2.add_patch(Rectangle((w, s), e - w, n - s, fill=False, ec="#d62728", lw=1.8, zorder=5))
    lat0, lon0 = 0.5 * (s + n), 0.5 * (w + e)
    for rad, ls in ((1000.0, "-"), (3000.0, "--")):
        th = np.linspace(0, 2 * np.pi, 200)
        ax2.plot(
            lon0 + rad * np.cos(th) / (M_PER_DEG * math.cos(math.radians(lat0))),
            lat0 + rad * np.sin(th) / M_PER_DEG,
            color=GREY,
            lw=1,
            ls=ls,
        )
    pad = 3300.0
    xl = (
        lon0 - pad / (M_PER_DEG * math.cos(math.radians(lat0))),
        lon0 + pad / (M_PER_DEG * math.cos(math.radians(lat0))),
    )
    _map_axes(ax2, xl, (lat0 - pad / M_PER_DEG, lat0 + pad / M_PER_DEG), scale=False)
    ax2.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), fontsize=8.5, framealpha=0.9)
    ax2.set_title("배경 기온 고리: 중심에서 1 km·3 km", fontsize=11)
    _save(fig, path)


def _poly_coll(gdf: Any, fc: str, hatch: str | None) -> Any:
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Polygon

    patches = []
    for geom in gdf.geometry:
        for g in getattr(geom, "geoms", [geom]):
            if g.is_empty:
                continue
            patches.append(Polygon(np.asarray(g.exterior.coords), closed=True))
    pc = PatchCollection(patches, facecolor=fc, edgecolor="0.25", linewidth=0.3, zorder=4)
    if hatch:
        pc.set_hatch(hatch)
    return pc


def plot_ring_days(path: Path, ctx: Mapping[str, Any]) -> None:
    """Ring-median daily maximum (10-17 h) over summer 2026 with the five chosen days."""
    plt = _plt()
    import matplotlib.dates as mdates
    import pandas as pd

    spec = ctx["spec"]
    prof = ctx["prof"].reset_index()
    lo, hi = spec["daylight_hours"]
    prof = prof[(prof["hour"] >= lo) & (prof["hour"] <= hi)]
    daily = prof.groupby("date")["t"].max()
    daily = daily[(daily.index >= "2026-05-25") & (daily.index <= "2026-09-06")]
    x = pd.to_datetime(daily.index)
    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.plot(x, daily.to_numpy(), "-", color=GREY, lw=1.2, zorder=2)
    ax.scatter(x, daily.to_numpy(), s=12, c=GREY, zorder=3, label="일별 최고(고리 중앙값)")
    chosen = sorted(spec["days"])
    for k, date in enumerate(chosen):
        if date not in daily.index:
            continue
        rank = spec["days"].index(date) + 1
        t = float(daily[date])
        ax.scatter([pd.Timestamp(date)], [t], s=70, c="#a50f15", zorder=5)
        ax.annotate(
            f"{rank}위 {date[5:]}\n{t:.1f} °C",
            (pd.Timestamp(date), t),
            xytext=((k - 2) * 42, 30 + (k % 2) * 26),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            arrowprops={"arrowstyle": "-", "color": "#a50f15"},
            bbox={"fc": "white", "ec": "#a50f15", "pad": 2},
        )
    ax.scatter([], [], s=70, c="#a50f15", label="선정된 5일(모형 입력)")
    ax.set_ylim(float(daily.min()) - 1, float(daily.max()) + 9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0, interval=2))
    ax.tick_params(labelsize=9)
    ax.set_ylabel(f"{lo}–{hi}시 고리 센서 기온 중앙값의 일 최고 (°C)", fontsize=10)
    ax.set_xlabel("2026년 5월 25일 – 9월 6일", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower center", fontsize=9)
    ax.set_title(
        "선정 날짜: 모형을 돌리기 전에 S-DoT 고리 기온만으로 정한 가장 더운 5일", fontsize=12
    )
    _save(fig, path)


def plot_height_svf(path: Path, ctx: Mapping[str, Any]) -> None:
    """Building height (main run, unknown separate) and SVF of open cells."""
    plt = _plt()
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    z, spec, ext = ctx["z"], ctx["spec"], ctx["extent"]
    open_m = z["open_mask"].astype(bool)
    h = z["height_m"].astype(float)
    xl, yl = _site_view(spec)
    fig, axs = plt.subplots(1, 2, figsize=(14, 6.4))
    ax = axs[0]
    unknown = ~open_m & (h <= 0)
    known = ~open_m & (h > 0)
    _draw_masked(
        ax,
        np.ma.masked_where(~unknown, np.ones(h.shape)),
        ext,
        cmap=ListedColormap(["#c7c7c7"]),
        zorder=2,
    )
    im = _draw_masked(
        ax, np.ma.masked_where(~known, h), ext, cmap="Blues", vmin=0, vmax=40, zorder=3
    )
    _map_axes(ax, xl, yl)
    cb = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02, extend="max")
    cb.set_label("건물 높이 (m, OSM 높이/층수×3.3 m 가정)", fontsize=9)
    ax.legend(
        handles=[
            Patch(fc="#c7c7c7", label="높이 미상: 그림자 0 가정"),
            Patch(fc="white", ec="0.5", label="열린 칸"),
        ],
        loc="lower right",
        fontsize=8.5,
        framealpha=0.92,
    )
    ax.set_title("건물 높이 (주 실행)", fontsize=12)
    ax = axs[1]
    _buildings_grey(ax, open_m, ext)
    im2 = _draw_masked(
        ax, np.ma.masked_where(~open_m, z["svf"]), ext, cmap="cividis", vmin=0, vmax=1, zorder=2
    )
    _map_axes(ax, xl, yl)
    cb2 = fig.colorbar(im2, ax=ax, shrink=0.8, pad=0.02)
    cb2.set_label("하늘 열림 비율 SVF (-)", fontsize=9)
    ax.set_title("열린 칸의 하늘 열림 비율 (건물 칸은 회색)", fontsize=12)
    fig.tight_layout()
    _save(fig, path)


def _class_labels(edges: Sequence[float], unit: str = "") -> list[str]:
    e = [f"{x:g}" for x in edges]
    return [
        f"≤ {e[0]}{unit}",
        *[f"{a}–{b}{unit}" for a, b in pairwise(e)],
        f"> {e[-1]}{unit}",
    ]


def plot_mrt_classes(path: Path, ctx: Mapping[str, Any]) -> None:
    """MRT map of the hottest chosen day by the paper's classes plus class shares per day."""
    plt = _plt()
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    z, spec, ext = ctx["z"], ctx["spec"], ctx["extent"]
    edges = spec["literature"]["mrt_class_edges_c"]
    day0 = spec["days"][0]
    open_m = z["open_mask"].astype(bool)
    xl, yl = _site_view(spec)
    cmap = ListedColormap(CLASS_COLOURS)
    norm = BoundaryNorm([-100.0, *edges, 200.0], 4)
    fig, axs = plt.subplots(1, 2, figsize=(14, 6.4), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axs[0]
    _buildings_grey(ax, open_m, ext)
    mrt = np.ma.masked_where(~open_m, z[f"mrt_{day0}"])
    _draw_masked(ax, mrt, ext, cmap=cmap, norm=norm, zorder=2)
    _map_axes(ax, xl, yl)
    labels = _class_labels(edges, " °C")
    ax.legend(
        handles=[
            Patch(fc=c, ec="0.4", label=lab) for c, lab in zip(CLASS_COLOURS, labels, strict=True)
        ]
        + [Patch(fc="#a3a3a3", label="건물")],
        title="평균복사온도 MRT 등급 (논문 기준)",
        loc="lower right",
        fontsize=8.5,
        title_fontsize=9,
        framealpha=0.92,
    )
    d0 = next(d for d in ctx["summary"]["days"] if d["date"] == day0)
    ax.set_title(f"{day0} {d0['hour']:02d}시 MRT (가장 더운 날, 주 실행)", fontsize=12)
    ax = axs[1]
    dates = [d["date"] for d in ctx["summary"]["days"]]
    bottom = np.zeros(len(dates))
    for k in range(4):
        vals = np.array([ctx["pack_mrt"][d][k]["share"] for d in dates]) * 100
        ax.bar(
            [d[5:] for d in dates],
            vals,
            bottom=bottom,
            color=CLASS_COLOURS[k],
            ec="0.4",
            label=labels[k],
        )
        bottom += vals
    ax.set_ylabel("연구 범위 열린 칸 비율 (%)", fontsize=10)
    ax.set_xlabel("날짜 (월-일)", fontsize=10)
    ax.set_ylim(0, 100)
    ax.legend(
        title="MRT 등급",
        fontsize=8.5,
        title_fontsize=9,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.17),
        ncol=4,
    )
    ax.set_title("날짜별 MRT 등급 비율", fontsize=12)
    fig.tight_layout()
    _save(fig, path)


def plot_mrt_vs_svf(path: Path, ctx: Mapping[str, Any]) -> None:
    """Binned MRT vs SVF (sunlit / shaded) with the paper's reference points and class edges."""
    plt = _plt()
    from matplotlib.lines import Line2D

    z, spec = ctx["z"], ctx["spec"]
    lit = spec["literature"]
    edges = lit["mrt_class_edges_c"]
    day0 = spec["days"][0]
    site = z["site_cells"].astype(bool)
    svf = z["svf"].astype(float)
    mrt = z[f"mrt_{day0}"].astype(float)
    sun = z[f"sunlit_{day0}"].astype(bool)
    bins = np.linspace(0.0, 1.0, 11)
    fig, ax = plt.subplots(figsize=(10.5, 7))
    for name, sel, col in (
        ("햇빛 드는 칸", site & sun, "#d94801"),
        ("그늘 칸", site & ~sun, "#3182bd"),
    ):
        st = binned_stats(svf[sel], mrt[sel], bins, min_n=20)
        c = np.array(st["centre"])
        ax.fill_between(c, st["p25"], st["p75"], color=col, alpha=0.25, lw=0)
        ax.plot(
            c,
            st["median"],
            "-o",
            color=col,
            lw=2,
            ms=5,
            label=f"모형 {name}: 중앙값, 띠=사분위 범위",
        )
    for edge in edges:
        ax.axhline(edge, color="k", ls="--", lw=0.9)
        ax.text(
            1.005, edge, f"{edge:g}", va="center", fontsize=8.5, transform=ax.get_yaxis_transform()
        )
    for form, mk in FORM_MARKERS.items():
        pts = [p for p in lit["reference_points"] if p["urban_form"] == form]
        if pts:
            ax.scatter(
                [p["svf"] for p in pts],
                [p["mrt_c"] for p in pts],
                marker=mk,
                s=70,
                facecolors="none",
                edgecolors="k",
                linewidths=1.5,
                zorder=6,
            )
    for p in lit["reference_points"]:
        if "문래" in p["place"]:
            ax.annotate(
                p["place"],
                (p["svf"], p["mrt_c"]),
                xytext=(15, -32),
                textcoords="offset points",
                fontsize=10,
                fontweight="bold",
                arrowprops={"arrowstyle": "->"},
                zorder=7,
            )
    handles, labels = ax.get_legend_handles_labels()
    for form, mk in FORM_MARKERS.items():
        handles.append(Line2D([], [], marker=mk, ls="", mfc="none", mec="k", ms=8))
        labels.append(f"논문 표 A2: {FORM_KO[form]}")
    handles.append(Line2D([], [], color="k", ls="--"))
    labels.append("논문 MRT 등급 경계 (47.6 / 55.5 / 59.4 °C)")
    ax.legend(
        handles,
        labels,
        fontsize=8.5,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.1),
        ncol=2,
        framealpha=0.92,
    )
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("하늘 열림 비율 SVF (-)", fontsize=10)
    ax.set_ylabel("평균복사온도 MRT (°C)", fontsize=10)
    ax.grid(alpha=0.3)
    ax.set_title(
        f"SVF별 MRT: 모형({day0}, 연구 범위 열린 칸) 대 논문 표 A2 (2018-06-19 10시, 다른 날씨)",
        fontsize=11,
    )
    _save(fig, path)


def plot_utci_days(path: Path, ctx: Mapping[str, Any]) -> None:
    """Five UTCI small multiples with the Bröde classes and a shared colour bar."""
    plt = _plt()
    from matplotlib.colors import BoundaryNorm, ListedColormap

    z, spec, ext = ctx["z"], ctx["spec"], ctx["extent"]
    edges = [float(x) for x in spec["figures"]["utci_edges_c"]]
    open_m = z["open_mask"].astype(bool)
    xl, yl = _site_view(spec)
    cmap = ListedColormap(CLASS_COLOURS)
    norm = BoundaryNorm([-100.0, *edges, 200.0], 4)
    fig, axs = plt.subplots(2, 3, figsize=(16, 11))
    flat = axs.ravel()
    im = None
    for k, d in enumerate(ctx["summary"]["days"]):
        ax = flat[k]
        _buildings_grey(ax, open_m, ext)
        im = _draw_masked(
            ax,
            np.ma.masked_where(~open_m, z[f"utci_{d['date']}"]),
            ext,
            cmap=cmap,
            norm=norm,
            zorder=2,
        )
        _map_axes(ax, xl, yl)
        ax.set_title(f"{d['date']} {d['hour']:02d}시  기온(고리) {d['t_ring']:.1f} °C", fontsize=11)
    ax = flat[5]
    ax.axis("off")
    cax = ax.inset_axes([0.08, 0.55, 0.84, 0.07])
    cb = fig.colorbar(im, cax=cax, orientation="horizontal", ticks=edges)
    cb.set_label("UTCI (°C), Bröde 외 (2012) 열스트레스 등급", fontsize=10)
    names = _class_labels(edges, " °C")
    ax.text(
        0.5,
        0.38,
        "등급: " + "  |  ".join(f"{n}" for n in names) + "\n32–38 강함, 38–46 매우 강함, > 46 극심",
        ha="center",
        va="top",
        fontsize=10,
        transform=ax.transAxes,
    )
    ax.text(
        0.5,
        0.18,
        "풍속 1 m/s 등은 가정이며 UTCI는 고리 기온·습도 기준입니다.\n회색 = 건물. 미검증.",
        ha="center",
        va="top",
        fontsize=9,
        color=GREY,
        transform=ax.transAxes,
    )
    fig.tight_layout()
    _save(fig, path)


def plot_utci_classes(path: Path, ctx: Mapping[str, Any]) -> None:
    """Stacked UTCI class shares with sunlit share, and the height-sensitivity panel."""
    plt = _plt()

    s, spec = ctx["summary"], ctx["spec"]
    edges = [float(x) for x in spec["figures"]["utci_edges_c"]]
    dates = [d["date"] for d in s["days"]]
    xs = np.arange(len(dates))
    labels = _class_labels(edges, " °C")
    fig, axs = plt.subplots(1, 2, figsize=(14, 5.8), gridspec_kw={"width_ratios": [1.2, 1]})
    ax = axs[0]
    bottom = np.zeros(len(dates))
    for k in range(4):
        v = np.array([ctx["pack_utci"][d][k]["share"] for d in dates]) * 100
        ax.bar(xs, v, bottom=bottom, color=CLASS_COLOURS[k], ec="0.4", label=labels[k])
        bottom += v
    sun = [s["main"]["per_day"][d]["sunlit_share"] * 100 for d in dates]
    ax.plot(xs, sun, "D", color="k", mfc="white", ms=8, label="햇빛 드는 칸 비율")
    ax.set_xticks(xs, [d[5:] for d in dates])
    ax.set_ylim(0, 100)
    ax.set_ylabel("연구 범위 열린 칸 비율 (%)", fontsize=10)
    ax.set_xlabel("날짜 (월-일)", fontsize=10)
    ax.legend(
        title="UTCI 등급",
        fontsize=8.5,
        title_fontsize=9,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.17),
        ncol=3,
    )
    ax.set_title("날짜별 UTCI 등급 비율과 햇빛 비율", fontsize=12)
    ax = axs[1]
    off = {"main": -0.2, "low": 0.0, "high": 0.2}
    nm = {"main": "주(높이 미상=0 m)", "low": "낮음(미상=3.3 m)", "high": "높음(미상=p90)"}
    cl = {"main": "#a50f15", "low": "#6baed6", "high": "#08519c"}
    mk = {"main": "o", "low": "s", "high": "^"}
    for key in ("main", "low", "high"):
        src = s["main"] if key == "main" else s["height_sensitivity"][key]
        ax.plot(
            xs + off[key],
            [src["per_day"][d]["utci_mean_c"] for d in dates],
            mk[key],
            color=cl[key],
            ms=8,
            label=nm[key],
        )
    ax.set_xticks(xs, [d[5:] for d in dates])
    ax.set_ylabel("연구 범위 UTCI 평균 (°C)", fontsize=10)
    ax.set_xlabel("날짜 (월-일)", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9, title="높이 미상 건물 가정", title_fontsize=9)
    ax.set_title("민감도: 높이 미상 건물의 높이 가정", fontsize=12)
    fig.tight_layout()
    _save(fig, path)


def _per_day_sensor(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per day: model UTCI at each site sensor and observed S-DoT anomaly (ring median)."""
    z, spec, s = ctx["z"], ctx["spec"], ctx["summary"]
    grid, site_s, hourly = ctx["grid"], ctx["site_s"], ctx["hourly"]
    rad = float(spec["model"]["sensor_search_radius_m"])
    open_m = z["open_mask"].astype(bool)
    out = []
    for d in s["days"]:
        model = ch.sample_at_sensors(
            grid,
            z[f"utci_{d['date']}"],
            open_m,
            site_s["lat"].to_numpy(),
            site_s["lon"].to_numpy(),
            rad,
        )
        h = hourly[(hourly["date"] == d["date"]) & (hourly["hour"] == d["hour"])].set_index(
            "serial"
        )["t"]
        obs = np.array(
            [
                float(h[x]) - d["t_ring"] if x in h.index and np.isfinite(h[x]) else np.nan
                for x in site_s["serial"]
            ]
        )
        ok = np.isfinite(model) & np.isfinite(obs)
        rho = ch.rank_check(model[ok], obs[ok], 0.0)["spearman"] if ok.sum() >= 3 else float("nan")
        out.append({"date": d["date"], "model": model, "obs": obs, "ok": ok, "rho": rho})
    return out


def plot_sensor_check(path: Path, ctx: Mapping[str, Any]) -> None:
    """Model UTCI vs observed S-DoT anomaly per day and as multi-day means (diagnostic)."""
    plt = _plt()

    s = ctx["summary"]
    rows = _per_day_sensor(ctx)
    fig, axs = plt.subplots(1, 2, figsize=(14, 6))
    ax = axs[0]
    cols = ["#08306b", "#2171b5", "#6baed6", "#fd8d3c", "#a50f15"]
    marks = ["o", "s", "^", "D", "v"]
    for k, r in enumerate(rows):
        ok = r["ok"]
        rho = r["rho"]
        lab = (
            f"{r['date'][5:]}  ρ={rho:.2f}, n={int(ok.sum())}"
            if np.isfinite(rho)
            else (f"{r['date'][5:]}  ρ 없음, n={int(ok.sum())}")
        )
        ax.scatter(
            r["model"][ok],
            r["obs"][ok],
            marker=marks[k],
            s=60,
            color=cols[k],
            label=lab,
            alpha=0.85,
        )
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xlabel("모형 UTCI (센서 주변 열린 칸 평균, °C)", fontsize=10)
    ax.set_ylabel("S-DoT 기온 - 고리 중앙값 (°C)", fontsize=10)
    ax.legend(title="날짜별 Spearman 순위상관", fontsize=9, title_fontsize=9, loc="best")
    ax.grid(alpha=0.3)
    ax.set_title("날짜별 센서 비교 (진단 전용)", fontsize=12)
    ax = axs[1]
    sens = [
        x
        for x in s["sensors"]
        if x["model_utci_mean_c"] is not None and x["obs_anom_mean_c"] is not None
    ]
    ax.scatter(
        [x["model_utci_mean_c"] for x in sens],
        [x["obs_anom_mean_c"] for x in sens],
        s=70,
        color="#a50f15",
    )
    for x in sens:
        ax.annotate(
            x["serial"][-3:],
            (x["model_utci_mean_c"], x["obs_anom_mean_c"]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=9,
        )
    c = s["check"]
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xlabel("모형 UTCI, 5일 평균 (°C)", fontsize=10)
    ax.set_ylabel("S-DoT 기온 - 고리 중앙값, 5일 평균 (°C)", fontsize=10)
    ax.grid(alpha=0.3)
    ax.set_title(
        f"5일 평균: Spearman ρ={c['spearman']}, n={c['n']} (기준 {c['min_spearman']})", fontsize=12
    )
    fig.text(
        0.5,
        -0.02,
        "진단 전용: 모형은 이 비교에 맞춰 조정되지 않았으며, 센서 수가 적어 통계적 검증이 아닙니다.",
        ha="center",
        fontsize=9.5,
        color=GREY,
    )
    fig.tight_layout()
    _save(fig, path)


def plot_model_chain(path: Path) -> None:
    """Box-and-arrow flow of inputs, shade/SVF, MRT, UTCI, outputs and the diagnostic checks."""
    plt = _plt()
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    fig, ax = plt.subplots(figsize=(15, 7.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 56)
    ax.axis("off")

    def box(
        x: float,
        y: float,
        w: float,
        h: float,
        head: str,
        sub: str,
        fc: str = "#deebf7",
        ec: str = BLUE,
        ls: str = "-",
    ) -> None:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.25,rounding_size=0.8",
                fc=fc,
                ec=ec,
                lw=1.6,
                ls=ls,
            )
        )
        ax.text(
            x + w / 2, y + h * 0.68, head, ha="center", va="center", fontsize=11, fontweight="bold"
        )
        ax.text(
            x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=8.5, color="#333333"
        )

    def arrow(a: tuple[float, float], b: tuple[float, float], dashed: bool = False) -> None:
        ax.add_patch(
            FancyArrowPatch(
                a,
                b,
                arrowstyle="-|>",
                mutation_scale=16,
                color=GREY if dashed else BLUE,
                lw=1.5,
                ls="--" if dashed else "-",
            )
        )

    inputs = [
        ("S-DoT 고리 센서", "1–3 km 중앙값 기온·습도\n(가장 더운 날 5일, 모형 전 선정)"),
        ("OSM 건물·도로·녹지", "건물 높이 = 태그 또는 층수×3.3 m\n(미상은 가정)"),
        ("태양 위치", "날짜·시각의 고도/방위각"),
        ("가정 (A1–A17)", "표면 알베도·온도 상승, 풍속 1 m/s,\n도로 폭 등"),
    ]
    for k, (h_, s_) in enumerate(inputs):
        box(
            2,
            44 - k * 12.5,
            24,
            9.5,
            h_,
            s_,
            fc="#f7f7f7" if k == 3 else "#deebf7",
            ec=GREY if k == 3 else BLUE,
            ls="--" if k == 3 else "-",
        )
        arrow((26.3, 48.7 - k * 12.5), (33.0, 30.0 if k < 3 else 28.0))
    box(33, 24, 17, 12, "그림자·SVF", "5 m 격자 위\n햇빛/그늘 마스크,\n하늘 열림 비율")
    box(55, 24, 17, 12, "평균복사온도 MRT", "단파·장파 수지\n(표면 종류별)")
    box(77, 24, 17, 12, "UTCI", "기온·MRT·바람·습도로\n열스트레스 지수")
    arrow((50.3, 30), (54.7, 30))
    arrow((72.3, 30), (76.7, 30))
    box(
        77,
        4,
        17,
        12,
        "출력",
        "UTCI 지도, 등급 비율,\n센서 비교, 그림 9장",
        fc="#e5f5e0",
        ec="#31a354",
    )
    arrow((85.5, 23.7), (85.5, 16.4))
    box(
        55,
        4,
        17,
        12,
        "하위 점검 (진단)",
        "센서 기온 편차 대 UTCI\n순위상관, 조정 안 함",
        fc="white",
        ec=GREY,
        ls="--",
    )
    box(
        33,
        4,
        17,
        12,
        "문헌 점검 (진단)",
        "Kim 외 (2022) MRT–SVF\n질적 주장 2건",
        fc="white",
        ec=GREY,
        ls="--",
    )
    arrow((79.0, 23.7), (70.0, 16.4), dashed=True)
    arrow((57.0, 23.7), (48.0, 16.4), dashed=True)
    ax.text(
        2,
        4.5,
        "점선 상자는 진단 전용입니다.\n모든 결과 '미검증' 표기.",
        fontsize=9.5,
        color=GREY,
        va="center",
    )
    ax.set_title("영등포 쪽방촌 거리 열환경 모형 흐름 (미검증)", fontsize=13)
    _save(fig, path)


# --------------------------------------------------------------------------- orchestration


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, bool | str) or x is None:
        return x
    if isinstance(x, float | np.floating):
        return round(float(x), 3) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    return x


def build_pack(out: Path, spec_path: Path, data_root: Path, osm_pbf: Path) -> dict[str, Any]:
    """Draw the nine figures and write ``figures_pack.json`` and ``literature_check``.

    Args:
        out: Case folder with ``maps.npz``, ``summary.json`` and the S-DoT subset cache.
        spec_path: Case spec JSON.
        data_root: ``CLIMATERISK_DATA_ROOT`` (S-DoT sensor table).
        osm_pbf: OSM extract for the building polygons of the study-area figure.

    Returns:
        The pack dict that was written.
    """
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    z = dict(np.load(out / "maps.npz"))
    lit = spec["literature"]
    grid = _grid_from(z, float(summary["cell_m"]))
    sensors, hourly = ch.load_sdot(spec, data_root, out / "sdot_subset.parquet")
    site_s = sensors[sensors["zone"] == "site"].reset_index(drop=True)
    prof = ch.ring_profile(sensors, hourly, int(spec["sdot"]["min_ring_sensors"]))
    ext = _extent(z["lat"], z["lon"])
    bld = ch.read_site_buildings(
        osm_pbf, (ext[0], ext[2], ext[1], ext[3]), float(spec["floor_to_floor_m"])
    )
    dates = [d["date"] for d in summary["days"]]
    site = z["site_cells"].astype(bool)
    svf = z["svf"].astype(float)
    pack_mrt = {d: class_shares(z[f"mrt_{d}"], lit["mrt_class_edges_c"], site) for d in dates}
    pack_utci = {
        d: class_shares(z[f"utci_{d}"], spec["figures"]["utci_edges_c"], site) for d in dates
    }
    rows = literature_rows(
        {d: {"mrt": z[f"mrt_{d}"], "sunlit": z[f"sunlit_{d}"]} for d in dates}, svf, site, lit
    )
    ctx = {
        "z": z,
        "spec": spec,
        "summary": summary,
        "grid": grid,
        "extent": ext,
        "site_s": site_s,
        "sensors": sensors,
        "hourly": hourly,
        "prof": prof,
        "buildings": bld,
        "pack_mrt": pack_mrt,
        "pack_utci": pack_utci,
    }
    plot_study_area(out / FIGS[0], ctx)
    plot_ring_days(out / FIGS[1], ctx)
    plot_height_svf(out / FIGS[2], ctx)
    plot_mrt_classes(out / FIGS[3], ctx)
    plot_mrt_vs_svf(out / FIGS[4], ctx)
    plot_utci_days(out / FIGS[5], ctx)
    plot_utci_classes(out / FIGS[6], ctx)
    plot_sensor_check(out / FIGS[7], ctx)
    plot_model_chain(out / FIGS[8])
    cap = _captions(summary, rows)
    pack = _jsonable(
        {
            "badge": "미검증",
            "literature": rows,
            "mrt_classes": pack_mrt,
            "utci_classes": pack_utci,
            "reference_points": lit["reference_points"],
            "not_compared": lit["not_compared"],
            "figures": [{"file": f, "caption_ko": cap[f][0], "idea_from": cap[f][1]} for f in FIGS],
        }
    )
    (out / "figures_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    n_ok = int(sum(bool(r["ok"]) for r in rows))
    summary["literature_check"] = _jsonable(
        {
            "source": lit["source"],
            "conditions_ko": lit["conditions_ko"],
            "rows": rows,
            "n_ok": n_ok,
            "n_rows": len(rows),
            "warning": n_ok < len(rows),
        }
    )
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    logger.info("figure pack written to %s (literature ok %d/%d)", out, n_ok, len(rows))
    return pack


def _captions(
    summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, tuple[str, str]]:
    k = "Kim 외 2022 Fig. 7 / 표 A2"
    return {
        FIGS[0]: (
            "연구 지역. 건물은 OSM 높이 태그, 층수×3.3 m(가정), 높이 미상(빗금, 그림자 0 가정)으로 "
            "구분했고, 연구 범위는 영등포역 북동쪽 약 0.9×1.0 km 상자(가정: 쪽방촌의 실제 필지 경계가 아님). "
            "삼각형은 S-DoT 센서, 오른쪽은 배경 기온에 쓴 1–3 km 고리 센서.",
            "Kim 외 2022 Fig. 1 연구 지역 지도",
        ),
        FIGS[1]: (
            "2026년 여름 S-DoT 1–3 km 고리 센서 기온 중앙값의 일 최고(10–17시). 붉은 점이 가장 더운 5일이며 "
            "모형을 돌리기 전에 이 기온만으로 정했습니다.",
            "일 최고기온 시계열 선정 도표",
        ),
        FIGS[2]: (
            "건물 높이(주 실행; 높이 미상은 회색, 그림자 0 가정)와 열린 칸의 하늘 열림 비율(SVF). "
            "높이 태그가 없는 건물이 많아 SVF는 실제보다 크게 나올 수 있습니다(가정).",
            k,
        ),
        FIGS[3]: (
            "가장 더운 날의 평균복사온도(MRT)를 논문의 4개 위험 등급(47.6/55.5/59.4 °C)으로 칠한 지도와 날짜별 "
            "등급 비율. 등급 경계는 날씨가 다른 논문 값을 그대로 쓴 기준(가정 A17)이며 조정하지 않았습니다.",
            k,
        ),
        FIGS[4]: (
            "가장 더운 날 연구 범위 열린 칸의 SVF별 MRT(중앙값과 사분위 범위). 속이 빈 표시는 논문 표 A2의 "
            "지점 값(2018-06-19 10시, 날씨가 달라 직접 비교가 아님). 문래동4가를 표시했습니다.",
            k,
        ),
        FIGS[5]: (
            "5일 각각의 UTCI 지도(선정 시각). 등급은 Bröde 외(2012) 32/38/46 °C 경계. 풍속 1 m/s, "
            "표면 특성 등은 가정입니다.",
            "Bröde 외 2012 UTCI 등급",
        ),
        FIGS[6]: (
            "날짜별 UTCI 등급 비율과 햇빛 드는 칸 비율, 그리고 높이 미상 건물 가정(0 m / 3.3 m / 상위 10% 높이)에 "
            "따른 UTCI 평균 민감도. 이 가정이 결과를 좌우하는 정도를 보여 줍니다.",
            "Bröde 외 2012 UTCI 등급",
        ),
        FIGS[7]: (
            "연구 범위 S-DoT 센서에서 모형 UTCI와 관측 기온 편차(센서 - 고리 중앙값)의 순위상관. "
            "진단 전용이며 모형을 이에 맞춰 조정하지 않았습니다.",
            "센서 대조 진단 도표",
        ),
        FIGS[8]: (
            "모형 흐름: 입력(S-DoT 고리, OSM, 태양 위치, 가정) → 그림자·SVF → MRT → UTCI → 출력. "
            "하위 점검과 문헌 점검은 진단 전용 곁가지입니다.",
            "Kim 외 2022 방법 흐름도",
        ),
    }
