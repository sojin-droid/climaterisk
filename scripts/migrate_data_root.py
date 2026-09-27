#!/usr/bin/env python
"""Plan (and, only when asked, perform) the move of legacy data into ``CLIMATERISK_DATA_ROOT``.

Dry run by default — prints what sits where, how big it is, and the exact command for each
item. Nothing is copied, linked, moved or deleted unless ``--apply`` is given, and even then
the legacy files are left in place (``copy`` duplicates, ``symlink`` points the canonical path
at the legacy folder). There is no ``move`` and no delete.

    python scripts/migrate_data_root.py                       # plan only
    python scripts/migrate_data_root.py --apply --method symlink --only kma
    python scripts/migrate_data_root.py --apply --method copy --only hazard_db,municipality_sgis

Items:

* ``kma``               legacy CLIMADA-dir ``kma/``              → ``<DATA_ROOT>/external/kma``
* ``municipality_sgis`` legacy ``municipality_src/`` (zip + extracted)
  → ``<DATA_ROOT>/external/municipality/sgis``
* ``hazard_db``         repo ``data/hazard_db``                 → ``<DATA_ROOT>/derived/hazard_db``
* ``floodmap``          legacy ``floodmap/`` — **licence-restricted** (공공누리 제4유형): listed,
  never migrated by this tool; the owner decides whether to keep it.

Works from any working directory (paths come from ``climaterisk.paths``).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402


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
            "local hazard catalogue (catalog.json + HDF5); serves from the repo until migrated",
        ),
        Item(
            "floodmap",
            legacy["floodmap"],
            paths.floodmap_dir(),
            "환경부 홍수위험지도 archives — 공공누리 제4유형; no code path reads them",
            migratable=False,
        ),
    ]


def size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n} B"


def plan(it: Item, method: str) -> list[str]:
    pairs = it.parts or (("", ""),)
    cmds = []
    for src_sub, dst_sub in pairs:
        src = it.legacy / src_sub if src_sub else it.legacy
        dst = it.canonical / dst_sub if dst_sub else it.canonical
        if not src.exists():
            continue
        if method == "symlink":
            cmds.append(f'ln -s "{src}" "{dst}"')
        elif src.is_dir():
            cmds.append(f'mkdir -p "{dst}" && cp -R "{src}/." "{dst}/"')
        else:
            cmds.append(f'mkdir -p "{dst.parent}" && cp "{src}" "{dst}"')
    return cmds


def apply(it: Item, method: str) -> None:
    pairs = it.parts or (("", ""),)
    for src_sub, dst_sub in pairs:
        src = it.legacy / src_sub if src_sub else it.legacy
        dst = it.canonical / dst_sub if dst_sub else it.canonical
        if not src.exists():
            continue
        if dst.exists() and (dst.is_file() or any(dst.iterdir())):
            print(f"  skip {dst} — already exists and is not empty")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if method == "symlink":
            if dst.exists():
                dst.rmdir()  # empty directory only (checked above)
            os.symlink(src, dst, target_is_directory=src.is_dir())
            print(f"  linked {dst} -> {src}")
        elif src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            print(f"  copied {src} -> {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"  copied {src} -> {dst}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true", help="perform the copy/link (default: plan only)"
    )
    ap.add_argument("--method", choices=("copy", "symlink"), default="copy")
    ap.add_argument(
        "--only", default="", help="comma-separated item keys (default: all migratable)"
    )
    args = ap.parse_args(argv)

    wanted = {k.strip() for k in args.only.split(",") if k.strip()}
    print(f"repo root : {paths.REPO_ROOT}")
    source = "env/.env" if paths.setting(paths.ENV_DATA_ROOT) else "default"
    print(f"data root : {paths.data_root()}  ({source})")
    print(
        f"method    : {args.method}{'' if args.apply else '  (plan only — pass --apply to act)'}\n"
    )
    for it in items():
        if wanted and it.key not in wanted:
            continue
        n = size(it.legacy)
        state = "absent" if not it.legacy.exists() else human(n)
        canon_state = (
            "present" if it.canonical.exists() and any(it.canonical.iterdir()) else "empty/absent"
        )
        print(f"[{it.key}] {it.note}")
        print(f"  legacy    : {it.legacy}  ({state})")
        print(f"  canonical : {it.canonical}  ({canon_state})")
        if not it.legacy.exists():
            print("  nothing to migrate\n")
            continue
        if not it.migratable:
            print("  NOT migrated by this tool (licence-restricted) — decide whether to keep it\n")
            continue
        for c in plan(it, args.method):
            print(f"  $ {c}")
        if args.apply:
            apply(it, args.method)
        print()
    if not args.apply:
        print("No files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
