"""Gate tests: river-flood headline = official map (present) × ISIMIP change factor (future).

User decision 2026-11-15: the Korea-local river model (환경부 홍수위험지도, T2) becomes the
headline. Present climate = the map as it is. Future = the same map with every event's
frequency multiplied by an ISIMIP change factor

    f = EAL_ISIMIP(rcp, future window) / EAL_ISIMIP(rcp, 2010–2030)

computed on a unit exposure at every ISIMIP KOR centroid (same RCP, same model chain —
the like-for-like rule of runner.baseline_groups). Depth stays the present-day map depth.
Main method: one national factor. Sub check (diagnostic only): per-시도 factors, warn when
the asset-weighted provincial factor departs from the national one by more than a tolerance.

These run in the CLIMADA worker environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")

import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker import flood_delta as fdl  # noqa: E402
from climaterisk_worker.physical_risk import adapters  # noqa: E402
from numpy.testing import assert_allclose  # noqa: E402
from scipy import sparse  # noqa: E402

from climaterisk.physical_risk import display_copy, models  # noqa: E402


# --------------------------------------------------------------------------- #
# unit_eal — expected annual damage ratio per centroid for a unit exposure      #
# --------------------------------------------------------------------------- #
def test_unit_eal_matches_hand_calculation() -> None:
    """EAL_c = Σ_e freq_e · MDD(I_ec) · PAA(I_ec) · fraction_ec (CLIMADA's impact rule)."""
    intensity = sparse.csr_matrix([[1.0, 0.0, 5.0], [2.0, 0.0, 0.0]])
    fraction = sparse.csr_matrix([[1.0, 0.0, 0.5], [1.0, 0.0, 0.0]])
    freq = np.array([0.01, 0.002])
    x = np.array([0.0, 10.0])  # depth (m)
    mdd = np.array([0.0, 1.0])  # linear: 10 % per metre
    paa = np.array([1.0, 1.0])
    eal = fdl.unit_eal(intensity, fraction, freq, x, mdd, paa)
    # c0: 0.01·0.1·1 + 0.002·0.2·1 = 0.0014 ; c1: 0 ; c2: 0.01·0.5·0.5 = 0.0025
    assert_allclose(eal, [0.0014, 0.0, 0.0025], rtol=1e-12, atol=0)


def test_unit_eal_without_fraction_treats_fraction_as_one() -> None:
    intensity = sparse.csr_matrix([[4.0]])
    eal = fdl.unit_eal(
        intensity,
        None,
        np.array([0.1]),
        np.array([0, 10.0]),
        np.array([0, 1.0]),
        np.array([1.0, 1.0]),
    )
    assert_allclose(eal, [0.04], rtol=1e-12)


# --------------------------------------------------------------------------- #
# change_factor — national and per-region ratio of future to present EAL        #
# --------------------------------------------------------------------------- #
def test_change_factor_national_and_by_region() -> None:
    present = np.array([1.0, 1.0, 2.0, 0.0, 0.0])
    future = np.array([2.0, 1.0, 2.0, 0.0, 1.0])
    regions = np.array(["A", "A", "B", "B", "C"])
    out = fdl.change_factor(present, future, regions, min_wet=1)
    assert out["national"] == pytest.approx(6.0 / 4.0)
    assert out["by_region"]["A"] == pytest.approx(1.5)
    assert out["by_region"]["B"] == pytest.approx(1.0)
    assert out["by_region"]["C"] is None  # present EAL 0 → no ratio, never inf
    assert out["n_wet"] == {"A": 2, "B": 1, "C": 1}


def test_change_factor_needs_enough_wet_centroids_per_region() -> None:
    present = np.array([1.0, 1.0, 2.0])
    future = np.array([2.0, 1.0, 2.0])
    out = fdl.change_factor(present, future, np.array(["A", "A", "B"]), min_wet=2)
    assert out["by_region"]["A"] == pytest.approx(1.5)
    assert out["by_region"]["B"] is None


def test_change_factor_national_none_when_no_present_loss() -> None:
    out = fdl.change_factor(np.zeros(3), np.ones(3), np.array(["A", "A", "A"]), min_wet=1)
    assert out["national"] is None


# --------------------------------------------------------------------------- #
# regional_check — the sub check (diagnostic only)                              #
# --------------------------------------------------------------------------- #
def test_regional_check_quiet_when_provinces_agree() -> None:
    by_region = {"A": 1.5, "B": 1.0, "C": None}
    s = fdl.regional_check(1.5, by_region, ["A", "A", "B", "C"], tol=0.25)
    assert s["weighted_factor"] == pytest.approx(4.0 / 3.0)  # C has no factor: left out
    assert s["n_assets_without_factor"] == 1
    assert s["warn"] is False


def test_regional_check_warns_when_provinces_depart() -> None:
    s = fdl.regional_check(1.5, {"B": 1.0}, ["B", "B", "B"], tol=0.25)
    assert s["weighted_factor"] == pytest.approx(1.0)
    assert s["warn"] is True
    assert "1.00" in s["warn_reason"] and "1.50" in s["warn_reason"]


def test_regional_check_no_verdict_without_any_provincial_factor() -> None:
    s = fdl.regional_check(1.5, {"A": None}, ["A"], tol=0.25)
    assert s["weighted_factor"] is None
    assert s["warn"] is None


# --------------------------------------------------------------------------- #
# scale_frequency — climate change enters as frequency, depth untouched         #
# --------------------------------------------------------------------------- #
def _tiny_hazard() -> Hazard:
    haz = Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=np.array([37.5, 37.6]), lon=np.array([127.0, 127.1])),
        event_id=np.array([1, 2]),
        event_name=["rp100", "rp500"],
        date=np.zeros(2, dtype=int),
        frequency=np.array([0.008, 0.002]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix([[0.75, 0.0], [1.5, 3.5]]),
        fraction=sparse.csr_matrix([[1.0, 0.0], [1.0, 1.0]]),
    )
    haz.check()
    return haz


def test_scale_frequency_multiplies_frequency_only() -> None:
    haz = _tiny_hazard()
    out = fdl.scale_frequency(haz, 1.5)
    assert_allclose(out.frequency, [0.012, 0.003], rtol=1e-12)
    assert_allclose(out.intensity.toarray(), haz.intensity.toarray())
    assert_allclose(haz.frequency, [0.008, 0.002])  # input untouched


def test_scale_frequency_rejects_non_positive_factor() -> None:
    with pytest.raises(ValueError):
        fdl.scale_frequency(_tiny_hazard(), 0.0)


# --------------------------------------------------------------------------- #
# The adapter and the headline                                                  #
# --------------------------------------------------------------------------- #
def test_korea_local_flood_serves_future_scenarios_through_the_delta() -> None:
    d = adapters.KoreaLocalFloodAdapter("rcp45", 2050).describe()
    assert d.served_scenario == "rcp60"  # RF_SCENARIO_MAP, as for the Data API models
    assert d.time_horizon == "2030–2050"
    assert d.hazard_dataset.startswith("floodmap_rfm_depth_class")
    assert "rcp60" in d.hazard_dataset and "2010_2030" in d.hazard_dataset
    assert "frequency" in (d.detail or "").lower()


def test_korea_local_flood_historical_is_the_map_as_is() -> None:
    d = adapters.KoreaLocalFloodAdapter("historical", 2050).describe()
    assert d.served_scenario == "historical"
    assert d.time_horizon == "present"
    assert d.hazard_dataset == "floodmap_rfm_depth_class"


def test_river_headline_is_the_official_map() -> None:
    cell = models.READINESS["RF"]["KOREA_LOCAL"]
    assert cell["status"] == "READY"
    assert cell.get("trial") != "yes"
    assert display_copy.recommended_models(["RF"]) == {"RF": "KOREA_LOCAL"}
