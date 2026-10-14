"""배수·빗물펌프장 library: publisher coordinates only, 계획빈도 as stated, distances right."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from climaterisk.physical_risk import pump_stations as ps

REPO = Path(__file__).resolve().parents[1]
BBOX = {"lat_min": 33.0, "lat_max": 38.7, "lon_min": 124.5, "lon_max": 131.0}


def _builder() -> Any:
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "build_pump_stations", REPO / "scripts" / "build_pump_stations.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_coordinates_are_kept_swapped_back_or_dropped_never_guessed() -> None:
    b = _builder()
    assert b.coordinates("37.5", "127.0", BBOX) == (37.5, 127.0)
    assert b.coordinates("127.0", "37.5", BBOX) == (37.5, 127.0)  # swapped pair put right
    assert b.coordinates("0", "0", BBOX) == (None, None)  # outside Korea: dropped
    assert b.coordinates("", "127.0", BBOX) == (None, None)
    assert b.coordinates("1,234", "x", BBOX) == (None, None)


def test_design_frequency_zero_or_blank_is_not_stated() -> None:
    b = _builder()
    assert b.design_frequency("30") == 30
    assert b.design_frequency("0") is None
    assert b.design_frequency("") is None
    assert b.design_frequency("nan") is None


def test_haversine_matches_a_known_distance() -> None:
    """One degree of latitude is π·R/180 km on the mean sphere."""
    expected = 3.141592653589793 * ps.EARTH_RADIUS_KM / 180.0
    assert ps.distance_km(37.0, 127.0, 38.0, 127.0) == pytest.approx(expected, rel=1e-12)


def test_library_rows_are_located_inside_korea_with_an_allowed_licence() -> None:
    rows = ps.load()
    assert rows, "korea_pump_stations.csv is empty"
    for s in ps.located(rows):
        assert BBOX["lat_min"] <= (s.latitude or 0) <= BBOX["lat_max"], s
        assert BBOX["lon_min"] <= (s.longitude or 0) <= BBOX["lon_max"], s
    assert {s.source_licence for s in rows} <= {"제한 없음", "공공누리 제1유형"}
    assert all(s.design_frequency_years is None or s.design_frequency_years > 0 for s in rows)


def test_nearest_returns_the_closest_located_station() -> None:
    rows = ps.located()
    s0 = rows[0]
    hit = ps.nearest(s0.latitude or 0.0, s0.longitude or 0.0)
    assert hit is not None and hit[1] == pytest.approx(0.0, abs=1e-9)
