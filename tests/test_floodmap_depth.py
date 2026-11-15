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


# --------------------------------------------------------------------------- #
# Main method: official class band                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("code", "lower", "mid", "upper"),
    [
        ("N330", 0.0, 0.25, 0.5),
        ("N331", 0.5, 0.75, 1.0),
        ("N332", 1.0, 1.5, 2.0),
        ("N333", 2.0, 3.5, 5.0),
        ("N334", 5.0, 5.0, 5.0),  # open class: always the configured open depth
    ],
)
def test_representative_depth_per_band(code: str, lower: float, mid: float, upper: float) -> None:
    got = [fd.representative_depth(code, m) for m in ("lower", "mid", "upper")]
    np.testing.assert_allclose(got, [lower, mid, upper], atol=0)


def test_representative_depth_edges() -> None:
    assert fd.representative_depth("", "mid") == 0.0
    assert fd.representative_depth("N334", "upper", open_class_depth_m=6.0) == 6.0
    with pytest.raises(ValueError):
        fd.representative_depth("N335", "mid")
    with pytest.raises(ValueError):
        fd.representative_depth("N330", "median")


def test_in_band_bounds_are_inclusive_and_open_class_has_no_cap() -> None:
    assert fd.in_band("N333", 2.0) and fd.in_band("N333", 5.0) and not fd.in_band("N333", 5.01)
    assert fd.in_band("N334", 40.0) and not fd.in_band("N334", 4.9)


def test_class_for_return_period_takes_deepest_and_carries_local_100() -> None:
    per = {"national": {100: "N331", 200: "N332", 500: "N334"}, "local": {100: "N333"}}
    assert [fd.class_for_return_period(rp, per) for rp in fd.RETURN_PERIODS] == [
        "N333",
        "N333",
        "N334",
    ]
    assert fd.class_for_return_period(100, {}) == ""


def test_class_depths_arrays() -> None:
    out = fd.class_depths([["N333", ""], ["N334", "N330"]], "mid", 5.0)
    np.testing.assert_allclose(out["depth"], [[3.5, 0.0], [5.0, 0.25]])
    np.testing.assert_allclose(out["lower"], [[2.0, 0.0], [5.0, 0.0]])
    assert np.isnan(out["upper"][1, 0]) and out["upper"][0, 0] == 5.0 and out["upper"][0, 1] == 0.0


def _zip_map(tmp_path: Path, name: str, polys: dict[str, object]) -> Path:
    gpd = pytest.importorskip("geopandas")
    d = tmp_path / name
    d.mkdir()
    gpd.GeoDataFrame({"SEG_CODE": list(polys)}, geometry=list(polys.values()), crs=5186).to_file(
        d / f"{name}.shp"
    )
    zp = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in d.iterdir():
            z.write(f, f.name)
    return zp


def test_classes_at_points_on_synthetic_maps(tmp_path: Path) -> None:
    from pyproj import Transformer

    to_ll = Transformer.from_crs(5186, 4326, always_xy=True)
    # national 100 y: nested classes (deepest wins); local 100 y covers a second area
    nat = _zip_map(
        tmp_path, "N_100", {"N331": box(0, 0, 1000, 1000), "N333": box(400, 400, 600, 600)}
    )
    nat5 = _zip_map(tmp_path, "N_500", {"N334": box(0, 0, 1000, 1000)})
    loc = _zip_map(tmp_path, "L_100", {"N332": box(2000, 0, 3000, 1000)})
    index = [
        {"path": str(nat), "family": "national", "rp": 100, "bounds": fd._shp_bounds(nat)},
        {"path": str(nat5), "family": "national", "rp": 500, "bounds": fd._shp_bounds(nat5)},
        {"path": str(loc), "family": "local", "rp": 100, "bounds": fd._shp_bounds(loc)},
    ]
    pts_xy = [(500, 500), (100, 100), (2500, 500), (5000, 5000)]
    pts = [to_ll.transform(x, y) for x, y in pts_xy]
    res = fd.classes_at_points(pts, index, fd.ClassIndex())
    assert res["codes"][0] == ["N333", "N331", "N332", ""]  # 100 y
    assert res["codes"][1] == ["N333", "N331", "N332", ""]  # 200 y: carried from 100 y
    assert res["codes"][2] == ["N334", "N334", "N332", ""]  # 500 y
    assert (
        res["file100"][0] == str(nat)
        and res["file100"][2] == str(loc)
        and res["file100"][3] is None
    )


# --------------------------------------------------------------------------- #
# Sub check summary                                                              #
# --------------------------------------------------------------------------- #
def test_check_summary_agreement_and_warn_rule() -> None:
    good = [(f"N33{k}", d, "dem") for k, d in ((0, 0.3), (1, 0.7), (2, 1.5), (3, 3.0))] * 6
    s = fd.check_summary(good)
    assert s["n_points"] == 24 and s["share_in_band"] == 1.0 and s["monotonic"] is True
    assert s["warn"] is False and s["dem_used"] == {"dem": 24}
    bad = [(f"N33{k}", d, "dem") for k, d in ((0, 3.0), (1, 2.5), (2, 0.2), (3, 0.1))] * 6
    s = fd.check_summary(bad)
    assert s["warn"] is True and "inside the official band" in s["warn_reason"]
    assert "not monotonic" in s["warn_reason"]


def test_check_summary_needs_enough_points_and_ignores_nan() -> None:
    few = [("N333", 0.1, "dem")] * 5 + [("N333", float("nan"), "")]
    s = fd.check_summary(few)
    assert s["n_points"] == 5 and s["n_not_computable"] == 1 and s["warn"] is False
    assert fd.check_summary([])["share_in_band"] is None


def test_strict_sampler_refuses_partial_coverage() -> None:
    inner = lambda xy: np.where(np.asarray(xy)[:, 0] < 10, 1.0, np.nan)  # noqa: E731
    strict = fd.StrictSampler(inner)
    assert np.isfinite(strict(np.array([[1.0, 0.0], [2.0, 0.0]]))).all()
    assert np.isnan(strict(np.array([[1.0, 0.0], [20.0, 0.0]]))).all()
