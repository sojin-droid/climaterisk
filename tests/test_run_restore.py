"""Re-attaching a reloaded page to finished runs (offline, hermetic — no worker spawned).

The browser only remembers run ids it submitted during the current page-load, so a refresh
used to blank the Results view even though the analysis was complete and persisted. These
tests lock in the two pieces that fix it:

  * ``run_kind`` — classify a persisted run from its ``perils`` field, which already carries
    a sentinel for every non-physical submission (so no schema migration was needed);
  * ``RunStore.latest_by_kind`` — newest **finished** run per kind for a session.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("pydantic")  # backend-only test; the CLIMADA worker env has no pydantic

from climaterisk.runs.store import RunStore, run_kind

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs.db")


# --------------------------------------------------------------------------- #
# run_kind: the classification the restore depends on.                        #
# --------------------------------------------------------------------------- #
def test_real_peril_lists_are_physical_runs() -> None:
    assert run_kind(["tropical_cyclone"]) == "physical"
    assert run_kind(["heat_mortality"]) == "physical"
    assert run_kind(["river_flood", "wildfire"]) == "physical"
    assert run_kind([]) == "physical"  # degenerate, but never a sentinel


def test_sentinels_classify_non_physical_runs() -> None:
    """These are the exact single-element lists RunManager.submit_* writes."""
    for sentinel in (
        "cost_benefit",
        "uncertainty",
        "litpop",
        "hazard_preview",
        "supplychain",
        "calibration",
        "forecast",
    ):
        assert run_kind([sentinel]) == sentinel


def test_ingest_runs_are_grouped_under_one_kind() -> None:
    """Ingest encodes its source in the sentinel (``ingest:aqueduct``)."""
    assert run_kind(["ingest:aqueduct"]) == "ingest"
    assert run_kind(["ingest:dataapi"]) == "ingest"


def test_a_sentinel_alongside_perils_is_not_a_sentinel_run() -> None:
    """Only a lone sentinel marks a special run — a peril list is never reinterpreted."""
    assert run_kind(["litpop", "tropical_cyclone"]) == "physical"


# --------------------------------------------------------------------------- #
# latest_by_kind: what the reloaded page actually adopts.                     #
# --------------------------------------------------------------------------- #
def test_latest_by_kind_returns_newest_finished_run_per_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)

    older = store.create("r1", "s1", "rcp45", ["heat_mortality"])
    newer = store.create("r2", "s1", "rcp45", ["heat_mortality"])
    litpop = store.create("r3", "s1", "rcp45", ["litpop"])
    for run, out in ((older, {"results": ["old"]}), (newer, {"results": ["new"]}), (litpop, {})):
        store.update(run.id, status="done", output=out)

    # created_at can tie at second resolution, so make the ordering explicit.
    with store._connect() as conn:  # private on purpose: assert on the persisted ordering
        conn.execute("UPDATE runs SET created_at = '2026-01-01T00:00:00' WHERE id = 'r1'")
        conn.execute("UPDATE runs SET created_at = '2026-01-02T00:00:00' WHERE id = 'r2'")
        conn.execute("UPDATE runs SET created_at = '2026-01-03T00:00:00' WHERE id = 'r3'")

    latest = store.latest_by_kind("s1")
    assert set(latest) == {"physical", "litpop"}
    assert latest["physical"].id == "r2"  # the newer physical run wins
    assert latest["litpop"].id == "r3"


def test_unfinished_and_failed_runs_are_never_restored(tmp_path: Path) -> None:
    """Restoring a queued or errored run would show a half-state as if it were a result."""
    store = _store(tmp_path)
    store.create("queued", "s1", "rcp45", ["heat_mortality"])  # left queued
    running = store.create("running", "s1", "rcp45", ["heat_mortality"])
    store.update(running.id, status="running")
    failed = store.create("failed", "s1", "rcp45", ["heat_mortality"])
    store.update(failed.id, status="error", detail="worker died")

    assert store.latest_by_kind("s1") == {}


def test_runs_do_not_leak_across_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mine = store.create("mine", "s1", "rcp45", ["heat_mortality"])
    theirs = store.create("theirs", "s2", "rcp45", ["heat_mortality"])
    store.update(mine.id, status="done", output={"results": []})
    store.update(theirs.id, status="done", output={"results": []})

    assert list(store.latest_by_kind("s1")) == ["physical"]
    assert store.latest_by_kind("s1")["physical"].id == "mine"
    assert store.latest_by_kind("unknown-session") == {}


def test_restored_run_carries_its_output(tmp_path: Path) -> None:
    """The whole point is re-rendering results, so the payload must survive the round-trip."""
    store = _store(tmp_path)
    run = store.create("r1", "s1", "historical", ["heat_mortality"])
    output = {
        "status": "ok",
        "results": [
            {"peril": "heat_mortality", "status": "ok", "result_kind": "mortality", "aai_agg": 0.15}
        ],
    }
    store.update(run.id, status="done", output=output)

    restored = store.latest_by_kind("s1")["physical"]
    assert restored.output == output
    assert restored.perils == ["heat_mortality"]
    assert restored.climate_scenario == "historical"


# --------------------------------------------------------------------------- #
# The HTTP contract the frontend restore calls on mount.                      #
# --------------------------------------------------------------------------- #
def test_latest_runs_endpoint(client: TestClient) -> None:
    """`GET /latest-runs` keys results by kind, and 404s for an unknown session."""
    assert client.get("/api/session/does-not-exist/latest-runs").status_code == 404

    session_id = client.post("/api/session").json()["id"]
    empty = client.get(f"/api/session/{session_id}/latest-runs")
    assert empty.status_code == 200
    assert empty.json() == {}  # nothing run yet — the UI simply shows its run button

    # Persist a finished physical run through the same store the API reads.
    from climaterisk.api.deps import get_run_manager

    store = get_run_manager()._store
    run = store.create("restored", session_id, "historical", ["heat_mortality"])
    store.update(run.id, status="done", output={"status": "ok", "results": [{"peril": "x"}]})

    body = client.get(f"/api/session/{session_id}/latest-runs").json()
    assert set(body) == {"physical"}
    assert body["physical"]["id"] == "restored"
    assert body["physical"]["output"]["results"] == [{"peril": "x"}]
