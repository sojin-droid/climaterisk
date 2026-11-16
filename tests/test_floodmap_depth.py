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


class _FakeCidx:
    """``code_at(path, x, y)`` driven by a dict ``{path: code}`` (no real maps)."""

    def __init__(self, codes: dict[str, str]) -> None:
        self.codes = codes

    def code_at(self, path: str, x: float, y: float) -> str:
        return self.codes.get(path, "")


def _fake_row(family: str, basin: str, rp: int) -> dict[str, object]:
    stem = "RFM_SBSN_NTN" if family == "national" else "RFM_SBSN_RGN"
    return {
        "path": f"/maps/{stem}_{basin}_{rp}.zip",
        "family": family,
        "rp": rp,
        "bounds": (-1e12, -1e12, 1e12, 1e12),
    }


_PT = [(127.0, 37.5)]


def test_basin_of_and_available_files() -> None:
    assert fd.basin_of("/x/RFM_SBSN_NTN_2101_200.zip") == "2101"
    assert fd.basin_of("/x/RFM_SBSN_RGN_1001_100.zip") == "1001"
    assert fd.basin_of("/x/other.zip") is None
    rows = [_fake_row("national", "2101", 100), _fake_row("local", "1001", 100)]
    assert fd.available_files(rows) == {("national", "2101", 100), ("local", "1001", 100)}


def test_missing_national_rp_is_nan() -> None:
    rows = [_fake_row("national", "2101", 100), _fake_row("national", "2101", 500)]
    cidx = _FakeCidx({rows[0]["path"]: "N331", rows[1]["path"]: "N332"})  # type: ignore[dict-item]
    res = fd.classes_at_points(_PT, rows, cidx)  # type: ignore[arg-type]
    assert [res["codes"][k][0] for k in range(3)] == ["N331", None, "N332"]
    assert res["missing"][1][0] is True and res["missing"][0][0] is False
    assert res["reason"] == ["no_file_for_rp:200"]
    assert res["local_only"] == [False]
    dep = fd.class_depths(res["codes"], "mid", 5.0)
    assert np.isnan(dep["depth"][1, 0]) and np.isnan(dep["lower"][1, 0])
    assert np.isnan(dep["upper"][1, 0])
    assert dep["depth"][0, 0] == pytest.approx(0.75)


def test_local_only_is_a_100y_lower_bound() -> None:
    rows = [_fake_row("local", "1001", 100)]
    cidx = _FakeCidx({rows[0]["path"]: "N331"})  # type: ignore[dict-item]
    res = fd.classes_at_points(_PT, rows, cidx)  # type: ignore[arg-type]
    assert [res["codes"][k][0] for k in range(3)] == ["N331"] * 3
    assert res["local_only"] == [True] and res["reason"] == [None]


def test_existing_file_dry_point_keeps_the_envelope() -> None:
    rows = [_fake_row("national", "2101", rp) for rp in (100, 200, 500)]
    cidx = _FakeCidx({rows[0]["path"]: "N331"})  # type: ignore[dict-item]
    res = fd.classes_at_points(_PT, rows, cidx)  # type: ignore[arg-type]
    assert [res["codes"][k][0] for k in range(3)] == ["N331"] * 3
    assert res["reason"] == [None] and res["local_only"] == [False]


def test_outside_everything_reads_zero() -> None:
    rows = [_fake_row("national", "2101", 100), _fake_row("national", "2101", 500)]
    res = fd.classes_at_points(_PT, rows, _FakeCidx({}))  # type: ignore[arg-type]
    assert [res["codes"][k][0] for k in range(3)] == ["", "", ""]
    assert res["reason"] == [None]
    assert fd.class_depths(res["codes"])["depth"][:, 0].tolist() == [0.0, 0.0, 0.0]


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
    assert s["n_points"] == 5 and s["n_not_computable"] == 1 and s["warn"] is None
    assert fd.check_summary([])["share_in_band"] is None


def test_strict_sampler_refuses_partial_coverage() -> None:
    inner = lambda xy: np.where(np.asarray(xy)[:, 0] < 10, 1.0, np.nan)  # noqa: E731
    strict = fd.StrictSampler(inner)
    assert np.isfinite(strict(np.array([[1.0, 0.0], [2.0, 0.0]]))).all()
    assert np.isnan(strict(np.array([[1.0, 0.0], [20.0, 0.0]]))).all()


# --------------------------------------------------------------------------- #
# Coverage table by file name, dataless detection, unreadable maps               #
# --------------------------------------------------------------------------- #
def test_coverage_table_from_names(tmp_path: Path) -> None:
    d100 = tmp_path / "유역별_100년_빈도_국가하천_하천범람지도"
    d500 = tmp_path / "유역별_500년_빈도_국가하천_하천범람지도"
    d100.mkdir()
    d500.mkdir()
    (d100 / "RFM_SBSN_NTN_2101_100.zip").write_bytes(b"")
    (d500 / "RFM_SBSN_NTN_2101_500.zip").write_bytes(b"")
    rows = fd.coverage_table(tmp_path, {**fd.FAMILIES, **fd.URBAN_FAMILIES})
    assert sorted((r["family"], r["basin"], r["rp"], r["state"]) for r in rows) == [
        ("national", "2101", 100, "present"),
        ("national", "2101", 500, "present"),
    ]
    assert set(rows[0]) == {"family", "basin", "rp", "state"}


def test_coverage_table_marks_dataless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "유역별_100년_빈도_국가하천_하천범람지도"
    d.mkdir()
    (d / "RFM_SBSN_NTN_2101_100.zip").write_bytes(b"")
    (d / "RFM_SBSN_NTN_2102_100.zip").write_bytes(b"")
    monkeypatch.setattr(fd, "is_dataless", lambda p: p.name.endswith("2102_100.zip"))
    got = {r["basin"]: r["state"] for r in fd.coverage_table(tmp_path, fd.FAMILIES)}
    assert got == {"2101": "present", "2102": "dataless"}
    assert fd.named_files(tmp_path, fd.FAMILIES) == {
        ("national", "2101", 100),
        ("national", "2102", 100),
    }


def test_is_dataless_reads_the_flag_only_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    from types import SimpleNamespace

    f = tmp_path / "a.zip"
    f.write_bytes(b"")
    monkeypatch.setattr(fd.sys, "platform", "darwin")
    monkeypatch.setattr(os, "stat", lambda p: SimpleNamespace(st_flags=0x40000000))
    assert fd.is_dataless(f) is True
    monkeypatch.setattr(os, "stat", lambda p: SimpleNamespace(st_flags=0))
    assert fd.is_dataless(f) is False
    monkeypatch.setattr(fd.sys, "platform", "linux")
    monkeypatch.setattr(os, "stat", lambda p: SimpleNamespace(st_flags=0x40000000))
    assert fd.is_dataless(f) is False


class _RaisingCidx(_FakeCidx):
    def __init__(self, codes: dict[str, str], bad: set[str]) -> None:
        super().__init__(codes)
        self.bad = bad

    def code_at(self, path: str, x: float, y: float) -> str:
        if path in self.bad:
            raise OSError("dataless / unreadable")
        return super().code_at(path, x, y)


def test_unreadable_file_gives_nan_with_reason(caplog: pytest.LogCaptureFixture) -> None:
    ok = _fake_row("national", "2101", 100)
    bad = _fake_row("national", "2102", 100)
    cidx = _RaisingCidx({ok["path"]: "N331"}, {bad["path"]})  # type: ignore[dict-item]
    with caplog.at_level("WARNING"):
        res = fd.classes_at_points(_PT + _PT, [ok, bad], cidx)  # type: ignore[arg-type]
    # both points sit inside both files' bounds: the unreadable file makes them unknown
    assert res["codes"][0] == [None, None]
    assert all("map_unreadable" in r for r in res["reason"])
    warns = [r for r in caplog.records if "map_unreadable" in r.getMessage()]
    assert len(warns) == 1 and "2102_100.zip" in warns[0].getMessage()
    assert "/maps/" not in warns[0].getMessage()  # file name only
    # another file still reads
    all_rp = [_fake_row("national", "2101", rp) for rp in (100, 200, 500)]
    res2 = fd.classes_at_points(_PT, all_rp, _RaisingCidx({ok["path"]: "N331"}, {bad["path"]}))  # type: ignore[arg-type,dict-item]
    assert [res2["codes"][k][0] for k in range(3)] == ["N331"] * 3 and res2["reason"] == [None]


def test_file_present_by_name_but_missing_from_index_is_unreadable_not_missing() -> None:
    rows = [_fake_row("national", "2101", 100)]
    cidx = _FakeCidx({rows[0]["path"]: "N331"})  # type: ignore[dict-item]
    named = {("national", "2101", 100), ("national", "2101", 200)}  # 200 exists, not indexed
    res = fd.classes_at_points(_PT, rows, cidx, named_files=named)  # type: ignore[arg-type]
    assert [res["codes"][k][0] for k in range(3)] == ["N331", None, None]
    assert res["reason"] == ["map_unreadable:200;map_unreadable:500"]
    # without the name list the same point is "no file" (genuinely absent)
    res2 = fd.classes_at_points(_PT, rows, cidx)  # type: ignore[arg-type]
    assert res2["reason"] == ["no_file_for_rp:200;no_file_for_rp:500"]


def test_map_index_leaves_out_unreadable_zip_and_is_not_cached(tmp_path: Path) -> None:
    d = tmp_path / "유역별_100년_빈도_도시침수지도"
    d.mkdir()
    good = _zip_map(d, "CFM_SBSN_2101_100", {"N331": box(0, 0, 10, 10)})
    (d / "CFM_SBSN_2102_100.zip").write_bytes(b"not a zip")
    cache = tmp_path / "cache" / "cfm.json"
    idx = fd.map_index(tmp_path, cache, families=fd.URBAN_FAMILIES)
    assert [r["path"] for r in idx] == [str(good)]
    assert not cache.exists()  # incomplete index is never cached


def test_unreadable_urban_zip_gives_nan_for_points_outside_indexed_bounds(
    tmp_path: Path,
) -> None:
    from pyproj import Transformer

    d = tmp_path / "유역별_100년_빈도_도시침수지도"
    d.mkdir()
    good = _zip_map(d, "CFM_SBSN_2101_100", {"N331": box(0, 0, 1000, 1000)})
    (d / "CFM_SBSN_2102_100.zip").write_bytes(b"not a zip")
    idx = fd.map_index(tmp_path, families=fd.URBAN_FAMILIES)
    to_ll = Transformer.from_crs(5186, 4326, always_xy=True)
    pts = [to_ll.transform(500, 500), to_ll.transform(50000, 50000)]
    named = fd.named_files(tmp_path, fd.URBAN_FAMILIES)
    res = fd.classes_at_points(pts, idx, fd.ClassIndex(), named_files=named)
    assert res["codes"][0][0] == "N331" and res["reason"][0] is None
    assert res["codes"][0][1] is None and "map_unreadable:100" in res["reason"][1]
    assert str(good) in {r["path"] for r in idx}


def test_map_index_cache_with_a_missing_name_rebuilds(tmp_path: Path) -> None:
    d = tmp_path / "유역별_100년_빈도_도시침수지도"
    d.mkdir()
    a = _zip_map(d, "CFM_SBSN_2101_100", {"N331": box(0, 0, 10, 10)})
    cache = tmp_path / "cache" / "cfm.json"
    assert len(fd.map_index(tmp_path, cache, families=fd.URBAN_FAMILIES)) == 1
    b = _zip_map(d, "CFM_SBSN_2102_100", {"N331": box(20, 20, 30, 30)})
    idx = fd.map_index(tmp_path, cache, families=fd.URBAN_FAMILIES)
    assert {r["path"] for r in idx} == {str(a), str(b)}


# --------------------------------------------------------------------------- #
# Fix wave 2026-11-16 (Fable deep check): unknown never becomes 0 m              #
# --------------------------------------------------------------------------- #
NAT = fd.FAMILIES["national"]
LOC = fd.FAMILIES["local"][100]
URB = fd.URBAN_FAMILIES["urban"][100]


def _ll(x: float, y: float) -> tuple[float, float]:
    from pyproj import Transformer

    lon, lat = Transformer.from_crs(5186, 4326, always_xy=True).transform(x, y)
    return float(lon), float(lat)


def test_named_files_and_coverage_table_need_the_family_set(tmp_path: Path) -> None:
    # the default used to include the urban maps: a river caller then counted every urban
    # zip as an unindexed river file and every river point became map_unreadable
    with pytest.raises(TypeError):
        fd.named_files(tmp_path)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        fd.coverage_table(tmp_path)  # type: ignore[call-arg]
    (tmp_path / URB).mkdir()
    (tmp_path / URB / "CFM_SBSN_2101_100.zip").write_bytes(b"")
    assert fd.named_files(tmp_path, fd.FAMILIES) == set()
    assert fd.named_files(tmp_path, fd.URBAN_FAMILIES) == {("urban", "2101", 100)}


@pytest.mark.parametrize(
    ("folder", "families", "family"),
    [(LOC, fd.FAMILIES, "local"), (URB, fd.URBAN_FAMILIES, "urban")],
)
def test_unindexed_zip_makes_a_point_in_a_readable_bbox_but_outside_its_polygons_unknown(
    tmp_path: Path, folder: str, families: dict[str, dict[int, str]], family: str
) -> None:
    d = tmp_path / folder
    d.mkdir()
    prefix = "RFM_SBSN_RGN" if family == "local" else "CFM_SBSN"
    # a readable file whose bbox spans the point but whose polygons are far from it
    _zip_map(
        d,
        f"{prefix}_9009_100",
        {"N330": box(190000, 490000, 190010, 490010), "N331": box(290000, 560000, 290010, 560010)},
    )
    (d / f"{prefix}_9006_100.zip").write_bytes(b"not a zip")  # the one that may cover it
    idx = fd.map_index(tmp_path, families=families)
    named = fd.named_files(tmp_path, families)
    pts = [_ll(255500, 500500), _ll(190005, 490005)]
    res = fd.classes_at_points(pts, idx, fd.ClassIndex(), named_files=named)
    assert res["codes"][0][0] is None  # was "" (0 m) from the bbox hit
    assert "map_unreadable:100" in res["reason"][0]
    assert res["codes"][0][1] == "N330" and res["reason"][1] is None  # a polygon hit keeps it


def test_missing_folders_names_absent_and_empty_family_folders(tmp_path: Path) -> None:
    assert sorted(fd.missing_folders(tmp_path, fd.FAMILIES)) == sorted([*NAT.values(), LOC])
    for folder in [*NAT.values(), LOC]:
        (tmp_path / folder).mkdir()
    (tmp_path / LOC / "RFM_SBSN_RGN_1001_100.zip").write_bytes(b"")
    for folder in NAT.values():
        (tmp_path / folder / "RFM_SBSN_NTN_1001_100.zip").write_bytes(b"")
    assert fd.missing_folders(tmp_path, fd.FAMILIES) == []
    (tmp_path / NAT[500] / "RFM_SBSN_NTN_1001_100.zip").unlink()  # partial sync: empty folder
    assert fd.missing_folders(tmp_path, fd.FAMILIES) == [NAT[500]]
    assert fd.missing_folders(tmp_path, fd.URBAN_FAMILIES) == [URB]


def test_map_index_never_writes_an_empty_cache(tmp_path: Path) -> None:
    for folder in NAT.values():
        (tmp_path / folder).mkdir()
    cache = tmp_path / "cache" / "rfm.json"
    assert fd.map_index(tmp_path, cache) == []
    assert not cache.exists()


def test_truncated_shapefile_is_unreadable_not_dry(tmp_path: Path) -> None:
    d = tmp_path / NAT[100]
    d.mkdir()
    zp = _zip_map(d, "RFM_SBSN_NTN_9004_100", {"N333": box(240000, 500000, 241000, 501000)})
    with zipfile.ZipFile(zp) as z:
        items = {n: z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(zp, "w") as z:  # valid header, zero records
        for n, b in items.items():
            z.writestr(n, b[:100] if n.endswith(".shp") else b)
    idx = fd.map_index(tmp_path)
    assert len(idx) == 1  # the header still gives the bounds
    res = fd.classes_at_points([_ll(240500, 500500)], idx, fd.ClassIndex())
    assert res["codes"][0] == [None]
    assert res["reason"][0].startswith("map_unreadable:100")


def test_degenerate_header_bounds_is_an_empty_file_not_an_existing_one(
    tmp_path: Path,
) -> None:
    gpd = pytest.importorskip("geopandas")
    for rp in (100, 500):
        (tmp_path / NAT[rp]).mkdir()
        _zip_map(
            tmp_path / NAT[rp],
            f"RFM_SBSN_NTN_1202_{rp}",
            {"N331": box(200000, 500000, 201000, 501000)},
        )
    d200 = tmp_path / NAT[200]
    d200.mkdir()
    work = tmp_path / "_empty"
    work.mkdir()
    gpd.GeoDataFrame({"SEG_CODE": []}, geometry=[], crs=5186).to_file(
        work / "RFM_SBSN_NTN_1202_200.shp"
    )
    with zipfile.ZipFile(d200 / "RFM_SBSN_NTN_1202_200.zip", "w") as z:
        for f in work.iterdir():
            z.write(f, f.name)
    cache = tmp_path / "cache" / "rfm.json"
    idx = fd.map_index(tmp_path, cache)
    empty = [r for r in idx if r.get("empty")]
    assert [Path(r["path"]).name for r in empty] == ["RFM_SBSN_NTN_1202_200.zip"]
    assert ("national", "1202", 200) not in fd.available_files(idx)
    named = fd.named_files(tmp_path, fd.FAMILIES)
    res = fd.classes_at_points(
        [_ll(200500, 500500), _ll(300500, 600500)], idx, fd.ClassIndex(), named_files=named
    )
    # inside the basin's 100-y polygon: the 200-y file holds nothing, so 200 is unknown
    assert [res["codes"][k][0] for k in range(3)] == ["N331", None, "N331"]
    assert res["reason"][0] == "no_file_for_rp:200"
    # outside every polygon: still 0 m, the empty file is not an unknown-extent file
    assert [res["codes"][k][1] for k in range(3)] == ["", "", ""]
    assert res["reason"][1] is None


def _write_n100(tmp_path: Path, name: str, poly: object) -> Path:
    d = tmp_path / NAT[100]
    d.mkdir(exist_ok=True)
    work = tmp_path / "_w" / name
    if work.exists():
        import shutil

        shutil.rmtree(work)
    work.mkdir(parents=True)
    gpd = pytest.importorskip("geopandas")
    gpd.GeoDataFrame({"SEG_CODE": ["N331"]}, geometry=[poly], crs=5186).to_file(
        work / f"{name}.shp"
    )
    zp = d / f"{name}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in work.iterdir():
            z.write(f, f.name)
    return zp


def test_map_index_cache_records_size_and_mtime_and_rebuilds_a_replaced_zip(
    tmp_path: Path,
) -> None:
    import os

    cache = tmp_path / "cache" / "rfm.json"
    zp = _write_n100(tmp_path, "RFM_SBSN_NTN_9001_100", box(200000, 500000, 201000, 501000))
    idx = fd.map_index(tmp_path, cache)
    st = zp.stat()
    assert idx[0]["size"] == st.st_size and idx[0]["mtime_ns"] == st.st_mtime_ns
    import json

    assert json.loads(cache.read_text())[0]["mtime_ns"] == st.st_mtime_ns
    # replaced under the same name: new extent
    _write_n100(tmp_path, "RFM_SBSN_NTN_9001_100", box(300000, 500000, 301000, 501000))
    os.utime(zp, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    idx2 = fd.map_index(tmp_path, cache)
    assert idx2[0]["bounds"][0] == pytest.approx(300000.0)


def test_old_format_cache_is_migrated_by_stat_without_opening_any_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # coordinator ruling 2026-11-16: an older cache (no size / mtime_ns) is migrated by stat
    # only — a full rebuild would make macOS download every iCloud placeholder
    import json

    cache = tmp_path / "cache" / "rfm.json"
    a = _write_n100(tmp_path, "RFM_SBSN_NTN_9001_100", box(300000, 500000, 301000, 501000))
    b = _write_n100(tmp_path, "RFM_SBSN_NTN_9002_100", box(310000, 500000, 311000, 501000))
    cache.parent.mkdir(parents=True)
    old = [
        {"path": str(a), "family": "national", "rp": 100, "bounds": [1.0, 2.0, 3.0, 4.0]},
        {"path": str(b), "family": "national", "rp": 100, "bounds": [0.0, 0.0, 0.0, 0.0]},
    ]
    cache.write_text(json.dumps(old))

    def _never(zp: Path) -> None:
        raise AssertionError(f"opened {zp.name}")

    monkeypatch.setattr(fd, "_shp_bounds", _never)
    with caplog.at_level("INFO", logger=fd.log.name):
        idx = {r["path"]: r for r in fd.map_index(tmp_path, cache)}
    assert idx[str(a)]["bounds"] == [1.0, 2.0, 3.0, 4.0]  # kept, not re-read
    assert idx[str(a)]["size"] == a.stat().st_size
    assert idx[str(a)]["mtime_ns"] == a.stat().st_mtime_ns
    assert idx[str(b)]["empty"] is True  # degenerate bounds, judged from the cached box
    written = json.loads(cache.read_text())
    assert all("size" in r and "mtime_ns" in r for r in written) and len(written) == 2
    assert any("migrated 2 rows" in r.getMessage() for r in caplog.records)
    # a second run reuses the migrated cache as is
    assert {r["path"] for r in fd.map_index(tmp_path, cache)} == {str(a), str(b)}


def test_old_format_cache_reads_only_the_new_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    cache = tmp_path / "cache" / "rfm.json"
    a = _write_n100(tmp_path, "RFM_SBSN_NTN_9001_100", box(300000, 500000, 301000, 501000))
    gone = str(tmp_path / NAT[100] / "RFM_SBSN_NTN_9009_100.zip")
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            [
                {"path": str(a), "family": "national", "rp": 100, "bounds": [1.0, 2.0, 3.0, 4.0]},
                {"path": gone, "family": "national", "rp": 100, "bounds": [5.0, 6.0, 7.0, 8.0]},
            ]
        )
    )
    new = _write_n100(tmp_path, "RFM_SBSN_NTN_9002_100", box(310000, 500000, 311000, 501000))
    real = fd._shp_bounds
    opened: list[str] = []

    def _spy(zp: Path) -> tuple[float, float, float, float] | None:
        opened.append(zp.name)
        return real(zp)

    monkeypatch.setattr(fd, "_shp_bounds", _spy)
    idx = {r["path"]: r for r in fd.map_index(tmp_path, cache)}
    assert opened == [new.name]  # only the file the old cache did not list
    assert idx[str(a)]["bounds"] == [1.0, 2.0, 3.0, 4.0]
    assert idx[str(new)]["bounds"][0] == pytest.approx(310000.0)
    assert gone not in idx  # a file no longer on disk drops out
    assert len(json.loads(cache.read_text())) == 2


def test_unchanged_rows_are_reused_and_an_unreadable_one_keeps_its_last_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache" / "rfm.json"
    a = _write_n100(tmp_path, "RFM_SBSN_NTN_9001_100", box(200000, 500000, 201000, 501000))
    b = _write_n100(tmp_path, "RFM_SBSN_NTN_9002_100", box(210000, 500000, 211000, 501000))
    first = {r["path"]: r for r in fd.map_index(tmp_path, cache)}
    # a new file appears (cache no longer matches); offline every header read now fails
    (tmp_path / NAT[100] / "RFM_SBSN_NTN_9003_100.zip").write_bytes(b"not a zip")
    import os

    st = b.stat()
    os.utime(b, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # b changed on disk

    def _fail(zp: Path) -> None:
        raise OSError("offline dataless")

    monkeypatch.setattr(fd, "_shp_bounds", _fail)
    idx = {r["path"]: r for r in fd.map_index(tmp_path, cache)}
    assert idx[str(a)]["bounds"] == first[str(a)]["bounds"]  # unchanged: reused, not opened
    assert not idx[str(a)].get("last_known_bounds")
    assert idx[str(b)]["bounds"] == first[str(b)]["bounds"]  # unreadable now: last-known
    assert idx[str(b)]["last_known_bounds"] is True
    assert len(idx) == 2  # 9003 has no bounds at all: unindexed
    import json

    assert set(json.loads(cache.read_text())[0]) >= {"size", "mtime_ns"}
    assert len(json.loads(cache.read_text())) == 2  # the old complete cache is not overwritten


def test_reasons_cover_only_the_requested_return_periods() -> None:
    rows = [
        {
            "path": "/maps/CFM_SBSN_2101_100.zip",
            "family": "urban",
            "rp": 100,
            "bounds": (-1e12, -1e12, 1e12, 1e12),
        }
    ]
    named = {("urban", "2101", 100), ("urban", "2102", 100)}
    res = fd.classes_at_points(
        _PT,
        rows,
        _FakeCidx({}),
        named_files=named,
        return_periods=(100,),  # type: ignore[arg-type]
    )
    assert res["rps"] == [100] and len(res["codes"]) == 1
    assert res["reason"] == ["map_unreadable:100"]


def test_check_summary_with_too_few_points_gives_no_verdict() -> None:
    s = fd.check_summary([("N333", 0.1, "dem")] * 5, min_n=20)
    assert s["warn"] is None and s["note"] == "not enough points (n=5)"
    assert fd.check_summary([])["warn"] is None
    from climaterisk.physical_risk.method_registry import _state_of

    check = {"status": "built", "diagnostics_key": "sub_check"}
    assert _state_of(check, {"sub_check": fd.check_summary([])}) == (
        "skipped",
        "not enough points (n=0)",
    )
