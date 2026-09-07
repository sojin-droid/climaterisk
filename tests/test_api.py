"""End-to-end API tests for health, session lifecycle, and libraries."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_libraries_present(client: TestClient) -> None:
    resp = client.get("/api/libraries")
    assert resp.status_code == 200
    libs = resp.json()
    assert {"sectors", "perils", "scenarios", "impact_functions", "carbon_prices"} <= set(libs)
    assert len(libs["sectors"]["sectors"]) >= 20  # expanded taxonomy
    assert len(libs["impact_functions"]["classes"]) == 5  # vulnerability classes


def test_unknown_library_404(client: TestClient) -> None:
    assert client.get("/api/libraries/nope").status_code == 404


def test_session_lifecycle_with_asset(client: TestClient) -> None:
    # Create
    created = client.post("/api/session")
    assert created.status_code == 201
    model = created.json()
    sid = model["id"]
    assert model["assets"] == []

    # Add an asset and save the whole model
    model["name"] = "Demo"
    model["assets"].append(
        {
            "name": "Seoul plant",
            "lat": 37.5665,
            "lon": 126.9780,
            "sector": "steel",
            "geographic_scale": "point",
            "value": 1_000_000.0,
            "currency": "USD",
        }
    )
    saved = client.put(f"/api/session/{sid}", json=model)
    assert saved.status_code == 200
    assert saved.json()["name"] == "Demo"
    assert len(saved.json()["assets"]) == 1
    assert saved.json()["assets"][0]["sector"] == "steel"

    # Read back
    fetched = client.get(f"/api/session/{sid}")
    assert fetched.status_code == 200
    assert len(fetched.json()["assets"]) == 1

    # Delete
    assert client.delete(f"/api/session/{sid}").status_code == 204
    assert client.get(f"/api/session/{sid}").status_code == 404


def test_run_validation(client: TestClient) -> None:
    sid = client.post("/api/session").json()["id"]
    # No assets yet -> 400
    assert client.post(f"/api/session/{sid}/run").status_code == 400
    # Unknown session -> 404
    assert client.post("/api/session/does-not-exist/run").status_code == 404


def test_calibration_rejects_an_unknown_observed_source(client: TestClient) -> None:
    """The observed series is validated before a worker is spawned (no run for a bad source)."""
    model = client.post("/api/session").json()
    model["assets"].append(
        {
            "name": "Seoul plant",
            "lat": 37.5665,
            "lon": 126.9780,
            "sector": "steel",
            "geographic_scale": "point",
            "value": 1_000_000.0,
            "currency": "USD",
        }
    )
    sid = model["id"]
    assert client.put(f"/api/session/{sid}", json=model).status_code == 200

    bad = client.post(f"/api/session/{sid}/calibration?observed_source=made_up")
    assert bad.status_code == 400
    assert "observed_source" in bad.json()["detail"]
    # Unknown session still fails first, as before.
    assert client.post("/api/session/does-not-exist/calibration").status_code == 404


def test_invalid_lat_rejected(client: TestClient) -> None:
    created = client.post("/api/session").json()
    created["assets"].append({"name": "bad", "lat": 999.0, "lon": 0.0})
    resp = client.put(f"/api/session/{created['id']}", json=created)
    assert resp.status_code == 422
