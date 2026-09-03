"""KMA 남한상세 1 km scenario grids → heat hazards, on a spec-conformant synthetic file.

The real files sit behind a login, so these tests build a small NetCDF that follows the
KMA 활용매뉴얼 v5.1 spec (regular 0.01° lat/lon, TAMAX in degC, missing -9990, tar.gz
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


def _write_kma_file(directory: Path, years: tuple[int, ...], as_archive: bool = True) -> Path:
    """Synthetic MK-PRISM-style daily TAMAX file: two sea columns filled with -9990."""
    import pandas as pd

    time = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D")
    lat = LAT0 + kma.GRID_RES_DEG * np.arange(N_LAT)
    lon = LON0 + kma.GRID_RES_DEG * np.arange(N_LON)
    doy = time.dayofyear.values
    seasonal = 12.0 + 16.0 * np.sin((doy - 110) / 365.0 * 2 * np.pi)  # peaks late July ~28-34
    rng = np.random.default_rng(0)
    data = seasonal[:, None, None] + rng.normal(0, 2.5, size=(time.size, N_LAT, N_LON))
    data += np.linspace(0, 3, N_LON)[None, None, :]  # east warmer
    data[:, :, :2] = kma.MISSING_VALUE  # sea columns
    ds = xr.Dataset(
        {"TAMAX": (("time", "latitude", "longitude"), data.astype(np.float32))},
        coords={"time": time, "latitude": lat, "longitude": lon},
    )
    ds["TAMAX"].attrs["units"] = "degC"
    stem = f"MKPRISM_MKPRISMv21_skorea_TAMAX_gridraw_daily_{years[0]}_{years[-1]}"
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
    f = kma.parse_name(Path("AR6_SSP585_5ENSMN_skorea_TAMAX_gridraw_daily_2021_2030.nc"))
    assert f is not None
    assert (f.scenario_token, f.model, f.variable, f.step) == ("SSP585", "5ENSMN", "TAMAX", "daily")
    assert (f.year_start, f.year_end, f.platform_scenario) == (2021, 2030, "rcp85")
    g = kma.parse_name(Path("MKPRISM_MKPRISMv21_skorea_TAMAX_gridraw_daily_2000_2019.nc"))
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
