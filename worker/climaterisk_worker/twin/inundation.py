"""River inundation of the synthetic city with the 2D hydraulic model (AGENTS.md step 6B).

Replaces the bathtub stage law (``flood.flood_depths``) by a time-stepped flood: the river
along the bank follows a stage hydrograph that peaks at the same stage $h(T)$ as the stage
law, and :func:`climaterisk_worker.twin.hydraulics.run` moves the water inland over the
city terrain. The peak depth at each asset becomes the RF intensity, priced by the same
engine. Repo-specific method; every parameter is an assumption in the spec's
``hydraulic`` section.
"""

from __future__ import annotations

import math
from typing import Any

from climaterisk_worker.twin.hydraulics import run as run_hydraulics


def city_raster(graph: Any, spec: dict[str, Any]) -> tuple[Any, float, int]:
    """Bed elevation raster of the city plus one river row, and the refinement factor.

    The street grid has nodes every ``spacing_m``; the raster refines that by
    ``k = spacing_m / cell_m`` (an even integer, so road midpoints fall on a cell centre)
    and interpolates ground elevation bilinearly between nodes, so a node's cell keeps the
    node's exact ground level. Row 0 of the raster is the river channel at
    ``river_bed_m``; city row ``r`` sits at raster row ``1 + r k``.

    Algorithm:
        $$z(r + a, c + b) = (1-a)(1-b) z_{r,c} + a(1-b) z_{r+1,c} + (1-a) b z_{r,c+1}
          + a b\\, z_{r+1,c+1},\\quad a, b \\in [0, 1)$$
        ASCII: z = bilinear blend of the four surrounding node elevations (m).

    Returns:
        ``(bed_z, cell_m, k)``: bed elevation (m) of shape
        ``(2 + (rows-1) k, 1 + (cols-1) k)``, the cell size (m) and the refinement factor.
    """
    import numpy as np

    rows, cols = int(spec["grid"]["rows"]), int(spec["grid"]["cols"])
    spacing = float(spec["grid"]["spacing_m"])
    hyd = spec["hydraulic"]
    k = round(spacing / float(hyd["cell_m"]))
    if k < 2 or k % 2 or not math.isclose(spacing / k, float(hyd["cell_m"])):
        raise ValueError("hydraulic.cell_m must divide grid.spacing_m an even number of times")
    z_nodes = np.array([[graph.nodes[(r, c)]["z_m"] for c in range(cols)] for r in range(rows)])
    fr = np.arange((rows - 1) * k + 1) / k
    fc = np.arange((cols - 1) * k + 1) / k
    r0 = np.minimum(fr.astype(int), rows - 2)
    c0 = np.minimum(fc.astype(int), cols - 2)
    a = (fr - r0)[:, None]
    b = (fc - c0)[None, :]
    z00 = z_nodes[r0][:, c0]
    z10 = z_nodes[r0 + 1][:, c0]
    z01 = z_nodes[r0][:, c0 + 1]
    z11 = z_nodes[r0 + 1][:, c0 + 1]
    land = (1 - a) * (1 - b) * z00 + a * (1 - b) * z10 + (1 - a) * b * z01 + a * b * z11
    river = np.full((1, land.shape[1]), float(hyd["river_bed_m"]))
    return np.vstack([river, land]), spacing / k, k


def asset_cells(assets: list[dict[str, Any]], k: int) -> tuple[Any, Any]:
    """Raster ``(rows, cols)`` index of every asset: its node, or its road's midpoint."""
    import numpy as np

    rr, cc = [], []
    for a in assets:
        if "node" in a:
            r, c = a["node"]
            rr.append(1 + r * k)
            cc.append(c * k)
        else:
            (r1, c1), (r2, c2) = a["edge"]
            rr.append(1 + (r1 + r2) * k // 2)
            cc.append((c1 + c2) * k // 2)
    return np.array(rr), np.array(cc)


def stage_hydrograph(t_s: float, peak_m: float, base_m: float, duration_s: float) -> float:
    """River stage (m above datum) at time ``t_s``: a raised-cosine flood wave.

    Algorithm:
        $$\\eta(t) = \\eta_b + (\\eta_p - \\eta_b)\\,\\tfrac12\\big(1 - \\cos(2\\pi t / D)\\big),
          \\quad 0 \\le t \\le D$$
        ASCII: eta(t) = base + (peak - base) * 0.5 * (1 - cos(2 pi t / D)); base after D.

    $\\eta_b$ base stage (m), $\\eta_p$ peak stage (m) reached at $t = D/2$, $D$ flood
    duration (s).
    """
    if t_s >= duration_s:
        return base_m
    return base_m + (peak_m - base_m) * 0.5 * (1.0 - math.cos(2.0 * math.pi * t_s / duration_s))


def peak_stages(spec: dict[str, Any]) -> Any:
    """Peak river stage per return period, from the same law as the bathtub model (m)."""
    import numpy as np

    fl = spec["flood"]
    t = np.asarray(sorted(fl["return_periods"]), dtype=float)
    return float(fl["stage_2y_m"]) + float(fl["stage_slope_m"]) * np.log(t / 2.0)


def _wave_run(
    bed: Any,
    cell: float,
    spec: dict[str, Any],
    base_m: float,
    peak_m: float,
    duration_s: float,
    t_end_s: float,
    snapshot_every_s: float | None = None,
) -> tuple[Any, dict[str, float]]:
    """One 2D run with row 0 of ``bed`` driven by a raised-cosine stage wave.

    Row 0 starts at ``max(base - z, 0)`` and follows :func:`stage_hydrograph` to
    ``peak_m`` and back over ``duration_s``; the run lasts ``t_end_s``. Manning ``n`` and the
    CFL coefficient come from ``spec["hydraulic"]``.

    Returns:
        ``(result, diagnostics)``: the :func:`hydraulics.run` result dict and the
        ``peak_stage_m``, ``max_froude``, ``boundary_volume_m3``, ``stored_volume_m3``
        (change since $t=0$, m^3) and ``n_steps``.
    """
    import numpy as np

    hyd = spec["hydraulic"]
    cols = np.arange(bed.shape[1])
    rows = np.zeros_like(cols)
    h0 = np.zeros_like(bed)
    h0[0, :] = np.maximum(base_m - bed[0, :], 0.0)
    initial_volume = float(h0.sum() * cell * cell)

    def stage(t: float) -> tuple[Any, Any, float]:
        return rows, cols, stage_hydrograph(t, peak_m, base_m, duration_s)

    res = run_hydraulics(
        h0,
        bed,
        cell,
        cell,
        float(hyd["manning_n"]),
        t_end_s,
        alpha_cfl=float(hyd["alpha_cfl"]),
        stage_fn=stage,
        snapshot_every_s=snapshot_every_s,
    )
    stored = float(res["h"].sum() * cell * cell)
    return res, {
        "peak_stage_m": float(peak_m),
        "max_froude": res["max_froude"],
        "boundary_volume_m3": res["boundary_volume_m3"],
        "stored_volume_m3": stored - initial_volume,
        "n_steps": float(res["n_steps"]),
    }


def hydraulic_depths(
    graph: Any, assets: list[dict[str, Any]], spec: dict[str, Any]
) -> tuple[Any, list[dict[str, float]]]:
    """Peak flood depth per return period at every asset, from the 2D model.

    One hydraulic run per return period: the river row follows
    :func:`stage_hydrograph` up to that period's stage-law peak and back, Manning
    friction ``manning_n`` everywhere, closed outer walls, and the run continues for
    ``recession_s`` after the wave so water can settle.

    Returns:
        ``(depth, diagnostics)``: depth matrix (m) of shape ``(n_events, n_assets)`` in
        ascending return-period order, and per event the ``max_froude``,
        ``boundary_volume_m3``, ``stored_volume_m3`` and ``n_steps``.
    """
    import numpy as np

    hyd = spec["hydraulic"]
    bed, cell, k = city_raster(graph, spec)
    rows_a, cols_a = asset_cells(assets, k)
    base = float(hyd["base_stage_m"])
    duration = float(hyd["flood_duration_h"]) * 3600.0
    t_end = duration + float(hyd["recession_h"]) * 3600.0

    depths, diags = [], []
    for peak in peak_stages(spec):
        res, diag = _wave_run(bed, cell, spec, base, float(peak), duration, t_end)
        depths.append(res["h_max"][rows_a, cols_a])
        diags.append(diag)
    return np.vstack(depths), diags
