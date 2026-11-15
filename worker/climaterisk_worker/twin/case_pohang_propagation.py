"""Flood maps at successive clock times for the Pohang Naengcheon case twin (미검증).

The published studies (Hwang et al. 2024) show the inundation at successive times, not only the
maximum. Here ONLY the main storm of :mod:`climaterisk_worker.twin.case_pohang` is rerun, with
the same inputs and solver, and the depth field is kept at the configured clock times (spec
``figures.propagation_clock``; the clock of model t = 0 is the anchoring of assumption A27 in
``figures_pack.json``). The rerun maximum depth must reproduce ``max_depth.tif``: that is the
check that the snapshots belong to the same run as every other figure. Nothing is tuned.

The pure functions (numpy / stdlib only) are the clock conversion, the snapshot keeper and the
flooded area; matplotlib is imported inside the plotting functions.

Algorithm:
    Model time of a clock time $c$ with model start $t_0$:
    $$ t = (c - t_0)\\ \\text{in seconds} $$
    ASCII: t = (clock - clock_start) in seconds.

    Flooded area of a depth field $h$ on the mask $M$ above the threshold $\\theta$:
    $$ A = n\\{ h_{ij} > \\theta,\\ (i,j) \\in M \\} \\cdot \\Delta x^2 / 10^6 $$
    ASCII: A = count(h > theta within mask) * cell_m^2 / 1e6.

    $h$ depth (m), $\\theta$ threshold (m), $M$ mask of non-channel land below 20 m (the
    ``land_low`` of the depth classes), $\\Delta x$ cell size (m), $A$ area (km2).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BADGE = "미검증"
FIGURE_FILES = ("fig_propagation.png", "fig_flooded_area_time.png")
AREA_SAMPLE_S = 60.0  # the flooded area is evaluated once per model minute
MASK_KO = "하도를 뺀 지반고 20 m 미만 땅(바다 제외)만 센다. 산지와 하도는 침수 면적에서 빠진다."


def model_seconds(clock_times: Sequence[str | datetime], start: datetime) -> list[float]:
    """Seconds after model t = 0 for clock times.

    Args:
        clock_times: ISO strings (or datetimes), strictly increasing.
        start: Clock time of model t = 0.

    Returns:
        $t = (c - t_0)$ in seconds for every clock time.

    Raises:
        ValueError: A time lies before ``start`` or the list is not strictly increasing.
    """
    times = [c if isinstance(c, datetime) else datetime.fromisoformat(c) for c in clock_times]
    out: list[float] = []
    for c in times:
        if c < start:
            raise ValueError(f"clock time {c.isoformat()} is before the model start")
        if out and (c - start).total_seconds() <= out[-1]:
            raise ValueError("clock times must be strictly increasing")
        out.append((c - start).total_seconds())
    return out


class Snapshots:
    """Keeps a float32 copy of the depth field at the first step at or after requested times.

    Args:
        times_s: Requested model times (s).
        on_offer: Optional ``f(t, h)`` called on every offer (read-only use of ``h``).
    """

    def __init__(
        self,
        times_s: Sequence[float],
        on_offer: Callable[[float, np.ndarray], None] | None = None,
    ) -> None:
        self.times_s = [float(t) for t in times_s]
        self._on_offer = on_offer
        self._got: dict[int, tuple[float, np.ndarray]] = {}

    def offer(self, t: float, h: np.ndarray) -> None:
        """Offer the depth field ``h`` at model time ``t`` (s); copies it when it is needed."""
        if self._on_offer is not None:
            self._on_offer(t, h)
        for k, big_t in enumerate(self.times_s):
            if k not in self._got and t >= big_t:
                self._got[k] = (float(t), np.array(h, dtype=np.float32, copy=True))

    @property
    def taken(self) -> list[tuple[float, float, np.ndarray]]:
        """``(requested T, actual t, array)`` of the snapshots taken, in request order."""
        return [(self.times_s[k], *self._got[k]) for k in sorted(self._got)]

    @property
    def missing(self) -> list[float]:
        """Requested times never reached."""
        return [big_t for k, big_t in enumerate(self.times_s) if k not in self._got]


def flooded_area_km2(h: np.ndarray, cell_m: float, mask: np.ndarray, threshold_m: float) -> float:
    """Area of the masked cells deeper than the threshold (km2).

    Algorithm:
        $$ A = n\\{ h_{ij} > \\theta,\\ (i,j) \\in M \\} \\cdot \\Delta x^2 / 10^6 $$
        ASCII: count(h > theta within mask) * cell_m^2 / 1e6.

    Args:
        h: Depth (m).
        cell_m: Cell size $\\Delta x$ (m).
        mask: Boolean mask $M$, same shape as ``h``.
        threshold_m: Threshold $\\theta$ (m); strictly deeper counts.

    Returns:
        Flooded area (km2).
    """
    n = int(np.count_nonzero((np.asarray(h) > threshold_m) & np.asarray(mask, dtype=bool)))
    return float(n) * float(cell_m) ** 2 / 1.0e6


# ---------------------------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------------------------


def plot_propagation(
    path: Path,
    frames: Sequence[tuple[datetime, np.ndarray]],
    channel: np.ndarray,
    grid: Any,
    box: tuple[int, int, int, int],
    points_rc: dict[str, tuple[int, int]],
    title: str,
) -> None:
    """Small multiples of the lower reach, one panel per clock time, one shared colour bar.

    Args:
        path: Output ``.png``.
        frames: ``(clock time, depth (m), full grid, NaN where not drawn)``.
        channel: Burnt channel raster, full grid.
        grid: ``SiteGrid``.
        box: Lower reach box ``(row0, row1, col0, col1)``.
        points_rc: ``{name: (row, col)}`` of the reported points.
        title: Suptitle.
    """
    from climaterisk_worker.twin import case_pohang_figures as figs

    plt = figs._plt()
    ext = figs._imshow_extent(grid, frames[0][1].shape)
    xl, yl = figs._box_limits(grid, box)
    n = len(frames)
    ncol = 4 if n > 6 else 3
    nrow = -(-n // ncol)
    fig, axes = plt.subplots(
        nrow, ncol, figsize=(3.0 * ncol + 1.4, 4.4 * nrow + 0.8), constrained_layout=True
    )
    flat = np.atleast_1d(axes).ravel()
    ch = np.ma.masked_where(~channel, np.ones(channel.shape))
    asp = 1.0 / np.cos(np.radians(0.5 * (ext[2] + ext[3])))
    im = None
    for k, ax in enumerate(flat):
        if k >= n:
            ax.axis("off")
            continue
        when, h = frames[k]
        im = figs._draw_depth(ax, h, ext)
        ax.imshow(
            ch, origin="lower", extent=ext, cmap="autumn", alpha=0.2, zorder=2,
            interpolation="nearest",
        )  # fmt: skip
        for rc in points_rc.values():
            lon, lat = figs._lonlat(grid, rc)
            ax.plot(lon, lat, "o", color=figs.OBS, ms=4, mec="white", mew=0.5, zorder=6)
        ax.set_xlim(*xl)
        ax.set_ylim(*yl)
        ax.set_aspect(asp)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(when.strftime("%H:%M"), fontsize=13)
    cb = fig.colorbar(im, ax=list(flat[:n]), shrink=0.7, label="침수심 (m)", spacing="uniform")
    cb.set_ticks([0.025, 0.15, 0.375, 0.75, 1.5, 3.0, 52.0])
    cb.set_ticklabels(list(figs.DEPTH_LABELS))
    fig.suptitle(title, fontsize=14)
    figs._save(fig, path)


def plot_flooded_area_time(
    path: Path,
    clock: Sequence[datetime],
    area_km2: Sequence[float],
    snapshot_clock: Sequence[datetime],
    observed: Sequence[tuple[datetime, str]],
    threshold_m: float,
    title: str,
) -> None:
    """Flooded area against clock time, with the snapshot and observed times marked.

    Args:
        path: Output ``.png``.
        clock: Clock time of every area sample.
        area_km2: Flooded area (km2) at every sample.
        snapshot_clock: Clock times of the snapshots (grey dotted lines).
        observed: ``(clock time, label)`` of the published observations (accent lines).
        threshold_m: Depth threshold (m), for the axis label.
        title: Figure title.
    """
    from climaterisk_worker.twin import case_pohang_figures as figs

    plt = figs._plt()
    fig, ax = plt.subplots(figsize=(11, 5.5), constrained_layout=True)
    ax.plot(clock, area_km2, color=figs.BLUE, lw=2.0, zorder=3)
    for k, when in enumerate(snapshot_clock):
        ax.axvline(when, color=figs.GREY, ls=":", lw=1.2, zorder=1,
                   label="그림 시각(위 그림의 패널)" if k == 0 else None)  # fmt: skip
    for k, (when, _lab) in enumerate(observed):
        ax.axvline(when, color=figs.OBS, ls="--", lw=1.3, zorder=2,
                   label="관측 시각(논문)" if k == 0 else None)  # fmt: skip
    ax.set_ylim(bottom=0.0)
    ax.set_ylabel(f"침수 면적 (km², 수심 {threshold_m:g} m 초과)")
    figs._hour_axis(ax, interval=1)
    ax.legend(loc="upper left", fontsize=10)
    ax.set_title(title)
    figs._save(fig, path)


# ---------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------


def build_propagation(out_dir: Path) -> dict[str, Any]:
    """Rerun the main storm with depth snapshots and write ``propagation.json`` + two figures.

    Rebuilds the inputs with :func:`case_pohang.build_case_inputs` (the same as the stored run),
    reruns ONLY the main storm, compares the rerun maximum depth with ``max_depth.tif`` on the
    domain cells and keeps the depth at each configured clock time. The flooded area is also
    evaluated once per model minute for the time series.

    Args:
        out_dir: The case folder (``<DATA_ROOT>/derived/case_twins/pohang``); needs
            ``figures_pack.json`` and ``max_depth.tif``.

    Returns:
        The content of ``propagation.json``.
    """
    import rasterio

    from climaterisk_worker.twin import case_pohang
    from climaterisk_worker.twin import case_pohang_figures as figs

    t_wall = time.time()
    spec = case_pohang.load_case_spec()
    fg = spec["figures"]
    clocks = [str(c) for c in fg["propagation_clock"]]
    theta = float(fg["propagation_threshold_m"])
    atol = float(fg["propagation_reproduce_atol_m"])
    pack = json.loads((out_dir / "figures_pack.json").read_text(encoding="utf-8"))
    clock_start = str(pack["clock_start"])
    start = datetime.fromisoformat(clock_start)
    times_s = model_seconds(clocks, start)
    obs = json.loads(figs.ASSETS_OBS.read_text(encoding="utf-8"))

    inp = case_pohang.build_case_inputs(spec)
    terrain, burn, crop = inp["terrain"], inp["burn"], inp["crop"]
    grid = terrain["grid"]
    ground, domain, sea = terrain["ground"], terrain["domain"], terrain["sea"]
    channel = burn["channel"]
    cell = float(grid.cell_m)
    land_low = domain & ~sea & ~channel & (ground < figs.LOW_GROUND_MAX_M)
    low_c = land_low[crop]
    dom_c = domain[crop]

    area_t: list[float] = []
    area_v: list[float] = []
    next_s = [0.0]

    def hook(t: float, h: np.ndarray) -> None:
        if t >= next_s[0]:
            area_t.append(t)
            area_v.append(flooded_area_km2(h, cell, low_c, theta))
            while next_s[0] <= t:
                next_s[0] += AREA_SAMPLE_S

    res = case_pohang.run_storm(
        spec, terrain, burn, inp["cn"], inp["main_inc"], inp["rec_points"], inp["bank"], crop,
        "main-propagation", snapshot_s=times_s, snapshot_hook=hook,
    )  # fmt: skip
    snaps: Snapshots = res["snapshots"]
    if snaps.missing:
        raise ValueError(f"snapshot times never reached (s): {snaps.missing}")

    # reproduce check against the stored maximum depth (domain cells)
    with rasterio.open(out_dir / "max_depth.tif") as src:
        arr = src.read(1).astype(float)
        nodata = src.nodata
    stored = np.where(arr == nodata, np.nan, arr)[::-1]
    if stored.shape != dom_c.shape:
        raise ValueError(f"max_depth.tif {stored.shape} does not match the crop {dom_c.shape}")
    diff = np.abs(np.asarray(res["h_max"], dtype=float) - stored)[dom_c]
    max_diff = float(np.nanmax(diff))
    ok = bool(max_diff <= atol)
    logger.info("reproduce: max abs diff %.6f m (atol %.4f m) ok=%s", max_diff, atol, ok)

    # frames
    full_shape = ground.shape
    draw_c = dom_c & ~sea[crop]
    frames_json: list[dict[str, Any]] = []
    frames_fig: list[tuple[datetime, np.ndarray]] = []
    for (big_t, t_act, arr_s), clk in zip(snaps.taken, clocks, strict=True):
        h_full = np.full(full_shape, np.nan)
        h_full[crop] = np.where(draw_c, arr_s.astype(float), np.nan)
        frames_fig.append((datetime.fromisoformat(clk), h_full))
        frames_json.append(
            {
                "clock": clk,
                "model_h": round(big_t / 3600.0, 4),
                "actual_h": round(t_act / 3600.0, 4),
                "flooded_km2": round(flooded_area_km2(arr_s, cell, low_c, theta), 4),
            }
        )

    # box and points as the lower-reach figure
    stem_rc = np.asarray(burn["stem"]["idx"])
    pts_osm = terrain["points"]

    def stem_k(name: str) -> int:
        ij = pts_osm[name]["ij"]
        return int(np.argmin(((stem_rc - np.asarray(ij)) ** 2).sum(axis=1)))

    k_min = min(stem_k("냉천교"), stem_k("인덕교"))
    points_rc = {n: (int(v[0]), int(v[1])) for n, v in inp["rec_points"].items()}
    box = figs.lower_reach_box(stem_rc, k_min, list(points_rc.values()), figs.LOWER_REACH_PAD_CELLS)

    caption_map = (
        "하류부(인덕교~하구와 아파트·POSCO 부근)의 침수심을 시각별로 나란히 보인 그림이다. "
        "파란색이 짙을수록 깊고, 주황색 점이 보고 지점이다. 각 칸의 시각은 모형 시간을 "
        "가정 A27로 시계에 맞춘 값이며, 실측 침수 지도와 비교하지 않았다."
    )
    caption_area = (
        f"하도를 뺀 지반고 20 m 미만 땅에서 수심이 {theta:g} m를 넘는 면적을 시각별로 나타낸 "
        "그림이다. 회색 점선은 위 그림의 시각, 주황색 점선은 논문에 실린 관측 시각이다. "
        "모형 시각은 가정 A27로 정했으며 아직 검증되지 않았다."
    )
    figs_dir = out_dir
    plot_propagation(
        figs_dir / FIGURE_FILES[0], frames_fig, channel, grid, box, points_rc,
        f"하류부 침수 범위의 시간 변화 (수심 색 구분은 최대 침수심 그림과 같음) — {BADGE}",
    )  # fmt: skip
    plot_flooded_area_time(
        figs_dir / FIGURE_FILES[1],
        figs._clock_array(np.asarray(area_t) / 3600.0, start),
        area_v,
        [f[0] for f in frames_fig],
        [(datetime.fromisoformat(o["when"]), str(o["label_ko"])) for o in obs["observations"]],
        theta,
        f"시각별 침수 면적 — {BADGE}",
    )

    result: dict[str, Any] = {
        "badge": BADGE,
        "clock_start": clock_start,
        "threshold_m": theta,
        "mask_ko": MASK_KO,
        "frames": frames_json,
        "reproduce": {"max_abs_diff_m": round(max_diff, 6), "atol_m": atol, "ok": ok},
        "area_samples": {"n": len(area_t), "step_s": AREA_SAMPLE_S},
        "runtime_s": round(time.time() - t_wall, 1),
        "figures": [
            {"file": FIGURE_FILES[0], "caption_ko": caption_map},
            {"file": FIGURE_FILES[1], "caption_ko": caption_area},
        ],
    }
    (out_dir / "propagation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("propagation: %d frames, reproduce ok=%s", len(frames_json), ok)
    return result
