"""Physical-risk metrics — thresholds, ratios, frequency semantics, comparison.

CLIMADA-free. These police the rules that keep a result honest: a number that cannot be
computed is null rather than zero, the risk bands come from configuration rather than the
code, and probability and return period are two views of one quantity.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk.physical_risk.metrics import (  # noqa: E402
    CalculationStatus,
    ModelId,
    ResultRow,
    change_abs,
    change_pct,
    comparison_row,
    config_path,
    eal_as_pct_of_assets,
    jrc_sector_for,
    load_config,
    max_resolvable_return_period,
    probability_from_return_period,
    return_period_from_probability,
    risk_level,
)


def test_risk_bands_come_from_config_not_from_code() -> None:
    """The band edges must exist only in the config — prose explaining them is fine."""
    import ast

    cfg = load_config()
    tree = ast.parse((REPO / "src" / "climaterisk" / "physical_risk" / "metrics.py").read_text())
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }
    assert 0.10 not in literals and 0.50 not in literals, (
        f"threshold literals leaked into the code: {sorted(literals)}"
    )
    bands = cfg["risk_levels"]["bands"]
    assert [b["level"] for b in bands] == ["Low", "Medium", "High"]
    # the attribution must keep saying this is ours, not GRESB's
    assert "NOT an official GRESB threshold" in cfg["risk_levels"]["attribution"]


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (0.0, "Low"),
        (0.09, "Low"),
        (0.0999, "Low"),
        (0.10, "Medium"),
        (0.49, "Medium"),
        (0.4999, "Medium"),
        (0.50, "High"),
        (5.0, "High"),
    ],
)
def test_risk_level_boundaries(pct: float, expected: str) -> None:
    level, criteria = risk_level(pct)
    assert level == expected and criteria


def test_risk_level_of_an_uncomputable_ratio_is_null_not_low() -> None:
    assert risk_level(None) == (None, None)


@pytest.mark.parametrize(
    ("eal", "value", "expected"),
    [
        (1_000.0, 1_000_000.0, 0.1),
        (0.0, 1_000_000.0, 0.0),
        (1_000.0, 0.0, None),
        (1_000.0, None, None),
        (None, 1_000_000.0, None),
        (1_000.0, -5.0, None),
    ],
)
def test_eal_as_pct_handles_zero_and_missing_asset_value(eal, value, expected) -> None:
    got = eal_as_pct_of_assets(eal, value)
    assert got == expected if expected is None else got == pytest.approx(expected)


def test_probability_and_return_period_are_one_quantity() -> None:
    for rp in (2.0, 10.0, 50.0, 100.0, 250.0):
        p = probability_from_return_period(rp)
        assert p == pytest.approx(1.0 / rp)
        assert return_period_from_probability(p) == pytest.approx(rp)
    for bad in (None, 0.0, -1.0):
        assert probability_from_return_period(bad) is None
        assert return_period_from_probability(bad) is None


def test_max_resolvable_return_period_is_one_over_the_rarest_event() -> None:
    """CLIMADA saturates past the record instead of returning NaN — this is the guard."""
    assert max_resolvable_return_period([0.1, 0.1, 0.1, 0.1]) == pytest.approx(10.0)
    assert max_resolvable_return_period([0.5, 0.2, 0.05, 0.01]) == pytest.approx(100.0)
    assert max_resolvable_return_period([0.0, 0.0]) is None
    assert max_resolvable_return_period([]) is None


def test_change_pct_refuses_a_zero_baseline_rather_than_hiding_it() -> None:
    assert change_pct(120.0, 100.0) == pytest.approx(20.0)
    assert change_pct(80.0, 100.0) == pytest.approx(-20.0)
    assert change_pct(5.0, 0.0) is None
    assert change_pct(None, 100.0) is None
    assert change_abs(120.0, 100.0) == pytest.approx(20.0)
    assert change_abs(None, 100.0) is None


def _row(**kw) -> ResultRow:  # type: ignore[no-untyped-def]
    base = {
        "facility_id": "F1",
        "facility_name": "Concordian",
        "hazard_type": "RF",
        "model_id": ModelId.GLOBAL_BASELINE.value,
    }
    base.update(kw)
    return ResultRow(**base)  # type: ignore[arg-type]


def test_finalise_derives_the_dependent_fields() -> None:
    row = _row(
        eal_usd=500_000.0,
        asset_value_usd=100_000_000.0,
        return_period_years=100.0,
        calculation_status=CalculationStatus.FULL.value,
    ).finalise()
    assert row.eal_as_pct_of_assets == pytest.approx(0.5)
    assert row.risk_level == "High"
    assert row.probability == pytest.approx(0.01)


@pytest.mark.parametrize("status", sorted(s.value for s in CalculationStatus if s.value != "FULL"))
def test_a_non_full_status_can_never_carry_financial_numbers(status: str) -> None:
    """The core rule: N/A is not zero, and a stale number cannot survive finalise()."""
    if status == CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value:
        pytest.skip("that status keeps EAL by design — covered in the engine tests")
    row = _row(
        eal_usd=1.0,
        potential_loss_usd=2.0,
        eal_as_pct_of_assets=3.0,
        risk_level="High",
        risk_level_criteria="x",
        climate_change_multiplier=1.7,
        asset_value_usd=100.0,
        calculation_status=status,
    ).finalise()
    assert row.eal_usd is None and row.potential_loss_usd is None
    assert row.eal_as_pct_of_assets is None and row.risk_level is None
    assert row.climate_change_multiplier is None
    assert row.calculation_status == status


def test_comparison_row_computes_both_change_forms_and_refuses_mismatched_rows() -> None:
    base = _row(
        eal_usd=610_000.0,
        potential_loss_usd=7_200_000.0,
        hazard_intensity=1.05,
        calculation_status="FULL",
    ).finalise()
    local = _row(
        model_id=ModelId.DATA_API_COUNTRY.value,
        eal_usd=770_000.0,
        potential_loss_usd=8_600_000.0,
        hazard_intensity=1.32,
        calculation_status="FULL",
    ).finalise()
    cmp = comparison_row(base, local)
    assert cmp["eal_change_usd"] == pytest.approx(160_000.0)
    assert cmp["eal_change_pct"] == pytest.approx(26.229508, rel=1e-6)
    assert cmp["potential_loss_change_pct"] == pytest.approx(19.444444, rel=1e-6)
    assert cmp["hazard_intensity_change_pct"] == pytest.approx(25.714286, rel=1e-6)
    assert cmp["baseline_model_id"] == "GLOBAL_BASELINE"
    assert cmp["comparison_model_id"] == "DATA_API_COUNTRY"
    with pytest.raises(ValueError, match="cannot compare"):
        comparison_row(base, _row(hazard_type="TC"))


def test_sector_mapping_is_configuration_and_falls_back_rather_than_guessing() -> None:
    assert jrc_sector_for("office") == "commercial"
    assert jrc_sector_for("warehouse") == "industrial"
    assert jrc_sector_for("residential") == "residential"
    assert jrc_sector_for("OFFICE") == "commercial"  # case-insensitive
    assert jrc_sector_for(None) == load_config()["property_type_to_jrc_sector"]["default"]
    assert jrc_sector_for("something_unheard_of") == "commercial"
    mapping = json.loads(config_path().read_text(encoding="utf-8"))["property_type_to_jrc_sector"]
    assert "platform choice" in mapping["_note"]
