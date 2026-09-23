"""Phase 7 — asset-level validation against the live engine (worker env, cached Data API).

Re-runs the fixture portfolio through the real chain — Data API KOR hazards, published
CLIMADA impact functions, ``ImpactCalc``, the engine's metrics — and compares with the
recorded evidence in ``tests/fixtures/asset_level_validation/``. Skips when the Data API
cache (or the name lookup it needs) is unavailable; never downloads a Global set.

Cases: A positive flood loss (Gwangju), B zero flood loss (Seoul), C positive TC loss
(Busan), D low TC loss (Chuncheon — no Korean cell is wind-free in this product),
E heatwave hazard-only (every site). Plus the return-period guard on the real
observed-only TC set, and the batch / Recommended-mode behaviour.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")

from climaterisk_worker.physical_risk import engine, runner  # noqa: E402

from climaterisk.physical_risk.metrics import max_resolvable_return_period  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "asset_level_validation"
A, B, C, D, E = "VAL-A-GWANGJU", "VAL-B-SEOUL", "VAL-C-BUSAN", "VAL-D-CHUNCHEON", "VAL-E-INCHEON"
COUNTRY, LOCAL = "DATA_API_COUNTRY", "KOREA_LOCAL"


def _cli():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "physical_risk_batch", REPO / "scripts" / "physical_risk_batch.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def live_run() -> dict:  # type: ignore[type-arg]
    """The fixture portfolio under Recommended, rcp85 / 2040 — the same call the CLI makes."""
    cli = _cli()
    facilities = cli.read_facilities(FIX / "validation_portfolio.csv")
    models = cli.parse_models(["recommended"], ["RF", "TC", "HEAT"])
    assert models == [COUNTRY, LOCAL]
    try:
        out = runner.compute_physical_risk_models(
            {
                "facilities": facilities,
                "climate_scenario": "rcp85",
                "target_year": 2040,
                "hazards": ["RF", "TC", "HEAT"],
                "models": models,
                "country": "KOR",
            }
        )
    except Exception as exc:  # pragma: no cover - network / cache dependent
        pytest.skip(f"Data API unavailable: {exc}")
    if any(r["calculation_status"] == "ERROR" for r in out["rows"]):
        pytest.skip(
            "Data API KOR sets not cached: "
            + str(
                [r["status_detail"] for r in out["rows"] if r["calculation_status"] == "ERROR"][:1]
            )
        )
    return out


def _by(out: dict) -> dict[tuple[str, str, str], dict]:  # type: ignore[type-arg]
    return {(r["facility_id"], r["hazard_type"], r["model_id"]): r for r in out["rows"]}


def test_batch_runs_every_facility_x_hazard_x_model_once_and_never_touches_global(live_run) -> None:  # type: ignore[no-untyped-def]
    assert len(live_run["rows"]) == 5 * 3 * 2
    keys = [(r["facility_id"], r["hazard_type"], r["model_id"]) for r in live_run["rows"]]
    assert len(set(keys)) == 30
    # one adapter per hazard x model — the hazard was loaded once, not once per asset
    assert len(live_run["adapters"]) == 6
    assert not any(a["model_id"] == "GLOBAL_BASELINE" for a in live_run["adapters"])
    assert live_run["impact_function_fixed"] == {"RF": True, "TC": True, "HW": True}


def test_live_results_reproduce_the_recorded_evidence(live_run) -> None:  # type: ignore[no-untyped-def]
    """The canonical regression check: same inputs, same numbers (to 1e-6 relative)."""
    recorded = _by(
        json.loads((FIX / "run_rcp85_2040_recommended.json").read_text(encoding="utf-8"))
    )
    live = _by(live_run)
    assert set(live) == set(recorded)
    for key, rec in recorded.items():
        now = live[key]
        assert now["calculation_status"] == rec["calculation_status"], key
        assert now["impact_function_id"] == rec["impact_function_id"], key
        assert now["hazard_dataset"] == rec["hazard_dataset"], key
        for f in ("hazard_intensity", "eal_usd", "potential_loss_usd", "eal_as_pct_of_assets"):
            if rec[f] is None:
                assert now[f] is None, (key, f)
            else:
                assert now[f] == pytest.approx(rec[f], rel=1e-6), (key, f)
        assert now["risk_level"] == rec["risk_level"], key


def test_positive_and_zero_flood_cases_on_live_data(live_run) -> None:  # type: ignore[no-untyped-def]
    by = _by(live_run)
    a, b = by[(A, "RF", COUNTRY)], by[(B, "RF", COUNTRY)]
    assert a["calculation_status"] == b["calculation_status"] == "FULL"
    assert a["hazard_intensity"] > 0 and a["potential_loss_usd"] > 0 and a["eal_usd"] > 0
    assert a["risk_level"] == "High"
    assert b["hazard_intensity"] == 0 and b["potential_loss_usd"] == 0 and b["eal_usd"] == 0
    assert b["risk_level"] == "Low"  # a computed zero is priced, and Low — not "not available"


def test_tc_cases_on_live_data(live_run) -> None:  # type: ignore[no-untyped-def]
    by = _by(live_run)
    c, d = by[(C, "TC", COUNTRY)], by[(D, "TC", COUNTRY)]
    assert c["eal_usd"] > d["eal_usd"] > 0 and c["hazard_intensity"] > d["hazard_intensity"]
    assert c["impact_function_id"] == d["impact_function_id"] == 9


def test_heat_is_hazard_only_on_live_data(live_run) -> None:  # type: ignore[no-untyped-def]
    by = _by(live_run)
    for fid in (A, B, C, D, E):
        r = by[(fid, "HW", LOCAL)]
        assert r["calculation_status"] == "HAZARD_ONLY" and r["hazard_intensity"] > 30
        assert r["eal_usd"] is None and r["risk_level"] is None


def test_return_period_guard_on_the_real_observed_tc_set() -> None:
    """3,890 observed-period events cannot express a 100-year loss; CLIMADA would saturate."""
    files = glob.glob(
        os.path.expanduser(
            "~/climada/data/hazard/tropical_cyclone/"
            "tropical_cyclone_0synth_tracks_150arcsec_historical_KOR_1980_2020/*/*.hdf5"
        )
    )
    if not files:
        pytest.skip("observed-only KOR TC set not cached")
    from climada.hazard import Hazard

    h = Hazard.from_hdf5(files[0])
    ceiling = max_resolvable_return_period([float(x) for x in h.frequency])
    assert ceiling is not None and ceiling < 100
    fac = {
        "facility_id": C,
        "facility_name": "Busan",
        "latitude": 35.18,
        "longitude": 129.08,
        "asset_value_usd": 6e7,
        "property_type": "warehouse",
    }
    r = engine.calculate(fac, h, "TC", model_id=COUNTRY, iso3="KOR")
    assert r.calculation_status == "RETURN_PERIOD_NOT_RESOLVABLE"
    assert r.potential_loss_usd is None  # no fake 100-year PML
    assert (
        r.eal_usd is not None and r.eal_usd > 0 and r.risk_level is not None
    )  # EAL needs no extrapolation
    assert f"{ceiling:.1f}-year ceiling" in (r.status_detail or "")
