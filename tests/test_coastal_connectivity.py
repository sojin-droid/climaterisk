"""Gate for Korea-local coastal flood (method tier T2, hazard-only), written before the code.

Module ``climaterisk_worker.coastal_connectivity`` — the connected-bathtub test validated in the
agrivoltaic analysis, applied at asset points:

* ``station_ahhw(df)`` — AHHW above MSL per KHOA station = (M2+S2+K1+O1 amplitudes) / 100 m.
* ``nearest_station(lat, lon, stations)`` — nearest by cos(lat)-scaled distance.
* ``tidal_mask(z, threshold, sea)`` — low = z < threshold; a low cell is tidal when its
  8-connected component of (low ∪ sea) contains sea. NaN elevation counts as sea.
* ``block_reduce(a, f, how)`` — ``min`` (removes barriers narrower than f cells) or ``max``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("scipy")
REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from climaterisk_worker import coastal_connectivity as cc  # noqa: E402


def test_station_ahhw_sums_four_amplitudes_in_metres() -> None:
    df = pd.DataFrame(
        {
            "관측소 명": ["인천", "묵호"],
            "위도": [37.452, 37.55],
            "경도": [126.592, 129.116],
            "M2 진폭": [282.0, 6.0],
            "S2 진폭": [112.0, 2.0],
            "K1 진폭": [41.0, 5.0],
            "O1 진폭": [31.7, 4.3],
        }
    )
    out = cc.station_ahhw(df)
    np.testing.assert_allclose(out["ahhw_m"].to_numpy(), [4.667, 0.173], atol=1e-9)


def test_nearest_station_uses_scaled_longitude() -> None:
    st = pd.DataFrame(
        {"name": ["W", "E"], "lat": [37.0, 37.0], "lon": [126.0, 127.0], "ahhw_m": [4.0, 1.0]}
    )
    assert cc.nearest_station(37.0, 126.4, st)["name"] == "W"
    assert cc.nearest_station(37.0, 126.6, st)["name"] == "E"


def test_basin_behind_a_wall_is_not_tidal_but_a_channel_is() -> None:
    #   sea | wall | low basin           (row 0..4, col 0..6)
    z = np.full((5, 7), 3.0)
    z[:, 0] = np.nan  # sea (NaN)
    z[:, 1] = 9.0  # wall above the threshold
    z[1:4, 3:6] = 1.0  # low basin behind the wall
    sea = np.isnan(z)
    t = cc.tidal_mask(z, 2.0, sea)
    assert not t[2, 4]  # walled off
    z2 = z.copy()
    z2[2, 1] = 1.0  # a gap in the wall
    z2[2, 2] = 1.0  # channel to the basin
    t2 = cc.tidal_mask(z2, 2.0, np.isnan(z2))
    assert t2[2, 4] and t2[2, 2]
    assert not t2[0, 6]  # high ground never tidal


def test_threshold_rise_reconnects() -> None:
    z = np.array([[np.nan, 2.5, 1.0, 1.0]])
    assert not cc.tidal_mask(z, 2.0, np.isnan(z))[0, 3]
    assert cc.tidal_mask(z, 2.0 + 1.0, np.isnan(z))[0, 3]  # +1.0 m sea-level rise


def test_diagonal_counts_as_connected() -> None:
    z = np.array([[np.nan, 9.0], [9.0, 1.0]])
    assert cc.tidal_mask(z, 2.0, np.isnan(z))[1, 1]


def test_block_reduce_min_removes_narrow_walls_and_max_keeps_them() -> None:
    a = np.array([[1.0, 9.0, 1.0], [1.0, 9.0, 1.0], [1.0, 9.0, 1.0]])
    np.testing.assert_allclose(cc.block_reduce(a, 3, "min"), [[1.0]])
    np.testing.assert_allclose(cc.block_reduce(a, 3, "max"), [[9.0]])
    with pytest.raises(ValueError):
        cc.block_reduce(a, 3, "mean")


def test_adapter_is_hazard_only_korea_local() -> None:
    pytest.importorskip("climada")
    from climaterisk_worker.physical_risk import adapters

    a = adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR")
    assert a.status == adapters.HAZARD_ONLY and a.hazard_type == "CF"
    d = a.describe()
    assert d.hazard_dataset == "khoa_ahhw_connected_bathtub" and d.served_scenario == "historical"
    assert "약최고고조면" in d.hazard_source


def test_real_points_when_data_present() -> None:
    pytest.importorskip("climada")
    from climaterisk_worker._paths import paths
    from climaterisk_worker.physical_risk import adapters

    khoa = paths.external_dir("khoa", "tidebed_harmonics_20250812.csv")
    if not khoa.exists():
        pytest.skip("KHOA harmonics not on this machine")
    a = adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR")
    # 해남 저지대 논 (agrivoltaic analysis: ground -0.11 m, 벽파진 AHHW 2.12 m,
    # sea-connected) and 서울시청
    h = a.load(None, points=[(34.6249, 126.4211), (37.5665, 126.978)])
    rows = a.diagnostics["assets"]
    assert rows[0]["connected"] is True and rows[0]["station"] == "벽파진"
    assert rows[0]["depth_m"]["slr0"] > 1.0
    assert rows[0]["depth_m"]["slr0.5"] == pytest.approx(rows[0]["depth_m"]["slr0"] + 0.5, abs=1e-6)
    assert rows[1]["connected"] is False and rows[1]["depth_m"]["slr0"] == 0.0
    assert float(h.intensity.toarray()[0, 0]) == pytest.approx(rows[0]["depth_m"]["slr0"])
    assert "narrow_only" in a.diagnostics["sub_check"]
    assert rows[0]["connected_by_slr"] == {"slr0": True, "slr0.5": True, "slr1": True}
    b = adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR", slr_m=0.5)
    hb = b.load(None, points=[(34.6249, 126.4211)])
    assert hb.event_name == ["ahhw+0.5m"]
    assert float(hb.intensity.toarray()[0, 0]) == pytest.approx(rows[0]["depth_m"]["slr0.5"])


def test_mosaic_edge_is_half_a_pixel_west_and_north(monkeypatch: pytest.MonkeyPatch) -> None:
    """GLO-30 pixels are centred on the tile's integer corner (AREA_OR_POINT=Point).

    Tile N34_E126 has its transform origin at (125.999861, 35.000139): the grid edge is half a
    1 arcsec pixel west and north of the corner. A point just west of 126° is still inside the
    first 90 m block; with the edge on the corner it fell outside the mosaic.
    """
    n = cc.CELLS_PER_DEG
    monkeypatch.setattr(
        cc, "load_tile", lambda la, lo, allow_download: (np.zeros((n, n)), np.zeros((n, n)))
    )
    mos = cc.build_mosaic((126.2, 34.2, 126.8, 34.8), allow_download=False)
    half = 0.5 / 3600
    assert mos.west == pytest.approx(126.0 - half, abs=1e-12)
    assert mos.north == pytest.approx(35.0 + half, abs=1e-12)
    assert mos.cell(35.0, 126.0) == (0, 0)  # the first pixel centre
    assert mos.cell(35.0 + 0.9 * half, 126.0 - 0.9 * half) == (0, 0)  # its north-west quarter
    # the first 90 m block spans three 1" pixels: 126° - half .. 126° + 2.5"
    assert mos.cell(35.0, 126.0 + 2.4 / 3600) == (0, 0)
    assert mos.cell(35.0, 126.0 + 2.6 / 3600) == (0, 1)


def _one_row_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ground_m: float
) -> tuple[float, float]:
    """sea | ridge 2.5 m | low 1.0 m | low 1.0 m, station AHHW 2.0 m; returns the last cell's point.

    At AHHW + 0 and + 0.5 m the ridge (2.5 m, not below 2.5) keeps the low cells dry; at
    + 1.0 m (threshold 3.0 m) they join the sea.
    """
    from climaterisk_worker import _paths
    from climaterisk_worker.physical_risk import adapters

    half = cc.PIXEL_HALF_DEG
    z = np.array([[np.nan, 2.5, 1.0, 1.0]], dtype=np.float32)
    mos = cc.Mosaic(west=126.0 - half, north=35.0 + half, zmin=z, zmax=z.copy())
    stations = pd.DataFrame({"name": ["S"], "lat": [35.0], "lon": [126.0], "ahhw_m": [2.0]})
    for f in ("khoa.csv", "land.shp"):
        (tmp_path / f).write_text("")

    class Paths:
        @staticmethod
        def external_dir(*parts: str) -> Path:
            return tmp_path / ("khoa.csv" if parts[0] == "khoa" else "land.shp")

    monkeypatch.setattr(_paths, "paths", Paths)
    monkeypatch.setattr(cc, "load_stations", lambda path: stations)
    monkeypatch.setattr(cc, "build_mosaic", lambda bbox, allow_download: mos)
    monkeypatch.setattr(cc, "land_mask", lambda m, shp: np.array([[False, True, True, True]]))
    monkeypatch.setattr(
        adapters.KoreaLocalCoastalFloodAdapter,
        "_ground",
        staticmethod(lambda lat, lon, p, o: (np.full(len(lat), ground_m), ["glo30"] * len(lat))),
    )
    res = 1.0 / cc.CELLS_PER_DEG
    return (mos.north - 0.5 * res, mos.west + 3.5 * res)


def test_slr_increment_is_its_own_hazard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Depth = AHHW + s - ground once the cell is sea-connected at that level (analytical)."""
    pytest.importorskip("climada")
    from climaterisk_worker.physical_risk import adapters

    point = _one_row_world(monkeypatch, tmp_path, ground_m=1.0)
    expected = {0.0: 0.0, 0.5: 0.0, 1.0: 2.0 + 1.0 - 1.0}
    for s, depth in expected.items():
        a = adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR", slr_m=s)
        h = a.load(None, points=[point])
        assert h.event_name == ["ahhw" if s == 0.0 else f"ahhw+{s:g}m"]
        np.testing.assert_allclose(h.intensity.toarray(), [[depth]], atol=1e-6)
        row = a.diagnostics["assets"][0]
        assert row["connected_by_slr"] == {"slr0": False, "slr0.5": False, "slr1": True}
        assert row["connected"] is False  # slr 0, unchanged meaning
        np.testing.assert_allclose(
            [row["depth_m"][k] for k in ("slr0", "slr0.5", "slr1")], [0.0, 0.0, 2.0], atol=1e-6
        )
        assert a.diagnostics["slr_m"] == s
    d = adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR", slr_m=1.0).describe()
    assert "+ 1 m (increment, no year)" in d.time_horizon


def test_cell_state_is_kept_when_the_ground_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing ground leaves the depth None (never 0) but the cell's connectivity is known."""
    pytest.importorskip("climada")
    from climaterisk_worker.physical_risk import adapters

    point = _one_row_world(monkeypatch, tmp_path, ground_m=float("nan"))
    a = adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR", slr_m=1.0)
    h = a.load(None, points=[point])
    row = a.diagnostics["assets"][0]
    assert row["connected"] is None and row["depth_m"]["slr1"] is None
    assert row["connected_by_slr"]["slr1"] is True
    assert np.isnan(h.intensity.toarray()[0, 0])


@pytest.mark.parametrize("bad", [-0.5, float("nan"), float("inf")])
def test_slr_increment_must_be_finite_and_non_negative(bad: float) -> None:
    pytest.importorskip("climada")
    from climaterisk_worker.physical_risk import adapters

    with pytest.raises(ValueError):
        adapters.KoreaLocalCoastalFloodAdapter("historical", 2020, "KOR", slr_m=bad)


def test_coastal_ground_is_glo30_only_never_ngii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec rule 5: one national ground (GLO-30) for the coastal main value, even with NGII."""
    pytest.importorskip("climada")
    from climaterisk_worker import floodmap_depth as fd
    from climaterisk_worker.physical_risk import adapters

    ngii = tmp_path / "ngii_dem"
    ngii.mkdir()
    (ngii / "tile.img").write_bytes(b"")  # an NGII tile is on disk

    class Paths:
        @staticmethod
        def external_dir(*parts: str) -> Path:
            return tmp_path.joinpath(*parts)

    def no_ngii(*a: object, **k: object) -> None:
        raise AssertionError("NGII must not be read for the coastal main value")

    class FakeGlo:
        def __init__(self, tile_dir: Path, allow_download: bool) -> None:
            assert allow_download is False  # offline: never a download

        def __call__(self, xy: np.ndarray) -> np.ndarray:
            return np.full(len(xy), 1.25)

        def close(self) -> None:
            pass

    monkeypatch.setattr(fd, "RasterSampler", no_ngii)
    monkeypatch.setattr(fd, "Glo30Sampler", FakeGlo)
    ground, source = adapters.KoreaLocalCoastalFloodAdapter._ground(
        np.array([34.6, 35.0]), np.array([126.4, 129.0]), Paths, lambda: True
    )
    np.testing.assert_allclose(ground, [1.25, 1.25])
    assert source == ["glo30_surface_model", "glo30_surface_model"]
