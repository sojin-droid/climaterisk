"""``GET /api/status/korea`` — the 현황 체크 reading must come from disk and claim nothing.

CLIMADA-free: the endpoint lists KMA archives by name, reads the catalog manifest, checks
credentials by presence only, and grades layers by the rules in
``docs/KOREA_1KM_IMPLEMENTATION_GAP.md``. It must never emit ``1km-ready``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from climaterisk.config import get_settings


def _seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Never let the developer's real .env leak into a test reading.
    from climaterisk.data import korea_status

    monkeypatch.setattr(korea_status, "env_file", lambda: tmp_path / "no-dotenv")
    kma = tmp_path / "kma"
    kma.mkdir()
    for name in (
        "MKPRISM_MKPRISMv31_TA_gridraw_daily_2000_2019_nc.tar.gz",
        "AR6_SSP245_5ENSMN_skorea_TA_gridraw_daily_2021_2030_asc.tar.gz",
        "MKPRISM_MKPRISMv31_TA_gridraw_daily_2000.nc",
        "unrelated.txt",
    ):
        (kma / name).write_bytes(b"x")
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(kma))
    monkeypatch.setenv("CLIMATERISK_DATAGOKR_KEY", "k" * 64)
    monkeypatch.delenv("CLIMATERISK_KOSIS_KEY", raising=False)

    db = get_settings().hazard_db_path
    db.mkdir(parents=True, exist_ok=True)
    (db / "catalog.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "peril": "heatwave",
                        "climate_scenario": "rcp45",
                        "region": "KOR",
                        "year": 2030,
                        "n_events": 10,
                        "n_centroids": 4312,
                        "file": "heatwave/HW_rcp45_KOR_2030.hdf5",
                        "source": (
                            "season p95 daily Tmax — KMA 남한상세 TA SSP245 5ENSMN "
                            "(2021-2030, 0.05 deg block mean)"
                        ),
                    },
                    {
                        "peril": "river_flood",
                        "climate_scenario": "rcp45",
                        "region": "KOR",
                        "year": 2050,
                        "n_events": 480,
                        "n_centroids": 5708,
                        "file": "river_flood/RF_rcp45_KOR_2050.hdf5",
                        "source": "CLIMADA Data API (river_flood, rcp60, cached)",
                    },
                    {
                        "peril": "tropical_cyclone",
                        "climate_scenario": "rcp45",
                        "region": "ESP",
                        "year": 2040,
                        "n_events": 1,
                        "n_centroids": 1,
                        "file": "tropical_cyclone/TC_rcp45_ESP_2040.hdf5",
                        "source": "CLIMADA Data API (tropical_cyclone, rcp45, cached)",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_status_reads_kma_files_by_name_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path)
    body = client.get("/api/status/korea").json()
    kma = body["kma"]
    assert kma["dir_exists"] is True
    names = {f["file"] for f in kma["files"]}
    assert "unrelated.txt" not in names and len(names) == 3
    archive = next(f for f in kma["files"] if f["file"].endswith("_2021_2030_asc.tar.gz"))
    assert archive == {
        **archive,
        "variable": "TA",
        "scenario_token": "SSP245",
        "model": "5ENSMN",
        "years": [2021, 2030],
        "format": "asc",
        "kind": "archive",
    }
    member = next(f for f in kma["files"] if f["file"].endswith("_2000.nc"))
    assert (
        member["years"] == [2000, 2000] and member["model"] is None and member["kind"] == "member"
    )
    assert kma["variables_present"] == ["TA"]
    assert kma["variables_consumed_by_code"] == ["TA", "TAMAX"]
    assert set(kma["variables_offered"]) == {"TA", "TAMAX", "TAMIN", "RN", "RHM", "WS", "SI"}


def test_status_makes_the_two_audited_label_problems_explicit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path)
    cat = client.get("/api/status/korea").json()["catalog"]
    assert cat["perils_with_kor_layer"] == ["heatwave", "river_flood"]  # ESP entry excluded
    rf = next(layer for layer in cat["kor_layers"] if layer["peril"] == "river_flood")
    assert rf["requested_scenario"] == "rcp45" and rf["served_scenario"] == "rcp60"
    assert rf["scenario_mismatch"] is True
    hw = next(layer for layer in cat["kor_layers"] if layer["peril"] == "heatwave")
    assert (
        hw["served_scenario"] == "SSP245" and hw["scenario_mismatch"] is False
    )  # naming system, not a mismatch
    assert hw["label_variable_conflict"] is True  # says Tmax, built from TA
    assert cat["scenario_mismatches"] == ["river_flood/RF_rcp45_KOR_2050.hdf5"]
    assert cat["label_variable_conflicts"] == ["heatwave/HW_rcp45_KOR_2030.hdf5"]


def test_status_grades_follow_the_gap_document_and_never_say_ready(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path)
    one_km = client.get("/api/status/korea").json()["one_km"]
    assert one_km["ready"] == []
    assert one_km["grades"]["heatwave"] == "5km-current"  # KMA-built, 0.05 deg
    assert one_km["grades"]["river_flood"] == "missing"  # Data API, not KMA
    assert one_km["grades"]["cold_wave"] == "1km-data-only"
    assert "1km-ready" not in one_km["grades"].values()
    assert "metadata" in one_km["rule"]


def test_status_reports_credentials_by_presence_never_by_value(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path)
    text = client.get("/api/status/korea").text
    assert "k" * 64 not in text, "a credential value leaked into the status payload"
    creds = {c["env"]: c for c in json.loads(text)["credentials"]}
    assert creds["CLIMATERISK_DATAGOKR_KEY"]["present"] is True
    assert creds["CLIMATERISK_DATAGOKR_KEY"]["length"] == 64
    assert creds["CLIMATERISK_KOSIS_KEY"]["present"] is False


def test_status_survives_an_empty_machine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from climaterisk.data import korea_status

    monkeypatch.setattr(korea_status, "env_file", lambda: tmp_path / "no-dotenv")
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path / "nope"))
    body = client.get("/api/status/korea").json()
    assert body["kma"]["dir_exists"] is False and body["kma"]["files"] == []
    assert body["catalog"]["kor_layers"] == [] or all(
        layer["peril"] for layer in body["catalog"]["kor_layers"]
    )
    assert body["one_km"]["ready"] == []


def test_status_falls_back_to_dotenv_for_credentials_by_presence_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bare uvicorn has no .env in its environment; the card must still know the key exists."""
    from climaterisk.data import korea_status

    env = tmp_path / ".env"
    secret = "s3cretvalue44chars_______________________x"
    env.write_text(f"CLIMATERISK_KOSIS_KEY={secret}\n", encoding="utf-8")
    monkeypatch.setattr(korea_status, "env_file", lambda: env)
    monkeypatch.delenv("CLIMATERISK_KOSIS_KEY", raising=False)
    monkeypatch.setenv("CLIMATERISK_KMA_DIR", str(tmp_path / "nope"))
    text = client.get("/api/status/korea").text
    assert "s3cretvalue" not in text
    creds = {c["env"]: c for c in json.loads(text)["credentials"]}
    assert creds["CLIMATERISK_KOSIS_KEY"]["present"] is True
    assert creds["CLIMATERISK_KOSIS_KEY"]["source"] == "dotenv"
    assert creds["CLIMATERISK_KOSIS_KEY"]["length"] == len(secret)
