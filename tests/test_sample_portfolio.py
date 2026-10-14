"""The synthetic 1,000-asset portfolio: balanced classes, consistent with the spec and config.

The committed CSV is checked without geopandas; the byte-for-byte rebuild needs the worker
environment and the SGIS boundary files under the data root, and skips otherwise.
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
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["asset_class"]] = counts.get(r["asset_class"], 0) + 1
    assert counts == b.class_counts(spec["n_assets"], list(classes))
    lo, hi, step = spec["value_usd_min"], spec["value_usd_max"], spec["value_rounding_usd"]
    for r in rows:
        value = int(r["asset_value_usd"])
        assert lo <= value <= hi and value % step == 0, r["facility_id"]
        assert r["property_type"] in classes[r["asset_class"]]["property_types"]


def test_every_property_type_maps_to_its_class_in_the_engine_config() -> None:
    """The class label is the JRC sector the engine will use — never a second taxonomy."""
    b = _builder()
    for cls, entry in b.asset_classes(b.load_spec()).items():
        for ptype in entry["property_types"]:
            assert jrc_sector_for(ptype) == cls, (ptype, cls)


def test_points_sit_in_korea_and_name_a_real_municipality(rows: list[dict[str, str]]) -> None:
    with MUNICIPALITIES.open(encoding="utf-8", newline="") as fh:
        units = {r["municipality_id"]: r for r in csv.DictReader(fh)}
    for r in rows:
        lat, lon = float(r["latitude"]), float(r["longitude"])
        assert 33.0 <= lat <= 38.7 and 124.5 <= lon <= 131.0, r["facility_id"]
        unit = units[r["municipality_id"]]
        assert unit["municipality_name"] == r["municipality_name"]
        assert unit["province_name"] == r["province_name"]
    # Uniform over units: 1,000 draws over 252 units reach well over half of them.
    assert len({r["municipality_id"] for r in rows}) > 200


def test_rebuild_is_byte_identical(tmp_path: Path) -> None:
    pytest.importorskip("geopandas")
    b = _builder()
    from climaterisk import paths

    source = paths.municipality_sgis_dir()
    version = b.load_spec()["boundary_version"]
    if not (source / f"bnd_sigungu_00_{version}.shp").exists():
        pytest.skip(f"SGIS boundary files not under {source}")
    out = tmp_path / "portfolio.csv"
    assert b.main(["--source", str(source), "--output", str(out)]) == 0
    assert out.read_bytes() == CSV_PATH.read_bytes()
