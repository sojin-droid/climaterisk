"""The public repo must never carry 홍수위험지도 geometry (공공누리 제4유형).

Overlay results and summary statistics may be committed; the map files, their shapefile
parts, or vector/raster derivatives may not. This guards against an accidental ``git add``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_GEOMETRY = re.compile(r"\.(shp|shx|dbf|prj|cpg|zip|gpkg|geojson|kml|fgb|tif|tiff)$", re.I)
_FLOODMAP = re.compile(r"(^|/)(RFM_|CFM_)|floodmap/|홍수위험|침수지도|범람지도", re.I)


def test_no_flood_map_geometry_is_tracked() -> None:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    tracked = [p for p in out.decode().split("\0") if p]
    hits = [p for p in tracked if _GEOMETRY.search(p) and _FLOODMAP.search(p)]
    assert not hits, f"flood-map geometry must not be committed: {hits}"
