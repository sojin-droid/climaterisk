"""The Korea-local river adapter's climate delta: factor cache, offline refusal, regions.

Run in the CLIMADA worker environment. Nothing here touches the network or the real maps.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")

import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker import _dataapi, _paths  # noqa: E402
from climaterisk_worker import flood_delta as fdl  # noqa: E402
from climaterisk_worker.physical_risk import adapters  # noqa: E402
from scipy import sparse  # noqa: E402

from climaterisk.physical_risk.metrics import hazard_forcing_family  # noqa: E402


def _isimip_like(depth_scale: float) -> Hazard:
    """Two events on three centroids; depth scales with ``depth_scale`` (m)."""
    haz = Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=np.array([37.5, 35.1, 33.4]), lon=np.array([127.0, 129.0, 126.5])),
        event_id=np.array([1, 2]),
        event_name=["a", "b"],
        date=np.zeros(2, dtype=int),
        frequency=np.array([0.05, 0.01]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(np.array([[1.0, 1.0, 0.0], [2.0, 3.0, 0.0]]) * depth_scale),
        fraction=sparse.csr_matrix(np.array([[1.0, 1.0, 0.0], [1.0, 1.0, 0.0]])),
    )
    haz.check()
    return haz


@pytest.fixture
def delta_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Cache under tmp, a fake Data API (present depth x1, future depth x2), fake 시도."""
    monkeypatch.setattr(_paths.paths, "cache_dir", lambda *p: tmp_path.joinpath(*p))
    calls: list[tuple[str, int]] = []

    def fake_load(self: Any, bbox: Any = None, points: Any = None) -> Hazard:
        calls.append((self.scenario, self.year))
        return _isimip_like(1.0 if self.year < 2030 else 2.0)

    monkeypatch.setattr(adapters.CountryFloodAdapter, "load", fake_load)
    monkeypatch.setattr(
        fdl,
        "assign_regions",
        lambda lat, lon, shp: np.array(["서울특별시" if la > 36 else "부산광역시" for la in lat]),
    )
    return {"calls": calls}


def test_future_factor_is_the_ratio_of_unit_eals_and_is_cached(delta_env: dict[str, Any]) -> None:
    a = adapters.KoreaLocalFloodAdapter("rcp85", 2050, "KOR")
    a.delta_min_wet = 1  # the fake grid has two wet centroids
    d = a._delta(points=[(37.5, 127.0), (35.1, 129.0)])
    assert d["basis"] == "national" and d["factor"] > 1.0
    assert d["isimip_future_dataset"].endswith("rcp85_KOR_2030_2050")
    assert d["isimip_present_dataset"].endswith("rcp85_KOR_2010_2030")
    assert set(d["by_region"]) == {"서울특별시", "부산광역시"}
    assert d["check"]["warn"] in (True, False) and d["check"]["n_assets_without_factor"] == 0
    assert len(delta_env["calls"]) == 2  # present window, then future window: one at a time
    again = adapters.KoreaLocalFloodAdapter("rcp85", 2050, "KOR")._delta(points=[(37.5, 127.0)])
    assert set(again["by_region"]) == set(d["by_region"])
    assert again["factor"] == pytest.approx(d["factor"])
    assert len(delta_env["calls"]) == 2  # cache hit, no second download


def test_present_window_is_factor_one_without_any_download(delta_env: dict[str, Any]) -> None:
    a = adapters.KoreaLocalFloodAdapter("rcp45", 2010, "KOR")
    assert a.describe().time_horizon == "2010–2030"
    d = a._delta(points=[(37.5, 127.0)])
    assert d["factor"] == 1.0 and delta_env["calls"] == []


def test_offline_without_cache_is_unavailable_never_factor_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_paths.paths, "cache_dir", lambda *p: tmp_path.joinpath(*p))

    def offline_load(self: Any, bbox: Any = None, points: Any = None) -> Any:
        raise _dataapi.OfflineError("offline mode: never fetched")

    monkeypatch.setattr(adapters.CountryFloodAdapter, "load", offline_load)
    a = adapters.KoreaLocalFloodAdapter("rcp85", 2050, "KOR")
    with pytest.raises(adapters.HazardUnavailable):
        a._delta(points=[(37.5, 127.0)])


def test_baseline_and_future_rows_share_a_model_chain_and_differ_in_period() -> None:
    base = adapters.KoreaLocalFloodAdapter("rcp45", 2010, "KOR").describe()
    fut = adapters.KoreaLocalFloodAdapter("rcp45", 2050, "KOR").describe()
    assert hazard_forcing_family("RF", base.hazard_dataset) == hazard_forcing_family(
        "RF", fut.hazard_dataset
    )
    assert (base.served_scenario, base.time_horizon) != (fut.served_scenario, fut.time_horizon)


def test_assign_regions_is_point_in_polygon_by_name(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import box

    shp = tmp_path / "sido.shp"
    gpd.GeoDataFrame(
        {"SIDO_NM": ["A", "B"]}, geometry=[box(126, 37, 128, 38), box(128, 37, 130, 38)], crs=4326
    ).to_file(shp)
    out = fdl.assign_regions(np.array([37.5, 37.5, 10.0]), np.array([127.0, 129.0, 127.0]), shp)
    assert list(out) == ["A", "B", fdl.UNASSIGNED]


def test_future_load_scales_frequency_and_reuses_the_map_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The map overlay runs once per asset set; the future run scales frequency, not depth."""
    calls: list[int] = []
    adapters.KoreaLocalFloodAdapter._map_memo = None

    def fake_map(self: Any, points: Any) -> Hazard:
        calls.append(1)
        self.diagnostics = {"sub_check": {"warn": None}}
        return _isimip_like(1.0)

    monkeypatch.setattr(adapters.KoreaLocalFloodAdapter, "_map_hazard", fake_map)
    monkeypatch.setattr(
        adapters.KoreaLocalFloodAdapter,
        "_delta",
        lambda self, points: {"factor": 1.0 if self.year < 2030 else 1.5, "check": None},
    )
    pts = [(37.5, 127.0), (35.1, 129.0)]
    base = adapters.KoreaLocalFloodAdapter("rcp85", 2010, "KOR")
    fut = adapters.KoreaLocalFloodAdapter("rcp85", 2050, "KOR")
    hb, hf = base.load(None, points=pts), fut.load(None, points=pts)
    assert len(calls) == 1  # second load hit the memo
    np.testing.assert_allclose(hf.frequency, hb.frequency * 1.5, rtol=1e-12)
    np.testing.assert_allclose(hf.intensity.toarray(), hb.intensity.toarray())
    assert fut.diagnostics["climate_delta"]["factor"] == 1.5
    assert "sub_check" in fut.diagnostics
    hb.frequency[:] = 0  # a returned hazard is a copy: the memo is not corrupted
    assert (
        adapters.KoreaLocalFloodAdapter("rcp85", 2010, "KOR").load(None, points=pts).frequency.sum()
        > 0
    )
    adapters.KoreaLocalFloodAdapter._map_memo = None
