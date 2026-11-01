"""Offline (closed-network) mode — the flag, what it refuses, and the readiness report.

CLIMADA-free. The worker-side guards are exercised through the worker package with a
stand-in client, so a network call is never attempted here either.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from climaterisk.config import Settings, get_settings
from climaterisk.data import offline_status as osm
from climaterisk.runs.manager import RunManager
from climaterisk.runs.store import RunStore

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import _dataapi, _params, dataapi_index, ingest  # noqa: E402


# --------------------------------------------------------------------------- #
# The flag                                                                     #
# --------------------------------------------------------------------------- #
def test_offline_is_configuration_and_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().offline is False
    monkeypatch.setenv("CLIMATERISK_OFFLINE", "1")
    assert Settings().offline is True
    for raw, expected in (("1", True), ("true", True), (" YES ", True), ("0", False), ("", False)):
        monkeypatch.setenv(_params.OFFLINE_ENV, raw)
        assert _params.offline() is expected, raw
    monkeypatch.delenv(_params.OFFLINE_ENV)
    assert _params.offline() is False


def test_manager_injects_the_flag_into_the_worker_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Proc:
        def poll(self) -> int | None:
            return 0

    def fake_popen(cmd: list[str], **kw: Any) -> _Proc:
        captured["cmd"], captured["env"] = cmd, kw["env"]
        return _Proc()

    monkeypatch.setattr("climaterisk.runs.manager.subprocess.Popen", fake_popen)
    mgr = RunManager(Settings(data_dir=tmp_path, offline=True), RunStore(tmp_path / "runs.db"))
    mgr._store.create("r1", "s1", "rcp45", ["physical"])
    mgr._spawn("r1", tmp_path / "r1", "{}")
    assert captured["env"]["CLIMATERISK_OFFLINE"] == "1"
    mgr2 = RunManager(Settings(data_dir=tmp_path, offline=False), RunStore(tmp_path / "runs2.db"))
    mgr2._store.create("r2", "s1", "rcp45", ["physical"])
    monkeypatch.delenv("CLIMATERISK_OFFLINE", raising=False)
    mgr2._spawn("r2", tmp_path / "r2", "{}")
    assert "CLIMATERISK_OFFLINE" not in captured["env"]


# --------------------------------------------------------------------------- #
# What offline refuses — fast, with the reason                                 #
# --------------------------------------------------------------------------- #
def test_worker_never_consults_the_data_api_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that could reach the network is refused offline (make_client builds the
    cache-only one; tests/test_offline_worker.py exercises it with CLIMADA)."""

    class _Client:
        def get_hazard(self, *a: Any, **k: Any) -> str:
            raise AssertionError("network call attempted in offline mode")

    monkeypatch.setenv(_params.OFFLINE_ENV, "1")
    with pytest.raises(_dataapi.OfflineError, match="could reach the network"):
        _dataapi.resilient_get_hazard(
            _Client(), "tropical_cyclone", properties={"country_iso3alpha": "KOR"}
        )
    out = ingest.run_ingest({"source": "dataapi", "peril": "river_flood"})
    assert out["status"] == "error" and "offline mode" in out["detail"]
    assert dataapi_index.compute_dataapi_index({})["status"] == "error"


def test_fetch_and_index_routes_refuse_when_offline(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIMATERISK_OFFLINE", "1")
    get_settings.cache_clear()
    try:
        assert client.get("/api/health").json()["offline"] is True
        r = client.post("/api/data/fetch?source_id=naturalearth")
        assert r.status_code == 200 and r.json()["status"] == "error"
        assert "offline mode" in r.json()["detail"]
        sid = client.post("/api/session").json()["id"]
        r = client.post(f"/api/session/{sid}/dataapi-index")
        assert r.status_code == 400 and "offline mode" in r.json()["detail"]
    finally:
        monkeypatch.delenv("CLIMATERISK_OFFLINE")
        get_settings.cache_clear()
    assert client.get("/api/health").json()["offline"] is False


# --------------------------------------------------------------------------- #
# The readiness report and the cached Data API index                           #
# --------------------------------------------------------------------------- #
class _Info:
    def __init__(self, data_type: str, name: str, **props: str) -> None:
        self.data_type, self.name, self.version, self.status = data_type, name, "v1", "active"
        self.properties, self.files, self.uuid = props, [object()], f"uuid-{name}"


class _FakeClient:
    def list_dataset_infos(self, data_type: str | None = None, **_: Any) -> list[_Info]:
        if data_type == "storm_europe":
            raise RuntimeError("no such type on this mirror")
        return [
            _Info(str(data_type), f"{data_type}_1", climate_scenario="rcp45"),
            _Info(str(data_type), f"{data_type}_2", climate_scenario="rcp85"),
        ]


def test_dataapi_index_is_metadata_only_and_survives_one_failing_type(tmp_path: Path) -> None:
    out = tmp_path / "cache" / "dataapi_index.json"
    index = dataapi_index.build_index(("tropical_cyclone", "storm_europe"), _FakeClient(), out)
    assert index["n_datasets"] == 2 and "storm_europe" in index["errors"]
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["datasets"][0]["properties"] == {"climate_scenario": "rcp45"}
    assert data["datasets"][0]["n_files"] == 1 and "fetched_at" in data


def test_offline_status_reads_only_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(osm, "dataapi_index_path", lambda: tmp_path / "dataapi_index.json")
    status = osm.offline_status()
    assert status["offline"] is False and status["env_var"] == "CLIMATERISK_OFFLINE"
    assert status["dataapi_index"]["present"] is False
    assert {f["name"] for f in status["local_files"]} >= {
        "Natural Earth 110m countries (clip layer for country grids)",
        "KMA 남한상세 archives (Korean heat perils)",
    }
    assert all(set(f) >= {"name", "path", "present"} for f in status["local_files"])
    assert status["online_only"][0]["feature"].startswith("Basemap tiles")
    assert "perils" in status["catalog"] and "how_to_prepare" in status
    # once an index is cached the report shows its age and per-type counts
    dataapi_index.build_index(("river_flood",), _FakeClient(), tmp_path / "dataapi_index.json")
    cached = osm.read_dataapi_index()
    assert cached["present"] and cached["counts"] == {"river_flood": 2}
    assert cached["age_days"] is not None and cached["age_days"] < 1


def test_offline_status_route_and_index_types_agree(client: Any) -> None:
    body = client.get("/api/status/offline").json()
    assert set(body) >= {"offline", "catalog", "dataapi_index", "local_files", "online_only"}
    assert tuple(dataapi_index.DEFAULT_TYPES) == osm.DATAAPI_INDEX_TYPES  # worker vs backend
