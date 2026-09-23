"""The physical-risk engine against real CLIMADA objects and synthetic hazard sets.

The hazards are synthetic so the arithmetic is checkable by hand; the **impact functions
are the published ones**, instantiated from the installed CLIMADA/Petals. Needs the worker
environment.

These tests exist to pin the refusals as much as the calculations: heat has no impact
function and must stay null, and an unresolvable return period must not quietly become the
largest event's loss.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")

import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker.physical_risk import registry  # noqa: E402
from climaterisk_worker.physical_risk.engine import (  # noqa: E402
    calculate,
    climate_change_multiplier,
)
from scipy import sparse  # noqa: E402

from climaterisk.physical_risk.metrics import CalculationStatus, ModelId  # noqa: E402

LAT, LON = 37.5, 127.0
FACILITY = {
    "facility_id": "KR-RE-001",
    "facility_name": "Concordian",
    "latitude": LAT,
    "longitude": LON,
    "asset_value_usd": 100_000_000.0,
    "property_type": "office",
}
GLOBAL = ModelId.GLOBAL_BASELINE.value


def synth(haz_type: str, units: str, intensity: list[float], freq: list[float]) -> Hazard:
    """A hand-checkable hazard set at one centroid."""
    n = len(intensity)
    return Hazard(
        haz_type=haz_type,
        centroids=Centroids(lat=np.array([LAT]), lon=np.array([LON])),
        event_id=np.arange(1, n + 1),
        event_name=[f"e{i}" for i in range(n)],
        date=np.arange(730000, 730000 + n),
        frequency=np.array(freq, dtype=float),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(np.array(intensity, dtype=float).reshape(n, 1)),
        fraction=sparse.csr_matrix(np.ones((n, 1))),
        units=units,
    )


# --------------------------------------------------------------------------- #
# Registry — what CLIMADA actually ships                                       #
# --------------------------------------------------------------------------- #
def test_registry_is_read_off_live_objects_not_transcribed() -> None:
    reg = registry.build_registry("Asia")
    assert reg["environment"]["climada"].startswith("6.")
    rf = {e["sector"]: e for e in reg["by_hazard"]["RF"]}
    assert set(rf) == set(registry.JRC_SECTORS)
    res = rf["residential"]
    assert res["hazard_type"] == "RF"  # not "FL" — CLIMADA's own tag
    assert res["intensity_unit"] == "m"
    assert (res["intensity_min"], res["intensity_max"]) == (0.0, 12.0)
    assert res["n_points"] == 11
    assert res["paa_min"] == res["paa_max"] == 1.0  # the "noPAA" curves
    assert "Huizinga" in res["reference"]
    tc_names = {e["impact_function_name"] for e in reg["by_hazard"]["TC"]}
    assert "North West Pacific" in tc_names and "Emanuel 2011" in tc_names


def test_korea_tc_region_comes_from_climadas_country_table() -> None:
    """Not matched on the label 'North West Pacific' — looked up by ISO code."""
    assert registry.tc_region_for_country("KOR") == (9, "WP4", "North West Pacific")
    assert registry.tc_region_for_country("JPN") == (9, "WP4", "North West Pacific")
    assert registry.tc_region_for_country("CHN") == (8, "WP3", "China Mainland")


def test_no_heat_impact_function_exists_and_the_registry_says_so() -> None:
    heat = registry.heat_status()
    assert heat["available"] is False
    assert heat["status"] == "NO_IMPACT_FUNCTION"
    assert heat["heat_like_modules"] == []
    core = heat["modules_present"]["climada.entity.impact_funcs"]
    petals = heat["modules_present"]["climada_petals.entity.impact_funcs"]
    assert "trop_cyclone" in core and "river_flood" in petals
    assert not any("heat" in m for m in core + petals)


# --------------------------------------------------------------------------- #
# Flood                                                                        #
# --------------------------------------------------------------------------- #
def test_flood_produces_a_loss_from_the_published_jrc_curve() -> None:
    hazard = synth("RF", "m", [0.5, 1.0, 2.0, 4.0], [0.5, 0.2, 0.05, 0.01])
    row = calculate(
        FACILITY, hazard, "RF", model_id=GLOBAL, iso3="KOR", hazard_source="synthetic test set"
    )
    assert row.calculation_status == CalculationStatus.FULL.value
    assert row.impact_function_id == 22  # Asia / commercial, because property_type=office
    assert row.impact_function_name == "Flood Asia JRC Commercial noPAA"
    assert "from_jrc_region_sector" in row.impact_function_source
    assert row.eal_usd > 0 and row.potential_loss_usd > 0
    assert row.hazard_intensity == pytest.approx(4.0) and row.hazard_intensity_unit == "m"
    assert row.return_period_years == 100.0 and row.probability == pytest.approx(0.01)
    assert row.eal_as_pct_of_assets == pytest.approx(row.eal_usd / 1e8 * 100)
    assert row.risk_level in {"Low", "Medium", "High"}


def test_eal_equals_climadas_own_frequency_weighted_sum() -> None:
    """EAL is CLIMADA's aai_agg, not a re-implementation — verified against the identity."""
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet

    hazard = synth("RF", "m", [0.5, 1.0, 2.0, 4.0], [0.5, 0.2, 0.05, 0.01])
    func = registry.flood_impact_function("Asia", "commercial")
    exp = Exposures(
        pd.DataFrame({"latitude": [LAT], "longitude": [LON], "value": [1e8], "impf_RF": [func.id]}),
        value_unit="USD",
    )
    impact = ImpactCalc(exp, ImpactFuncSet([func]), hazard).impact(save_mat=False)
    identity = float((np.asarray(impact.at_event) * np.asarray(impact.frequency)).sum())
    assert impact.aai_agg == pytest.approx(identity)
    row = calculate(FACILITY, hazard, "RF", model_id=GLOBAL, iso3="KOR")
    assert row.eal_usd == pytest.approx(impact.aai_agg)


def test_sector_mapping_changes_which_published_curve_is_used() -> None:
    hazard = synth("RF", "m", [1.0, 2.0, 4.0], [0.2, 0.05, 0.01])
    ids = {}
    for prop, expect in (("residential", 21), ("office", 22), ("warehouse", 23)):
        row = calculate(
            {**FACILITY, "property_type": prop}, hazard, "RF", model_id=GLOBAL, iso3="KOR"
        )
        ids[prop] = row.impact_function_id
        assert row.impact_function_id == expect
    assert len(set(ids.values())) == 3


# --------------------------------------------------------------------------- #
# Tropical cyclone                                                             #
# --------------------------------------------------------------------------- #
def test_tc_uses_the_regional_function_climada_assigns_to_korea() -> None:
    hazard = synth("TC", "m/s", [20.0, 40.0, 60.0, 80.0], [0.5, 0.2, 0.05, 0.01])
    row = calculate(FACILITY, hazard, "TC", model_id=GLOBAL, iso3="KOR")
    assert row.calculation_status == CalculationStatus.FULL.value
    assert row.impact_function_id == 9
    assert row.impact_function_name == "North West Pacific"
    assert "WP4" in row.impact_function_source and "Eberenz" in row.impact_function_source
    assert row.eal_usd > 0 and row.hazard_intensity == pytest.approx(80.0)
    assert row.hazard_intensity_unit == "m/s"


def test_tc_without_a_country_falls_back_to_climadas_rest_of_the_world() -> None:
    hazard = synth("TC", "m/s", [40.0, 80.0], [0.1, 0.01])
    row = calculate(FACILITY, hazard, "TC", model_id=GLOBAL, iso3=None)
    assert row.impact_function_id == 10
    assert "Rest of The World" in row.impact_function_source


# --------------------------------------------------------------------------- #
# Heat — the refusal                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("haz_type", ["HM", "HW"])
def test_heat_is_hazard_only_with_null_financials_never_zero(haz_type: str) -> None:
    hazard = synth(haz_type, "degC-days", [10.0, 40.0, 90.0], [0.5, 0.2, 0.05])
    row = calculate(FACILITY, hazard, haz_type, model_id=ModelId.KOREA_LOCAL.value, iso3="KOR")
    assert row.calculation_status == CalculationStatus.HAZARD_ONLY.value
    for field in (
        "eal_usd",
        "potential_loss_usd",
        "eal_as_pct_of_assets",
        "risk_level",
        "risk_level_criteria",
        "climate_change_multiplier",
    ):
        assert getattr(row, field) is None, f"{field} must be null, not 0"
    # the hazard itself is still reported
    assert row.hazard_intensity == pytest.approx(90.0)
    assert row.hazard_intensity_unit == "degC-days"
    assert "no heat/heatwave impact function" in row.status_detail


# --------------------------------------------------------------------------- #
# Guards                                                                       #
# --------------------------------------------------------------------------- #
def test_an_unresolvable_return_period_is_refused_not_saturated() -> None:
    """calc_freq_curve returns the largest event's loss for any RP past the record."""
    hazard = synth("RF", "m", [0.5, 1.0, 2.0, 4.0], [0.1, 0.1, 0.1, 0.1])  # ceiling 10 yr
    row = calculate(FACILITY, hazard, "RF", model_id=GLOBAL, iso3="KOR")
    assert row.calculation_status == CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value
    assert row.potential_loss_usd is None
    assert "10.0-year ceiling" in row.status_detail
    # EAL needs no extrapolation, so it survives and stays usable
    assert row.eal_usd > 0 and row.risk_level is not None
    # and CLIMADA really would have saturated
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet

    func = registry.flood_impact_function("Asia", "commercial")
    exp = Exposures(
        pd.DataFrame({"latitude": [LAT], "longitude": [LON], "value": [1e8], "impf_RF": [func.id]}),
        value_unit="USD",
    )
    impact = ImpactCalc(exp, ImpactFuncSet([func]), hazard).impact(save_mat=False)
    curve = impact.calc_freq_curve([10.0, 100.0])
    assert curve.impact[0] == pytest.approx(curve.impact[1]), "saturation confirmed"


def test_missing_hazard_and_missing_asset_value_are_distinct_statuses() -> None:
    row = calculate(FACILITY, None, "RF", model_id=GLOBAL, iso3="KOR")
    assert row.calculation_status == CalculationStatus.NO_HAZARD_DATA.value
    assert row.eal_usd is None

    hazard = synth("RF", "m", [1.0, 4.0], [0.1, 0.01])
    for value in (0.0, None):
        row = calculate(
            {**FACILITY, "asset_value_usd": value}, hazard, "RF", model_id=GLOBAL, iso3="KOR"
        )
        assert row.calculation_status == CalculationStatus.NO_EXPOSURE_DATA.value
        assert row.eal_usd is None and row.risk_level is None
        # the impact function was still resolved, so provenance survives
        assert row.impact_function_id is not None


def test_climate_change_multiplier_only_from_two_real_runs() -> None:
    hazard_now = synth("TC", "m/s", [20.0, 40.0, 60.0, 80.0], [0.5, 0.2, 0.05, 0.01])
    hazard_fut = synth("TC", "m/s", [25.0, 50.0, 70.0, 95.0], [0.5, 0.2, 0.05, 0.01])
    base = calculate(
        FACILITY,
        hazard_now,
        "TC",
        model_id=GLOBAL,
        iso3="KOR",
        scenario="historical",
        time_horizon="present",
    )
    fut = calculate(
        FACILITY,
        hazard_fut,
        "TC",
        model_id=GLOBAL,
        iso3="KOR",
        scenario="rcp45",
        time_horizon="2040",
    )
    out = climate_change_multiplier(base, fut)
    assert out["climate_change_multiplier"] == pytest.approx(fut.eal_usd / base.eal_usd)
    assert out["baseline_scenario"] == "historical" and out["future_scenario"] == "rcp45"
    assert out["multiplier_definition"].startswith("future_EAL / baseline_EAL")

    heat = calculate(
        FACILITY, synth("HM", "degC-days", [10.0], [0.1]), "HM", model_id=GLOBAL, iso3="KOR"
    )
    blocked = climate_change_multiplier(heat, heat)
    assert blocked["climate_change_multiplier"] is None
    assert "no EAL" in blocked["detail"]


def test_legacy_and_direct_climada_flood_curves_differ_and_the_reason_is_the_curve() -> None:
    """The engine uses the published 11-point JRC curve; the legacy runner resamples 8.

    A difference is expected and must be attributable to the curve, not to hazard,
    exposure or frequency — those are identical here by construction.
    """
    import json

    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFunc, ImpactFuncSet

    hazard = synth("RF", "m", [0.5, 1.0, 2.0, 4.0], [0.5, 0.2, 0.05, 0.01])
    direct = registry.flood_impact_function("Asia", "residential")
    assert np.asarray(direct.intensity).size == 11

    presets = json.loads(
        (REPO / "assets" / "libraries" / "impact_function_presets.json").read_text()
    )
    items = presets.get("presets", presets)
    items = items if isinstance(items, list) else list(items.values())
    asia = next(p for p in items if p.get("id") == "flood_jrc_asia")
    depths = np.array(
        presets.get("_meta", {}).get("flood_depth_m") or [0, 0.5, 1, 2, 3, 4, 5, 6], dtype=float
    )
    mdr = np.array(asia["flood_mdr"], dtype=float)
    assert mdr.size == 8, "the legacy preset is the 8-point resample"
    legacy = ImpactFunc(
        haz_type="RF",
        id=99,
        intensity=depths,
        mdd=mdr,
        paa=np.ones_like(depths),
        intensity_unit="m",
        name="legacy 8-point",
    )

    exp = Exposures(
        pd.DataFrame({"latitude": [LAT], "longitude": [LON], "value": [1e8], "impf_RF": [0]}),
        value_unit="USD",
    )
    out = {}
    for name, func in (("direct", direct), ("legacy", legacy)):
        exp.gdf["impf_RF"] = func.id
        out[name] = ImpactCalc(exp, ImpactFuncSet([func]), hazard).impact(save_mat=False).aai_agg
    assert out["direct"] > 0 and out["legacy"] > 0
    # Same hazard, same exposure, same frequencies — any difference is the curve alone.
    assert np.asarray(direct.intensity).size != depths.size
