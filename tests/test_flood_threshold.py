"""Site-survey zero-damage flood threshold — the staged rule, its plumbing, and the curve.

The backend part is CLIMADA-free: the staged rule, the cited defaults, the row field, the
export column, the API schema and the batch CSV column. The CLIMADA part (``_jrc``) is
skipped outside the worker environment and checks that the stepped curve is zero up to
``T`` and the published JRC curve above it, and that ``ImpactCalc`` agrees with the
analytic sum.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk.physical_risk import results_table as rt  # noqa: E402
from climaterisk.physical_risk.flood_threshold import (  # noqa: E402
    STAGE_BOARD,
    STAGE_DEFAULT,
    STAGE_FIRST_FLOOR,
    STAGE_SILL,
    STAGE_WALL,
    default,
    defaults,
    pump_head_m,
    zero_damage_threshold,
)
from climaterisk.physical_risk.metrics import ModelId, ResultRow  # noqa: E402


# --------------------------------------------------------------------------- #
# The staged rule                                                              #
# --------------------------------------------------------------------------- #
def test_no_measurement_keeps_the_published_curve() -> None:
    t = zero_damage_threshold()
    assert (t.t_m, t.stage, t.t_if_board_m) == (0.05, STAGE_DEFAULT, 0.05)


@pytest.mark.parametrize(
    ("kw", "t_m", "stage"),
    [
        ({"sill_m": 0.2}, 0.2, STAGE_SILL),
        ({"sill_m": 0.2, "board_status": "설치됨", "board_m": 0.5}, 0.5, STAGE_BOARD),
        # a recommended / planned board is not protection today
        ({"sill_m": 0.2, "board_status": "설치 권고", "board_m": 0.5}, 0.2, STAGE_SILL),
        (
            {"board_status": "설치됨", "board_m": 0.5, "wall_status": "설치됨", "wall_m": 0.8},
            0.8,
            STAGE_WALL,
        ),
        ({"wall_status": "없음", "wall_m": 0.8}, 0.05, STAGE_DEFAULT),
        # the first floor counts only when nothing opens to the basement
        ({"first_floor_m": 0.9, "basement_opening": "없음"}, 0.9, STAGE_FIRST_FLOOR),
        ({"first_floor_m": 0.9, "basement_opening": "있음"}, 0.05, STAGE_DEFAULT),
        ({"sill_m": 1.2, "first_floor_m": 0.9, "basement_opening": "없음"}, 1.2, STAGE_SILL),
        # a lower stage never pulls T down
        ({"sill_m": 0.7, "board_status": "설치됨", "board_m": 0.3}, 0.7, STAGE_SILL),
        # blanks and text are "not measured", never 0
        ({"sill_m": "", "board_status": "설치됨", "board_m": "n/a"}, 0.05, STAGE_DEFAULT),
    ],
)
def test_staged_threshold(kw: dict, t_m: float, stage: str) -> None:  # type: ignore[type-arg]
    t = zero_damage_threshold(**kw)
    assert t.t_m == pytest.approx(t_m) and t.stage == stage


def test_if_installed_value_uses_the_planned_board_or_its_default() -> None:
    assert zero_damage_threshold(board_status="설치 권고").t_if_board_m == pytest.approx(
        default("BOARD_PLAN")
    )
    assert zero_damage_threshold(board_status="설치 계획", board_m=0.4).t_if_board_m == (
        pytest.approx(0.4)
    )
    assert zero_damage_threshold(board_status="없음").t_if_board_m == pytest.approx(0.05)


def test_pump_head_inverts_the_weir_equation() -> None:
    # Q = C·B·H^1.5·3600 -> H; analytic round trip at H = 0.1 m, B = 6 m
    q = default("WEIR") * 6.0 * 0.1**1.5 * 3600
    assert pump_head_m(q) == pytest.approx(0.1, rel=1e-12)
    assert pump_head_m(q / 2, ramp_width_m=3.0) == pytest.approx(0.1, rel=1e-12)
    assert pump_head_m(0.0) == 0.0
    t = zero_damage_threshold(sill_m=0.2, pump_q_m3h=q)
    assert t.road_m == pytest.approx(0.3)
    assert t.t_m == pytest.approx(0.2)  # pumps never raise the river / coastal threshold


def test_every_default_cites_a_listed_source() -> None:
    d = defaults()
    for key, v in d["values"].items():
        assert v["sources"], key
        assert all(s in d["sources"] for s in v["sources"]), key
    assert default("SILL_DEF") == 0.05  # the JRC curve's own zero point


# --------------------------------------------------------------------------- #
# Plumbing — row, export, API schema, batch CSV                                #
# --------------------------------------------------------------------------- #
def test_the_row_keeps_t_through_finalise_and_the_export_has_its_column() -> None:
    row = ResultRow(
        facility_id="F1",
        facility_name="x",
        hazard_type="RF",
        model_id=ModelId.GLOBAL_BASELINE.value,
        flood_zero_damage_threshold_m=0.6,
        calculation_status="FULL",
    ).finalise()
    assert row.flood_zero_damage_threshold_m == 0.6
    assert "flood_zero_damage_threshold_m" in rt.HAZARD_RESULTS_COLUMNS
    frame = rt.hazard_results_frame([row])
    assert frame[0]["flood_zero_damage_threshold_m"] == 0.6
    # a dict round-trip (result.json) keeps it too
    assert rt.hazard_results_frame([row.to_dict()])[0]["flood_zero_damage_threshold_m"] == 0.6


def test_facility_spec_accepts_t_and_refuses_a_negative_one() -> None:
    pytest.importorskip("pydantic")
    from pydantic import ValidationError

    from climaterisk.engines.base import FacilitySpec, _optional_nonneg_float

    base = {"facility_id": "F1", "facility_name": "x", "latitude": 37.5, "longitude": 127.0}
    assert FacilitySpec(**base).flood_zero_damage_threshold_m is None
    assert (
        FacilitySpec(**base, flood_zero_damage_threshold_m=0.6).flood_zero_damage_threshold_m == 0.6
    )
    with pytest.raises(ValidationError):
        FacilitySpec(**base, flood_zero_damage_threshold_m=-0.1)
    # the portfolio asset property, as the mapping reads it
    assert _optional_nonneg_float(0.6) == 0.6 and _optional_nonneg_float("0.6") == 0.6
    assert _optional_nonneg_float(None) is None and _optional_nonneg_float("") is None
    assert _optional_nonneg_float(True) is None and _optional_nonneg_float(-1) is None


def _batch():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "physical_risk_batch", REPO / "scripts" / "physical_risk_batch.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def test_batch_csv_reads_the_optional_threshold_column(tmp_path: Path) -> None:
    cli = _batch()
    common = {"latitude": "37.5", "longitude": "127.0", "asset_value_usd": "1000000"}
    path = _write_csv(
        tmp_path / "f.csv",
        [
            {"facility_id": "A", **common, "flood_zero_damage_threshold_m": "0.6"},
            {"facility_id": "B", **common, "flood_zero_damage_threshold_m": ""},
        ],
    )
    facs = {f["facility_id"]: f for f in cli.read_facilities(path)}
    assert facs["A"]["flood_zero_damage_threshold_m"] == 0.6
    assert "flood_zero_damage_threshold_m" not in facs["B"]  # blank = not surveyed, not 0
    # the Korean header alias
    path_ko = _write_csv(
        tmp_path / "k.csv", [{"facility_id": "C", **common, "무피해문턱_m": "0.35"}]
    )
    assert cli.read_facilities(path_ko)[0]["flood_zero_damage_threshold_m"] == 0.35
    with pytest.raises(ValueError, match="negative"):
        cli.read_facilities(
            _write_csv(
                tmp_path / "n.csv",
                [{"facility_id": "D", **common, "flood_zero_damage_threshold_m": "-0.1"}],
            )
        )
    assert (
        "flood_zero_damage_threshold_m"
        not in cli.read_facilities(
            _write_csv(tmp_path / "o.csv", [{"facility_id": "E", **common}])
        )[0]
    )


# --------------------------------------------------------------------------- #
# CLIMADA side — needs the worker environment                                  #
# --------------------------------------------------------------------------- #
def _jrc():  # type: ignore[no-untyped-def]
    pytest.importorskip("climada")
    pytest.importorskip("climada_petals")
    from climaterisk_worker.physical_risk import registry

    return registry, registry.flood_impact_function("Asia", "commercial")


def test_threshold_at_the_curve_zero_returns_the_curve_itself() -> None:
    registry, f = _jrc()
    assert registry.thresholded_flood_impact_function(f, 0.05) is f
    assert registry.thresholded_flood_impact_function(f, 0.0) is f


def test_thresholded_curve_is_zero_up_to_t_and_unchanged_above() -> None:
    registry, f = _jrc()
    g = registry.thresholded_flood_impact_function(f, 0.6)
    d = np.array([0.1, 0.5, 0.6, 0.61, 1.0, 3.0, 8.0])
    jrc = np.interp(d, f.intensity, f.mdd * f.paa)
    np.testing.assert_allclose(g.calc_mdr(d), np.where(d > 0.6, jrc, 0.0), atol=1e-5)
    assert g.id == f.id and g.haz_type == f.haz_type
    assert g.name.endswith("| 0 up to 0.60 m")
    assert f.name.endswith("noPAA")  # the published object was not mutated


def test_impactcalc_eal_matches_the_analytic_sum() -> None:
    registry, f = _jrc()
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    depth = np.array([0.10, 0.40, 0.80, 1.50])
    freq = np.array([0.10, 0.05, 0.02, 0.01])
    haz = Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=np.array([37.5]), lon=np.array([127.0])),
        event_id=np.arange(1, 5),
        frequency=freq,
        intensity=sparse.csr_matrix(depth[:, None]),
        fraction=sparse.csr_matrix(np.full((4, 1), 0.5)),
        event_name=list("abcd"),
        date=np.zeros(4, int),
    )
    exp = Exposures(
        pd.DataFrame({"latitude": [37.5], "longitude": [127.0], "value": [1.0], "impf_RF": [f.id]})
    )

    def jrc(x: float) -> float:
        return float(np.interp(x, f.intensity, f.mdd * f.paa))

    for t in (0.05, 0.3, 0.6, 0.9, 2.0):
        g = registry.thresholded_flood_impact_function(f, t)
        got = (
            ImpactCalc(exp, ImpactFuncSet([g]), haz)
            .impact(save_mat=False, assign_centroids=True)
            .aai_agg
        )
        want = sum(fr * 0.5 * jrc(x) for x, fr in zip(depth, freq, strict=True) if x > t)
        assert got == pytest.approx(want, rel=1e-9, abs=1e-12), t
