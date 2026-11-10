#!/usr/bin/env python
"""Agricultural drought v0 for Korea: SPEI-3 per TerraClimate cell x 팜맵 farmland per piece.

Method: ``docs/DROUGHT_AGRI_KO.md``; piece frame: ``docs/VISUAL_BOARD_KO.md``; parameters:
``assets/libraries/drought_agri.json``. Runs in the CLIMADA worker environment (no network)::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    ./.climada-env/bin/python scripts/drought_agri.py [--workers 4]

Steps: (1) SPEI-3 from TerraClimate ppt - pet per 1/24 deg cell, reference 1991-2020;
(2) pieces = TerraClimate cell x SGIS 시군구 (50 m outline simplification, as the board);
(3) 팜맵 2025 parcels read per 시군 from the province zips, each parcel assigned WHOLLY to the
piece containing its representative point (a parcel crossing a cell edge is not split);
(4) outputs under ``<DATA_ROOT>/derived/drought/``: ``pieces.csv`` (farmland m^2 by class,
long-run stage frequencies), ``piece_year.csv`` (min season SPEI-3 and stage per year),
``sigungu_summary.csv``, ``sigungu_year_affected.csv`` (sum over pieces, never an averaged
SPEI), ``drought_agri.meta.json``. 논 (rice) is reported as area only; SPEI is not applied.
The 팜맵 archives are untrusted: only whitelisted members are extracted to a fresh temporary
directory under the output folder (renamed, never by the archive's own path) and deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import re
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

_M_PER_DEG_LAT = 111_320.0
_SHP_EXT = (".shp", ".shx", ".dbf", ".prj", ".cpg")
_MAX_MEMBER_BYTES = 4 * 1024**3
FARMMAP_YEAR = 2025
_state: dict[str, Any] = {}


# ----------------------------------------------------------------------------- farmmap --
def _member_name(info: zipfile.ZipInfo) -> str:
    """Archive member name: UTF-8 if flagged, else the legacy cp949 the 팜맵 zips use."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.orig_filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def list_sigun_tasks(zip_path: Path) -> list[dict[str, Any]]:
    """Group a province zip's members by folder into one task per 시군 shapefile set.

    Returns:
        ``[{"zip", "folder", "survey_year", "members": {ext: ZipInfo.filename}}]``. Folders
        lacking ``.shp``, ``.shx`` or ``.dbf`` and non-shapefile members are ignored.
    """
    groups: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = _member_name(info)
            if info.is_dir() or "/" not in name:
                continue
            folder, _, base = name.rpartition("/")
            ext = Path(base).suffix.lower()
            if ext in _SHP_EXT and info.file_size <= _MAX_MEMBER_BYTES:
                groups.setdefault(folder, {})[ext] = info.filename
    tasks = []
    for folder, members in sorted(groups.items()):
        if not {".shp", ".shx", ".dbf"} <= members.keys():
            continue
        m = re.match(r"^(\d{4})_", Path(folder).name)
        tasks.append(
            {
                "zip": str(zip_path),
                "folder": Path(folder).name,
                "survey_year": int(m.group(1)) if m else None,
                "members": members,
            }
        )
    return tasks


def _init_worker(pieces: Any, tmp_root: str) -> None:
    _state["pieces"] = pieces
    _state["tmp_root"] = tmp_root


def process_task(task: dict[str, Any]) -> dict[str, Any]:
    """Read one 시군 shapefile and return its per-(piece, class) area sums and counts."""
    import pyogrio
    from climaterisk_worker import drought_agri as da

    tmp = Path(tempfile.mkdtemp(dir=_state["tmp_root"]))
    try:
        with zipfile.ZipFile(task["zip"]) as z:
            for ext, member in task["members"].items():
                with z.open(member) as src, open(tmp / f"parcels{ext}", "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        shp = tmp / "parcels.shp"
        info = pyogrio.read_info(shp)
        crs = info["crs"]
        parcels = pyogrio.read_dataframe(shp, columns=["CLSF_CD", "AREA"], encoding="cp949")
        if parcels.crs is None:
            raise ValueError(f"{task['folder']}: shapefile has no CRS")
        assigned, counts = da.assign_parcels(parcels, _state["pieces"])
        table = assigned.groupby(["piece_id", "cls"])["area_m2"].sum().reset_index()
        return {"task": task, "crs": str(crs), "counts": counts, "table": table, "error": None}
    except Exception as exc:  # one bad 시군 must not hide the others; reported in metadata
        return {"task": task, "crs": None, "counts": None, "table": None, "error": repr(exc)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write_farmmap_manifest(zips: list[Path], out: Path) -> None:
    """``external/farmmap/<year>/manifest.json``: origin, licence, files and sizes."""
    files = []
    for z in zips:
        h = hashlib.sha256()
        with open(z, "rb") as f:
            while chunk := f.read(1 << 24):
                h.update(chunk)
        files.append({"path": f"raw/{z.name}", "bytes": z.stat().st_size, "sha256": h.hexdigest()})
    manifest = {
        "source": "팜맵 (Farmmap) 2025 시도별 SHP - 농림축산식품부 / 농림식품기술기획평가원",
        "origin": "user's own copy downloaded from data.go.kr (fileData 15104481-15104491)",
        "licence": "이용허락범위 제한 없음 (data.go.kr)",
        "attribute_definition": "2025년도_팜맵_속성정의서.xlsx (inside the archives)",
        "crs": "EPSG:5179 (Korea 2000 Unified CS); members cp949-named, DBF EUC-KR",
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files),
        "manifest_written": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
    }
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------------------ hazard --
def compute_hazard(cfg: dict[str, Any]) -> dict[str, Any]:
    """SPEI-3 for every TerraClimate cell and the per-year season minimum with its stage."""
    import numpy as np
    import xarray as xr
    from climaterisk_worker import drought_agri as da

    from climaterisk import paths

    base = paths.external_drought_dir("terraclimate")
    ppt = xr.open_dataset(base / cfg["terraclimate_files"]["ppt"])["ppt"]
    pet = xr.open_dataset(base / cfg["terraclimate_files"]["pet"])["pet"]
    if not (ppt.time.values == pet.time.values).all():
        raise ValueError("ppt and pet time axes differ")
    lat = ppt.lat.values.astype(float)
    lon = ppt.lon.values.astype(float)
    nt, nlat, nlon = ppt.shape
    times = ppt.time.values
    years = times.astype("datetime64[Y]").astype(int) + 1970
    months = times.astype("datetime64[M]").astype(int) % 12 + 1
    if nt % 12 or not (months[:12] == np.arange(1, 13)).all():
        raise ValueError("TerraClimate series must be whole calendar years starting in January")
    d = (ppt.values - pet.values).reshape(nt, nlat * nlon)
    ref_lo, ref_hi = cfg["spei"]["reference_years"]
    ref = (years >= ref_lo) & (years <= ref_hi)
    scale = int(cfg["spei"]["scale_months"])
    diagnostics: dict[str, int] = {}
    s = da.spei(d, months, scale, ref, diagnostics=diagnostics)
    season = np.isin(months, cfg["spei"]["season_end_months"])
    year_list = np.unique(years)
    yr_min = np.full((year_list.size, nlat * nlon), np.nan)
    for i, y in enumerate(year_list):
        sel = (years == y) & season
        block = s[sel]
        with np.errstate(all="ignore"):
            ok = np.isfinite(block).all(axis=0)
            yr_min[i] = np.where(ok, block.min(axis=0), np.nan)
    res_deg = float(abs(lon[1] - lon[0]))
    return {
        "lon": np.tile(lon, nlat),
        "lat": np.repeat(lat, nlon),
        "res_deg": res_deg,
        "years": year_list,
        "season_min": yr_min,
        "stage": da.stage(yr_min),
        "global_attrs": {
            "terraclimate_version": str(ppt.attrs.get("version", "")),
        },
        "spei_fit_diagnostics": diagnostics,
        "n_cells": nlat * nlon,
        "n_cells_nan_all_years": int(np.isnan(yr_min).all(axis=0).sum()),
    }


# ------------------------------------------------------------------------------- main ---
def run(workers: int, limit: int | None) -> dict[str, Any]:
    """Build every output; return the metadata."""
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from climaterisk_worker import board
    from climaterisk_worker import drought_agri as da

    from climaterisk import paths

    t0 = time.time()
    cfg = da.load_config()
    out_dir = paths.derived_drought_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = out_dir / "tmp"
    tmp_root.mkdir(exist_ok=True)

    print("[1/4] hazard: SPEI from TerraClimate", flush=True)
    hz = compute_hazard(cfg)
    res_deg = hz["res_deg"]

    print("[2/4] pieces: TerraClimate cell x 시군구", flush=True)
    shp = paths.municipality_sgis_dir() / f"bnd_sigungu_00_{cfg['boundary_version']}.shp"
    regions = gpd.read_file(shp, encoding="utf-8")
    regions = board.simplify_regions(regions, float(cfg["boundary_simplify_m"]))
    cells = board.grid_cells(hz["lon"], hz["lat"], res_deg)
    pieces = board.clip_pieces(
        cells, regions, cfg["boundary_code_column"], cfg["boundary_name_column"]
    )
    pieces.insert(0, "id", np.arange(len(pieces)))
    print(f"      {len(pieces)} pieces, {pieces['code'].nunique()} 시군구", flush=True)

    print("[3/4] exposure: 팜맵 parcels per 시군", flush=True)
    raw = paths.farmmap_dir(str(FARMMAP_YEAR), "raw")
    zips = sorted(raw.glob("팜맵2025_*.zip"))
    if not zips:
        raise FileNotFoundError(f"no 팜맵 zips in {raw}")
    write_farmmap_manifest(zips, raw.parent / "manifest.json")
    tasks = [t for z in zips for t in list_sigun_tasks(z)]
    if limit:
        tasks = tasks[:limit]
    print(f"      {len(zips)} archives, {len(tasks)} 시군 files, workers={workers}", flush=True)
    results = []
    run_tmp = Path(tempfile.mkdtemp(dir=tmp_root))
    try:
        if workers > 1:
            with mp.get_context("spawn").Pool(
                workers, initializer=_init_worker, initargs=(pieces, str(run_tmp))
            ) as pool:
                for i, r in enumerate(pool.imap_unordered(process_task, tasks), 1):
                    results.append(r)
                    print(f"      {i}/{len(tasks)} {r['task']['folder']}", flush=True)
        else:
            _init_worker(pieces, str(run_tmp))
            for i, t in enumerate(tasks, 1):
                results.append(process_task(t))
                print(f"      {i}/{len(tasks)} {t['folder']}", flush=True)
    finally:
        shutil.rmtree(run_tmp, ignore_errors=True)
        if not any(tmp_root.iterdir()):
            tmp_root.rmdir()

    failed = [r for r in results if r["error"]]
    ok = [r for r in results if not r["error"]]
    class_names = ["rice", "upland", "orchard", "greenhouse"]
    area = pd.concat([r["table"] for r in ok], ignore_index=True)
    farm = area.pivot_table(
        index="piece_id", columns="cls", values="area_m2", aggfunc="sum", fill_value=0.0
    ).reindex(index=pieces["id"].to_numpy(), columns=class_names, fill_value=0.0)
    farm = farm.fillna(0.0)

    print("[4/4] outputs", flush=True)
    stage_p = hz["stage"][:, pieces["centroid"].to_numpy()]  # (years, pieces)
    spei_p = hz["season_min"][:, pieces["centroid"].to_numpy()]
    years = hz["years"]
    y_lo, y_hi = cfg["output_years"]
    out_sel = (years >= y_lo) & (years <= y_hi)
    r_lo, r_hi = cfg["spei"]["reference_years"]
    ref_sel = (years >= r_lo) & (years <= r_hi)
    n_stages = len(cfg["stages"])

    valid_ref = (stage_p[ref_sel] >= 0).sum(axis=0)
    freq = {}
    for k in range(1, n_stages + 1):
        with np.errstate(invalid="ignore", divide="ignore"):
            f = (stage_p[ref_sel] >= k).sum(axis=0) / valid_ref
        freq[k] = np.where(valid_ref > 0, f, np.nan)
    has_hazard = valid_ref > 0

    pcs = pieces.drop(columns="geometry").copy()
    for c in class_names:
        pcs[f"{c}_m2"] = farm[c].to_numpy()
    for k in freq:
        pcs[f"freq_stage_ge{k}"] = freq[k]
    pcs["has_hazard_data"] = has_hazard
    pcs.to_csv(out_dir / "pieces.csv", index=False, encoding="utf-8")
    pieces[["id", "centroid", "code", "name", "geometry"]].to_file(
        out_dir / "pieces.geojson", driver="GeoJSON"
    )
    py_rows = []
    for yi in np.flatnonzero(out_sel):
        py_rows.append(
            pd.DataFrame(
                {
                    "piece_id": pieces["id"].to_numpy(),
                    "year": int(years[yi]),
                    "min_spei3_season": spei_p[yi],
                    "stage": stage_p[yi],
                }
            )
        )
    pd.concat(py_rows, ignore_index=True).to_csv(
        out_dir / "piece_year.csv", index=False, encoding="utf-8", float_format="%.4f"
    )

    spei_classes = cfg["spei_evaluated_classes"]
    eval_m2 = farm[spei_classes].sum(axis=1).to_numpy()
    eval_stage = int(cfg["evaluated_stage_for_yearly_area"])
    codes = pieces["code"].to_numpy()
    names = pieces.groupby("code")["name"].first()
    summ = pd.DataFrame(index=names.index)
    summ.index.name = "code"
    summ.insert(0, "name", names)
    for c in class_names:
        summ[f"{c}_ha"] = farm[c].groupby(codes).sum() / 1e4
    summ["rice_note"] = cfg["unevaluated_classes"]["rice"]
    summ["greenhouse_note"] = cfg["unevaluated_classes"]["greenhouse"]
    summ["upland_orchard_no_hazard_data_ha"] = (
        pd.Series(np.where(has_hazard, 0.0, eval_m2)).groupby(codes).sum() / 1e4
    )
    for k in freq:
        weighted = np.where(has_hazard, eval_m2 * np.nan_to_num(freq[k]), 0.0)
        summ[f"expected_affected_ha_stage_ge{k}"] = pd.Series(weighted).groupby(codes).sum() / 1e4
    summ.reset_index().to_csv(out_dir / "sigungu_summary.csv", index=False, encoding="utf-8")

    yr_rows = []
    nat: dict[int, float] = {}
    for yi in np.flatnonzero(out_sel):
        hit = (stage_p[yi] >= eval_stage) & has_hazard
        ha = pd.Series(np.where(hit, eval_m2, 0.0)).groupby(codes).sum() / 1e4
        nat[int(years[yi])] = float(ha.sum())
        yr_rows.append(
            pd.DataFrame(
                {
                    "code": ha.index,
                    "year": int(years[yi]),
                    f"upland_orchard_stage_ge{eval_stage}_ha": ha.to_numpy(),
                }
            )
        )
    pd.concat(yr_rows, ignore_index=True).to_csv(
        out_dir / "sigungu_year_affected.csv", index=False, encoding="utf-8", float_format="%.3f"
    )

    # ---- counts, sanity checks, metadata
    def total(key: str) -> Any:
        return sum(r["counts"][key] for r in ok)

    area_read = {c: sum(r["counts"]["area_read_m2"][c] for r in ok) / 1e4 for c in class_names}
    excluded: dict[str, int] = {}
    for r in ok:
        for code, n in r["counts"]["excluded_by_code"].items():
            excluded[code] = excluded.get(code, 0) + n
    assigned_ha = {c: float(farm[c].sum() / 1e4) for c in class_names}
    top = sorted(nat.items(), key=lambda kv: kv[1], reverse=True)[:6]
    mean_lat = float(np.mean(hz["lat"]))
    km_ns = res_deg * _M_PER_DEG_LAT / 1000.0
    survey = {r["task"]["folder"]: r["task"]["survey_year"] for r in ok}
    meta = {
        "built_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": {
            "hazard": "TerraClimate v1.1 monthly ppt and pet (Penman-Monteith), CC0, 1981-2025, "
            "1/24 deg, external/drought/terraclimate (manifest.json there)",
            "boundary": f"SGIS 시군구 {cfg['boundary_version']}, outlines simplified "
            f"{cfg['boundary_simplify_m']} m",
            "exposure": f"팜맵 {FARMMAP_YEAR} 시도별 SHP (data.go.kr 15104481-15104491), "
            "external/farmmap/2025/manifest.json",
            "method": "docs/DROUGHT_AGRI_KO.md; Vicente-Serrano, Begueria & Lopez-Moreno 2010, "
            "J. Climate 23:1696-1718",
        },
        "config": cfg,
        "resolution": {
            "deg": res_deg,
            "label": "1/24 deg",
            "cell_km_ns": round(km_ns, 2),
            "cell_km_ew": round(km_ns * math.cos(math.radians(mean_lat)), 2),
        },
        "reference_period": [r_lo, r_hi],
        "output_years": [y_lo, y_hi],
        "n_cells": hz["n_cells"],
        "spei_fit_diagnostics": hz["spei_fit_diagnostics"],
        "n_pieces": len(pieces),
        "n_sigungu": int(pieces["code"].nunique()),
        "pieces_without_hazard_data": int((~has_hazard).sum()),
        "pieces_without_hazard_data_upland_orchard_ha": float(eval_m2[~has_hazard].sum() / 1e4),
        "farmmap": {
            "files_ok": len(ok),
            "files_failed": [{"folder": r["task"]["folder"], "error": r["error"]} for r in failed],
            "survey_year_by_sigun": survey,
            "survey_year_counts": {
                str(y): sum(1 for v in survey.values() if v == y)
                for y in sorted({v for v in survey.values() if v})
            },
            "parcels_read": int(total("read")),
            "parcels_assigned": int(total("assigned")),
            "parcels_assigned_by_class": {
                c: int(sum(r["counts"]["by_class"][c] for r in ok)) for c in class_names
            },
            "parcels_excluded_by_code": excluded,
            "parcels_farmland_outside_every_piece": int(total("outside")),
            "area_ha_farmland_classes_read": area_read,
            "area_ha_assigned": assigned_ha,
        },
        "national_upland_orchard_stage_ge2_ha_by_year": nat,
        "assumptions": [
            "Parcel assignment by representative point (point_on_surface): a parcel crossing a "
            "cell or 시군구 edge goes wholly to one piece; no area splitting.",
            "팜맵 2025 survey year differs by 시군 (2022-2025 prefix of the folder name); "
            "treated as one current exposure snapshot; per-시군 years in "
            "farmmap.survey_year_by_sigun.",
            "Piece boundaries use 시군구 outlines simplified 50 m; parcels within about 50 m of a "
            "시군구 boundary may take the neighbouring 시군구 label.",
            "SPEI-3 from TerraClimate ppt - pet, reference 1991-2020, GEV fitted by L-moments "
            "per cell and calendar month (Stagge et al. 2015; config spei.distribution); annual "
            "indicator = minimum SPEI-3 over end months May-Sep; stage -1/-1.5/-2; a year is NaN "
            "if any of those months is NaN.",
            "Frequencies are in-sample: the GEV is fitted on 1991-2020 and the stage frequencies "
            "are counted on the same years. Values beyond the fitted law's bound are clipped to "
            "+-3 (count in spei_fit_diagnostics.beyond_bound).",
            "TerraClimate 1/24 deg (about 4.6 x 3.7 km) cells are far coarser than parcels: all "
            "parcels in a piece share one SPEI value; no downscaling.",
            "논 (rice) reported as area only, not evaluated: 관개 논은 저수율 지표 필요 "
            "(docs/DROUGHT_AGRI_KO.md). 시설 (greenhouse) reported as area only.",
            "Yearly affected area = upland + orchard area in pieces at stage >= 2 (any SPEI-3 "
            "<= -1.5 in the season); it is exposure to the hazard, not damaged area or loss.",
            "Expected affected area = sum over pieces of area x share of years 1991-2020 with "
            "stage >= k; the reference years are the fit years, so frequencies are in-sample.",
            "No SPEI averaging over 시군구: 시군구 values are sums over pieces.",
            "Pieces whose cell has no TerraClimate value are listed separately "
            "(pieces_without_hazard_data), not counted as zero.",
        ],
        "runtime_seconds": round(time.time() - t0, 1),
    }
    (out_dir / "drought_agri.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n== sanity checks (not tuned) ==")
    print("farmland area assigned to pieces (ha):", {k: round(v) for k, v in assigned_ha.items()})
    print("farmland area read, all kept classes (ha):", {k: round(v) for k, v in area_read.items()})
    print(f"논+밭+과수 assigned: {sum(assigned_ha[c] for c in class_names[:3]):,.0f} ha")
    print("parcels read/assigned/outside/excluded:", total("read"), total("assigned"),
          total("outside"), excluded)  # fmt: skip
    print("top years, national upland+orchard stage>=2 (ha):",
          [(y, round(v)) for y, v in top])  # fmt: skip
    if failed:
        print("FAILED files:", [r["task"]["folder"] for r in failed])
    return meta


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workers", type=int, default=4, help="parallel 시군 readers (default 4)")
    ap.add_argument("--limit", type=int, default=None, help="process only the first N 시군 files")
    args = ap.parse_args()
    meta = run(args.workers, args.limit)
    print(f"done in {meta['runtime_seconds']} s")


if __name__ == "__main__":
    main()
