#!/usr/bin/env python
"""Heat days (폭염일수) board layer ``heat_days_kor`` (visual board A5).

Method and caveats: ``worker/climaterisk_worker/heat_days.py`` (delta change as the main method,
direct model count as a diagnostic sub check), parameters ``assets/libraries/heat_days.json``.
Reads the KMA 남한상세 TAMAX files under ``<DATA_ROOT>/external/kma`` only (offline)::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    ./.climada-env/bin/python scripts/heat_days_board.py

The daily fields of each window are block-meaned to 0.05 deg once and cached under
``<DATA_ROOT>/cache/heat_days`` (the AR6 archives are streamed, about 13 GB uncompressed per
decade; the first run takes tens of minutes). Output: ``<DATA_ROOT>/derived/board/heat_days_kor``
``.board.json`` and ``.meta.json``. A cell outside a window's land mask is null, never 0.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

_M_PER_DEG_LAT = 111_320.0
log = logging.getLogger("heat_days_board")


def load_config() -> dict[str, Any]:
    """``assets/libraries/heat_days.json`` (``CLIMATERISK_LIBRARY_DIR`` overrides the folder)."""
    from climaterisk_worker import board

    path = board.board_config_path().parent / "heat_days.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def cached_block(name: str, loader: Any) -> Any:
    """A ``DailyBlock`` from ``<DATA_ROOT>/cache/heat_days/<name>.npz``, built once."""
    import numpy as np
    from climaterisk_worker.heat_days import DailyBlock

    from climaterisk import paths

    path = paths.cache_dir("heat_days", f"{name}.npz")
    if path.exists():
        with np.load(path) as z:
            return DailyBlock(z["tmax"], z["years"], z["months"], z["lat"], z["lon"])
    t0 = time.time()
    block = loader()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, tmax=block.tmax, years=block.years, months=block.months, lat=block.lat, lon=block.lon
    )
    log.info("%s: built %s in %.0f s (%s)", name, block.tmax.shape, time.time() - t0, path.name)
    return block


def run(out_dir: Path | None) -> dict[str, Any]:
    """Build the layer and its metadata; return the metadata."""
    import climaterisk_worker  # noqa: F401
    import geopandas as gpd
    import numpy as np
    from board_layer import metric_board_payload
    from climaterisk_worker import board
    from climaterisk_worker import heat_days as hd

    from climaterisk import paths

    t0 = time.time()
    cfg = load_config()
    thr = float(cfg["threshold_degC"])
    months = [int(m) for m in cfg["months"]]
    block_deg = float(cfg["block_deg"])
    miss = float(cfg["max_missing_frac"])
    oy, by, fy = cfg["obs_years"], cfg["model_base_years"], cfg["model_future_years"]
    tag = f"m{months[0]}-{months[-1]}_b{block_deg:g}"

    obs = cached_block(
        f"obs_{oy[0]}_{oy[1]}_{tag}",
        lambda: hd.read_observed((oy[0], oy[1]), months, block_deg),
    )
    obs_ok = hd.valid_cells(obs.tmax, miss)
    obs_days = hd.count_days(obs.tmax, obs.years, thr)
    obs_days[~obs_ok] = np.nan
    log.info("observed %s days, %d valid cells of %d", obs.tmax.shape, obs_ok.sum(), obs_ok.size)

    per: dict[str, dict[str, Any]] = {}
    for sc in cfg["scenarios"]:
        key = cfg["scenario_keys"][sc]
        base = cached_block(
            f"{sc}_{by[0]}_{by[1]}_{tag}",
            lambda k=key: hd.read_model(k, (by[0], by[1]), months, block_deg),
        )
        fut = cached_block(
            f"{sc}_{fy[0]}_{fy[1]}_{tag}",
            lambda k=key: hd.read_model(k, (fy[0], fy[1]), months, block_deg),
        )
        for name, blk in (("base", base), ("future", fut)):
            if not (np.array_equal(blk.lat, obs.lat) and np.array_equal(blk.lon, obs.lon)):
                raise ValueError(f"{sc} {name}: block grid differs from the observed grid")
        base_ok, fut_ok = hd.valid_cells(base.tmax, miss), hd.valid_cells(fut.tmax, miss)
        ok = obs_ok & base_ok & fut_ok
        shifted = hd.delta_change(
            obs.tmax, obs.months, base.tmax, base.months, fut.tmax, fut.months
        )
        delta_days = hd.count_days(shifted, obs.years, thr)
        del shifted
        delta_days[~ok] = np.nan
        direct_base = hd.count_days(base.tmax, base.years, thr)
        direct_base[~base_ok] = np.nan
        direct_fut = hd.count_days(fut.tmax, fut.years, thr)
        direct_fut[~fut_ok] = np.nan
        per[sc] = {
            "delta_days": delta_days,
            "direct_base": direct_base,
            "direct_fut": direct_fut,
            "ok": ok,
            "climate_bias": hd.climate_bias(
                base.tmax, base.months, obs.tmax, obs.months, months, ok
            ),
        }
        log.info("%s: delta-valid cells %d", sc, int(ok.sum()))

    # ---- pieces: 0.05 deg cell x 시군구 on the observed footprint
    lon_g, lat_g = np.meshgrid(obs.lon, obs.lat)
    flat_ok = obs_ok.ravel()
    cen_lon, cen_lat = lon_g.ravel()[flat_ok], lat_g.ravel()[flat_ok]
    shp = paths.municipality_sgis_dir() / f"bnd_sigungu_00_{cfg['boundary_version']}.shp"
    regions = board.simplify_regions(
        gpd.read_file(shp, encoding="utf-8"), float(cfg["boundary_simplify_m"])
    )
    cells = board.grid_cells(cen_lon, cen_lat, block_deg)
    pieces = board.clip_pieces(
        cells, regions, cfg["boundary_code_column"], cfg["boundary_name_column"]
    )
    cix = pieces["centroid"].to_numpy(dtype=int)

    def on_pieces(field: Any) -> Any:
        return np.asarray(field, dtype=float).ravel()[flat_ok][cix]

    values: dict[str, Any] = {"obs_2000_2019": on_pieces(obs_days)}
    status: dict[str, Any] = {}
    for sc in cfg["scenarios"]:
        values[f"{sc}_{fy[0]}_{fy[1]}_delta"] = on_pieces(per[sc]["delta_days"])
        ratio, st = board.change_values(
            values["obs_2000_2019"], values[f"{sc}_{fy[0]}_{fy[1]}_delta"]
        )
        values[f"{sc}_ratio"] = ratio
        status[f"{sc}_ratio"] = st
    expect = {k["key"] for k in cfg["board"]["keys"]}
    if set(values) != expect:
        raise ValueError(f"layer keys {sorted(values)} differ from the config {sorted(expect)}")

    # ---- sub check (diagnostic only; never a second result)
    warn = float(cfg["check"]["warn_rel_bias"])
    nat = {"obs": float(np.nanmean(obs_days))}
    bias: dict[str, Any] = {}
    ratios: dict[str, Any] = {}
    warnings: list[str] = []
    for sc in cfg["scenarios"]:
        p = per[sc]
        b = hd.bias_check(p["direct_base"], obs_days, warn)
        b["share_model_cells_zero"] = float(
            np.mean(p["direct_base"][np.isfinite(p["direct_base"])] == 0)
        )
        bias[sc] = b
        if b["warning"]:
            warnings.append(
                f"{sc}: direct model count {by[0]}-{by[1]} is {b['rel_bias']:+.0%} vs observed "
                f"{oy[0]}-{oy[1]} (|bias| > {warn:.0%}): the direct count on the 5-model ensemble "
                "mean (damped spread, level offset in degC in climate_bias) is not usable; the "
                "delta method is the main result"
            )
        direct = hd.count_ratio(p["direct_base"], p["direct_fut"])
        delta = hd.count_ratio(obs_days, p["delta_days"])
        ratios[sc] = {
            "direct_model": direct | {"windows": f"{fy[0]}-{fy[1]} / {by[0]}-{by[1]}"},
            "delta_method": delta
            | {"windows": f"delta-shifted observed / observed {oy[0]}-{oy[1]}"},
            "note": "the delta ratio's base is the observed 2000-2019 climate, the direct ratio's "
            "base "
            "is the model 2021-2030 (damped and about 20 years warmer), so they are not the same "
            "ratio; recorded for comparison only",
        }
        nat[f"{sc}_delta"] = float(np.nanmean(p["delta_days"]))
        nat[f"{sc}_direct_{by[0]}_{by[1]}"] = float(np.nanmean(p["direct_base"]))
        nat[f"{sc}_direct_{fy[0]}_{fy[1]}"] = float(np.nanmean(p["direct_fut"]))
    check = {
        "role": "sub check only: diagnostic, never a reported result, never averaged with the main",
        "main_method": "delta change: observed daily TAMAX + monthly model change "
        "(2051-2060 minus 2021-2030)",
        "check_method": "direct count on the model daily fields",
        "warn_rel_bias": warn,
        "bias_check": bias,
        "climate_bias": {sc: per[sc]["climate_bias"] for sc in cfg["scenarios"]},
        "climate_bias_note": "model 2021-2030 minus observed 2000-2019 on the cells valid in "
        "both: monthly mean offset (degC) and the ratio of the pooled daily spread; the spread "
        "ratio below 1 is the ensemble-mean damping, a positive offset is a warm level bias",
        "direct_ratio_vs_delta_ratio": ratios,
        "warning": bool(any(b["warning"] for b in bias.values())),
        "warnings": warnings,
    }
    for w in warnings:
        log.warning(w)

    deg_ns = block_deg * _M_PER_DEG_LAT / 1000.0
    resolution = {
        "deg": block_deg,
        "cell_km_ns": round(deg_ns, 2),
        "cell_km_ew": round(deg_ns * math.cos(math.radians(float(cen_lat.mean()))), 2),
    }
    payload = metric_board_payload(
        pieces,
        layer=cfg["board"]["layer"],
        board_cfg=cfg["board"],
        values=values,
        status=status,
        exposure=None,
        resolution=resolution,
        boundary_source=f"SGIS 시군구 {cfg['boundary_version']}",
        boundary_simplify_m=float(cfg["boundary_simplify_m"]),
        check=check,
    )
    out = out_dir or paths.derived_board_dir()
    out.mkdir(parents=True, exist_ok=True)
    name = cfg["board"]["layer"]
    board_file = out / f"{name}.board.json"
    board_file.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    counts = {
        k: {
            "null": int(np.isnan(v).sum()),
            "zero": int((v == 0).sum()),
            "positive": int((v > 0).sum()),
        }
        for k, v in values.items()
    }
    status_counts = {
        k: {s: int((a == s).sum()) for s in ("", "both_zero", "base_zero", "null")}
        for k, a in status.items()
    }
    meta = {
        "layer": name,
        "built_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": {
            k: cfg[k]
            for k in (
                "threshold_degC",
                "months",
                "obs_years",
                "model_base_years",
                "model_future_years",
                "scenarios",
                "block_deg",
            )
        },
        "resolution": resolution,
        "n_cells_grid": int(obs_ok.size),
        "n_cells_observed": int(obs_ok.sum()),
        "n_cells_delta_valid": {sc: int(per[sc]["ok"].sum()) for sc in cfg["scenarios"]},
        "n_pieces": len(pieces),
        "n_regions_with_pieces": int(pieces["code"].nunique()),
        "national_cell_mean_days_per_year": nat,
        "national_mean_note": "unweighted mean over cells; orientation only, not an area or "
        "population mean",
        "value_counts_over_pieces": counts,
        "status_counts_over_pieces": status_counts,
        "check": check,
        "board_json_bytes": board_file.stat().st_size,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    (out / f"{name}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="default: <DATA_ROOT>/derived/board")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    meta = run(args.out)
    print(json.dumps({k: meta[k] for k in ("n_pieces", "national_cell_mean_days_per_year",
                                           "board_json_bytes", "runtime_seconds")},
                     ensure_ascii=False, indent=2))  # fmt: skip


if __name__ == "__main__":
    main()
