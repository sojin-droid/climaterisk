"""KMA 남한상세 1 km scenario grids → heat hazards, on a spec-conformant synthetic file.

The real files sit behind a login, so these tests build a small NetCDF that follows the
KMA 활용매뉴얼 v5.1 spec (regular 0.01° lat/lon, TA in degC, missing -9990, tar.gz
delivery) and push it through the whole on-ramp: archive → loader → degree-days →
catalog → CLIMADA runner.
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import heat_mortality as hm  # noqa: E402
from climaterisk_worker import kma_scenario as kma  # noqa: E402

xr = pytest.importorskip("xarray")
pytest.importorskip("netCDF4")

N_LAT, N_LON = 10, 10
LAT0, LON0 = 37.40, 126.80  # Seoul


def _write_kma_file(
    directory: Path,
    years: tuple[int, ...],
    as_archive: bool = True,
    stem: str | None = None,
    warm_offset: float = 0.0,
) -> Path:
    """Synthetic MK-PRISM-style daily TA file: two sea columns filled with -9990.

    ``stem`` overrides the file name (e.g. an ``AR6_SSP245_5ENSMN_…`` future product);
    ``warm_offset`` shifts every day's Tmax (degC) so a "future" file is distinguishable.
    """
    import pandas as pd

    time = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D")
    lat = LAT0 + kma.GRID_RES_DEG * np.arange(N_LAT)
    lon = LON0 + kma.GRID_RES_DEG * np.arange(N_LON)
    doy = time.dayofyear.values
    seasonal = 12.0 + 16.0 * np.sin((doy - 110) / 365.0 * 2 * np.pi)  # peaks late July ~28-34
    rng = np.random.default_rng(0)
    data = seasonal[:, None, None] + rng.normal(0, 2.5, size=(time.size, N_LAT, N_LON))
    data += np.linspace(0, 3, N_LON)[None, None, :]  # east warmer
    data += warm_offset
    data[:, :, :2] = kma.MISSING_VALUE  # sea columns
    ds = xr.Dataset(
        {"TA": (("time", "latitude", "longitude"), data.astype(np.float32))},
        coords={"time": time, "latitude": lat, "longitude": lon},
    )
    ds["TA"].attrs["units"] = "degC"
    stem = stem or f"MKPRISM_MKPRISMv21_skorea_TA_gridraw_daily_{years[0]}_{years[-1]}"
    nc = directory / f"{stem}.nc"
    ds.to_netcdf(nc)
    if not as_archive:
        return nc
    tgz = directory / f"{stem}_nc.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(nc, arcname=nc.name)
    nc.unlink()
    return tgz


def test_file_name_parsing_covers_both_products() -> None:
    f = kma.parse_name(Path("AR6_SSP585_5ENSMN_skorea_TA_gridraw_daily_2021_2030.nc"))
    assert f is not None
    assert (f.scenario_token, f.model, f.variable, f.step) == ("SSP585", "5ENSMN", "TA", "daily")
    assert (f.year_start, f.year_end, f.platform_scenario) == (2021, 2030, "rcp85")
    g = kma.parse_name(Path("MKPRISM_MKPRISMv21_skorea_TA_gridraw_daily_2000_2019.nc"))
    assert g is not None and g.model is None and g.platform_scenario == "historical"
    assert kma.parse_name(Path("tx_ens_mean_0.25deg_reg_v31.0e.nc")) is None


def test_archive_is_unpacked_once_and_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path))
    _write_kma_file(tmp_path, (2000, 2001))
    first = kma.extract_archives()
    assert len(first) == 1 and first[0].suffix == ".nc"
    assert kma.extract_archives() == []  # idempotent
    files = kma.list_files(scenario="historical")
    assert [f.year_start for f in files] == [2000]
    assert kma.available("historical") and not kma.available("rcp85")


def test_loader_drops_sea_coarsens_and_keeps_calendar_years(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path))
    _write_kma_file(tmp_path, (2000, 2001))
    obs = kma.load_summer_tmax("historical", coarsen=2)
    # 10x10 → 5x5 blocks; the two sea columns form one all-missing block column → 20 cells.
    assert obs.n_cells == 20
    assert obs.years.tolist() == [2000, 2001]
    assert obs.tmax.shape == (20, 2, kma.SEASON_DAYS)
    assert not np.isnan(obs.tmax).any()
    assert 20.0 < obs.tmax.mean() < 35.0  # degC, summer
    # block mean of coordinates: first kept column block is lon index 2..3
    assert np.isclose(obs.lon.min(), LON0 + kma.GRID_RES_DEG * 2.5)
    assert "MKPRISMv21" in obs.source and "ensemble" not in obs.source


def test_native_resolution_is_kept_when_coarsen_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path))
    _write_kma_file(tmp_path, (2000,), as_archive=False)
    obs = kma.load_summer_tmax("historical", coarsen=1)
    assert obs.n_cells == N_LAT * (N_LON - 2)


def test_block_mean_matches_plain_mean_without_gaps() -> None:
    arr = np.arange(2 * 6 * 4, dtype=float).reshape(2, 6, 4)
    out = kma._block_mean(arr, 2)
    assert out.shape == (2, 3, 2)
    np.testing.assert_allclose(out[0, 0, 0], arr[0, :2, :2].mean(), rtol=0, atol=1e-12)


def test_missing_file_gives_actionable_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path / "empty"))
    with pytest.raises(kma.KmaUnavailable) as ei:
        kma.load_summer_tmax("historical")
    assert "climate.go.kr" in ei.value.detail and "CLIMATERISK_KMA_DIR" in ei.value.detail


def test_degree_days_from_kma_stack_are_nonnegative_and_warmer_east(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path))
    _write_kma_file(tmp_path, (2000, 2001))
    obs = kma.load_summer_tmax("historical", coarsen=2)
    cells, dd, years = hm.grid_from_summer_tmax(obs, "KOR", tag="kma", land_mask=False)
    assert len(cells) == obs.n_cells and dd.shape == (obs.n_cells, 2)
    assert (dd >= 0).all() and years.tolist() == [2000, 2001]
    assert cells[0].name.startswith("KOR_kma_") and cells[0].country == "KOR"
    # The comfort band adapts to climatology, so the eastern (warmer) cells do not simply
    # inherit more degree-days one-for-one — but their mmt_high must be higher.
    east = np.array([c.lon for c in cells]) > np.median([c.lon for c in cells])
    assert np.mean([c.mmt_high for c, e in zip(cells, east, strict=True) if e]) > np.mean(
        [c.mmt_high for c, e in zip(cells, east, strict=True) if not e]
    )


def test_korean_sites_use_korean_age_structure_not_a_european_city() -> None:
    kor = hm.share_over65_for(37.57, 126.98, "KOR")
    assert kor == pytest.approx(hm.COUNTRY_SHARE_OVER65["KOR"])
    assert kor != hm.nearest_city(37.57, 126.98).share_over65
    assert hm.share_over65_for(37.57, 126.98, "KOR", override=0.3) == pytest.approx(0.3)
    assert hm.share_over65_for(37.57, 126.98, "KOR", override=0.9) == hm.MAX_SHARE_OVER65
    # Europe still resolves through the reference table.
    assert hm.share_over65_for(40.42, -3.70, "ESP") == hm.nearest_city(40.42, -3.70).share_over65


def test_full_on_ramp_registers_and_runs_a_korean_portfolio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("climada")
    from climaterisk_worker import catalog, physical
    from climaterisk_worker.hazard_convert import convert_grid_to_catalog

    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path / "kma"))
    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path / "db"))
    (tmp_path / "kma").mkdir()
    _write_kma_file(tmp_path / "kma", (2000, 2001, 2002, 2003))

    obs = kma.load_summer_tmax("historical", coarsen=2)
    cells, dd, years = hm.grid_from_summer_tmax(obs, "KOR", tag="kma", land_mask=False)
    grid = hm.standardized_grid(dd, cells, region="KOR", years=years, source=obs.source)
    entry = convert_grid_to_catalog(grid, catalog.catalog_dir())
    catalog.register(entry)
    assert entry["n_events"] == 4 and entry["n_centroids"] == obs.n_cells
    haz = catalog.load_hazard("heat_mortality", "historical", "KOR", 2020)
    assert haz is not None and haz.event_name == ["2000", "2001", "2002", "2003"]

    site = {
        "id": "seoul",
        "lat": LAT0 + 0.05,
        "lon": LON0 + 0.06,
        "value": 0.0,
        "currency": "KRW",
        "headcount": 1000,
    }
    res = physical._run_heat_mortality([site], "rcp45", [2020], {"country_iso3": "KOR"})
    assert res["result_kind"] == "mortality" and res["status"] == "ok"
    assert res["aai_agg"] >= 0.0 and "KOR heat mortality" in res["detail"]
    assert res["total_value"] == pytest.approx(1000.0)
    # 4 seasons → record 4 yr → cap 2 yr: nothing beyond that is extrapolated.
    assert res["freq_curve"]["max_resolvable_return_period"] == pytest.approx(2.0)
    # Only the historical layer exists → the runner says so instead of pretending rcp45 ran.
    assert "scenario historical" in res["detail"] and "fallback" in res["detail"]


def test_requested_ssp_layer_is_resolved_end_to_end_for_korea(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """request(rcp45, 2030) → catalog SSP245 layer → CLIMADA Hazard → ImpactCalc (deaths).

    Registers the historical MK-PRISM window AND an SSP245 future window (warmer by 3 degC)
    through scripts/heat_korea.py's ``register_window`` — the real on-ramp — then checks the
    ``heat_mortality`` runner picks the requested scenario (not ``historical``) and that the
    ``heatwave`` layer written by the same on-ramp runs under the runner's ``HW`` hazard type.
    """
    pytest.importorskip("climada")
    import importlib.util

    from climaterisk_worker import catalog, physical

    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path / "kma"))
    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path / "db"))
    (tmp_path / "kma").mkdir()
    _write_kma_file(tmp_path / "kma", (2000, 2001, 2002, 2003))
    _write_kma_file(
        tmp_path / "kma",
        (2021, 2022, 2023, 2024),
        stem="AR6_SSP245_5ENSMN_skorea_TA_gridraw_daily_2021_2024",
        warm_offset=3.0,
    )
    assert kma.available("historical") and kma.available("rcp45") and not kma.available("rcp85")

    spec = importlib.util.spec_from_file_location("heat_korea", REPO / "scripts" / "heat_korea.py")
    hk = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hk)
    _, baseline_cells = hk.register_window("historical", 2020, None, None, 2, catalog.catalog_dir())
    # The future window inherits the baseline comfort band: no adaptation is the reference
    # case, so a warmer stack must produce a larger load (docs/HEAT_ADAPTATION_KR.md).
    hk.register_window(
        "rcp45", 2030, 2021, 2024, 2, catalog.catalog_dir(), reference_cells=baseline_cells
    )

    keys = {(e["peril"], e["climate_scenario"], e["year"]) for e in catalog.load_manifest()}
    assert {
        ("heat_mortality", "historical", 2020),
        ("heat_mortality", "rcp45", 2030),
        ("heatwave", "historical", 2020),
        ("heatwave", "rcp45", 2030),
    } <= keys

    site = {
        "id": "seoul",
        "lat": LAT0 + 0.05,
        "lon": LON0 + 0.06,
        "value": 0.0,
        "currency": "KRW",
        "headcount": 1000,
        "wf_max_mdd": 0.4,
    }
    fut = physical._run_heat_mortality([site], "rcp45", [2030], {"country_iso3": "KOR"})
    assert fut["status"] == "ok" and "scenario rcp45" in fut["detail"]
    assert "fallback" not in fut["detail"]
    hist = physical._run_heat_mortality([site], "historical", [2020], {"country_iso3": "KOR"})
    assert hist["status"] == "ok" and "scenario historical" in hist["detail"]
    # a warmer future stack cannot produce fewer expected deaths than the present one
    assert fut["aai_agg"] > hist["aai_agg"]
    # a scenario with no layer falls back to historical and says so
    miss = physical._run_heat_mortality([site], "rcp85", [2030], {"country_iso3": "KOR"})
    assert "fallback" in miss["detail"] and miss["aai_agg"] == pytest.approx(hist["aai_agg"])

    # heatwave (productivity ramp) — same on-ramp, must resolve under haz_type "HW"
    hw = physical._RUNNERS["heatwave"](
        [{**site, "value": 1.0e6}], "rcp45", [2030], {"country_iso3": "KOR"}
    )
    assert hw["status"] == "ok" and hw["result_kind"] == "productivity"
