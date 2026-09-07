"""Impact-function calibration — graceful degradation, record metadata, persistence.

EM-DAT is login-gated; the EM-DAT-path check runs before any CLIMADA import, so the
graceful path is offline-testable in the backend env. The fit itself needs CLIMADA and an
EM-DAT CSV and is not exercised here; the record builder and the persistence →
application chain are pure Python and are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import calibration as cal  # noqa: E402
from climaterisk_worker import vulnerability as vul  # noqa: E402
from climaterisk_worker.calibration import compute_calibration  # noqa: E402

_ASSET = {
    "id": "a",
    "name": "P",
    "lat": 35.6,
    "lon": 139.7,
    "value": 1.0e7,
    "currency": "USD",
    "tc_v_half": 70.0,
}
_REQ = {"assets": [_ASSET], "climate_scenario": "rcp45", "anchor_years": [2040]}


def test_calibration_needs_emdat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIMATERISK_EMDAT_PATH", raising=False)
    out = compute_calibration(_REQ)
    assert out["status"] == "error"
    assert "EM-DAT" in out["detail"]


def test_calibration_no_assets() -> None:
    out = compute_calibration({"assets": [], "climate_scenario": "rcp45", "anchor_years": [2040]})
    assert out["status"] == "error"


def test_record_carries_the_required_provenance_metadata() -> None:
    rec = cal.calibration_record(
        country="KOR",
        initial=190.5,
        calibrated=150.2,
        observed_annual_loss=3.2e8,
        modelled_annual_loss=3.19e8,
        observed_source="EM-DAT (public.emdat.be) CSV emdat_kor.csv",
        observed_period=(2000, 2023),
        n_observed_events=17,
        hazard="CLIMADA Data API synthetic TC, present-day",
        n_assets=3,
        calibrated_at="2026-09-07T00:00:00+00:00",
    )
    for key in (
        "peril",
        "param",
        "country",
        "observed_source",
        "observed_period",
        "calibrated_at",
        "schema_version",
        "objective",
        "method",
        "bounds",
        "calibrated",
        "initial",
        "fit_status",
        "applies_when",
    ):
        assert key in rec, key
    assert rec["peril"] == "tropical_cyclone" and rec["param"] == "v_half"
    assert rec["observed_period"] == [2000, 2023]
    assert rec["fit_status"] == "fitted"  # never "validated" from this runner
    assert rec["bounds"] == [25.7, 200.0]
    assert "minimize_scalar" in rec["method"] and "aai_agg" in rec["objective"]
    assert "calibrated" in rec["applies_when"]


def test_persisted_calibration_is_consumed_by_a_subsequent_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """calibration → persisted record → later run (tc_impf_default='calibrated') uses it."""
    monkeypatch.setenv("CLIMATERISK_CALIBRATION_DIR", str(tmp_path))
    rec = cal.calibration_record(
        country="KOR",
        initial=190.5,
        calibrated=150.2,
        observed_annual_loss=1.0,
        modelled_annual_loss=1.0,
        observed_source="EM-DAT test",
        observed_period=(2000, 2020),
        n_observed_events=5,
        hazard="synthetic",
        n_assets=1,
    )
    path = vul.save_calibration(rec)
    assert path == tmp_path / "tropical_cyclone_v_half_KOR.json"
    loaded = vul.load_calibration("tropical_cyclone", "v_half", "KOR")
    assert loaded is not None and loaded["calibrated"] == pytest.approx(150.2)
    assert loaded["schema_version"] == cal.CALIBRATION_SCHEMA_VERSION

    asset = {**_ASSET, "lat": 37.5, "lon": 127.0}
    # default mode ignores the record (opt-in application) …
    values, sources, _ = vul.resolve_tc_vhalf([asset], ["KOR"], {})
    assert sources == [vul.SRC_REGIONAL] and values == [pytest.approx(190.5)]
    # … the calibrated mode applies it
    values, sources, note = vul.resolve_tc_vhalf(
        [asset], ["KOR"], {"tc_impf_default": "calibrated"}
    )
    assert sources == [vul.SRC_CALIBRATED] and values == [pytest.approx(150.2)]
    assert "fitted" in note
    # … and overwriting keeps one record per country
    vul.save_calibration({**rec, "calibrated": 160.0})
    assert vul.load_calibration("tropical_cyclone", "v_half", "KOR")["calibrated"] == 160.0
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_calibration_dir_defaults_next_to_the_hazard_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLIMATERISK_CALIBRATION_DIR", raising=False)
    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path / "data" / "hazard_db"))
    assert vul.calibration_dir() == tmp_path / "data" / "calibrations"
