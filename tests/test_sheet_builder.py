"""Gate for the worker-side sheet builder (``climaterisk_worker.sheet_builder``), written first.

Pure parts are tested everywhere numpy + pyproj exist; the real-map build is skipped when the
환경부 홍수위험지도 is not on this machine. Validity is checked with the backend validator.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyproj")
REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk.sheets import validate_sheet  # noqa: E402
from climaterisk_worker import sheet_builder as sb  # noqa: E402


def hav_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def test_cut_line_east_west_keeps_latitude_and_length() -> None:
    lon, lat, km = sb.cut_line(37.547, 127.047, bearing_deg=90.0, half_km=2.0, step_km=0.03)
    assert np.allclose(lat, lat[0], atol=2e-4)
    assert lon[0] < 127.047 < lon[-1]
    np.testing.assert_allclose(hav_km(lon[0], lat[0], lon[-1], lat[-1]), 4.0, rtol=5e-3)
    assert km[0] == 0.0 and np.all(np.diff(km) > 0)
    np.testing.assert_allclose(np.diff(km), 0.03, rtol=1e-6, atol=1e-9)


def test_cut_line_north_south_keeps_longitude() -> None:
    lon, lat, km = sb.cut_line(37.5, 127.0, bearing_deg=0.0, half_km=1.0, step_km=0.1)
    assert np.allclose(lon, 127.0, atol=1e-6) and lat[0] < 37.5 < lat[-1]
    np.testing.assert_allclose(km[-1], 2.0, rtol=1e-6)


@pytest.mark.parametrize(
    ("classes", "checks", "state", "agree", "of"),
    [
        ({"rp100": "N333", "rp200": "N333", "rp500": "N334"}, {"rp100": 4.05, "rp200": 4.05, "rp500": 2.31}, "partial", 2, 3),
        ({"rp100": "N333", "rp200": "N333", "rp500": "N334"}, {"rp100": 3.0, "rp200": 4.0, "rp500": 6.0}, "agree", 3, 3),
        ({"rp100": "N331", "rp200": "N331", "rp500": "N331"}, {"rp100": 3.0, "rp200": 3.0, "rp500": 3.0}, "disagree", 0, 3),
        ({"rp100": "N333", "rp200": "", "rp500": "N334"}, {"rp100": None, "rp200": None, "rp500": None}, "empty", 0, 0),
        ({"rp100": "", "rp200": "", "rp500": ""}, {"rp100": None, "rp200": None, "rp500": None}, "empty", 0, 0),
    ],
)
def test_review_stamp(classes, checks, state, agree, of) -> None:  # type: ignore[no-untyped-def]
    s = sb.review_stamp(classes, checks)
    assert (s["state"], s.get("agree", 0), s.get("of", 0)) == (state, agree, of)


def test_assemble_sheet_from_plain_inputs_is_valid_and_keeps_nulls() -> None:
    lon, lat, km = sb.cut_line(37.547, 127.047, bearing_deg=90.0, half_km=0.3, step_km=0.1)
    n = len(km)
    sheet = sb.assemble_sheet(
        sheet_id="DM-T1",
        title="시험 도면",
        place="시험",
        site={"lat": 37.547, "lon": 127.047},
        cut={"lon": lon.tolist(), "lat": lat.tolist(), "km": km.tolist(), "bearing_deg": 90.0},
        z_ground=[None] + [10.0] * (n - 1),
        ground_source="test_dem",
        z_surface=[11.0] * n,
        surface_source="test_dsm",
        section_classes={"rp100": ["N333"] * n, "rp200": ["N333"] * n, "rp500": ["N334"] * n},
        site_classes={"rp100": "N333", "rp200": "N333", "rp500": "N334"},
        site_z_ground=10.0,
        site_z_surface=11.0,
        check_depths={"rp100": 4.0, "rp200": 4.0, "rp500": None},
        check_dem="test_dem",
        global_depths={"historical": {"rp": [], "depth_m": []}},
        date="2026-11-15",
    )
    assert validate_sheet(sheet) == []
    assert sheet["section"]["z_ground_m"][0] is None
    assert sheet["site"]["depth_m"] == {"rp100": 3.5, "rp200": 3.5, "rp500": 5.0}
    assert sheet["site"]["bands_m"]["rp500"] == [5.0, None]
    assert sheet["stamps"]["author"]["state"] == "T2"
    assert 0.0 <= sheet["site"]["km"] <= sheet["section"]["cut"]["length_km"]


def test_build_sheet_on_the_real_maps_when_present() -> None:
    from climaterisk_worker._paths import paths

    if not paths.floodmap_dir().is_dir():
        pytest.skip("홍수위험지도 not on this machine")
    sheet = sb.build_sheet(37.547, 127.047, sheet_id="DM-T2", bearing_deg=90.0, half_km=0.5, step_km=0.05, with_global=False)
    assert validate_sheet(sheet) == []
    assert sheet["site"]["classes"] == {"rp100": "N333", "rp200": "N333", "rp500": "N334"}
    assert sheet["site"]["depth_m"] == {"rp100": 3.5, "rp200": 3.5, "rp500": 5.0}
