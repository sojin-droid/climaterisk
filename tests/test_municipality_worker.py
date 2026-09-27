"""V0.2 — municipality runs through the real engine, and spatial resolution read from data.

Needs the worker environment (CLIMADA). Synthetic hazards check the arithmetic offline:

* a municipality point with no asset value is screened — hazard intensity and resolution
  on the row, ``NO_EXPOSURE_DATA``, null money; the same point with a supplied value is
  priced with the same published impact function;
* the runner tags every row with ``assessment_target = MUNICIPALITY`` and the municipality
  fields, and records the measured resolution on the adapter description;
* the measured grid spacing of the cached Data API / KMA layers equals the declared
  registry value (skipped when the cache is absent) — the resolution table is verified
  against data, not only against itself.
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

from climaterisk.physical_risk import coverage as cov  # noqa: E402
from climaterisk.physical_risk.metrics import CalculationStatus, ModelId  # noqa: E402
from climaterisk.physical_risk.municipalities import by_id  # noqa: E402

C, K = ModelId.DATA_API_COUNTRY.value, ModelId.KOREA_LOCAL.value
GWANGJU = by_id(["SGIS-24"])[0]


def grid_hazard(haz_type: str, units: str, step_deg: float, value: float) -> Hazard:
    """A 5x5 grid around Gwangju's point, two uniform events (0.1/yr and 0.005/yr).

    The rarest event is a 200-year one, so the configured 100-year potential loss is
    resolvable and a priced row is ``FULL`` (not ``RETURN_PERIOD_NOT_RESOLVABLE``).
    """
    lat0, lon0 = GWANGJU.latitude, GWANGJU.longitude
    lats = lat0 + step_deg * np.arange(-2, 3)
    lons = lon0 + step_deg * np.arange(-2, 3)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    cen = Centroids(lat=la.ravel(), lon=lo.ravel())
    n = cen.size
    intensity = sparse.csr_matrix(np.full((2, n), value))
    return Hazard(
        haz_type=haz_type,
        units=units,
        centroids=cen,
        event_id=np.array([1, 2]),
        event_name=["a", "b"],
        date=np.array([1, 2]),
        orig=np.array([True, True]),
        frequency=np.array([0.1, 0.005]),
        intensity=intensity,
        fraction=sparse.csr_matrix(np.ones((2, n))),
    )


def test_measure_grid_spacing_reads_the_native_step() -> None:
    # centroids are rounded to 1e-6 deg before differencing, hence the tolerance
    assert adapters.measure_grid_spacing(grid_hazard("RF", "m", 150 / 3600, 1.0)) == pytest.approx(
        150 / 3600, rel=1e-4
    )
    assert adapters.measure_grid_spacing(grid_hazard("HW", "degC", 0.05, 30.0)) == pytest.approx(
        0.05
    )
    rec = adapters.resolution_record(
        150.0, "arcsec", "grid cell", source="test", measured_deg=150 / 3600, dataset_family="x"
    )
    assert rec["consistent_with_measurement"] is True and "~4–5 km" in rec["description"]
    bad = adapters.resolution_record(
        150.0, "arcsec", "grid cell", source="test", measured_deg=0.25, dataset_family="x"
    )
    assert bad["consistent_with_measurement"] is False and "NOTE" in bad["description"]


def test_screening_then_pricing_the_same_municipality_point() -> None:
    hazard = grid_hazard("RF", "m", 150 / 3600, 2.0)
    res = adapters.resolution_record(
        150.0, "arcsec", "grid cell", source="test", measured_deg=150 / 3600, dataset_family="t"
    )
    screened = engine.calculate(
        GWANGJU.as_facility(),
        hazard,
        "RF",
        model_id=C,
        iso3="KOR",
        requested_scenario="rcp85",
        served_scenario="rcp85",
        spatial_resolution=res,
    )
    assert screened.calculation_status == CalculationStatus.NO_EXPOSURE_DATA.value
    assert (
        screened.assessment_target == "MUNICIPALITY" and screened.municipality_name == "광주광역시"
    )
    assert screened.hazard_intensity == pytest.approx(2.0) and screened.hazard_intensity_unit == "m"
    assert screened.spatial_resolution == 150.0 and screened.spatial_unit_type == "grid cell"
    assert (
        screened.eal_usd is None
        and screened.potential_loss_usd is None
        and screened.risk_level is None
    )
    assert (
        screened.impact_function_id is not None
    )  # the function was resolved, the value was missing
    assert "no asset value supplied for this municipality" in (screened.status_detail or "")

    priced = engine.calculate(
        GWANGJU.as_facility(80_000_000.0, "office"),
        hazard,
        "RF",
        model_id=C,
        iso3="KOR",
        requested_scenario="rcp85",
        served_scenario="rcp85",
        spatial_resolution=res,
    )
    assert priced.calculation_status == CalculationStatus.FULL.value
    assert priced.impact_function_id == screened.impact_function_id  # same published function
    assert priced.eal_usd is not None and priced.eal_usd > 0 and priced.risk_level is not None
    assert priced.asset_value_usd == 80_000_000.0 and priced.latitude == GWANGJU.latitude


def test_runner_tags_rows_and_records_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCountryFlood:
        model_id, status, hazard_type = C, adapters.READY, "RF"

        def __init__(self, *a, **k) -> None:  # type: ignore[no-untyped-def]
            pass

        def describe(self) -> adapters.HazardDescription:
            return adapters.HazardDescription(
                "RF",
                C,
                adapters.READY,
                "synthetic",
                "syn_rf",
                "v1",
                "KOR",
                "rcp85",
                "rcp85",
                "2040",
                None,
            )

        def load(self, bbox=None):  # type: ignore[no-untyped-def]
            return grid_hazard("RF", "m", 150 / 3600, 1.0)

        def spatial_resolution(self, hazard):  # type: ignore[no-untyped-def]
            return adapters.resolution_record(
                150.0,
                "arcsec",
                "grid cell",
                source="test",
                measured_deg=adapters.measure_grid_spacing(hazard),
                dataset_family="syn_rf",
            )

    original = adapters.adapter_for

    def fake_adapter_for(hazard_type, model_id, scenario, year, iso3):  # type: ignore[no-untyped-def]
        if hazard_type == "RF" and model_id == C:
            return FakeCountryFlood()
        return original(hazard_type, model_id, scenario, year, iso3)

    monkeypatch.setattr(runner.adapters, "adapter_for", fake_adapter_for)
    seoul, gwangju = by_id(["SGIS-11", "SGIS-24"])
    out = runner.compute_physical_risk_models(
        {
            "assessment_target": "MUNICIPALITY",
            "facilities": [seoul.as_facility(), gwangju.as_facility(50_000_000.0, "office")],
            "climate_scenario": "rcp85",
            "target_year": 2040,
            "hazards": ["RF"],
            "models": [C, K],
            "country": "KOR",
        }
    )
    assert out["assessment_target"] == "MUNICIPALITY"
    assert out["municipality_dataset"]["total"] >= 217
    assert len(out["rows"]) == 2 * 2
    assert all(r["assessment_target"] == "MUNICIPALITY" for r in out["rows"])
    by = {(r["facility_id"], r["model_id"]): r for r in out["rows"]}
    assert by[("SGIS-11", C)]["calculation_status"] == "NO_EXPOSURE_DATA"
    assert by[("SGIS-11", C)]["spatial_resolution"] == 150.0
    assert by[("SGIS-24", C)]["calculation_status"] == "FULL" and by[("SGIS-24", C)]["eal_usd"] > 0
    assert by[("SGIS-24", K)]["calculation_status"] == "NOT_IMPLEMENTED"
    assert by[("SGIS-24", K)]["spatial_resolution"] is None
    used = {(a["hazard_type"], a["model_id"]): a for a in out["adapters"]}
    assert used[("RF", C)]["spatial_resolution"]["value"] == 150.0
    assert used[("RF", K)]["spatial_resolution"] is None


@pytest.mark.parametrize(
    ("path", "key", "model"),
    [
        ("river_flood/RF_rcp45_KOR_2050.hdf5", "RF", C),
        ("tropical_cyclone/TC_rcp45_KOR_2040.hdf5", "TC", C),
        ("heatwave/HW_rcp85_KOR_2030.hdf5", "HEAT", K),
    ],
)
def test_measured_resolution_of_cached_layers_equals_the_registry(
    path: str, key: str, model: str
) -> None:
    from climaterisk_worker import catalog

    file = catalog.catalog_dir() / path
    if not file.is_file():
        pytest.skip(f"cached layer {path} not present")
    hazard = Hazard.from_hdf5(str(file))
    measured = adapters.measure_grid_spacing(hazard)
    declared = cov.resolution_for(key, model)
    assert declared is not None
    declared_deg = declared["value"] / 3600.0 if declared["unit"] == "arcsec" else declared["value"]
    assert measured == pytest.approx(declared_deg, rel=1e-3)
