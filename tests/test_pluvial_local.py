"""Gate tests: Korea-local urban pluvial flood (도시침수, method tier T2, hazard-only).

User decision 2026-11-15 ("깊이만, 돈은 나중에"): a new Models-tab hazard key ``PF``.

* **Main (작성):** the 환경부 홍수위험지도 100-y 도시침수지도 (CFM_SBSN_<basin>_100.zip) official
  depth class at each asset, read with the river machinery in ``floodmap_depth``. The class
  is reported as the band's representative depth (m). Its SEG_CODE N330–N334 ↔ band order
  is taken from the river maps (inferred, not officially confirmed). One event, 100 y.
* **Sub check (검토, diagnostic only):** DEM edge depth against the band, the river check.
* **Validation (승인, diagnostic only):** 서울시 침수흔적도 2010–2025 (공공누리 제1유형): per
  asset, the years whose observed flood polygon lies within a small buffer and the deepest
  observed depth (``F_SHIM``, m). Only assets inside the record's coverage (서울특별시) count.
* **No loss is computed:** rows are HAZARD_ONLY. Global and Data API cells: NO_HAZARD_DATA.
* The CFM polygons are 공공누리 제4유형 like the river maps: overlaid in memory only, never
  rasterised or written (tests/test_no_floodmap_geometry.py).

These run in the CLIMADA worker environment. Nothing here reads the real maps or the network.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker import _paths  # noqa: E402
from climaterisk_worker import flood_traces as ft  # noqa: E402
from climaterisk_worker import floodmap_depth as fd  # noqa: E402
from climaterisk_worker.physical_risk import adapters, engine  # noqa: E402
from pyproj import Transformer  # noqa: E402
from scipy import sparse  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

from climaterisk.physical_risk import display_copy, models  # noqa: E402

# Two Seoul points (lat, lon) and one Busan point: A inside the synthetic CFM N332 polygon,
# B dry; C outside 서울특별시 (outside the trace record's coverage).
A = (37.5000, 127.0000)
B = (37.5200, 127.0300)
C = (35.1000, 129.0000)
CFM_DIR = "유역별_100년_빈도_도시침수지도"
RFM_DIR = "유역별_100년_빈도_국가하천_하천범람지도"


def _to(epsg: int, lat: float, lon: float) -> tuple[float, float]:
    x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    return float(x), float(y)


def _zip_shapefile(gdf: gpd.GeoDataFrame, zpath: Path) -> None:
    """Write ``gdf`` as <stem>.shp and pack the sidecars into ``zpath`` (the map layout)."""
    work = zpath.parent / f"_{zpath.stem}"
    work.mkdir(parents=True)
    gdf.to_file(work / f"{zpath.stem}.shp")
    with zipfile.ZipFile(zpath, "w") as z:
        for f in sorted(work.iterdir()):
            z.write(f, f.name)


@pytest.fixture
def map_root(tmp_path: Path) -> Path:
    """A CFM file (N332 square around A) and a river file (N334 square around B)."""
    root = tmp_path / "floodmap"
    (root / CFM_DIR).mkdir(parents=True)
    (root / RFM_DIR).mkdir(parents=True)
    ax, ay = _to(5186, *A)
    bx, by = _to(5186, *B)
    cfm = gpd.GeoDataFrame(
        {"SEG_CODE": ["N332"], "FLDLV_FREQ": [100], "SBSN_CD": ["9999"]},
        geometry=[box(ax - 50, ay - 50, ax + 50, ay + 50)],
        crs=5186,
    )
    rfm = gpd.GeoDataFrame(
        {"SEG_CODE": ["N334"]}, geometry=[box(bx - 50, by - 50, bx + 50, by + 50)], crs=5186
    )
    _zip_shapefile(cfm, root / CFM_DIR / "CFM_SBSN_9999_100.zip")
    _zip_shapefile(rfm, root / RFM_DIR / "RFM_SBSN_NTN_9999_100.zip")
    return root


@pytest.fixture
def trace_root(tmp_path: Path) -> Path:
    """서울시 침수흔적도-like folders: 2010 and 2022 polygons at A (EPSG:5179, F_SHIM in m)."""
    root = tmp_path / "seoul_flood_traces"
    ax, ay = _to(5179, *A)
    for year, depth, off in ((2010, 0.3, 10.0), (2022, 0.5, 0.0), (2018, 0.1, 400.0)):
        d = root / str(year)
        d.mkdir(parents=True)
        g = gpd.GeoDataFrame(
            {"F_SHIM": [depth], "F_YR": [str(year)]},
            geometry=[box(ax + off - 5, ay - 5, ax + off + 5, ay + 5)],
            crs=5179,
        )
        g.to_file(d / f"{year} 서울시 침수흔적도.shp")
    return root


SEOUL_BOX = box(126.76, 37.42, 127.19, 37.70)  # lon/lat coverage for the synthetic record


# --------------------------------------------------------------------------- #
# floodmap_depth: the urban map family, indexed apart from the river maps       #
# --------------------------------------------------------------------------- #
def test_urban_family_names_the_cfm_folder_only() -> None:
    assert fd.URBAN_FAMILIES == {"urban": {100: CFM_DIR}}
    assert all(CFM_DIR not in folder for fam in fd.FAMILIES.values() for folder in fam.values())


def test_map_index_reads_one_family_set_at_a_time(map_root: Path) -> None:
    river = fd.map_index(map_root)
    urban = fd.map_index(map_root, families=fd.URBAN_FAMILIES)
    assert [Path(r["path"]).name for r in river] == ["RFM_SBSN_NTN_9999_100.zip"]
    assert [Path(r["path"]).name for r in urban] == ["CFM_SBSN_9999_100.zip"]
    assert urban[0]["family"] == "urban" and urban[0]["rp"] == 100


def test_urban_class_at_points_reads_the_cfm_polygon(map_root: Path) -> None:
    index = fd.map_index(map_root, families=fd.URBAN_FAMILIES)
    pts = [(A[1], A[0]), (B[1], B[0])]  # lon, lat
    res = fd.classes_at_points(pts, index, fd.ClassIndex())
    assert res["codes"][0] == ["N332", ""]  # B is wet only on the river map
    dep = fd.class_depths([res["codes"][0]], "mid", 5.0)["depth"][0]
    np.testing.assert_allclose(dep, [1.5, 0.0], rtol=0, atol=1e-12)


# --------------------------------------------------------------------------- #
# flood_traces: observed floods per asset (validation, diagnostic only)          #
# --------------------------------------------------------------------------- #
def test_observed_at_points_years_depth_and_coverage(trace_root: Path) -> None:
    obs = ft.observed_at_points(
        [(A[1], A[0]), (B[1], B[0]), (C[1], C[0])], trace_root, coverage=SEOUL_BOX, buffer_m=20.0
    )
    a, b, c = obs
    # 2010 polygon is 5 m away (inside the 20 m buffer), 2022 covers A, 2018 is 395 m away
    assert a["covered"] is True and a["years"] == [2010, 2022]
    assert a["max_depth_m"] == pytest.approx(0.5) and a["n_traces"] == 2
    assert b["covered"] is True and b["years"] == [] and b["max_depth_m"] is None
    assert c["covered"] is False and c["years"] == [] and c["max_depth_m"] is None


def test_observed_buffer_zero_needs_the_polygon_to_touch_the_point(trace_root: Path) -> None:
    (a,) = ft.observed_at_points([(A[1], A[0])], trace_root, coverage=SEOUL_BOX, buffer_m=0.0)
    assert a["years"] == [2022]


def test_observed_without_coverage_counts_every_point_as_covered(trace_root: Path) -> None:
    (c,) = ft.observed_at_points([(C[1], C[0])], trace_root, coverage=None, buffer_m=20.0)
    assert c["covered"] is True and c["years"] == []


def test_validation_summary_counts_only_covered_assets() -> None:
    observed = [
        {"covered": True, "years": [2010], "max_depth_m": 0.3, "n_traces": 1},  # hit
        {"covered": True, "years": [2022], "max_depth_m": 0.5, "n_traces": 1},  # missed
        {"covered": True, "years": [], "max_depth_m": None, "n_traces": 0},  # mapped only
        {"covered": True, "years": [], "max_depth_m": None, "n_traces": 0},  # dry, dry
        {"covered": False, "years": [], "max_depth_m": None, "n_traces": 0},  # left out
    ]
    s = ft.validation_summary([True, False, True, False, True], observed)
    assert s["n_covered"] == 4 and s["n_observed"] == 2 and s["n_mapped_wet"] == 2
    assert s["n_hit"] == 1
    assert s["pod"] == pytest.approx(0.5)  # observed assets the map also floods
    assert s["share_mapped_observed"] == pytest.approx(0.5)
    assert "diagnostic" in s["note"].lower()
    assert "warn" not in s  # validation reports; it never issues a verdict


def test_validation_summary_none_when_nothing_to_compare() -> None:
    s = ft.validation_summary([False], [{"covered": False, "years": [], "max_depth_m": None}])
    assert s["n_covered"] == 0 and s["pod"] is None and s["share_mapped_observed"] is None


# --------------------------------------------------------------------------- #
# Readiness, adapters, engine                                                   #
# --------------------------------------------------------------------------- #
def test_pf_is_a_models_tab_key_with_its_own_tag() -> None:
    assert "PF" in models.HAZARD_KEYS
    assert models.HAZARD_TAGS["PF"] == ("PF",)
    cells = models.READINESS["PF"]
    assert cells["GLOBAL_BASELINE"]["status"] == "NO_HAZARD_DATA"
    assert cells["DATA_API_COUNTRY"]["status"] == "NO_HAZARD_DATA"
    assert cells["KOREA_LOCAL"]["status"] == "HAZARD_ONLY"
    assert "도시침수" in cells["KOREA_LOCAL"]["hazard_source"]
    assert display_copy.recommended_models(["PF"]) == {"PF": "KOREA_LOCAL"}
    assert "pluvial" in display_copy.HAZARD_LABEL["PF"].lower()


def test_pf_adapters_match_the_readiness_table() -> None:
    assert adapters.readiness_matches_backend()
    g = adapters.adapter_for("PF", "GLOBAL_BASELINE", "rcp85", 2050, "KOR")
    c = adapters.adapter_for("PF", "DATA_API_COUNTRY", "rcp85", 2050, "KOR")
    assert g.status == c.status == "NO_HAZARD_DATA"
    assert g.load(points=[A]) is None and c.load(points=[A]) is None


def test_korea_local_pluvial_describes_present_day_hazard_only() -> None:
    d = adapters.adapter_for("PF", "KOREA_LOCAL", "rcp85", 2050, "KOR").describe()
    assert d.status == "HAZARD_ONLY" and d.hazard_type == "PF"
    assert d.hazard_dataset == "floodmap_cfm_depth_class"
    assert d.served_scenario == "historical" and d.time_horizon == "present"
    text = f"{d.hazard_source} {d.detail}"
    assert "도시침수" in text and "100" in text
    assert "no loss computed" in text and "침수흔적도" in text


def test_korea_local_pluvial_load_builds_one_100y_depth_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, map_root: Path, trace_root: Path
) -> None:
    sgis = tmp_path / "external" / "municipality" / "sgis"
    sgis.mkdir(parents=True)
    gpd.GeoDataFrame(
        {"SIDO_CD": ["11"], "SIDO_NM": ["서울특별시"]}, geometry=[SEOUL_BOX], crs=4326
    ).to_crs(5179).to_file(sgis / "bnd_sido_00_2025_2Q.shp")
    ext = {"seoul_flood_traces": trace_root}
    monkeypatch.setattr(_paths.paths, "floodmap_dir", lambda: map_root)
    monkeypatch.setattr(_paths.paths, "cache_dir", lambda *p: tmp_path.joinpath("cache", *p))
    monkeypatch.setattr(
        _paths.paths,
        "external_dir",
        lambda *p: ext[p[0]] if p and p[0] in ext else tmp_path.joinpath("external", *p),
    )
    monkeypatch.setenv("CLIMATERISK_OFFLINE", "1")  # the DEM sub check may fail; never raise

    ad = adapters.adapter_for("PF", "KOREA_LOCAL", "rcp85", 2050, "KOR")
    haz = ad.load(points=[A, B, C])
    assert haz.haz_type == "PF" and haz.units == "m"
    assert list(haz.event_name) == ["rp100"]
    np.testing.assert_allclose(haz.frequency, [0.01], rtol=1e-12)
    np.testing.assert_allclose(haz.intensity.toarray()[0], [1.5, 0.0, 0.0], atol=1e-12)
    diag = ad.diagnostics
    assert diag["classes"]["rp100"] == ["N332", "", ""]
    assert "sub_check" in diag and "warn" in diag["sub_check"]  # reported, maybe an error
    val = diag["validation"]
    assert val["n_covered"] == 2 and val["n_observed"] == 1 and val["n_hit"] == 1
    assert diag["observed"][0]["years"] == [2010, 2022]
    assert diag["observed"][2]["covered"] is False


def test_hazard_only_pf_row_keeps_depth_and_nulls_money() -> None:
    h = Hazard(
        haz_type="PF",
        units="m",
        centroids=Centroids(lat=np.array([A[0]]), lon=np.array([A[1]])),
        event_id=np.array([1]),
        event_name=["rp100"],
        date=np.array([0]),
        frequency=np.array([0.01]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix([[1.5]]),
        fraction=sparse.csr_matrix([[1.0]]),
    )
    fac: dict[str, Any] = {
        "facility_id": "X",
        "facility_name": "x",
        "latitude": A[0],
        "longitude": A[1],
        "asset_value_usd": 1e6,
        "asset_value_currency": "USD",
        "property_type": "office",
    }
    row = engine.calculate(
        fac, h, "PF", model_id="KOREA_LOCAL", hazard_only=True, hazard_only_detail="no loss"
    )
    assert row.calculation_status == "HAZARD_ONLY"
    assert row.hazard_intensity == pytest.approx(1.5) and row.hazard_intensity_unit == "m"
    assert row.eal_usd is None and row.potential_loss_usd is None


def test_models_view_lists_pf() -> None:
    tsx = (ROOT / "frontend/climaterisk/src/views/ModelsView.tsx").read_text(encoding="utf-8")
    line = next(ln for ln in tsx.splitlines() if ln.startswith("const HAZARD_KEYS"))
    assert '"PF"' in line


def test_point_helper_is_lon_lat() -> None:
    """Guard on this file's own convention: ``observed_at_points`` takes (lon, lat)."""
    assert SEOUL_BOX.contains(Point(A[1], A[0]))
