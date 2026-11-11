"""Step R1 gate (written by the reviewer before the step; agents may not edit it).

A real neighbourhood from public data (user approval 2026-11-05): OpenStreetMap buildings
and the Copernicus GLO-30 surface model, turned into a hydraulic raster where buildings are
walls. Module under test: ``worker/climaterisk_worker/twin/site.py``, plus one addition to
``twin/hydraulics.py``:

- ``hydraulics.run(..., blocked=None)``: optional boolean raster; every face touching a
  blocked cell carries zero discharge, rain/inflow on blocked cells is ignored, so blocked
  cells stay at their initial depth and water flows around them.
- ``SiteGrid`` (dataclass): ``west``, ``south`` (deg), ``cell_m`` (m), ``nx``, ``ny``; cell
  ``(i, j)`` (row i northward, column j eastward) has its centre at
  ``x = (j + 0.5) cell_m``, ``y = (i + 0.5) cell_m`` metres from the south-west corner, and
  ``lat = south + y / 111320``, ``lon = west + x / (111320 cos(south))``. Methods
  ``centres() -> (lat, lon)`` arrays of shape ``(ny, nx)``.
- ``site_grid(bbox, cell_m)`` with ``bbox = (west, south, east, north)``: ``nx``, ``ny`` =
  floor of the bbox extent in metres over ``cell_m``.
- ``read_buildings(osm_path, bbox, spec)`` -> GeoDataFrame (EPSG:4326) of building
  polygons inside the bbox, columns ``building`` (OSM value), ``height_m`` and
  ``height_source``: the ``height`` tag when it parses as metres ("45", "45 m", "45.5");
  else ``building:levels`` x ``spec["site"]["floor_to_floor_m"]``; else
  ``spec["site"]["default_levels"][building]`` (falling back to ``["yes"]``) x
  ``floor_to_floor_m``; ``height_source`` is ``"height"``, ``"levels"`` or ``"typology"``.
  Added in review (the Gangnam underground mall was walling off the station crossing):
  ``underground`` (``layer`` < 0, ``location=underground`` or ``tunnel=yes``) and ``is_wall``
  (false for underground features and open structures such as ``building=roof``).
- ``building_mask(grid, buildings)`` -> boolean ``(ny, nx)``: cell centre inside a footprint.
- ``bare_earth(dsm_path, grid, mask)`` -> float ``(ny, nx)`` ground elevation (m): the surface
  model sampled at the cell centres (bilinear), then every masked cell replaced by
  interpolation from unmasked cells (linear inside their convex hull, nearest outside), so a
  building standing on a plane leaves the plane behind.
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
pytest.importorskip("rasterio")

import numpy as np  # noqa: E402
from climaterisk_worker.twin import hydraulics  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

site = pytest.importorskip("climaterisk_worker.twin.site")

M = 111320.0
BBOX = (127.0, 37.0, 127.004, 37.003)  # ~355 m x 334 m


def _osm_xml(path: Path) -> Path:
    """Three square buildings (~40 m) with a height tag, a levels tag and no tag."""
    lon_m = M * math.cos(math.radians(37.0))

    def square(x0: float, y0: float, s: float = 40.0) -> list[tuple[float, float]]:
        pts = [(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)]
        return [(37.0 + y / M, 127.0 + x / lon_m) for x, y in pts]

    blds = [
        (square(50, 50), {"building": "office", "height": "45 m"}),
        (square(150, 50), {"building": "apartments", "building:levels": "12"}),
        (square(250, 150), {"building": "commercial"}),
        (square(50, 200), {"building": "yes", "layer": "-1", "location": "underground"}),
        (square(150, 200), {"building": "roof", "layer": "1"}),
    ]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="test">']
    nid, ways = 1, []
    for k, (pts, tags) in enumerate(blds):
        ids = []
        for lat, lon in pts:
            lines.append(f'<node id="{nid}" version="1" lat="{lat:.9f}" lon="{lon:.9f}"/>')
            ids.append(nid)
            nid += 1
        ways.append((100 + k, [*ids, ids[0]], tags))
    for wid, ids, tags in ways:
        lines.append(f'<way id="{wid}" version="1">')
        lines += [f'<nd ref="{i}"/>' for i in ids]
        lines += [f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()]
        lines.append("</way>")
    lines.append("</osm>")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_site_grid_geometry() -> None:
    grid = site.site_grid(BBOX, 10.0)
    width = (BBOX[2] - BBOX[0]) * M * math.cos(math.radians(BBOX[1]))
    height = (BBOX[3] - BBOX[1]) * M
    assert (grid.nx, grid.ny) == (int(width // 10.0), int(height // 10.0))
    lat, lon = grid.centres()
    assert lat.shape == (grid.ny, grid.nx)
    assert lat[0, 0] == pytest.approx(37.0 + 5.0 / M, abs=1e-12)
    assert lon[0, 0] == pytest.approx(127.0 + 5.0 / (M * math.cos(math.radians(37.0))), abs=1e-12)
    assert lat[1, 0] - lat[0, 0] == pytest.approx(10.0 / M, rel=1e-9)


def test_building_heights_follow_the_tag_rules(tmp_path: Path) -> None:
    spec = load_spec()
    ff = spec["site"]["floor_to_floor_m"]
    b = site.read_buildings(_osm_xml(tmp_path / "t.osm"), BBOX, spec)
    assert b.crs.to_epsg() == 4326 and len(b) == 5
    under = b[b["underground"]]
    assert len(under) == 1 and not under["is_wall"].any()
    assert not b[b["building"] == "roof"]["is_wall"].any()
    b = b[~b["underground"] & (b["building"] != "roof")]
    assert b["is_wall"].all()
    by = {r.building: r for r in b.itertuples()}
    assert by["office"].height_m == pytest.approx(45.0)
    assert by["office"].height_source == "height"
    assert by["apartments"].height_m == pytest.approx(12 * ff)
    assert by["apartments"].height_source == "levels"
    default = spec["site"]["default_levels"].get(
        "commercial", spec["site"]["default_levels"]["yes"]
    )
    assert by["commercial"].height_m == pytest.approx(default * ff)
    assert by["commercial"].height_source == "typology"


def test_building_mask_marks_cells_inside_footprints(tmp_path: Path) -> None:
    grid = site.site_grid(BBOX, 10.0)
    b = site.read_buildings(_osm_xml(tmp_path / "t.osm"), BBOX, load_spec())
    b = b[b["is_wall"]]
    mask = site.building_mask(grid, b)
    assert mask.shape == (grid.ny, grid.nx) and mask.dtype == bool
    assert mask.sum() == 3 * 16  # 40 m squares on 10 m cells, edges on cell boundaries
    assert mask[5, 5] and mask[5, 15] and mask[15, 25] and not mask[0, 0]


def test_bare_earth_removes_buildings_from_a_plane(tmp_path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    res = 1.0 / 3600.0  # ~30 m, like GLO-30
    west, north, n = 126.999, 37.004, 40
    lat = north - (np.arange(n) + 0.5) * res
    lon = west + (np.arange(n) + 0.5) * res
    plane = 10.0 + 2000.0 * (lat[:, None] - 37.0) + 1000.0 * (lon[None, :] - 127.0)
    dsm = plane.copy()
    dsm[7:11, 8:12] += 30.0  # a building in the surface model (inside BBOX)
    path = tmp_path / "dsm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, res, res),
    ) as ds:
        ds.write(dsm.astype("float32"), 1)
    grid = site.site_grid(BBOX, 10.0)
    glat, glon = grid.centres()
    truth = 10.0 + 2000.0 * (glat - 37.0) + 1000.0 * (glon - 127.0)
    bump = (np.abs(glat - lat[7:11].mean()) < 3 * res) & (np.abs(glon - lon[8:12].mean()) < 3 * res)
    assert bump.sum() > 0
    ground = site.bare_earth(path, grid, bump)
    np.testing.assert_allclose(ground[~bump], truth[~bump], atol=0.05)
    np.testing.assert_allclose(ground[bump], truth[bump], atol=0.5)


def test_blocked_cells_stay_dry_and_mass_is_kept() -> None:
    ny, nx = 20, 30
    bed = np.zeros((ny, nx))
    blocked = np.zeros((ny, nx), dtype=bool)
    blocked[5:15, 12:16] = True  # a building in the way
    h0 = np.zeros((ny, nx))
    h0[:, :5] = 1.0
    res = hydraulics.run(h0, bed, 5.0, 5.0, 0.03, t_end=120.0, blocked=blocked)
    assert np.all(res["h"][blocked] == 0.0)
    assert np.all(res["h_max"][blocked] == 0.0)
    np.testing.assert_allclose(res["h"].sum(), h0.sum(), rtol=1e-12)
    assert res["h"][:, 20:].sum() > 0  # water went round the building
    free = hydraulics.run(h0, bed, 5.0, 5.0, 0.03, t_end=120.0)
    assert free["h"][blocked].sum() > 0  # without walls it would have flooded those cells


def test_rain_on_blocked_cells_is_ignored() -> None:
    bed = np.zeros((10, 10))
    blocked = np.zeros((10, 10), dtype=bool)
    blocked[3:6, 3:6] = True
    rain = np.full((10, 10), 1e-4)
    res = hydraulics.run(
        np.zeros((10, 10)), bed, 1.0, 1.0, 0.03, t_end=100.0, rain=rain, blocked=blocked
    )
    assert np.all(res["h"][blocked] == 0.0)
    np.testing.assert_allclose(res["h"].sum(), 1e-4 * 100.0 * (100 - 9), rtol=1e-9)
