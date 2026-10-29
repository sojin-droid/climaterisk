"""Synthetic virtual city: generator, hazard law and the engine EAL against a hand integral.

Needs the worker environment (CLIMADA, NetworkX).
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")
pytest.importorskip("networkx")

import numpy as np  # noqa: E402
from climaterisk_worker.twin import (  # noqa: E402
    build_city,
    compare_climate,
    compare_defences,
    flood_depths,
    layered_frequencies,
    load_spec,
)
from climaterisk_worker.twin.hazard import build_hazard  # noqa: E402
from climaterisk_worker.twin.network import network_failure  # noqa: E402
from climaterisk_worker.twin.pricing import (  # noqa: E402
    defences_for,
    portfolio_impact,
    price_assets,
)


@pytest.fixture()
def small_spec() -> dict:
    spec = copy.deepcopy(load_spec())
    spec["grid"].update(rows=4, cols=4)
    spec["zoning"].update(industrial_rows=[0, 0], commercial_rows=[2, 2], commercial_cols=[1, 2])
    spec["facilities"].update(substations=[[3, 3]], hospital=[3, 0])
    return spec


def test_layered_frequencies_telescope_to_the_most_frequent_layer() -> None:
    f = layered_frequencies([2, 5, 10, 100])
    np.testing.assert_allclose(f, [0.5 - 0.2, 0.2 - 0.1, 0.1 - 0.01, 0.01], rtol=0, atol=1e-15)
    np.testing.assert_allclose(f.sum(), 0.5, rtol=1e-15)


def test_depth_follows_the_stage_law(small_spec: dict) -> None:
    """d(T) = max(0, h_2 + b ln(T/2) - z) with h_2 = 0.3, b = 0.8."""
    d = flood_depths([0.0, 1.0, 10.0], small_spec)
    rps = np.array(sorted(small_spec["flood"]["return_periods"]), dtype=float)
    h = 0.3 + 0.8 * np.log(rps / 2)
    np.testing.assert_allclose(d[:, 0], h, rtol=1e-12)
    np.testing.assert_allclose(d[:, 1], np.maximum(0, h - 1.0), rtol=1e-12)
    np.testing.assert_allclose(d[:, 2], 0.0)


def test_city_is_reproducible_and_complete(small_spec: dict) -> None:
    g1, a1 = build_city(small_spec)
    _, a2 = build_city(small_spec)
    assert [a["asset_value_usd"] for a in a1] == [a["asset_value_usd"] for a in a2]
    assert g1.number_of_nodes() == 16 and g1.number_of_edges() == 24
    assert len(a1) == 16 + 24
    kinds = {a["kind"] for a in a1}
    assert kinds == {"industrial", "commercial", "residential", "substation", "hospital", "road"}


def test_engine_eal_equals_hand_integral(small_spec: dict) -> None:
    """EAL_k = sum_i f_i * v_k * MDR_JRC(d_ik), MDR by linear interpolation of the JRC points."""
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    _, assets = build_city(small_spec)
    hazard = build_hazard(assets, small_spec)
    rows = price_assets(assets, hazard)
    depth = hazard.intensity.toarray()
    for k, (a, row) in enumerate(zip(assets, rows, strict=True)):
        func = registry.flood_impact_function("Asia", jrc_sector_for(a["property_type"]))
        mdr = np.interp(depth[:, k], func.intensity, func.mdd * func.paa)
        expected = float(hazard.frequency @ (a["asset_value_usd"] * mdr))
        np.testing.assert_allclose(row.eal_usd, expected, rtol=1e-9, atol=1e-6)
    _, impact = portfolio_impact(assets, hazard)
    np.testing.assert_allclose(sum(r.eal_usd for r in rows), impact.aai_agg, rtol=1e-9)


def test_protection_removes_layers_up_to_the_design_period(small_spec: dict) -> None:
    """Design RP 50 y: only the 100/200/500-y layers remain, at full depth."""
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    _, assets = build_city(small_spec)
    hazard = build_hazard(assets, small_spec)
    rows = price_assets(assets, hazard, protection_rp=50)
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
    graph, assets = build_city(small_spec)
    net = network_failure(graph, assets, build_hazard(assets, small_spec), small_spec)
    assert all(n["failed_nodes"] == 0 and n["residential_cut_off"] == 0 for n in net)


def _scenario_eal_pct(
    assets: list[dict],
    hazard: Any,
    spec: dict[str, Any],
    protection_rp: float | None,
    levee: bool,
) -> float:
    """Portfolio EAL as % of total value for one defence scenario (fast path).

    Uses ``portfolio_impact`` on the protected hazard: the engine's per-asset sum is
    cross-checked against ``aai_agg`` elsewhere, so the portfolio figure is the authoritative
    EAL the pipeline reports.
    """
    priced, _ = defences_for(hazard, assets, spec, protection_rp, levee)
    _, impact = portfolio_impact(assets, priced)
    total = float(sum(a["asset_value_usd"] for a in assets))
    return 100.0 * float(impact.aai_agg) / total


def test_defence_scenarios_match_expected_eal() -> None:
    """Full city (seed 20261029): EAL % must match AGENTS.md's expectations.

    Expected: none / 10 / 50 / 100 y -> 1.303 / 0.749 / 0.313 / 0.186 %.
    """
    spec = load_spec()
    _, assets = build_city(spec)
    hazard = build_hazard(assets, spec)
    # uniform defence scenarios — the AGENTS.md expectation
    expected = {None: 1.303, 10.0: 0.749, 50.0: 0.313, 100.0: 0.186}
    for rp, want in expected.items():
        np.testing.assert_allclose(
            _scenario_eal_pct(assets, hazard, spec, rp, False), want, rtol=1e-3, atol=1e-4
        )
    # more defence -> lower EAL
    pcts = [_scenario_eal_pct(assets, hazard, spec, rp, False) for rp in (None, 10.0, 50.0, 100.0)]
    assert pcts == sorted(pcts, reverse=True)
    # local 100y levee on half the bank (cols 5..14): a real defence (below none) but weaker
    # than area-wide 100y, because the floodplain of the other cols still floods.
    levee_pct = _scenario_eal_pct(assets, hazard, spec, None, True)
    np.testing.assert_allclose(levee_pct, 0.739, rtol=1e-3, atol=1e-4)
    assert levee_pct < pcts[0] and levee_pct > pcts[-1]


def test_levee_protects_only_the_band_behind_it(small_spec: dict) -> None:
    """Levee on cols 0..1 of a 4x4 city: exactly the assets in that band are levee-protected.

    Edges are placed at their midpoint, so an edge between cols 1 and 2 (midpoint 1.5) is out.
    """
    small_spec["levee"].update(rows=[0, 3], cols=[0, 1])
    _, assets = build_city(small_spec)
    hazard = build_hazard(assets, small_spec)
    priced, _ = defences_for(hazard, assets, small_spec, None, True)
    rows = price_assets(assets, hazard, levee=True, spec=small_spec)
    # per-asset engine EAL sum == portfolio aai_agg for the levee scenario too
    per = np.array([r.eal_usd or 0.0 for r in rows])
    _, impact = portfolio_impact(assets, priced)
    np.testing.assert_allclose(per.sum(), impact.aai_agg, rtol=1e-9)
    for a, r in zip(assets, rows, strict=True):
        if "node" in a:
            col = a["node"][1]
        else:
            (_, c1), (_, c2) = a["edge"]
            col = (c1 + c2) / 2.0
        assert bool(r.levee_protected) is (col <= 1), a
    # the levee cuts EAL vs no defence
    none_rows = price_assets(assets, hazard)
    assert per.sum() < sum(r.eal_usd or 0.0 for r in none_rows)


def test_compare_defences_writes_table_and_orders_by_defence(small_spec: dict, tmp_path) -> None:
    small_spec["levee"].update(rows=[0, 3], cols=[0, 1])
    table = compare_defences(small_spec, tmp_path)
    assert [t["scenario"] for t in table] == [
        "none",
        "uniform_10y",
        "uniform_50y",
        "uniform_100y",
        "levee_bank_100y",
    ]
    uniform = [t["eal_pct"] for t in table[:4]]
    assert uniform == sorted(uniform, reverse=True)  # stronger uniform defence -> lower EAL
    assert table[-1]["eal_pct"] < table[0]["eal_pct"]  # levee still beats no defence
    assert (tmp_path / "defence_scenarios.csv").exists()
    assert (tmp_path / "defence_summary.json").exists()
    # known trap: report cut-off alongside EAL, never on its own
    assert all("expected_cut_off_value_usd_per_year" in t for t in table)


# --------------------------------------------------------------------------- #
# Step 3 — climate change (levees keep their present height)                  #
# --------------------------------------------------------------------------- #


def _plus_half_m() -> dict[str, Any]:
    """The spec's future scenario: +0.5 m on the 2-year stage, slope unchanged."""
    return {"stage_2y_m_shift": 0.5, "stage_slope_m_shift": 0.0, "name": "+0.5m"}


def test_future_hazard_shifts_stage_by_the_scenario_amount(small_spec: dict) -> None:
    """+0.5 m on stage_2y_m raises every present depth on flooded cells by exactly +0.5 m."""
    from climaterisk_worker.twin.climate import build_future_hazard
    from climaterisk_worker.twin.flood import flood_depths

    _, assets = build_city(small_spec)
    present = build_hazard(assets, small_spec)
    future = build_future_hazard(assets, small_spec, _plus_half_m())
    pd = present.intensity.toarray()
    fd = future.intensity.toarray()
    # events and centroids line up between present and future hazards
    assert future.event_name == present.event_name
    # the future depth matrix equals the shifted stage law
    shifted = copy.deepcopy(small_spec)
    shifted["flood"]["stage_2y_m"] += 0.5
    expected = flood_depths([a["ground_m"] for a in assets], shifted)
    np.testing.assert_allclose(fd, expected, rtol=0, atol=1e-12)
    # future depths are never below present, and rise by exactly +0.5 m where present > 0
    assert np.all(fd >= pd - 1e-12)
    np.testing.assert_allclose(fd[pd > 0] - pd[pd > 0], 0.5, rtol=0, atol=1e-12)


def test_climate_change_multiplier_above_one_for_every_defence(tmp_path) -> None:
    """Higher stage -> higher EAL: the multiplier exceeds 1 for every defence scenario.

    Present-day headline EALs are unchanged by step 3 (none / 10 / 50 / 100 y /
    levee = 1.303 / 0.749 / 0.313 / 0.186 / 0.739 %), so the present column is the step-2
    baseline and only the future column (and the ratio) is new.
    """
    spec = load_spec()
    results = compare_climate(spec, tmp_path)
    scen = results[0]
    by_defence = {t["scenario"]: t for t in scen["defence_results"]}
    # present-day values are the step-2 baseline, untouched by step 3
    np.testing.assert_allclose(by_defence["none"]["present_eal_pct"], 1.303, rtol=1e-3, atol=1e-4)
    np.testing.assert_allclose(
        by_defence["uniform_100y"]["present_eal_pct"], 0.186, rtol=1e-3, atol=1e-4
    )
    np.testing.assert_allclose(
        by_defence["levee_bank_100y"]["present_eal_pct"], 0.739, rtol=1e-3, atol=1e-4
    )
    # higher stage -> higher EAL for every defence (defences keep their present height)
    for t in scen["defence_results"]:
        assert t["future_eal_pct"] > t["present_eal_pct"]
        assert t["climate_change_multiplier"] > 1.0
    assert (tmp_path / "climate_scenarios.csv").exists()
    assert (tmp_path / "climate_summary.json").exists()


def test_levee_keeps_present_height_avoids_the_trap(small_spec: dict) -> None:
    """The trap: deriving the levee from the future hazard silently raises it.

    Re-deriving the design depth from the future hazard (``defences_for(future, levee=True)``
    without a ``threshold_hazard``) makes the levee grow with the flood, so it protects too
    much and the levee EAL comes out too low. Using ``threshold_hazard=present`` keeps the
    present height, so the levee floods more and the correct EAL is HIGHER than the trap.
    """
    from climaterisk_worker.twin.climate import build_future_hazard
    from climaterisk_worker.twin.pricing import defences_for, portfolio_impact

    small_spec["levee"].update(rows=[0, 3], cols=[0, 1])
    _, assets = build_city(small_spec)
    present = build_hazard(assets, small_spec)
    future = build_future_hazard(assets, small_spec, _plus_half_m())
    # correct: present design depth applied to the future hazard
    correct_priced, _ = defences_for(
        future, assets, small_spec, None, True, threshold_hazard=present
    )
    # trap: design depth re-derived from the future hazard (raises the levee)
    trap_priced, _ = defences_for(future, assets, small_spec, None, True)
    correct = float(portfolio_impact(assets, correct_priced)[1].aai_agg)
    trap = float(portfolio_impact(assets, trap_priced)[1].aai_agg)
    assert correct > trap > 0
    # the same invariant on per-asset EAL sums (rtol 1e-9) for the correct (present-height) run
    rows = price_assets(assets, future, levee=True, spec=small_spec, threshold_hazard=present)
    _, correct_impact = portfolio_impact(assets, correct_priced)
    np.testing.assert_allclose(
        sum(r.eal_usd or 0.0 for r in rows), correct_impact.aai_agg, rtol=1e-9
    )


def test_protect_with_threshold_matches_protect_when_threshold_is_derived(
    small_spec: dict,
) -> None:
    """``protect_with_threshold`` reproduces ``protect`` when the threshold is derived
    from the same hazard (backward-compatibility of the refactor)."""
    from climaterisk_worker.physical_risk import protection

    _, assets = build_city(small_spec)
    hazard = build_hazard(assets, small_spec)
    rps = np.full(hazard.centroids.size, 50.0)
    threshold = protection.thresholds(hazard, rps)
    a = protection.protect(hazard, rps)
    b = protection.protect_with_threshold(hazard, threshold)
    np.testing.assert_allclose(a.intensity.toarray(), b.intensity.toarray(), rtol=0, atol=1e-12)
