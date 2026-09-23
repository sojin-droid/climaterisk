"""Phase 3 — hazard adapters and the three-model runner, against real CLIMADA objects.

Needs the worker environment. Hazards are synthetic where arithmetic is checked by hand,
so the tests run offline; the one test that prices cached Data API files is skipped when
the cache (or the network the client needs to resolve a dataset name) is absent.

Spec letters: A/B = flood global/country, C = same impact function across models,
D/E = TC global/country, F = heat financials null under every model, G = KOREA_LOCAL
produces no fake result, L = legacy runner untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")

import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker.physical_risk import adapters, engine, runner  # noqa: E402
from scipy import sparse  # noqa: E402

from climaterisk.physical_risk.metrics import CalculationStatus, ModelId  # noqa: E402
from climaterisk.physical_risk.models import READINESS  # noqa: E402

G, C, K = ModelId.GLOBAL_BASELINE.value, ModelId.DATA_API_COUNTRY.value, ModelId.KOREA_LOCAL.value
LAT, LON = 37.5, 127.0
FACILITY = {
    "facility_id": "KR-RE-001",
    "facility_name": "Concordian",
    "latitude": LAT,
    "longitude": LON,
    "asset_value_usd": 100_000_000.0,
    "asset_value_currency": "USD",
    "property_type": "office",
}


def synth(haz_type: str, units: str, intensity: list[float], freq: list[float]) -> Hazard:
    n = len(intensity)
    return Hazard(
        haz_type=haz_type,
        centroids=Centroids(lat=np.array([LAT]), lon=np.array([LON])),
        event_id=np.arange(1, n + 1),
        event_name=[f"e{i}" for i in range(n)],
        date=np.arange(730000, 730000 + n),
        frequency=np.array(freq, dtype=float),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(np.array(intensity, dtype=float).reshape(n, 1)),
        fraction=sparse.csr_matrix(np.ones((n, 1))),
        units=units,
    )


# --------------------------------------------------------------------------- #
# Adapter naming and readiness — pure                                          #
# --------------------------------------------------------------------------- #
def test_dataset_names_differ_only_by_coverage_and_record_the_served_scenario() -> None:
    g, g_served, g_h = adapters.dataapi_dataset_name(
        "RF", "rcp45", 2050, coverage="global", iso3=None
    )
    c, c_served, c_h = adapters.dataapi_dataset_name(
        "RF", "rcp45", 2050, coverage="country", iso3="KOR"
    )
    assert g == "river_flood_150arcsec_rcp60_2030_2050"
    assert c == "river_flood_150arcsec_rcp60_KOR_2030_2050"
    assert g_served == c_served == "rcp60" and g_h == c_h == "2030–2050"  # rcp45 is not published
    assert (
        adapters.dataapi_dataset_name("RF", "rcp60", 2050, coverage="global", iso3=None)[1]
        == "rcp60"
    )
    assert adapters.dataapi_dataset_name("RF", "historical", 1990, coverage="country", iso3="KOR")[
        0
    ] == ("river_flood_150arcsec_hist_KOR_1980_2000")
    assert adapters.dataapi_dataset_name("TC", "rcp45", 2045, coverage="global", iso3=None)[0] == (
        "tropical_cyclone_10synth_tracks_150arcsec_rcp45_global_2040"
    )
    assert adapters.dataapi_dataset_name("TC", "historical", 2000, coverage="country", iso3="KOR")[
        0
    ] == ("tropical_cyclone_10synth_tracks_150arcsec_KOR_1980_2020")
    with pytest.raises(ValueError):
        adapters.dataapi_dataset_name("RF", "rcp45", 2050, coverage="country", iso3=None)


def test_adapter_statuses_match_the_backend_readiness_table() -> None:
    assert adapters.readiness_matches_backend()
    mine = adapters.adapter_statuses()
    assert mine == {k: {m: c["status"] for m, c in v.items()} for k, v in READINESS.items()}


def test_country_adapter_describes_itself_as_data_api_not_korea_local() -> None:
    d = adapters.CountryFloodAdapter("rcp60", 2050, "KOR").describe()
    assert d.model_id == C and d.hazard_country == "KOR"
    assert "Data API" in d.hazard_source and "not Korea-local" in (d.detail or "")
    assert d.requested_scenario == d.served_scenario == "rcp60"
    d45 = adapters.CountryFloodAdapter("rcp45", 2050, "KOR").describe()
    assert (d45.requested_scenario, d45.served_scenario) == ("rcp45", "rcp60")


# --------------------------------------------------------------------------- #
# Test G — the KOREA_LOCAL interfaces refuse to produce data                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("klass", [adapters.KoreaLocalFloodAdapter, adapters.KoreaLocalTCAdapter])
def test_g_korea_local_flood_and_tc_adapters_load_nothing_and_say_why(klass) -> None:  # type: ignore[no-untyped-def]
    a = klass("rcp45", 2050, "KOR")
    assert a.status == adapters.NOT_IMPLEMENTED and a.model_id == K
    assert a.load() is None and a.load((30.0, 120.0, 40.0, 130.0)) is None
    d = a.describe()
    assert d.hazard_dataset is None and d.served_scenario is None
    assert "no domestic" in (d.detail or "")


def test_g_runner_emits_not_implemented_rows_with_null_numbers_for_korea_local() -> None:
    out = runner.compute_physical_risk_models(
        {
            "facilities": [FACILITY],
            "climate_scenario": "rcp45",
            "target_year": 2050,
            "hazards": ["RF", "TC"],
            "models": [K],
            "country": "KOR",
        }
    )
    assert out["status"] == "ok" and len(out["rows"]) == 2
    for r in out["rows"]:
        assert (
            r["model_id"] == K
            and r["calculation_status"] == CalculationStatus.NOT_IMPLEMENTED.value
        )
        for f in (
            "eal_usd",
            "potential_loss_usd",
            "eal_as_pct_of_assets",
            "risk_level",
            "hazard_intensity",
        ):
            assert r[f] is None, f
        assert r["hazard_dataset"] is None
    assert all(a["status"] == adapters.NOT_IMPLEMENTED for a in out["adapters"])


# --------------------------------------------------------------------------- #
# Tests A–E on synthetic hazards: the model id changes, the impact function does not  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("haz_type", "units", "intensity", "expected_if"),
    [("RF", "m", [0.5, 1.0, 2.0, 4.0], 22), ("TC", "m/s", [20.0, 40.0, 60.0, 80.0], 9)],
)
def test_abcde_global_and_country_rows_use_the_same_published_function(
    haz_type: str, units: str, intensity: list[float], expected_if: int
) -> None:
    hazard = synth(haz_type, units, intensity, [0.5, 0.2, 0.05, 0.01])
    rows = {
        m: engine.calculate(
            FACILITY,
            hazard,
            haz_type,
            model_id=m,
            iso3="KOR",
            requested_scenario="rcp60",
            served_scenario="rcp60",
            hazard_country="KOR" if m == C else None,
        )
        for m in (G, C)
    }
    for r in rows.values():
        assert r.calculation_status == CalculationStatus.FULL.value
        assert r.impact_function_id == expected_if
        assert r.eal_usd > 0 and r.potential_loss_usd > 0
    # Test C: identical impact function, and with an identical hazard identical numbers
    assert rows[G].impact_function_id == rows[C].impact_function_id
    assert rows[G].impact_function_source == rows[C].impact_function_source
    assert rows[G].eal_usd == pytest.approx(rows[C].eal_usd)
    assert rows[C].hazard_country == "KOR" and rows[G].hazard_country is None


def test_h_engine_marks_a_served_scenario_that_differs_from_the_requested_one() -> None:
    hazard = synth("RF", "m", [0.5, 1.0, 2.0, 4.0], [0.5, 0.2, 0.05, 0.01])
    row = engine.calculate(
        FACILITY,
        hazard,
        "RF",
        model_id=C,
        iso3="KOR",
        requested_scenario="rcp45",
        served_scenario="rcp60",
    )
    assert row.calculation_status == CalculationStatus.SCENARIO_MISMATCH.value
    assert row.eal_usd is None and row.impact_function_id == 22  # provenance kept, number withheld
    assert row.hazard_intensity == pytest.approx(4.0)  # the hazard itself is still described


# --------------------------------------------------------------------------- #
# Test F — heat has null financials under every model                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", [G, C, K])
def test_f_heat_rows_never_carry_money_under_any_model(model: str) -> None:
    hazard = synth("HW", "degC", [30.0, 33.0, 36.0], [0.5, 0.2, 0.05])
    row = engine.calculate(FACILITY, hazard, "HW", model_id=model, iso3="KOR")
    assert row.calculation_status == CalculationStatus.HAZARD_ONLY.value
    for f in (
        "eal_usd",
        "potential_loss_usd",
        "eal_as_pct_of_assets",
        "risk_level",
        "climate_change_multiplier",
    ):
        assert getattr(row, f) is None, f
    assert row.hazard_intensity == pytest.approx(36.0)


def test_f_runner_heat_cells_follow_the_readiness_table() -> None:
    out = runner.compute_physical_risk_models(
        {
            "facilities": [FACILITY],
            "climate_scenario": "rcp45",
            "target_year": 2030,
            "hazards": ["HEAT"],
            "country": "KOR",
        }
    )
    by_model = {r["model_id"]: r for r in out["rows"]}
    assert by_model[G]["calculation_status"] == CalculationStatus.NO_HAZARD_DATA.value
    assert by_model[C]["calculation_status"] == CalculationStatus.NO_HAZARD_DATA.value
    k = by_model[K]
    if k["hazard_dataset"] is None:
        pytest.skip("local catalog has no KOR heatwave layer on this machine")
    assert k["calculation_status"] == CalculationStatus.HAZARD_ONLY.value
    assert k["eal_usd"] is None and k["hazard_intensity"] is not None
    assert "KMA" in k["hazard_source"]


# --------------------------------------------------------------------------- #
# Real cached Data API files: global vs country cut must agree exactly          #
# --------------------------------------------------------------------------- #
def test_country_cut_of_the_same_data_api_product_prices_identically() -> None:
    """The KOR TC set is the global set cropped, so the two models must give one number.

    Uses only files already in the Data API cache; skipped when they (or the name lookup)
    are unavailable.
    """
    try:
        out = runner.compute_physical_risk_models(
            {
                "facilities": [FACILITY],
                "climate_scenario": "rcp45",
                "target_year": 2040,
                "hazards": ["TC"],
                "models": [G, C],
                "country": "KOR",
            }
        )
    except Exception as exc:  # pragma: no cover - network / cache dependent
        pytest.skip(f"Data API unavailable: {exc}")
    rows = {r["model_id"]: r for r in out["rows"]}
    if any(r["calculation_status"] == "ERROR" for r in rows.values()):
        pytest.skip(
            "Data API files not cached: "
            + "; ".join(str(r["status_detail"]) for r in rows.values())
        )
    assert rows[G]["impact_function_id"] == rows[C]["impact_function_id"] == 9
    assert rows[G]["hazard_dataset"].endswith("_global_2040") and rows[C][
        "hazard_dataset"
    ].endswith("_KOR_2040")
    assert rows[G]["eal_usd"] == pytest.approx(rows[C]["eal_usd"])
    assert rows[G]["hazard_intensity"] == pytest.approx(rows[C]["hazard_intensity"])
    cmp = out["comparisons"]["global_vs_country"][0]
    assert cmp["available"] is True and cmp["eal_change_pct"] == pytest.approx(0.0)
    assert out["impact_function_fixed"]["TC"] is True


def test_cropping_a_hazard_to_the_facility_does_not_change_the_price() -> None:
    hazard = synth("TC", "m/s", [20.0, 40.0, 60.0, 80.0], [0.5, 0.2, 0.05, 0.01])
    cropped = adapters._crop(hazard, (LAT, LON, LAT, LON))
    a = engine.calculate(FACILITY, hazard, "TC", model_id=G, iso3="KOR")
    b = engine.calculate(FACILITY, cropped, "TC", model_id=G, iso3="KOR")
    assert a.eal_usd == pytest.approx(b.eal_usd) and a.potential_loss_usd == pytest.approx(
        b.potential_loss_usd
    )


# --------------------------------------------------------------------------- #
# Test L — the legacy runner is not touched by this phase                      #
# --------------------------------------------------------------------------- #
def test_l_legacy_runner_module_does_not_import_the_new_engine() -> None:
    src = (REPO / "worker" / "climaterisk_worker" / "physical.py").read_text(encoding="utf-8")
    assert "physical_risk.engine" not in src and "physical_risk.adapters" not in src
    assert "_flood_impf_set" in src  # the 8-point legacy path still exists, unchanged in role
