"""Ingest sources: API whitelist == Data-tab catalog == worker refiners (incl. ``tcrain``).

The TCRain refiner existed in the worker and was advertised in the Data tab, but the API
whitelist rejected ``source="tcrain"`` with 400. These tests pin the three lists together.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))


def _advertised_sources() -> set[str]:
    data = json.loads(
        (REPO / "assets" / "libraries" / "data_sources.json").read_text(encoding="utf-8")
    )
    items = data["sources"] if isinstance(data, dict) and "sources" in data else data
    if isinstance(items, dict):  # {"_meta":…, "<list-key>": [...]}
        items = next(v for k, v in items.items() if isinstance(v, list))
    out = set()
    for it in items:
        fetch = it.get("fetch") if isinstance(it, dict) else None
        if fetch and fetch.get("mode") == "auto" and fetch.get("source"):
            out.add(fetch["source"])
    return out


def test_api_whitelist_matches_data_tab_and_worker() -> None:
    pytest.importorskip("pydantic")
    from climaterisk_worker.ingest import _REFINERS

    from climaterisk.api.routers.run import INGEST_SOURCES

    api = set(INGEST_SOURCES)
    assert "tcrain" in api
    assert api == _advertised_sources(), "Data tab and API whitelist disagree"
    assert api <= set(_REFINERS), "API accepts a source the worker cannot refine"


def test_tcrain_ingest_is_accepted_by_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pydantic")
    from fastapi.testclient import TestClient

    from climaterisk.api.main import create_app
    from climaterisk.runs.manager import RunManager

    spawned: list[str] = []

    def fake_spawn(self, run_id, run_dir, request_json):  # type: ignore[no-untyped-def]
        spawned.append(json.loads(request_json)["source"])

    monkeypatch.setattr(RunManager, "_spawn", fake_spawn)
    client = TestClient(create_app())
    created = client.post("/api/session").json()
    created["assets"].append({"name": "Busan", "lat": 35.18, "lon": 129.08})
    sid = created["id"]
    assert client.put(f"/api/session/{sid}", json=created).status_code == 200

    ok = client.post(f"/api/session/{sid}/ingest", json={"source": "tcrain", "peril": "tc_rain"})
    assert ok.status_code == 200, ok.text
    assert spawned == ["tcrain"]
    bad = client.post(f"/api/session/{sid}/ingest", json={"source": "nope", "peril": "tc_rain"})
    assert bad.status_code == 400
