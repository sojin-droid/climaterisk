"""Step R3d gate (written by the reviewer before the step; agents may not edit it).

R3c read the ramp weir off the street depth without taking the water out of the street, so
basements swallowed about 40 times the storm. R3d couples them both ways: the water a
basement takes leaves the 2D street flow during the run.

- ``hydraulics.run(..., sink_fn=None)``: optional callable ``(t, h, dt) -> depth raster (m)``
  called once per step after the continuity update (``h`` is the updated depth, ``dt`` the
  step, s); the solver clips the returned depth to ``[0, h]`` cell by cell, removes it, and
  reports the total removed volume as ``sink_fn_volume_m3`` in the result (0.0 when unused).
  The volume balance ``rain = stored + sink + sink_fn + outflow`` holds with closed walls.
- ``pluvial.run_pluvial(..., sink_fn=None)`` passes it through.
- ``basement.entrance_cells(grid, buildings, bed, walls, n_levels)`` -> ``(rows, cols)`` int
  arrays: the ramp of each building with ``n_levels > 0`` sits in its lowest ring cell (the
  ring of ``site_loss.ring_cells``; lowest ``bed``, ties to the first in row-major order);
  ``-1`` for buildings without basements or with an empty ring.
- ``basement.BasementSink(rows, cols, capacity_m3, cell_m, spec)``: callable usable as
  ``sink_fn``. For building ``b`` with ``rows[b] >= 0``, in step ``dt`` it wants
  ``W_b = C B max(h - T, 0)^{3/2} dt`` (m^3; ``C = basement.weir_coefficient``,
  ``B = basement.ramp_width_m``, ``T = loss.entry_threshold_m``, ``h`` the depth in its
  entrance cell). Only water above the sill can spill: per cell, the wants of the buildings
  sharing it are scaled by ``min(1, max(h - T, 0) cell_m^2 / sum W)``; then each building
  takes ``min(scaled want, capacity_m3[b] - volume_m3[b])``. Returns the removed depth
  raster; attribute ``volume_m3`` (float array) is the cumulative volume per building.
  ``final_volume_m3(t_end_s)`` = ``max(volume_m3 - pump_m3_h * t_end_s / 3600, 0)``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("geopandas")

import numpy as np  # noqa: E402
from climaterisk_worker.twin import hydraulics  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

basement = pytest.importorskip("climaterisk_worker.twin.basement")
if not hasattr(basement, "BasementSink"):
    pytest.skip("R3d not implemented yet", allow_module_level=True)

M = 111320.0
LON_M = M * math.cos(math.radians(37.0))


def _square(x0: float, y0: float, s: float):
    from shapely.geometry import box

    return box(127.0 + x0 / LON_M, 37.0 + y0 / M, 127.0 + (x0 + s) / LON_M, 37.0 + (y0 + s) / M)


def _toy():
    import geopandas as gpd
    from climaterisk_worker.twin import site

    grid = site.site_grid((127.0, 37.0, 127.0 + 205 / LON_M, 37.0 + 205 / M), 10.0)
    b = gpd.GeoDataFrame(
        {
            "building": ["office", "apartments"],
            "height_m": [45.0, 3.3],
            "height_source": ["height"] * 2,
            "underground": [False, False],
            "is_wall": [True, True],
        },
        geometry=[_square(50, 50, 40), _square(120, 120, 20)],
        crs="EPSG:4326",
    )
    walls = np.zeros((grid.ny, grid.nx), dtype=bool)
    walls[5:9, 5:9] = True
    walls[12:14, 12:14] = True
    return grid, b, walls


def test_sink_fn_is_clipped_and_conserves_volume() -> None:
    ny, nx = 20, 20
    j = np.arange(nx)
    bed = np.tile(0.01 * j, (ny, 1))
    rain = np.full((ny, nx), 2e-5)

    def half(t: float, h: np.ndarray, dt: float) -> np.ndarray:
        out = np.zeros_like(h)
        out[10, 3] = 0.5 * h[10, 3]
        out[2, 2] = 10.0 * h[2, 2] + 1.0  # asks for more than is there: clipped to h
        return out

    res = hydraulics.run(np.zeros((ny, nx)), bed, 5.0, 5.0, 0.03, 600.0, rain=rain, sink_fn=half)
    stored = float(res["h"].sum()) * 25.0
    assert res["sink_fn_volume_m3"] > 0.0
    assert np.all(res["h"] >= 0.0) and res["h"][2, 2] == 0.0
    np.testing.assert_allclose(
        res["rain_volume_m3"],
        stored + res["sink_volume_m3"] + res["sink_fn_volume_m3"] + res["outflow_volume_m3"],
        rtol=1e-9,
    )
    plain = hydraulics.run(np.zeros((ny, nx)), bed, 5.0, 5.0, 0.03, 600.0, rain=rain)
    assert plain["sink_fn_volume_m3"] == 0.0


def test_entrance_is_the_lowest_ring_cell() -> None:
    spec = load_spec()
    grid, b, walls = _toy()
    i, j = np.mgrid[0 : grid.ny, 0 : grid.nx]
    bed = 1.0 * i + 0.1 * j
    n = basement.basement_levels(b, spec)
    assert n[0] > 0 and n[1] == 0
    rows, cols = basement.entrance_cells(grid, b, bed, walls, n)
    assert (rows[0], cols[0]) == (4, 4)
    assert (rows[1], cols[1]) == (-1, -1)
    flat = np.zeros_like(bed)
    rows, cols = basement.entrance_cells(grid, b, flat, walls, n)
    assert (rows[0], cols[0]) == (4, 4)  # tie: first ring cell in row-major order


def test_sink_takes_weir_flow_above_the_sill_up_to_capacity() -> None:
    spec = load_spec()
    bs = spec["basement"]
    T = spec["loss"]["entry_threshold_m"]
    q = bs["weir_coefficient"] * bs["ramp_width_m"]
    cell = 10.0
    h = np.zeros((6, 6))
    h[1, 1] = T + 0.25
    h[4, 4] = T + 0.01
    sink = basement.BasementSink(
        np.array([1, 4, 4, -1]), np.array([1, 4, 4, -1]), np.array([1e9, 1e9, 1e9, 1e9]), cell, spec
    )
    d = sink(0.0, h, 1.0)
    np.testing.assert_allclose(d[1, 1], q * 0.25**1.5 / cell**2, rtol=1e-12)
    assert d[0, 0] == 0.0
    np.testing.assert_allclose(sink.volume_m3[0], q * 0.25**1.5, rtol=1e-12)
    # cell (4, 4): two buildings, a long step: together they drain only the water above the sill
    before = sink.volume_m3.copy()
    d = sink(1.0, h, 1e4)
    np.testing.assert_allclose(d[4, 4], 0.01, rtol=1e-12)
    gained = sink.volume_m3 - before
    np.testing.assert_allclose(gained[1], gained[2], rtol=1e-12)
    np.testing.assert_allclose(gained[1] + gained[2], 0.01 * cell**2, rtol=1e-12)
    assert sink.volume_m3[3] == 0.0
    full = basement.BasementSink(np.array([1]), np.array([1]), np.array([5.0]), cell, spec)
    full(0.0, h, 10.0)
    np.testing.assert_allclose(full.volume_m3[0], 5.0, rtol=1e-12)
    assert np.all(full(10.0, h, 10.0) == 0.0)
    pump = bs["pump_m3_h"]
    np.testing.assert_allclose(
        full.final_volume_m3(7200.0)[0], max(5.0 - pump * 2.0, 0.0), rtol=1e-12
    )


def test_coupled_run_cannot_swallow_more_than_the_rain() -> None:
    spec = load_spec()
    grid, b, walls = _toy()
    bed = np.zeros((grid.ny, grid.nx))
    n = basement.basement_levels(b, spec)
    rows, cols = basement.entrance_cells(grid, b, bed, walls, n)
    sink = basement.BasementSink(rows, cols, np.array([1e9, 1e9]), grid.cell_m, spec)
    rain = np.where(walls, 0.0, 5e-5)  # 180 mm/h
    res = hydraulics.run(
        np.zeros(bed.shape), bed, 10.0, 10.0, 0.03, 1800.0, rain=rain, blocked=walls, sink_fn=sink
    )
    np.testing.assert_allclose(sink.volume_m3.sum(), res["sink_fn_volume_m3"], rtol=1e-9)
    assert 0.0 < res["sink_fn_volume_m3"] < res["rain_volume_m3"]
    stored = float(res["h"].sum()) * 100.0
    np.testing.assert_allclose(res["rain_volume_m3"], stored + res["sink_fn_volume_m3"], rtol=1e-9)
