"""Portfolio return-period loss from event-level losses, against hand-computed curves.

Two assets hit by *different* events: each asset's own 100-year loss is its single big
event, but the portfolio's 100-year loss is not their sum — the hand calculation below is
the analytical reference. Needs the worker environment (CLIMADA).
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
from climaterisk_worker.physical_risk import aggregate  # noqa: E402
from climaterisk_worker.physical_risk.runner import _loss_curves  # noqa: E402
from scipy import sparse  # noqa: E402

from climaterisk.physical_risk.metrics import ModelId  # noqa: E402

# Four events, 0.01/yr each (total 0.04/yr → curve spans 25 … 100 years).
FREQ = np.array([0.01, 0.01, 0.01, 0.01])
# Asset A is hit by events 0 and 2, asset B by events 1 and 3 — never together.
LOSS_A = np.array([100.0, 0.0, 40.0, 0.0])
LOSS_B = np.array([0.0, 80.0, 0.0, 10.0])


def test_portfolio_curve_matches_hand_calculation() -> None:
    """Portfolio event losses [100, 80, 40, 10] → RP 100 = 100, RP 50 = 80, RP 25 = 10.

    Sorted descending, cumulative frequency is 0.01, 0.02, 0.03, 0.04, so the curve's
    points are (100 y, 100), (50 y, 80), (33.3 y, 40), (25 y, 10). Standalone: A's curve is
    (100 y, 100), (50 y, 40), (33.3 y, 0), (25 y, 0); B's is (100 y, 80), (50 y, 10), … .
    """
    out = aggregate.group_loss_curve(
        {"A": LOSS_A, "B": LOSS_B},
        FREQ,
        [25, 50, 100],
        currencies={"A": "USD", "B": None},
        asset_values={"A": 1000.0, "B": 2000.0},
    )
    assert out["status"] == aggregate.GROUP_OK
    assert out["currency"] == "USD"
    assert out["points_priced"] == 2
    assert out["priced_asset_value"] == 3000.0
    by_rp = {c["return_period_years"]: c for c in out["losses"]}
    np.testing.assert_allclose(
        [by_rp[25]["loss"], by_rp[50]["loss"], by_rp[100]["loss"]], [10.0, 80.0, 100.0], rtol=1e-12
    )
    # Standalone sums: RP100 = 100 + 80, RP50 = 40 + 10.
    np.testing.assert_allclose(by_rp[100]["sum_of_standalone_losses"], 180.0, rtol=1e-12)
    np.testing.assert_allclose(by_rp[50]["sum_of_standalone_losses"], 50.0, rtol=1e-12)
    np.testing.assert_allclose(by_rp[100]["diversification_pct"], (1 - 100 / 180) * 100, rtol=1e-12)
    # At RP 50 the portfolio loss exceeds the standalone sum (80 > 50): a negative benefit is
    # reported as is — the curve of a sum is not bounded by the sum of curves at one RP.
    np.testing.assert_allclose(by_rp[50]["diversification_pct"], (1 - 80 / 50) * 100, rtol=1e-12)


def test_portfolio_eal_is_sum_of_asset_eal() -> None:
    """Expectation is linear: portfolio aai_agg equals the sum of the two assets' EAL."""
    out = aggregate.group_loss_curve(
        {"A": LOSS_A, "B": LOSS_B}, FREQ, [100], currencies={}, asset_values={}
    )
    expected = float(np.sum(LOSS_A * FREQ) + np.sum(LOSS_B * FREQ))
    np.testing.assert_allclose(out["eal"], expected, rtol=1e-12)
    np.testing.assert_allclose(out["eal"], 2.3, rtol=1e-12)


def test_return_periods_outside_the_curve_are_null_not_clamped() -> None:
    """np.interp would repeat the end values; the module refuses on both sides."""
    cells, lo, hi = aggregate.curve_losses(LOSS_A + LOSS_B, FREQ, [10, 250])
    np.testing.assert_allclose([lo, hi], [25.0, 100.0], rtol=1e-12)
    below, above = cells
    assert below["loss"] is None and below["status"] == aggregate.BELOW_CURVE
    assert above["loss"] is None and above["status"] == aggregate.ABOVE_CURVE


def test_ceiling_is_the_largest_event_not_the_rarest_frequency() -> None:
    """The largest portfolio event is the *frequent* one: the curve ends at 1/0.1 = 10 y.

    ``1 / min(frequency)`` would be 100 y, and a 50-year request would quietly return the
    largest event's loss. The curve's own end is the right ceiling.
    """
    cells, _, hi = aggregate.curve_losses([500.0, 1.0], [0.1, 0.01], [50])
    np.testing.assert_allclose(hi, 10.0, rtol=1e-12)
    assert cells[0]["loss"] is None and cells[0]["status"] == aggregate.ABOVE_CURVE


def test_mixed_currencies_are_not_added() -> None:
    out = aggregate.group_loss_curve(
        {"A": LOSS_A, "B": LOSS_B},
        FREQ,
        [100],
        currencies={"A": "USD", "B": "KRW"},
        asset_values={"A": 1.0, "B": 1.0},
    )
    assert out["status"] == aggregate.GROUP_MIXED_CURRENCY
    assert out["losses"] == [] and out.get("eal") is None


def _synth_hazard(lats: list[float], lons: list[float], intensity: np.ndarray) -> Hazard:
    n_ev = intensity.shape[0]
    return Hazard(
        haz_type="RF",
        centroids=Centroids(lat=np.array(lats), lon=np.array(lons)),
        event_id=np.arange(1, n_ev + 1),
        event_name=[f"e{i}" for i in range(n_ev)],
        date=np.arange(730000, 730000 + n_ev),
        frequency=np.full(n_ev, 0.01),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(intensity),
        fraction=sparse.csr_matrix(np.ones_like(intensity)),
        units="m",
    )


def test_runner_groups_by_class_and_province_and_skips_unpriced_points() -> None:
    """Through the real engine: portfolio, one curve per asset class and per 시도.

    Office → commercial, warehouse → industrial, apartment → residential (the configured
    JRC sector map). The apartment has no value, so its class is assessed but unpriced, and
    the portfolio's flood curve mixes two JRC functions — both listed on the record.
    """
    from climaterisk_worker.physical_risk import engine

    lats, lons = [37.5, 35.1, 35.2], [127.0, 129.0, 129.1]
    # Three events; event 0 floods Seoul only, event 1 the two Busan points.
    intensity = np.array([[3.0, 0.0, 0.0], [0.0, 2.0, 2.0], [0.5, 0.5, 0.5]])
    haz = _synth_hazard(lats, lons, intensity)
    facilities = [
        {
            "facility_id": "S1",
            "latitude": 37.5,
            "longitude": 127.0,
            "asset_value_usd": 1e6,
            "property_type": "office",
            "province_name": "서울특별시",
        },
        {
            "facility_id": "B1",
            "latitude": 35.1,
            "longitude": 129.0,
            "asset_value_usd": 2e6,
            "property_type": "warehouse",
            "province_name": "부산광역시",
        },
        {
            "facility_id": "B2",
            "latitude": 35.2,
            "longitude": 129.1,
            "asset_value_usd": None,
            "property_type": "apartment",
            "province_name": "부산광역시",
        },
    ]
    common = {
        "model_id": ModelId.GLOBAL_BASELINE.value,
        "requested_scenario": "rcp45",
        "served_scenario": "rcp45",
        "time_horizon": "2050",
        "hazard_dataset": "synthetic",
    }
    sink: dict[str, object] = {}
    rows = [engine.calculate(f, haz, "RF", event_losses=sink, **common) for f in facilities]
    curves = _loss_curves(rows, sink, haz.frequency, "RF", common)

    by_group = {(c["group_level"], c["group_name"]): c for c in curves}
    assert set(by_group) == {
        ("PORTFOLIO", None),
        ("ASSET_CLASS", "commercial"),
        ("ASSET_CLASS", "industrial"),
        ("ASSET_CLASS", "residential"),
        ("PROVINCE", "부산광역시"),
        ("PROVINCE", "서울특별시"),
    }
    portfolio = by_group[("PORTFOLIO", None)]
    assert portfolio["points_assessed"] == 3 and portfolio["points_priced"] == 2
    busan = by_group[("PROVINCE", "부산광역시")]
    assert busan["points_assessed"] == 2 and busan["points_priced"] == 1
    # Portfolio EAL equals the sum of the priced rows' EAL (the Portfolio Totals figure).
    np.testing.assert_allclose(
        portfolio["eal"], sum(r.eal_usd for r in rows if r.eal_usd is not None), rtol=1e-12
    )
    np.testing.assert_allclose(
        portfolio["eal"],
        by_group[("PROVINCE", "서울특별시")]["eal"] + busan["eal"],
        rtol=1e-12,
    )
    # Asset classes partition the portfolio: their EALs add back to it.
    classes = [by_group[("ASSET_CLASS", k)] for k in ("commercial", "industrial", "residential")]
    assert [c["points_priced"] for c in classes] == [1, 1, 0]
    np.testing.assert_allclose(
        portfolio["eal"], sum(c.get("eal") or 0.0 for c in classes), rtol=1e-12
    )
    assert portfolio["asset_classes"] == {"commercial": 1, "industrial": 1}
    names = {f["impact_function_name"]: f["assets"] for f in portfolio["impact_functions"]}
    assert len(names) == 2 and set(names.values()) == {1}
    assert any("Commercial" in n for n in names) and any("Industrial" in n for n in names)
    by_row = {r.facility_id: r.impact_function_id for r in rows}
    assert {f["impact_function_id"] for f in portfolio["impact_functions"]} == {
        by_row["S1"],
        by_row["B1"],
    }
    # Seoul (event 0, 3 m) and B1 (event 1, 2 m) both sit in wet cells.
    assert portfolio["points_with_hazard"] == 2
