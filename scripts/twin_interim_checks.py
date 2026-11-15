"""Interim checks on the R6 twin while the full NGII terrain is missing (exploratory, not shipped).

A. Terrain error: GLO-30-derived site ground vs NGII 1:5,000 spot heights (sheet 37705091 only).
B. Pit-fill sensitivity: fill closed depressions deeper than 1 m (priority flood), rerun 2022.
C. Other years 2023-2025: observed gauge rain (largest 3 h burst, +-6 h) vs traces in the site.
"""

from __future__ import annotations

import heapq
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src", REPO / "scripts"):
    sys.path.insert(0, str(extra))

from climaterisk_worker.twin import real_rebuild as rr  # noqa: E402
from climaterisk_worker.twin import validate  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

OUT = Path.home() / "Data/climaterisk/derived/synthetic_city/interim_checks.json"
DXF = next((Path.home() / "Data/climaterisk/external/ngii_dem/topo5000/raw").glob("*37705091*.dxf"))
FILL_MIN_M = 1.0


def priority_flood(z: np.ndarray) -> np.ndarray:
    """Fill closed depressions to their spill level (Barnes et al. 2014, priority flood)."""
    ny, nx = z.shape
    filled = z.copy()
    done = np.zeros(z.shape, bool)
    pq: list[tuple[float, int, int]] = []
    for i in range(ny):
        for j in range(nx):
            if i in (0, ny - 1) or j in (0, nx - 1):
                heapq.heappush(pq, (z[i, j], i, j))
                done[i, j] = True
    while pq:
        h, i, j = heapq.heappop(pq)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                a, b = i + di, j + dj
                if (di or dj) and 0 <= a < ny and 0 <= b < nx and not done[a, b]:
                    done[a, b] = True
                    filled[a, b] = max(z[a, b], h)
                    heapq.heappush(pq, (filled[a, b], a, b))
    return filled


def spot_heights() -> pd.DataFrame:
    sql = (
        "SELECT ST_X(ST_PointN(ST_GeometryN(GEOMETRY,1),1)) x, "
        "ST_Y(ST_PointN(ST_GeometryN(GEOMETRY,1),1)) y, ST_MinZ(GEOMETRY) z "
        "FROM entities WHERE Layer='F0027217'"
    )
    csv = subprocess.run(
        ["ogr2ogr", "-f", "CSV", "/vsistdout/", "-dialect", "SQLITE", "-sql", sql, str(DXF)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    from io import StringIO

    d = pd.read_csv(StringIO(csv))
    from pyproj import Transformer

    lon, lat = Transformer.from_crs(5186, 4326, always_xy=True).transform(d.x.values, d.y.values)
    d["lon"], d["lat"] = lon, lat
    return d


def sample(grid, field: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    j = ((lon - grid.west) * grid.lon_m_per_deg / grid.cell_m).astype(int)
    i = ((lat - grid.south) * 111320.0 / grid.cell_m).astype(int)
    ok = (i >= 0) & (i < grid.ny) & (j >= 0) & (j < grid.nx)
    out = np.full(len(lon), np.nan)
    out[ok] = field[i[ok], j[ok]]
    return out


def main() -> None:
    spec = load_spec()
    v = spec["validation"]
    thr, buf, step = (
        float(spec["loss"]["entry_threshold_m"]),
        float(v["buffer_m"]),
        float(v["step_min"]),
    )
    bbox = tuple(float(x) for x in spec["site"]["bbox"])
    twin = rr.build_real_twin(spec)
    site = twin["site"]
    grid, b, walls, ground = site["grid"], site["buildings"], site["mask"], site["ground"]
    score = b["is_wall"].to_numpy(bool) & ~b["underground"].to_numpy(bool)
    res: dict = {"grid": [grid.nx, grid.ny, grid.cell_m]}

    # A. terrain error at the NGII spot heights inside the site
    sp = spot_heights()
    sp["model"] = sample(grid, ground, sp.lon.values, sp.lat.values)
    sp["wall"] = sample(grid, walls.astype(float), sp.lon.values, sp.lat.values)
    ins = sp[np.isfinite(sp.model)]
    err = ins.model - ins.z
    res["terrain"] = {
        "n_spot_heights_in_site": len(ins),
        "n_on_building_cells": int((ins.wall > 0).sum()),
        "ngii_m": ins.z.round(2).tolist(),
        "twin_m": ins.model.round(2).tolist(),
        "bias_m": float(err.mean()) if len(ins) else None,
        "sd_m": float(err.std(ddof=0)) if len(ins) else None,
        "rmse_m": float(np.sqrt((err**2).mean())) if len(ins) else None,
        "max_abs_m": float(err.abs().max()) if len(ins) else None,
        "share_of_site_covered": "sheet 37705091 only (NW corner)",
    }
    print("A terrain", json.dumps(res["terrain"]))

    # B. pit fill > 1 m
    filled = priority_flood(ground)
    depth = filled - ground
    # label pits: fill a cell only if it belongs to a depression whose max depth > 1 m
    from scipy import ndimage

    lab, n = ndimage.label(depth > 1e-6, structure=np.ones((3, 3)))
    deep = np.zeros_like(depth, bool)
    if n:
        mx = ndimage.maximum(depth, lab, index=np.arange(1, n + 1))
        deep = np.isin(lab, np.flatnonzero(mx > FILL_MIN_M) + 1)
    g2 = np.where(deep, filled, ground)
    res["fill"] = {
        "pits_total": int(n),
        "pits_deeper_than_1m": int((mx > FILL_MIN_M).sum()) if n else 0,
        "cells_raised": int(deep.sum()),
        "share_cells_raised": float(deep.mean()),
        "max_raise_m": float(depth[deep].max()) if deep.any() else 0.0,
        "mean_raise_m": float(depth[deep].mean()) if deep.any() else 0.0,
    }
    print("B fill", json.dumps(res["fill"]))

    obs = {}
    traced = {}
    for y in ("2022", "2023", "2024", "2025"):
        obs[y], traced[y] = rr.observed_flags(b, y, bbox, buf)
    res["traces"] = {
        y: {"observed_wall_buildings": int(obs[y][score].sum()), "traced_km2": traced[y]}
        for y in obs
    }
    print("traces", json.dumps(res["traces"]))

    ev = v["events"]["2022"]
    s, e = pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"])
    series22 = validate.gauge_series(
        pd.concat([validate.read_gauge_file(s.year, m) for m in sorted({s.month, e.month})]),
        ev["gauges"],
        s,
        e,
        step,
    )

    def run_case(tw, rain: np.ndarray, sewer: float, obs_flags):
        sp_ = validate.observed_rain_spec(rr.with_sewer(spec, sewer), rain, step)
        r = rr.run_storm(tw, sp_)
        f = validate.model_flags(r["entry_depth"], thr)
        c = validate.contingency(f[score], obs_flags[score])
        return {
            "sewer_mm_h": sewer,
            "csi": c["csi"],
            "pod": c["pod"],
            "far": c["far"],
            "model_flooded": int(f[score].sum()),
            "street_km2": r["street_km2"],
            "max_froude": r["max_froude"],
        }

    sewers = [float(x) for x in spec["calibration"]["sewer_mm_h"]]
    tw2 = dict(twin)
    tw2["site"] = dict(site)
    tw2["site"]["ground"] = g2
    rows = []
    for sw in sewers:
        a = run_case(twin, series22.to_numpy(), sw, obs["2022"])
        f2 = run_case(tw2, series22.to_numpy(), sw, obs["2022"])
        rows.append({"sewer_mm_h": sw, "original": a, "filled": f2})
        print(
            f"B 2022 sewer {sw:g}: CSI orig {a['csi']:.3f} filled {f2['csi']:.3f} "
            f"(street {a['street_km2']:.3f}/{f2['street_km2']:.3f})",
            flush=True,
        )
    n22 = int(obs["2022"][score].sum())
    naive_o = validate.contingency(
        rr.lowest_ground_flags(rr.ring_minimum(twin), score, n22)[score], obs["2022"][score]
    )
    naive_f = validate.contingency(
        rr.lowest_ground_flags(rr.ring_minimum(tw2), score, n22)[score], obs["2022"][score]
    )
    res["fill_2022"] = {
        "runs": rows,
        "naive_original_csi": naive_o["csi"],
        "naive_filled_csi": naive_f["csi"],
    }
    print(f"B naive CSI orig {naive_o['csi']:.3f} filled {naive_f['csi']:.3f}")

    # C. 2023-2025: biggest 3 h burst of the summer at the same gauges, +-6 h window
    gauges = ev["gauges"]
    res["other_years"] = {}
    for y in (2023, 2024, 2025):
        df = pd.concat([validate.read_gauge_file(y, m) for m in (6, 7, 8, 9)])
        full = validate.gauge_series(
            df, gauges, pd.Timestamp(f"{y}-06-01"), pd.Timestamp(f"{y}-10-01"), step
        )
        roll = full.rolling(int(180 / step)).sum()
        peak_end = roll.idxmax()
        w0, w1 = peak_end - pd.Timedelta(hours=9), peak_end + pd.Timedelta(hours=6)
        rain = full[(full.index >= w0) & (full.index < w1)]
        rec = {
            "window": [str(w0), str(w1)],
            "total_mm": float(rain.sum()),
            "max_3h_mm": float(roll.max()),
            "max_10min_mm": float(rain.max()),
            "observed_wall_buildings": int(obs[str(y)][score].sum()),
            "note": "trace map covers the whole year; it need not come from this burst",
        }
        rec["runs"] = {}
        for sw, tag in ((10.0, "calibrated_10"), (95.0, "design_95")):
            for name, tw in (("original", twin), ("filled", tw2)):
                rec["runs"][f"{tag}_{name}"] = run_case(tw, rain.to_numpy(), sw, obs[str(y)])
        res["other_years"][str(y)] = rec
        print(
            "C",
            y,
            json.dumps(
                {k: rec[k] for k in ("window", "total_mm", "max_3h_mm", "observed_wall_buildings")}
            ),
            {
                k: (r["model_flooded"], round(r["csi"], 3) if r["csi"] == r["csi"] else None)
                for k, r in rec["runs"].items()
            },
            flush=True,
        )

    OUT.write_text(json.dumps(res, indent=1, default=float))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
