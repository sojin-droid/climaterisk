#!/usr/bin/env python
"""Build the Korea municipality representative-point dataset from the official SGIS boundaries.

Source: 국가데이터처 (Statistics Korea) *SGIS 행정구역 통계 및 경계* on data.go.kr
(dataset 15129688, 이용허락범위 제한 없음), boundary files ``bnd_sido_00_<ver>.shp`` (17 시도)
and ``bnd_sigungu_00_<ver>.shp`` (시군구 statistical units), EPSG:5179.

For every unit the representative point is the polygon's **interior point**
(``shapely.representative_point()``: a point guaranteed to lie inside the official
boundary, computed in the projected CRS and then transformed to WGS84). It is a spatial
anchor for hazard screening — *not* a city-hall location and *not* an area aggregate:

    point_type = BOUNDARY_INTERIOR_POINT

No coordinate is typed in or copied from a web page; every value is derived from the
official geometry by this script. When the 행정안전부 *민원행정기관 전자지도* (office
locations; released only after an application on juso.go.kr) becomes available, rows with
``point_type = OFFICE_LOCATION`` can replace these without a schema change.

Runs in the CLIMADA worker environment (needs geopandas)::

    ./.climada-env/bin/python scripts/build_korea_municipalities.py \\
        --source ~/climada/data/municipality_src/extracted --version 2025_2Q \\
        --output assets/libraries/korea_municipalities.csv

The output is small (a few hundred rows) and is committed as a library asset; the source
package (~270 MB) stays outside the repository.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk.physical_risk.municipalities import (  # noqa: E402
    COLUMNS,
    POINT_TYPE_BOUNDARY_INTERIOR,
    level_for_name,
    validate,
)

SOURCE_NAME = "국가데이터처 SGIS 행정구역 통계 및 경계 (data.go.kr 15129688)"
SOURCE_LICENCE = "공공데이터포털 이용허락범위 제한 없음"


def build(source_dir: Path, version: str) -> list[dict[str, object]]:
    """Read the two boundary layers and derive one row per unit."""
    import geopandas as gpd

    sido = gpd.read_file(source_dir / f"bnd_sido_00_{version}.shp", encoding="utf-8")
    sigungu = gpd.read_file(source_dir / f"bnd_sigungu_00_{version}.shp", encoding="utf-8")
    if sido.crs is None or sigungu.crs is None:
        raise SystemExit("boundary files carry no CRS — refusing to guess one")
    province_by_code = {str(r.SIDO_CD): str(r.SIDO_NM) for r in sido.itertuples()}
    base_date = str(sido.BASE_DATE.iloc[0])

    def rows_for(frame, code_col: str, name_col: str, kind: str):  # type: ignore[no-untyped-def]
        # representative_point in the projected CRS, then to WGS84 — never the reverse
        pts = frame.geometry.representative_point()
        wgs = gpd.GeoSeries(pts, crs=frame.crs).to_crs(4326)
        inside = frame.geometry.contains(pts)
        layer = "sido" if kind == "SIDO" else "sigungu"
        out = []
        for i, r in enumerate(frame.itertuples()):
            code = str(getattr(r, code_col))
            name = str(getattr(r, name_col))
            if not bool(inside.iloc[i]):
                raise SystemExit(f"{name}: representative point is not inside its polygon")
            province = name if kind == "SIDO" else province_by_code.get(code[:2], "")
            out.append(
                {
                    "municipality_id": f"SGIS-{code}",
                    "municipality_name": name,
                    "municipality_level": level_for_name(name, kind),
                    "province_name": province,
                    "office_name": None,
                    "address": None,
                    "latitude": round(float(wgs.iloc[i].y), 6),
                    "longitude": round(float(wgs.iloc[i].x), 6),
                    "point_type": POINT_TYPE_BOUNDARY_INTERIOR,
                    "admin_code": code,
                    "source": SOURCE_NAME,
                    "source_layer": f"bnd_{layer}_00_{version}.shp",
                    "source_version": f"{version} (BASE_DATE {base_date})",
                    "source_licence": SOURCE_LICENCE,
                    "source_crs": frame.crs.to_string(),
                }
            )
        return out

    rows = rows_for(sido, "SIDO_CD", "SIDO_NM", "SIDO")
    rows += rows_for(sigungu, "SIGUNGU_CD", "SIGUNGU_NM", "SIGUNGU")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", required=True, help="directory holding the bnd_*.shp files")
    ap.add_argument("--version", default="2025_2Q", help="boundary version token in the file names")
    ap.add_argument(
        "--output", default=str(REPO / "assets" / "libraries" / "korea_municipalities.csv")
    )
    args = ap.parse_args(argv)

    rows = build(Path(args.source).expanduser(), args.version)
    problems = validate(rows)
    if problems:
        for p in problems:
            print("invalid:", p, file=sys.stderr)
        return 2
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    levels: dict[str, int] = {}
    for r in rows:
        levels[str(r["municipality_level"])] = levels.get(str(r["municipality_level"]), 0) + 1
    print(f"wrote {out} — {len(rows)} rows, generated {datetime.now(UTC):%Y-%m-%d} UTC")
    print("levels:", levels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
