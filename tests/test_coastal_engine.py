"""Gate: a hazard-only adapter yields HAZARD_ONLY rows with the intensity kept and money null."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("climada")
REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker.physical_risk import engine  # noqa: E402
from scipy import sparse  # noqa: E402

FAC = {
    "facility_id": "X",
    "facility_name": "x",
    "latitude": 34.6249,
    "longitude": 126.4211,
    "asset_value_usd": 1e6,
    "asset_value_currency": "USD",
    "property_type": "office",
}


def test_hazard_only_flag_keeps_intensity_and_nulls_money() -> None:
    h = Hazard(
        haz_type="CF",
        units="m",
        centroids=Centroids(lat=np.array([34.6249]), lon=np.array([126.4211])),
        event_id=np.array([1]),
        event_name=["ahhw"],
        date=np.array([0]),
        frequency=np.array([1.0]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix([[2.2]]),
        fraction=sparse.csr_matrix([[1.0]]),
    )
    row = engine.calculate(
        FAC,
        h,
        "CF",
        model_id="KOREA_LOCAL",
        hazard_only=True,
        hazard_only_detail="below AHHW and sea-connected; no loss computed",
    )
    assert row.calculation_status == "HAZARD_ONLY"
    assert row.hazard_intensity == pytest.approx(2.2) and row.hazard_intensity_unit == "m"
    assert row.eal_usd is None and row.potential_loss_usd is None
    assert "no loss computed" in (row.status_detail or "")
