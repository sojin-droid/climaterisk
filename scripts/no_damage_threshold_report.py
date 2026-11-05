#!/usr/bin/env python
"""How much does a no-damage depth threshold change the existing flood risk numbers?

Runs the existing physical-risk engine (``runner.compute_physical_risk_models``, the same
path as ``scripts/physical_risk_batch.py --models recommended``) over seeded random spots in
Korea, twice: as it is, and with the user's no-damage threshold rule
(무피해문턱_계산표 261020, FLOPROS-style) applied to every river- and coastal-flood hazard
after the existing defences:

    damage = 0 where depth <= T, else the unchanged JRC curve at the full depth.

The rule is :func:`apply_threshold`, the mechanics of :func:`protection.protect` with one
scalar depth instead of a per-cell return-period depth; the engine and the damage curves
are untouched. A second pair of runs repeats the comparison with the defences switched off,
to show the threshold on its own.

Algorithm:
    $$\\mathrm{EAL}_T = \\sum_e f_e\\, V\\, \\mathrm{MDR}(d_e)\\, \\mathbf{1}[d_e > T]$$
    $$\\Delta = (\\mathrm{EAL}_T - \\mathrm{EAL}) / \\mathrm{EAL}$$
    ASCII: EAL_T = sum_e freq_e * V * MDR(d_e) * [d_e > T];  delta = (EAL_T - EAL) / EAL
    with f_e the event frequency (1/yr), V the asset value (USD), d_e the event depth at
    the spot's nearest centroid (m), MDR the JRC mean damage ratio (-), T the threshold (m).

Run in the CLIMADA environment::

    ../climaterisk/.climada-env/bin/python scripts/no_damage_threshold_report.py \\
        --n 100 --seed 20261105 --out <DATA_ROOT>/derived/no_damage_threshold
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402

from climaterisk.physical_risk.metrics import load_config  # noqa: E402

#: Hazards the threshold applies to (depth hazards in metres).
FLOOD_TAGS = ("RF", "CF")


def threshold_config() -> dict[str, Any]:
    """``no_damage_threshold`` block of the physical-risk configuration."""
    cfg = load_config().get("no_damage_threshold")
    if not cfg or cfg.get("depth_m") is None:
        raise KeyError("physical_risk_config.json has no no_damage_threshold.depth_m")
    return dict(cfg)


def random_spots(
    lat: np.ndarray, lon: np.ndarray, n: int, seed: int, bbox: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """``n`` seeded random spots on the hazard grid inside ``bbox``, jittered within a cell.

    Args:
        lat: Hazard centroid latitudes (deg), land cells of the country cut.
        lon: Hazard centroid longitudes (deg).
        n: Number of spots.
        seed: ``numpy.random.default_rng`` seed.
        bbox: ``(lon_min, lat_min, lon_max, lat_max)`` (deg), kept inside the cached
            coastal-flood tiles so no download is triggered.

    Returns:
        ``[(lat, lon), ...]``.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    inside = np.flatnonzero(
        (lon >= lon_min) & (lon <= lon_max) & (lat >= lat_min) & (lat <= lat_max)
    )
    rng = np.random.default_rng(seed)
    pick = rng.choice(inside, size=min(n, inside.size), replace=False)
    half = 0.5 * float(np.median(np.diff(np.unique(np.round(lat, 6)))))
    jitter = rng.uniform(-0.45 * half, 0.45 * half, size=(pick.size, 2))
    return [(float(lat[i] + a), float(lon[i] + b)) for i, (a, b) in zip(pick, jitter, strict=True)]


def apply_threshold(hazard: Any, threshold_m: float) -> Any:
    """A copy of ``hazard`` with every intensity at or below ``threshold_m`` set to 0.

    Same mechanics as :func:`protection.protect` (which zeroes at or below a per-cell
    return-period depth); here the depth is one scalar, the no-damage threshold. Depths
    above it keep their full value, so the JRC curve is read unshifted.
    """
    import copy

    intensity = hazard.intensity.tocsr(copy=True)
    intensity.data = np.where(intensity.data > threshold_m, intensity.data, 0.0)
    intensity.eliminate_zeros()
    out = copy.deepcopy(hazard)
    out.intensity = intensity
    if hazard.fraction is not None and hazard.fraction.nnz:
        out.fraction = hazard.fraction.tocsr().multiply(intensity > 0).tocsr()
    else:
        out.fraction = (intensity > 0).astype(float).tocsr()
    return out


def _run(request: dict[str, Any], threshold_m: float | None, defences: bool) -> list[Any]:
    """One engine run, optionally with the threshold and/or without defences."""
    from climaterisk_worker.physical_risk import runner

    original = runner._with_defences

    def patched(hazard: Any, tag: str) -> tuple[Any, dict[str, Any] | None]:
        hz, record = original(hazard, tag) if defences else (hazard, None)
        if threshold_m is not None and tag in FLOOD_TAGS:
            hz = apply_threshold(hz, threshold_m)
        return hz, record

    runner._with_defences = patched
    try:
        out = runner.compute_physical_risk_models(json.loads(json.dumps(request)))
    finally:
        runner._with_defences = original
    return list(out["rows"])


def _as_dict(row: Any) -> dict[str, Any]:
    return row if isinstance(row, dict) else row.to_dict()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--value-usd", type=float, default=1.0e6)
    ap.add_argument("--scenarios", default="historical:2000,rcp85:2050")
    ap.add_argument("--hazards", default="RF,CF")
    ap.add_argument("--sample", choices=("grid", "coastal"), default="grid")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    import climaterisk_worker  # noqa: F401  (relocates CLIMADA before climada is imported)
    from climada.hazard import Hazard

    from climaterisk import paths
    from climaterisk.physical_risk.display_copy import recommended_models

    cfg = threshold_config()
    T = float(cfg["depth_m"])
    levels = [float(v) for v in cfg["report_sensitivity_m"]]
    bbox = tuple(float(v) for v in cfg["report_bbox"])
    grid_file = next(
        (paths.default_data_root() / "external/climada/hazard/river_flood").glob(
            "river_flood_150arcsec_hist_KOR_1980_2000/*/*.hdf5"
        )
    )
    if args.sample == "grid":
        haz = Hazard.from_hdf5(grid_file)
        spots = random_spots(haz.centroids.lat, haz.centroids.lon, args.n, args.seed, bbox)
    else:  # coastal: wet cells of the cached Aqueduct coastal layer (any return period)
        tile = next(paths.cache_dir("aqueduct").glob(cfg["report_coastal_tile"]))
        grid_file = tile
        with np.load(tile) as z:
            wet = np.nanmax(np.nan_to_num(z["depth"]), axis=0) > 0
            ii, jj = np.nonzero(wet)
            spots = random_spots(z["lat"][ii], z["lon"][jj], args.n, args.seed, bbox)
    types = cfg["report_property_types"]
    rng = np.random.default_rng(args.seed + 1)
    facilities = [
        {
            "facility_id": f"S{k:03d}",
            "facility_name": f"random spot {k:03d}",
            "latitude": la,
            "longitude": lo,
            "asset_value_usd": args.value_usd,
            "asset_value_currency": "USD",
            "property_type": str(rng.choice(types)),
        }
        for k, (la, lo) in enumerate(spots)
    ]
    hazard_keys = args.hazards.split(",")
    picks = recommended_models(hazard_keys)
    args.out.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for item in args.scenarios.split(","):
        scenario, year = item.split(":")
        request = {
            "facilities": facilities,
            "climate_scenario": scenario,
            "target_year": int(year),
            "hazards": hazard_keys,
            "hazard_models": {h: [picks[h]] for h in hazard_keys if h in picks},
            "models": sorted(set(picks.values())),
            "country": "KOR",
        }
        runs = {
            (defended, t): _run(request, t, defended)
            for defended in (True, False)
            for t in (None, *levels)
        }
        for i, base in enumerate(runs[(True, None)]):
            b = _as_dict(base)
            rec = {
                "scenario": scenario,
                "year": int(year),
                "facility_id": b["facility_id"],
                "latitude": facilities[int(b["facility_id"][1:])]["latitude"],
                "longitude": facilities[int(b["facility_id"][1:])]["longitude"],
                "property_type": b["property_type"],
                "hazard": b["hazard_type"],
                "model_id": b["model_id"],
                "hazard_dataset": b["hazard_dataset"],
                "status": b["calculation_status"],
                "status_detail": b["status_detail"],
            }
            for (defended, t), rows in runs.items():
                r = _as_dict(rows[i])
                assert r["facility_id"] == b["facility_id"] and r["hazard_type"] == b["hazard_type"]
                key = ("def" if defended else "nodef") + ("" if t is None else f"_T{t:.2f}")
                rec[f"eal_usd_{key}"] = r["eal_usd"]
                rec[f"risk_level_{key}"] = r["risk_level"]
            records.append(rec)
    with open(args.out / "spots.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "threshold_m": T,
                "sensitivity_m": levels,
                "basis": cfg.get("basis"),
                "n": len(facilities),
                "seed": args.seed,
                "value_usd": args.value_usd,
                "bbox": bbox,
                "models": picks,
                "grid": grid_file.name,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(records)} rows to {args.out}")


if __name__ == "__main__":
    main()
