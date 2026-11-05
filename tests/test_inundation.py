"""Step 6B: the 2D model coupled to the synthetic city.

The gates are physical properties, set before looking at city results:

- the bathtub stage law is the long-wave limit of the 2D model, so on terrain with no
  enclosed hollows the two must agree, and the 2D depth may never exceed the bathtub
  depth by more than a small momentum overshoot;
- a hollow below the river stage but enclosed by higher ground stays dry in 2D (the
  bathtub floods it): this is what the 2D model adds;
- the river boundary's water is all accounted for in the city (volume closure);
- priced through the engine, the 2D hazard keeps the per-asset vs portfolio identity.

Needs the worker environment (CLIMADA, NetworkX).
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("networkx")

import numpy as np  # noqa: E402
from climaterisk_worker.twin.city import build_city  # noqa: E402
from climaterisk_worker.twin.flood import flood_depths  # noqa: E402
from climaterisk_worker.twin.hazard import build_hazard  # noqa: E402
from climaterisk_worker.twin.inundation import (  # noqa: E402
    asset_cells,
    city_raster,
    hydraulic_depths,
    stage_hydrograph,
)
from climaterisk_worker.twin.pricing import portfolio_impact, price_assets  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

#: Allowed 2D-over-bathtub excess (m): local-inertial momentum can overshoot slightly.
OVERSHOOT_M = 0.02


@pytest.fixture()
def small_spec() -> dict:
    """6 x 5 city, 100 y and 500 y events only, so a 2D run takes seconds."""
    spec = copy.deepcopy(load_spec())
    spec["grid"].update(rows=6, cols=5)
    spec["zoning"].update(industrial_rows=[0, 0], commercial_rows=[2, 2], commercial_cols=[1, 2])
    spec["facilities"].update(substations=[[5, 4]], hospital=[5, 0])
    spec["flood"]["return_periods"] = [100, 500]
    return spec


def _bathtub(assets: list[dict], spec: dict) -> np.ndarray:
    return flood_depths([a["ground_m"] for a in assets], spec)


def test_raster_keeps_node_ground_and_road_midpoints(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    bed, cell, k = city_raster(graph, small_spec)
    assert cell * k == pytest.approx(small_spec["grid"]["spacing_m"])
    rr, cc = asset_cells(assets, k)
    np.testing.assert_allclose(bed[rr, cc], [a["ground_m"] for a in assets], rtol=0, atol=1e-12)
    assert np.all(bed[0] == small_spec["hydraulic"]["river_bed_m"])


def test_hydrograph_peaks_at_half_duration() -> None:
    assert stage_hydrograph(0.0, 3.0, 0.0, 100.0) == pytest.approx(0.0)
    assert stage_hydrograph(50.0, 3.0, 0.0, 100.0) == pytest.approx(3.0)
    assert stage_hydrograph(150.0, 3.0, 0.0, 100.0) == 0.0


def test_long_wave_on_open_terrain_converges_to_the_bathtub(small_spec: dict) -> None:
    """No terrain noise -> no enclosed hollows -> 2D peak depth = stage-law depth."""
    small_spec["terrain"]["noise_sd_m"] = 0.0
    graph, assets = build_city(small_spec)
    depth, diag = hydraulic_depths(graph, assets, small_spec)
    np.testing.assert_allclose(depth, _bathtub(assets, small_spec), rtol=0, atol=OVERSHOOT_M)
    assert all(d["max_froude"] < small_spec["hydraulic"]["max_valid_froude"] for d in diag)


def test_2d_never_floods_deeper_than_the_bathtub(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    depth, _ = hydraulic_depths(graph, assets, small_spec)
    assert np.all(depth <= _bathtub(assets, small_spec) + OVERSHOOT_M)


def test_enclosed_hollow_stays_dry_in_2d(small_spec: dict) -> None:
    """A lot below the 100 y stage, ringed by ground above the 500 y stage."""
    small_spec["terrain"]["noise_sd_m"] = 0.0
    graph, _ = build_city(small_spec)
    hollow = (3, 2)
    for node in graph.nodes:
        if abs(node[0] - hollow[0]) <= 1 and abs(node[1] - hollow[1]) <= 1:
            graph.nodes[node]["z_m"] = 10.0
    graph.nodes[hollow]["z_m"] = 0.0
    assets = [{"node": n, "ground_m": graph.nodes[n]["z_m"]} for n in sorted(graph.nodes)]
    depth, _ = hydraulic_depths(graph, assets, small_spec)
    k = sorted(graph.nodes).index(hollow)
    assert _bathtub(assets, small_spec)[0, k] > 1.0  # the bathtub floods it
    assert depth[:, k].max() == 0.0  # water cannot get there


def test_river_water_is_all_in_the_city(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    _, diag = hydraulic_depths(graph, assets, small_spec)
    for d in diag:
        assert d["boundary_volume_m3"] > 0
        assert abs(d["stored_volume_m3"] - d["boundary_volume_m3"]) < 1e-6 * d["boundary_volume_m3"]


def test_2d_hazard_prices_through_the_engine(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    depth, _ = hydraulic_depths(graph, assets, small_spec)
    hazard = build_hazard(assets, small_spec, depth=depth)
    np.testing.assert_array_equal(hazard.intensity.toarray(), depth)
    rows = price_assets(assets, hazard)
    _, impact = portfolio_impact(assets, hazard)
    np.testing.assert_allclose(sum(r.eal_usd for r in rows), impact.aai_agg, rtol=1e-9)
