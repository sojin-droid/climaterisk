"""Phase 3 — model ids, readiness, scenario mismatch, change arithmetic, comparison, table.

CLIMADA-free. These pin the rules that keep a three-model comparison honest:

* exactly three canonical model ids, defined in one place;
* ``requested_scenario`` and ``served_scenario`` are separate and a mismatch is a status;
* percent (relative) and percentage points (difference) are never interchanged;
* a model-vs-model ratio is not a climate-change multiplier;
* a model that produced no numbers is "not available", never zero;
* the batch table, its filters and the five export frames share one schema.

Spec letters (the Phase 3 brief): H = scenario mismatch, I = zero baseline, J = pct vs pp,
K = multiplier vs source comparison, G = no fake KOREA_LOCAL results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk.engines.base import PhysicalRiskModelsRequest  # noqa: E402
from climaterisk.physical_risk import results_table as rt  # noqa: E402
from climaterisk.physical_risk.metrics import (  # noqa: E402
    MODEL_DEFINITIONS,
    NON_FINANCIAL_STATUSES,
    CalculationStatus,
    ModelId,
    ResultRow,
    change_pct,
    change_pp,
    climate_change_multiplier,
    compare_models,
    comparison_row,
    relabel_comparison,
)
from climaterisk.physical_risk.models import READINESS, cell_status, readiness  # noqa: E402
from climaterisk.runs.store import run_kind  # noqa: E402

G, C, K = ModelId.GLOBAL_BASELINE.value, ModelId.DATA_API_COUNTRY.value, ModelId.KOREA_LOCAL.value


def _row(**kw) -> ResultRow:  # type: ignore[no-untyped-def]
    base = {"facility_id": "F1", "facility_name": "Concordian", "hazard_type": "RF", "model_id": G}
    base.update(kw)
    return ResultRow(**base)  # type: ignore[arg-type]


def _priced(model: str, eal: float, pml: float | None = None, **kw) -> ResultRow:  # type: ignore[no-untyped-def]
    kw.setdefault("impact_function_id", 22)
    return _row(
        model_id=model,
        eal_usd=eal,
        potential_loss_usd=pml,
        asset_value_usd=100_000_000.0,
        return_period_years=100.0,
        calculation_status=CalculationStatus.FULL.value,
        **kw,
    ).finalise()


# --------------------------------------------------------------------------- #
# Model ids and definitions                                                    #
# --------------------------------------------------------------------------- #
def test_exactly_three_canonical_model_ids_and_no_stray_strings() -> None:
    assert [m.value for m in ModelId] == [G, C, K]
    assert set(MODEL_DEFINITIONS) == {G, C, K}
    # the wording the brief fixes
    assert "not treated as Korea-local source data" in MODEL_DEFINITIONS[C]
    assert "only when a domestic hazard dataset and compatible adapter" in MODEL_DEFINITIONS[K]
    # no other module spells a model id the enum does not know
    import re

    known = {m.value for m in ModelId}
    for path in (REPO / "src" / "climaterisk").rglob("*.py"):
        for found in re.findall(
            r'"([A-Z_]*(?:BASELINE|COUNTRY|LOCAL|HAZARD)[A-Z_]*)"', path.read_text()
        ):
            if found.endswith(("_BASELINE", "_COUNTRY", "_LOCAL")):
                assert found in known, f"{path.name} spells an unknown model id {found!r}"


def test_readiness_table_is_the_brief_adjusted_to_the_code() -> None:
    r = readiness()
    assert r["models"] == [G, C, K]
    assert r["summary"]["RF"] == {G: "READY", C: "READY", K: "NOT_IMPLEMENTED"}
    assert r["summary"]["TC"] == {G: "READY", C: "READY", K: "NOT_IMPLEMENTED"}
    # heat: the Data API has no heat type and the platform has no global heat layer, so the
    # two Data API cells are NO_HAZARD_DATA (not the brief's illustrative HAZARD_ONLY)
    assert r["summary"]["HEAT"] == {G: "NO_HAZARD_DATA", C: "NO_HAZARD_DATA", K: "HAZARD_ONLY"}
    assert cell_status("HW", K) == "HAZARD_ONLY" and cell_status("HM", C) == "NO_HAZARD_DATA"
    assert cell_status("XX", G) is None
    for cells in READINESS.values():
        for cell in cells.values():
            assert cell["status"] in {"READY", "HAZARD_ONLY", "NO_HAZARD_DATA", "NOT_IMPLEMENTED"}
            assert cell["hazard_source"] and cell["detail"]


# --------------------------------------------------------------------------- #
# Test H — requested vs served scenario                                        #
# --------------------------------------------------------------------------- #
def test_h_scenario_mismatch_nulls_the_financials_and_keeps_both_keys() -> None:
    row = _row(
        eal_usd=75_000.0,
        potential_loss_usd=7_200_000.0,
        asset_value_usd=1e8,
        requested_scenario="rcp45",
        served_scenario="rcp60",
        calculation_status=CalculationStatus.FULL.value,
    ).finalise()
    assert row.calculation_status == CalculationStatus.SCENARIO_MISMATCH.value
    assert row.requested_scenario == "rcp45" and row.served_scenario == "rcp60"
    assert row.scenario == "rcp45"  # the table column mirrors what was asked for
    assert row.eal_usd is None and row.potential_loss_usd is None and row.risk_level is None
    assert "served_scenario=rcp60" in (row.status_detail or "")


def test_h_matching_scenarios_stay_full_and_hazard_only_is_not_relabelled() -> None:
    ok = _row(
        eal_usd=1.0,
        asset_value_usd=1e8,
        requested_scenario="rcp60",
        served_scenario="rcp60",
        calculation_status="FULL",
    ).finalise()
    assert ok.calculation_status == "FULL" and ok.eal_usd == 1.0
    heat = _row(
        hazard_type="HW",
        requested_scenario="rcp45",
        served_scenario="rcp85",
        calculation_status=CalculationStatus.HAZARD_ONLY.value,
    ).finalise()
    assert heat.calculation_status == CalculationStatus.HAZARD_ONLY.value  # already unpriced


def test_scenario_mismatch_and_not_implemented_are_non_financial_statuses() -> None:
    assert CalculationStatus.SCENARIO_MISMATCH in NON_FINANCIAL_STATUSES
    assert CalculationStatus.NOT_IMPLEMENTED in NON_FINANCIAL_STATUSES
    row = _row(eal_usd=5.0, risk_level="High", calculation_status="NOT_IMPLEMENTED").finalise()
    assert row.eal_usd is None and row.risk_level is None


# --------------------------------------------------------------------------- #
# Tests I and J — change arithmetic                                            #
# --------------------------------------------------------------------------- #
def test_i_zero_or_missing_baseline_gives_null_never_infinity() -> None:
    assert change_pct(5.0, 0.0) is None
    assert change_pct(0.0, 0.0) is None
    assert change_pct(5.0, None) is None
    assert change_pct(None, 5.0) is None
    assert change_pct(0.0, 5.0) == pytest.approx(-100.0)
    assert change_pp(5.0, None) is None and change_pp(None, 5.0) is None


def test_j_percentage_points_and_relative_percent_are_different_numbers() -> None:
    base_pct, local_pct = 0.61, 0.77
    assert change_pp(local_pct, base_pct) == pytest.approx(0.16)
    assert change_pct(local_pct, base_pct) == pytest.approx((0.77 / 0.61 - 1) * 100)
    assert change_pp(local_pct, base_pct) != pytest.approx(change_pct(local_pct, base_pct))
    base = _priced(G, 610_000.0, 7_200_000.0)
    local = _priced(C, 770_000.0, 8_600_000.0)
    cmp = comparison_row(base, local)
    assert cmp["baseline_eal_as_pct_of_assets"] == pytest.approx(0.61)
    assert cmp["comparison_eal_as_pct_of_assets"] == pytest.approx(0.77)
    assert cmp["eal_as_pct_assets_change_pp"] == pytest.approx(0.16)
    assert cmp["eal_as_pct_assets_relative_change_pct"] == pytest.approx(26.229508, rel=1e-6)
    assert cmp["eal_change_usd"] == pytest.approx(160_000.0)
    assert cmp["eal_change_pct"] == pytest.approx(26.229508, rel=1e-6)
    assert cmp["potential_loss_change_pct"] == pytest.approx(19.444444, rel=1e-6)
    assert cmp["baseline_risk_level"] == "High" and cmp["comparison_risk_level"] == "High"
    assert cmp["same_impact_function"] is True and cmp["impact_function_id"] == 22


# --------------------------------------------------------------------------- #
# Test K — a model comparison is not a climate-change multiplier               #
# --------------------------------------------------------------------------- #
def test_k_multiplier_refuses_cross_model_rows_and_same_period_rows() -> None:
    base = _priced(G, 100.0, scenario="historical", time_horizon="present")
    country = _priced(C, 150.0, scenario="historical", time_horizon="present")
    out = climate_change_multiplier(base, country)
    assert out["climate_change_multiplier"] is None
    assert "hazard-source comparison" in out["detail"] and out["model_id"] is None

    same = _priced(G, 150.0, scenario="historical", time_horizon="present")
    assert climate_change_multiplier(base, same)["climate_change_multiplier"] is None

    future = _priced(G, 150.0, scenario="rcp45", time_horizon="2040")
    ok = climate_change_multiplier(base, future)
    assert ok["climate_change_multiplier"] == pytest.approx(1.5) and ok["model_id"] == G
    assert ok["baseline_scenario"] == "historical" and ok["future_scenario"] == "rcp45"

    zero = _priced(G, 0.0, scenario="historical", time_horizon="present")
    assert "zero" in climate_change_multiplier(zero, future)["detail"]
    # and the comparison row says what it is
    assert "not a climate-change multiplier" in comparison_row(base, country)["comparison_kind"]


# --------------------------------------------------------------------------- #
# Test G — KOREA_LOCAL without an adapter yields nothing that looks like a result  #
# --------------------------------------------------------------------------- #
def test_g_missing_or_unimplemented_korea_local_is_not_available_not_zero() -> None:
    rows = [
        _priced(G, 75_000.0, 7_200_000.0),
        _priced(C, 75_000.0, 7_200_000.0),
        _row(
            model_id=K, calculation_status="NOT_IMPLEMENTED", status_detail="no adapter"
        ).finalise(),
    ]
    assert compare_models(rows, G, K, "F1", "RF") is not None  # both rows exist ...
    frame = rt.comparison_frame(rows, G, K, "korea_local")
    assert len(frame) == 1 and frame[0]["available"] is False  # ... but nothing was priced
    assert "NOT_IMPLEMENTED" in frame[0]["detail"]
    assert frame[0].get("korea_local_eal_usd") is None
    assert compare_models(rows, G, K, "F1", "TC") is None  # no rows at all -> None
    gvc = rt.comparison_frame(rows, G, C, "country")
    assert gvc[0]["available"] is True and gvc[0]["eal_change_pct"] == pytest.approx(0.0)
    assert "country_eal_usd" in gvc[0] and "comparison_eal_usd" not in gvc[0]


def test_relabel_only_renames_comparison_keys() -> None:
    cmp = comparison_row(_priced(G, 1.0), _priced(C, 2.0))
    out = relabel_comparison(cmp, "country")
    assert set(out) == {
        ("country_" + k[len("comparison_") :] if k.startswith("comparison_") else k) for k in cmp
    }
    assert out["country_eal_usd"] == 2.0 and out["baseline_eal_usd"] == 1.0


def test_comparison_refuses_mismatched_facility_or_hazard() -> None:
    with pytest.raises(ValueError, match="cannot compare"):
        comparison_row(_priced(G, 1.0), _priced(C, 1.0, hazard_type="TC"))


# --------------------------------------------------------------------------- #
# Batch table, filters, export frames                                          #
# --------------------------------------------------------------------------- #
def _portfolio_rows() -> list[ResultRow]:
    return [
        _priced(G, 75_000.0, 7_200_000.0, scenario="rcp60", time_horizon="2030–2050"),
        _priced(C, 75_000.0, 7_200_000.0, scenario="rcp60", time_horizon="2030–2050"),
        _row(model_id=K, scenario="rcp60", calculation_status="NOT_IMPLEMENTED").finalise(),
        _priced(G, 3_576.0, 121_636.0, hazard_type="TC", scenario="rcp60", impact_function_id=9),
        _priced(C, 3_576.0, 121_636.0, hazard_type="TC", scenario="rcp60", impact_function_id=9),
        _row(
            hazard_type="HW",
            model_id=K,
            scenario="rcp60",
            hazard_intensity=31.9,
            calculation_status="HAZARD_ONLY",
        ).finalise(),
        _row(
            hazard_type="HW", model_id=G, scenario="rcp60", calculation_status="NO_HAZARD_DATA"
        ).finalise(),
    ]


def test_batch_table_has_the_canonical_columns_in_order_and_filters() -> None:
    rows = _portfolio_rows()
    table = rt.table_rows(rows)
    assert list(table[0]) == list(rt.CANONICAL_COLUMNS)
    assert rt.CANONICAL_COLUMNS[:4] == ("facility_id", "facility_name", "hazard_type", "model_id")
    assert rt.CANONICAL_COLUMNS[-1] == "calculation_status"
    assert len(rt.filter_rows(rows, hazard="RF")) == 3
    assert len(rt.filter_rows(rows, model=K)) == 2
    assert len(rt.filter_rows(rows, risk_level="Low")) == 4  # only priced rows have a band
    assert len(rt.filter_rows(rows, scenario="rcp60")) == 7
    assert len(rt.filter_rows(rows, hazard=["RF", "TC"], model=G)) == 2
    # dict input (worker JSON) is accepted too
    assert len(rt.filter_rows([r.to_dict() for r in rows], hazard="HW")) == 2


def test_export_frames_have_the_five_sheets_and_say_what_was_not_priced() -> None:
    frames = rt.export_frames(_portfolio_rows())
    assert {
        "hazard_results",
        "asset_summary",
        "global_vs_country",
        "global_vs_korea_local",
        "methodology",
        "portfolio_summary",
        "asset_risk_matrix",
    } <= set(frames)
    hr = frames["hazard_results"]
    assert list(hr[0]) == list(rt.HAZARD_RESULTS_COLUMNS)
    assert {"requested_scenario", "served_scenario", "hazard_dataset", "exposure_version"} <= set(
        hr[0]
    )
    summary = {(s["facility_id"], s["model_id"]): s for s in frames["asset_summary"]}
    g = summary[("F1", G)]
    assert g["eal_usd"] == pytest.approx(78_576.0) and g["priced_hazards"] == "RF,TC"
    assert "HW:NO_HAZARD_DATA" in g["unpriced_hazards"]
    k = summary[("F1", K)]
    assert k["eal_usd"] is None and k["max_risk_level"] is None
    assert (
        "RF:NOT_IMPLEMENTED" in k["unpriced_hazards"] and "HW:HAZARD_ONLY" in k["unpriced_hazards"]
    )
    gvk = {c["hazard_type"]: c for c in frames["global_vs_korea_local"]}
    assert all(c["available"] is False for c in gvk.values())
    meth = {m["key"]: m["value"] for m in frames["methodology"]}
    assert "not a climate multiplier" in meth["climate_change_multiplier"]
    assert "percentage points" in meth["change_pp"]
    assert "NOT an official GRESB threshold" in meth["risk_levels"]
    assert "population is never used" in meth["exposure"]


# --------------------------------------------------------------------------- #
# Wiring — request model and run kind                                          #
# --------------------------------------------------------------------------- #
def test_request_maps_assets_to_facilities_and_run_kind_is_a_sentinel() -> None:
    from climaterisk.core.entities import Asset, Portfolio

    pf = Portfolio(
        assets=[
            Asset(
                name="Concordian",
                lat=37.5,
                lon=127.0,
                value=1e8,
                properties={"property_type": "office"},
            )
        ]
    )
    req = PhysicalRiskModelsRequest.from_portfolio(pf)
    assert req.mode == "physical_risk_models"
    assert req.models == [G, C, K] and req.hazards == ["RF", "TC", "HEAT"]
    f = req.facilities[0]
    assert (f.latitude, f.longitude, f.asset_value_usd, f.property_type) == (
        37.5,
        127.0,
        1e8,
        "office",
    )
    assert req.target_year == max(pf.scenario.anchor_years)
    zero = Portfolio(assets=[Asset(name="x", lat=0.0, lon=0.0, value=0.0)])
    assert PhysicalRiskModelsRequest.from_portfolio(zero).facilities[0].asset_value_usd is None
    assert run_kind(["physical_risk_models"]) == "physical_risk_models"


def test_readiness_route_and_table_route_exist(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/libraries/physical-risk-models")
    assert r.status_code == 200 and r.json()["summary"]["RF"][K] == "NOT_IMPLEMENTED"
    r = client.get("/api/session/nope/run/nope/physical-risk-table")
    assert r.status_code == 404


def test_korea_local_flood_stays_unimplemented_while_the_licence_blocks_it() -> None:
    """Phase 4 investigation: access is open, the licence is 공공누리 제4유형 — no adapter data."""
    doc = (REPO / "docs" / "KOREA_FLOODMAP_INVESTIGATION.md").read_text(encoding="utf-8")
    assert "제4유형" in doc and "상업적 이용금지" in doc and "변경금지" in doc
    assert "NOT_IMPLEMENTED" in doc and "구현은 하지 않았다" in doc
    cell = READINESS["RF"][K]
    assert cell["status"] == "NOT_IMPLEMENTED"
    assert "제4유형" in cell["detail"] and "KOREA_FLOODMAP_INVESTIGATION" in cell["detail"]
