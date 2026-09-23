"""Phase 7 — asset-level validation, the CLIMADA-free half: the recorded evidence must obey
every rule the engine promises, and the Excel built from it must render it faithfully.

The evidence file ``tests/fixtures/asset_level_validation/run_rcp85_2040_recommended.json``
was produced by the worker on 2026-09-24 from the fixture portfolio (five real Korean sites,
chosen from the hazard data itself — see ``docs/asset-level-validation.md``). This test
does not recompute anything; ``tests/test_asset_level_validation.py`` (worker env) does,
and compares against this file.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk.physical_risk import excel_export as xl  # noqa: E402
from climaterisk.physical_risk.display_copy import recommended_models  # noqa: E402
from climaterisk.physical_risk.metrics import ResultRow, load_config, risk_level  # noqa: E402
from climaterisk.physical_risk.results_table import (  # noqa: E402
    asset_risk_matrix_frame,
    portfolio_summary_frame,
    primary_rows,
    summary_counts,
)

FIX = REPO / "tests" / "fixtures" / "asset_level_validation"
EVIDENCE = FIX / "run_rcp85_2040_recommended.json"
PORTFOLIO = FIX / "validation_portfolio.csv"

A, B, C, D, E = "VAL-A-GWANGJU", "VAL-B-SEOUL", "VAL-C-BUSAN", "VAL-D-CHUNCHEON", "VAL-E-INCHEON"
COUNTRY, LOCAL = "DATA_API_COUNTRY", "KOREA_LOCAL"


@pytest.fixture(scope="module")
def evidence() -> dict:  # type: ignore[type-arg]
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(evidence) -> dict[tuple[str, str, str], ResultRow]:  # type: ignore[no-untyped-def]
    out = {}
    for r in evidence["rows"]:
        row = ResultRow.from_dict(r)
        out[(row.facility_id, row.hazard_type, row.model_id)] = row
    return out


def test_fixture_portfolio_has_the_five_cases_and_matches_the_evidence(evidence) -> None:  # type: ignore[no-untyped-def]
    with PORTFOLIO.open(encoding="utf-8") as fh:
        facs = list(csv.DictReader(fh))
    assert [f["facility_id"] for f in facs] == [A, B, C, D, E]
    assert all(f["case"] for f in facs)
    assert {r["facility_id"] for r in evidence["rows"]} == {A, B, C, D, E}
    assert evidence["climate_scenario"] == "rcp85" and evidence["target_year"] == 2040
    # batch shape: 5 facilities x 3 hazards x 2 recommended models, one row each, no repeats
    assert len(evidence["rows"]) == 5 * 3 * 2
    keys = [(r["facility_id"], r["hazard_type"], r["model_id"]) for r in evidence["rows"]]
    assert len(set(keys)) == len(keys)
    # Recommended mode: readiness decided the models, and Global was never touched
    assert {r["model_id"] for r in evidence["rows"]} == {COUNTRY, LOCAL}
    assert recommended_models() == {"RF": COUNTRY, "TC": COUNTRY, "HEAT": LOCAL}
    assert not any(a["model_id"] == "GLOBAL_BASELINE" for a in evidence["adapters"])
    assert not any("global" in str(a.get("hazard_dataset")) for a in evidence["adapters"])


def test_a_positive_flood_loss_is_a_full_row_with_every_link_of_the_chain(rows) -> None:  # type: ignore[no-untyped-def]
    r = rows[(A, "RF", COUNTRY)]
    assert r.calculation_status == "FULL"
    assert r.hazard_intensity is not None and r.hazard_intensity > 0
    assert r.potential_loss_usd is not None and r.potential_loss_usd > 0
    assert r.eal_usd is not None and r.eal_usd > 0
    assert r.eal_as_pct_of_assets == pytest.approx(r.eal_usd / r.asset_value_usd * 100)  # type: ignore[operator]
    assert r.risk_level == risk_level(r.eal_as_pct_of_assets)[0] == "High"
    assert (
        r.impact_function_id == 22 and r.impact_function_name == "Flood Asia JRC Commercial noPAA"
    )
    assert "from_jrc_region_sector" in (r.impact_function_source or "")
    assert r.hazard_dataset == "river_flood_150arcsec_rcp85_KOR_2030_2050"
    assert r.requested_scenario == r.served_scenario == "rcp85" and r.time_horizon == "2030–2050"
    assert r.return_period_years == 100.0 and r.probability == pytest.approx(0.01)


def test_b_zero_flood_loss_is_a_computed_zero_not_a_missing_value(rows) -> None:  # type: ignore[no-untyped-def]
    r = rows[(B, "RF", COUNTRY)]
    assert (
        r.calculation_status == "FULL"
    )  # distinct from NO_HAZARD_DATA / NOT_IMPLEMENTED / NO_IMPACT_FUNCTION
    assert r.hazard_intensity == 0.0 and r.potential_loss_usd == 0.0 and r.eal_usd == 0.0
    assert r.eal_as_pct_of_assets == 0.0 and r.risk_level == "Low"
    assert r.impact_function_id == 22  # the function was resolved; the hazard was simply dry


def test_c_and_d_tc_losses_are_connected_and_ordered_by_modeled_wind(rows) -> None:  # type: ignore[no-untyped-def]
    c, d = rows[(C, "TC", COUNTRY)], rows[(D, "TC", COUNTRY)]
    for r in (c, d):
        assert r.calculation_status == "FULL" and r.eal_usd is not None and r.eal_usd > 0
        assert r.impact_function_id == 9 and r.impact_function_name == "North West Pacific"
        assert "Eberenz" in (r.impact_function_source or "") and "WP4" in (
            r.impact_function_source or ""
        )
        assert r.hazard_dataset == "tropical_cyclone_10synth_tracks_150arcsec_rcp85_KOR_2040"
        assert r.hazard_source and r.scenario == "rcp85" and r.time_horizon == "2040"
        assert r.risk_level == risk_level(r.eal_as_pct_of_assets)[0]
    assert c.hazard_intensity > d.hazard_intensity  # type: ignore[operator]
    assert c.eal_usd > 10 * d.eal_usd  # type: ignore[operator]
    assert d.eal_as_pct_of_assets < 0.01  # type: ignore[operator]  # "low": a hundredth of the Low band
    # no Korean cell is wind-free in this product — D is low, not zero, and the doc says so
    assert d.eal_usd > 0  # type: ignore[operator]


def test_e_heatwave_is_hazard_only_with_intensity_and_no_money_under_any_model(rows) -> None:  # type: ignore[no-untyped-def]
    for fid in (A, B, C, D, E):
        local = rows[(fid, "HW", LOCAL)]
        assert local.calculation_status == "HAZARD_ONLY"
        assert local.hazard_intensity is not None and 30.0 < local.hazard_intensity < 45.0
        assert local.hazard_dataset == "heatwave/HW_rcp85_KOR_2030.hdf5"
        assert "TAMAX" in (local.hazard_data_version or "")
        for f in ("eal_usd", "potential_loss_usd", "eal_as_pct_of_assets", "risk_level"):
            assert getattr(local, f) is None, f
        country = rows[(fid, "HW", COUNTRY)]
        assert country.calculation_status == "NO_HAZARD_DATA" and country.eal_usd is None


def test_korea_local_flood_and_tc_stay_not_implemented_with_no_numbers(rows) -> None:  # type: ignore[no-untyped-def]
    for fid in (A, B, C, D, E):
        for haz in ("RF", "TC"):
            r = rows[(fid, haz, LOCAL)]
            assert r.calculation_status == "NOT_IMPLEMENTED"
            assert r.eal_usd is None and r.potential_loss_usd is None and r.hazard_intensity is None


def test_risk_levels_in_the_evidence_follow_the_configured_thresholds_exactly(rows) -> None:  # type: ignore[no-untyped-def]
    bands = {b["level"]: b for b in load_config()["risk_levels"]["bands"]}
    assert (bands["Low"]["max_pct"], bands["Medium"]["max_pct"]) == (0.10, 0.50)
    seen = set()
    for r in rows.values():
        if r.eal_as_pct_of_assets is None:
            assert r.risk_level is None
            continue
        assert r.risk_level == risk_level(r.eal_as_pct_of_assets)[0]
        seen.add(r.risk_level)
    assert seen == {"Low", "Medium", "High"}  # the real portfolio spans all three bands
    # the exact edges, as the engine applies them
    assert risk_level(0.0999)[0] == "Low" and risk_level(0.10)[0] == "Medium"
    assert risk_level(0.4999)[0] == "Medium" and risk_level(0.50)[0] == "High"


def test_non_expert_views_read_the_evidence_the_way_the_ui_does(evidence) -> None:  # type: ignore[no-untyped-def]
    rs = [ResultRow.from_dict(r) for r in evidence["rows"]]
    prim = primary_rows(rs, recommended_models())
    assert prim[(A, "RF")].model_id == COUNTRY and prim[(A, "HW")].model_id == LOCAL
    matrix = {m["Facility ID"]: m for m in asset_risk_matrix_frame(rs, recommended_models())}
    assert (
        matrix[A]["Flood"] == "High"
        and matrix[D]["Flood"] == "Medium"
        and matrix[B]["Flood"] == "Low"
    )
    assert all(m["Heatwave"] == "Hazard only" for m in matrix.values())
    counts = summary_counts(rs, recommended_models())
    assert (
        counts["assets_analyzed"] == 5 and counts["high_risk"] == 1 and counts["medium_risk"] == 1
    )
    assert counts["low_risk"] == 8 and counts["hazard_only"] == 5 and counts["not_available"] == 0
    summary = {s["Facility ID"]: s for s in portfolio_summary_frame(rs, recommended_models())}
    assert summary[B]["Flood EAL"] == 0.0 and summary[B]["Flood Status"] == "Calculated"
    assert summary[E]["Heatwave Status"] == "Hazard only" and "Heatwave EAL" not in summary[E]


def test_excel_from_the_evidence_keeps_zero_blank_and_status_apart(evidence) -> None:  # type: ignore[no-untyped-def]
    wb = load_workbook(io.BytesIO(xl.workbook_bytes(evidence)))
    assert wb.sheetnames == [
        "Portfolio Summary",
        "Asset Risk Matrix",
        "Hazard Results",
        "Global vs Country",
        "Global vs Korea Local",
        "Methodology",
        "Run Info",
    ]  # no Climate Change sheet: no baseline scenario was run
    ws = wb["Hazard Results"]
    it = ws.iter_rows(values_only=True)
    header = list(next(it))
    recs = [dict(zip(header, r, strict=True)) for r in it]
    by = {(r["facility_id"], r["hazard_type"], r["model_id"]): r for r in recs}
    zero = by[(B, "RF", COUNTRY)]
    assert (
        zero["eal_usd"] == 0
        and zero["potential_loss_usd"] == 0
        and zero["calculation_status"] == "FULL"
    )
    heat = by[(E, "HW", LOCAL)]
    assert (
        heat["eal_usd"] is None
        and heat["risk_level"] is None
        and heat["calculation_status"] == "HAZARD_ONLY"
    )
    assert heat["hazard_intensity"] is not None
    local = by[(A, "RF", LOCAL)]
    assert local["eal_usd"] is None and local["calculation_status"] == "NOT_IMPLEMENTED"
    pos = by[(A, "RF", COUNTRY)]
    assert pos["eal_usd"] > 0 and pos["risk_level"] == "High" and pos["impact_function_id"] == 22
    ps = wb["Portfolio Summary"]
    ph = [c.value for c in ps[1]]
    line = {
        ps.cell(row=i, column=ph.index("Facility ID") + 1).value: i
        for i in range(2, ps.max_row + 1)
    }
    assert ps.cell(row=line[B], column=ph.index("Flood EAL") + 1).value == 0
    assert ps.cell(row=line[A], column=ph.index("Flood Risk") + 1).value == "High"
    assert ps.cell(row=line[E], column=ph.index("Heatwave Status") + 1).value == "Hazard only"
