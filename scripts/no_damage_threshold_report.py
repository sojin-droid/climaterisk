#!/usr/bin/env python
"""How much does the no-damage threshold change the existing flood risk numbers?

Prices seeded random spots in Korea with the existing engine
(``runner.compute_physical_risk_models``, recommended models, configured defences on) and
the engine's own per-facility threshold input ``flood_zero_damage_threshold_m``
(``registry.thresholded_flood_impact_function``: zero damage up to ``T``, the JRC curve
unchanged above it). Two baselines:

* ``climada`` — the curve as the engine uses it. CLIMADA's ``ImpfRiverFlood`` already has
  ``MDD = 0`` at 0.05 m, so the 5 cm default is built in.
* ``pure`` — the same JRC points without that 0.05 m point (linear from 0 m to 0.5 m), i.e.
  Huizinga et al. (2017) with no threshold at all. Against it the 5 cm default shows.

Step 1 (``--sweep``) prices every spot at ``T`` = none and every level of a 0.05 m grid.
Step 2 (``--random``) draws, per spot and per draw, the survey inputs of the user's
무피해문턱 sheet from assumed distributions by property type (spec JSON, marked
"가정"), turns them into ``T`` with ``flood_threshold.zero_damage_threshold`` and reads the
spot's EAL at ``T`` floored to the grid (never more protection than drawn).

Algorithm:
    $$\\mathrm{EAL}_i(T) = \\sum_e f_e\\, V\\, \\mathrm{MDR}(d_{ie})\\, \\mathbb{1}[d_{ie} > T]$$
    $$\\Delta = \\sum_i \\mathrm{EAL}_i(T_i) / \\sum_i \\mathrm{EAL}_i(\\varnothing) - 1$$
    ASCII: EAL_i(T) = sum_e f_e * V * MDR(d_ie) * [d_ie > T];
           delta = sum_i EAL_i(T_i) / sum_i EAL_i(none) - 1
    f_e event frequency (1/yr), V asset value (USD), d_ie depth of event e at spot i (m),
    MDR JRC mean damage ratio (-), T_i the drawn threshold of spot i (m).

Run in the CLIMADA environment (``CLIMATERISK_DATA_ROOT`` and
``CLIMATERISK_CLIMADA_DATA_DIR`` set as in ``.env``)::

    ../climaterisk/.climada-env/bin/python scripts/no_damage_threshold_report.py --sweep \\
        --sample grid --hazards RF --scenarios historical:2000,rcp85:2050 --out <dir>/grid
    ../climaterisk/.climada-env/bin/python scripts/no_damage_threshold_report.py --random \\
        --out <dir>/grid
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402

from climaterisk.physical_risk.metrics import load_config  # noqa: E402

CURVES = ("climada", "pure")


def threshold_config() -> dict[str, Any]:
    """``no_damage_threshold`` block of the physical-risk configuration."""
    cfg = load_config().get("no_damage_threshold")
    if not cfg or cfg.get("depth_m") is None:
        raise KeyError("physical_risk_config.json has no no_damage_threshold.depth_m")
    return dict(cfg)


def t_grid(cfg: dict[str, Any]) -> np.ndarray:
    """Threshold levels (m) priced in the sweep: ``step`` .. ``max`` in steps of ``step``."""
    step, top = float(cfg["report_grid_step_m"]), float(cfg["report_grid_max_m"])
    return np.round(np.arange(step, top + step / 2, step), 6)


def pure_jrc(func: Any) -> Any:
    """``func`` without the zero-damage points CLIMADA adds between 0 m and the first damage.

    ``ImpfRiverFlood.from_jrc_region_sector`` inserts ``(0.05 m, MDD 0)`` ahead of the JRC
    table's first damaging depth (0.5 m). Dropping every interior zero point before that
    depth leaves the published table: linear from ``(0, 0)`` to the first damage.
    """
    x = np.asarray(func.intensity, dtype=float)
    mdr = np.asarray(func.mdd) * np.asarray(func.paa)
    first = int(np.flatnonzero(mdr > 0)[0])
    keep = np.ones(x.size, dtype=bool)
    keep[1:first] = False
    out = copy.deepcopy(func)
    out.intensity, out.mdd, out.paa = (
        x[keep],
        np.asarray(func.mdd)[keep],
        np.asarray(func.paa)[keep],
    )
    out.name = f"{func.name} | pure JRC (no 0.05 m point)"
    return out


@contextmanager
def curve(kind: str) -> Iterator[None]:
    """Price with the engine's curve (``climada``) or the pure JRC table (``pure``)."""
    from climaterisk_worker.physical_risk import registry

    if kind == "climada":
        yield
        return
    original = registry.flood_impact_function
    registry.flood_impact_function = lambda region, sector: pure_jrc(original(region, sector))
    try:
        yield
    finally:
        registry.flood_impact_function = original


def random_spots(
    lat: np.ndarray, lon: np.ndarray, n: int, seed: int, bbox: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """``n`` seeded random spots among grid cells inside ``bbox``, jittered within a cell.

    Args:
        lat: Cell-centre latitudes (deg).
        lon: Cell-centre longitudes (deg).
        n: Number of spots.
        seed: ``numpy.random.default_rng`` seed.
        bbox: ``(lon_min, lat_min, lon_max, lat_max)`` (deg), kept inside the cached
            coastal-flood tiles so no download is triggered.

    Returns:
        ``[(lat, lon), ...]``.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    in_box = (lon >= lon_min) & (lon <= lon_max) & (lat >= lat_min) & (lat <= lat_max)
    inside = np.flatnonzero(in_box)
    rng = np.random.default_rng(seed)
    pick = rng.choice(inside, size=min(n, inside.size), replace=False)
    half = 0.5 * float(np.median(np.diff(np.unique(np.round(lat, 6)))))
    jitter = rng.uniform(-0.45 * half, 0.45 * half, size=(pick.size, 2))
    return [(float(lat[i] + a), float(lon[i] + b)) for i, (a, b) in zip(pick, jitter, strict=True)]


def draw_threshold(kind: str, dist: dict[str, Any], rng: np.random.Generator) -> float:
    """One spot's ``T`` (m): survey inputs drawn from ``dist[kind]``, staged by the sheet rule.

    ``dist[kind]`` (all probabilities 0-1, heights m): ``opening`` P(basement opening),
    ``board_installed`` / ``board_planned`` P(flood board), ``board_m`` board height,
    ``wall`` P(flood wall) with ``wall_m`` [low, high], ``first_floor_m`` [low, high].
    The sill is never drawn (not measured -> the sheet's default).
    """
    from climaterisk.physical_risk import flood_threshold as ft

    d = dist[kind]
    u = rng.uniform()
    board = (
        "설치됨"
        if u < d["board_installed"]
        else ("설치 계획" if u < d["board_installed"] + d["board_planned"] else "없음")
    )
    wall = rng.uniform() < d["wall"]
    lo, hi = d["first_floor_m"]
    return float(
        ft.zero_damage_threshold(
            board_status=board,
            board_m=d["board_m"],
            wall_status="있음" if wall else "없음",
            wall_m=float(rng.uniform(*d["wall_m"])) if wall else None,
            first_floor_m=float(rng.uniform(lo, hi)),
            basement_opening="있음" if rng.uniform() < d["opening"] else "없음",
        ).t_m
    )


def _rows(request: dict[str, Any], kind: str, t: float | None) -> list[dict[str, Any]]:
    from climaterisk_worker.physical_risk import runner

    req = json.loads(json.dumps(request))
    for f in req["facilities"]:
        if t is not None:
            f["flood_zero_damage_threshold_m"] = t
    with curve(kind):
        out = runner.compute_physical_risk_models(req)
    return [r if isinstance(r, dict) else r.to_dict() for r in out["rows"]]


def sweep(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    """Price every spot at T = none and at every grid level, under both curves."""
    import climaterisk_worker  # noqa: F401  (relocates CLIMADA before climada is imported)
    from climada.hazard import Hazard

    from climaterisk import paths
    from climaterisk.physical_risk.display_copy import recommended_models

    bbox = tuple(float(v) for v in cfg["report_bbox"])
    if args.sample == "grid":
        src = next(
            (paths.default_data_root() / "external/climada/hazard/river_flood").glob(
                cfg["report_grid_hazard"]
            )
        )
        haz = Hazard.from_hdf5(src)
        spots = random_spots(haz.centroids.lat, haz.centroids.lon, args.n, args.seed, bbox)
    else:  # coastal: wet cells of the cached Aqueduct coastal layer (any return period)
        src = next(paths.cache_dir("aqueduct").glob(cfg["report_coastal_tile"]))
        with np.load(src) as z:
            ii, jj = np.nonzero(np.nanmax(np.nan_to_num(z["depth"]), axis=0) > 0)
            spots = random_spots(z["lat"][ii], z["lon"][jj], args.n, args.seed, bbox)
    rng = np.random.default_rng(args.seed + 1)
    facilities = [
        {
            "facility_id": f"S{k:03d}",
            "facility_name": f"random spot {k:03d}",
            "latitude": la,
            "longitude": lo,
            "asset_value_usd": args.value_usd,
            "asset_value_currency": "USD",
            "property_type": str(rng.choice(cfg["report_property_types"])),
        }
        for k, (la, lo) in enumerate(spots)
    ]
    hazards = args.hazards.split(",")
    picks = recommended_models(hazards)
    levels: list[float | None] = [None, *t_grid(cfg).tolist()]
    records: list[dict[str, Any]] = []
    for item in args.scenarios.split(","):
        scenario, year = item.split(":")
        request = {
            "facilities": facilities,
            "climate_scenario": scenario,
            "target_year": int(year),
            "hazards": hazards,
            "hazard_models": {h: [picks[h]] for h in hazards if h in picks},
            "models": sorted(set(picks.values())),
            "country": "KOR",
        }
        for kind in CURVES:
            for t in levels:
                for r in _rows(request, kind, t):
                    fac = facilities[int(r["facility_id"][1:])]
                    records.append(
                        {
                            "scenario": scenario,
                            "year": int(year),
                            "curve": kind,
                            "t_m": "" if t is None else t,
                            "facility_id": r["facility_id"],
                            "latitude": fac["latitude"],
                            "longitude": fac["longitude"],
                            "property_type": r["property_type"],
                            "hazard": r["hazard_type"],
                            "model_id": r["model_id"],
                            "hazard_dataset": r["hazard_dataset"],
                            "status": r["calculation_status"],
                            "eal_usd": r["eal_usd"],
                            "risk_level": r["risk_level"],
                        }
                    )
            print(f"{scenario} {kind}: {len(levels)} levels priced", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "sweep.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)
    meta = {
        "sample": args.sample,
        "n": len(facilities),
        "seed": args.seed,
        "value_usd": args.value_usd,
        "bbox": bbox,
        "models": picks,
        "spots_from": src.name,
        "grid_m": t_grid(cfg).tolist(),
    }
    (args.out / "sweep.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"wrote {len(records)} rows to {args.out}")


def random_thresholds(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    """Monte Carlo over drawn per-spot thresholds, read from the sweep."""
    grid = t_grid(cfg)
    dist = cfg["report_random"]["by_property_type"]
    with open(args.out / "sweep.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rng = np.random.default_rng(args.seed + 2)
    keys = sorted({(r["scenario"], r["hazard"], r["curve"]) for r in rows})
    ids = sorted({r["facility_id"] for r in rows})
    kinds = {r["facility_id"]: r["property_type"] for r in rows}
    # one set of draws for every hazard / curve, so the curves are compared on equal terms
    draws = np.array(
        [[draw_threshold(kinds[i], dist, rng) for i in ids] for _ in range(args.draws)]
    )
    floored = np.clip(np.floor(np.round(draws / grid[0], 6)) * grid[0], 0.0, grid[-1])
    summary: dict[str, Any] = {
        "draws": args.draws,
        "t_quantiles_m": np.quantile(draws, [0.1, 0.25, 0.5, 0.75, 0.9]).round(3).tolist(),
        "t_share_above_default": float((draws > float(cfg["depth_m"])).mean()),
        "results": [],
    }
    for scenario, hazard, kind in keys:
        sub = [
            r for r in rows if (r["scenario"], r["hazard"], r["curve"]) == (scenario, hazard, kind)
        ]
        if any(r["status"] != "FULL" for r in sub):
            continue
        eal = {(r["facility_id"], r["t_m"]): float(r["eal_usd"]) for r in sub}
        none = np.array([eal[(i, "")] for i in ids])
        table = np.array([[eal[(i, str(t))] for t in grid] for i in ids])
        # EAL of each spot at its drawn T (T below the first grid level = no threshold)
        col = np.searchsorted(grid, floored + 1e-9) - 1
        got = np.where(
            col >= 0, table[np.arange(len(ids))[None, :], np.maximum(col, 0)], none[None, :]
        )
        default = table[:, int(np.argmin(np.abs(grid - float(cfg["depth_m"]))))]
        base = float(none.sum())
        pct = (got.sum(axis=1) / base - 1.0) * 100.0 if base > 0 else np.zeros(args.draws)
        lossy = none > 0
        spot = np.where(lossy[None, :], got / np.where(lossy, none, 1.0)[None, :] - 1.0, 0.0)
        summary["results"].append(
            {
                "scenario": scenario,
                "hazard": hazard,
                "curve": kind,
                "spots_with_loss": int(lossy.sum()),
                "base_total_usd": round(base, 2),
                "default_5cm_pct": round((default.sum() / base - 1.0) * 100.0, 3) if base else 0.0,
                "random_pct_quantiles": np.quantile(pct, [0.05, 0.5, 0.95]).round(3).tolist(),
                "random_spots_to_zero_median": float(
                    np.median(((got == 0) & lossy[None, :]).sum(axis=1))
                ),
                "random_spot_change_median_pct": round(float(np.median(spot[:, lossy])) * 100, 2)
                if lossy.any()
                else 0.0,
                "sweep_pct": [
                    round((table[:, k].sum() / base - 1.0) * 100.0, 3) if base else 0.0
                    for k in range(grid.size)
                ],
            }
        )
    (args.out / "random.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary["results"], ensure_ascii=False)[:2000])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sweep", action="store_true")
    mode.add_argument("--random", action="store_true")
    ap.add_argument("--sample", choices=("grid", "coastal"), default="grid")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--value-usd", type=float, default=1.0e6)
    ap.add_argument("--scenarios", default="historical:2000")
    ap.add_argument("--hazards", default="RF,CF")
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cfg = threshold_config()
    if args.sweep:
        sweep(args, cfg)
    else:
        random_thresholds(args, cfg)


if __name__ == "__main__":
    main()
