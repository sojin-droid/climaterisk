"""The climate-change baseline is like-for-like: same model chain, earliest window.

Evidence behind the rule (1,000-building sample, JRC Asia curves, DATA_API_COUNTRY): the
portfolio flood EAL is $772,738 on the reanalysis-forced ``hist`` 1980-2000 set, $3,809,858
on rcp85 2010-2030 and $11,147,286 on rcp85 2030-2050 — 14.4x against the historical set,
2.93x within the GCM-forced chain. CLIMADA-free: the runner's baseline plan, the forcing-family
check and the multiplier refusal are pure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(_WORKER) not in sys.path:
    sys.path.insert(0, str(_WORKER))

from climaterisk_worker._params import RF_YEAR_RANGES  # noqa: E402
from climaterisk_worker.physical_risk import adapters, runner  # noqa: E402

from climaterisk.physical_risk.metrics import (  # noqa: E402
    CalculationStatus,
    ModelId,
    ResultRow,
    climate_change_multiplier,
    hazard_forcing_family,
)

C, K = ModelId.DATA_API_COUNTRY.value, ModelId.KOREA_LOCAL.value
HIST = "river_flood_150arcsec_hist_KOR_1980_2000"
RF_2010 = "river_flood_150arcsec_rcp85_KOR_2010_2030"
RF_2030 = "river_flood_150arcsec_rcp85_KOR_2030_2050"


def _rf(eal: float, dataset: str, scenario: str, horizon: str, hazard: str = "RF") -> ResultRow:
    return ResultRow(
        facility_id="F1",
        facility_name="F1",
        hazard_type=hazard,
        model_id=C,
        eal_usd=eal,
        asset_value_usd=100_000_000.0,
        calculation_status=CalculationStatus.FULL.value,
        hazard_dataset=dataset,
        served_scenario=scenario,
        time_horizon=horizon,
    ).finalise()


def test_present_baseline_picks_the_same_chain_per_hazard() -> None:
    plan = runner.baseline_groups("present", "rcp85", 2040, ["RF", "TC", "CF", "HEAT"])
    assert plan == [("rcp85", 2010, ["RF"]), ("historical", 2040, ["TC", "CF", "HEAT"])]
    assert int(RF_YEAR_RANGES[0][:4]) == 2010  # the present window is the list's first entry


def test_named_baselines_pass_through_unchanged() -> None:
    assert runner.baseline_groups("historical", "rcp85", 2040, ["RF", "TC"]) == [
        ("historical", 2040, ["RF", "TC"])
    ]
    assert runner.baseline_groups("rcp26", "rcp85", 2040, ["TC"]) == [("rcp26", 2040, ["TC"])]


def test_present_flood_baseline_names_the_same_rcp_product() -> None:
    """The 'present' flood year resolves to the same RCP set, 2010-2030 window."""
    name, served, window = adapters.dataapi_dataset_name(
        "RF", "rcp85", 2010, coverage="country", iso3="KOR"
    )
    assert (name, served, window) == (RF_2010, "rcp85", "2010–2030")
    future, _, _ = adapters.dataapi_dataset_name(
        "RF", "rcp85", 2040, coverage="country", iso3="KOR"
    )
    assert future == RF_2030
    assert hazard_forcing_family("RF", name) == hazard_forcing_family("RF", future)


def test_forcing_family_reads_the_dataset_name() -> None:
    assert hazard_forcing_family("RF", HIST).startswith("observed-reanalysis")
    assert hazard_forcing_family("RF", RF_2030).startswith("GCM")
    assert hazard_forcing_family("RF", None) is None
    assert hazard_forcing_family("RF", "my_local_floodmap") is None  # unknown: not blocked
    assert (
        hazard_forcing_family("TC", "tropical_cyclone_10synth_tracks_150arcsec_KOR_1980_2020")
        is None
    )


def test_multiplier_refuses_a_reanalysis_flood_baseline() -> None:
    base = _rf(772_738.0, HIST, "historical", "1980–2000")
    future = _rf(11_147_286.0, RF_2030, "rcp85", "2030–2050")
    out = climate_change_multiplier(base, future)
    assert out["climate_change_multiplier"] is None
    assert "model-chain" in out["detail"] and "'present'" in out["detail"]


def test_multiplier_within_the_chain_is_the_plain_ratio() -> None:
    base = _rf(3_809_858.0, RF_2010, "rcp85", "2010–2030")
    future = _rf(11_147_286.0, RF_2030, "rcp85", "2030–2050")
    out = climate_change_multiplier(base, future)
    assert out["climate_change_multiplier"] == pytest.approx(11_147_286.0 / 3_809_858.0, rel=1e-12)
    assert out["climate_change_multiplier"] == pytest.approx(2.926, abs=5e-4)


def test_multiplier_refuses_when_the_target_is_the_present_window() -> None:
    base = _rf(3_809_858.0, RF_2010, "rcp85", "2010–2030")
    same = _rf(3_809_858.0, RF_2010, "rcp85", "2010–2030")
    assert "nothing to multiply" in climate_change_multiplier(base, same)["detail"]


def test_typhoon_historical_baseline_is_still_accepted() -> None:
    base = _rf(
        458_220.0,
        "tropical_cyclone_10synth_tracks_150arcsec_KOR_1980_2020",
        "historical",
        "1980–2020",
        "TC",
    )
    fut = _rf(
        544_302.0, "tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040", "rcp85", "2040", "TC"
    )
    assert climate_change_multiplier(base, fut)["climate_change_multiplier"] == pytest.approx(
        544_302.0 / 458_220.0, rel=1e-12
    )


def test_runner_present_baseline_runs_each_hazard_against_its_own_present() -> None:
    """KOREA_LOCAL is unpriced (no network): the plan is visible in the baseline rows."""
    out = runner.compute_physical_risk_models(
        {
            "facilities": [
                {
                    "facility_id": "F1",
                    "facility_name": "F1",
                    "latitude": 37.5,
                    "longitude": 127.0,
                    "asset_value_usd": 1_000_000.0,
                    "property_type": "residential",
                }
            ],
            "climate_scenario": "rcp85",
            "target_year": 2040,
            "hazards": ["RF", "TC"],
            "models": [K],
            "country": "KOR",
            "baseline_scenario": "present",
        }
    )
    assert out["baseline_scenario"] == "present"
    by_hazard = {r["hazard_type"]: r for r in out["baseline_rows"]}
    assert set(by_hazard) == {"RF", "TC"}
    assert by_hazard["RF"]["requested_scenario"] == "rcp85"
    assert by_hazard["TC"]["requested_scenario"] == "historical"
    assert len(out["climate_change_multipliers"]) == 2
