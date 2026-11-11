"""Step 9B gate (written by the reviewer before the step; agents may not edit it).

Storm surge on the step-6 2D model instead of the bathtub, with time snapshots so the
flood can be animated. Module under test: ``worker/climaterisk_worker/twin/surge2d.py``,
plus one addition to ``twin/hydraulics.py``:

- ``hydraulics.run(..., snapshot_every_s=None)``: when set, the result has ``"snapshots"``,
  a list of ``(t_s, h)`` pairs — ``h`` a copy of the depth raster (m) — taken at ``t = 0``
  and then at the first step reaching each multiple of ``snapshot_every_s``; times are
  strictly increasing and the last one is the end time. Without it there is no key.
- ``coast_stage_peaks(spec)`` -> peak sea level (m above datum) per typhoon track, in
  ``typhoon.synthetic_tracks`` order: the Xu (2010) line used by Petals,
  ``0.1023 max(v - 26.8224, 0) + 1.8288 + spec["coast"]["sea_level_rise_m"]``, at the
  largest wind ``v`` (m/s) that ``typhoon.wind_hazard`` gives on the coastline (latitude
  ``coastal.coast_latitude(spec)``, one point per grid column at that column's longitude);
  exactly ``sea_level_rise_m`` for a track whose coastline wind is 0.
- ``surge_depths_2d(graph, assets, spec, snapshot_every_s=None)`` -> ``(depth, diags)``:
  ``depth`` (m) has shape ``(n_tracks, n_assets)`` = peak 2D depth at each asset's raster
  cell (``inundation.city_raster`` / ``inundation.asset_cells``, with the boundary row 0 as
  sea at ``spec["coast"]["sea_bed_m"]``). The sea row follows
  ``inundation.stage_hydrograph(t, peak, base=sea_level_rise_m,
  duration=spec["coast"]["surge_duration_h"] * 3600)``; the run lasts
  ``surge_duration_h + surge_recession_h``; Manning n and alpha from ``spec["hydraulic"]``.
  ``diags[i]`` has ``peak_stage_m``, ``max_froude``, ``boundary_volume_m3``,
  ``stored_volume_m3`` (change since t = 0) and, when requested, ``snapshots``.
- ``surge_hazard_2d(graph, assets, spec)`` -> CLIMADA ``Hazard`` (``CF``, m) with that depth
  and the typhoon track frequencies, priceable by ``coastal.price_surge``.
"""

from __future__ import annotations

import copy
import itertools
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")
pytest.importorskip("networkx")

import numpy as np  # noqa: E402
from climaterisk_worker.twin import hydraulics  # noqa: E402
from climaterisk_worker.twin.city import build_city  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

surge2d = pytest.importorskip("climaterisk_worker.twin.surge2d")

#: Allowed 2D-over-bathtub excess (m), as in step 6B.
OVERSHOOT_M = 0.02


@pytest.fixture()
def small_spec() -> dict:
    """6 x 5 city, 3 typhoon tracks, so a 2D surge run takes seconds."""
    spec = copy.deepcopy(load_spec())
    spec["grid"].update(rows=6, cols=5)
    spec["zoning"].update(industrial_rows=[0, 0], commercial_rows=[2, 2], commercial_cols=[1, 2])
    spec["facilities"].update(substations=[[5, 4]], hospital=[5, 0])
    spec["typhoon"]["n_tracks"] = 3
    return spec


def test_snapshots_are_recorded_on_schedule() -> None:
    bed = np.zeros((3, 8))
    h0 = np.zeros_like(bed)
    h0[:, :3] = 1.0
    res = hydraulics.run(h0, bed, 1.0, 1.0, 0.0, t_end=10.0, snapshot_every_s=2.0)
    times = [t for t, _ in res["snapshots"]]
    assert times[0] == 0.0 and times[-1] == pytest.approx(res["t"])
    assert all(b > a for a, b in itertools.pairwise(times))
    assert len(times) >= 6
    np.testing.assert_array_equal(res["snapshots"][0][1], h0)
    np.testing.assert_allclose(res["snapshots"][-1][1], res["h"], rtol=0, atol=0)
    stacked = np.stack([h for _, h in res["snapshots"]])
    assert np.all(stacked.max(axis=0) <= res["h_max"] + 1e-12)
    assert "snapshots" not in hydraulics.run(h0, bed, 1.0, 1.0, 0.0, t_end=1.0)


def test_coast_stage_follows_the_xu_line(small_spec: dict) -> None:
    from climaterisk_worker.twin import coastal, typhoon
    from climaterisk_worker.twin.city import _to_latlon

    tracks, _ = typhoon.synthetic_tracks(small_spec)
    grid = small_spec["grid"]
    lon = np.array([_to_latlon(c * grid["spacing_m"], 0.0, grid)[1] for c in range(grid["cols"])])
    lat = np.full(lon.size, coastal.coast_latitude(small_spec))
    wind = typhoon.wind_hazard(lat, lon, tracks, np.ones(tracks.size)).intensity.toarray()
    v = wind.max(axis=1)
    slr = small_spec["coast"]["sea_level_rise_m"]
    want = np.where(v > 0, 0.1023 * np.fmax(v - 26.8224, 0.0) + 1.8288, 0.0) + slr
    np.testing.assert_allclose(surge2d.coast_stage_peaks(small_spec), want, rtol=1e-12)


def _bathtub_no_decay(assets: list, spec: dict) -> np.ndarray:
    peaks = surge2d.coast_stage_peaks(spec)
    z = np.array([a["ground_m"] for a in assets])
    return np.maximum(0.0, peaks[:, None] - z[None, :])


def test_2d_surge_never_exceeds_the_bathtub(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    depth, diags = surge2d.surge_depths_2d(graph, assets, small_spec)
    assert depth.shape == (small_spec["typhoon"]["n_tracks"], len(assets))
    assert np.all(depth <= _bathtub_no_decay(assets, small_spec) + OVERSHOOT_M)
    for d in diags:
        assert abs(d["stored_volume_m3"] - d["boundary_volume_m3"]) <= 1e-6 * max(
            d["boundary_volume_m3"], 1.0
        )


def test_a_long_surge_on_open_terrain_fills_like_the_bathtub(small_spec: dict) -> None:
    small_spec["terrain"]["noise_sd_m"] = 0.0
    small_spec["coast"]["surge_duration_h"] = 72.0
    graph, assets = build_city(small_spec)
    depth, _ = surge2d.surge_depths_2d(graph, assets, small_spec)
    np.testing.assert_allclose(depth, _bathtub_no_decay(assets, small_spec), atol=OVERSHOOT_M)


def test_a_short_surge_floods_less_than_a_long_one(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    short, _ = surge2d.surge_depths_2d(graph, assets, small_spec)
    longer = copy.deepcopy(small_spec)
    longer["coast"]["surge_duration_h"] = 4.0 * small_spec["coast"]["surge_duration_h"]
    long_depth, _ = surge2d.surge_depths_2d(graph, assets, longer)
    assert np.all(short <= long_depth + OVERSHOOT_M)
    assert short.sum() < long_depth.sum()


def test_snapshots_animate_the_surge(small_spec: dict) -> None:
    graph, assets = build_city(small_spec)
    depth, diags = surge2d.surge_depths_2d(graph, assets, small_spec, snapshot_every_s=600.0)
    d = diags[int(np.argmax(depth.max(axis=1)))]
    times = [t for t, _ in d["snapshots"]]
    end = (
        small_spec["coast"]["surge_duration_h"] + small_spec["coast"]["surge_recession_h"]
    ) * 3600
    assert times[0] == 0.0 and times[-1] == pytest.approx(end)
    land = [h[1:].sum() for _, h in d["snapshots"]]  # row 0 is the sea
    assert max(land) > land[0]  # water came ashore at some point


def test_2d_surge_prices_through_the_engine(small_spec: dict) -> None:
    from climaterisk_worker.twin import coastal, typhoon

    graph, assets = build_city(small_spec)
    hazard = surge2d.surge_hazard_2d(graph, assets, small_spec)
    assert hazard.haz_type == "CF" and hazard.units == "m"
    depth, _ = surge2d.surge_depths_2d(graph, assets, small_spec)
    np.testing.assert_allclose(hazard.intensity.toarray(), depth, rtol=0, atol=0)
    np.testing.assert_allclose(hazard.frequency, typhoon.synthetic_tracks(small_spec)[1])
    rows = coastal.price_surge(assets, hazard)
    assert len(rows) == len(assets)
