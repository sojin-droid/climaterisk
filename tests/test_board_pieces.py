"""Visual board step 1 gate (written by the reviewer before the step; agents may not edit it).

Module ``worker/climaterisk_worker/board.py`` (worker env: CLIMADA + geopandas). See
``docs/VISUAL_BOARD_KO.md`` §2. Functions under test:

- ``rp_intensity(hazard, return_periods)`` -> dict with ``periods`` (kept, ascending),
  ``max_supported`` and ``record_years`` exactly as ``physical._resolvable_return_periods``
  (cap = half the record) EXCEPT that nothing is substituted when every period is beyond
  the cap (``periods`` may be empty), and ``intensity``: float array ``(len(periods),
  n_centroids)`` from ``Hazard.local_exceedance_intensity(..., method="extrapolate_constant")``
  — a period shorter than the most frequent event at a centroid is 0, never NaN.
- ``grid_cells(lon, lat, res_deg)`` -> GeoDataFrame (EPSG:4326), one square cell of side
  ``res_deg`` centred on each centroid, column ``centroid`` = index into lon/lat.
- ``clip_pieces(cells, regions, code_col, name_col)`` -> GeoDataFrame of pieces = cell ∩ region
  with columns ``centroid``, ``code``, ``name``, ``area_km2`` (EPSG:5179 area) and geometry
  (EPSG:4326). Empty intersections are dropped. A cell split by a boundary gives one piece
  per region, all with the same ``centroid``. No averaging anywhere.
- ``lookup(pieces, lon, lat)`` -> dict ``{"centroid", "code", "name"}`` of the piece that
  contains the point, or ``None`` outside every piece.
- ``region_summary(pieces, values)`` -> dict code -> ``{"name", "n_pieces", "min", "max"}``
  where ``values`` is indexed by centroid; NaN values are ignored for min/max (all-NaN ->
  None). There is deliberately no mean.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("geopandas")

import numpy as np  # noqa: E402

board = pytest.importorskip("climaterisk_worker.board")


def _hazard(intensity: np.ndarray, frequency: np.ndarray, lat: np.ndarray, lon: np.ndarray):
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    n_ev = intensity.shape[0]
    return Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=lat, lon=lon),
        event_id=np.arange(1, n_ev + 1),
        event_name=[f"e{i}" for i in range(n_ev)],
        date=np.arange(1, n_ev + 1),
        frequency=frequency,
        intensity=sparse.csr_matrix(intensity),
        fraction=sparse.csr_matrix((intensity > 0).astype(float)),
    )


def test_rp_intensity_analytical_and_capped() -> None:
    # centroid 0: events of 1, 2, 3, 4 m, each 0.1 /yr -> exceedance freq 0.4, 0.3, 0.2, 0.1
    # (RP 2.5, 3.3, 5, 10). centroid 1: never flooded. record = 1 / 0.1 = 10 y -> cap 5 y.
    inten = np.array([[1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]])
    haz = _hazard(inten, np.full(4, 0.1), np.array([37.0, 37.0]), np.array([127.0, 127.1]))
    out = board.rp_intensity(haz, [2, 2.5, 5, 10])
    assert out["periods"] == [2, 2.5, 5]
    assert out["record_years"] == pytest.approx(10.0)
    assert out["max_supported"] == pytest.approx(5.0)
    i = np.asarray(out["intensity"])
    assert i.shape == (3, 2)
    np.testing.assert_allclose(i[:, 0], [0.0, 1.0, 3.0], rtol=1e-6, atol=1e-9)
    np.testing.assert_array_equal(i[:, 1], [0.0, 0.0, 0.0])  # 0, not NaN
    # every requested period beyond the cap -> nothing substituted
    assert board.rp_intensity(haz, [50, 100])["periods"] == []


def _regions():
    import geopandas as gpd
    from shapely.geometry import box

    # two regions split at lon 127.05; cells of 0.1 deg centred on 127.0 and 127.1
    return gpd.GeoDataFrame(
        {"SIGUNGU_CD": ["A", "B"], "SIGUNGU_NM": ["west", "east"]},
        geometry=[box(126.9, 36.9, 127.05, 37.2), box(127.05, 36.9, 127.2, 37.2)],
        crs="EPSG:4326",
    )


def test_cells_pieces_lookup_and_summary() -> None:
    lon = np.array([127.0, 127.1, 127.0])
    lat = np.array([37.0, 37.0, 37.1])
    cells = board.grid_cells(lon, lat, 0.1)
    assert len(cells) == 3 and list(cells["centroid"]) == [0, 1, 2]
    pieces = board.clip_pieces(cells, _regions(), "SIGUNGU_CD", "SIGUNGU_NM")
    # cell 0 (126.95-127.05) lies in A; cell 1 (127.05-127.15) in B; cell 2 in A
    assert sorted(zip(pieces["centroid"], pieces["code"], strict=True)) == [
        (0, "A"),
        (1, "B"),
        (2, "A"),
    ]
    # a cell straddling the boundary: centred on 127.05 -> two pieces, same centroid
    straddle = board.clip_pieces(
        board.grid_cells(np.array([127.05]), np.array([37.0]), 0.1),
        _regions(),
        "SIGUNGU_CD",
        "SIGUNGU_NM",
    )
    assert sorted(straddle["code"]) == ["A", "B"] and set(straddle["centroid"]) == {0}
    a = straddle.set_index("code")["area_km2"]
    assert a["A"] == pytest.approx(a["B"], rel=0.02)
    # pieces of a cell tile it: area sum = cell area
    cell_area = float(board.grid_cells(np.array([127.05]), np.array([37.0]), 0.1).to_crs(5179).area.iloc[0]) / 1e6
    assert float(a.sum()) == pytest.approx(cell_area, rel=1e-3)

    hit = board.lookup(pieces, 127.12, 37.01)
    assert hit == {"centroid": 1, "code": "B", "name": "east"}
    assert board.lookup(pieces, 130.0, 40.0) is None

    values = np.array([0.5, np.nan, 2.0])
    s = board.region_summary(pieces, values)
    assert s["A"] == {"name": "west", "n_pieces": 2, "min": 0.5, "max": 2.0}
    assert s["B"]["min"] is None and s["B"]["max"] is None
    assert all("mean" not in v for v in s.values())


def test_lookup_matches_nearest_centroid() -> None:
    """The board value at a point is the value an asset run reads (nearest centroid)."""
    from climada.util.coordinates import match_coordinates

    rng = np.random.default_rng(20261110)
    gx, gy = np.meshgrid(np.arange(126.95, 127.2, 0.05), np.arange(36.95, 37.2, 0.05))
    lon, lat = gx.ravel(), gy.ravel()
    pieces = board.clip_pieces(board.grid_cells(lon, lat, 0.05), _regions(), "SIGUNGU_CD", "SIGUNGU_NM")
    px = rng.uniform(126.93, 127.17, 200)
    py = rng.uniform(36.93, 37.17, 200)
    near = match_coordinates(np.column_stack([py, px]), np.column_stack([lat, lon]))
    for x, y, n in zip(px, py, near, strict=True):
        hit = board.lookup(pieces, float(x), float(y))
        if hit is not None:
            assert hit["centroid"] == int(n)
