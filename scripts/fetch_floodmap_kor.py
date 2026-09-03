#!/usr/bin/env python
"""Bulk-download 환경부 홍수위험지도 SHP archives from data.floodmap.go.kr (no login).

License: 공공누리 제4유형 (출처표시 · 상업적 이용금지 · 변경금지) — internal validation
only until terms are cleared with 한강홍수통제소. Files land under
``~/climada/data/floodmap/<dataset>/RFM_*.zip``; existing complete files are skipped.

Usage::

    python scripts/fetch_floodmap_kor.py                       # 100/200/500-yr 유역별 국가하천
    python scripts/fetch_floodmap_kor.py --datasets "유역별 기왕최대 도시침수지도"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://data.floodmap.go.kr"
LIST_KIND = {  # dataset name → list endpoint suffix (scope-river kind), from the portal bundle
    "국가하천 하천범람지도": "rv-ntn",
    "지방하천 하천범람지도": "rv-rgn",
    "도시침수지도": "rv-cty",
}
DEFAULT_DATASETS = [
    "유역별 100년 빈도 국가하천 하천범람지도",
    "유역별 200년 빈도 국가하천 하천범람지도",
    "유역별 500년 빈도 국가하천 하천범람지도",
]
OUT_ROOT = Path.home() / "climada" / "data" / "floodmap"


def _kind(dataset: str) -> str:
    for key, kind in LIST_KIND.items():
        if key in dataset:
            return kind
    raise SystemExit(f"unknown dataset family: {dataset}")


def list_files(dataset: str) -> list[dict]:  # type: ignore[type-arg]
    files: list[dict] = []  # type: ignore[type-arg]
    page = 1
    while True:
        q = urllib.parse.urlencode({"fileDataSetNm": dataset, "keyword": "", "pageNo": page})
        with urllib.request.urlopen(
            f"{BASE}/api/shp-file-list/{_kind(dataset)}?{q}", timeout=60
        ) as r:
            j = json.load(r)
        files += j.get("content", [])
        if j.get("last", True):
            return files
        page += 1


def download(dataset: str, f: dict, out_dir: Path) -> str:  # type: ignore[type-arg]
    target = out_dir / f["fileEngNm"]
    if target.is_file() and target.stat().st_size == int(f["fileSize"]):
        return "skip"
    body = urllib.parse.urlencode(
        {"fileEngNm": f["fileEngNm"], "fileKorNm": f["fileKorNm"], "dataNm": dataset}
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/api/shp/download",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": BASE + "/"},
    )
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as w:
        while chunk := r.read(1 << 20):
            w.write(chunk)
    if tmp.stat().st_size != int(f["fileSize"]):
        tmp.unlink(missing_ok=True)
        return f"size mismatch ({tmp.stat().st_size if tmp.exists() else 0} vs {f['fileSize']})"
    tmp.rename(target)
    return "ok"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--datasets", nargs="+", default=DEFAULT_DATASETS)
    ap.add_argument("--only", default=None, help="download just this fileEngNm (smoke test)")
    args = ap.parse_args(argv)
    for ds in args.datasets:
        files = list_files(ds)
        out = OUT_ROOT / ds.replace(" ", "_")
        out.mkdir(parents=True, exist_ok=True)
        total = sum(int(f["fileSize"]) for f in files) / 1e6
        print(f"== {ds}: {len(files)} files, {total:.0f} MB → {out}", flush=True)
        for f in files:
            if args.only and f["fileEngNm"] != args.only:
                continue
            t0 = time.time()
            try:
                status = download(ds, f, out)
            except Exception as exc:
                status = f"ERROR {exc}"
            print(
                f"  {status:>6} {f['fileEngNm']} {int(f['fileSize']) / 1e6:.1f} MB "
                f"{time.time() - t0:.0f}s",
                flush=True,
            )
            time.sleep(0.6)  # the portal's own UI paces requests at 600 ms
    return 0


if __name__ == "__main__":
    sys.exit(main())
