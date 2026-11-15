"""Step R2b gate (written by the reviewer before the step; agents may not edit it).

Time viewer for a real site: the rain snapshots over real OSM footprints, with a toggle
between the run with buildings as walls and the run without buildings. Module
``worker/climaterisk_worker/twin/viewer_site.py``:

- ``render_site_view(with_npz, without_npz, grid, buildings, spec, out_html, title)`` -> Path.
  ``with_npz`` / ``without_npz`` hold ``times_s``, ``depth`` (n_t, ny, nx), ``bed`` (ny, nx),
  ``cell_m`` and ``walls`` (ny, nx) as written by ``--pluvial`` (the without-buildings file
  has an all-False ``walls``). ``grid`` is a ``site.SiteGrid``; cell ``(i, j)`` is drawn as the
  square around its centre (``SiteGrid`` convention: row i northward from ``grid.south``).
  ``buildings`` is the GeoDataFrame from ``site.read_buildings``; only walls
  (``is_wall``) are drawn, extruded to ``height_m``, coloured by the deepest water reached
  so far in the open cells touching the footprint (one-cell ring around the wall cells).
  Same page conventions as ``viewer_time``: deck.gl 9.4.1 and KaTeX 0.16.11 from
  cdn.jsdelivr.net only, ``parent`` (not ``container``) for the deck canvas, play/pause,
  frame slider, a "with buildings / without buildings" toggle, live numbers (time,
  flooded area > 10 cm in km^2, stored volume, max depth), the rain hyetograph drawn with a
  cursor, the equations, the method label "repo-specific pluvial method", and the
  attributions "© OpenStreetMap contributors" and "Copernicus DEM".
  The page embeds ``<script id="twin-data" type="application/json">`` with at least
  ``times_s``, ``ny``, ``nx``, ``cell_m``, ``bed``, ``frames_with`` and ``frames_without``
  (``viewer_time.encode_frames``) and ``buildings`` (list with ``ring`` = [[lon, lat], ...]
  and ``h``).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("geopandas")

import numpy as np  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

viewer_site = pytest.importorskip("climaterisk_worker.twin.viewer_site")


def _inputs(tmp_path: Path) -> tuple:
    import geopandas as gpd
    from climaterisk_worker.twin import site
    from shapely.geometry import box

    grid = site.site_grid((127.0, 37.0, 127.002, 37.0015), 10.0)
    ny, nx = grid.ny, grid.nx
    walls = np.zeros((ny, nx), dtype=bool)
    walls[5:9, 5:9] = True
    lat, lon = grid.centres()
    b = gpd.GeoDataFrame(
        {
            "building": ["office", "yes"],
            "height_m": [40.0, 9.9],
            "height_source": ["height", "typology"],
            "underground": [False, True],
            "is_wall": [True, False],
        },
        geometry=[
            box(lon[5, 5] - 4e-5, lat[5, 5] - 4e-5, lon[8, 8] + 4e-5, lat[8, 8] + 4e-5),
            box(lon[2, 2], lat[2, 2], lon[3, 3], lat[3, 3]),
        ],
        crs="EPSG:4326",
    )
    times = np.arange(0.0, 3600.0 + 1, 600.0)
    bed = np.zeros((ny, nx))
    d_with = np.zeros((times.size, ny, nx))
    d_without = np.zeros_like(d_with)
    for k in range(times.size):
        d_with[k] = 0.05 * k * ~walls
        d_without[k] = 0.04 * k
    paths = []
    for name, d, w in (("w.npz", d_with, walls), ("wo.npz", d_without, np.zeros_like(walls))):
        np.savez_compressed(tmp_path / name, times_s=times, depth=d, bed=bed, cell_m=10.0, walls=w)
        paths.append(tmp_path / name)
    return grid, b, paths, times, d_with, d_without


def test_site_page_carries_both_runs_and_real_footprints(tmp_path: Path) -> None:
    from climaterisk_worker.twin.viewer_time import decode_frame

    grid, b, (pw, pwo), times, d_with, d_without = _inputs(tmp_path)
    out = viewer_site.render_site_view(pw, pwo, grid, b, load_spec(), tmp_path / "s.html", "toy")
    html = Path(out).read_text(encoding="utf-8")
    m = re.search(r'<script id="twin-data" type="application/json">(.*?)</script>', html, re.S)
    assert m
    data = json.loads(m.group(1))
    assert (data["ny"], data["nx"]) == (grid.ny, grid.nx)
    assert len(data["frames_with"]) == len(data["frames_without"]) == times.size
    np.testing.assert_allclose(
        decode_frame(data["frames_with"][-1], grid.ny, grid.nx), d_with[-1], atol=0.005
    )
    np.testing.assert_allclose(
        decode_frame(data["frames_without"][3], grid.ny, grid.nx), d_without[3], atol=0.005
    )
    assert len(data["buildings"]) == 1  # the underground one is not a wall
    bld = data["buildings"][0]
    assert bld["h"] == pytest.approx(40.0)
    ring = np.asarray(bld["ring"])
    assert ring.shape[1] == 2 and ring[:, 0].min() > 126.99 and ring[:, 1].max() < 37.01
    for needle in (
        "https://cdn.jsdelivr.net/npm/deck.gl@9.4.1/dist.min.js",
        "katex@0.16.11",
        "parent:",
        "repo-specific pluvial method",
        "OpenStreetMap contributors",
        "Copernicus DEM",
        'type="range"',
        "\\partial",
    ):
        assert needle in html, needle
    assert "container:" not in html
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert srcs and all(s.startswith("https://cdn.jsdelivr.net/npm/") for s in srcs)
    assert len(html.encode("utf-8")) < 15_000_000
