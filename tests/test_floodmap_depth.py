"""Edge-depth (FwDET) at asset points: analytical checks on a synthetic valley.

Needs shapely / pyproj (the CLIMADA worker environment); skipped elsewhere.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker import floodmap_depth as fd  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402


def v_valley(slope: float = 0.01):  # type: ignore[no-untyped-def]
    """z(x, y) = slope · |x| — a V-shaped floodplain centred on x = 0 (metres)."""
    return lambda xy: slope * np.abs(np.asarray(xy)[:, 0])


def test_depth_in_a_v_valley_matches_the_analytical_value() -> None:
    # Flood extent |x| <= 500 m: the edge sits at z = 5 m everywhere on the long sides.
    extent = box(-500, -5000, 500, 5000)
    for x, expected in [(0.0, 5.0), (100.0, 4.0), (-250.0, 2.5), (499.0, 0.01)]:
        h, inside = fd.depth_at_point(x, 0.0, [extent], v_valley())
        assert inside
        np.testing.assert_allclose(h, expected, atol=1e-9)


def test_outside_the_extent_is_zero_and_flagged_outside() -> None:
    h, inside = fd.depth_at_point(800.0, 0.0, [box(-500, -5000, 500, 5000)], v_valley())
    assert (h, inside) == (0.0, False)


def test_depth_is_never_negative() -> None:
    # Ground at the asset higher than the edge (a mound inside the extent) → 0 m.
    def mound(xy):  # type: ignore[no-untyped-def]
        xy = np.asarray(xy)
        return np.where(np.hypot(xy[:, 0], xy[:, 1]) < 1, 9.0, 1.0)

    h, inside = fd.depth_at_point(0.0, 0.0, [box(-500, -500, 500, 500)], mound)
    assert inside and h == 0.0


def test_dem_gap_inside_gives_nan_not_zero() -> None:
    h, inside = fd.depth_at_point(
        0.0, 0.0, [box(-500, -500, 500, 500)], lambda xy: np.full(len(xy), np.nan)
    )
    assert inside and np.isnan(h)


def test_hole_boundaries_count_as_flood_edge() -> None:
    # An island (hole) close to the asset is also a waterline.
    outer = [(-5000, -5000), (5000, -5000), (5000, 5000), (-5000, 5000)]
    hole = [(50, -50), (150, -50), (150, 50), (50, 50)]
    pts = fd.edge_points(Polygon(outer, [hole]), spacing_m=30.0)
    assert ((pts[:, 0] >= 50) & (pts[:, 0] <= 150) & (np.abs(pts[:, 1]) <= 50)).any()


def test_sparse_edge_falls_back_to_nearest_samples() -> None:
    # Radius 10 m catches no edge point; the 5 nearest are used instead.
    params = fd.DepthParams(radius_m=10.0, spacing_m=30.0, min_samples=5)
    h, _ = fd.depth_at_point(0.0, 0.0, [box(-500, -5000, 500, 5000)], v_valley(), params)
    np.testing.assert_allclose(h, 5.0, atol=1e-9)


def test_return_period_lower_bound_from_a_family_with_fewer_maps() -> None:
    per = {"national": {100: 0.5, 200: 0.8, 500: 1.2}, "local": {100: 1.0}}
    got = [fd.depth_for_return_period(rp, per) for rp in fd.RETURN_PERIODS]
    assert got == [1.0, 1.0, 1.2]  # local 100-y depth carried to 200 y; national wins at 500 y
    assert fd.depth_for_return_period(100, {}) == 0.0  # outside every map
    assert np.isnan(fd.depth_for_return_period(100, {"national": {100: float("nan")}}))


def test_class_cross_check_detects_ordering() -> None:
    rising = [(f"N33{k}", float(k) + d) for k in range(5) for d in (0.0, 0.1, 0.2, 0.3, 0.4)]
    assert fd.class_monotonic(rising)["monotonic"] is True
    shuffled = [("N330", 3.0)] * 5 + [("N331", 0.5)] * 5 + [("N332", 2.0)] * 5
    assert fd.class_monotonic(shuffled)["monotonic"] is False
    assert fd.class_monotonic([("N330", 1.0)] * 5)["monotonic"] is None  # too few classes


def test_shapefile_header_bounds_are_read_without_geometry(tmp_path: Path) -> None:
    gpd = pytest.importorskip("geopandas")
    shp_dir = tmp_path / "x"
    shp_dir.mkdir()
    gpd.GeoDataFrame({"SEG_CODE": ["N330"]}, geometry=[box(10, 20, 30, 40)], crs=5186).to_file(
        shp_dir / "T_100.shp"
    )
    zp = tmp_path / "T_100.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in shp_dir.iterdir():
            z.write(f, f.name)
    assert fd._shp_bounds(zp) == (10.0, 20.0, 30.0, 40.0)
    classes = fd._read_polygons(str(zp))
    assert len(classes) == 1 and classes[0][0] == "N330"


def test_clip_window_border_is_not_a_flood_edge() -> None:
    # Two depth classes side by side make one extent |x| <= 500; the class seam at x = 0
    # and the clip window must not be read as edges.
    classes = [("N330", box(-500, -5000, 0, 5000)), ("N331", box(0, -5000, 500, 5000))]
    parts, border = fd.local_extent(classes, 100.0, 0.0, 200.0)  # window misses every edge
    h, inside = fd.depth_at_point(100.0, 0.0, parts, v_valley(), border=border)
    assert inside and np.isnan(h)  # caller widens the window
    parts, border = fd.local_extent(classes, 100.0, 0.0, 2000.0)
    h, inside = fd.depth_at_point(100.0, 0.0, parts, v_valley(), border=border)
    np.testing.assert_allclose(h, 4.0, atol=1e-9)
