"""The 1,000-asset OSM-building portfolio: balanced classes, consistent with spec and config.

The committed CSV is checked without geopandas; the byte-for-byte rebuild needs the worker
environment, the OSM extract and the SGIS boundary files under the data root, and the
GDAL / GEOS / PROJ build recorded in build_env.json — it skips otherwise (conftest isolates
the data root, so it runs only when pointed at real data).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from climaterisk.physical_risk.metrics import jrc_sector_for

REPO = Path(__file__).resolve().parents[1]
CSV_PATH = REPO / "tests" / "fixtures" / "sample_portfolio_1000" / "portfolio.csv"
MUNICIPALITIES = REPO / "assets" / "libraries" / "korea_municipalities.csv"


def _builder() -> Any:
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "build_sample_portfolio", REPO / "scripts" / "build_sample_portfolio.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_class_counts_split_evenly() -> None:
    b = _builder()
    assert b.class_counts(1000, ["commercial", "residential", "industrial"]) == {
        "commercial": 334,
        "residential": 333,
        "industrial": 333,
    }
    assert sum(b.class_counts(7, ["a", "b", "c"]).values()) == 7


def test_planned_counts_come_from_the_spec() -> None:
    b = _builder()
    spec = b.load_spec()
    assert b.planned_counts(spec) == {"commercial": 405, "residential": 405, "industrial": 190}
    broken = {**spec, "n_assets": 999}
    with pytest.raises(SystemExit):
        b.planned_counts(broken)
    no_counts = {
        **spec,
        "asset_classes": {
            c: {k: v for k, v in e.items() if k != "n_assets"} if isinstance(e, dict) else e
            for c, e in spec["asset_classes"].items()
        },
    }
    assert b.planned_counts(no_counts) == b.class_counts(1000, list(b.asset_classes(spec)))


def test_no_class_puts_two_buildings_in_one_hazard_cell(rows: list[dict[str, str]]) -> None:
    """Spec 2.2: buildings in one cell share every hazard value, so each class keeps at most
    ``max_per_cell_per_class`` per cell. The cell is the centroid CLIMADA assigns, written
    by the builder; the worker-env test below re-derives it with CLIMADA."""
    spec = _builder().load_spec()
    per_cell: dict[tuple[str, str], int] = {}
    for r in rows:
        assert r["hazard_centroid"], r["facility_id"]
        key = (r["asset_class"], r["hazard_centroid"])
        per_cell[key] = per_cell.get(key, 0) + 1
    assert max(per_cell.values()) <= spec["max_per_cell_per_class"]


def test_hazard_centroid_is_the_one_climada_assigns(rows: list[dict[str, str]]) -> None:
    """The written cell equals ``Exposures.assign_centroids`` on the CSV coordinates."""
    pytest.importorskip("climada")
    import pandas as pd
    from climada.entity import Exposures
    from climada.util.api_client import Client

    b = _builder()
    ref = b.load_spec()["hazard_cell_reference"]
    try:
        hazard = Client().get_hazard(ref["hazard_type"], name=ref["name"], version=ref["version"])
    except Exception as exc:  # offline and not cached
        pytest.skip(f"reference hazard not available: {exc}")
    exp = Exposures(
        pd.DataFrame(
            {
                "latitude": [float(r["latitude"]) for r in rows],
                "longitude": [float(r["longitude"]) for r in rows],
                "value": 1.0,
            }
        )
    )
    exp.assign_centroids(hazard)
    lat, lon = hazard.centroids.lat, hazard.centroids.lon
    assigned = [b.centroid_label(lat[i], lon[i]) for i in exp.gdf[f"centr_{hazard.haz_type}"]]
    assert assigned == [r["hazard_centroid"] for r in rows]


def test_portfolio_matches_the_spec(rows: list[dict[str, str]]) -> None:
    b = _builder()
    spec = b.load_spec()
    classes = b.asset_classes(spec)
    assert len(rows) == spec["n_assets"]
    assert len({r["facility_id"] for r in rows}) == len(rows)
    assert len({(r["osm_type"], r["osm_id"]) for r in rows}) == len(rows)  # no building twice
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["asset_class"]] = counts.get(r["asset_class"], 0) + 1
    assert counts == b.planned_counts(spec)
    for r in rows:
        tags = classes[r["asset_class"]]["osm_building_to_property_type"]
        assert tags[r["osm_building"]] == r["property_type"], r["facility_id"]


def test_unit_values_are_the_cited_building_costs_at_the_cited_rate() -> None:
    """Spec 2.3: 2026 건물신축가격기준액 (KRW / m²) ÷ BOK 2025 average KRW / USD."""
    b = _builder()
    spec = b.load_spec()
    assert spec["fx"]["krw_per_usd"] == 1422.22
    cited = spec["building_cost_source"]["values_krw_per_m2"]
    for cls, entry in b.asset_classes(spec).items():
        assert entry["building_cost_krw_per_m2"] == cited[entry["building_cost_category"]], cls
    approx = pytest.approx
    assert b.unit_value_usd(spec, "residential") == approx(860000 / 1422.22, rel=1e-12)
    assert b.unit_value_usd(spec, "commercial") == approx(604.69, abs=0.005)
    assert b.unit_value_usd(spec, "industrial") == approx(590.63, abs=0.005)
    assert not {"value_usd_min", "value_usd_max"} & set(spec)  # no bounds


def test_value_is_footprint_times_floors_times_unit_value(rows: list[dict[str, str]]) -> None:
    """Each value is reproducible from the row's own OSM fields and the spec's cited costs."""
    b = _builder()
    spec = b.load_spec()
    classes = b.asset_classes(spec)
    for r in rows:
        unit = b.unit_value_usd(spec, r["asset_class"])
        levels = int(r["building_levels"])
        expected = b.asset_value(float(r["footprint_m2"]), levels, unit)
        # footprint is written rounded to 0.1 m²: ±0.05 m² of floor plate on every floor
        assert abs(int(r["asset_value_usd"]) - expected) <= 0.05 * levels * unit + 1, r
        assert int(r["asset_value_usd"]) > 0, r["facility_id"]
        if r["levels_from_osm"] == "no":
            assert int(r["building_levels"]) == classes[r["asset_class"]]["default_levels"]


def test_every_building_uses_an_observed_floor_count(rows: list[dict[str, str]]) -> None:
    """Spec 2.1: only buildings whose OSM record carries building:levels are eligible, so no
    value rests on a class-default floor count."""
    spec = _builder().load_spec()
    assert spec["require_levels_tag"] is True
    defaulted = [r["facility_id"] for r in rows if r["levels_from_osm"] != "yes"]
    assert not defaulted, f"{len(defaulted)} rows use a default floor count: {defaulted[:5]}"
    assert all(1 <= int(r["building_levels"]) <= spec["max_levels"] for r in rows)


def test_levels_tag_parsing() -> None:
    b = _builder()
    assert b.levels_from('"building:levels"=>"12"', 3, 120) == (12, True)
    assert b.levels_from('"building:levels"=>"2.5","name"=>"x"', 3, 120) == (3, True)
    assert b.levels_from('"building:levels"=>"B1"', 3, 120) == (3, False)
    assert b.levels_from('"building:levels"=>"999"', 3, 120) == (3, False)
    assert b.levels_from(None, 4, 120) == (4, False)


def test_every_property_type_maps_to_its_class_in_the_engine_config() -> None:
    """The class label is the JRC sector the engine will use — never a second taxonomy."""
    b = _builder()
    for cls, entry in b.asset_classes(b.load_spec()).items():
        for tag, ptype in entry["osm_building_to_property_type"].items():
            assert jrc_sector_for(ptype) == cls, (tag, ptype, cls)


def test_points_sit_in_korea_and_name_a_real_municipality(rows: list[dict[str, str]]) -> None:
    with MUNICIPALITIES.open(encoding="utf-8", newline="") as fh:
        units = {r["municipality_id"]: r for r in csv.DictReader(fh)}
    for r in rows:
        lat, lon = float(r["latitude"]), float(r["longitude"])
        assert 33.0 <= lat <= 38.7 and 124.5 <= lon <= 131.0, r["facility_id"]
        unit = units[r["municipality_id"]]
        assert unit["municipality_name"] == r["municipality_name"]
        assert unit["province_name"] == r["province_name"]
    assert len({r["province_name"] for r in rows}) == 17


def test_build_env_is_recorded() -> None:
    """The committed CSV names the GDAL / GEOS / PROJ build it was drawn with."""
    env = json.loads((CSV_PATH.parent / _builder().ENV_FILENAME).read_text(encoding="utf-8"))
    assert {"gdal", "geos", "proj", "pyogrio", "shapely", "geopandas"} <= set(env)


def test_rebuild_is_byte_identical(tmp_path: Path) -> None:
    """Byte identity is promised only on the recorded library build: another GDAL / GEOS can
    assemble a multipolygon or place an interior point across a 시군구 edge, moving the
    eligible pool by a building and with it the seeded draw (docs/sample-portfolio-1000.md)."""
    pytest.importorskip("geopandas")
    b = _builder()
    from climaterisk import paths

    spec = b.load_spec()
    osm = paths.osm_dir(spec["osm_extract"]["file"])
    sgis = paths.municipality_sgis_dir()
    if not osm.exists() or not (sgis / f"bnd_sigungu_00_{spec['boundary_version']}.shp").exists():
        pytest.skip(f"OSM extract or SGIS boundaries not under {paths.data_root()}")
    recorded = json.loads((CSV_PATH.parent / b.ENV_FILENAME).read_text(encoding="utf-8"))
    current = b.build_environment()
    differs = {k: (v, current.get(k)) for k, v in recorded.items() if current.get(k) != v}
    if differs:
        pytest.skip(f"library build differs from the one that drew the CSV: {differs}")
    out = tmp_path / "portfolio.csv"
    assert b.main(["--osm", str(osm), "--sgis", str(sgis), "--output", str(out)]) == 0
    assert out.read_bytes() == CSV_PATH.read_bytes()
    assert json.loads((tmp_path / b.ENV_FILENAME).read_text(encoding="utf-8")) == recorded
