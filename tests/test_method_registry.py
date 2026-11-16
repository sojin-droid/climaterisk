"""Consistency tests for the method registry against READINESS, adapters and config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from climaterisk.physical_risk.method_registry import (
    TAGS,
    checks,
    defences_allowed,
    load_registry,
    main_method,
    method_for,
    rank1_model,
    readiness_key,
    run_checks,
    supplementary,
)
from climaterisk.physical_risk.metrics import load_config
from climaterisk.physical_risk.models import READINESS

_REPO = Path(__file__).resolve().parents[1]


def test_every_tag_has_exactly_one_rank1_main() -> None:
    reg = load_registry()
    for t in TAGS:
        ranks = [m["rank"] for m in reg["tags"][t]["main"]]
        assert ranks.count(1) == 1
        assert sorted(ranks) == list(range(1, len(ranks) + 1))


def test_rank1_cells_are_runnable_in_readiness() -> None:
    for t in TAGS:
        cell = READINESS[readiness_key(t)][rank1_model(t)]
        assert cell["status"] in {"READY", "HAZARD_ONLY"}
        assert cell.get("trial") != "yes"


def test_rank1_adapter_dataset_matches() -> None:
    pytest.importorskip("climada")
    pytest.importorskip("climada_petals")
    from climaterisk_worker.physical_risk.adapters import adapter_for

    for t in TAGS:
        main = main_method(t)
        assert main is not None
        if main["dataset"] is None:
            continue
        adapter = adapter_for(t, rank1_model(t), "rcp45", 2050, "KOR")
        assert type(adapter).dataset == main["dataset"]


def test_heat_switch_matches_registry() -> None:
    main = main_method("HW")
    assert main is not None
    assert load_config()["heat_utci"]["enabled"] is (main["dataset"] == "kma_asos_open_field_utci")


def test_user_decisions_2026_11_16() -> None:
    assert rank1_model("CF") == "KOREA_LOCAL"
    assert defences_allowed("RF", "KOREA_LOCAL") is False
    assert defences_allowed("RF", "DATA_API_COUNTRY") is True
    assert defences_allowed("CF", "GLOBAL_BASELINE") is True
    assert {s["id"] for s in supplementary("RF")} == {"rf.disaster_yearbook"}
    assert {s["id"] for s in supplementary("HW")} == {"hw.kdca_heat_illness"}
    for t in TAGS:
        for ch in checks(t):
            assert "kdca" not in ch["check_id"]
            assert "yearbook" not in ch["check_id"]


def test_checks_are_ordered_and_typed() -> None:
    for t in TAGS:
        cs = checks(t)
        assert [c["order"] for c in cs] == list(range(1, len(cs) + 1))
        for c in cs:
            assert c["status"] in {"exists", "to_build"}
            if c["status"] == "exists":
                assert c["diagnostics_key"]


def test_every_studio_peril_is_listed() -> None:
    perils = json.loads((_REPO / "assets/libraries/perils.json").read_text(encoding="utf-8"))
    assert {p["id"] for p in perils["perils"]} == set(load_registry()["studio"])


def test_method_for_reference_model() -> None:
    assert method_for("TC", "GLOBAL_BASELINE") == {
        "method_id": "tc.reference.global_baseline",
        "method_rank": None,
        "method_role": "reference",
    }
    assert method_for("RF", "KOREA_LOCAL") == {
        "method_id": "rf.floodmap_class",
        "method_rank": 1,
        "method_role": "main",
    }


def test_load_registry_path_override(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text('{"tags": {}}', encoding="utf-8")
    assert load_registry(p) == {"tags": {}}


# --------------------------------------------------------------------------- #
# Task 2: rank 1 is the result; references never fill; rows carry their method #
# --------------------------------------------------------------------------- #
def test_recommended_models_are_the_registry_rank1() -> None:
    import copy

    from climaterisk.physical_risk import display_copy as dc

    assert dc.recommended_models() == {
        "RF": "KOREA_LOCAL",
        "PF": "KOREA_LOCAL",
        "CF": "KOREA_LOCAL",
        "TC": "DATA_API_COUNTRY",
        "HEAT": "KOREA_LOCAL",
    }
    table = copy.deepcopy(READINESS)
    table["RF"]["KOREA_LOCAL"]["status"] = "NOT_IMPLEMENTED"
    assert "RF" not in dc.recommended_models(readiness=table)


def _stub_adapter(model_id: str, status: str = "NO_HAZARD_DATA"):  # type: ignore[no-untyped-def]
    pytest.importorskip("climada")
    import sys

    for extra in (_REPO / "worker", _REPO / "src"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    from climaterisk_worker.physical_risk.adapters import HazardDescription

    desc = HazardDescription(
        hazard_type="HW",
        model_id=model_id,
        status=status,
        hazard_source="stub",
        hazard_dataset=None,
        hazard_data_version=None,
        hazard_country="KOR",
        requested_scenario="historical",
        served_scenario="historical",
        time_horizon=None,
    )

    class Stub:
        def describe(self):  # type: ignore[no-untyped-def]
            return desc

    return Stub()


def test_rows_are_stamped_with_their_method() -> None:
    adapter = _stub_adapter("KOREA_LOCAL")
    from climaterisk_worker.physical_risk import runner

    fac = [
        {
            "facility_id": "F1",
            "facility_name": "F1",
            "latitude": 37.5,
            "longitude": 127.0,
            "asset_value_usd": 1.0e6,
        }
    ]
    rows, _ = runner._rows_for(adapter, "HW", fac, "KOR", None)
    assert rows[0].method_id == "hw.open_field_utci"
    assert rows[0].method_rank == 1 and rows[0].method_role == "main"
    rows, _ = runner._rows_for(_stub_adapter("GLOBAL_BASELINE"), "HW", fac, "KOR", None)
    assert rows[0].method_role == "reference" and rows[0].method_rank is None


def test_method_fields_reach_table_rows() -> None:
    from climaterisk.physical_risk import results_table as rt
    from climaterisk.physical_risk.metrics import ResultRow

    row = ResultRow(
        facility_id="F1",
        facility_name="F1",
        hazard_type="RF",
        model_id="KOREA_LOCAL",
        method_id="rf.official_map",
        method_rank=1,
        method_role="main",
        unavailable_reason="map_unreadable",
    )
    out = rt.table_rows([row])[0]
    for key in ("method_id", "method_rank", "method_role", "unavailable_reason"):
        assert key in out
    assert out["method_rank"] == 1
    sheet = rt.hazard_results_frame([row])[0]
    for key in ("method_id", "method_rank", "method_role", "unavailable_reason"):
        assert key in sheet
    assert sheet["unavailable_reason"] == "map_unreadable"


def test_unknown_hazard_key_is_omitted_not_an_error() -> None:
    from climaterisk.physical_risk import display_copy as dc

    assert dc.recommended_models(["RF", "NOPE"]) == {"RF": "KOREA_LOCAL"}


def test_primary_rows_without_preferred_use_the_recommended_models() -> None:
    from climaterisk.physical_risk import results_table as rt
    from climaterisk.physical_risk.metrics import ResultRow

    rows = [
        ResultRow(facility_id="F", facility_name="F", hazard_type="RF", model_id=m)
        for m in ("GLOBAL_BASELINE", "KOREA_LOCAL")
    ]
    assert rt.primary_rows(rows)[("F", "RF")].model_id == "KOREA_LOCAL"
    assert rt.primary_rows(rows, {}) == {}


_FAC = [
    {
        "facility_id": "F1",
        "facility_name": "F1",
        "latitude": 37.5,
        "longitude": 127.0,
        "asset_value_usd": 1.0e6,
    }
]


def test_hazard_unavailable_path_is_stamped() -> None:
    adapter = _stub_adapter("KOREA_LOCAL", status="READY")
    from climaterisk_worker.physical_risk import adapters, runner

    def boom(bbox, points=None):  # type: ignore[no-untyped-def]
        raise adapters.HazardUnavailable("offline")

    adapter.load = boom
    rows, rec = runner._rows_for(adapter, "HW", _FAC, "KOR", None)
    assert "load_error" in rec
    assert rows[0].method_id == "hw.open_field_utci" and rows[0].method_role == "main"


def test_success_path_is_stamped(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _stub_adapter("DATA_API_COUNTRY", status="READY")
    from climaterisk_worker.physical_risk import engine, runner

    class Haz:
        frequency = None

    adapter.load = lambda bbox, points=None: Haz()
    adapter.spatial_resolution = lambda hazard: None
    adapter.diagnostics = {}
    orig = engine.calculate

    def fake(f, hazard, tag, **kw):  # type: ignore[no-untyped-def]
        # the engine is not under test here; keep its row construction, skip the hazard maths
        return orig(f, None, tag, model_id=kw["model_id"], status_override="NO_HAZARD_DATA")

    monkeypatch.setattr(engine, "calculate", fake)
    rows, _ = runner._rows_for(adapter, "HW", _FAC, "KOR", None)
    assert rows[0].method_role == "reference" and rows[0].method_rank is None
    assert rows[0].method_id == "hw.reference.data_api_country"


def test_rank1_flood_map_is_never_defended(monkeypatch: pytest.MonkeyPatch) -> None:
    """The official map assumes levee failure: no national defence on top of it."""
    pytest.importorskip("climada")
    import numpy as np
    from climada.hazard import Hazard
    from scipy import sparse

    adapter = _stub_adapter("KOREA_LOCAL", status="READY")
    from climaterisk_worker import ingest
    from climaterisk_worker.hazard_convert import _centroids
    from climaterisk_worker.physical_risk import runner

    def fail(hazard, tag):  # type: ignore[no-untyped-def]
        raise AssertionError("_with_defences must not run for the rank-1 flood map")

    monkeypatch.setattr(runner, "_with_defences", fail)

    rps = [100, 200, 500]
    hazard = Hazard(
        haz_type="RF",
        units="m",
        centroids=_centroids(np.array([37.5]), np.array([127.0])),
        event_id=np.arange(1, 4),
        event_name=[f"rp{rp}" for rp in rps],
        date=np.zeros(3, dtype=int),
        frequency=ingest._incremental_frequency(rps),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(np.ones((3, 1))),
        fraction=sparse.csr_matrix(np.ones((3, 1))),
    )
    hazard.check()
    adapter.load = lambda bbox, points=None: hazard
    adapter.spatial_resolution = lambda hz: None
    adapter.diagnostics = {}
    rows, rec = runner._rows_for(adapter, "RF", _FAC, "KOR", None)
    assert rows[0].calculation_status == "FULL"
    assert rows[0].flood_protection_return_period_years is None
    assert rows[0].eal_usd > 0
    assert "flood_protection" not in rec


def _states(rows: list[dict]) -> list[tuple[str, str]]:  # type: ignore[type-arg]
    return [(r["check_id"], r["state"]) for r in rows]


def _rank1(tag: str) -> str:
    model = rank1_model(tag)
    assert model is not None
    return model


def test_run_checks_rf_present_day() -> None:
    rows = run_checks("RF", _rank1("RF"), {"sub_check": {"warn": False}})
    assert _states(rows) == [
        ("rf.fwdet_edge", "ran"),
        ("rf.isimip_sido_factor", "skipped"),
        ("rf.isimip_grid_depth", "skipped"),
    ]
    assert [r["order"] for r in rows] == [1, 2, 3]
    assert rows[1]["reason"] == "no diagnostics for this run"
    assert rows[2]["reason"] == "not built yet"


def test_run_checks_warn_and_error() -> None:
    hw = run_checks("HW", _rank1("HW"), {"sub_check": {"warn": True, "warn_reason": "x"}})
    assert _states(hw) == [("hw.utci_vs_heat_days", "warn")]
    assert hw[0]["reason"] == "x"
    pf = run_checks("PF", _rank1("PF"), {"sub_check": {"warn": None}, "validation": {"error": "E"}})
    assert _states(pf) == [("pf.fwdet_edge", "skipped"), ("pf.seoul_traces", "error")]
    assert pf[0]["reason"] == "not enough points"
    assert pf[1]["reason"] == "E"


def test_reference_models_run_no_checks() -> None:
    assert run_checks("RF", "GLOBAL_BASELINE", {}) == []


def test_run_checks_no_warn_key_means_ran_without_verdict() -> None:
    pf = run_checks(
        "PF",
        _rank1("PF"),
        {"sub_check": {"warn": False}, "validation": {"pod": 0.5, "note": "diagnostic only"}},
    )
    assert _states(pf) == [("pf.fwdet_edge", "ran"), ("pf.seoul_traces", "ran")]
    assert pf[1]["reason"] == "diagnostic only"


def test_run_checks_warn_reason_falls_back_to_reason() -> None:
    cf = run_checks("CF", _rank1("CF"), {"sub_check": {"warn": True, "reason": "r"}})
    assert cf[0]["state"] == "warn"
    assert cf[0]["reason"] == "r"
