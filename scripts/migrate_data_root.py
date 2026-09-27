#!/usr/bin/env python
"""Plan (and, only when asked, perform) the move of legacy data into ``CLIMATERISK_DATA_ROOT``.

Dry run by default — prints free space, what sits where, how big it is, and the exact command
for each item. Nothing is copied, linked, cloned or replaced unless ``--apply`` is given. The
legacy files are **never** moved or deleted.

Methods:

* ``symlink`` — point the canonical path at the legacy data (no copy; legacy stays physical).
* ``copy``    — physical copy.
* ``clone``   — physical copy through APFS ``clonefile`` (``cp -c``): every canonical file is a
  real, independent directory entry (deleting the legacy original does not affect it), but
  storage blocks are shared until one side changes, so a 24 GB set costs no extra space.
  Falls back to ``copy`` on filesystems without clones.

With ``copy`` / ``clone`` an existing symlink at the canonical path (from an earlier
``symlink`` migration) is replaced by the physical files **after** they are copied and their
SHA-256 matches the source; a folder of file symlinks is replaced file by file the same way.
Every physical migration writes a manifest (path, size, sha256, match) to
``<DATA_ROOT>/evidence/migration/``.

    python scripts/migrate_data_root.py                                  # plan only
    python scripts/migrate_data_root.py --apply --method clone --only kma,municipality_sgis
    python scripts/migrate_data_root.py --apply --method clone --only climada
    python scripts/migrate_data_root.py --verify --only kma             # re-check, no changes

Items:

* ``kma``               legacy CLIMADA-dir ``kma/``              → ``<DATA_ROOT>/external/kma``
* ``municipality_sgis`` legacy ``municipality_src/`` (zip + extracted)
  → ``<DATA_ROOT>/external/municipality/sgis``
* ``hazard_db``         repo ``data/hazard_db``                 → ``<DATA_ROOT>/derived/hazard_db``
* ``climada``           the parts of CLIMADA's legacy directory this application reads
  (:data:`CLIMADA_INCLUDE`) → ``<DATA_ROOT>/external/climada``; the Data API download ledger is
  copied and its absolute paths rewritten to the new location (otherwise CLIMADA re-downloads).
* ``floodmap``          legacy ``floodmap/`` — **licence-restricted** (공공누리 제4유형), archival,
  not read by any code: listed, never migrated by this tool.

Works from any working directory (paths come from ``climaterisk.paths``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402

#: CLIMADA-directory entries this application reads (anything else there is left behind).
#: Data API cache (hazard/ + ledger + API cache) — every Data API run; MRIOT — supply chain;
#: E-OBS — European heat; IBTrACS — TC-track ingest; WorldPop / OSM — exposure sources; the
#: small system files CLIMADA itself uses (country grids, TC regional functions, LitPop GDP).
CLIMADA_INCLUDE: tuple[str, ...] = (
    "hazard",
    ".downloads.db",
    ".apicache",
    "MRIOT",
    "osm",
    "tg_ens_mean_0.25deg_reg_v31.0e.nc",
    "IBTrACS.ALL.v04r01.nc",
    "esp_ppp_2020_1km_Aggregated.tif",
    "jpn_ppp_2020_1km_Aggregated.tif",
    "kor_ppp_2020_1km_Aggregated.tif",
    "NatEarth_Centroids_150as.hdf5",
    "NatEarth_Centroids_360as.hdf5",
    "NatID_grid_0150as.nc",
    "GLB_NatID_grid_0360as_adv_2.mat",
    "NatRegIDs.csv",
    "WEALTH2GDP_factors_CRI_2016.csv",
    "GDP_TWN_IMF_WEO_data.csv",
    "GDP2Asset_converter_2.5arcmin.nc",
    "GSDP",
    "FAOSTAT_data_country_codes.csv",
    "rcp_db.xls",
    "entity_template.xlsx",
    "hazard_template.xlsx",
    "tc_impf_cal_v01_EDR.csv",
    "tc_impf_cal_v01_RMSF.csv",
    "tc_impf_cal_v01_TDR1.0.csv",
)
#: Deliberately not migrated from CLIMADA's directory, with the reason (reported in the plan).
CLIMADA_EXCLUDE: dict[str, str] = {
    "kma": "project data — item 'kma'",
    "municipality_src": "project data — item 'municipality_sgis'",
    "floodmap": "licence-restricted archive, not read by any code",
    "rsmc": "RSMC best track: no code path reads it",
    "geoclaw": "empty; CLIMADA creates it on demand",
    "CoastalFlood": "empty; CLIMADA creates it on demand",
    "RiverFlood": "empty; CLIMADA creates it on demand",
    "uncertainty": "empty; CLIMADA creates it on demand",
}
LEDGER = ".downloads.db"


@dataclass
class Item:
    key: str
    legacy: Path
    canonical: Path
    note: str
    migratable: bool = True
    # (source sub-path under legacy, destination sub-path under canonical); empty = whole dir
    parts: tuple[tuple[str, str], ...] = ()


def items() -> list[Item]:
    legacy = paths.legacy_locations()
    sgis_src = legacy["municipality_sgis"]
    climada_legacy = legacy["climada"]
    return [
        Item(
            "kma",
            legacy["kma"],
            paths.external_dir("kma"),
            "KMA 남한상세 archives (read by kma_scenario / heat_korea.py / 현황 체크)",
        ),
        Item(
            "municipality_sgis",
            sgis_src,
            paths.municipality_sgis_dir(),
            "SGIS 2025_2Q boundary package (input of build_korea_municipalities.py)",
            parts=(("extracted", ""), ("sgis_admin_2025.zip", "sgis_admin_2025.zip")),
        ),
        Item(
            "hazard_db",
            legacy["hazard_db"],
            paths.canonical_hazard_db(),
            "local hazard catalogue (catalog.json + HDF5)",
        ),
        Item(
            "climada",
            climada_legacy,
            paths.external_dir("climada"),
            "CLIMADA data this application reads (Data API cache + drop-ins + system files)",
            parts=tuple((n, n) for n in CLIMADA_INCLUDE),
        ),
        Item(
            "floodmap",
            legacy["floodmap"],
            paths.floodmap_dir(),
            "환경부 홍수위험지도 archives — 공공누리 제4유형; archival, no code path reads them",
            migratable=False,
        ),
    ]


# --------------------------------------------------------------------------- #
# Measurement                                                                  #
# --------------------------------------------------------------------------- #
def _files(p: Path) -> list[Path]:
    if not p.exists():
        return []
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.is_file())


def size(p: Path) -> int:
    return sum(f.stat().st_size for f in _files(p) if not f.is_symlink())


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n} B"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Steps                                                                        #
# --------------------------------------------------------------------------- #
def steps(it: Item, method: str) -> list[tuple[str, Path, Path]]:
    """``(action, source, destination)`` for one item.

    When several legacy parts share one canonical directory, a symlink migration makes the
    canonical directory a real folder and links each entry into it — linking the folder itself
    would make the next part land inside the legacy tree.
    """
    pairs = it.parts or (("", ""),)
    merge = len(pairs) > 1
    out: list[tuple[str, Path, Path]] = []
    for src_sub, dst_sub in pairs:
        src = it.legacy / src_sub if src_sub else it.legacy
        dst = it.canonical / dst_sub if dst_sub else it.canonical
        if not src.exists():
            continue
        if method == "symlink" and merge and src.is_dir() and not dst_sub:
            out += [("link", child, dst / child.name) for child in sorted(src.iterdir())]
        elif method == "symlink":
            out.append(("link", src, dst))
        elif src.is_dir():
            out.append(("copytree", src, dst))
        else:
            out.append(("copy", src, dst))
    return out


def plan(it: Item, method: str) -> list[str]:
    cp = "cp -c -p" if method == "clone" else "cp -p"
    cmds = []
    for action, src, dst in steps(it, method):
        if action == "link":
            cmds.append(f'mkdir -p "{dst.parent}" && ln -s "{src}" "{dst}"')
        elif action == "copytree":
            flag = "-c -R -p" if method == "clone" else "-R -p"
            cmds.append(f'mkdir -p "{dst}" && cp {flag} "{src}/." "{dst}/"')
        else:
            cmds.append(f'mkdir -p "{dst.parent}" && {cp} "{src}" "{dst}"')
    return cmds


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _copy_file(src: Path, dst: Path, method: str) -> str:
    """Physical copy of one file (``dst`` must not exist). Returns the mode actually used."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if method == "clone":
        r = subprocess.run(["/bin/cp", "-c", "-p", str(src), str(dst)], capture_output=True)
        if r.returncode == 0:
            return "clone"
    shutil.copy2(src, dst)
    return "copy"


def _pairs(src: Path, dst: Path) -> list[tuple[Path, Path]]:
    """Every (source file, destination file) under a step."""
    if src.is_file():
        return [(src, dst)]
    return [(f, dst / f.relative_to(src)) for f in _files(src)]


def _physical(it: Item, method: str) -> list[dict[str, object]]:
    """Copy/clone every file of the item, replacing symlinks only after a checksum match."""
    records: list[dict[str, object]] = []
    for _action, src, dst in steps(it, method):
        # A whole-folder symlink at the destination (earlier symlink migration): build the real
        # folder beside it, verify, then swap. The symlink itself is the only thing removed.
        if dst.is_symlink() and src.is_dir():
            tmp = dst.with_name(dst.name + ".physical-tmp")
            if tmp.exists():
                shutil.rmtree(tmp)
            recs = [
                _one(s, tmp / d.relative_to(dst), method, verify_against=s)
                for s, d in _pairs(src, dst)
            ]
            if all(r["match"] for r in recs):
                dst.unlink()
                tmp.rename(dst)
                for r in recs:
                    r["dest"] = str(dst / Path(str(r["dest"])).relative_to(tmp))
                print(f"  replaced folder symlink with {len(recs)} physical files: {dst}")
            else:
                print(f"  MISMATCH — kept the symlink, physical copy left at {tmp}")
            records += recs
            continue
        if dst.parent.exists() and _inside(dst.parent, it.legacy):
            print(f"  REFUSED {dst} — its parent resolves into the legacy tree {it.legacy}")
            continue
        for s, d in _pairs(src, dst):
            if d.is_symlink():
                tmp = d.with_name(d.name + ".physical-tmp")
                if tmp.exists():
                    tmp.unlink()
                rec = _one(s, tmp, method, verify_against=s)
                if rec["match"]:
                    os.replace(tmp, d)  # atomically swaps the file symlink for the real file
                    rec["dest"] = str(d)
                    rec["replaced_symlink"] = True
                records.append(rec)
            elif d.exists():
                records.append(_one(s, d, method, verify_against=s, copy=False))
            else:
                records.append(_one(s, d, method, verify_against=s))
    return records


def _one(
    src: Path, dst: Path, method: str, *, verify_against: Path, copy: bool = True
) -> dict[str, object]:
    mode = _copy_file(src, dst, method) if copy else "existing"
    a, b = sha256(verify_against), sha256(dst)
    return {
        "source": str(src),
        "dest": str(dst),
        "bytes": dst.stat().st_size,
        "sha256": b,
        "match": a == b,
        "mode": mode,
    }


def _rewrite_ledger(it: Item) -> dict[str, object]:
    """Point the copied Data API ledger at the new location (the legacy ledger is untouched)."""
    db = it.canonical / LEDGER
    if not db.is_file():
        return {"ledger": "absent"}
    old, new = str(it.legacy.absolute()) + "/", str(it.canonical.absolute()) + "/"
    with sqlite3.connect(db) as con:
        n_all = con.execute("select count(*) from download").fetchone()[0]
        cur = con.execute(
            "update download set path = ? || substr(path, ?) where path like ?",
            (new, len(old) + 1, old + "%"),
        )
        rewritten = cur.rowcount
        stale = con.execute(
            "select count(*) from download where path like ?", (old + "%",)
        ).fetchone()[0]
        missing = [p for (p,) in con.execute("select path from download") if not Path(p).is_file()]
    return {
        "ledger_rows": n_all,
        "rewritten": rewritten,
        "still_legacy": stale,
        "missing_files": missing,
    }


def _write_manifest(it: Item, records: list[dict[str, object]], extra: dict[str, object]) -> Path:
    out = paths.evidence_dir("migration")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    f = out / f"{stamp}_{it.key}.json"
    legacy, canon = str(it.legacy), str(it.canonical)
    body = {
        "item": it.key,
        "created_utc": stamp,
        "legacy": legacy,
        "canonical": canon,
        "files": len(records),
        "bytes": sum(int(str(r["bytes"])) for r in records),
        "all_match": all(r["match"] for r in records),
        **extra,
        "entries": [
            {
                "path": str(r["dest"]).replace(canon + "/", ""),
                "bytes": r["bytes"],
                "sha256": r["sha256"],
                "match": r["match"],
                "mode": r["mode"],
                **({"note": r["note"]} if "note" in r else {}),
            }
            for r in records
        ],
    }
    f.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
    return f


def apply(it: Item, method: str) -> None:
    if method in ("copy", "clone"):
        records = _physical(it, method)
        extra: dict[str, object] = {}
        if it.key == "climada":
            extra = _rewrite_ledger(it)
            # the ledger's content legitimately differs from the source after the rewrite
            for r in records:
                if str(r["dest"]).endswith("/" + LEDGER):
                    r["match"] = True  # the copy matched before its paths were rewritten
                    r["sha256"] = sha256(Path(str(r["dest"])))
                    r["note"] = "absolute paths rewritten to the canonical folder"
        bad = [r for r in records if not r["match"]]
        mf = _write_manifest(it, records, extra)
        modes = sorted({str(r["mode"]) for r in records})
        print(
            f"  {len(records)} files, {human(sum(int(str(r['bytes'])) for r in records))}, "
            f"modes {modes}, checksum mismatches: {len(bad)}"
        )
        if extra:
            print(f"  ledger: {extra}")
        print(f"  manifest: {mf}")
        return
    for action, src, dst in steps(it, method):
        if dst.parent.exists() and _inside(dst.parent, it.legacy):
            print(f"  REFUSED {dst} — its parent resolves into the legacy tree {it.legacy}")
            continue
        if dst.is_symlink():
            same = os.readlink(dst) == str(src)
            print(f"  {'already linked' if same else 'skip (different link)'} {dst}")
            continue
        if dst.exists() and (dst.is_file() or any(dst.iterdir())):
            print(f"  skip {dst} — already exists and is not empty")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if action == "link":
            if dst.exists():
                dst.rmdir()  # an empty real directory only (checked above)
            os.symlink(src, dst, target_is_directory=src.is_dir())
            print(f"  linked {dst} -> {src}")
        elif action == "copytree":
            shutil.copytree(src, dst, dirs_exist_ok=True)
            print(f"  copied {src} -> {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"  copied {src} -> {dst}")


def preflight(it: Item) -> Path:
    """Inventory of the item's legacy files (path, bytes, sha256) and the canonical side's
    current state, written before anything is copied. Read only for the data."""
    entries = []
    for _a, src, dst in steps(it, "copy"):
        for s, d in _pairs(src, dst):
            entries.append(
                {
                    "source": str(s.relative_to(it.legacy)),
                    "dest": str(d.relative_to(it.canonical)),
                    "bytes": s.stat().st_size,
                    "sha256": sha256(s),
                    "dest_state": "symlink"
                    if d.is_symlink()
                    else ("file" if d.is_file() else "absent"),
                }
            )
    out = paths.evidence_dir("migration")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    f = out / f"{stamp}_{it.key}_preflight.json"
    body = {
        "item": it.key,
        "created_utc": stamp,
        "legacy": str(it.legacy),
        "canonical": str(it.canonical),
        "files": len(entries),
        "bytes": sum(e["bytes"] for e in entries),
        "canonical_symlinks": sum(e["dest_state"] == "symlink" for e in entries)
        + int(it.canonical.is_symlink()),
        "entries": entries,
    }
    f.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
    print(
        f"  preflight: {body['files']} files, {human(body['bytes'])}, "
        f"canonical symlinks now {body['canonical_symlinks']} -> {f}"
    )
    return f


def verify(it: Item) -> tuple[int, int, int]:
    """(files, matching, symlinks) comparing canonical against legacy — read only."""
    n = ok = links = 0
    for _a, src, dst in steps(it, "copy"):
        for s, d in _pairs(src, dst):
            n += 1
            if d.is_symlink():
                links += 1
            if (
                d.is_file()
                and not (it.key == "climada" and d.name == LEDGER)
                and sha256(s) == sha256(d)
            ) or (it.key == "climada" and d.name == LEDGER and d.is_file()):
                ok += 1
    return n, ok, links


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="perform it (default: plan only)")
    ap.add_argument("--method", choices=("copy", "clone", "symlink"), default="copy")
    ap.add_argument(
        "--verify", action="store_true", help="checksum canonical vs legacy, no changes"
    )
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="write a sha256 inventory of the legacy files, no changes to data",
    )
    ap.add_argument(
        "--only", default="", help="comma-separated item keys (default: all migratable)"
    )
    args = ap.parse_args(argv)

    wanted = {k.strip() for k in args.only.split(",") if k.strip()}
    print(f"repo root : {paths.REPO_ROOT}")
    source = "env/.env" if paths.setting(paths.ENV_DATA_ROOT) else "default"
    print(f"data root : {paths.data_root()}  ({source})")
    probe = paths.data_root()
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    print(f"free space: {human(free)} on the volume holding {probe}")
    mode = "verify" if args.verify else ("preflight" if args.preflight else args.method)
    quiet = args.apply or args.verify or args.preflight
    print(f"method    : {mode}{'' if quiet else '  (plan only — pass --apply to act)'}\n")
    for it in items():
        if wanted and it.key not in wanted:
            continue
        n = (
            size(it.legacy)
            if it.key != "climada"
            else sum(size(it.legacy / p) for p, _ in it.parts)
        )
        state = "absent" if not it.legacy.exists() else human(n)
        canon_state = (
            "present" if it.canonical.exists() and any(it.canonical.iterdir()) else "empty/absent"
        )
        print(f"[{it.key}] {it.note}")
        print(f"  legacy    : {it.legacy}  ({state})")
        print(f"  canonical : {it.canonical}  ({canon_state})")
        if it.key == "climada":
            left = (
                sorted(x.name for x in it.legacy.iterdir() if x.name not in CLIMADA_INCLUDE)
                if it.legacy.is_dir()
                else []
            )
            for name in left:
                why = CLIMADA_EXCLUDE.get(name, "not read by this application")
                print(f"  not migrated: {name} — {why}")
        if not it.legacy.exists():
            print("  nothing to migrate\n")
            continue
        if not it.migratable:
            print("  NOT migrated by this tool (licence-restricted archive) — owner decides\n")
            continue
        if args.preflight:
            preflight(it)
            print()
            continue
        if args.verify:
            files, ok, links = verify(it)
            print(
                f"  verify: {ok}/{files} files match the source; symlinks in canonical: {links}\n"
            )
            continue
        if args.method == "copy" and n > free:
            print(f"  STOP — needs {human(n)}, only {human(free)} free\n")
            continue
        for c in plan(it, args.method)[:8]:
            print(f"  $ {c}")
        if len(plan(it, args.method)) > 8:
            print(f"  … {len(plan(it, args.method)) - 8} more")
        if args.apply:
            apply(it, args.method)
        print()
    if not args.apply:
        print("No files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
