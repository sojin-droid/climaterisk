"""Step R3c gate (written by the reviewer before the step; agents may not edit it).

Basement flooding of the real-site buildings during the design storm, after the user's
sheets 층별_침수피해율_비교(261020) and 지하침수_시험계산. Module
``worker/climaterisk_worker/twin/basement.py``; every number comes from ``spec["basement"]``,
``spec["loss"]`` and ``spec["site"]``, with ``T = spec["loss"]["entry_threshold_m"]`` and
``ff = spec["site"]["floor_to_floor_m"]``:

- ``basement_levels(buildings, spec)`` -> int array: 0 unless the row is a wall
  (``is_wall``, not ``underground``) with ``site_loss.storeys >= min_storeys``; then
  ``levels[building]`` (``levels["_default"]`` when unlisted).
- ``ring_series(grid, buildings, depth_t, walls)`` -> float ``(n_t, n_buildings)``: for every
  frame of ``depth_t`` (``(n_t, ny, nx)``, m) the maximum depth over the same ring that
  ``site_loss.entry_depths`` uses (8-neighbourhood of the footprint cells minus the cells minus
  every wall cell); 0 for rows that are not walls or whose ring is empty.
- ``basement_volume_m3(h_series, times_s, spec)`` -> float ``(n_buildings,)``: water that
  ends up in the basement over the run, through one ramp entrance as a broad-crested weir,
  plus the rain falling on the ramp, minus pumping, floored at 0:
  $$V = \\int C\\,B\\,\\max(h(t) - T, 0)^{3/2}\\,dt + A_r\\,P/1000 - Q_p\\,(t_{end} - t_0)/3600$$
  with the trapezoid rule over ``times_s`` (s), ``C = weir_coefficient``,
  ``B = ramp_width_m``, ``A_r = ramp_rain_area_m2``, ``P = spec["rain"]["total_mm"]``,
  ``Q_p = pump_m3_h``.
- ``basement_depths(volume_m3, area_m2, n_levels, spec)`` -> float
  ``(n_buildings, max(n_levels))``: column ``k`` is the ``k``-th basement storey counted from
  the LOWEST; water fills from the bottom with each storey's floor area equal to the
  footprint: depth ``clip(V / area - k ff, 0, ff)`` for ``k < n_levels``, else 0.
- ``price_with_basements(buildings, ground_depth, basement_depth, spec)`` -> dict with
  ``ground_loss_usd`` (``site_loss.price_buildings`` with ``site_loss.exposed_value_usd``),
  ``basement_value_usd`` (``n_levels * area * cost_usd_per_m2[type]``, type as in
  ``site_loss``), ``basement_loss_usd`` (per building, the sum over its basement storeys,
  each storey priced through the engine with ``site_loss.price_buildings`` at value
  ``area * cost`` and that storey's depth) and ``loss_usd`` (ground + basement).
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
pytest.importorskip("climada")

import numpy as np  # noqa: E402
from climaterisk_worker.twin import site_loss  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

basement = pytest.importorskip("climaterisk_worker.twin.basement")

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
            "building": ["office", "apartments", "no-such-value", "yes", "office"],
            "height_m": [45.0, 3.3, 30.0, 3.3, 4.0],
            "height_source": ["height"] * 5,
            "underground": [False, False, False, True, False],
            "is_wall": [True, True, True, False, False],
        },
        geometry=[
            _square(50, 50, 40),  # cells rows/cols 5..8
            _square(120, 120, 20),  # cells 12..13
            _square(150, 30, 20),  # rows 3..4, cols 15..16
            _square(0, 150, 30),  # underground
            _square(100, 0, 20),  # canopy (not a wall)
        ],
        crs="EPSG:4326",
    )
    walls = np.zeros((grid.ny, grid.nx), dtype=bool)
    walls[5:9, 5:9] = True
    walls[12:14, 12:14] = True
    walls[3:5, 15:17] = True
    return grid, b, walls


def test_basement_levels_follow_use_and_size() -> None:
    spec = load_spec()
    lv = spec["basement"]["levels"]
    assert spec["basement"]["min_storeys"] <= round(30.0 / spec["site"]["floor_to_floor_m"])
    _, b, _ = _toy()
    n = basement.basement_levels(b, spec)
    assert list(n) == [lv["office"], 0, lv["_default"], 0, 0]


def test_ring_series_matches_the_ring_of_entry_depths() -> None:
    spec = load_spec()
    grid, b, walls = _toy()
    rng = np.random.default_rng(3)
    depth_t = rng.uniform(0.0, 1.0, size=(4, grid.ny, grid.nx))
    s = basement.ring_series(grid, b, depth_t, walls)
    assert s.shape == (4, len(b))
    assert np.all(s[:, 3:] == 0.0)
    T = spec["loss"]["entry_threshold_m"]
    for k in range(4):
        d = site_loss.entry_depths(grid, b, depth_t[k], walls, spec)
        for i in range(3):
            np.testing.assert_allclose(d[i], s[k, i] if s[k, i] > T else 0.0, atol=1e-12)
    np.testing.assert_allclose(s[2, 0], depth_t[2, 4:10, 4:10][~walls[4:10, 4:10]].max())


def test_weir_volume_against_the_closed_form() -> None:
    spec = load_spec()
    bs = spec["basement"]
    T = spec["loss"]["entry_threshold_m"]
    rain = bs["ramp_rain_area_m2"] * spec["rain"]["total_mm"] / 1000.0
    times = np.arange(0.0, 3600.0 + 1, 300.0)
    h = np.zeros((times.size, 3))
    h[:, 0] = T + 0.25
    h[:, 1] = T  # at the threshold: nothing over the weir
    h[:, 2] = np.linspace(0.0, 1.0, times.size)  # rising: trapezoid of the weir flow
    v = basement.basement_volume_m3(h, times, spec)
    pump = bs["pump_m3_h"] * 1.0
    q = bs["weir_coefficient"] * bs["ramp_width_m"]
    np.testing.assert_allclose(v[0], max(q * 0.25**1.5 * 3600.0 + rain - pump, 0.0), rtol=1e-12)
    np.testing.assert_allclose(v[1], max(rain - pump, 0.0), rtol=1e-12)
    flow = q * np.maximum(h[:, 2] - T, 0.0) ** 1.5
    trap = float(np.sum(0.5 * (flow[1:] + flow[:-1]) * np.diff(times)))
    np.testing.assert_allclose(v[2], max(trap + rain - pump, 0.0), rtol=1e-12)
    assert np.all(v >= 0.0)


def test_basement_fills_from_the_lowest_storey() -> None:
    spec = load_spec()
    ff = spec["site"]["floor_to_floor_m"]
    area = np.array([1000.0, 1000.0, 500.0, 800.0])
    vol = np.array([1000.0 * ff * 1.5, 1e9, 0.0, 800.0 * ff * 0.25])
    n = np.array([4, 2, 3, 0])
    d = basement.basement_depths(vol, area, n, spec)
    assert d.shape == (4, 4)
    np.testing.assert_allclose(d[0], [ff, 0.5 * ff, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(d[1], [ff, ff, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(d[2], 0.0)
    np.testing.assert_allclose(d[3], 0.0)  # no basement


def test_basement_storeys_are_priced_through_the_engine() -> None:
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    spec = load_spec()
    ff = spec["site"]["floor_to_floor_m"]
    cost = spec["loss"]["cost_usd_per_m2"]
    _, b, _ = _toy()
    n = basement.basement_levels(b, spec)
    area = site_loss.footprint_area_m2(b)
    ground = np.array([0.4, 0.0, 0.2, 0.0, 0.0])
    bdepth = basement.basement_depths(
        np.array([area[0] * ff * 1.5, 0.0, area[2] * ff * 0.2, 0.0, 0.0]), area, n, spec
    )
    out = basement.price_with_basements(b, ground, bdepth, spec)
    g = site_loss.price_buildings(b, ground, site_loss.exposed_value_usd(b, spec), spec)
    np.testing.assert_allclose(out["ground_loss_usd"], g["loss_usd"], rtol=1e-12)
    for i in range(len(b)):
        kind = site_loss.property_type(b["building"].iloc[i], spec)
        f = registry.flood_impact_function("Asia", jrc_sector_for(kind))
        unit = area[i] * cost[kind]
        np.testing.assert_allclose(out["basement_value_usd"][i], n[i] * unit, rtol=1e-12)
        mdr = f.calc_mdr(bdepth[i, : n[i]]) if n[i] else np.zeros(0)
        np.testing.assert_allclose(out["basement_loss_usd"][i], unit * mdr.sum(), rtol=1e-9)
    assert out["basement_loss_usd"][0] > out["ground_loss_usd"][0] > 0
    np.testing.assert_allclose(
        out["loss_usd"], out["ground_loss_usd"] + out["basement_loss_usd"], rtol=1e-12
    )
