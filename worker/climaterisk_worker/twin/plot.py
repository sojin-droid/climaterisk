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
