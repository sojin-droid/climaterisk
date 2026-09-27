#!/usr/bin/env python
"""Build ``assets/libraries/korea_official_offices.csv`` — the V0.3 official office POC.

Reads the source registry ``assets/libraries/official_office_sources.json`` and, for every
target whose status is ``AVAILABLE``, the raw government file it names under
``<DATA_ROOT>/external/municipality/source/``. For that file it:

1. checks the file's sha256 against the registry (the exact bytes that were inspected);
2. takes the row whose name column equals the registry's ``row_name`` **exactly** (no fuzzy
   match, no second guess) and copies its published latitude / longitude / address;
3. tests the point against the V0.2 SGIS 시도 polygon (``polygon_check`` INSIDE / OUTSIDE /
   UNKNOWN) — an OUTSIDE point is kept as published and flagged, never moved;
4. records the great-circle distance to the V0.2 representative point (descriptive only).

Targets without a published coordinate are written with empty coordinates and their status
(``OFFICIAL_COORDINATE_NOT_AVAILABLE`` / ``AMBIGUOUS_OFFICE``). Nothing is geocoded.

Runs in the CLIMADA worker environment (needs geopandas for the polygon test)::

    ./.climada-env/bin/python scripts/build_official_offices.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402
from climaterisk.physical_risk.municipalities import load_municipalities  # noqa: E402
from climaterisk.physical_risk.official_offices import (  # noqa: E402
    COLUMNS,
    COORDINATE_METHOD,
    STATUS_AVAILABLE,
    TARGETS,
    haversine_km,
    registry_path,
    validate,
)


def _published_row(source: dict, source_dir: Path) -> dict[str, str]:
    """The one row of the raw file whose name equals ``row_name`` (exactly)."""
    f = source_dir / source["file"]
    if not f.is_file():
        raise SystemExit(f"raw source file missing: {f}")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    if digest != source["sha256"]:
        raise SystemExit(f"{f.name}: sha256 {digest} differs from the registry — re-inspect")
    with f.open(encoding=source.get("encoding", "utf-8-sig"), newline="") as fh:
        rows = list(csv.DictReader(fh))
    hits = [r for r in rows if (r.get(source["name_column"]) or "").strip() == source["row_name"]]
    if len(hits) != 1:
        raise SystemExit(
            f"{f.name}: expected exactly one row named {source['row_name']!r}, got {len(hits)}"
        )
    return hits[0]


def _polygon_check(sgis_dir: Path, version: str, admin_code: str, lat: float, lon: float) -> str:
    """INSIDE / OUTSIDE of the SGIS 시도 polygon; UNKNOWN when the polygon is unavailable."""
    shp = sgis_dir / f"bnd_sido_00_{version}.shp"
    if not shp.is_file():
        return "UNKNOWN"
    import geopandas as gpd
    from shapely.geometry import Point

    sido = gpd.read_file(shp, encoding="utf-8")
    poly = sido[sido.SIDO_CD.astype(str) == admin_code]
    if len(poly) != 1 or sido.crs is None:
        return "UNKNOWN"
    pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(sido.crs).iloc[0]
    return "INSIDE" if bool(poly.geometry.iloc[0].contains(pt)) else "OUTSIDE"


def build(source_dir: Path, sgis_dir: Path, version: str) -> list[dict[str, object]]:
    registry = json.loads(registry_path().read_text(encoding="utf-8"))
    reps = {m.municipality_id: m for m in load_municipalities()}
    out: list[dict[str, object]] = []
    for t in registry["targets"]:
        mid = t["municipality_id"]
        if TARGETS.get(mid) != t["office_name"]:
            raise SystemExit(f"registry target {mid} / {t['office_name']} is not a V0.3 target")
        row: dict[str, object] = dict.fromkeys(COLUMNS)
        row.update(
            municipality_id=mid,
            municipality_name=t["municipality_name"],
            office_name=t["office_name"],
            office_status=t["status"],
            note=t.get("note", ""),
        )
        if t["status"] == STATUS_AVAILABLE:
            s = t["source"]
            pub = _published_row(s, source_dir)
            lat = float(pub[s["lat_column"]])
            lon = float(pub[s["lon_column"]])
            rep = reps[mid]
            check = _polygon_check(sgis_dir, version, rep.admin_code, lat, lon)
            note = str(t.get("note", ""))
            if check == "OUTSIDE":
                note = (
                    note + " OFFICE_OUTSIDE_MUNICIPALITY: the published point lies outside "
                    "the SGIS boundary; kept as published."
                ).strip()
            row.update(
                address=(pub.get(s["address_column"]) or "").strip() or None,
                latitude=pub[s["lat_column"]].strip(),  # the published text, unrounded
                longitude=pub[s["lon_column"]].strip(),
                coordinate_method=COORDINATE_METHOD,
                coordinate_crs=s["coordinate_crs"],
                source_dataset=s["dataset"],
                source_dataset_id=s["dataset_id"],
                source_provider=s["provider"],
                source_url=s["url"],
                source_last_modified=s["last_modified"],
                source_licence=s["licence"],
                source_row_name=s["row_name"],
                polygon_check=check,
                distance_to_representative_km=round(
                    haversine_km(rep.latitude, rep.longitude, lat, lon), 3
                ),
                note=note,
            )
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", default=str(paths.municipality_source_dir()))
    ap.add_argument("--sgis", default=str(paths.municipality_sgis_dir()))
    ap.add_argument("--version", default="2025_2Q")
    ap.add_argument(
        "--output", default=str(REPO / "assets" / "libraries" / "korea_official_offices.csv")
    )
    args = ap.parse_args(argv)
    rows = build(Path(args.source).expanduser(), Path(args.sgis).expanduser(), args.version)
    problems = validate([{k: ("" if v is None else v) for k, v in r.items()} for r in rows])
    if problems:
        for p in problems:
            print("invalid:", p, file=sys.stderr)
        return 2
    out = Path(args.output)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    n = sum(r["office_status"] == STATUS_AVAILABLE for r in rows)
    print(f"wrote {out} — {len(rows)} targets, {n} with a government-published coordinate")
    for r in rows:
        print(
            f"  {r['municipality_id']} {r['office_name']}: {r['office_status']}"
            + (
                f" ({r['latitude']}, {r['longitude']}) polygon {r['polygon_check']}, "
                f"{r['distance_to_representative_km']} km from the representative point"
                if r["office_status"] == STATUS_AVAILABLE
                else ""
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
