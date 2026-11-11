"""Step R3 gate (written by the reviewer before the step; agents may not edit it).

Per-building loss of one design storm on the real site, priced by the repo engine. Module
``worker/climaterisk_worker/twin/site_loss.py``; every number comes from ``spec["loss"]``:

- ``property_type(building, spec)`` -> str: ``spec["loss"]["property_type"][building]``,
  else ``spec["loss"]["property_type"]["_default"]``. Underground features use
  ``spec["loss"]["underground_property_type"]`` instead (see ``exposed_value_usd``).
- ``footprint_area_m2(buildings)`` -> float array: planar footprint area (m^2) in a
  metric CRS (e.g. the GeoDataFrame's estimated UTM zone).
- ``storeys(buildings, spec)`` -> int array: ``max(1, round(height_m / floor_to_floor_m))``.
- ``exposed_value_usd(buildings, spec)`` -> float array: the part of the building that
  street water can reach,
  ``area * min(storeys, max_exposed_storeys) * cost_usd_per_m2[property type]``;
  underground features count one storey with the underground property type.
- ``entry_depths(grid, buildings, h_max, walls, spec)`` -> float array (m), the depth that
  goes into the depth-damage curve, one per building row, with ``T = entry_threshold_m``.
  No-damage threshold rule of the user's sheet (무피해문턱_계산표 261020, FLOPROS-style):
  damage is 0 while the outside depth is at most T and, once over T, the unchanged JRC
  curve at the FULL outside depth (the curve is NOT shifted by T):
  * a wall (``is_wall``): its cells are those whose centre lies inside the footprint (the
    cell containing ``representative_point()`` when none does); the ring is the
    8-neighbourhood of those cells minus the cells themselves minus every wall cell;
    ``m = max(h_max over ring)`` (0 when the ring is empty), depth = ``m if m > T else 0``;
  * above ground but not a wall (roof, canopy): the same with ``m`` = max over its cells;
  * underground: water standing above the threshold over the footprint drains into it,
    ``V = sum over its cells of max(h_max - T, 0) * cell_m^2`` and
    depth = ``min(V / area, floor_to_floor_m)``.
- ``price_buildings(buildings, depth_m, value_usd, spec)`` -> dict with ``rows`` (one engine
  ``ResultRow`` per building, from ``engine.calculate(..., "RF", model_id=MODEL_ID,
  flood_region="Asia")`` against ONE river-flood (RF) event of frequency 1/year, centroids
  at each building's ``representative_point()``), ``loss_usd`` (float array = each row's
  ``eal_usd``, i.e. the loss of this one event) and ``event_loss_usd`` (portfolio
  ``ImpactCalc`` ``at_event[0]``; the sum of ``loss_usd`` must equal it, rtol 1e-9).
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
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

site_loss = pytest.importorskip("climaterisk_worker.twin.site_loss")

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
            "building": ["office", "apartments", "yes", "yes", "roof"],
            "height_m": [45.0, 1.0, 9.9, 3.3, 4.0],
            "height_source": ["height", "height", "typology", "typology", "typology"],
            "underground": [False, False, False, True, False],
            "is_wall": [True, True, True, False, False],
        },
        geometry=[
            _square(50, 50, 40),  # cells rows/cols 5..8
            _square(120, 120, 20),  # cells 12..13
            _square(156, 56, 3),  # tiny: no cell centre inside -> cell (5, 15)
            _square(0, 150, 30),  # underground, cells rows 15..17, cols 0..2
            _square(100, 0, 20),  # roof canopy, cells rows 0..1, cols 10..11
        ],
        crs="EPSG:4326",
    )
    walls = np.zeros((grid.ny, grid.nx), dtype=bool)
    walls[5:9, 5:9] = True
    walls[12:14, 12:14] = True
    return grid, b, walls


def test_property_type_area_storeys_and_value() -> None:
    spec = load_spec()
    loss = spec["loss"]
    ff = spec["site"]["floor_to_floor_m"]
    _, b, _ = _toy()
    types = [site_loss.property_type(k, spec) for k in b["building"]]
    assert types[0] == loss["property_type"].get("office", loss["property_type"]["_default"])
    assert types[1] == loss["property_type"]["apartments"]
    assert site_loss.property_type("no-such-osm-value", spec) == loss["property_type"]["_default"]
    area = site_loss.footprint_area_m2(b)
    np.testing.assert_allclose(area[:2], [1600.0, 400.0], rtol=0.01)
    st = site_loss.storeys(b, spec)
    assert list(st[:2]) == [round(45.0 / ff), 1]
    value = site_loss.exposed_value_usd(b, spec)
    cost = loss["cost_usd_per_m2"]
    k = loss["max_exposed_storeys"]
    np.testing.assert_allclose(value[0], area[0] * min(st[0], k) * cost[types[0]], rtol=1e-9)
    np.testing.assert_allclose(value[1], area[1] * 1 * cost[types[1]], rtol=1e-9)
    under = loss["underground_property_type"]
    np.testing.assert_allclose(value[3], area[3] * 1 * cost[under], rtol=1e-9)
    assert np.all(value > 0)


def test_entry_depths_ring_tiny_canopy_and_underground() -> None:
    spec = load_spec()
    s = spec["loss"]["entry_threshold_m"]
    ff = spec["site"]["floor_to_floor_m"]
    assert 0.0 < s < 0.2
    grid, b, walls = _toy()
    h = np.full((grid.ny, grid.nx), 0.2)
    h[walls] = 9.9  # wall cells never count
    h[4, 9] = 0.7  # diagonal ring cell of the office
    h[2, 5] = 5.0  # two cells away: not in the ring
    h[11, 11] = s * 0.5  # apartments ring: below the threshold everywhere
    h[11:15, 11:15] = np.where(walls[11:15, 11:15], 9.9, s * 0.5)
    h[4:7, 14:17] = 0.2
    h[4, 16] = 0.55  # ring of the tiny building's cell (5, 15)
    h[15:18, 0:3] = 0.3 + s  # over the underground footprint
    h[0:2, 10:12] = 0.4
    d = site_loss.entry_depths(grid, b, h, walls, spec)
    np.testing.assert_allclose(d[0], 0.7, atol=1e-12)  # full depth, not 0.7 - T
    assert d[1] == 0.0
    np.testing.assert_allclose(d[2], 0.55, atol=1e-12)
    area_u = site_loss.footprint_area_m2(b)[3]
    np.testing.assert_allclose(d[3], min(9 * 0.3 * 100.0 / area_u, ff), rtol=1e-9)
    np.testing.assert_allclose(d[4], 0.4, atol=1e-12)


def test_losses_go_through_the_engine_and_add_up() -> None:
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    spec = load_spec()
    _, b, _ = _toy()
    depth = np.array([0.0, 0.3, 1.0, 2.5, 0.05])
    value = np.array([1e6, 2e6, 3e6, 4e6, 5e6])
    out = site_loss.price_buildings(b, depth, value, spec)
    loss = out["loss_usd"]
    assert len(out["rows"]) == len(b) == loss.size
    assert loss[0] == 0.0
    for i in range(1, len(b)):
        pt = (
            spec["loss"]["underground_property_type"]
            if b["underground"].iloc[i]
            else site_loss.property_type(b["building"].iloc[i], spec)
        )
        f = registry.flood_impact_function("Asia", jrc_sector_for(pt))
        expect = value[i] * float(f.calc_mdr(np.array([depth[i]]))[0])
        np.testing.assert_allclose(loss[i], expect, rtol=1e-9)
        assert out["rows"][i].eal_usd == pytest.approx(loss[i], rel=1e-12)
    np.testing.assert_allclose(loss.sum(), out["event_loss_usd"], rtol=1e-9)


def test_real_site_buildings_all_get_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from climaterisk import paths

    monkeypatch.setenv("CLIMATERISK_DATA_ROOT", str(paths.default_data_root()))
    paths.clear_cache()
    spec = load_spec()
    try:
        from climaterisk_worker.twin.site import build_site

        st = build_site(spec)
    except (FileNotFoundError, OSError) as exc:  # data not on this machine
        pytest.skip(f"real site data missing: {exc}")
    b, grid, walls = st["buildings"], st["grid"], st["mask"]
    value = site_loss.exposed_value_usd(b, spec)
    assert np.all(np.isfinite(value)) and np.all(value > 0)
    assert b["underground"].any()
    h = np.where(walls, 0.0, 0.3 + spec["loss"]["entry_threshold_m"])
    d = site_loss.entry_depths(grid, b, h, walls, spec)
    wall_rows = b["is_wall"].to_numpy()
    np.testing.assert_allclose(d[wall_rows], 0.3 + spec["loss"]["entry_threshold_m"], atol=1e-9)
    assert np.all(d[b["underground"].to_numpy()] > 0)
