"""Cost-benefit honours ``request["peril"]`` — no silent tropical-cyclone fallback.

The unsupported-peril branch runs before any CLIMADA import (backend env). The
``tropical_cyclone`` computation test needs CLIMADA and a synthetic hazard, so it is skipped
where CLIMADA is absent (BLOCKED BY ENVIRONMENT, not a pass).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import cost_benefit as cb  # noqa: E402

_ASSET = {
    "id": "a",
    "name": "Busan plant",
    "lat": 35.18,
    "lon": 129.08,
    "value": 1.0e8,
    "currency": "KRW",
    "tc_v_half": 70.0,
}
_MEASURE = {"name": "wind retrofit", "cost": 1.0e6, "damage_reduction": 0.5}


def _req(peril: str, measures=(_MEASURE,)):  # type: ignore[no-untyped-def]
    return {
        "peril": peril,
        "assets": [_ASSET],
        "climate_scenario": "rcp45",
        "anchor_years": [2040],
        "measures": list(measures),
    }


@pytest.mark.parametrize("peril", ["river_flood", "wildfire", "heat_mortality", "made_up"])
def test_unsupported_peril_returns_structured_error_not_tc_numbers(peril: str) -> None:
    out = cb.compute_cost_benefit(_req(peril))
    assert out["status"] == "error"
    assert out["peril"] == peril
    assert out["measures"] == []
    assert "tropical_cyclone" in out["detail"] and "Not computed" in out["detail"]


def test_missing_measures_still_errors_and_echoes_the_peril() -> None:
    out = cb.compute_cost_benefit(_req("tropical_cyclone", measures=()))
    assert out["status"] == "error" and out["peril"] == "tropical_cyclone"


def test_only_tropical_cyclone_is_wired() -> None:
    assert set(cb._SUPPORTED_PERILS) == {"tropical_cyclone"}
    assert cb._SUPPORTED_PERILS["tropical_cyclone"] == "TC"


def test_tropical_cyclone_cost_benefit_computes_on_a_synthetic_hazard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Computation test: CostBenefit on a two-event synthetic TC hazard at the asset.

    A 50 % damage-reduction measure must avert a positive, finite share of the NPV risk.
    """
    pytest.importorskip("climada")
    import numpy as np
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    def synthetic_tc(iso3, scenario, ref_year):  # type: ignore[no-untyped-def]
        cent = Centroids(lat=np.array([_ASSET["lat"]]), lon=np.array([_ASSET["lon"]]))
        haz = Hazard(
            haz_type="TC",
            units="m/s",
            centroids=cent,
            event_id=np.array([1, 2]),
            event_name=["e1", "e2"],
            date=np.array([738000, 738365]),
            frequency=np.array([0.1, 0.02]),
            intensity=sparse.csr_matrix(np.array([[35.0], [60.0]])),
            fraction=sparse.csr_matrix(np.array([[1.0], [1.0]])),
        )
        return haz

    monkeypatch.setattr(cb, "_tc_hazard", synthetic_tc)
    monkeypatch.setattr(cb, "_per_asset_iso3", lambda lats, lons: ["KOR"] * len(lats))
    out = cb.compute_cost_benefit(_req("tropical_cyclone"))
    assert out["status"] == "ok", out.get("detail")
    assert out["peril"] == "tropical_cyclone"
    assert out["tot_climate_risk"] > 0
    m = out["measures"][0]
    assert 0 < m["benefit"] <= out["tot_climate_risk"] * 1.0001
    assert m["benefit_cost_ratio"] == pytest.approx(m["benefit"] / _MEASURE["cost"])
    assert "WP4" in out["detail"]  # regional v_half default was applied (KOR → Eberenz WP4)
