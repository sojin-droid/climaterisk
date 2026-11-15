"""Step R6 gate (written by the reviewer before the step; agents may not edit it).

Rebuild of the Gangnam twin from data that is real and already on disk (user rule
2026-11-06: a twin replicates real space; nothing about buildings is assumed). Three new
pieces; every number from ``spec["landcover"]`` / ``spec["runon"]``:

``worker/climaterisk_worker/twin/landcover.py``
- ``green_mask(grid, polygons)`` -> bool raster ``(ny, nx)``: True where the cell CENTRE
  (``grid.centres()``) lies inside any polygon of ``polygons`` (GeoDataFrame, any CRS;
  reprojected to EPSG:4326). Empty ``polygons`` -> all False.
- ``cn_raster(green, spec)`` -> float raster: ``spec["landcover"]["cn_green"]`` on green
  cells, ``spec["rain"]["cn_paved"]`` elsewhere.

``pluvial.run_pluvial(..., cn=None, inflow=None)`` (new optional arguments)
- ``cn``: per-cell curve number raster. Each open cell gets the rain excess of ITS curve
  number; the excess of the rain falling on wall cells (roofs, ``cn_paved``) is spread over
  the open cells exactly as today. ``cn=None`` or a raster equal to ``cn_paved`` everywhere
  reproduces today's run exactly (bit-for-bit ``h_max``).
- ``inflow``: ``(rows, cols, area_m2)`` arrays — site cells receiving run-on from an
  upstream area outside the site. Each gets, every step, ``runon.runon_rate(t, area, spec)``
  m^3/s (through ``hydraulics.run(inflow_fn=...)``). The run reports ``inflow_volume_m3``;
  the volume balance ``rain + inflow = stored + sink + outflow (+ sink_fn)`` holds.

``worker/climaterisk_worker/twin/runon.py``
- ``d8_accumulation(z)`` -> int raster: number of cells (itself included) whose D8
  steepest-descent path passes through each cell. D8 = steepest drop to one of the 8
  neighbours, diagonal drops divided by sqrt(2); ties to the first neighbour in the order
  N, NE, E, SE, S, SW, W, NW (row index increases to the south here: N = row - 1); a cell with
  no lower neighbour has no receiver.
- ``edge_inflow(z_ext, inside)`` -> ``(rows, cols, n_up)`` in the coordinates of the
  extended raster: for every OUTSIDE cell (``~inside``) whose D8 receiver is INSIDE, the
  receiving inside cell and the accumulation of the outside cell. Several outside cells
  feeding one inside cell are summed into one entry; entries sorted row-major.
- ``runon_rate(t_s, area_m2, spec)`` -> ``area_m2 * max(r(t) - s, 0)`` (m^3/s) where ``r`` is
  ``pluvial.excess_rate(t_s, spec, cn_paved)`` and ``s`` the sewer capacity
  ``rain.sewer_capacity_mm_h / 3.6e6`` (the upstream area drains to its own sewer first).
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
from climaterisk_worker.twin import pluvial  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

landcover = pytest.importorskip("climaterisk_worker.twin.landcover")
runon = pytest.importorskip("climaterisk_worker.twin.runon")


def test_d8_on_planes_and_a_pit() -> None:
    i, j = np.mgrid[0:5, 0:6]
    east = 10.0 - 1.0 * j  # falls to the east: each row is one flow line
    acc = runon.d8_accumulation(east)
    np.testing.assert_array_equal(acc, np.tile(np.arange(1, 7), (5, 1)))
    south = 10.0 - 1.0 * i
    np.testing.assert_array_equal(
        runon.d8_accumulation(south), np.tile(np.arange(1, 6)[:, None], (1, 6))
    )
    bowl = (i - 2.0) ** 2 + (j - 2.0) ** 2  # every cell drains to the centre (2, 2)
    a = runon.d8_accumulation(bowl[:, :5])
    assert a[2, 2] == 25 and a.sum() >= 25


def test_edge_inflow_counts_the_upstream_area() -> None:
    _, j = np.mgrid[0:4, 0:8]
    z = 20.0 - 1.0 * j  # flows east
    inside = j >= 5
    rows, cols, n_up = runon.edge_inflow(z, inside)
    assert list(rows) == [0, 1, 2, 3] and list(cols) == [5, 5, 5, 5]
    np.testing.assert_array_equal(n_up, [5, 5, 5, 5])
    # nothing flows in when the outside drains away from the site
    r2, c2, n2 = runon.edge_inflow(-z, inside)
    assert len(r2) == len(c2) == len(n2) == 0


def test_runon_rate_is_excess_above_the_sewer() -> None:
    spec = load_spec()
    cn = float(spec["rain"]["cn_paved"])
    s = float(spec["rain"]["sewer_capacity_mm_h"]) / 3.6e6
    t_grid, _ = pluvial.hyetograph(spec)
    for t in 0.5 * (t_grid[1:] + t_grid[:-1]):
        r = pluvial.excess_rate(float(t), spec, cn)
        assert runon.runon_rate(float(t), 1e4, spec) == pytest.approx(1e4 * max(r - s, 0.0))
    assert runon.runon_rate(1e9, 1e4, spec) == 0.0


def _grid_and_ground():
    from climaterisk_worker.twin import site

    M = 111320.0
    lon_m = M * math.cos(math.radians(37.0))
    g = site.site_grid((127.0, 37.0, 127.0 + 200 / lon_m, 37.0 + 150 / M), 10.0)
    _, jj = np.mgrid[0 : g.ny, 0 : g.nx]
    return g, 5.0 - 0.01 * jj, lon_m, M


def test_green_mask_and_cn_raster() -> None:
    import geopandas as gpd
    from shapely.geometry import box

    spec = load_spec()
    g, _, lon_m, M = _grid_and_ground()
    park = box(127.0, 37.0, 127.0 + 40 / lon_m, 37.0 + 40 / M)  # cells 0..3 x 0..3
    m = landcover.green_mask(g, gpd.GeoDataFrame(geometry=[park], crs="EPSG:4326").to_crs(5179))
    assert m.dtype == bool and int(m.sum()) == 16 and m[:4, :4].all()
    assert not landcover.green_mask(g, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")).any()
    cn = landcover.cn_raster(m, spec)
    assert np.all(cn[m] == spec["landcover"]["cn_green"])
    assert np.all(cn[~m] == spec["rain"]["cn_paved"])
    assert spec["landcover"]["cn_green"] < spec["rain"]["cn_paved"]


def test_pluvial_cn_and_inflow_keep_the_balance() -> None:
    spec = load_spec()
    spec["rain"]["recession_min"] = 20.0
    _, ground, _, _ = _grid_and_ground()
    walls = np.zeros(ground.shape, dtype=bool)
    walls[5:8, 5:8] = True
    base = pluvial.run_pluvial(ground, walls, 10.0, spec)
    same = pluvial.run_pluvial(
        ground, walls, 10.0, spec, cn=np.full(ground.shape, float(spec["rain"]["cn_paved"]))
    )
    np.testing.assert_array_equal(base["h_max"], same["h_max"])
    green = np.zeros(ground.shape, dtype=bool)
    green[:, :6] = True
    soft = pluvial.run_pluvial(ground, walls, 10.0, spec, cn=landcover.cn_raster(green, spec))
    assert soft["rain_volume_m3"] < base["rain_volume_m3"]
    rows, cols, area = np.array([3, 9]), np.array([0, 0]), np.array([2e4, 5e3])
    wet = pluvial.run_pluvial(ground, walls, 10.0, spec, inflow=(rows, cols, area))
    assert wet["inflow_volume_m3"] > 0.0
    stored = float(wet["h"].sum()) * 100.0
    np.testing.assert_allclose(
        wet["rain_volume_m3"] + wet["inflow_volume_m3"],
        stored + wet["sink_volume_m3"] + wet["outflow_volume_m3"] + wet["sink_fn_volume_m3"],
        rtol=1e-6,
    )
    assert float(wet["h_max"].max()) >= float(base["h_max"].max())


def test_spec_sections() -> None:
    spec = load_spec()
    lc, ro = spec["landcover"], spec["runon"]
    assert set(lc["green_tags"]) <= {"leisure", "landuse", "natural"}
    assert ro["pad_m"] > 0
    cal = spec["calibration"]["sewer_mm_h"]
    assert cal == sorted(cal) and spec["rain"]["sewer_capacity_mm_h"] <= max(cal)
