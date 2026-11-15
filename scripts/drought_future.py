#!/usr/bin/env python
"""Agricultural drought future: SPEI-3 stage frequencies under TerraClimate +2 C / +4 C.

Method: ``docs/DROUGHT_AGRI_KO.md`` (section "미래"); parameters: ``future`` block of
``assets/libraries/drought_agri.json``. Needs the present layer from ``scripts/drought_agri.py``.
Runs in the CLIMADA worker environment (no network)::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    ./.climada-env/bin/python scripts/drought_future.py

Steps: (1) verify the scenario files share the grid and time axis of the present
TerraClimate files; (2) D = ppt - pet; SPEI-3 of the scenario D standardised with the PRESENT
1991-2020 fit; May-Sep season minimum per year; stage frequencies over the 1991-2020 months
shifted to the warmer climate; the same with the log-logistic check; (3) metric layer
``drought_spei3_future_kor.board.json`` reusing the present layer's pieces and exposure;
(4) ``drought_future.meta.json`` under ``<DATA_ROOT>/derived/drought/``. The +2 C / +4 C series
are a pseudo climate change experiment on the observed 1981-2025 months, not a dated projection.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

_URL_TEMPLATE = (
    "http://thredds.northwestknowledge.net:8080/thredds/ncss/TERRACLIMATE_ALL/data_{folder}/"
    "TerraClimate_{folder}_{var}_{YYYY}.nc?var={var}&north=38.7&west=124.5&east=130.0"
    "&south=33.0&horizStride=1&temporal=all&accept=netcdf"
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_scenario_manifest(folder: Path, scenario: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Write ``manifest.json`` for one scenario folder and return the byte/sha record.

    Bytes and sha256 are recomputed from the files and compared with
    ``<DATA_ROOT>/external/fetch_2026-11-11.log`` (written at download time).
    """
    from climaterisk import paths

    log_file = paths.external_dir() / "fetch_2026-11-11.log"
    logged: dict[str, tuple[int, str]] = {}
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            parts = line.split(" ", 3)
            if len(parts) == 4 and parts[0] == "ok":
                logged[Path(parts[3]).name] = (int(parts[1]), parts[2])
    files = []
    for f in sorted((folder / "raw").glob("TerraClimate_*.nc")):
        rec = {"path": f"raw/{f.name}", "bytes": f.stat().st_size, "sha256": _sha256(f)}
        if f.name in logged and logged[f.name] != (rec["bytes"], rec["sha256"]):
            raise ValueError(f"{f.name}: bytes/sha256 differ from the fetch log")
        files.append(rec)
    manifest = {
        "source": f"TerraClimate v1.1 {scenario} scenario (Abatzoglou et al.; University of "
        "California Merced) - monthly ppt and pet, 1/24 deg (~4 km)",
        "retrieved": "2026-11-11",
        "access": "free, no login: THREDDS NetCDF Subset Service (server-side bbox subset), "
        "one request per variable-year",
        "url_template": _URL_TEMPLATE.replace("{folder}", folder.name.split("_")[1]),
        "bbox_WSEN": [124.5, 33.0, 130.0, 38.7],
        "grid": "same as the present terraclimate files: 1/24 deg EPSG:4326, 138 lat x 133 lon",
        "period": "1981-01 .. 2025-12",
        "variables": {
            "ppt": "precipitation, mm/month",
            "pet": "reference evapotranspiration (Penman-Monteith, TerraClimate), mm/month",
        },
        "what_the_scenario_is": {
            "method_attribute": attrs["method"],
            "summary_attribute_excerpt": (
                "Complementary datasets are also produced for 1950-2025 for counterfactual "
                "climate scenarios that remove the first-order influence of climate change, as "
                "well as a +2C and +4C scenario that apply a pseudo climate change experiment "
                "to observational data. These datasets preserve the observed variability during "
                "1950-2025 but adjust individual climate variables monthly using ensemble "
                "median changes derived from CMIP6."
            ),
            "reading": "the observed 1981-2025 months with the climate shifted by CMIP6 "
            "ensemble-median monthly changes; not a dated projection year. The files do not "
            "state the reference period of the +2C / +4C offset.",
        },
        "licence": "CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) - from the "
        "'license' global attribute of the delivered NetCDF files",
        "citation": str(attrs["references"])
        + " Also acknowledge WorldClim and ERA5 (file 'acknowledgment' attribute).",
        "files": files,
        "manifest_written": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"n_files": len(files), "bytes": sum(f["bytes"] for f in files)}


def _load_scenario(folder: Path, scenario: str, cfg_future: dict[str, Any], like: Any) -> Any:
    """Monthly D = ppt - pet of one scenario ``(T, n_cells)``; grid/time checked vs ``like``."""
    import numpy as np
    import xarray as xr

    out = {}
    attrs: dict[str, Any] = {}
    for var in ("ppt", "pet"):
        names = [
            folder / "raw" / cfg_future["files"][var].format(scenario=scenario, year=y)
            for y in range(1981, 2026)
        ]
        ds = xr.open_mfdataset(names, combine="by_coords")
        arr = ds[var]
        attrs = dict(ds.attrs)
        if arr.shape != like.shape:
            raise ValueError(f"{scenario} {var}: shape {arr.shape} != present {like.shape}")
        for dim in ("lat", "lon", "time"):
            if not np.array_equal(arr[dim].values, like[dim].values):
                raise ValueError(f"{scenario} {var}: {dim} axis differs from the present files")
        out[var] = arr.values
        ds.close()
    return (out["ppt"] - out["pet"]).reshape(like.shape[0], -1), attrs


def main() -> None:
    """Build the future metric layer and its metadata."""
    import numpy as np
    import xarray as xr
    from climaterisk_worker import board
    from climaterisk_worker import drought_agri as da

    from climaterisk import paths

    cfg = da.load_config()
    fut = cfg["future"]
    fb = fut["board"]
    sp = cfg["spei"]
    check_dist = str(fut["check"]["distribution"])

    # ---- present series and layer
    base = paths.external_drought_dir("terraclimate")
    ppt = xr.open_dataset(base / cfg["terraclimate_files"]["ppt"])["ppt"]
    pet = xr.open_dataset(base / cfg["terraclimate_files"]["pet"])["pet"]
    times = ppt.time.values
    years = times.astype("datetime64[Y]").astype(int) + 1970
    months = times.astype("datetime64[M]").astype(int) % 12 + 1
    nt = ppt.shape[0]
    d_present = (ppt.values - pet.values).reshape(nt, -1)
    ref_lo, ref_hi = fut["reference_years"]
    if [ref_lo, ref_hi] != list(sp["reference_years"]):
        raise ValueError("future.reference_years must equal spei.reference_years")
    ref = (years >= ref_lo) & (years <= ref_hi)
    scale = int(sp["scale_months"])
    season = np.isin(months, sp["season_end_months"])
    year_list = np.unique(years)
    ref_years = (year_list >= ref_lo) & (year_list <= ref_hi)
    thresholds = [float(s["spei_le"]) for s in cfg["stages"]]
    stage_names = {s["stage"]: s["name"] for s in cfg["stages"]}

    present_file = paths.derived_board_dir() / f"{cfg['board']['layer']}.board.json"
    present = json.loads(present_file.read_text(encoding="utf-8"))
    pieces = present["pieces"]
    cell = np.array([p["centroid"] for p in pieces], dtype=int)

    def season_min(x: Any) -> Any:
        out = np.full((year_list.size, x.shape[1]), np.nan)
        for i, y in enumerate(year_list):
            block = x[(years == y) & season]
            with np.errstate(all="ignore"):
                out[i] = np.where(np.isfinite(block).all(axis=0), block.min(axis=0), np.nan)
        return out

    def freqs(d_target: Any, dist: str) -> tuple[dict[int, Any], Any]:
        s = da.spei_against_reference(d_present, d_target, months, scale, ref, distribution=dist,
                                      clip=float(sp["clip"]))  # fmt: skip
        st = da.stage(season_min(s), thresholds)[ref_years]
        valid = (st >= 0).sum(axis=0)
        out = {}
        for k in (1, 2, 3):
            with np.errstate(invalid="ignore", divide="ignore"):
                out[k] = np.where(valid > 0, (st >= k).sum(axis=0) / valid, np.nan)
        return out, st

    main_dist = str(sp["distribution"])
    present_f, present_st = freqs(d_present, main_dist)
    # the present layer's frequencies must be reproduced from the same code path
    for k in fb["stages_reported"]:
        got = np.round(present_f[k][cell], 4)
        have = np.array(
            [
                np.nan if p["values"][f"freq_ge{k}"] is None else p["values"][f"freq_ge{k}"]
                for p in pieces
            ]
        )
        if not np.allclose(got, have, rtol=0, atol=1e-9, equal_nan=True):
            raise ValueError(f"present freq_ge{k} is not reproduced from the layer")

    exposure_ha = np.array(
        [p["exposure"]["upland_ha"] + p["exposure"]["orchard_ha"] for p in pieces]
    )
    has_data = np.isfinite(present_f[2][cell])
    w = np.where(has_data, exposure_ha, 0.0)

    def national(f: Any) -> float:
        return float((np.nan_to_num(f[cell]) * w).sum() / w.sum())

    results: dict[str, Any] = {}
    meta_scn: dict[str, Any] = {}
    for scn in fut["scenarios"]:
        folder = paths.external_drought_dir(fut["folders"][scn])
        d_scn, attrs = _load_scenario(folder, scn, fut, ppt)
        man = write_scenario_manifest(folder, scn, attrs)
        f_main, st_main = freqs(d_scn, main_dist)
        _, st_chk = freqs(d_scn, check_dist)
        _, pst_chk = freqs(d_present, check_dist)
        both = (st_main >= 0) & (st_chk >= 0)
        differ = both & (st_main != st_chk)
        w_year = w[None, :] * np.ones((st_main.shape[0], 1))
        results[scn] = f_main
        meta_scn[scn] = {
            "files": man,
            "national_freq_main": {str(k): national(f_main[k]) for k in (1, 2, 3)},
            "national_freq_present": {str(k): national(present_f[k]) for k in (1, 2, 3)},
            "check_distribution": check_dist,
            "check_common_cell_year_share": float(both[:, cell].mean()),
            "check_stage_disagreement_share": float(differ[:, cell].sum() / both[:, cell].sum()),
            "check_stage_disagreement_share_area_weighted": float(
                (differ[:, cell] * w_year).sum() / (both[:, cell] * w_year).sum()
            ),
            "check_national_freq_ge2_same_common_area": {
                "main": float(
                    (((st_main[:, cell] >= 2) & both[:, cell]) * w_year).sum()
                    / (both[:, cell] * w_year).sum()
                ),
                "check": float(
                    (((st_chk[:, cell] >= 2) & both[:, cell]) * w_year).sum()
                    / (both[:, cell] * w_year).sum()
                ),
            },
            "check_ratio_ge2_main_vs_check_common_area": None,
        }
        # change check on the common (cell, year) area: ratio of >=2 shares vs present
        pb = (present_st >= 0) & (pst_chk >= 0)
        com = both & pb
        wy = w_year * com[:, cell]
        m_p = float(((present_st[:, cell] >= 2) * wy).sum() / wy.sum())
        m_f = float(((st_main[:, cell] >= 2) * wy).sum() / wy.sum())
        c_p = float(((pst_chk[:, cell] >= 2) * wy).sum() / wy.sum())
        c_f = float(((st_chk[:, cell] >= 2) * wy).sum() / wy.sum())
        meta_scn[scn]["check_ratio_ge2_main_vs_check_common_area"] = {
            "main_present": m_p, "main_future": m_f, "main_ratio": m_f / m_p,
            "check_present": c_p, "check_future": c_f, "check_ratio": c_f / c_p,
            "common_area_share": float(wy.sum() / (w_year.sum())),
        }  # fmt: skip
        m = meta_scn[scn]
        m["warning"] = (
            f"check stage disagreement {m['check_stage_disagreement_share']:.3f} exceeds "
            f"{fut['check']['warn_stage_disagreement_share']}"
            if m["check_stage_disagreement_share"] > fut["check"]["warn_stage_disagreement_share"]
            else None
        )
        m["scenario_attrs"] = {"method": attrs["method"], "license": attrs["license"],
                               "references": attrs["references"]}  # fmt: skip
        print(scn, json.dumps({k: v for k, v in m.items() if k != "scenario_attrs"}, indent=1))

    # ---- board layer: pieces, ids and exposure from the present layer
    keys: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = [{} for _ in pieces]
    status: list[dict[str, str]] = [{} for _ in pieces]
    status_counts: dict[str, dict[str, int]] = {}
    ratio_arr: dict[str, Any] = {}

    def label(tmpl: str, k: int, scn: str) -> str:
        return tmpl.format(
            stage_name=stage_names[k],
            threshold=f"{thresholds[k - 1]:g}".replace("-", "−"),
            scenario=fb["scenario_labels"][scn],
        )

    for k in fb["stages_reported"]:
        for scn in fut["scenarios"]:
            key = f"freq_ge{k}_{scn}"
            keys.append(
                {
                    "key": key,
                    "label": label(fb["freq_label"], k, scn),
                    "units": fb["freq_units"],
                    "scale": "share",
                    "quantity": "share_of_years",
                    "comparable_across_regions": False,
                }
            )
            f_cell = results[scn][k][cell]
            for i, v in enumerate(f_cell):
                values[i][key] = None if np.isnan(v) else round(float(v), 4)
    for k in fb["stages_reported"]:
        for scn in fut["scenarios"]:
            key = f"ratio_ge{k}_{scn}"
            keys.append({"key": key, "label": label(fb["ratio_label"], k, scn),
                         "units": fb["ratio_units"], "scale": "ratio", "quantity": "change",
                         "comparable_across_regions": True})  # fmt: skip
            base_cell = present_f[k][cell]
            ratio, st = board.change_values(base_cell, results[scn][k][cell])
            ratio_arr[key] = ratio
            status_counts[key] = {
                s_: int((st == s_).sum()) for s_ in ("", "both_zero", "base_zero", "null")
            }
            for i, (r, s_) in enumerate(zip(ratio, st, strict=True)):
                values[i][key] = None if np.isnan(r) else round(float(r), 3)
                if s_:
                    status[i][key] = str(s_)
    if fb["default_key"] not in {k["key"] for k in keys}:
        raise ValueError("future.board.default_key is not a key of the layer")

    pm = present["meta"]
    meta = {
        "layer": fb["layer"],
        "label": fb["label"],
        "peril": "drought",
        "key_kind": "metric",
        "default_key": fb["default_key"],
        "keys": keys,
        "units": fb["freq_units"],
        "base_layer": pm["layer"],
        "resolution_arcsec": pm["resolution_arcsec"],
        "cell_km_ns": pm["cell_km_ns"],
        "cell_km_ew": pm["cell_km_ew"],
        "hazard_source": fb["hazard_source"],
        "boundary_source": pm["boundary_source"],
        "boundary_simplify_m": pm["boundary_simplify_m"],
        "use_tier": fb["use_tier"],
        "use_tier_reason": fb["use_tier_reason"],
        "notes": fb["notes"],
        "status_counts": status_counts,
    }
    rows = []
    for i, p in enumerate(pieces):
        row = {k: p[k] for k in ("id", "centroid", "code", "name", "polygons")}
        row["values"] = values[i]
        row["exposure"] = p["exposure"]
        if status[i]:
            row["status"] = status[i]
        rows.append(row)
    assert [r["id"] for r in rows] == [p["id"] for p in pieces]
    assert all(r["exposure"] == p["exposure"] for r, p in zip(rows, pieces, strict=True))
    out_file = paths.derived_board_dir() / f"{fb['layer']}.board.json"
    out_file.write_text(
        json.dumps({"meta": meta, "pieces": rows}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    ratio_summary = {}
    for key, r in ratio_arr.items():
        ok = np.isfinite(r) & (w > 0)
        rr, ww = r[ok], w[ok]
        order = np.argsort(rr)
        cw = np.cumsum(ww[order]) / ww.sum()
        ratio_summary[key] = {
            "n_defined": int(np.isfinite(r).sum()),
            "unweighted_quantiles_p5_50_95": [float(q) for q in np.nanpercentile(r, [5, 50, 95])],
            "area_weighted_median": float(rr[order][np.searchsorted(cw, 0.5)]),
            "share_below_1": float((r[np.isfinite(r)] < 1).mean()),
        }
    meta_out = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "layer": fb["layer"],
        "board_file_bytes": out_file.stat().st_size,
        "reference_years": [ref_lo, ref_hi],
        "weights": "upland+orchard ha of the present layer, pieces with hazard data",
        "scenarios": meta_scn,
        "ratio_summary": ratio_summary,
        "status_counts": status_counts,
    }
    meta_path = paths.derived_drought_dir() / "drought_future.meta.json"
    meta_path.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(ratio_summary, indent=1))
    print(f"{out_file} ({out_file.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
