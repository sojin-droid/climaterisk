"""Synthetic virtual city: generator, hazard law and the engine EAL against a hand integral.

Needs the worker environment (CLIMADA, NetworkX).
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src", REPO / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")
pytest.importorskip("networkx")

import numpy as np  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "synthetic_city", REPO / "scripts" / "synthetic_city.py"
)
assert _SPEC is not None and _SPEC.loader is not None
city = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(city)


@pytest.fixture()
def small_spec() -> dict:
    spec = copy.deepcopy(city.load_spec())
    spec["grid"].update(rows=4, cols=4)
    spec["zoning"].update(industrial_rows=[0, 0], commercial_rows=[2, 2], commercial_cols=[1, 2])
    spec["facilities"].update(substations=[[3, 3]], hospital=[3, 0])
    return spec


def test_layered_frequencies_telescope_to_the_most_frequent_layer() -> None:
    f = city.layered_frequencies([2, 5, 10, 100])
    np.testing.assert_allclose(f, [0.5 - 0.2, 0.2 - 0.1, 0.1 - 0.01, 0.01], rtol=0, atol=1e-15)
    np.testing.assert_allclose(f.sum(), 0.5, rtol=1e-15)


def test_depth_follows_the_stage_law(small_spec: dict) -> None:
    """d(T) = max(0, h_2 + b ln(T/2) - z) with h_2 = 0.3, b = 0.8."""
    d = city.flood_depths([0.0, 1.0, 10.0], small_spec)
    rps = np.array(sorted(small_spec["flood"]["return_periods"]), dtype=float)
    h = 0.3 + 0.8 * np.log(rps / 2)
    np.testing.assert_allclose(d[:, 0], h, rtol=1e-12)
    np.testing.assert_allclose(d[:, 1], np.maximum(0, h - 1.0), rtol=1e-12)
    np.testing.assert_allclose(d[:, 2], 0.0)


def test_city_is_reproducible_and_complete(small_spec: dict) -> None:
    g1, a1 = city.build_city(small_spec)
    _, a2 = city.build_city(small_spec)
    assert [a["asset_value_usd"] for a in a1] == [a["asset_value_usd"] for a in a2]
    assert g1.number_of_nodes() == 16 and g1.number_of_edges() == 24
    assert len(a1) == 16 + 24
    kinds = {a["kind"] for a in a1}
    assert kinds == {"industrial", "commercial", "residential", "substation", "hospital", "road"}


def test_engine_eal_equals_hand_integral(small_spec: dict) -> None:
    """EAL_k = Σ_i f_i · v_k · MDR_JRC(d_ik), MDR by linear interpolation of the JRC points."""
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    _, assets = city.build_city(small_spec)
    hazard = city.build_hazard(assets, small_spec)
    rows = city.price_assets(assets, hazard)
    depth = hazard.intensity.toarray()
    for k, (a, row) in enumerate(zip(assets, rows, strict=True)):
        func = registry.flood_impact_function("Asia", jrc_sector_for(a["property_type"]))
        mdr = np.interp(depth[:, k], func.intensity, func.mdd * func.paa)
        expected = float(hazard.frequency @ (a["asset_value_usd"] * mdr))
        np.testing.assert_allclose(row.eal_usd, expected, rtol=1e-9, atol=1e-6)
    _, impact = city.portfolio_impact(assets, hazard)
    np.testing.assert_allclose(sum(r.eal_usd for r in rows), impact.aai_agg, rtol=1e-9)


def test_protection_removes_layers_up_to_the_design_period(small_spec: dict) -> None:
    """Design RP 50 y: only the 100/200/500-y layers remain, at full depth."""
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    _, assets = city.build_city(small_spec)
    hazard = city.build_hazard(assets, small_spec)
    rows = city.price_assets(assets, hazard, protection_rp=50)
    rps = sorted(small_spec["flood"]["return_periods"])
    keep = np.array([rp > 50 for rp in rps])
    for k, (a, row) in enumerate(zip(assets, rows, strict=True)):
        func = registry.flood_impact_function("Asia", jrc_sector_for(a["property_type"]))
        d = hazard.intensity.toarray()[:, k] * keep
        mdr = np.interp(d, func.intensity, func.mdd * func.paa)
        np.testing.assert_allclose(
            row.eal_usd,
            float(hazard.frequency @ (a["asset_value_usd"] * mdr)),
            rtol=1e-9,
            atol=1e-6,
        )
        assert row.eal_undefended_usd is not None and row.eal_undefended_usd >= row.eal_usd


def test_network_has_no_cut_off_when_nothing_fails(small_spec: dict) -> None:
    small_spec["network"]["failure_depth_m"] = 1e9
    graph, assets = city.build_city(small_spec)
    net = city.network_failure(graph, assets, city.build_hazard(assets, small_spec), small_spec)
    assert all(n["failed_nodes"] == 0 and n["residential_cut_off"] == 0 for n in net)
