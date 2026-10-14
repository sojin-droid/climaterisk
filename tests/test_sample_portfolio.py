"""The 1,000-asset OSM-building portfolio: balanced classes, consistent with spec and config.

The committed CSV is checked without geopandas; the byte-for-byte rebuild needs the worker
environment, the OSM extract and the SGIS boundary files under the data root, and skips
otherwise (conftest isolates the data root, so it runs only when pointed at real data).
"""

from __future__ import annotations

import csv
import importlib.util
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
    assert counts == b.class_counts(spec["n_assets"], list(classes))
    for r in rows:
        tags = classes[r["asset_class"]]["osm_building_to_property_type"]
        assert tags[r["osm_building"]] == r["property_type"], r["facility_id"]


def test_value_is_footprint_times_floors_times_unit_value(rows: list[dict[str, str]]) -> None:
    """The one assumed number is reproducible from the row's own OSM fields and the spec."""
    b = _builder()
    spec = b.load_spec()
    classes = b.asset_classes(spec)
    for r in rows:
        unit = float(classes[r["asset_class"]]["unit_value_usd_per_m2"])
        expected = b.asset_value(float(r["footprint_m2"]), int(r["building_levels"]), unit, spec)
        # footprint is written rounded to 0.1 m²: allow one rounding step either way
        assert abs(int(r["asset_value_usd"]) - expected) <= spec["value_rounding_usd"], r
        if r["levels_from_osm"] == "no":
            assert int(r["building_levels"]) == classes[r["asset_class"]]["default_levels"]


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


def test_rebuild_is_byte_identical(tmp_path: Path) -> None:
    pytest.importorskip("geopandas")
    b = _builder()
    from climaterisk import paths

    spec = b.load_spec()
    osm = paths.osm_dir(spec["osm_extract"]["file"])
    sgis = paths.municipality_sgis_dir()
    if not osm.exists() or not (sgis / f"bnd_sigungu_00_{spec['boundary_version']}.shp").exists():
        pytest.skip(f"OSM extract or SGIS boundaries not under {paths.data_root()}")
    out = tmp_path / "portfolio.csv"
    assert b.main(["--osm", str(osm), "--sgis", str(sgis), "--output", str(out)]) == 0
    assert out.read_bytes() == CSV_PATH.read_bytes()
