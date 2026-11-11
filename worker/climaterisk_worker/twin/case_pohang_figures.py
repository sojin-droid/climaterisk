"""Figure pack of the Pohang Naengcheon case twin, in the style of the published studies.

Charts, tables and a diagram built from the EXISTING 30 m run of
:mod:`climaterisk_worker.twin.case_pohang` (no new solver run); only the terrain is rebuilt, for
the channel profile and the catchment outline. Everything carries the badge "미검증" and no
money. The published observations (Hwang, Kang, Jin, Lee 2024, J. Korea Water Resour. Assoc.
57(7), doi:10.3741/JKWRA.2024.57.7.451, CC BY; ``assets/libraries/case_pohang_observations.json``)
are used only as a literature check: nothing is tuned to them.

The pure functions (numpy / stdlib only) are the clock anchoring, the series helpers, the
validation rows, the depth classes and the longitudinal profile. matplotlib / rasterio are
imported inside the plotting functions.

Algorithm:
    Clock anchoring. The model time $t$ (h after the start of the storm) has no clock time. The
    largest 60-min moving window of the hyetograph has its centre at $c$ (h); the observed
    peak hour runs from $a$ to $b$ (clock). The clock time of $t = 0$ is
    $$ t_0 = \\tfrac{1}{2}(a + b) - c $$
    ASCII: t0 = midpoint(obs peak hour) - model_centre.
    The clock time of a model time $t$ is $t_0 + t$.

    Longitudinal profile along the ordered stem cells $k = 0..K$ (mouth upstream):
    $$ x_k = \\sum_{m \\le k} \\Delta s_m,\\quad \\Delta s = \\Delta x \\sqrt{(\\Delta i)^2 +
       (\\Delta j)^2},\\quad \\eta_k = z_{bed,k} + h_{max,k} $$
    ASCII: x[k] = sum(ds[m]), ds = cell_m*hypot(di, dj); surface[k] = bed[k] + hmax[k].

    $\\Delta x$ cell size (m), $z_{bed}$ burnt bed (m a.s.l.), $h_{max}$ maximum depth (m),
    $\\eta$ maximum water surface (m a.s.l.), $x$ distance from the mouth (km).
"""

from __future__ import annotations

import csv
import json
import logging
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[3]
ASSETS_OBS = REPO / "assets" / "libraries" / "case_pohang_observations.json"

BADGE = "미검증"
FIGURE_FILES = (
    "fig_study_area.png",
    "fig_rain.png",
    "fig_hyeto_hydrograph.png",
    "fig_lower_reach_depth.png",
    "fig_point_series.png",
    "fig_long_profile.png",
    "fig_depth_classes.png",
    "fig_model_chain.png",
)
DPI = 150
# depth classes of fig. 7 (m) and the low ground limit (m) of "non-channel land below 20 m"
DEPTH_CLASS_EDGES = (0.3, 0.5, 1.0, 2.0, 4.0)
LOW_GROUND_MAX_M = 20.0
# the lower reach box = the stem from 인덕교 to the mouth plus the points, padded (cells)
LOWER_REACH_PAD_CELLS = 18
INSET_HALF_CELLS = 14
# colours: depth ramp as plot.plot_case_depth_map, one accent for observations, grey references
DEPTH_RAMP = ("#f7f7f5", "#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c", "#08306b")
DEPTH_BOUNDS = (0.0, 0.05, 0.25, 0.5, 1.0, 2.0, 4.0, 100.0)
DEPTH_LABELS = ("< 0.05", "0.05–0.25", "0.25–0.5", "0.5–1", "1–2", "2–4", "> 4")
BLUE = "#08519c"
BLUE_LIGHT = "#6baed6"
OBS = "#d95f02"
GREY = "#7f7f7f"
GREY_LIGHT = "#d9d9d9"

# ---------------------------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------------------------


def _as_datetime(when: str | datetime) -> datetime:
    """Naive ``datetime`` from an ISO string or a ``datetime``."""
    return when if isinstance(when, datetime) else datetime.fromisoformat(when)


def peak_window_center_h(increments_mm: Any, dt_min: float, window_min: float = 60.0) -> float:
    """Centre of the moving window with the largest rain sum.

    Args:
        increments_mm: Rain per step (mm), time order.
        dt_min: Step length (min).
        window_min: Window length (min).

    Returns:
        Centre of the first window (steps ``i .. i+n-1``) with the largest sum, in hours after
        the start: $(i + n/2)\\,\\Delta t / 60$. ASCII: (i + n/2) * dt / 60.
    """
    inc = np.asarray(increments_mm, dtype=float)
    n = max(round(window_min / dt_min), 1)
    n = min(n, inc.size)
    c = np.concatenate([[0.0], np.cumsum(inc)])
    sums = c[n:] - c[:-n]
    i = int(np.argmax(sums))  # first maximum
    return float((i + n / 2.0) * dt_min / 60.0)


def anchor_start(
    model_center_h: float, obs_start: str | datetime, obs_end: str | datetime
) -> datetime:
    """Clock time of model t = 0 that puts the model peak hour on the observed peak hour.

    Algorithm:
        $$ t_0 = \\tfrac{1}{2}(a + b) - c $$
        ASCII: t0 = midpoint(obs) - model_center.

        $a$, $b$ start and end of the observed peak hour (clock), $c$ centre of the model's
        largest 60-min window (h after the model start).

    Args:
        model_center_h: $c$ (h).
        obs_start: $a$ (ISO string or datetime).
        obs_end: $b$ (ISO string or datetime).

    Returns:
        $t_0$ as a naive ``datetime``.
    """
    a = _as_datetime(obs_start)
    b = _as_datetime(obs_end)
    return a + (b - a) / 2 - timedelta(hours=float(model_center_h))


def to_clock(t_h: float, start: datetime) -> datetime:
    """Clock time of the model time ``t_h`` (h): ``start + t_h`` hours."""
    return start + timedelta(hours=float(t_h))


def depth_at_clock(t_h: Any, values: Any, start: datetime, when: str | datetime) -> float:
    """Series value at a clock time (linear interpolation); NaN outside the series."""
    t = np.asarray(t_h, dtype=float)
    v = np.asarray(values, dtype=float)
    x = (_as_datetime(when) - start).total_seconds() / 3600.0
    if t.size == 0 or x < t[0] or x > t[-1]:
        return float("nan")
    return float(np.interp(x, t, v))


def first_crossing_h(t_h: Any, values: Any, level: float = 0.0) -> float | None:
    """First time the series rises above ``level`` (linear interpolation), else ``None``.

    If the first sample is already above the level, its time is returned.
    """
    t = np.asarray(t_h, dtype=float)
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return None
    if v[0] > level:
        return float(t[0])
    up = np.flatnonzero((v[:-1] <= level) & (v[1:] > level))
    if up.size == 0:
        return None
    i = int(up[0])
    frac = (level - v[i]) / (v[i + 1] - v[i])
    return float(t[i] + frac * (t[i + 1] - t[i]))


def validation_rows(
    observations: Sequence[Mapping[str, Any]],
    series: Mapping[str, Mapping[str, Any]],
    start: datetime,
) -> list[dict[str, Any]]:
    """Compare the model with the published observations, one row per observation.

    Args:
        observations: Entries of the observation config (``depth_at_time``, ``overtopped_by``).
        series: ``series[point] = {"t_h", "depth", "above_bank"}`` (``above_bank`` may be None).
        start: Clock time of model t = 0 (:func:`anchor_start`).

    Returns:
        ``depth_at_time``: ``{id, kind, observed, model, diff, ok}`` in m (diff = model -
        observed, ok = ``|diff| <= tol_m``). ``overtopped_by``: ``observed`` the ISO time,
        ``model`` the ISO clock time of the first crossing of ``above_bank`` (None if never),
        ``diff`` hours (model - observed, negative = earlier), ok = model exists and diff <= 0.
    """
    rows: list[dict[str, Any]] = []
    for ob in observations:
        s = series[ob["point"]]
        if ob["kind"] == "depth_at_time":
            model = depth_at_clock(s["t_h"], s["depth"], start, ob["when"])
            diff = model - float(ob["value_m"])
            rows.append(
                {
                    "id": ob["id"],
                    "kind": ob["kind"],
                    "observed": float(ob["value_m"]),
                    "model": model,
                    "diff": diff,
                    "ok": bool(abs(diff) <= float(ob["tol_m"])),
                }
            )
        else:
            above = s.get("above_bank")
            t_cross = None if above is None else first_crossing_h(s["t_h"], above, 0.0)
            obs_dt = _as_datetime(ob["when"])
            if t_cross is None:
                rows.append(
                    {
                        "id": ob["id"],
                        "kind": ob["kind"],
                        "observed": obs_dt.isoformat(timespec="minutes"),
                        "model": None,
                        "diff": None,
                        "ok": False,
                    }
                )
                continue
            model_dt = to_clock(t_cross, start)
            diff_h = (model_dt - obs_dt).total_seconds() / 3600.0
            rows.append(
                {
                    "id": ob["id"],
                    "kind": ob["kind"],
                    "observed": obs_dt.isoformat(timespec="minutes"),
                    "model": model_dt.isoformat(timespec="minutes"),
                    "diff": diff_h,
                    "ok": bool(diff_h <= 0.0),
                }
            )
    return rows


def depth_class_areas(
    h_max: Any, cell_m: float, edges: Sequence[float], mask: Any = None
) -> list[dict[str, Any]]:
    """Flooded area by maximum-depth class.

    Classes are $(lo, hi]$ for consecutive ``edges``; the last class is open (``hi`` None).
    NaN cells and cells outside ``mask`` are ignored.

    Args:
        h_max: Maximum depth raster (m).
        cell_m: Cell size (m).
        edges: Increasing class edges (m).
        mask: Optional boolean raster of the cells to count.

    Returns:
        ``[{"lo", "hi", "area_km2"}]``.
    """
    h = np.asarray(h_max, dtype=float)
    ok = np.isfinite(h)
    if mask is not None:
        ok &= np.asarray(mask, dtype=bool)
    cell_km2 = (cell_m / 1000.0) ** 2
    out: list[dict[str, Any]] = []
    for k, lo in enumerate(edges):
        hi = edges[k + 1] if k + 1 < len(edges) else None
        sel = ok & (h > lo)
        if hi is not None:
            sel &= h <= hi
        out.append(
            {
                "lo": float(lo),
                "hi": None if hi is None else float(hi),
                "area_km2": float(sel.sum()) * cell_km2,
            }
        )
    return out


def long_profile(
    cells_rc: Sequence[tuple[int, int]],
    bed: Any,
    bank: Any,
    h_max: Any,
    cell_m: float,
) -> dict[str, list[float]]:
    """Bed, bank and maximum water surface along ordered cells.

    Args:
        cells_rc: Ordered ``(row, col)`` cells (here: the stem from the mouth upstream).
        bed: Bed raster (m).
        bank: Bank ground raster (m).
        h_max: Maximum depth raster (m).
        cell_m: Cell size (m).

    Returns:
        ``{"distance_km", "bed_m", "bank_m", "max_surface_m"}``; distance 0 at the first cell,
        steps of ``cell_m`` (4-neighbour) or ``cell_m * sqrt(2)`` (diagonal); the surface is
        ``bed + h_max``.
    """
    bed_a, bank_a, h_a = (np.asarray(a, dtype=float) for a in (bed, bank, h_max))
    rc = np.asarray(cells_rc, dtype=int).reshape(-1, 2)
    step = np.hypot(*np.diff(rc, axis=0).T) * cell_m if rc.shape[0] > 1 else np.zeros(0)
    dist = np.concatenate([[0.0], np.cumsum(step)]) / 1000.0
    z = bed_a[rc[:, 0], rc[:, 1]]
    return {
        "distance_km": dist.tolist(),
        "bed_m": z.tolist(),
        "bank_m": bank_a[rc[:, 0], rc[:, 1]].tolist(),
        "max_surface_m": (z + h_a[rc[:, 0], rc[:, 1]]).tolist(),
    }


def lower_reach_box(
    stem_rc: np.ndarray, k_from: int, extra_rc: Sequence[tuple[int, int]], pad: int
) -> tuple[int, int, int, int]:
    """Bounding box ``(row0, row1, col0, col1)`` (inclusive) of the lower reach plus points.

    Args:
        stem_rc: Stem cells ``(n, 2)``, upstream first.
        k_from: Index of the upstream end of the lower reach.
        extra_rc: Further cells to include (points).
        pad: Padding (cells).
    """
    cells = np.vstack([np.asarray(stem_rc)[k_from:], np.asarray(list(extra_rc), dtype=int)])
    r0, c0 = cells.min(axis=0) - pad
    r1, c1 = cells.max(axis=0) + pad
    return int(max(r0, 0)), int(r1), int(max(c0, 0)), int(c1)


def bank_ground(
    ground: np.ndarray,
    channel: np.ndarray,
    domain: np.ndarray,
    cells_rc: np.ndarray,
    radius: np.ndarray,
) -> np.ndarray:
    """Lowest ground beside the burnt channel along the cells (where it spills), as a raster.

    Same rule as the overtopping bank of :func:`case_pohang.run_case`: the minimum ground of the
    non-channel domain cells within ``radius + 2`` cells. NaN where there is none.
    """
    out = np.full(ground.shape, np.nan)
    for (i, j), r in zip(np.asarray(cells_rc).tolist(), np.asarray(radius).tolist(), strict=True):
        w = int(r) + 2
        win = (slice(max(i - w, 0), i + w + 1), slice(max(j - w, 0), j + w + 1))
        side = ~channel[win] & domain[win]
        if side.any():
            out[i, j] = float(ground[win][side].min())
    return out


def read_columns(path: Path) -> dict[str, np.ndarray]:
    """Read a numeric CSV with a header row into ``{column: array}``."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    head, body = rows[0], rows[1:]
    data = np.array([[float(v) for v in r] for r in body])
    return {h: data[:, k] for k, h in enumerate(head)}


# ---------------------------------------------------------------------------------------------
# plotting helpers
# ---------------------------------------------------------------------------------------------


def _plt() -> Any:
    """pyplot with the Agg backend and a Hangul font."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from climaterisk_worker.twin import plot as plot_mod

    plot_mod._korean_font(plt)
    return plt


def _depth_norm() -> tuple[Any, Any]:
    from matplotlib import colors as mcolors

    cmap = mcolors.ListedColormap(list(DEPTH_RAMP))
    return cmap, mcolors.BoundaryNorm(list(DEPTH_BOUNDS), cmap.N)


def _hour_axis(ax: Any, label: bool = True, interval: int = 2) -> None:
    """Clock time ticks (HH:MM) on the x axis."""
    import matplotlib.dates as mdates

    ax.xaxis.set_major_locator(mdates.HourLocator(interval=interval))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(axis="x", color=GREY_LIGHT, lw=0.5)
    if label:
        ax.set_xlabel("시각 (2022-09-05 밤 ~ 09-06, 모형 시각은 A27로 보정)")


def _clock_array(t_h: Any, start: datetime) -> list[datetime]:
    return [to_clock(float(t), start) for t in np.asarray(t_h, dtype=float)]


def _save(fig: Any, path: Path) -> None:
    fig.savefig(path, dpi=DPI)
    import matplotlib.pyplot as plt

    plt.close(fig)
    logger.info("figure %s", path.name)


def _imshow_extent(grid: Any, shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """``(west, east, south, north)`` (deg) of a full-grid raster."""
    from climaterisk_worker.twin import site

    dlon = grid.cell_m / grid.lon_m_per_deg
    dlat = grid.cell_m / site.M_PER_DEG
    return (grid.west, grid.west + shape[1] * dlon, grid.south, grid.south + shape[0] * dlat)


def _lonlat(grid: Any, rc: tuple[int, int]) -> tuple[float, float]:
    """Cell centre ``(lon, lat)`` of full-grid ``(row, col)``."""
    from climaterisk_worker.twin import site

    return (
        grid.west + (rc[1] + 0.5) * grid.cell_m / grid.lon_m_per_deg,
        grid.south + (rc[0] + 0.5) * grid.cell_m / site.M_PER_DEG,
    )


def _box_limits(
    grid: Any, box: tuple[int, int, int, int]
) -> tuple[tuple[float, float], tuple[float, float]]:
    r0, r1, c0, c1 = box
    lo = _lonlat(grid, (r0, c0))
    hi = _lonlat(grid, (r1, c1))
    half_lon = 0.5 * grid.cell_m / grid.lon_m_per_deg
    from climaterisk_worker.twin import site

    half_lat = 0.5 * grid.cell_m / site.M_PER_DEG
    return (lo[0] - half_lon, hi[0] + half_lon), (lo[1] - half_lat, hi[1] + half_lat)


def _hillshade(ground: np.ndarray, cell_m: float) -> np.ndarray:
    from matplotlib.colors import LightSource

    return np.asarray(
        LightSource(azdeg=315, altdeg=40).hillshade(ground, vert_exag=2.0, dx=cell_m, dy=cell_m)
    )


# ---------------------------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------------------------


def plot_study_area(
    path: Path,
    ground: np.ndarray,
    mask: np.ndarray,
    channel: np.ndarray,
    grid: Any,
    buildings: Any,
    points_rc: Mapping[str, tuple[int, int]],
    box: tuple[int, int, int, int],
    label_offsets: Mapping[str, tuple[float, float]],
    title: str,
) -> None:
    """Study area: catchment, burnt channel, points and a box on the lower reach (+ zoom).

    Args:
        path: Output ``.png``.
        ground: Ground raster (m), full grid.
        mask: Catchment raster.
        channel: Burnt channel raster.
        grid: ``SiteGrid`` of the full raster.
        buildings: OSM footprints (GeoDataFrame, EPSG:4326) drawn in the zoom panel.
        points_rc: ``{name: (row, col)}`` report points (full grid).
        box: Lower reach box ``(row0, row1, col0, col1)``.
        label_offsets: ``{name: (dx, dy)}`` label offsets in points (zoom panel).
        title: Figure title.
    """
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    ext = _imshow_extent(grid, ground.shape)
    shade = _hillshade(ground, grid.cell_m)
    ch = np.ma.masked_where(~channel, np.ones(channel.shape))
    xl, yl = _box_limits(grid, box)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    full_lim = _box_limits(
        grid,
        (max(int(rows[0]) - 6, 0), int(rows[-1]) + 6, max(int(cols[0]) - 6, 0), int(cols[-1]) + 6),
    )
    fig, axes = plt.subplots(
        1, 2, figsize=(14, 8), constrained_layout=True, gridspec_kw={"width_ratios": [1, 1.25]}
    )
    asp = 1.0 / np.cos(np.radians(0.5 * (ext[2] + ext[3])))
    for k, ax in enumerate(axes):
        ax.imshow(
            shade,
            origin="lower",
            extent=ext,
            cmap="gray",
            vmin=0.2,
            vmax=1.0,
            interpolation="nearest",
            zorder=1,
        )
        ax.contour(
            mask.astype(float),
            levels=[0.5],
            origin="lower",
            extent=ext,
            colors="k",
            linewidths=1.0,
            zorder=3,
        )
        ax.imshow(
            ch,
            origin="lower",
            extent=ext,
            cmap="autumn",
            alpha=0.9,
            zorder=2,
            interpolation="nearest",
        )
        ax.set_aspect(asp)
        ax.set_xlabel("경도 (deg)")
        ax.set_ylabel("위도 (deg)")
        for name, rc in points_rc.items():
            lon, lat = _lonlat(grid, rc)
            ax.plot(
                lon, lat, "o", color=OBS if k == 1 else "red", ms=5, zorder=6, mec="white", mew=0.6
            )
            if k == 1:
                dx, dy = label_offsets.get(name, (6, 5))
                ax.annotate(
                    name,
                    (lon, lat),
                    textcoords="offset points",
                    xytext=(dx, dy),
                    fontsize=9,
                    zorder=7,
                    ha="left" if dx >= 0 else "right",
                    bbox={"fc": "white", "ec": "none", "alpha": 0.75, "pad": 1.2},
                )
    axes[0].set_xlim(*full_lim[0])
    axes[0].set_ylim(*full_lim[1])
    axes[0].set_title("냉천 유역 (검은 윤곽)")
    axes[0].add_patch(
        Rectangle((xl[0], yl[0]), xl[1] - xl[0], yl[1] - yl[0], fill=False, ec=OBS, lw=2, zorder=8)
    )
    axes[1].set_xlim(*xl)
    axes[1].set_ylim(*yl)
    axes[1].set_title("하류부 확대 (왼쪽 주황 박스)")
    if buildings is not None and len(buildings):
        sub = buildings.cx[xl[0] : xl[1], yl[0] : yl[1]]
        sub.boundary.plot(ax=axes[1], color="#444444", linewidth=0.25, zorder=4)
    handles = [
        Line2D([], [], color="k", lw=1.0, label="유역 경계"),
        Line2D([], [], color="#ff7f00", lw=4, label="굴착한 하도 (OSM)"),
        Line2D([], [], marker="o", color="w", mfc=OBS, ms=7, label="보고 지점"),
        Line2D([], [], color="#444444", lw=0.5, label="건물 외곽 (OSM)"),
    ]
    axes[1].legend(handles=handles, loc="upper right", fontsize=9, framealpha=0.9)
    fig.suptitle(title, fontsize=14)
    _save(fig, path)


def plot_rain(
    path: Path,
    increments_mm: np.ndarray,
    dt_min: float,
    start: datetime,
    peak_hour: tuple[datetime, datetime],
    published_peak_mm: float,
    published_total_mm: float,
    title: str,
) -> None:
    """Nested hyetograph (mm per step) with the cumulative curve on a panel below.

    Args:
        path: Output ``.png``.
        increments_mm: Rain per step (mm).
        dt_min: Step (min).
        start: Clock time of model t = 0.
        peak_hour: Observed peak hour ``(start, end)``.
        published_peak_mm: Published maximum 60-min depth (mm).
        published_total_mm: Published accumulated depth (mm).
        title: Figure title.
    """
    import matplotlib.dates as mdates

    plt = _plt()
    inc = np.asarray(increments_mm, dtype=float)
    t_left = [start + timedelta(minutes=dt_min * k) for k in range(inc.size)]
    width = dt_min / 1440.0
    n60 = max(round(60.0 / dt_min), 1)
    model_peak = float(np.max(np.convolve(inc, np.ones(n60), mode="valid")))
    total = float(inc.sum())
    fig, (a1, a2) = plt.subplots(
        2,
        1,
        figsize=(11, 8),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.3, 1]},
    )
    a1.bar(
        mdates.date2num(t_left), inc, width=width, align="edge", color=BLUE_LIGHT, ec=BLUE, lw=0.3
    )
    cum = np.cumsum(inc)
    t_right = [t + timedelta(minutes=dt_min) for t in t_left]
    a2.plot(t_right, cum, color=BLUE, lw=2)
    for ax in (a1, a2):
        ax.axvspan(*peak_hour, color=OBS, alpha=0.18, lw=0)
    a1.set_ylabel(f"강우 (mm / {dt_min:g} min)")
    a2.set_ylabel("누적 강우 (mm)")
    a1.text(
        0.015,
        0.97,
        f"모형 60분 최대 {model_peak:.1f} mm  vs  관측 {published_peak_mm:.1f} mm/h\n"
        f"모형 총량 {total:.1f} mm  vs  관측 누적 {published_total_mm:.1f} mm",
        transform=a1.transAxes,
        va="top",
        fontsize=11,
        bbox={"fc": "white", "ec": GREY, "alpha": 0.9, "pad": 4},
    )
    a2.text(
        peak_hour[0] + (peak_hour[1] - peak_hour[0]) / 2,
        0.04,
        "관측 최대 1시간\n04:00–05:00",
        transform=a2.get_xaxis_transform(),
        ha="center",
        va="bottom",
        fontsize=9,
        color=OBS,
    )
    a2.axhline(published_total_mm, color=GREY, ls="--", lw=1)
    a2.text(
        t_left[0], published_total_mm, " 관측 누적 379.7 mm", va="bottom", fontsize=9, color=GREY
    )
    _hour_axis(a2)
    a1.grid(axis="y", color=GREY_LIGHT, lw=0.5)
    a2.grid(axis="y", color=GREY_LIGHT, lw=0.5)
    a1.set_title(title)
    _save(fig, path)


def plot_hyeto_hydrograph(
    path: Path,
    increments_mm: np.ndarray,
    dt_min: float,
    t_q_h: np.ndarray,
    q: np.ndarray,
    refs: Mapping[str, float],
    markers: Sequence[Mapping[str, Any]],
    start: datetime,
    title: str,
) -> None:
    """Rain (inverted, top) and mouth discharge (bottom) on a shared clock axis.

    Args:
        path: Output ``.png``.
        increments_mm: Rain per step (mm).
        dt_min: Step (min).
        t_q_h: Discharge time (h after the model start).
        q: Mouth discharge (m^3/s).
        refs: ``{label: discharge}`` reference lines (directly labelled).
        markers: Vertical markers ``{"when": datetime, "label", "kind": "model"|"obs"}``.
        start: Clock time of model t = 0.
        title: Figure title.
    """
    import matplotlib.dates as mdates

    plt = _plt()
    inc = np.asarray(increments_mm, dtype=float)
    t_left = [start + timedelta(minutes=dt_min * k) for k in range(inc.size)]
    fig, (a1, a2) = plt.subplots(
        2,
        1,
        figsize=(12, 8.5),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1, 2.3], "hspace": 0.04},
    )
    a1.bar(
        mdates.date2num(t_left),
        inc,
        width=dt_min / 1440.0,
        align="edge",
        color=BLUE_LIGHT,
        ec=BLUE,
        lw=0.3,
    )
    a1.invert_yaxis()
    a1.set_ylabel(f"강우 (mm / {dt_min:g} min)")
    a1.set_title(title)
    clock = _clock_array(t_q_h, start)
    a2.plot(clock, q, color=BLUE, lw=2, label="모형 하구 유량")
    x0 = clock[0]
    for label, val in refs.items():
        a2.axhline(val, color=GREY, ls="--", lw=1)
        a2.text(x0, val, f" {label}", va="bottom", ha="left", fontsize=9, color="#444444")
    ymax = max(float(np.max(q)), max(refs.values())) * 1.12
    a2.set_ylim(0, ymax)
    for k, m in enumerate(markers):
        col = OBS if m["kind"] == "obs" else BLUE
        for ax in (a1, a2):
            ax.axvline(m["when"], color=col, ls="-" if m["kind"] == "obs" else ":", lw=1.6)
        a2.text(
            m["when"],
            ymax * (0.30 - 0.0 * k),
            f"{m['label']} ",
            rotation=90,
            ha="right",
            va="top",
            fontsize=9,
            color=col,
            bbox={"fc": "white", "ec": "none", "alpha": 0.8, "pad": 0.8},
        )
    a2.set_ylabel("유량 (m³/s)")
    a2.grid(axis="y", color=GREY_LIGHT, lw=0.5)
    _hour_axis(a2)
    a2.set_xlim(start, clock[-1])
    _save(fig, path)


def _draw_depth(ax: Any, h: np.ndarray, extent: tuple[float, float, float, float]) -> Any:
    cmap, norm = _depth_norm()
    ax.set_facecolor("#ececec")
    return ax.imshow(
        np.ma.masked_invalid(h),
        origin="lower",
        extent=extent,
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
        zorder=1,
    )


def plot_lower_reach_depth(
    path: Path,
    h_full: np.ndarray,
    mask: np.ndarray,
    channel: np.ndarray,
    sea: np.ndarray,
    grid: Any,
    box: tuple[int, int, int, int],
    points_rc: Mapping[str, tuple[int, int]],
    point_depth_m: Mapping[str, float],
    zoom_name: str,
    label_offsets: Mapping[str, tuple[float, float]],
    title: str,
) -> None:
    """Maximum depth of the lower reach with an inset zoom and the depths printed at points.

    Args:
        path: Output ``.png``.
        h_full: Maximum depth (m), full grid, NaN outside the computed domain.
        mask: Catchment raster.
        channel: Burnt channel raster.
        sea: Sea raster.
        grid: ``SiteGrid``.
        box: Lower reach box ``(row0, row1, col0, col1)``.
        points_rc: ``{name: (row, col)}`` report points.
        point_depth_m: ``{name: maximum depth (m)}`` printed at the points.
        zoom_name: Point around which the inset zooms.
        label_offsets: ``{name: (dx, dy)}`` label offsets (points).
        title: Figure title.
    """
    plt = _plt()
    ext = _imshow_extent(grid, h_full.shape)
    h = np.where(sea, np.nan, h_full)
    xl, yl = _box_limits(grid, box)
    fig, ax = plt.subplots(figsize=(11, 10), constrained_layout=True)
    im = _draw_depth(ax, h, ext)
    ch = np.ma.masked_where(~channel, np.ones(channel.shape))
    ax.imshow(
        ch, origin="lower", extent=ext, cmap="autumn", alpha=0.35, zorder=2, interpolation="nearest"
    )
    ax.contour(
        mask.astype(float),
        levels=[0.5],
        origin="lower",
        extent=ext,
        colors="k",
        linewidths=0.8,
        zorder=3,
    )
    ax.set_xlim(*xl)
    ax.set_ylim(*yl)
    ax.set_aspect(1.0 / np.cos(np.radians(0.5 * (ext[2] + ext[3]))))
    ax.set_xlabel("경도 (deg)")
    ax.set_ylabel("위도 (deg)")
    for name, rc in points_rc.items():
        lon, lat = _lonlat(grid, rc)
        ax.plot(lon, lat, "o", color=OBS, ms=6, mec="white", mew=0.7, zorder=6)
        dx, dy = label_offsets.get(name, (6, 5))
        ax.annotate(
            f"{name}\n최대 {point_depth_m[name]:.2f} m",
            (lon, lat),
            textcoords="offset points",
            xytext=(dx, dy),
            fontsize=9,
            zorder=7,
            ha="left" if dx >= 0 else "right",
            bbox={"fc": "white", "ec": GREY, "alpha": 0.85, "pad": 1.5},
        )
    # inset zoom
    zr, zc = points_rc[zoom_name]
    zbox = (
        zr - INSET_HALF_CELLS,
        zr + INSET_HALF_CELLS,
        zc - INSET_HALF_CELLS,
        zc + INSET_HALF_CELLS,
    )
    zx, zy = _box_limits(grid, zbox)
    ins = ax.inset_axes((0.58, 0.60, 0.40, 0.38))
    _draw_depth(ins, h, ext)
    ins.imshow(
        ch, origin="lower", extent=ext, cmap="autumn", alpha=0.35, zorder=2, interpolation="nearest"
    )
    ins.set_xlim(*zx)
    ins.set_ylim(*zy)
    ins.set_aspect(1.0 / np.cos(np.radians(0.5 * (ext[2] + ext[3]))))
    ins.set_xticks([])
    ins.set_yticks([])
    lon, lat = _lonlat(grid, points_rc[zoom_name])
    ins.plot(lon, lat, "o", color=OBS, ms=6, mec="white", mew=0.7)
    ins.set_title(f"{zoom_name} 부근 확대", fontsize=9)
    ax.indicate_inset_zoom(ins, edgecolor="k", lw=1.0)
    cb = fig.colorbar(im, ax=ax, shrink=0.6, label="최대 침수심 (m)", spacing="uniform")
    cb.set_ticks([0.025, 0.15, 0.375, 0.75, 1.5, 3.0, 52.0])
    cb.set_ticklabels(list(DEPTH_LABELS))
    ax.set_title(title)
    _save(fig, path)


def plot_point_series(
    path: Path,
    start: datetime,
    t_h: np.ndarray,
    apt_depth: np.ndarray,
    indeok_above: np.ndarray,
    observations: Sequence[Mapping[str, Any]],
    apt_name: str,
    title: str,
) -> None:
    """Depth at the apartment and water level above the bank at 인덕교 vs clock time.

    Args:
        path: Output ``.png``.
        start: Clock time of model t = 0.
        t_h: Time (h after the model start).
        apt_depth: Model depth at the apartment point (m).
        indeok_above: Model water surface above the 인덕교 bank (m).
        observations: Observation config entries (``depth_at_time`` / ``overtopped_by``).
        apt_name: Name of the apartment point (selects its observations).
        title: Figure title.
    """
    plt = _plt()
    clock = _clock_array(t_h, start)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8.5), sharex=True, constrained_layout=True)
    a1.plot(clock, apt_depth, color=BLUE, lw=2, label="모형 (격자 최저지반 셀)")
    first = True
    for ob in observations:
        if ob["kind"] == "depth_at_time" and ob["point"] == apt_name:
            a1.errorbar(
                _as_datetime(ob["when"]),
                ob["value_m"],
                yerr=ob["tol_m"],
                fmt="D",
                color=OBS,
                ms=8,
                capsize=4,
                lw=1.6,
                zorder=5,
                label="관측 (블랙박스, ±0.1 m)" if first else None,
            )
            first = False
    a1.set_ylabel("침수심 (m)")
    a1.set_title(apt_name, fontsize=11, loc="left")
    a1.legend(loc="upper left", fontsize=10)
    a2.plot(clock, indeok_above, color=BLUE, lw=2, label="모형 수위 - 제방고")
    a2.axhline(0.0, color=GREY, ls="--", lw=1)
    a2.text(clock[0], 0.0, " 제방 높이 (0 = 월류 시작)", va="bottom", fontsize=9, color="#444444")
    for ob in observations:
        if ob["kind"] == "overtopped_by":
            when = _as_datetime(ob["when"])
            for ax in (a1, a2):
                ax.axvline(when, color=OBS, lw=1.6)
            a2.text(
                when,
                0.62,
                " 관측: 06:20에 이미 월류 (CCTV)",
                transform=a2.get_xaxis_transform(),
                va="top",
                ha="left",
                fontsize=10,
                color=OBS,
            )
    a2.set_ylabel("인덕교 수위 - 제방고 (m)")
    a2.legend(loc="center left", fontsize=10)
    for ax in (a1, a2):
        ax.grid(axis="y", color=GREY_LIGHT, lw=0.5)
    obs_times = [_as_datetime(o["when"]) for o in observations]
    a2.set_xlim(min(obs_times) - timedelta(hours=2.0), max(obs_times) + timedelta(hours=2.0))
    _hour_axis(a2, interval=1)
    fig.suptitle(title, fontsize=13)
    _save(fig, path)


def plot_long_profile(
    path: Path,
    profile: Mapping[str, Sequence[float]],
    bridges_km: Mapping[str, float],
    title: str,
) -> None:
    """Classic longitudinal profile: bed, bank ground and maximum water surface vs distance.

    Args:
        path: Output ``.png``.
        profile: Result of :func:`long_profile` (mouth upstream).
        bridges_km: ``{bridge name: distance from the mouth (km)}``.
        title: Figure title.
    """
    plt = _plt()
    x = np.asarray(profile["distance_km"])
    bed = np.asarray(profile["bed_m"])
    bank = np.asarray(profile["bank_m"])
    wse = np.asarray(profile["max_surface_m"])
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 9), constrained_layout=True)
    x_zoom = max(bridges_km.values()) + 1.5
    for k, ax in enumerate((a1, a2)):
        ax.fill_between(
            x,
            bed,
            wse,
            where=wse > bed + 0.01,
            color=BLUE_LIGHT,
            alpha=0.8,
            lw=0,
            label="최대 수면 아래 (물)",
            zorder=2,
        )
        ax.plot(x, wse, color=BLUE, lw=1.6, label="최대 수면", zorder=3)
        ax.plot(x, bank, color=GREY, lw=1.4, label="제방 지반고 (하도 옆 최저)", zorder=3)
        ax.plot(x, bed, color="#5b3a1e", lw=1.8, label="굴착한 하상", zorder=4)
        ax.axhline(0.0, color=GREY, lw=0.6, ls=":")
        for name, xb in bridges_km.items():
            if k == 1 and xb > x_zoom:
                continue
            ax.axvline(xb, color=OBS, lw=1.6, ls="--", zorder=5)
            ax.text(
                xb,
                0.98,
                f" {name}",
                transform=ax.get_xaxis_transform(),
                color=OBS,
                va="top",
                ha="left",
                fontsize=10,
                fontweight="bold",
            )
        ax.set_ylabel("표고 (m)")
        ax.set_xlabel("하구에서 상류로의 거리 (km)")
        ax.grid(color=GREY_LIGHT, lw=0.5)
    sel = x <= x_zoom
    lo = float(min(bed[sel].min(), 0.0)) - 1.0
    hi = float(max(bank[sel].max(), wse[sel].max())) + 2.0
    a2.set_xlim(0, x_zoom)
    a2.set_ylim(lo, hi)
    a1.set_xlim(0, float(x[-1]))
    a1.legend(loc="lower right", fontsize=9)
    a1.set_title(title)
    a2.set_title("하류부 확대 (하구 ~ 인덕교 위 1.5 km)", fontsize=11)
    _save(fig, path)


def plot_depth_classes(path: Path, classes: Sequence[Mapping[str, Any]], title: str) -> None:
    """Horizontal bars of flooded area by maximum-depth class, value printed on each bar.

    Args:
        path: Output ``.png``.
        classes: Result of :func:`depth_class_areas`.
        title: Figure title.
    """
    plt = _plt()
    labels = [
        f"{c['lo']:g}–{c['hi']:g} m" if c["hi"] is not None else f"> {c['lo']:g} m" for c in classes
    ]
    vals = np.array([c["area_km2"] for c in classes])
    ramp = [DEPTH_RAMP[k] for k in (2, 3, 4, 5, 6)][: len(classes)]
    fig, ax = plt.subplots(figsize=(9, 5.5), constrained_layout=True)
    y = np.arange(len(classes))[::-1]
    ax.barh(y, vals, color=ramp, ec=BLUE, lw=0.6)
    for yy, v in zip(y, vals, strict=True):
        ax.text(v + 0.012 * float(vals.max()), yy, f"{v:.2f} km²", va="center", fontsize=11)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, float(vals.max()) * 1.18)
    ax.set_xlabel("침수 면적 (km²), 하도 제외 · 지반고 20 m 미만 육지")
    ax.set_ylabel("최대 침수심 구간")
    ax.grid(axis="x", color=GREY_LIGHT, lw=0.5)
    ax.set_axisbelow(True)
    ax.set_title(title)
    _save(fig, path)


def plot_model_chain(path: Path, title: str) -> None:
    """Box-and-arrow diagram of the model chain with the list of what is not in the model."""
    plt = _plt()
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    steps = [
        ("공표된 강우 깊이–지속시간", "포항 ASOS 최대 강우 (60·120·180·360·720분)"),
        ("중첩 교대블록 강우 곡선", "10분 간격, 가장 큰 블록을 가운데에"),
        ("NRCS CN 손실", "OSM 녹지·시가지로 CN 74 / 85 구분"),
        ("30 m 국지관성 2D 강우–격자 모형", "GLO-30 지반 + OSM 하도 굴착 (약 700 m³/s 용량)"),
        ("해수면 경계 0 m", "하구 바다 셀 고정 (조위 없음)"),
        ("출력", "하구 유량 · 최대 침수심 · 지점 시계열"),
        ("문헌 점검 (진단 전용)", "Hwang 외 (2024) 관측과 비교, 조정하지 않음"),
    ]
    fig, ax = plt.subplots(figsize=(13, 8.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(-3, 66)
    ax.axis("off")
    bw, bh, gap, x0 = 58.0, 6.4, 2.9, 3.0
    top = 62.0
    for k, (head, sub) in enumerate(steps):
        y = top - k * (bh + gap) - bh
        last = k == len(steps) - 1
        ax.add_patch(
            FancyBboxPatch(
                (x0, y),
                bw,
                bh,
                boxstyle="round,pad=0.25,rounding_size=0.8",
                fc="white" if last else "#deebf7",
                ec=OBS if last else BLUE,
                lw=1.6,
                ls="--" if last else "-",
            )
        )
        ax.text(
            x0 + bw / 2,
            y + bh * 0.66,
            head,
            ha="center",
            va="center",
            fontsize=12,
            fontweight="bold",
        )
        ax.text(
            x0 + bw / 2, y + bh * 0.26, sub, ha="center", va="center", fontsize=9.5, color="#333333"
        )
        if k < len(steps) - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (x0 + bw / 2, y - 0.2),
                    (x0 + bw / 2, y - gap + 0.2),
                    arrowstyle="-|>",
                    mutation_scale=16,
                    color=BLUE,
                    lw=1.6,
                )
            )
    lx, lw_, ly_top = 68.0, 29.0, 62.0
    items = [
        "하수관망 (우수관·맨홀 월류)",
        "조위 (해수면 고정)",
        "교량·교각 (하도 단면만)",
        "건물 (지반에서 제거)",
        "오어지 (저수지 조절)",
    ]
    lh = 4.4 * len(items) + 8.0
    ax.add_patch(
        FancyBboxPatch(
            (lx, ly_top - lh),
            lw_,
            lh,
            boxstyle="round,pad=0.25,rounding_size=0.8",
            fc="#f2f2f2",
            ec=GREY,
            lw=1.4,
        )
    )
    ax.text(
        lx + lw_ / 2,
        ly_top - 3.2,
        "모형에 없는 것",
        ha="center",
        va="center",
        fontsize=12,
        fontweight="bold",
        color="#444444",
    )
    for k, it in enumerate(items):
        ax.text(
            lx + 2.0,
            ly_top - 8.0 - 4.4 * k,
            f"×  {it}",
            ha="left",
            va="center",
            fontsize=10.5,
            color="#333333",
        )
    ax.text(
        lx,
        6.0,
        "Hwang 외 (2024)의 TUFLOW + XP-SWMM은\n하수관망·조위·교량을 포함한다.",
        ha="left",
        va="center",
        fontsize=9.5,
        color=GREY,
    )
    ax.set_title(title, fontsize=14)
    _save(fig, path)


# ---------------------------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------------------------


def build_pack(out_dir: Path, obs_path: Path = ASSETS_OBS) -> dict[str, Any]:
    """Build the eight figures, ``figures_pack.json`` and the literature check.

    Reads ``summary.json``, ``hydrograph_mouth.csv``, ``points_depth.csv`` and ``max_depth.tif``
    of the case folder (no new solver run), rebuilds the terrain for the profile and the outline,
    writes the PNGs and ``figures_pack.json`` and adds ``literature_check`` and the assumptions
    A27 / A28 to ``summary.json`` (idempotent).

    Args:
        out_dir: The case folder (``<DATA_ROOT>/derived/case_twins/pohang``).
        obs_path: Observation config JSON.

    Returns:
        The figure pack dict.
    """
    import rasterio

    from climaterisk_worker.twin import case_pohang

    obs = json.loads(obs_path.read_text(encoding="utf-8"))
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    spec = case_pohang.load_case_spec()
    dt_min = float(spec["rain"]["main"]["dt_min"])
    inc = case_pohang.nested_hyetograph(spec["rain"]["main"]["depth_duration_mm"], dt_min)
    pk = obs["rain_peak_hour"]
    c_h = peak_window_center_h(inc, dt_min)
    start = anchor_start(c_h, pk["start"], pk["end"])
    peak_hour = (datetime.fromisoformat(pk["start"]), datetime.fromisoformat(pk["end"]))
    logger.info("clock start %s (model peak-hour centre %.3f h)", start.isoformat(), c_h)

    hyd = read_columns(out_dir / "hydrograph_mouth.csv")
    pts = read_columns(out_dir / "points_depth.csv")
    t_pts = pts["time_h_after_rain_start"]
    series: dict[str, dict[str, Any]] = {}
    for name in pts:
        if name == "time_h_after_rain_start" or name.endswith("_water_surface_above_bank_m"):
            continue
        series[name] = {
            "t_h": t_pts,
            "depth": pts[name],
            "above_bank": pts.get(f"{name}_water_surface_above_bank_m"),
        }
    rows = validation_rows(obs["observations"], series, start)
    n_ok = sum(1 for r in rows if r["ok"])

    # terrain rebuild (channel, outline, profile) and the max-depth grid
    terrain = case_pohang.build_terrain(spec)
    pts_osm = case_pohang.find_points(spec, terrain)
    terrain["points"] = pts_osm
    burn = case_pohang.burn_channels(spec, terrain)
    grid = terrain["grid"]
    ground, domain, sea, mask = (
        terrain["ground"],
        terrain["domain"],
        terrain["sea"],
        terrain["mask"],
    )
    dom_rows = np.flatnonzero(domain.any(axis=1))
    dom_cols = np.flatnonzero(domain.any(axis=0))
    crop = (
        slice(int(dom_rows[0]), int(dom_rows[-1]) + 1),
        slice(int(dom_cols[0]), int(dom_cols[-1]) + 1),
    )
    with rasterio.open(out_dir / "max_depth.tif") as src:
        arr = src.read(1).astype(float)
        nodata = src.nodata
        tf = src.transform
    h_crop = np.where(arr == nodata, np.nan, arr)[::-1]
    shape_crop = (crop[0].stop - crop[0].start, crop[1].stop - crop[1].start)
    if h_crop.shape != shape_crop:
        raise ValueError(
            f"max_depth.tif {h_crop.shape} does not match the terrain crop {shape_crop}"
        )
    from climaterisk_worker.twin import site

    west = grid.west + crop[1].start * grid.cell_m / grid.lon_m_per_deg
    south = grid.south + crop[0].start * grid.cell_m / site.M_PER_DEG
    if not (
        math.isclose(tf.c, west, abs_tol=1e-9)
        and math.isclose(tf.f - shape_crop[0] * (-tf.e), south, abs_tol=1e-9)
    ):
        raise ValueError("max_depth.tif georeferencing does not match the terrain crop")
    h_full = np.full(ground.shape, np.nan)
    h_full[crop] = h_crop

    stem = burn["stem"]
    stem_rc = np.asarray(stem["idx"])
    channel = burn["channel"]

    def stem_k(name: str) -> int:
        ij = pts_osm[name]["ij"]
        return int(np.argmin(((stem_rc - np.asarray(ij)) ** 2).sum(axis=1)))

    k_br = {name: stem_k(name) for name in ("냉천교", "인덕교")}
    apt1 = "우방신세계타운1차 입구(최저지반)"
    apt2 = "우방신세계타운2차 입구(최저지반)"
    pts_rc: dict[str, tuple[int, int]] = {
        name: (int(stem_rc[k, 0]), int(stem_rc[k, 1])) for name, k in k_br.items()
    }
    pts_rc["POSCO"] = tuple(int(v) for v in pts_osm["POSCO"]["ij"])  # type: ignore[assignment]
    for key in spec["points"]["apartments"]:
        cells = np.asarray(pts_osm[key]["cells"])
        low = cells[int(np.argmin(ground[cells[:, 0], cells[:, 1]]))]
        pts_rc[f"{key} 입구(최저지반)"] = (int(low[0]), int(low[1]))
    if apt1 not in pts_rc or apt2 not in pts_rc:
        raise ValueError("apartment points not found")
    box = lower_reach_box(stem_rc, min(k_br.values()), list(pts_rc.values()), LOWER_REACH_PAD_CELLS)
    point_depth = {n: float(summary["points"][n]["max_depth_m"]) for n in pts_rc}

    # long profile along the stem, mouth upstream
    order = stem_rc[::-1]
    bank_r = bank_ground(ground, channel, domain, stem_rc, np.asarray(stem["radius"]))
    prof = long_profile(
        [tuple(c) for c in order.tolist()], burn["bed"], bank_r, h_full, grid.cell_m
    )
    n_stem = stem_rc.shape[0]
    bridges_km = {n: float(prof["distance_km"][n_stem - 1 - k]) for n, k in k_br.items()}

    # depth classes: non-channel land below 20 m (domain, no sea)
    land_low = domain & ~sea & ~channel & (ground < LOW_GROUND_MAX_M)
    classes = depth_class_areas(h_full, grid.cell_m, DEPTH_CLASS_EDGES, mask=land_low)

    # figures
    badge = BADGE
    off_zoom = {
        "냉천교": (-8, 8),
        "인덕교": (8, 8),
        "POSCO": (8, -14),
        apt1: (-14, -12),
        apt2: (12, -20),
    }
    off_reach = {
        "냉천교": (-10, 10),
        "인덕교": (-40, 0),
        "POSCO": (10, -34),
        apt1: (34, 34),
        apt2: (34, -34),
    }
    sm = summary
    mouth_over = {
        n: sm["points"][n]["overtopping_time_h_after_rain_start"] for n in ("냉천교", "인덕교")
    }
    markers = [
        {
            "when": to_clock(mouth_over["인덕교"], start),
            "label": "모형 인덕교 월류",
            "kind": "model",
        },
        {
            "when": to_clock(mouth_over["냉천교"], start),
            "label": "모형 냉천교 월류",
            "kind": "model",
        },
        {
            "when": datetime.fromisoformat(obs["observations"][0]["when"]),
            "label": "관측: 06:20 이미 월류",
            "kind": "obs",
        },
    ]
    dm = sm["discharge_mouth"]
    refs = {
        f"하도 용량 ({dm['channel_capacity_ref_m3s']})": float(dm["channel_capacity_ref_m3s"]),
        f"100년 빈도 ({dm['design_100yr_m3s']})": float(dm["design_100yr_m3s"]),
        f"500년 빈도 ({dm['design_500yr_m3s']})": float(dm["design_500yr_m3s"]),
    }
    total_pub = 379.7
    plot_study_area(
        out_dir / FIGURE_FILES[0],
        ground,
        mask,
        channel,
        grid,
        terrain["buildings"],
        pts_rc,
        box,
        off_zoom,
        f"연구 지역: 냉천 유역과 하류부 — {badge}",
    )
    plot_rain(
        out_dir / FIGURE_FILES[1],
        inc,
        dt_min,
        start,
        peak_hour,
        float(pk["mm"]),
        total_pub,
        f"중첩 강우 곡선과 관측 최대 1시간 — {badge}",
    )
    plot_hyeto_hydrograph(
        out_dir / FIGURE_FILES[2],
        inc,
        dt_min,
        hyd["time_h_after_rain_start"],
        hyd["discharge_m3s"],
        refs,
        markers,
        start,
        f"강우와 하구 유량, 월류 시각 — {badge}",
    )
    plot_lower_reach_depth(
        out_dir / FIGURE_FILES[3],
        h_full,
        mask,
        channel,
        sea,
        grid,
        box,
        pts_rc,
        point_depth,
        "인덕교",
        off_reach,
        f"하류부 최대 침수심 — {badge}",
    )
    plot_point_series(
        out_dir / FIGURE_FILES[4],
        start,
        t_pts,
        pts[apt1],
        pts["인덕교_water_surface_above_bank_m"],
        obs["observations"],
        apt1,
        f"지점별 시계열과 관측 — {badge}",
    )
    plot_long_profile(
        out_dir / FIGURE_FILES[5], prof, bridges_km, f"본류 종단면 (하구에서 상류로) — {badge}"
    )
    plot_depth_classes(
        out_dir / FIGURE_FILES[6], classes, f"최대 침수심 구간별 침수 면적 — {badge}"
    )
    plot_model_chain(out_dir / FIGURE_FILES[7], f"모형 체인과 모형에 없는 것 — {badge}")

    note = (
        "모형 시각 t = 0은 알 수 없으므로, 모형의 가장 센 60분 구간의 중심을 관측 최대 1시간 "
        "(04:00–05:00)의 한가운데에 맞추어 "
        f"{start.isoformat(timespec='minutes')}로 둔 가정(A27)이며, "
        "피크 전후의 시각은 참고용일 뿐이다."
    )
    figures = [
        (
            "냉천 유역 윤곽, 굴착한 하도, 보고 지점, 하류부 박스와 그 확대.",
            "Hwang 외 (2024) Fig. 2",
        ),
        (
            "중첩 강우 곡선(10분 막대)과 누적 곡선. 모형 60분 최대와 총량을 공표값과 비교.",
            "Hwang 외 (2024) Fig. 3a",
        ),
        (
            "강우와 하구 유량. 하도 용량·100년·500년 빈도 유량과 모형·관측 월류 시각.",
            "Hwang 외 (2024) Fig. 3, Fig. 11",
        ),
        ("하류부 최대 침수심, 인덕교 확대, 지점별 최대 침수심.", "Hwang 외 (2024) Fig. 8"),
        (
            "우방신세계타운1차 침수심과 인덕교 제방 대비 수위의 시계열, 관측 표식.",
            "Hwang 외 (2024) Fig. 11, Table 1",
        ),
        ("본류 종단면: 하상, 제방 지반고, 최대 수면, 교량.", "HEC-RAS 종단면 (Jang 외 2025 유형)"),
        (
            "하도 제외 지반고 20 m 미만 육지의 최대 침수심 구간별 면적.",
            "Jang 외 (2025): 얕은 침수 대 깊은 침수",
        ),
        ("모형 체인과 모형에 없는 것.", "Hwang 외 (2024) Fig. 1, Fig. 4"),
    ]
    pack = {
        "badge": BADGE,
        "clock_start": start.isoformat(timespec="minutes"),
        "clock_anchor_note": note,
        "validation": rows,
        "depth_classes": classes,
        "other_models": obs["other_models"],
        "not_compared": obs["not_compared"],
        "figures": [
            {"file": f, "caption_ko": c, "idea_from": i}
            for f, (c, i) in zip(FIGURE_FILES, figures, strict=True)
        ],
    }
    (out_dir / "figures_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary["literature_check"] = {
        "source": obs["source"],
        "clock_start": pack["clock_start"],
        "rows": rows,
        "n_ok": n_ok,
        "n_rows": len(rows),
        "warning": n_ok < len(rows),
    }
    kept = [a for a in summary["assumptions"] if not a.startswith(("A27 ", "A28 "))]
    summary["assumptions"] = [*kept, *obs["assumptions"]]
    summary["n_assumptions"] = len(summary["assumptions"])
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return pack


__all__ = [
    "anchor_start",
    "build_pack",
    "depth_at_clock",
    "depth_class_areas",
    "first_crossing_h",
    "long_profile",
    "peak_window_center_h",
    "to_clock",
    "validation_rows",
]
