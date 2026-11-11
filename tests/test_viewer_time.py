"""Step 9b gate (written by the reviewer before the step; agents may not edit it).

Time viewer: animate 2D flood snapshots over the 3D synthetic city, with the governing
equations beside the picture. Module under test:
``worker/climaterisk_worker/twin/viewer_time.py`` with

- ``encode_frames(depth)`` -> list of str: one per time frame, the depth raster (m) of shape
  ``(n_t, ny, nx)`` rounded to whole centimetres, clipped to ``[0, 65535]`` cm, stored
  row-major as little-endian ``uint16`` and base64-encoded (standard alphabet).
- ``decode_frame(text, ny, nx)`` -> the inverse for one frame, in metres (float).
- ``render_time_view(npz_path, spec, out_html, title)`` -> ``out_html`` (Path). Reads a
  snapshot file with arrays ``times_s``, ``depth`` (n_t, ny, nx), ``bed`` (ny, nx) and
  ``cell_m`` (as written by ``--coastal2d``), and writes ONE self-contained HTML page:
  deck.gl from ``https://cdn.jsdelivr.net/npm/deck.gl@9.4.1/dist.min.js`` and KaTeX
  ``0.16.11`` from cdn.jsdelivr.net (exact versions), a play/pause button and a time
  slider over all frames, the water depth of the current frame on the raster, the
  synthetic buildings (``viewer3d.generate_buildings``) coloured by the deepest water
  reached at their cell **up to the current frame**, and a side panel with the
  local-inertial continuity and momentum equations (LaTeX) and live numbers for the
  current frame: time (h), flooded area (km^2, cells deeper than 1 cm), stored volume
  (m^3) and maximum depth (m).
  The page embeds one JSON object in ``<script id="twin-data" type="application/json">``
  with at least ``times_s``, ``ny``, ``nx``, ``cell_m``, ``frames`` (from
  ``encode_frames``) and ``bed``.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("networkx")

import numpy as np  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

viewer_time = pytest.importorskip("climaterisk_worker.twin.viewer_time")


def test_frames_round_trip_to_the_centimetre() -> None:
    rng = np.random.default_rng(1)
    depth = rng.uniform(0.0, 6.0, size=(3, 4, 5))
    depth[0, 0, 0] = 0.0
    frames = viewer_time.encode_frames(depth)
    assert len(frames) == 3
    raw = np.frombuffer(base64.b64decode(frames[1]), dtype="<u2").reshape(4, 5)
    np.testing.assert_array_equal(raw, np.round(depth[1] * 100).astype(np.uint16))
    for k in range(3):
        np.testing.assert_allclose(viewer_time.decode_frame(frames[k], 4, 5), depth[k], atol=0.005)


def _snapshot_file(tmp_path: Path, spec: dict) -> Path:
    """A small synthetic flood: water spreading north from the sea row over the city raster."""
    from climaterisk_worker.twin.city import build_city
    from climaterisk_worker.twin.inundation import city_raster

    graph, _ = build_city(spec)
    bed, cell, _ = city_raster(graph, spec)
    ny, nx = bed.shape
    times = np.arange(0.0, 6 * 3600.0 + 1, 1800.0)
    depth = np.zeros((times.size, ny, nx))
    for i in range(times.size):
        reach = int(i * ny / times.size)
        depth[i, : reach + 1, :] = np.maximum(0.0, 1.5 - bed[: reach + 1, :]) * (i / times.size)
    path = tmp_path / "snap.npz"
    np.savez_compressed(path, times_s=times, depth=depth, bed=bed, cell_m=cell)
    return path


def test_page_is_self_contained_and_carries_every_frame(tmp_path: Path) -> None:
    spec = load_spec()
    snap = _snapshot_file(tmp_path, spec)
    out = viewer_time.render_time_view(snap, spec, tmp_path / "flood_time.html", "test flood")
    html = Path(out).read_text(encoding="utf-8")
    assert "https://cdn.jsdelivr.net/npm/deck.gl@9.4.1/dist.min.js" in html
    assert re.search(r"cdn\.jsdelivr\.net/npm/katex@0\.16\.11/", html)
    match = re.search(r'<script id="twin-data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "embedded data block missing"
    data = json.loads(match.group(1))
    with np.load(snap) as z:
        times, depth, bed = z["times_s"], z["depth"], z["bed"]
    assert data["ny"] == bed.shape[0] and data["nx"] == bed.shape[1]
    np.testing.assert_allclose(data["times_s"], times)
    assert len(data["frames"]) == times.size
    last = viewer_time.decode_frame(data["frames"][-1], data["ny"], data["nx"])
    np.testing.assert_allclose(last, depth[-1], atol=0.005)
    np.testing.assert_allclose(np.asarray(data["bed"]).reshape(bed.shape), bed, atol=0.005)
    assert "test flood" in html
    for needle in ("play", 'type="range"', "\\partial", "Manning", "km"):
        assert needle in html, needle
    assert len(html.encode("utf-8")) < 15_000_000


def test_page_has_no_other_external_scripts(tmp_path: Path) -> None:
    spec = load_spec()
    out = viewer_time.render_time_view(
        _snapshot_file(tmp_path, spec), spec, tmp_path / "f.html", "t"
    )
    html = Path(out).read_text(encoding="utf-8")
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert srcs, "expected CDN scripts"
    assert all(s.startswith("https://cdn.jsdelivr.net/npm/") for s in srcs), srcs
