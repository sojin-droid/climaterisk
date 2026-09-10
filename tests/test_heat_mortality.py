"""The `heat_mortality` peril — a health end-point that must never behave like money.

Covers the parts that hold without CLIMADA (comfort bands, the degree-day -> dose
calibration, exposure/finance separation) and, in the worker env, the full CLIMADA run:
hazard -> Exposures(persons) -> ImpactFuncSet -> ImpactCalc yielding expected annual
deaths.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import heat_mortality as hm  # noqa: E402
from climaterisk_worker import physical  # noqa: E402

_ASSET = {
    "id": "a",
    "name": "Site",
    "lat": 40.42,  # Madrid
    "lon": -3.70,
    "value": 1.0e7,
    "currency": "EUR",
    "wf_max_mdd": 0.4,
    "headcount": 500,
}


# --------------------------------------------------------------------------- #
# Registration + graceful degradation (no CLIMADA data needed).               #
# --------------------------------------------------------------------------- #
def test_registered_as_a_dedicated_runner() -> None:
    """It has its own runner: the shared catalog runner would return currency, not deaths."""
    assert "heat_mortality" in physical._RUNNERS
    assert "heat_mortality" not in physical._CATALOG_PERILS


def test_peril_is_in_the_backend_enum_and_library() -> None:
    import json

    pytest.importorskip("pydantic")  # backend deps are absent in the worker env
    from climaterisk.core.enums import Peril

    assert Peril("heat_mortality") is Peril.HEAT_MORTALITY
    perils = json.loads(
        (REPO / "assets" / "libraries" / "perils.json").read_text(encoding="utf-8")
    )["perils"]
    entry = next(p for p in perils if p["id"] == "heat_mortality")
    assert entry["supported_mvp"] is True  # else the UI silently drops it
    assert entry["result_kind"] == "mortality"


def test_needs_ingest_before_touching_headcount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With no hazard filed it must raise the standard message — and do so first."""
    pytest.importorskip("climada")
    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path))
    asset_without_headcount = {k: v for k, v in _ASSET.items() if k != "headcount"}
    with pytest.raises(ValueError, match="no local hazard"):
        physical._RUNNERS["heat_mortality"]([asset_without_headcount], "rcp45", [2050])


# --------------------------------------------------------------------------- #
# The science: comfort band + the degree-day -> dose calibration.             #
# --------------------------------------------------------------------------- #
def test_comfort_band_is_higher_and_wider_where_more_adapted() -> None:
    """Adaptation is *where the band sits and how wide it is*, not one MMT point."""
    sevilla = next(c for c in hm.REF_CITIES if c.name == "Sevilla")
    hamburg = next(c for c in hm.REF_CITIES if c.name == "Hamburg")
    s_low, s_high, s_width = hm.comfort_band(sevilla)
    h_low, h_high, h_width = hm.comfort_band(hamburg)

    assert s_high > h_high  # hot-adapted -> heat onset far higher
    assert s_width > h_width  # …and a wider low-mortality plateau
    assert s_low < s_high and h_low < h_high  # a band, not a point

    # The payoff: a 35 degC day is inside Sevilla's band but far above Hamburg's.
    assert s_low <= 35.0 <= s_high
    assert h_high < 35.0


def test_dose_curve_is_superlinear_and_zero_at_zero() -> None:
    """The exponential dose grows faster than the linear degree-day sum."""
    for band in hm.AGE_BANDS:
        a, b = hm.dose_curve(band.key)
        assert a > 0.0
        assert b > 1.0, f"{band.key}: expected super-linear curvature, got exponent {b}"

    dd = np.array([0.0, 25.0, 50.0, 100.0])
    dose = hm.dose_from_degree_days(dd, "o65")
    assert dose[0] == 0.0
    assert np.all(np.diff(dose) > 0)  # monotone increasing
    # Doubling the load more than doubles the dose (the curvature that matters).
    assert dose[3] > 2.0 * dose[2]


def test_elderly_dose_response_dominates() -> None:
    o65 = hm.band_by_key("o65")
    u65 = hm.band_by_key("u65")
    assert o65.beta > u65.beta
    # Per exposed person the elderly risk is far larger at the same heat load.
    load = np.array([80.0])
    risk_o = o65.baseline_daily_mortality * hm.dose_from_degree_days(load, "o65")
    risk_u = u65.baseline_daily_mortality * hm.dose_from_degree_days(load, "u65")
    assert risk_o[0] > 10.0 * risk_u[0]


def test_nearest_city_picks_the_local_reference() -> None:
    assert hm.nearest_city(40.42, -3.70).name == "Madrid"
    assert hm.nearest_city(52.52, 13.40).name == "Berlin"
    assert hm.nearest_city(37.98, 23.73).name == "Athina"


def test_exceedance_load_reorders_cities_versus_raw_temperature() -> None:
    """The whole point: hottest != most at-risk, once adaptation is accounted for."""
    cities = tuple(c for c in hm.REF_CITIES if c.name in ("Sevilla", "Zamora"))
    tmax = hm.simulate_seasons(np.random.default_rng(0), 120, cities)
    load = hm.exceedance_degree_days(tmax, cities).mean(axis=1)
    sevilla_i = [c.name for c in cities].index("Sevilla")
    zamora_i = [c.name for c in cities].index("Zamora")

    assert cities[sevilla_i].tmax_jja_mean > cities[zamora_i].tmax_jja_mean  # hotter
    assert load[sevilla_i] < load[zamora_i]  # yet a LOWER adaptation-adjusted load


# --------------------------------------------------------------------------- #
# Population exposure: run a whole country with no facility placed.           #
# --------------------------------------------------------------------------- #
def test_adaptation_fit_uses_the_published_slope() -> None:
    """The slope is Tobías et al. (2021)'s 0.8 degC per degC, not a local fit."""
    a, b = hm.adaptation_fit()
    assert b == pytest.approx(hm.MMT_ANNUAL_MEAN_SLOPE) == pytest.approx(0.8)
    assert b < 1.0, "a slope >= 1 would let warming reduce the exceedance load"
    # the intercept still places the curve on the reference table's level
    hot, cool = (
        max(hm.REF_CITIES, key=lambda c: c.tmax_jja_mean),
        min(hm.REF_CITIES, key=lambda c: c.tmax_jja_mean),
    )
    assert a + b * hot.tmax_jja_mean > a + b * cool.tmax_jja_mean


def test_country_grid_is_denser_than_the_reference_points() -> None:
    """The hazard's resolution, not the exposure's, is what a population run needs."""
    pytest.importorskip("geopandas")
    grid = hm.country_grid("ESP", res_deg=0.5)
    refs = [c for c in hm.REF_CITIES if c.country == "ESP"]
    assert len(grid) > 4 * len(refs)
    assert all(g.country == "ESP" for g in grid)
    assert all(g.population == 0 for g in grid)  # exposure comes from a population layer
    # Interpolated climatology stays inside the reference range (no wild extrapolation).
    lo = min(c.tmax_jja_mean for c in hm.REF_CITIES)
    hi = max(c.tmax_jja_mean for c in hm.REF_CITIES)
    assert all(lo - 1.0 <= g.tmax_jja_mean <= hi + 1.0 for g in grid)
    # …and every cell has a comfort band above its own mean summer temperature.
    assert all(g.mmt_high > g.tmax_jja_mean - 1.0 for g in grid)


def test_degree_days_chunked_matches_the_unchunked_reduction() -> None:
    """Chunking exists only to bound memory; it must not change the numbers."""
    cities = tuple(c for c in hm.REF_CITIES if c.country == "PRT")
    chunked = hm.degree_days_chunked(cities, n_seasons=20, seed=5, chunk=7)
    direct = hm.exceedance_degree_days(
        hm.simulate_seasons(np.random.default_rng(5), 20, cities), cities
    )
    assert chunked.shape == direct.shape == (len(cities), 20)
    # Same seed + same season count in one block reproduces the stream exactly.
    one_block = hm.degree_days_chunked(cities, n_seasons=20, seed=5, chunk=20)
    np.testing.assert_allclose(one_block, direct, rtol=1e-12, atol=0)


def test_population_exposure_carries_people_not_money() -> None:
    """`population_ref` exposes residents; the grid→asset step must pass them as headcount."""
    pytest.importorskip("climada")
    from climaterisk_worker.exposures import POPULATION_SOURCES, build_exposure
    from climaterisk_worker.litpop import _grid_to_assets

    assert "population_ref" in POPULATION_SOURCES
    exp = build_exposure("population_ref", "ESP")
    assert exp.value_unit == "persons"
    total = float(exp.gdf["value"].sum())
    assert total == pytest.approx(
        sum(c.population for c in hm.REF_CITIES if c.country == "ESP"), rel=1e-9
    )

    assets = _grid_to_assets(exp, "persons", is_population=True)
    assert all(a["headcount"] > 0 for a in assets)
    assert all(a["value"] == 0.0 for a in assets)  # never mistakable for currency
    assert sum(a["headcount"] for a in assets) == pytest.approx(total, rel=1e-9)


def test_unknown_country_for_population_exposure_degrades_gracefully() -> None:
    pytest.importorskip("climada")
    from climaterisk_worker.exposures import ExposureUnavailable, build_exposure

    with pytest.raises(ExposureUnavailable, match="no reference-population points"):
        build_exposure("population_ref", "KOR")


def test_raster_block_aggregation_preserves_the_total() -> None:
    """Population counts must be SUMMED when coarsening — averaging would destroy them."""
    pytest.importorskip("climada")
    rasterio = pytest.importorskip("rasterio")
    raster = Path.home() / "climada" / "data" / "esp_ppp_2020_1km_Aggregated.tif"
    if not raster.is_file():
        pytest.skip("WorldPop ESP raster not downloaded")
    from climaterisk_worker.exposures import _raster_exposure

    with rasterio.open(raster) as src:
        arr = src.read(1, masked=True).filled(0.0)
        arr[arr < 0] = 0.0
        native_total = float(arr.sum())

    exp = _raster_exposure(raster, res_arcsec=300)
    assert exp.value_unit == "persons"
    coarse_total = float(exp.gdf["value"].sum())
    # Block trimming can drop at most one partial block row/column at the edges.
    assert coarse_total == pytest.approx(native_total, rel=1e-3)
    assert len(exp.gdf) < 50_000  # coarsened enough to be tractable


def test_majority_iso3_survives_offshore_cells() -> None:
    """A gridded country exposure always has cells whose ISO3 lookup returns None."""
    assert physical._single_country_iso3(["ESP", None, "ESP"]) is None  # strict, unchanged
    assert physical._majority_iso3(["ESP", None, "ESP", "PRT"]) == "ESP"
    assert physical._majority_iso3([None, None]) is None


# --------------------------------------------------------------------------- #
# Deaths must never be summed into a currency total.                          #
# --------------------------------------------------------------------------- #
def test_non_monetary_perils_excluded_from_financial_aai() -> None:
    pytest.importorskip("pydantic")  # backend deps are absent in the worker env
    from climaterisk.finance.service import per_asset_aai

    run_output = {
        "results": [
            {
                "peril": "river_flood",
                "status": "ok",
                "result_kind": "monetary",
                "per_asset": [{"id": "a", "eai": 1000.0}],
            },
            {
                "peril": "heat_mortality",
                "status": "ok",
                "result_kind": "mortality",
                "per_asset": [{"id": "a", "eai": 3.5}],  # deaths — must not be added
            },
        ]
    }
    assert per_asset_aai(run_output) == {"a": 1000.0}


# --------------------------------------------------------------------------- #
# Full CLIMADA path (worker env only).                                        #
# --------------------------------------------------------------------------- #
def test_full_climada_run_reports_deaths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end: file a hazard in a temp catalog, run the peril, get deaths in persons."""
    pytest.importorskip("climada")
    from climaterisk_worker import catalog
    from climaterisk_worker.hazard_convert import convert_grid_to_catalog

    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path))
    cities = tuple(c for c in hm.REF_CITIES if c.country == "ESP")
    tmax = hm.simulate_seasons(np.random.default_rng(7), 60, cities)
    dd = hm.exceedance_degree_days(tmax, cities)
    entry = convert_grid_to_catalog(hm.standardized_grid(dd, cities, region="ESP"), tmp_path)
    catalog.register(entry)
    assert entry["units"] == hm.INTENSITY_UNIT

    res = physical._RUNNERS["heat_mortality"]([_ASSET], "historical", [2020])

    assert res["status"] == "ok"
    assert res["result_kind"] == "mortality"
    assert "deaths" in (res["metric_unit"] or "")
    # Exposure is people, not money: total_value is the headcount we supplied.
    assert res["total_value"] == pytest.approx(float(_ASSET["headcount"]), rel=1e-6)
    # A plausible number of deaths: positive, and far below the exposed headcount.
    assert 0.0 < res["aai_agg"] < 0.05 * float(_ASSET["headcount"])
    assert res["per_asset"][0]["eai"] == pytest.approx(res["aai_agg"], rel=1e-6)
    assert res["per_asset"][0]["country"] == "ESP"


def test_headcount_default_is_stated_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fallback headcount is allowed, but it must never be silent."""
    pytest.importorskip("climada")
    from climaterisk_worker import catalog
    from climaterisk_worker.hazard_convert import convert_grid_to_catalog

    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path))
    cities = tuple(c for c in hm.REF_CITIES if c.country == "ESP")
    dd = hm.exceedance_degree_days(
        hm.simulate_seasons(np.random.default_rng(7), 40, cities), cities
    )
    catalog.register(
        convert_grid_to_catalog(hm.standardized_grid(dd, cities, region="ESP"), tmp_path)
    )

    bare = {k: v for k, v in _ASSET.items() if k != "headcount"}
    res = physical._RUNNERS["heat_mortality"](
        [bare], "historical", [2020], {"default_headcount": 80}
    )
    assert res["total_value"] == pytest.approx(80.0, rel=1e-6)
    assert "headcount assumed 80" in (res["detail"] or "")


# --------------------------------------------------------------------------- #
# The methodology figure endpoint (served to the Results view).                #
# --------------------------------------------------------------------------- #
def test_method_figure_endpoint_serves_png_or_404s_actionably() -> None:
    """The figure is script-produced, so a missing one must 404 with a runnable hint."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from climaterisk.api.main import app

    client = TestClient(app)

    # A peril with no methodology figure registered at all.
    unknown = client.get("/api/libraries/method-figure/river_flood.png")
    assert unknown.status_code == 404
    assert "no methodology figure" in unknown.json()["detail"]

    got = client.get("/api/libraries/method-figure/heat_mortality.png")
    if got.status_code == 404:
        # Not generated in this checkout — the message must say how to make it.
        assert "scripts/heatwave_europe.py" in got.json()["detail"]
    else:
        assert got.status_code == 200
        assert got.headers["content-type"] == "image/png"
        assert got.content[:8] == b"\x89PNG\r\n\x1a\n"  # a real PNG, not an error page


# --------------------------------------------------------------------------- #
# E-OBS: observed daily temperature instead of the synthetic generator.        #
# --------------------------------------------------------------------------- #
def test_eobs_missing_file_gives_actionable_help() -> None:
    """Without the NetCDF the model must fall back, not crash opaquely."""
    from climaterisk_worker import eobs

    err = eobs.EobsUnavailable()
    assert "curl" in err.detail and "CLIMATERISK_EOBS_TG" in err.detail
    # `available()` is the switch every caller uses to pick observed vs synthetic.
    assert isinstance(eobs.available(), bool)


def test_eobs_season_is_122_days_regardless_of_leap_year() -> None:
    """Jun 1 - Sep 30 is 30+31+31+30 days, so seasons are directly comparable."""
    import calendar

    from climaterisk_worker import eobs

    for year in (2003, 2004, 2022):  # includes a leap year
        days = sum(calendar.monthrange(year, m)[1] for m in (6, 7, 8, 9))
        assert days == eobs.SEASON_DAYS


def test_observed_grid_reproduces_the_real_ranking_of_spanish_summers() -> None:
    """The payoff of real data: 2022 must come out as Spain's most extreme summer.

    2022 was Spain's hottest summer on record and MoMo's deadliest (~20,291 excess
    deaths). A synthetic ensemble cannot be checked against that; observations can.
    """
    pytest.importorskip("xarray")
    pytest.importorskip("geopandas")
    from climaterisk_worker import eobs

    if not eobs.available():
        pytest.skip("E-OBS daily-Tmax NetCDF not downloaded")

    cells, dd, years = hm.observed_country_grid("ESP", year_start=1980)
    assert len(cells) > 300  # a real grid, not the 15 reference points
    assert dd.shape == (len(cells), len(years))
    assert years[0] >= 1980 and years[-1] >= 2020

    national = dd.mean(axis=0)
    hottest = int(years[int(np.argmax(national))])
    assert hottest == 2022, f"expected 2022 as the most extreme summer, got {hottest}"
    # 2003 (the other landmark European heatwave) must sit well inside the top decile.
    rank_2003 = int(np.argsort(national)[::-1].tolist().index(int(np.where(years == 2003)[0][0])))
    assert rank_2003 < max(5, len(years) // 8)

    # Observed climatology must be physical, and each cell adapted above its own mean.
    assert all(5.0 < c.tmax_jja_mean < 45.0 for c in cells)
    assert all(c.mmt_high > c.tmax_jja_mean for c in cells)


def test_observed_hazard_grid_carries_real_calendar_years() -> None:
    """A synthetic season has no year; an observed one must keep the year it happened."""
    synthetic = hm.standardized_grid(np.zeros((2, 3)), hm.REF_CITIES[:2], region="ESP")
    assert {o["year"] for o in synthetic["observations"]} == {1, 2, 3}
    assert "synthetic" in synthetic["license"]

    observed = hm.standardized_grid(
        np.zeros((2, 3)), hm.REF_CITIES[:2], region="ESP", years=np.array([2003, 2022, 2024])
    )
    assert {o["year"] for o in observed["observations"]} == {2003, 2022, 2024}
    assert "E-OBS" in observed["license"] or "ECA&D" in observed["license"]

    with pytest.raises(ValueError, match="years has"):
        hm.standardized_grid(np.zeros((2, 3)), hm.REF_CITIES[:2], "ESP", years=np.array([2003]))


# --------------------------------------------------------------------------- #
# Return periods must not be extrapolated past the record.                     #
# --------------------------------------------------------------------------- #
def test_return_periods_capped_to_half_the_event_record() -> None:
    """A 45-summer record is reported to ~22 years: the tail is noise before the record ends."""

    class _Imp:  # minimal stand-in: the helper only reads `frequency`
        def __init__(self, n: int) -> None:
            self.frequency = np.full(n, 1.0 / n)

    kept, cap, record = physical._resolvable_return_periods(_Imp(45))
    assert record == pytest.approx(45.0)
    assert cap == pytest.approx(22.5)  # half the record, not the whole record
    assert all(rp <= cap for rp in kept)
    assert 25 not in kept and 50 not in kept  # 25 now falls outside too

    # A large Data-API set keeps the full ladder — existing perils are untouched.
    kept_big, cap_big, record_big = physical._resolvable_return_periods(_Imp(20_000))
    assert record_big == pytest.approx(20_000.0)
    assert cap_big == pytest.approx(10_000.0)
    assert kept_big == physical._RETURN_PERIODS


def test_return_period_curve_is_never_empty() -> None:
    """Even a very short record keeps one point, so the curve still renders."""

    class _Imp:
        frequency = np.array([0.5, 0.5])  # a 2-event record

    kept, cap, record = physical._resolvable_return_periods(_Imp())
    assert record == pytest.approx(2.0) and cap == pytest.approx(1.0)
    assert len(kept) == 1 and kept[0] == min(physical._RETURN_PERIODS)


def test_custom_requested_periods_respect_the_half_record_cap() -> None:
    class _Imp:
        frequency = np.full(45, 1.0 / 45)

    kept, cap, _ = physical._resolvable_return_periods(_Imp(), [2, 5, 20, 22, 25, 45, 100])
    assert cap == pytest.approx(22.5)
    assert kept == [2, 5, 20, 22]  # 25 and beyond are dropped


def test_record_fraction_is_explicit_and_conservative() -> None:
    """The convention is a named constant, not a magic number buried in the helper."""
    assert 0.0 < physical._RP_RECORD_FRACTION <= 0.5
