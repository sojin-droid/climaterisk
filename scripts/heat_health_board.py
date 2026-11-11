#!/usr/bin/env python
"""Heat-health board layer ``heat_health_kor`` (visual board A4): heat deaths per 100 000 people.

Method: ``worker/climaterisk_worker/heat_health.py`` (the repository's heat-mortality hazard and
age-band impact functions applied to WorldPop 2024 age bands); parameters
``assets/libraries/heat_health.json``. Runs in the CLIMADA worker environment, offline::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    ./.climada-env/bin/python scripts/heat_health_board.py

Pieces = hazard cell (0.05 deg) x 시군구. WorldPop persons are summed per piece (the 1 km pixel
whose centre lies in the piece); the impact is computed per piece for the <65 and 65+ rows; the
rate is 1e5 x deaths / persons. Absolute deaths are not a map key (use tier S); only the rates
and the persons (``pop_total``, ``pop_65plus`` as exposure) are written.

Sub check (diagnostic only, recorded in the meta with an automatic warning): the modelled
national 2018 rate against the published Korean 2018 excess-mortality rate, and WorldPop's
national 65+ share against the KOSIS share the heat-mortality module cites.
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
log = logging.getLogger("heat_health_board")


def load_config() -> dict[str, Any]:
    """``assets/libraries/heat_health.json`` (``CLIMATERISK_LIBRARY_DIR`` overrides the folder)."""
    from climaterisk_worker import board

    path = board.board_config_path().parent / "heat_health.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def read_worldpop(cfg: dict[str, Any]) -> dict[str, Any]:
    """The age-band rasters as persons arrays on their common grid.

    Returns:
        ``{"bands": {label: (rows, cols) float array}, "nodata_mask": bool array, "lon", "lat"
        (1-D pixel centres), "res_deg"}``. WorldPop nodata is NaN in ``bands`` (not 0).
    """
    import numpy as np
    import rasterio

    from climaterisk import paths

    wp = cfg["worldpop"]
    folder = paths.worldpop_dir(wp["folder"])
    bands: dict[str, Any] = {}
    grid = None
    for label in wp["age_bands"]:
        path = folder / wp["file_pattern"].format(age=label)
        with rasterio.open(path) as ds:
            arr = ds.read(1).astype("float64")
            if ds.nodata is not None:
                arr[arr == ds.nodata] = np.nan
            here = (ds.transform, ds.shape)
            if grid is None:
                grid = here
            elif here != grid:
                raise ValueError(f"{path.name}: grid differs from the first age band")
        bands[str(int(label))] = arr
    assert grid is not None
    tr, (n_rows, n_cols) = grid
    lon = tr.c + (np.arange(n_cols) + 0.5) * tr.a
    lat = tr.f + (np.arange(n_rows) + 0.5) * tr.e
    nodata = np.all([np.isnan(a) for a in bands.values()], axis=0)
    return {"bands": bands, "nodata_mask": nodata, "lon": lon, "lat": lat, "res_deg": abs(tr.a)}


def run(out_dir: Path | None) -> dict[str, Any]:
    """Build the layer; return the metadata."""
    import climaterisk_worker  # noqa: F401  (must precede climada: project-scoped conf)
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from board_layer import metric_board_payload
    from climada.entity import Exposures
    from climada.hazard import Hazard
    from climaterisk_worker import board
    from climaterisk_worker import heat_health as hh
    from climaterisk_worker import heat_mortality as hm
    from climaterisk_worker.physical import _impact

    from climaterisk import paths

    t0 = time.time()
    cfg = load_config()
    iso3 = cfg["country_iso3"]
    hz_file = paths.hazard_db_dir() / cfg["hazard_file"]
    haz = Hazard.from_hdf5(str(hz_file))
    years = [int(n) for n in haz.event_name]
    y0, y1 = cfg["years"]
    if years != list(range(y0, y1 + 1)):
        raise ValueError(f"hazard seasons {years[0]}..{years[-1]} differ from config {y0}..{y1}")
    freq = np.asarray(haz.frequency, dtype=float)
    hz_lon = np.asarray(haz.centroids.lon, dtype=float)
    hz_lat = np.asarray(haz.centroids.lat, dtype=float)
    cell_deg = float(cfg["cell_deg"])
    log.info("hazard %s seasons %d..%d centroids %d cell %.3f deg", hz_file.name, y0, y1,
             hz_lon.size, cell_deg)  # fmt: skip

    # ---- pieces on the hazard cells
    shp = paths.municipality_sgis_dir() / f"bnd_sigungu_00_{cfg['boundary_version']}.shp"
    regions = board.simplify_regions(
        gpd.read_file(shp, encoding="utf-8"), float(cfg["boundary_simplify_m"])
    )
    pieces = board.clip_pieces(
        board.grid_cells(hz_lon, hz_lat, cell_deg),
        regions,
        cfg["boundary_code_column"],
        cfg["boundary_name_column"],
    )
    n_p = len(pieces)
    cix = pieces["centroid"].to_numpy(dtype=int)

    # ---- exposure: WorldPop persons per piece (sums)
    wp = read_worldpop(cfg)
    lon_g, lat_g = np.meshgrid(wp["lon"], wp["lat"])
    total_all = np.nansum(np.stack(list(wp["bands"].values())), axis=0)
    populated = total_all > 0
    ix = hh.assign_pixels(pieces, lon_g[populated], lat_g[populated])
    piece_bands = {
        k: hh.sum_by_piece(ix, np.nan_to_num(v[populated]), n_p) for k, v in wp["bands"].items()
    }
    pop_total = sum(piece_bands.values())
    old_from = int(cfg["worldpop"]["age_65plus_from"])
    pop_65 = sum(v for k, v in piece_bands.items() if int(k) >= old_from)
    pop_u65 = pop_total - pop_65
    persons_all = float(total_all[populated].sum())
    persons_in = float(pop_total.sum())
    share_all = float(
        hh.share_over65({k: np.array([np.nansum(v)]) for k, v in wp["bands"].items()}, old_from)[0]
    )
    share_in = float(
        hh.share_over65({k: np.array([v.sum()]) for k, v in piece_bands.items()}, old_from)[0]
    )
    log.info(
        "persons %.0f total, %.0f in pieces (%.2f %%); 65+ share %.4f / %.4f in pieces",
        persons_all,
        persons_in,
        100 * persons_in / persons_all,
        share_all,
        share_in,
    )

    # ---- impact per piece: one exposure row per (piece, band), CLIMADA impact = value*mdd*paa
    impf_set, impf_ids = hm.build_impact_functions(haz_type=haz.haz_type, country=iso3)
    col = f"impf_{haz.haz_type}"
    has_pop = pop_total > 0
    sel = np.flatnonzero(has_pop)
    lat_r = hz_lat[cix[sel]]
    lon_r = hz_lon[cix[sel]]
    df = pd.DataFrame(
        {
            "latitude": np.r_[lat_r, lat_r],
            "longitude": np.r_[lon_r, lon_r],
            "value": np.r_[pop_u65[sel], pop_65[sel]],
            col: np.r_[np.full(sel.size, impf_ids["u65"]), np.full(sel.size, impf_ids["o65"])],
        }
    )
    imp = _impact(Exposures(df, value_unit="persons"), impf_set, haz)
    mat = imp.imp_mat.toarray() if hasattr(imp.imp_mat, "toarray") else np.asarray(imp.imp_mat)
    deaths_ev = mat[:, : sel.size] + mat[:, sel.size :]  # (seasons, pieces with people)
    if deaths_ev.shape[0] != len(years):
        raise ValueError("impact matrix event count differs from the seasons")
    log.info("impact matrix %s dtype %s, nan %d", mat.shape, mat.dtype, int(np.isnan(mat).sum()))

    deaths_mean = np.full(n_p, np.nan)
    deaths_2018 = np.full(n_p, np.nan)
    deaths_mean[sel] = freq @ deaths_ev
    hi_year = int(cfg["highlight_year"])
    deaths_2018[sel] = deaths_ev[years.index(hi_year)]
    rate_mean = hh.rate_per_100k(np.nan_to_num(deaths_mean), pop_total)
    rate_hi = hh.rate_per_100k(np.nan_to_num(deaths_2018), pop_total)
    rate_mean[~has_pop] = np.nan
    rate_hi[~has_pop] = np.nan

    # ---- national figures and the sub check
    nat_pop = float(pop_total[has_pop].sum())
    nat_rate_mean = float(hh.rate_per_100k(np.nansum(deaths_mean), nat_pop))
    nat_rate_hi = float(hh.rate_per_100k(np.nansum(deaths_2018), nat_pop))
    nat_rate_by_year = {
        str(y): float(hh.rate_per_100k(deaths_ev[i].sum(), nat_pop)) for i, y in enumerate(years)
    }
    ck = cfg["check"]
    lo, hi = (float(x) for x in ck["warn_ratio"])
    ref = float(ck["reference_rate_per_100k"])
    ratio = nat_rate_hi / ref
    kosis_share = float(hm.COUNTRY_SHARE_OVER65[iso3])
    tol = float(ck["share_over65_tolerance"])
    warnings: list[str] = []
    if not (lo <= ratio <= hi):
        warnings.append(
            f"national {ck['reference_year']} modelled rate {nat_rate_hi:.2f} per 100k is "
            f"x{ratio:.2f} of the published {ref:g} (outside {lo:g}-{hi:g})"
        )
    if abs(share_all - kosis_share) > tol:
        warnings.append(
            f"WorldPop 65+ share {share_all:.3f} differs from the KOSIS share {kosis_share:.3f} "
            f"by more than {tol:g}"
        )
    check = {
        "role": "sub check only: diagnostic, never a reported result, never a calibration",
        "reference_year": ck["reference_year"],
        "modelled_national_rate_per_100k": nat_rate_hi,
        "reference_rate_per_100k": ref,
        "ratio": ratio,
        "warn_ratio": [lo, hi],
        "reference_source": ck["reference_source"],
        "comparability_note": "not the same quantity: modelled = heat-attributable deaths over the "
        "whole May-Sep exceedance season per 100 000 people; reference = excess deaths in the 2018 "
        "heatwave per 100 000 person-years (docs/HEAT_ADAPTATION_KR.md)",
        "national_rate_by_year_per_100k": nat_rate_by_year,
        "share_over65": {
            "worldpop_national": share_all,
            "worldpop_in_pieces": share_in,
            "kosis": kosis_share,
            "kosis_source": "heat_mortality.COUNTRY_SHARE_OVER65 (KOSIS 주민등록인구 2025)",
            "tolerance": tol,
            "within_tolerance": bool(abs(share_all - kosis_share) <= tol),
        },
        "warning": bool(warnings),
        "warnings": warnings,
    }
    for w in warnings:
        log.warning(w)

    # ---- board layer
    deg_ns = cell_deg * _M_PER_DEG_LAT / 1000.0
    resolution = {
        "deg": cell_deg,
        "cell_km_ns": round(deg_ns, 2),
        "cell_km_ew": round(deg_ns * math.cos(math.radians(float(hz_lat.mean()))), 2),
    }
    payload = metric_board_payload(
        pieces,
        layer=cfg["board"]["layer"],
        board_cfg=cfg["board"],
        values={"rate_per_100k": rate_mean, "rate_2018": rate_hi},
        status=None,
        exposure={"pop_total": pop_total, "pop_65plus": pop_65},
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
    quant = {
        k: {str(q): float(np.nanquantile(v, q)) for q in (0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 1)}
        for k, v in (("rate_per_100k", rate_mean), ("rate_2018", rate_hi))
    }
    bands = hm.age_bands_for(iso3)
    meta = {
        "layer": name,
        "built_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "hazard_file": str(cfg["hazard_file"]),
        "seasons": [y0, y1],
        "resolution": resolution,
        "n_pieces": n_p,
        "n_pieces_with_people": int(has_pop.sum()),
        "n_pieces_without_people_null": int((~has_pop).sum()),
        "n_regions_with_pieces": int(pieces["code"].nunique()),
        "persons": {
            "worldpop_total_populated_pixels": persons_all,
            "in_pieces": persons_in,
            "outside_pieces": persons_all - persons_in,
            "outside_share": 1 - persons_in / persons_all,
            "outside_note": "pixels whose centre is in no piece: sea or north of the boundary, "
            "or a cell missing from the 0.05 deg hazard footprint",
        },
        "national": {
            "population_pieces_with_people": nat_pop,
            "mean_annual_rate_per_100k": nat_rate_mean,
            "rate_2018_per_100k": nat_rate_hi,
            "mean_annual_deaths_modelled": float(np.nansum(deaths_mean)),
            "deaths_2018_modelled": float(np.nansum(deaths_2018)),
            "note": "absolute deaths are metadata only (use tier S) and are not a board key",
        },
        "piece_rate_quantiles": quant,
        "palette_breaks_heat_deaths_per_100k": None,
        "baseline_mortality": {
            "version": hm.baseline_mortality_version(iso3),
            "per_100k": {b.key: b.baseline_daily_mortality * 1e5 * 365.0 for b in bands},
        },
        "worldpop": cfg["worldpop"]["source"],
        "check": check,
        "board_json_bytes": board_file.stat().st_size,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    pal = json.loads((board.board_config_path().parent / "board_palettes.json").read_text("utf-8"))
    brk = pal["quantities"]["heat_deaths_per_100k"]["breaks"]
    meta["palette_breaks_heat_deaths_per_100k"] = {
        "breaks": brk,
        "share_of_pieces_per_class": {
            k: [
                float(((v > lo_b) & (v <= hi_b)).sum() / np.isfinite(v).sum())
                for lo_b, hi_b in zip([-np.inf, *brk], [*brk, np.inf], strict=True)
            ]
            for k, v in (("rate_per_100k", rate_mean), ("rate_2018", rate_hi))
        },
        "note": "classes: <=b0, (b0,b1], ... , >b_last, over pieces with a rate",
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
    show = (
        "n_pieces", "n_pieces_with_people", "persons", "national", "piece_rate_quantiles",
        "palette_breaks_heat_deaths_per_100k", "board_json_bytes", "runtime_seconds",
    )  # fmt: skip
    print(json.dumps({k: meta[k] for k in show}, ensure_ascii=False, indent=2))
    print(json.dumps(meta["check"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
