"""End to end: synthetic 홍수위험지도 zips → Korea-local adapters → rows, totals, 단면 sheet.

Tiny fake map zips are built in ``tmp_path`` with the real folder names and EPSG:5186, and the
data root is pointed there (offline). Nothing reads the real licensed maps or the network.
These tests cannot be skipped by a missing data folder: they build their own.

Points (map CRS x, y = 500 500 m unless noted), one per case:

* ``A`` — national basin 9001 complete (100/200/500 y: N331 / N332 / N333); an urban
  (도시침수) file also covers it, which must not touch the river result;
* ``B`` — national basin 9002 with no 200-y file → NaN at 200 y (``no_file_for_rp:200``);
* ``D`` — national basin 9004 whose ``.shp`` is truncated (header only) → ``map_unreadable``;
* ``E`` — local (지방하천) 9005 only → 100-y class used at 200 / 500 y, a lower bound;
* ``O`` — outside every polygon → 0 m, priced at zero.

Needs CLIMADA (the worker environment).
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
from climaterisk_worker import floodmap_depth as fd  # noqa: E402
from climaterisk_worker import ingest  # noqa: E402
from climaterisk_worker.physical_risk import adapters, runner  # noqa: E402
from climaterisk_worker.physical_risk.registry import flood_impact_function  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import box  # noqa: E402

from climaterisk import paths  # noqa: E402

NAT = {rp: fd.FAMILIES["national"][rp] for rp in (100, 200, 500)}
LOC = fd.FAMILIES["local"][100]
URB = fd.URBAN_FAMILIES["urban"][100]
X = {"A": 200000, "B": 210000, "C": 220000, "D": 240000, "E": 250000, "O": 280000}
VALUE = 1_000_000.0


def _sq(x: float, y: float = 500000, w: float = 1000) -> Any:
    return box(x, y, x + w, y + w)


def _zmap(root: Path, folder: str, name: str, polys: list[tuple[str, Any]]) -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    work = root.parent / "_build" / name
    work.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(
        {"SEG_CODE": [c for c, _ in polys]}, geometry=[g for _, g in polys], crs=5186
    ).to_file(work / f"{name}.shp")
    zp = d / f"{name}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in sorted(work.iterdir()):
            z.write(f, f.name)
    return zp


def _truncate_shp(zp: Path) -> None:
    """Keep the 100-byte .shp header (valid bounds) and drop every record."""
    with zipfile.ZipFile(zp) as z:
        items = {n: z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(zp, "w") as z:
        for n, b in items.items():
            z.writestr(n, b[:100] if n.endswith(".shp") else b)


def _build_maps(data_root: Path, *, with_river: bool = True, with_urban: bool = True) -> Path:
    root = data_root / "external" / "other" / "floodmap"
    root.mkdir(parents=True, exist_ok=True)
    if with_river:
        for rp, code in ((100, "N331"), (200, "N332"), (500, "N333")):
            _zmap(root, NAT[rp], f"RFM_SBSN_NTN_9001_{rp}", [(code, _sq(X["A"]))])
        _zmap(root, NAT[100], "RFM_SBSN_NTN_9002_100", [("N331", _sq(X["B"]))])
        _zmap(root, NAT[500], "RFM_SBSN_NTN_9002_500", [("N333", _sq(X["B"]))])
        for rp in (100, 200, 500):
            _truncate_shp(_zmap(root, NAT[rp], f"RFM_SBSN_NTN_9004_{rp}", [("N333", _sq(X["D"]))]))
        _zmap(root, LOC, "RFM_SBSN_RGN_9005_100", [("N332", _sq(X["E"]))])
    if with_urban:
        _zmap(root, URB, "CFM_SBSN_9101_100", [("N330", _sq(X["A"]))])
    return root


def _facilities(keys: str = "ABDEO") -> list[dict[str, Any]]:
    to_ll = Transformer.from_crs(5186, 4326, always_xy=True)
    out = []
    for k in keys:
        lon, lat = to_ll.transform(X[k] + 500, 500500)
        out.append(
            {
                "facility_id": k,
                "facility_name": k,
                "latitude": float(lat),
                "longitude": float(lon),
                "asset_value_usd": VALUE,
                "asset_value_currency": "USD",
                "property_type": "residential",
                "assessment_target": "FACILITY",
            }
        )
    return out


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "dataroot"
    root.mkdir()
    monkeypatch.setenv("CLIMATERISK_DATA_ROOT", str(root))
    monkeypatch.setenv("CLIMATERISK_OFFLINE", "1")
    monkeypatch.setattr(adapters.KoreaLocalFloodAdapter, "_map_memo", None)
    monkeypatch.setattr(adapters.KoreaLocalPluvialAdapter, "_memo", None)
    return root


def _expected_eal(depths_m: list[float]) -> float:
    """EAL by hand: incremental frequencies × JRC Asia residential MDD·PAA × value.

    Algorithm:
        $$\\mathrm{EAL} = V \\sum_k \\Delta f_k\\, \\mathrm{MDD}(h_k)\\, \\mathrm{PAA}(h_k)$$
        ASCII: EAL = value * sum_k df_k * mdd(h_k) * paa(h_k), df = incremental frequency
    """
    func = flood_impact_function("Asia", "residential")
    x = np.asarray(func.intensity, dtype=float)
    mdr = np.interp(depths_m, x, np.asarray(func.mdd) * np.asarray(func.paa))
    df = ingest._incremental_frequency([100, 200, 500])
    return float(VALUE * np.sum(df * mdr))


def _rows(tag: str, facilities: list[dict[str, Any]]) -> tuple[list[Any], dict[str, Any], list]:
    adapter = adapters.adapter_for(tag, "KOREA_LOCAL", "historical", 2030, "KOR")
    curves: list[dict[str, Any]] = []
    rows, record = runner._rows_for(
        adapter, tag, facilities, "KOR", runner._bbox(facilities), curves
    )
    return rows, record, curves


# --------------------------------------------------------------------------- #
# 7a. the whole path, per asset and for the portfolio                           #
# --------------------------------------------------------------------------- #
def test_river_flood_end_to_end_per_asset_and_portfolio(data_root: Path) -> None:
    _build_maps(data_root)
    rows, record, curves = _rows("RF", _facilities())
    by = {r.facility_id: r for r in rows}

    a = by["A"]  # national complete: 0.75 / 1.5 / 3.5 m (band midpoints)
    assert a.calculation_status == "FULL" and a.unavailable_reason is None
    assert a.hazard_intensity == pytest.approx(3.5)
    np.testing.assert_allclose(a.eal_usd, _expected_eal([0.75, 1.5, 3.5]), rtol=1e-9)

    b = by["B"]  # basin 9002 has no 200-y file: NaN at 200, never borrowed
    assert b.calculation_status == "NO_HAZARD_DATA" and b.eal_usd is None
    assert b.unavailable_reason == "no_file_for_rp:200"

    d = by["D"]  # truncated .shp: unreadable, never dry
    assert d.calculation_status == "NO_HAZARD_DATA" and d.eal_usd is None
    assert d.unavailable_reason is not None and d.unavailable_reason.startswith("map_unreadable")

    e = by["E"]  # local 100-y map only: 1.5 m at every return period, a lower bound
    assert e.calculation_status == "FULL" and e.unavailable_reason is None
    assert e.hazard_intensity == pytest.approx(1.5)
    np.testing.assert_allclose(e.eal_usd, _expected_eal([1.5, 1.5, 1.5]), rtol=1e-9)
    assert "lower bound" in (e.confidence or "") and "lower bound" in (e.status_detail or "")
    assert "lower bound" not in (a.confidence or "")

    o = by["O"]  # outside every polygon: 0 m, priced at zero
    assert o.calculation_status == "FULL" and o.unavailable_reason is None
    assert o.hazard_intensity == 0.0 and o.eal_usd == 0.0

    assert record.get("load_error") is None
    # portfolio: priced assets only (A, E, O), the NaN ones are counted, never summed as 0
    from climaterisk.physical_risk import results_table as rt

    flood = {line["Hazard"]: line for line in rt.portfolio_totals_frame(rows)}["Flood"]
    assert flood["Assets Assessed"] == 5 and flood["Priced"] == 3
    assert flood["Not Available"] == 2
    np.testing.assert_allclose(flood["Total EAL"], a.eal_usd + e.eal_usd + o.eal_usd, rtol=1e-12)
    assert flood["Priced Asset Value"] == pytest.approx(3 * VALUE)
    port = next(c for c in curves if c["group_level"] == "PORTFOLIO")
    assert port["points_priced"] == 3
    # the Excel hazard sheet carries the reason and the lower-bound flag per asset
    sheet = {r["facility_id"]: r for r in rt.hazard_results_frame(rows)}
    assert sheet["B"]["unavailable_reason"] == "no_file_for_rp:200"
    assert sheet["B"]["eal_usd"] is None
    assert "lower bound" in sheet["E"]["confidence"]


def test_urban_pluvial_end_to_end_reasons_stay_at_100_years(data_root: Path) -> None:
    root = _build_maps(data_root)
    (root / URB / "CFM_SBSN_9106_100.zip").write_bytes(b"not a zip")  # unindexed urban file
    rows, _record, _ = _rows("PF", _facilities("AO"))
    by = {r.facility_id: r for r in rows}
    assert by["A"].calculation_status == "HAZARD_ONLY"
    assert by["A"].hazard_intensity == pytest.approx(0.25)  # N330 under the urban polygon
    assert by["O"].hazard_intensity is None  # unindexed urban file: extent unknown
    assert by["O"].unavailable_reason == "map_unreadable:100"  # no 200 / 500-y reason
    # unknown is not "Hazard only": the row is unavailable and counted as such
    assert by["O"].calculation_status == "NO_HAZARD_DATA"
    assert "map_unreadable:100" in (by["O"].status_detail or "")
    from climaterisk.physical_risk import results_table as rt

    counts = rt.summary_counts(rows)
    assert counts["hazard_only"] == 1 and counts["not_available"] == 1
    pf = {line["Hazard"]: line for line in rt.portfolio_totals_frame(rows)}["Urban Pluvial Flood"]
    assert pf["Hazard Only"] == 1 and pf["Not Available"] == 1


# --------------------------------------------------------------------------- #
# 1. blocker: urban zips are not river files                                    #
# --------------------------------------------------------------------------- #
def test_river_adapter_ignores_the_urban_folder(data_root: Path) -> None:
    root = _build_maps(data_root)
    for b in ("9102", "9103"):  # urban zips with no national counterpart
        _zmap(root, URB, f"CFM_SBSN_{b}_100", [("N333", _sq(X["O"]))])
    rows, _, _ = _rows("RF", _facilities("A"))
    assert rows[0].calculation_status == "FULL" and rows[0].unavailable_reason is None
    assert rows[0].hazard_intensity == pytest.approx(3.5)


# --------------------------------------------------------------------------- #
# 3. missing or empty family folders refuse to run                              #
# --------------------------------------------------------------------------- #
def test_all_river_folders_missing_is_hazard_unavailable_not_zero(data_root: Path) -> None:
    _build_maps(data_root, with_river=False)  # only the urban folder exists
    rows, record, _ = _rows("RF", _facilities("AO"))
    assert all(r.calculation_status == "NO_HAZARD_DATA" for r in rows)
    assert all(r.eal_usd is None and r.unavailable_reason == "hazard_unavailable" for r in rows)
    assert NAT[100] in record["load_error"] and LOC in record["load_error"]
    assert not paths.cache_dir("floodmap", "rfm_index.json").exists()


def test_one_river_folder_missing_partial_sync_is_hazard_unavailable(data_root: Path) -> None:
    import shutil

    root = _build_maps(data_root)
    shutil.rmtree(root / NAT[500])
    rows, record, _ = _rows("RF", _facilities("AO"))
    assert all(r.calculation_status == "NO_HAZARD_DATA" for r in rows)
    assert NAT[500] in record["load_error"] and NAT[100] not in record["load_error"]


def test_urban_folder_missing_is_hazard_unavailable(data_root: Path) -> None:
    _build_maps(data_root, with_urban=False)
    rows, record, _ = _rows("PF", _facilities("A"))
    assert rows[0].calculation_status == "NO_HAZARD_DATA"
    assert rows[0].unavailable_reason == "hazard_unavailable"
    assert URB in record["load_error"]


# --------------------------------------------------------------------------- #
# 7c. the overlay memo is keyed by the map root and its files                   #
# --------------------------------------------------------------------------- #
def test_map_memo_is_not_reused_for_another_map_root(
    tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_maps(data_root)
    pts = [(f["latitude"], f["longitude"]) for f in _facilities("O")]
    river = adapters.KoreaLocalFloodAdapter("historical", 2030, "KOR")
    urban = adapters.KoreaLocalPluvialAdapter("historical", 2030, "KOR")
    assert river.load(None, points=pts).intensity.toarray()[:, 0].tolist() == [0.0] * 3
    assert urban.load(None, points=pts).intensity.toarray()[:, 0].tolist() == [0.0]
    # a second data root: same file names, same points, but O now lies under a polygon
    other = tmp_path / "other_root"
    other.mkdir()
    monkeypatch.setenv("CLIMATERISK_DATA_ROOT", str(other))
    root2 = _build_maps(other)
    for rp in (100, 200, 500):
        _zmap(root2, NAT[rp], f"RFM_SBSN_NTN_9008_{rp}", [("N334", _sq(X["O"]))])
    _zmap(root2, URB, "CFM_SBSN_9108_100", [("N334", _sq(X["O"]))])
    river2 = adapters.KoreaLocalFloodAdapter("historical", 2030, "KOR")
    urban2 = adapters.KoreaLocalPluvialAdapter("historical", 2030, "KOR")
    assert river2.load(None, points=pts).intensity.toarray()[:, 0].tolist() == [5.0] * 3
    assert urban2.load(None, points=pts).intensity.toarray()[:, 0].tolist() == [5.0]


def test_memo_key_changes_when_a_map_file_changes(data_root: Path) -> None:
    root = _build_maps(data_root)
    pts = [(f["latitude"], f["longitude"]) for f in _facilities("O")]
    a = adapters.KoreaLocalFloodAdapter("historical", 2030, "KOR")
    assert a.load(None, points=pts).intensity.toarray()[:, 0].tolist() == [0.0, 0.0, 0.0]
    for rp in (100, 200, 500):
        _zmap(root, NAT[rp], f"RFM_SBSN_NTN_9008_{rp}", [("N334", _sq(X["O"]))])
    b = adapters.KoreaLocalFloodAdapter("historical", 2030, "KOR")
    assert b.load(None, points=pts).intensity.toarray()[:, 0].tolist() == [5.0, 5.0, 5.0]


# --------------------------------------------------------------------------- #
# 단면 sheet on the same synthetic maps                                          #
# --------------------------------------------------------------------------- #
def _sheet(key: str) -> dict[str, Any]:
    from climaterisk_worker import sheet_builder as sb

    f = _facilities(key)[0]
    return sb.build_sheet(
        f["latitude"], f["longitude"], f"PR-{key}", half_km=0.3, step_km=0.1, with_global=False
    )


def test_sheet_end_to_end_classes_and_reasons(data_root: Path) -> None:
    from climaterisk import sheets

    _build_maps(data_root)
    a = _sheet("A")
    assert sheets.validate_sheet(a) == []
    assert a["site"]["classes"] == {"rp100": "N331", "rp200": "N332", "rp500": "N333"}
    assert a["site"]["reasons"] == {"rp100": None, "rp200": None, "rp500": None}
    b = _sheet("B")
    assert sheets.validate_sheet(b) == []
    assert b["site"]["classes"]["rp200"] is None and b["site"]["bands_m"]["rp200"] is None
    assert b["site"]["reasons"]["rp200"] == "no_file_for_rp"
    d = _sheet("D")
    assert sheets.validate_sheet(d) == []
    assert d["site"]["classes"] == {"rp100": None, "rp200": None, "rp500": None}
    assert d["site"]["reasons"]["rp100"] == "map_unreadable"
    o = _sheet("O")
    assert o["site"]["classes"] == {"rp100": "", "rp200": "", "rp500": ""}
    assert o["site"]["bands_m"]["rp100"] == [0.0, 0.0]


def test_sheet_refuses_missing_river_folders(data_root: Path) -> None:
    _build_maps(data_root, with_river=False)
    with pytest.raises(FileNotFoundError, match=NAT[100]):
        _sheet("A")


# --------------------------------------------------------------------------- #
# 9b. adapter-wide unavailable rows carry a reason                              #
# --------------------------------------------------------------------------- #
def test_adapter_without_hazard_data_gives_rows_a_reason(data_root: Path) -> None:
    from climaterisk_worker.physical_risk.adapters import HazardDescription

    class _NoData:
        def describe(self) -> HazardDescription:
            return HazardDescription(
                hazard_type="RF",
                model_id="KOREA_LOCAL",
                status=adapters.NO_HAZARD_DATA,
                hazard_source="none",
                hazard_dataset="none",
                hazard_data_version="none",
                hazard_country="KOR",
                requested_scenario="historical",
                served_scenario="historical",
                time_horizon="present",
                detail="no data for this cell",
            )

    rows, _ = runner._rows_for(_NoData(), "RF", _facilities("A"), "KOR", None)
    assert rows[0].calculation_status == "NO_HAZARD_DATA"
    assert rows[0].unavailable_reason == "no_hazard_data"
    assert rows[0].status_detail == "no data for this cell"


# --------------------------------------------------------------------------- #
# registry checks read keys the adapters really write                           #
# --------------------------------------------------------------------------- #
def test_registry_diagnostics_keys_exist_in_rf_and_pf_output(data_root: Path) -> None:
    """Every RF / PF check's diagnostics_key is a dict in the rank-1 adapter's diagnostics.

    RF runs a present-window future scenario (rcp85 2020 → ISIMIP 2010–2030), so the climate
    factor is 1 by definition and no ISIMIP set is read — and ``climate_delta.check`` still
    exists. CF and HW are checked statically in ``test_method_registry`` (their inputs are real
    rasters / station files).
    """
    from climaterisk.physical_risk.method_registry import _dig, checks, rank1_model

    _build_maps(data_root)
    points = [(f["latitude"], f["longitude"]) for f in _facilities("AO")]
    for tag, scenario, year in (("RF", "rcp85", 2020), ("PF", "historical", 2030)):
        model = rank1_model(tag)
        assert model is not None
        adapter = adapters.adapter_for(tag, model, scenario, year, "KOR")
        adapter.load(None, points=points)
        for check in checks(tag):
            if check["diagnostics_key"] is None:  # to_build: nothing to read yet
                continue
            value = _dig(adapter.diagnostics, check["diagnostics_key"])
            assert isinstance(value, dict), (tag, check["check_id"], check["diagnostics_key"])


def test_present_window_factor_check_says_factor_one_by_definition(data_root: Path) -> None:
    _build_maps(data_root)
    adapter = adapters.adapter_for("RF", "KOREA_LOCAL", "rcp85", 2020, "KOR")
    _rows_out, record = runner._rows_for(adapter, "RF", _facilities("A"), "KOR", None)[:2]
    factor = next(c for c in record["checks"] if c["check_id"] == "rf.isimip_sido_factor")
    assert factor["state"] == "skipped"
    assert factor["reason"] == "factor 1 by definition (present window)"
    assert record["climate_delta"]["factor"] == 1.0
