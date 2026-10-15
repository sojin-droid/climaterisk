#!/usr/bin/env python
"""Build the 1,000-asset Korea test portfolio from real OpenStreetMap buildings.

Every asset is a **real OSM building** with a use tag: its location (interior point of the
footprint), footprint and use come from OpenStreetMap; its 시도 / 시군구 from the official
SGIS boundaries. Only the **value** is an assumption — footprint × floors × a class unit
value from the spec — so the numbers are a method test, never a valuation. Each class has
hundreds of assets (spec counts) so the portfolio / asset-class / 시도 loss curves can be
read per class, and no class puts two buildings in one hazard grid cell; the 5-asset
validation fixture stays the golden regression set.

Everything that shapes the sample — seed, size, which OSM ``building=*`` tags belong to
which class, default floors, unit values, bounds — lives in
``assets/libraries/sample_portfolio_spec.json``; this script holds no tuning number. Same
spec + same extract + same boundaries + same GDAL / GEOS / PROJ build ⇒ byte-identical CSV.
A different library build can assemble a multipolygon or place an interior point on the
other side of a 시군구 edge, which changes the eligible pool by a building and so the draw;
the versions used are written next to the CSV as ``build_env.json``.

Algorithm:
    $$k(b) = \\arg\\min_{g \\in G}
      \\lVert (\\varphi_b, \\lambda_b) - (\\varphi_g, \\lambda_g) \\rVert,$$
    $$S_c = \\text{first } n_c \\text{ of } \\pi(B_c) \\text{ accepted while }
      |\\{b' \\in S_c : k(b') = k(b)\\}| < m,$$
    $$V_b = \\mathrm{clip}\\big(A_b \\cdot L_b \\cdot u_c,\\ V_{min},\\ V_{max}\\big)$$
    ASCII: cell k(b) = the nearest centroid of the reference hazard grid G (CLIMADA
    match_coordinates, as assign_centroids); visit B_c in a seeded random order π and accept
    a building while its cell holds fewer than m of class c, until n_c are accepted;
    value V_b = footprint A_b × floors L_b × unit value u_c, clipped, rounded.

    ``n_c``: class count (spec ``asset_classes.<c>.n_assets``, else an even split);
    ``G``: centroids of the spec's ``hazard_cell_reference`` Data API set (degrees);
    ``m``: buildings of one class allowed per cell (spec ``max_per_cell_per_class``);
    ``φ_b, λ_b``: the building's WGS84 latitude / longitude (degrees, as in the CSV);
    ``B_c``: OSM buildings whose tag maps to class c and whose interior point lies in a
    시군구 — and, when the spec sets ``require_levels_tag``, that carry a usable
    ``building:levels``; ``A_b``: footprint area (m², EPSG:5179); ``L_b``:
    ``building:levels`` when a positive number ≤ max_levels, else the class default (floors;
    never used under ``require_levels_tag``); ``u_c``: unit value (USD / m²); ``V_min`` /
    ``V_max``: clip bounds (USD).

Data: ``<DATA_ROOT>/external/osm/<spec osm_extract.file>`` (Geofabrik, ODbL — "©
OpenStreetMap contributors") and ``<DATA_ROOT>/external/municipality/sgis``. Runs in the
CLIMADA worker environment (needs geopandas / pyogrio with the GDAL OSM driver)::

    ./.climada-env/bin/python scripts/build_sample_portfolio.py \\
        [--osm <pbf>] [--sgis <dir>] [--output tests/fixtures/sample_portfolio_1000/portfolio.csv]
"""

from __future__ import annotations

import argparse
import csv
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

SPEC_PATH = REPO / "assets" / "libraries" / "sample_portfolio_spec.json"
DEFAULT_OUTPUT = REPO / "tests" / "fixtures" / "sample_portfolio_1000" / "portfolio.csv"
ENV_FILENAME = "build_env.json"
COLUMNS: tuple[str, ...] = (
    "facility_id",
    "facility_name",
    "latitude",
    "longitude",
    "asset_value_usd",
    "asset_value_currency",
    "property_type",
    "asset_class",
    "osm_type",
    "osm_id",
    "osm_building",
    "building_levels",
    "levels_from_osm",
    "footprint_m2",
    "province_name",
    "municipality_name",
    "municipality_id",
    "hazard_centroid",
)
_LEVELS = re.compile(r'"building:levels"=>"([^"]*)"')


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    """The sample specification (seed, size, class tags, value rule)."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def asset_classes(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The class entries of the spec (keys starting with ``_`` are notes, not classes)."""
    return {k: v for k, v in spec["asset_classes"].items() if not k.startswith("_")}


def class_counts(n_assets: int, classes: list[str]) -> dict[str, int]:
    """Split ``n_assets`` across ``classes`` as evenly as possible (first classes get +1)."""
    base, extra = divmod(n_assets, len(classes))
    return {c: base + (1 if i < extra else 0) for i, c in enumerate(classes)}


def planned_counts(spec: dict[str, Any]) -> dict[str, int]:
    """Assets per class: each class's ``n_assets`` when the spec sets it, else an even split.

    Raises:
        SystemExit: when the per-class counts do not add up to the spec's ``n_assets``.
    """
    classes = asset_classes(spec)
    if not all("n_assets" in entry for entry in classes.values()):
        return class_counts(int(spec["n_assets"]), list(classes))
    counts = {c: int(entry["n_assets"]) for c, entry in classes.items()}
    if sum(counts.values()) != int(spec["n_assets"]):
        raise SystemExit(f"class counts {counts} do not sum to n_assets {spec['n_assets']}")
    return counts


def centroid_label(latitude: float, longitude: float) -> str:
    """How a hazard centroid is written to the CSV (``hazard_centroid``)."""
    return f"{latitude:.6f},{longitude:.6f}"


def hazard_centroids(spec: dict[str, Any], latitude: Any, longitude: Any) -> list[str | None]:
    """The hazard centroid each point is read from, exactly as the engine assigns it.

    Uses CLIMADA's own ``match_coordinates`` (what ``Exposures.assign_centroids`` calls,
    euclidean, default 100 km threshold) against the centroids of the spec's
    ``hazard_cell_reference`` Data API set. The Korea sets hold only some nodes of the
    150-arcsec rectangle, so a coastal point can be read from a neighbouring node — plain
    rounding to the grid would miss that. ``None`` marks a point no centroid reaches.
    """
    import numpy as np
    from climada.util.api_client import Client
    from climada.util.coordinates import match_coordinates

    ref = spec["hazard_cell_reference"]
    hazard = Client().get_hazard(ref["hazard_type"], name=ref["name"], version=ref["version"])
    lat = np.asarray(hazard.centroids.lat, dtype=float)
    lon = np.asarray(hazard.centroids.lon, dtype=float)
    points = np.column_stack([np.asarray(latitude, float), np.asarray(longitude, float)])
    idx = match_coordinates(points, np.column_stack([lat, lon]))
    return [centroid_label(lat[i], lon[i]) if i >= 0 else None for i in idx]


def levels_from(other_tags: str | None, default: int, max_levels: int) -> tuple[int, bool]:
    """Floors from an OGR ``other_tags`` string: ``(levels, from_tag)``.

    ``building:levels`` is used when it parses as a positive number ≤ ``max_levels``
    (rounded half up: "2.5" → 3); anything else falls back to the class default.
    """
    m = _LEVELS.search(other_tags or "")
    if m:
        try:
            value = float(m.group(1).strip())
        except ValueError:
            value = 0.0
        if 0 < value <= max_levels:
            return int(value + 0.5), True
    return default, False


def asset_value(footprint_m2: float, levels: int, unit_value: float, spec: dict[str, Any]) -> int:
    """Footprint × floors × unit value, clipped to the spec bounds and rounded."""
    lo, hi = float(spec["value_usd_min"]), float(spec["value_usd_max"])
    step = float(spec["value_rounding_usd"])
    raw = footprint_m2 * levels * unit_value
    return int(round(min(max(raw, lo), hi) / step) * step)


def build_environment() -> dict[str, str]:
    """Versions of the geometry libraries the draw depends on (written as build_env.json)."""
    import geopandas
    import pyogrio
    import pyproj
    import shapely

    return {
        "gdal": pyogrio.__gdal_version_string__,
        "geos": shapely.geos_version_string,
        "proj": pyproj.proj_version_str,
        "pyogrio": pyogrio.__version__,
        "shapely": shapely.__version__,
        "geopandas": geopandas.__version__,
    }


def _read_buildings(osm_path: Path, tags: list[str]) -> Any:
    """OSM building polygons with one of ``tags``, in EPSG:5179 with area and interior point."""
    import pyogrio

    quoted = ", ".join(f"'{t}'" for t in sorted(tags))
    frame = pyogrio.read_dataframe(
        osm_path,
        layer="multipolygons",
        columns=["osm_id", "osm_way_id", "building", "other_tags"],
        where=f"building IN ({quoted})",
    )
    frame = frame.to_crs(5179)
    frame["footprint_m2"] = frame.geometry.area
    frame = frame[frame["footprint_m2"] > 0].copy()
    frame["osm_type"] = frame["osm_way_id"].notna().map({True: "way", False: "relation"})
    frame["osm_ref"] = frame["osm_way_id"].fillna(frame["osm_id"]).astype("int64")
    frame["geometry"] = frame.geometry.representative_point()
    return frame.sort_values(["osm_type", "osm_ref"]).reset_index(drop=True)


def build(spec: dict[str, Any], osm_path: Path, sgis_dir: Path) -> list[dict[str, Any]]:
    """The portfolio rows, deterministic in ``spec['seed']``."""
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    classes = asset_classes(spec)
    tag_class = {
        tag: (cls, ptype)
        for cls, entry in classes.items()
        for tag, ptype in entry["osm_building_to_property_type"].items()
    }
    buildings = _read_buildings(osm_path, list(tag_class))

    version = str(spec["boundary_version"])
    sido = gpd.read_file(sgis_dir / f"bnd_sido_00_{version}.shp", encoding="utf-8")
    units = gpd.read_file(sgis_dir / f"bnd_sigungu_00_{version}.shp", encoding="utf-8")
    if units.crs is None or sido.crs is None:
        raise SystemExit("boundary files carry no CRS — refusing to guess one")
    province_by_code = {str(r.SIDO_CD): str(r.SIDO_NM) for r in sido.itertuples()}
    units = units.to_crs(5179)[["SIGUNGU_CD", "SIGUNGU_NM", "geometry"]]
    placed = gpd.sjoin(buildings, units, how="inner", predicate="within")
    placed = placed[~placed.index.duplicated(keep="first")].sort_index()
    placed["asset_class"] = placed["building"].map(lambda t: tag_class[t][0])
    max_levels = int(spec["max_levels"])
    if spec.get("require_levels_tag"):
        # Only buildings whose floor count OpenStreetMap records: the value then uses an
        # observed number of floors, never a class default.
        tagged = placed["other_tags"].map(lambda t: levels_from(t, 1, max_levels)[1])
        placed = placed[tagged.to_numpy(dtype=bool)]

    # The hazard cell is matched from the coordinates exactly as the CSV will carry them.
    wgs_pool = placed.geometry.to_crs(4326)
    placed = placed.assign(
        cell=hazard_centroids(spec, np.round(wgs_pool.y, 6), np.round(wgs_pool.x, 6))
    )
    placed = placed[placed["cell"].notna()]
    cap = int(spec["max_per_cell_per_class"])

    rng = np.random.default_rng(int(spec["seed"]))
    counts = planned_counts(spec)
    picks = []
    for cls in classes:
        pool = placed[placed["asset_class"] == cls]
        visit = pool.iloc[rng.permutation(len(pool))]
        allowed = visit[visit.groupby("cell", sort=False).cumcount().to_numpy() < cap]
        n_cells = pool["cell"].nunique()
        print(
            f"eligible {cls}: {len(pool):,} buildings in {n_cells:,} cells -> drawing {counts[cls]}"
        )
        if len(allowed) < counts[cls]:
            raise SystemExit(
                f"only {len(allowed)} {cls} buildings fit {cap} per cell for {counts[cls]}"
            )
        picks.append(allowed.iloc[: counts[cls]].sort_index())
    chosen = gpd.GeoDataFrame(pd.concat(picks), crs=buildings.crs)
    chosen = chosen.iloc[rng.permutation(len(chosen))].reset_index(drop=True)
    wgs = chosen.geometry.to_crs(4326)

    rows: list[dict[str, Any]] = []
    for i, b in enumerate(chosen.itertuples()):
        cls, ptype = tag_class[str(b.building)]
        entry = classes[cls]
        levels, tagged = levels_from(b.other_tags, int(entry["default_levels"]), max_levels)
        area = float(b.footprint_m2)
        code = str(b.SIGUNGU_CD)
        rows.append(
            {
                "facility_id": f"OSM-{i + 1:04d}",
                "facility_name": f"OSM {b.osm_type} {b.osm_ref} ({b.building})",
                "latitude": round(float(wgs.iloc[i].y), 6),
                "longitude": round(float(wgs.iloc[i].x), 6),
                "asset_value_usd": asset_value(
                    area, levels, float(entry["unit_value_usd_per_m2"]), spec
                ),
                "asset_value_currency": "USD",
                "property_type": ptype,
                "asset_class": cls,
                "osm_type": b.osm_type,
                "osm_id": int(b.osm_ref),
                "osm_building": str(b.building),
                "building_levels": levels,
                "levels_from_osm": "yes" if tagged else "no",
                "footprint_m2": round(area, 1),
                "province_name": province_by_code.get(code[:2], ""),
                "municipality_name": str(b.SIGUNGU_NM),
                "municipality_id": f"SGIS-{code}",
                "hazard_centroid": str(b.cell),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    spec = load_spec()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--osm", default=str(paths.osm_dir(spec["osm_extract"]["file"])))
    ap.add_argument(
        "--sgis",
        default=str(paths.municipality_sgis_dir()),
        help="directory of the bnd_*.shp files (default: <DATA_ROOT>/external/municipality/sgis)",
    )
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = ap.parse_args(argv)

    osm_path = Path(args.osm)
    if not osm_path.exists():
        raise SystemExit(
            f"{osm_path} not found — download {spec['osm_extract']['source']} into "
            f"{paths.osm_dir()} first"
        )
    rows = build(spec, osm_path, Path(args.sgis))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    env_path = out.parent / ENV_FILENAME
    # LF on every platform (write_text would emit CRLF on Windows).
    env_path.write_bytes((json.dumps(build_environment(), indent=2) + "\n").encode("utf-8"))
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["asset_class"]] = by_class.get(r["asset_class"], 0) + 1
    print(f"wrote {len(rows)} OSM buildings to {out} — {by_class}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
