"""Board layer config and metadata (the CLI's pure parts; no CLIMADA needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "scripts", REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import board_layer  # noqa: E402
from climaterisk_worker import board  # noqa: E402


def test_shipped_board_config_is_valid() -> None:
    cfg = board.load_board_config()
    rps = cfg["return_periods_years"]
    assert rps == sorted(rps) and all(p > 0 for p in rps)
    spec = cfg["layers"]["river_flood_hist_kor"]
    assert spec["country_iso3"] == "KOR" and spec["boundary_code_column"] == "SIGUNGU_CD"


def test_board_config_rejects_bad_periods(tmp_path: Path) -> None:
    bad = tmp_path / "visual_board.json"
    bad.write_text(json.dumps({"return_periods_years": [10, -1], "layers": {"a": {}}}))
    with pytest.raises(ValueError, match="return_periods_years"):
        board.load_board_config(bad)


def test_library_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLIMATERISK_LIBRARY_DIR", str(tmp_path))
    assert board.board_config_path() == tmp_path / "visual_board.json"


def test_period_column_names() -> None:
    assert [board_layer.period_column(p) for p in (2, 2.5, 100)] == ["rp2", "rp2.5", "rp100"]


def test_metadata_counts_null_zero_positive_and_cell_size() -> None:
    values = np.array([[0.0, 1.5, np.nan, 0.0], [2.0, 3.0, 0.0, 4.0]])
    result = {
        "periods": [5, 50],
        "max_supported": 460.0,
        "record_years": 920.0,
        "intensity": values,
        "piece_values": values,
    }
    spec = {
        "peril": "river_flood",
        "climate_scenario": "historical",
        "year_range": "1980_2000",
        "country_iso3": "KOR",
        "boundary_version": "2025_2Q",
    }
    meta = board_layer.build_metadata(
        layer="x",
        spec=spec,
        result=result,
        res_deg=150 / 3600,
        mean_lat=60.0,
        n_centroids=4,
        n_pieces=4,
        n_regions=2,
        n_regions_with_pieces=2,
        units="m",
        requested=[5, 50, 500],
    )
    assert meta["value_counts_over_pieces"]["null"] == {"5": 1, "50": 0}
    assert meta["value_counts_over_pieces"]["zero"] == {"5": 2, "50": 1}
    assert meta["value_counts_over_pieces"]["positive"] == {"5": 1, "50": 3}
    assert meta["resolution_arcsec"] == pytest.approx(150.0)
    np.testing.assert_allclose(meta["cell_km_ns"], 4.6383, rtol=1e-3)  # 150" x 111.32 km/deg
    np.testing.assert_allclose(meta["cell_km_ew"], meta["cell_km_ns"] * 0.5, rtol=1e-9)  # cos 60
    assert meta["periods_kept"] == [5.0, 50.0] and meta["columns"] == ["rp5", "rp50"]
