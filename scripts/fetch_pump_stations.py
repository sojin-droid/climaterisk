#!/usr/bin/env python
"""Download the 배수·빗물펌프장 file datasets published on data.go.kr, with provenance.

Searches the portal's file-data catalogue for the keywords in
``assets/libraries/pump_station_sources.json``, keeps datasets whose title names a pump
station, and saves every file of each into ``<DATA_ROOT>/external/pumpstations/raw`` as
``<dataset id>_<n>.<ext>``. ``manifest_raw.json`` beside it records, per dataset: title,
page URL, modified date, 이용허락범위 as printed on the page, and per file the download URL,
size and sha256. No key or login is used; one request per second.

    python scripts/fetch_pump_stations.py   # backend env is enough (stdlib only)

The files stay outside the repository; ``scripts/build_pump_stations.py`` turns them into
``assets/libraries/korea_pump_stations.csv``.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402

SOURCES = REPO / "assets" / "libraries" / "pump_station_sources.json"
_PORTAL = "https://www.data.go.kr"
_UA = {"User-Agent": "climaterisk/pump-station-collector"}


def _get(url: str) -> tuple[bytes, Any]:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def search(keywords: list[str], title_pattern: str) -> dict[str, str]:
    """``dataset id -> title`` for file datasets matching any keyword and the title pattern."""
    found: dict[str, str] = {}
    for kw in keywords:
        query = urllib.parse.urlencode(
            {"keyword": kw, "dType": "FILE", "perPage": 100, "currentPage": 1}
        )
        body, _ = _get(f"{_PORTAL}/tcs/dss/selectDataSetList.do?{query}")
        page = body.decode("utf-8", "ignore")
        for m in re.finditer(r'<a href="/data/(\d{8})/fileData\.do">(.*?)</a>', page, re.S):
            title = _text(m.group(2))
            if title and re.search(title_pattern, title):
                found[m.group(1)] = title
        time.sleep(1.0)
    return found


def licence_of(page: str) -> str | None:
    """The 이용허락범위 as printed on a dataset page: "제한 없음" or "공공누리 제N유형".

    The portal prints either "이용허락범위 제한 없음" or, for 공공누리 works,
    "공공저작물 : 출처표시 (제 1유형)" a few lines below the heading.
    """
    text = _text(page)
    i = text.find("이용허락범위")
    window = text[i : i + 400] if i >= 0 else ""
    if "제한 없음" in window:
        return "제한 없음"
    m = re.search(r"제\s*(\d)\s*유형", window)
    return f"공공누리 제{m.group(1)}유형" if m else None


def fetch(dataset_id: str, title: str, raw: Path) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": dataset_id,
        "title": title,
        "page": f"{_PORTAL}/data/{dataset_id}/fileData.do",
    }
    body, _ = _get(rec["page"])
    page = body.decode("utf-8", "ignore")
    rec["licence"] = licence_of(page)
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    meta: dict[str, Any] = {}
    if m:
        try:
            meta = json.loads(m.group(1), strict=False)
        except json.JSONDecodeError:
            meta = {}
    rec["modified"] = meta.get("dateModified")
    urls = re.findall(r"(/cmm/cmm/fileDownload\.do\?atchFileId=FILE_\d+&fileDetailSn=\d+)", page)
    files = []
    for n, path in enumerate(dict.fromkeys(urls), start=1):
        url = _PORTAL + path + "&insertDataPrcus=N"
        data, headers = _get(url)
        disp = urllib.parse.unquote(headers.get("Content-Disposition") or "")
        ext = (re.search(r"\.(csv|xlsx|xls|json|zip|xml)\b", disp, re.I) or [None, "bin"])[1]
        out = raw / f"{dataset_id}_{n}.{ext.lower()}"
        out.write_bytes(data)
        files.append(
            {
                "file": out.name,
                "url": url,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_disposition": disp,
            }
        )
        time.sleep(1.0)
    rec["files"] = files
    return rec


def main() -> int:
    spec = json.loads(SOURCES.read_text(encoding="utf-8"))
    raw = paths.pumpstation_dir("raw")
    raw.mkdir(parents=True, exist_ok=True)
    ids = search(spec["search_keywords"], spec["title_pattern"])
    manifest = []
    for dataset_id, title in sorted(ids.items()):
        try:
            rec = fetch(dataset_id, title, raw)
        except Exception as exc:  # one broken page never stops the collection
            rec = {"id": dataset_id, "title": title, "error": f"{type(exc).__name__}: {exc}"}
        manifest.append(rec)
        print(
            dataset_id, title, len(rec.get("files", [])), rec.get("licence"), rec.get("error", "")
        )
        time.sleep(1.0)
    out = paths.pumpstation_dir("manifest_raw.json")
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(manifest)} datasets → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
