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
