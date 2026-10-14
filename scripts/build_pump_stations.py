#!/usr/bin/env python
"""Normalise the downloaded 배수·빗물펌프장 files into one library table.

Reads ``<DATA_ROOT>/external/pumpstations/manifest_raw.json`` and the raw files beside it
(``scripts/fetch_pump_stations.py``) and writes ``assets/libraries/korea_pump_stations.csv``:
one row per facility with its name, the coordinates **the publisher gave** (none are
geocoded here), the 계획빈도 (design return period) when the file has one, the address
text, and the source dataset, licence and modified date.

Every dataset in ``pump_station_sources.json → exclude_datasets`` is skipped with its
reason, and so is any dataset whose 이용허락범위 is not in ``allowed_licences``. Columns are
found by name (시설명/펌프장명, 위도/경도, 빈도, 주소/소재지/위치); a coordinate outside the
spec's Korea bbox is dropped (a swapped pair is put right first), never guessed. A design
frequency of 0 or blank is "not stated", not zero.

Two more rules from the spec: a facility whose name matches ``exclude_name_pattern``
(underpass / manhole / sewage pumps — they drain a road or sewer, not a levee-enclosed
제내지) is left out; and a coordinate farther than ``address_check_km`` from the 시군구
(else 시도) that its own address names is dropped as a publisher error — the row stays,
unlocated. The check uses the SGIS boundaries, so the script runs in the worker env::

    ./.climada-env/bin/python scripts/build_pump_stations.py
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402

SOURCES = REPO / "assets" / "libraries" / "pump_station_sources.json"
OUTPUT = REPO / "assets" / "libraries" / "korea_pump_stations.csv"
COLUMNS: tuple[str, ...] = (
    "pump_station_id",
    "name",
    "latitude",
    "longitude",
    "design_frequency_years",
    "address",
    "source_dataset_id",
    "source_title",
    "source_licence",
    "source_modified",
)
_NAME = re.compile(r"시설명|펌프장명|배수장명|명칭|^이름$|^명$|펌프장$")
_LAT = re.compile(r"위도|^lat", re.I)
_LON = re.compile(r"경도|^lon|^lng", re.I)
_FREQ = re.compile(r"빈도")
_ADDR = re.compile(r"주소|소재지|위\s*치")


def read_table(path: Path) -> Any:
    """A raw file as a string DataFrame (CSV in UTF-8 or CP949, or Excel), or None."""
    import pandas as pd

    data = path.read_bytes()
    if path.suffix == ".csv":
        for enc in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                return pd.read_csv(io.BytesIO(data), encoding=enc, dtype=str)
            except (UnicodeDecodeError, ValueError):
                continue
        return None
    if path.suffix in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(data), dtype=str)
    return None


def _first(cols: list[str], pattern: re.Pattern[str]) -> str | None:
    return next((c for c in cols if pattern.search(c)), None)


def _float(value: Any) -> float | None:
    try:
        out = float(str(value).strip().replace(",", ""))
    except ValueError:
        return None
    return out if out == out else None  # NaN → None


def coordinates(
    lat: Any, lon: Any, bbox: dict[str, float]
) -> tuple[float, float] | tuple[None, None]:
    """``(lat, lon)`` inside the bbox, putting a swapped pair right; else ``(None, None)``."""
    a, b = _float(lat), _float(lon)
    if a is None or b is None:
        return None, None

    def inside(la: float, lo: float) -> bool:
        return bbox["lat_min"] <= la <= bbox["lat_max"] and bbox["lon_min"] <= lo <= bbox["lon_max"]

    if inside(a, b):
        return a, b
    if inside(b, a):
        return b, a
    return None, None


def design_frequency(value: Any) -> int | None:
    """A positive 계획빈도 in years, or None when blank, zero or not a number."""
    v = _float(value)
    return int(v) if v is not None and v > 0 else None


class AddressCheck:
    """Distance (km) from a point to the 시군구 or 시도 an address names, via SGIS polygons."""

    def __init__(self, sgis_dir: Path, version: str) -> None:
        import geopandas as gpd

        sido = gpd.read_file(sgis_dir / f"bnd_sido_00_{version}.shp", encoding="utf-8")
        sgg = gpd.read_file(sgis_dir / f"bnd_sigungu_00_{version}.shp", encoding="utf-8")
        self.crs = sido.crs
        names = {str(r.SIDO_CD): str(r.SIDO_NM) for r in sido.itertuples()}
        self.sido = {str(r.SIDO_NM): r.geometry for r in sido.itertuples()}
        # 시군구 keyed by (시도, 시군구) and by name alone when the name is unique
        self.sgg: dict[tuple[str, str], Any] = {}
        for r in sgg.itertuples():
            prov = names.get(str(r.SIGUNGU_CD)[:2], "")
            key = (prov, str(r.SIGUNGU_NM))
            self.sgg[key] = r.geometry if key not in self.sgg else self.sgg[key].union(r.geometry)
        counts: dict[str, int] = {}
        for _, n in self.sgg:
            counts[n] = counts.get(n, 0) + 1
        self.unique_sgg = {n for n, c in counts.items() if c == 1}

    def distance_km(self, address: str, lat: float, lon: float) -> float | None:
        """None when the address names no 시도 / 시군구 this check can recognise."""
        import geopandas as gpd
        from shapely.geometry import Point

        pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(self.crs).iloc[0]
        tokens = address.replace(",", " ").split()
        prov = next((p for p in self.sido if p in tokens or address.startswith(p)), None)

        def allowed(p: str, n: str) -> bool:
            return p == prov or (prov is None and n in self.unique_sgg)

        # a whole-word match first; a substring match only as a fallback, longest name
        # first — "강서구" contains "서구", which is a different 시군구
        exact = [(p, n) for (p, n) in self.sgg if n in tokens and allowed(p, n)]
        squeezed = address.replace(" ", "")
        loose = [(p, n) for (p, n) in self.sgg if n.replace(" ", "") in squeezed and allowed(p, n)]
        for pool in (exact, loose):
            if pool:
                key = max(pool, key=lambda k: len(k[1]))
                return float(self.sgg[key].distance(pt)) / 1000.0
        if prov is not None:
            return float(self.sido[prov].distance(pt)) / 1000.0
        return None


def build(
    spec: dict[str, Any],
    manifest: list[dict[str, Any]],
    raw: Path,
    check: AddressCheck | None = None,
) -> tuple[list, list]:
    """``(rows, skipped)`` — the library rows and why each dataset / row not used was skipped."""
    rows: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    allowed = set(spec["allowed_licences"])
    exclude_name = re.compile(spec.get("exclude_name_pattern") or r"(?!)")
    max_km = float(spec.get("address_check_km") or 0.0)
    for rec in manifest:
        did = rec["id"]
        if did in spec["exclude_datasets"]:
            skipped.append((did, spec["exclude_datasets"][did]))
            continue
        if rec.get("licence") not in allowed:
            skipped.append((did, f"licence {rec.get('licence')!r} not allowed"))
            continue
        if not rec.get("files"):
            skipped.append((did, "no downloadable file (API or link only)"))
            continue
        for f in rec["files"]:
            table = read_table(raw / f["file"])
            if table is None:
                skipped.append((did, f"{f['file']}: unreadable"))
                continue
            cols = [str(c).strip() for c in table.columns]
            table.columns = cols
            name_c, lat_c, lon_c = _first(cols, _NAME), _first(cols, _LAT), _first(cols, _LON)
            freq_c, addr_c = _first(cols, _FREQ), _first(cols, _ADDR)
            for i, r in enumerate(table.itertuples(index=False), start=1):
                rec_row = dict(zip(cols, r, strict=True))
                name = str(rec_row.get(name_c) or "").strip() if name_c else ""
                if not name or name.lower() == "nan":
                    continue
                if exclude_name.search(name):
                    skipped.append(
                        (f"{did}-{i:03d}", f"{name}: excluded by name (not levee drainage)")
                    )
                    continue
                lat, lon = (
                    coordinates(rec_row.get(lat_c), rec_row.get(lon_c), spec["korea_bbox"])
                    if lat_c and lon_c
                    else (None, None)
                )
                address = str(rec_row.get(addr_c) or "").strip() if addr_c else ""
                if address.lower() == "nan":
                    address = ""
                if lat is not None and lon is not None and check is not None and address:
                    off = check.distance_km(address, lat, lon)
                    if off is not None and off > max_km:
                        skipped.append(
                            (
                                f"{did}-{i:03d}",
                                f"{name}: coordinate {off:.1f} km from its address area — dropped",
                            )
                        )
                        lat, lon = None, None
                rows.append(
                    {
                        "pump_station_id": f"{did}-{i:03d}",
                        "name": name,
                        "latitude": None if lat is None else round(lat, 6),
                        "longitude": None if lon is None else round(lon, 6),
                        "design_frequency_years": design_frequency(rec_row.get(freq_c))
                        if freq_c
                        else None,
                        "address": address,
                        "source_dataset_id": did,
                        "source_title": rec["title"],
                        "source_licence": rec["licence"],
                        "source_modified": rec.get("modified"),
                    }
                )
    return rows, skipped


def main() -> int:
    spec = json.loads(SOURCES.read_text(encoding="utf-8"))
    manifest = json.loads(paths.pumpstation_dir("manifest_raw.json").read_text(encoding="utf-8"))
    check = AddressCheck(paths.municipality_sgis_dir(), spec.get("boundary_version", "2025_2Q"))
    rows, skipped = build(spec, manifest, paths.pumpstation_dir("raw"), check)
    with OUTPUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    located = sum(1 for r in rows if r["latitude"] is not None)
    rated = sum(1 for r in rows if r["design_frequency_years"] is not None)
    print(
        f"{len(rows)} pump stations ({located} with publisher coordinates, {rated} with 계획빈도)"
    )
    for did, why in skipped:
        print(f"  skipped {did}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
