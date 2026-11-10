"""Static two-panel map of the synthetic city.

Panel 1: 100-year flood depth with out-of-service nodes. Panel 2: EAL as a percentage of
each lot's value. Uses the non-interactive Agg backend (no server).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def plot_city(
    graph: Any,
    assets: list[dict[str, Any]],
    hazard: Any,
    rows: list[Any],
    spec: dict[str, Any],
    path: Path,
) -> None:
    """Two panels: 100-year depth with failed nodes, and per-lot EAL as % of value.

    Args:
        graph: NetworkX street graph (must carry ``x_m``, ``y_m``, ``kind`` on nodes).
        assets: Asset dicts (one per node and one per edge).
        hazard: CLIMADA ``Hazard`` (uses ``event_name`` and ``intensity``).
        rows: Engine ``ResultRow`` list (same order as ``assets``); uses
            ``eal_as_pct_of_assets``.
        spec: City specification (uses ``network.failure_depth_m``).
        path: Output image path (``.png``).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    names = list(hazard.event_name)
    e = names.index("rp100") if "rp100" in names else len(names) - 1
    depth = hazard.intensity.toarray()[e]
    limit = float(spec["network"]["failure_depth_m"])
    pos = {n: (graph.nodes[n]["x_m"], graph.nodes[n]["y_m"]) for n in graph.nodes}
    nodes = [(i, a) for i, a in enumerate(assets) if "node" in a]
    xy = np.array([pos[a["node"]] for _, a in nodes])
    pct = np.array([(rows[i].eal_as_pct_of_assets or 0.0) for i, _ in nodes])
    d = np.array([depth[i] for i, _ in nodes])

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), constrained_layout=True)
    for ax in axes:
        for a, b in graph.edges:
            ax.plot(*zip(pos[a], pos[b], strict=True), color="#bbbbbb", lw=0.6, zorder=1)
        ax.axhspan(-120, -20, color="#6baed6", alpha=0.6)
        ax.set_aspect("equal")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("distance inland (m)")
    sc = axes[0].scatter(
        xy[:, 0], xy[:, 1], c=d, cmap="Blues", s=40, zorder=2, edgecolor="k", lw=0.3
    )
    fail = d > limit
    axes[0].scatter(
        xy[fail, 0],
        xy[fail, 1],
        marker="x",
        color="red",
        s=25,
        zorder=3,
        label=f"out of service (> {limit} m)",
    )
    for kind, mk in (("substation", "s"), ("hospital", "P")):
        sel = np.array([a["kind"] == kind for _, a in nodes])
        axes[0].scatter(
            xy[sel, 0],
            xy[sel, 1],
            marker=mk,
            facecolor="none",
            edgecolor="orange",
            s=160,
            lw=2,
            zorder=4,
            label=kind,
        )
    axes[0].set_title("100-year flood depth (m) - synthetic river at the bottom")
    axes[0].legend(loc="upper right", fontsize=8)
    fig.colorbar(sc, ax=axes[0], shrink=0.8, label="depth (m)")
    sc2 = axes[1].scatter(
        xy[:, 0], xy[:, 1], c=pct, cmap="OrRd", s=40, zorder=2, edgecolor="k", lw=0.3
    )
    axes[1].set_title("EAL as % of lot value (engine ResultRow)")
    fig.colorbar(sc2, ax=axes[1], shrink=0.8, label="EAL %")
    fig.suptitle("Synthetic virtual city - all inputs assumed (synthetic_city_spec.json)")
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _korean_font(plt: Any) -> None:
    """Prefer an installed Hangul font so place names render (falls back to DejaVu Sans)."""
    plt.rcParams["font.sans-serif"] = [
        "Apple SD Gothic Neo",
        "AppleGothic",
        "Malgun Gothic",
        "NanumGothic",
        "Noto Sans CJK KR",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def plot_case_depth_map(
    path: Path,
    h_max: Any,
    extent: tuple[float, float, float, float],
    buildings: Any,
    channel: Any,
    mask: Any,
    points: dict[str, tuple[float, float]],
    title: str,
) -> None:
    """Maximum flood depth over a real catchment with OSM buildings, channel and points.

    Args:
        path: Output ``.png``.
        h_max: Maximum depth (m), ``(ny, nx)`` row 0 = south, NaN where not computed.
        extent: ``(west, east, south, north)`` of the raster (deg).
        buildings: GeoDataFrame of OSM footprints (EPSG:4326), drawn as thin outlines.
        channel: Boolean raster of burnt channel cells.
        mask: Boolean raster of the catchment (its outline is drawn).
        points: ``{name: (lon, lat)}`` report points.
        title: Figure title.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    _korean_font(plt)
    fig, ax = plt.subplots(figsize=(10, 11), constrained_layout=True)
    from matplotlib import colors as mcolors

    depth = np.ma.masked_invalid(np.asarray(h_max, dtype=float))
    # Board colour system (docs/BOARD_COLORS_KO.md, water_depth_m): fixed breaks, water family
    # ramp (ColorBrewer Blues), shared near-white below the first class.
    ramp = ["#f7f7f5", "#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c", "#08306b"]
    bounds = [0.0, 0.05, 0.25, 0.5, 1.0, 2.0, 4.0, 100.0]
    depth_cmap = mcolors.ListedColormap(ramp)
    depth_norm = mcolors.BoundaryNorm(bounds, depth_cmap.N)
    im = ax.imshow(
        depth,
        origin="lower",
        extent=extent,
        cmap=depth_cmap,
        norm=depth_norm,
        interpolation="nearest",
        zorder=1,
    )
    ax.contour(
        np.asarray(mask, dtype=float), levels=[0.5], origin="lower", extent=extent,
        colors="k", linewidths=0.6, zorder=3,
    )  # fmt: skip
    ch = np.ma.masked_where(~np.asarray(channel, dtype=bool), np.ones(np.shape(channel)))
    ax.imshow(ch, origin="lower", extent=extent, cmap="autumn", alpha=0.8, zorder=2)
    if buildings is not None and len(buildings):
        west, east, south, north = extent
        sub = buildings.cx[west:east, south:north]
        sub.boundary.plot(ax=ax, color="#555555", linewidth=0.2, zorder=4)
    for name, (lon, lat) in points.items():
        ax.plot(lon, lat, "o", color="red", ms=5, zorder=6)
        ax.annotate(
            name, (lon, lat), textcoords="offset points", xytext=(5, 4), fontsize=8, zorder=7
        )
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect(1.0 / np.cos(np.radians(0.5 * (extent[2] + extent[3]))))
    ax.set_xlabel("longitude (deg)")
    ax.set_ylabel("latitude (deg)")
    ax.set_title(title)
    cb = fig.colorbar(im, ax=ax, shrink=0.6, label="max depth (m)", spacing="uniform")
    cb.set_ticks([0.025, 0.15, 0.375, 0.75, 1.5, 3.0, 52.0])
    cb.set_ticklabels(["< 0.05", "0.05–0.25", "0.25–0.5", "0.5–1", "1–2", "2–4", "> 4"])
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_case_hydrograph(
    path: Path,
    t_h: Any,
    q: Any,
    refs: dict[str, float],
    title: str,
) -> None:
    """Outlet hydrograph with reference discharges as horizontal lines.

    Args:
        path: Output ``.png``.
        t_h: Time (h after the rain start).
        q: Discharge (m^3/s).
        refs: ``{label: discharge}`` reference lines.
        title: Figure title.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _korean_font(plt)
    fig, ax = plt.subplots(figsize=(9, 4.5), constrained_layout=True)
    ax.plot(t_h, q, color="#08519c", lw=1.4, label="model, outlet (10-min mean)")
    for (label, v), c in zip(refs.items(), ("#e6550d", "#756bb1", "#636363"), strict=False):
        ax.axhline(v, color=c, ls="--", lw=1.0, label=label)
    ax.set_xlabel("time after the start of the 12-h storm window (h)")
    ax.set_ylabel("discharge (m$^3$/s)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.savefig(path, dpi=130)
    plt.close(fig)
