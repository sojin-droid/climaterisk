"""Modeled-exposure source registry + graceful degradation (offline, hermetic).

Exercises the climada-free paths of ``climaterisk_worker.exposures``: the source
registry, the actionable help messages, and the fast-fail for sources that need an
explicit local data file (crop/osm) or are unknown. The litpop/blackmarble/gdp paths
import CLIMADA lazily and are covered by the worker-env regression suite, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker.exposures import (  # noqa: E402
    EXPOSURE_HELP,
    EXPOSURE_SOURCES,
    ExposureUnavailable,
    build_exposure,
)


def test_every_source_has_label_and_help() -> None:
    for source in EXPOSURE_SOURCES:
        assert EXPOSURE_SOURCES[source]["label"]
        assert EXPOSURE_SOURCES[source]["engine"]
        assert source in EXPOSURE_HELP and len(EXPOSURE_HELP[source]) > 20


def test_unknown_source_degrades() -> None:
    with pytest.raises(ExposureUnavailable) as exc:
        build_exposure("does_not_exist", "JPN")
    assert "unknown exposure source" in exc.value.detail


@pytest.mark.parametrize("source", ["crop", "osm", "raster"])
def test_file_gated_sources_fast_fail_with_actionable_help(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # These need an explicit local data file we cannot synthesise, so they must fail
    # fast with the actionable message — without importing CLIMADA. Point the resolvers
    # at an empty tmp dir so real data under ~/climada/data can't defeat the fast-fail.
    import climaterisk_worker.exposures as exposures

    monkeypatch.setattr(exposures, "_HOME_CLIMADA", tmp_path)
    monkeypatch.setattr(exposures, "_OSM_DIR", tmp_path / "osm")
    monkeypatch.delenv("CLIMATERISK_EXPOSURE_RASTER", raising=False)
    with pytest.raises(ExposureUnavailable) as exc:
        build_exposure(source, "JPN")
    assert exc.value.source == source
    assert exc.value.detail == EXPOSURE_HELP[source]
