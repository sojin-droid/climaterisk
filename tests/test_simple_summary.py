"""Simple-mode summary: sums, ratios and lookups over a run's own rows and curves.

Hand-checkable synthetic runs (four facilities in two 시도) pin the arithmetic; the route test
pins the API contract. The real 1,000-building run reproduces the workbook figures (flood EAL
$6,146,027, multiplier 2.505, top-1 share 36.2 %) — checked when this module was written,
not here, because that run output is not a repository fixture.
"""

from __future__ import annotations

from typing import Any

import pytest

from climaterisk.physical_risk.simple_summary import build_simple_summary

C = "DATA_API_COUNTRY"
FX = 1422.22


def _row(
    fid: str, hz: str, eal: float, *, value: float, prov: str, level: str = "Low", **kw: Any
) -> dict[str, Any]:
    return {
        "facility_id": fid,
        "facility_name": f"{fid} name",
        "hazard_type": hz,
        "model_id": C,
        "eal_usd": eal,
        "asset_value_usd": value,
        "province_name": prov,
        "municipality_name": f"{prov} 시군구",
        "property_type": "residential",
        "calculation_status": "FULL",
        "risk_level": level,
        **kw,
    }


def _curve(hz: str, losses: dict[int, float]) -> dict[str, Any]:
    return {
        "hazard_type": hz,
        "model_id": C,
        "group_level": "PORTFOLIO",
        "group_name": None,
        "losses": [{"return_period_years": rp, "loss": v} for rp, v in losses.items()],
    }


def _output(baseline: bool = True, refused: bool = False) -> dict[str, Any]:
    # A: big flooded asset (up), B: decreases, C: new flood, D: no flood at all
    rows = [
        _row(
            "A",
            "RF",
            600.0,
            value=100_000.0,
            prov="경기도",
            level="High",
            flood_protection_return_period_years=50.0,
            eal_undefended_usd=900.0,
            levee_protected=False,
        ),
        _row(
            "B",
            "RF",
            80.0,
            value=50_000.0,
            prov="경기도",
            level="Medium",
            flood_protection_return_period_years=100.0,
            eal_undefended_usd=80.0,
            levee_protected=True,
        ),
        _row(
            "C",
            "RF",
            20.0,
            value=40_000.0,
            prov="부산광역시",
            flood_protection_return_period_years=50.0,
            eal_undefended_usd=20.0,
            levee_protected=False,
        ),
        _row(
            "D",
            "RF",
            0.0,
            value=10_000.0,
            prov="부산광역시",
            flood_protection_return_period_years=50.0,
            eal_undefended_usd=0.0,
            levee_protected=False,
        ),
        _row("A", "TC", 10.0, value=100_000.0, prov="경기도"),
        _row("B", "TC", 5.0, value=50_000.0, prov="경기도"),
        _row("C", "TC", 4.0, value=40_000.0, prov="부산광역시"),
        _row("D", "TC", 1.0, value=10_000.0, prov="부산광역시"),
    ]
    base = [
        _row("A", "RF", 200.0, value=100_000.0, prov="경기도"),
        _row("B", "RF", 100.0, value=50_000.0, prov="경기도"),
        _row("C", "RF", 0.0, value=40_000.0, prov="부산광역시"),
        _row("D", "RF", 0.0, value=10_000.0, prov="부산광역시"),
        _row("A", "TC", 8.0, value=100_000.0, prov="경기도"),
        _row("B", "TC", 4.0, value=50_000.0, prov="경기도"),
        _row("C", "TC", 4.0, value=40_000.0, prov="부산광역시"),
        _row("D", "TC", 1.0, value=10_000.0, prov="부산광역시"),
    ]
    mults = (
        [
            {
                "hazard_type": "RF",
                "climate_change_multiplier": None,
                "detail": "baseline set is X, future set is Y: mixes the model-chain change",
            }
        ]
        if refused
        else []
    )
    return {
        "climate_scenario": "rcp85",
        "target_year": 2040,
        "baseline_scenario": "present" if baseline else None,
        "assessment_target": "FACILITY",
        "rows": rows,
        "baseline_rows": base if baseline else [],
        "portfolio_loss_curves": [
            _curve("RF", {10: 0.0, 100: 5_000.0}),
            _curve("TC", {10: 30.0, 100: 90.0}),
        ],
        "baseline_portfolio_loss_curves": [
            _curve("RF", {10: 0.0, 100: 2_000.0}),
            _curve("TC", {10: 25.0, 100: 70.0}),
        ]
        if baseline
        else [],
        "climate_change_multipliers": mults,
    }


def test_portfolio_figures_are_sums_and_ratios() -> None:
    s = build_simple_summary(_output(), krw_per_usd=FX)
    assert s["portfolio_value_usd"] == 200_000.0
    rf, tc = s["hazards"]["RF"], s["hazards"]["TC"]
    assert rf["eal_usd"] == 700.0 and rf["baseline_eal_usd"] == 300.0
    assert rf["climate_multiplier"] == pytest.approx(700.0 / 300.0, rel=1e-12)
    assert rf["eal_pct_of_value"] == pytest.approx(700.0 / 200_000.0)
    assert tc["climate_multiplier"] == pytest.approx(20.0 / 17.0, rel=1e-12)
    assert s["total"]["eal_usd"] == 720.0
    assert rf["eal_undefended_usd"] == 1000.0
    assert rf["flood_protection_return_periods_years"] == [50.0, 100.0]
    assert rf["levee_protected_assets"] == 1
    assert s["currency"]["display"]["krw_per_usd"] == FX and s["currency"]["amounts"] == "USD"


def test_return_periods_are_read_from_the_curves_never_summed() -> None:
    rf = build_simple_summary(_output(), krw_per_usd=FX)["hazards"]["RF"]
    assert rf["return_periods"] == [
        {"return_period_years": 10, "future_usd": 0.0, "baseline_usd": 0.0},
        {"return_period_years": 100, "future_usd": 5_000.0, "baseline_usd": 2_000.0},
    ]


def test_risk_counts_value_shares_and_concentration() -> None:
    rf = build_simple_summary(_output(), krw_per_usd=FX)["hazards"]["RF"]
    assert rf["risk_counts"] == {"High": 1, "Medium": 1, "Low": 2}
    assert rf["risk_value_share"]["High"] == pytest.approx(0.5)
    assert rf["risk_value_share"]["Medium"] == pytest.approx(0.25)
    conc = rf["concentration"]
    assert conc["top1_facility_id"] == "A"
    assert conc["top1_share"] == pytest.approx(600.0 / 700.0)
    assert conc["flag"] is True  # >= 25 %
    assert rf["n_with_loss"] == 3


def test_province_change_kinds() -> None:
    provs = {
        p["province_name"]: p for p in build_simple_summary(_output(), krw_per_usd=FX)["provinces"]
    }
    gg, bs = provs["경기도"], provs["부산광역시"]
    assert gg["n_assets"] == 2 and gg["value_usd"] == 150_000.0
    assert (
        gg["per_hazard"]["RF"]["future_usd"] == 680.0
        and gg["per_hazard"]["RF"]["baseline_usd"] == 300.0
    )
    assert gg["per_hazard"]["RF"]["change"]["kind"] == "up"
    assert gg["per_hazard"]["RF"]["change"]["pct"] == pytest.approx((680.0 / 300.0 - 1) * 100)
    assert bs["per_hazard"]["RF"]["change"]["kind"] == "new"  # 0 -> 20
    assert bs["per_hazard"]["TC"]["change"]["kind"] == "flat"  # 5 -> 5
    assert list(provs) == ["경기도", "부산광역시"]  # sorted by future total


def test_top_assets_are_ranked_by_total_eal() -> None:
    top = build_simple_summary(_output(), krw_per_usd=FX)["top_assets"]
    assert [a["facility_id"] for a in top] == ["A", "B", "C", "D"]
    assert top[0]["eal_usd"] == 610.0 and top[0]["main_hazard"] == "RF"
    assert top[0]["main_hazard_baseline_eal_usd"] == 200.0
    assert top[0]["share_of_portfolio_eal"] == pytest.approx(610.0 / 720.0)


def test_refused_model_chain_gives_no_flood_multiplier() -> None:
    s = build_simple_summary(_output(refused=True), krw_per_usd=FX)
    assert s["hazards"]["RF"]["climate_multiplier"] is None
    assert "모델 체인" in s["hazards"]["RF"]["climate_multiplier_note"]
    assert s["hazards"]["TC"]["climate_multiplier"] is not None
    for p in s["provinces"]:
        assert p["per_hazard"]["RF"]["change"]["kind"] == "no_baseline"
        assert p["total"]["change"]["kind"] == "no_baseline"


def test_without_a_baseline_the_change_is_not_invented() -> None:
    s = build_simple_summary(_output(baseline=False), krw_per_usd=FX)
    rf = s["hazards"]["RF"]
    assert rf["climate_multiplier"] is None and rf["baseline_eal_usd"] is None
    assert rf["climate_multiplier_note"] == "현재기후 기준 실행 없음"
    assert all(p["total"]["change"]["kind"] == "no_baseline" for p in s["provinces"])
    assert all(x["baseline_usd"] is None for x in rf["return_periods"])


def test_notes_name_missing_hazards_defences_and_the_tier() -> None:
    kinds = {
        (n["kind"], n["hazard"]) for n in build_simple_summary(_output(), krw_per_usd=FX)["notes"]
    }
    assert ("not_assessed", "CF") in kinds  # coastal flood was not run
    assert ("defences", "RF") in kinds
    assert ("tier", None) in kinds
    assert ("not_assessed", "RF") not in kinds


def test_route_returns_the_summary_and_refuses_unfinished_runs(client) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from climaterisk.api.deps import get_run_manager

    runs = {
        "done": SimpleNamespace(session_id="s1", status="done", output=_output()),
        "running": SimpleNamespace(session_id="s1", status="running", output=None),
    }
    fake = SimpleNamespace(poll=lambda rid: runs.get(rid))
    client.app.dependency_overrides[get_run_manager] = lambda: fake
    try:
        ok = client.get("/api/session/s1/run/done/simple-summary")
        assert ok.status_code == 200
        body = ok.json()
        assert body["hazards"]["RF"]["eal_usd"] == 700.0
        assert body["currency"]["display"]["unit_label"] == "억 원"
        assert client.get("/api/session/s1/run/running/simple-summary").status_code == 409
        assert client.get("/api/session/other/run/done/simple-summary").status_code == 404
        assert client.get("/api/session/s1/run/nope/simple-summary").status_code == 404
    finally:
        client.app.dependency_overrides.clear()
