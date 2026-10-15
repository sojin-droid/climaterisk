"""Offline mode with CLIMADA — the cache-only client, and a real run served from the cache.

Runs in the worker env (``.climada-env``); skipped without CLIMADA. The last two tests use
whatever CLIMADA has cached on this machine and skip when the KOR sets were never fetched —
they are the end-to-end proof that an offline run reproduces the online numbers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("climada")

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker import _dataapi, _params  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "asset_level_validation"


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_params.OFFLINE_ENV, "1")


def test_offline_client_never_probes_or_downloads(
    offline: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import requests

    def no_network(*a: Any, **k: Any) -> None:
        raise AssertionError("network call attempted in offline mode")

    monkeypatch.setattr(requests, "head", no_network)
    monkeypatch.setattr(requests, "get", no_network)
    client = _dataapi.make_client()
    assert client.online is False  # decided without the stock HEAD probe
    with pytest.raises(_dataapi.OfflineError, match="not in CLIMADA's download cache"):
        client._download("https://example.invalid/x.hdf5", tmp_path / "x.hdf5")


def test_a_dataset_never_fetched_fails_fast_with_the_reason(offline: None) -> None:
    from climaterisk_worker.physical_risk import adapters

    adapter = adapters.DataApiAdapter("TC", "rcp85", 2040, "ZZZ")  # no such dataset, ever
    adapter.dataset = "tropical_cyclone_never_fetched_on_any_machine"
    with pytest.raises(_dataapi.OfflineError, match="never fetched"):
        adapter.load()


def _validation_run() -> dict[str, Any]:
    import importlib.util

    from climaterisk_worker.physical_risk import runner

    spec = importlib.util.spec_from_file_location(
        "prb", REPO / "scripts" / "physical_risk_batch.py"
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return runner.compute_physical_risk_models(
        {
            "facilities": cli.read_facilities(FIX / "validation_portfolio.csv"),
            "climate_scenario": "rcp85",
            "target_year": 2040,
            "hazards": ["RF", "TC"],
            "models": ["DATA_API_COUNTRY"],
            "country": "KOR",
        }
    )


def test_offline_run_reproduces_the_online_evidence(offline: None) -> None:
    """Models tab, KOR country sets, offline: same numbers as the frozen online evidence."""
    out = _validation_run()
    errors = [r["status_detail"] for r in out["rows"] if r["calculation_status"] == "ERROR"]
    if errors:
        assert all("offline mode" in str(e) for e in errors), errors  # never a raw network error
        pytest.skip(f"KOR Data API sets not in this machine's CLIMADA cache: {errors[0]}")
    recorded = {
        (r["facility_id"], r["hazard_type"], r["model_id"]): r
        for r in json.loads((FIX / "run_rcp85_2040_recommended.json").read_text("utf-8"))["rows"]
    }
    assert out["rows"]
    for r in out["rows"]:
        rec = recorded[(r["facility_id"], r["hazard_type"], r["model_id"])]
        assert r["calculation_status"] == rec["calculation_status"]
        for f in ("eal_usd", "potential_loss_usd", "hazard_intensity"):
            assert r[f] == pytest.approx(rec[f], rel=1e-6), (r["facility_id"], r["hazard_type"], f)
