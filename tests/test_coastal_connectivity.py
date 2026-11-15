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
