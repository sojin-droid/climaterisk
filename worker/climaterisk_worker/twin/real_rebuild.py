"""Rebuilt Gangnam twin from real data only (step R6): helpers for ``--real``.

Real buildings (OSM footprints as walls), real terrain (Copernicus GLO-30 bare earth), real
land cover (OSM green polygons -> curve numbers) and run-on from the real catchment outside
the site box (D8 on the padded ground). No basements and no building attribute that is not
in OSM. The one free parameter is the uniform sewer capacity, chosen on one observed event.

Algorithm:
    Contingency scores over the wall buildings (h hits, f false alarms, m misses):
    $$CSI = \\frac{h}{h+f+m},\\quad POD = \\frac{h}{h+m},\\quad FAR = \\frac{f}{h+f},\\quad
    B = \\frac{h+f}{h+m}$$
    Calibrated sewer capacity $s^* = \\arg\\max_s CSI(s)$ over the spec grid.
    Volume balance of a run: $V_{rain} + V_{in} = V_{stored} + V_{sewer} + V_{out} +
    V_{sink}$ (m^3).
    ASCII: CSI = h/(h+f+m); s* = argmax CSI(s); rain + inflow = stored + sewer + outflow + sink.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from climaterisk_worker.twin import landcover, pluvial, rain_eal, runon, site_loss, validate
from climaterisk_worker.twin.site import build_site

logger = logging.getLogger(__name__)


def build_real_twin(spec: dict[str, Any]) -> dict[str, Any]:
    """Assemble the R6 twin: site, green curve numbers and the run-on cells.

    Args:
        spec: City spec (``site``, ``landcover``, ``runon`` sections).

    Returns:
        Dict with ``site`` (``build_site`` result), ``cn`` (curve-number raster), ``green``
        (boolean raster), ``n_green_polygons``, ``inflow`` (``(rows, cols, area_m2)`` in
        site coordinates), ``runon_stats`` and ``padded_shape``.
    """
    from climaterisk import paths

    site = build_site(spec)
    bbox = tuple(float(v) for v in spec["site"]["bbox"])
    polys = landcover.read_green(paths.osm_dir(spec["site"]["osm_file"]), bbox, spec)  # type: ignore[arg-type]
    green = landcover.green_mask(site["grid"], polys)
    ext = runon.padded_ground(spec, site)
    inflow, stats = runon.site_inflow(ext, float(spec["site"]["cell_m"]))
    return {
        "site": site,
        "cn": landcover.cn_raster(green, spec),
        "green": green,
        "n_green_polygons": len(polys),
        "inflow": inflow,
        "runon_stats": stats,
        "padded_shape": tuple(ext["ground"].shape),
        "padded_buildings": int(ext["n_buildings"]),
    }


def with_sewer(spec: dict[str, Any], sewer_mm_h: float) -> dict[str, Any]:
    """Deep copy of ``spec`` with ``rain.sewer_capacity_mm_h`` replaced."""
    out = copy.deepcopy(spec)
    out["rain"]["sewer_capacity_mm_h"] = float(sewer_mm_h)
    return out


def volume_balance(run: dict[str, Any], cell_m: float) -> dict[str, float]:
    """Closing of the volume balance of a ``run_pluvial`` result.

    Args:
        run: ``run_pluvial`` result.
        cell_m: Cell size (m).

    Returns:
        ``supply_m3`` (rain + inflow), ``demand_m3`` (stored + sewer + outflow + sink_fn) and
        ``relative_error``.
    """
    supply = float(run["rain_volume_m3"] + run["inflow_volume_m3"])
    stored = float(run["h"].sum()) * cell_m**2
    demand = (
        stored
        + float(run["sink_volume_m3"])
        + float(run["outflow_volume_m3"])
        + float(run["sink_fn_volume_m3"])
    )
    return {
        "supply_m3": supply,
        "demand_m3": demand,
        "relative_error": abs(supply - demand) / supply if supply > 0 else 0.0,
    }


def run_storm(twin: dict[str, Any], spec_run: dict[str, Any]) -> dict[str, Any]:
    """One storm on the R6 twin (walls, green curve numbers, run-on; no basements).

    Args:
        twin: Result of :func:`build_real_twin`.
        spec_run: City spec with the storm in ``rain`` (and the sewer capacity to use).

    Returns:
        Dict with ``run``, ``entry_depth`` (m, per building, threshold ``loss`` rule),
        ``street_km2`` (open cells above ``validation.flooded_depth_m``), ``max_froude``,
        ``balance`` and ``seconds``.
    """
    site = twin["site"]
    cell_m = float(spec_run["site"]["cell_m"])
    walls = site["mask"]
    t0 = time.perf_counter()
    r = pluvial.run_pluvial(
        site["ground"], walls, cell_m, spec_run, cn=twin["cn"], inflow=twin["inflow"]
    )
    seconds = time.perf_counter() - t0
    flood_h = float(spec_run["validation"]["flooded_depth_m"])
    return {
        "run": r,
        "entry_depth": site_loss.entry_depths(
            site["grid"], site["buildings"], r["h_max"], walls, spec_run
        ),
        "street_km2": float(((r["h_max"] > flood_h) & ~walls).sum()) * cell_m**2 / 1e6,
        "max_froude": float(r["max_froude"]),
        "balance": volume_balance(r, cell_m),
        "seconds": seconds,
    }


def ring_minimum(twin: dict[str, Any]) -> np.ndarray:
    """Lowest ground (m) around each wall building (the building's own cells otherwise)."""
    site = twin["site"]
    grid, b, ground, walls = site["grid"], site["buildings"], site["ground"], site["mask"]
    is_wall = b["is_wall"].to_numpy(dtype=bool)
    out = np.full(len(b), np.nan)
    for i, geom in enumerate(b.geometry):
        rows, cols = site_loss._cells(grid, geom)
        if is_wall[i]:
            rr, cc = site_loss.ring_cells(grid, rows, cols, walls)
            if rr.size:
                out[i] = float(ground[rr, cc].min())
                continue
        out[i] = float(ground[rows, cols].min())
    return out


def lowest_ground_flags(ring_min: np.ndarray, score_set: np.ndarray, n_flag: int) -> np.ndarray:
    """Naive baseline: flag the ``n_flag`` scored buildings with the lowest ground."""
    order = np.argsort(np.where(score_set, ring_min, np.inf))
    flags = np.zeros(len(ring_min), dtype=bool)
    flags[order[:n_flag]] = True
    return flags


def match_total(
    evaluate: Callable[[float], tuple[float, Any]],
    target_km2: float,
    lo_mm: float,
    hi_mm: float,
    max_runs: int,
) -> tuple[float, Any, int]:
    """Storm total whose flooded street area is closest to ``target_km2`` (log bisection).

    Algorithm:
        Evaluate both ends; if the target lies between them, bisect in log space
        $m = \\sqrt{m_{lo} m_{hi}}$ until ``max_runs`` runs; return the run closest to the
        target. ASCII: m = sqrt(lo * hi); keep the half that brackets the target.

    Args:
        evaluate: ``mm -> (street_km2, payload)``.
        target_km2: Traced flooded area (km^2).
        lo_mm: Lower end of the search (mm).
        hi_mm: Upper end of the search (mm).
        max_runs: Cap on evaluations.

    Returns:
        ``(total_mm, payload, n_runs)`` of the best run.
    """
    tried: list[tuple[float, float, Any]] = []

    def go(mm: float) -> float:
        a, payload = evaluate(mm)
        tried.append((mm, a, payload))
        return a

    a_lo, a_hi = go(lo_mm), go(hi_mm)
    lo, hi = lo_mm, hi_mm
    if a_lo < target_km2 < a_hi:
        while len(tried) < max_runs:
            mid = float(np.sqrt(lo * hi))
            if go(mid) < target_km2:
                lo = mid
            else:
                hi = mid
    best = min(tried, key=lambda r: abs(r[1] - target_km2))
    return best[0], best[2], len(tried)


def storm_for_total(twin: dict[str, Any], spec_sewer: dict[str, Any], mm: float) -> dict[str, Any]:
    """:func:`run_storm` for the design storm of total ``mm`` (calibrated sewer in the spec)."""
    return run_storm(twin, rain_eal.spec_for_rain(spec_sewer, mm))


def observed_flags(
    buildings: Any, year: str, bbox: tuple[float, float, float, float], buffer_m: float
) -> tuple[np.ndarray, float]:
    """Observed-flooded flag per building and traced area (km^2) inside the site box.

    Args:
        buildings: Buildings (EPSG:4326).
        year: Flood-trace year folder.
        bbox: Site box ``(west, south, east, north)`` in degrees.
        buffer_m: Building buffer (m).

    Returns:
        ``(flags, traced_km2)``; the trace polygons are only queried, never returned.
    """
    import geopandas as gpd
    from shapely.geometry import box as shp_box

    tr = validate.read_traces(year, bbox)
    flags = validate.trace_flags(buildings, tr, buffer_m)
    site_box = gpd.GeoSeries([shp_box(*bbox)], crs="EPSG:4326").to_crs("EPSG:5179").iloc[0]
    area = float(tr.geometry.intersection(site_box).union_all().area) / 1e6 if len(tr) else 0.0
    return flags, area


def threshold_tables(
    depth: np.ndarray,
    use_sel: np.ndarray,
    value: np.ndarray,
    priced: dict[str, dict[Any, dict[str, np.ndarray]]],
    thresholds: list[float | None],
    rps: list[float],
    shown: list[float],
) -> dict[str, Any]:
    """Per-use annual and per-storm change of the loss against no threshold (as R5).

    Args:
        depth: Entry depths ``(K, n)`` of the known-use buildings.
        use_sel: Use of each of the ``n`` buildings.
        value: Exposed value (USD), length ``n``.
        priced: ``{curve: threshold_compare.price_thresholds(...)}`` for ``pure``, ``climada``.
        thresholds: ``[None, T1, ...]``.
        rps: Return periods (years), length ``K``.
        shown: Storms listed individually (years).

    Returns:
        Dict of groups (``residential``, ``commercial``, ``industrial``, ``all known use``).
    """
    shown_idx = [rps.index(t) for t in shown]

    def pct(new: float, base: float) -> float | None:
        return None if base <= 0 else 100.0 * (new - base) / base

    groups = [
        ("residential", use_sel == "residential"),
        ("commercial", use_sel == "commercial"),
        ("industrial", use_sel == "industrial"),
        ("all known use", np.ones(len(use_sel), bool)),
    ]
    result: dict[str, Any] = {}
    for gname, mask in groups:
        g: dict[str, Any] = {"n_buildings": int(mask.sum()), "value_usd": float(value[mask].sum())}
        for c in ("pure", "climada"):
            base = priced[c][None]
            g[c] = {
                "n_with_loss": int((base["eal_usd"][mask] > 0).sum()),
                "eal_none_usd": float(base["eal_usd"][mask].sum()),
                "by_threshold": {},
                "storm_loss_none_usd": {
                    f"{rp:g}": float(base["event_loss_usd"][k][mask].sum())
                    for k, rp in zip(shown_idx, shown, strict=True)
                },
            }
            for t in thresholds[1:]:
                r = priced[c][t]
                g[c]["by_threshold"][f"{t:g}"] = {
                    "eal_usd": float(r["eal_usd"][mask].sum()),
                    "eal_pct_change": pct(
                        float(r["eal_usd"][mask].sum()), float(base["eal_usd"][mask].sum())
                    ),
                    "storm_pct_change": {
                        f"{rp:g}": pct(
                            float(r["event_loss_usd"][k][mask].sum()),
                            float(base["event_loss_usd"][k][mask].sum()),
                        )
                        for k, rp in zip(shown_idx, shown, strict=True)
                    },
                }
        result[gname] = g
    return result


def depth_distribution(
    depth: np.ndarray, rps: list[float], shown: list[float], central_mm: np.ndarray
) -> dict[str, Any]:
    """Share (%) of buildings by entry-depth class for the storms in ``shown`` (as R5)."""
    edges = [0.0, 0.05, 0.15, 0.30, 0.60]
    labels = ["0-5", "5-15", "15-30", "30-60", ">60"]
    out: dict[str, Any] = {}
    for rp in shown:
        k = rps.index(rp)
        d = depth[k]
        shares = {"dry (0)": float((d <= 0).mean() * 100)}
        for j, lab in enumerate(labels):
            hi = edges[j + 1] if j + 1 < len(edges) else np.inf
            shares[lab] = float(((d > edges[j]) & (d <= hi)).mean() * 100)
        out[f"{rp:g}"] = {"rain_mm": float(central_mm[k]), "share_pct_by_depth_cm": shares}
    return out


__all__ = [
    "build_real_twin",
    "depth_distribution",
    "lowest_ground_flags",
    "match_total",
    "observed_flags",
    "ring_minimum",
    "run_storm",
    "storm_for_total",
    "threshold_tables",
    "volume_balance",
    "with_sewer",
]
