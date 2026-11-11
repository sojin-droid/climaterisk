"""Case twin gate: Yeongdeungpo 쪽방촌 street heat (reviewer-written; agents may not edit it).
Mission: docs/reports/twin_case_sites_mission_ko.md section B.

Spec ``assets/libraries/case_yeongdeungpo_spec.json``:
- ``site``: {name, bbox [W, S, E, N] within 126.89..126.93 E, 37.505..37.53 N, cell_m}.
- ``sdot``: {locations_file, radius_m <= 1000, files_glob, licence "공공누리 제1유형"}.
- ``days``: list of ISO dates (summer 2026, at least 3) chosen from S-DoT before modelling,
  with ``days_rule`` saying how they were chosen (not by model fit).
- ``height_policy``: "osm_only" — a building without OSM height or building:levels has height
  None (never a default storey count); ``floor_to_floor_m`` listed in ``assumptions``.
- ``check``: {"min_spearman": number in (0, 1)}.
- ``assumptions``: non-empty list of strings.

Module ``climaterisk_worker.twin.case_heat``:
- ``building_height(tags, floor_to_floor_m)`` -> float | None: "height" tag ("12", "12 m",
  "12.5m") wins; else building:levels x floor_to_floor_m; else None.
- ``rank_check(model, obs, min_spearman)`` -> {"spearman", "n", "warning"} over sensors
  finite in both; warning when spearman < min_spearman or n < 3.
- ``utci(tdb, tr, v, rh)`` -> UTCI degC via pythermalcomfort (vectorised); for tr == tdb,
  v = 0.5, rh = 50 it is within 3 degC of tdb at tdb = 30, and increases with tr.
Outputs ``$CLIMATERISK_DATA_ROOT/derived/case_twins/yeongdeungpo/summary.json`` (skipped if
absent): ``n_sensors >= 3``, ``check`` keys, ``n_buildings_height_unknown`` (int),
``height_sensitivity`` with "low" and "high" runs, ``badge == "미검증"``,
``n_assumptions == len(assumptions)``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
SPEC = json.loads(
    (ROOT / "assets/libraries/case_yeongdeungpo_spec.json").read_text(encoding="utf-8")
)


def test_spec() -> None:
    w, s, e, n = SPEC["site"]["bbox"]
    assert 126.89 <= w < e <= 126.93 and 37.505 <= s < n <= 37.53
    assert SPEC["sdot"]["radius_m"] <= 1000 and SPEC["sdot"]["licence"] == "공공누리 제1유형"
    assert len(SPEC["days"]) >= 3 and all(d.startswith("2026-") for d in SPEC["days"])
    assert SPEC["days_rule"]
    assert SPEC["height_policy"] == "osm_only"
    assert any("floor_to_floor" in a or "층고" in a for a in SPEC["assumptions"])
    assert 0 < SPEC["check"]["min_spearman"] < 1


def test_building_height() -> None:
    ch = pytest.importorskip("climaterisk_worker.twin.case_heat")
    assert ch.building_height({"height": "12 m", "building:levels": "9"}, 3.3) == 12.0
    assert ch.building_height({"height": "12.5m"}, 3.3) == 12.5
    assert ch.building_height({"building:levels": "4"}, 3.0) == 12.0
    assert ch.building_height({"building": "yes"}, 3.3) is None
    assert ch.building_height({"height": "tall"}, 3.3) is None


def test_rank_check() -> None:
    ch = pytest.importorskip("climaterisk_worker.twin.case_heat")
    r = ch.rank_check(np.array([1.0, 2.0, 3.0, np.nan]), np.array([10.0, 20.0, 30.0, 5.0]), 0.5)
    assert r["n"] == 3 and r["spearman"] == pytest.approx(1.0) and r["warning"] is False
    r2 = ch.rank_check(np.array([3.0, 2.0, 1.0]), np.array([1.0, 2.0, 3.0]), 0.5)
    assert r2["warning"] is True


def test_utci() -> None:
    pytest.importorskip("pythermalcomfort")
    ch = pytest.importorskip("climaterisk_worker.twin.case_heat")
    u = np.asarray(ch.utci(np.array([30.0, 30.0]), np.array([30.0, 50.0]), 0.5, 50.0))
    assert abs(u[0] - 30.0) <= 3.0 and u[1] > u[0]


SUMMARY = Path(os.environ.get("CLIMATERISK_DATA_ROOT", "/nonexistent")) / (
    "derived/case_twins/yeongdeungpo/summary.json"
)


@pytest.mark.skipif(not SUMMARY.exists(), reason="Yeongdeungpo twin not built here")
def test_summary() -> None:
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert s["n_sensors"] >= 3 and {"spearman", "n", "warning"} <= set(s["check"])
    assert isinstance(s["n_buildings_height_unknown"], int)
    assert {"low", "high"} <= set(s["height_sensitivity"])
    assert s["badge"] == "미검증" and s["n_assumptions"] == len(SPEC["assumptions"])
