"""Flood defences as a design return period, against hand-computed event sets.

A layered set like Aqueduct's (one event per return period, incremental frequencies): with
protection to 10 years, the 2-, 5- and 10-year layers are defended and only the rarer ones
flood, with their full depth. Needs the worker environment (CLIMADA).
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
pytest.importorskip("climada_petals")

import numpy as np  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402
from climaterisk_worker.physical_risk import protection  # noqa: E402
from scipy import sparse  # noqa: E402

RPS = [2, 5, 10, 100, 1000]
# incremental exceedance frequency 1/RP_i - 1/RP_{i+1}; the rarest keeps 1/RP
FREQ = np.array([1 / 2 - 1 / 5, 1 / 5 - 1 / 10, 1 / 10 - 1 / 100, 1 / 100 - 1 / 1000, 1 / 1000])
# centroid 0: floods from the 2-year layer (reclaimed land); 1: only from 100 years; 2: dry
DEPTH = np.array(
    [
        [1.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [3.0, 0.0, 0.0],
        [4.0, 0.5, 0.0],
        [5.0, 1.5, 0.0],
    ]
)


def _layered() -> Hazard:
    return Hazard(
        haz_type="CF",
        units="m",
        centroids=Centroids(lat=np.array([37.0, 37.1, 37.2]), lon=np.array([126.0] * 3)),
        event_id=np.arange(1, 6),
        event_name=[f"rp{r}" for r in RPS],
        date=np.zeros(5, dtype=int),
        frequency=FREQ,
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(DEPTH),
        fraction=sparse.csr_matrix((DEPTH > 0).astype(float)),
    )


def test_local_intensity_reads_the_layer_depth() -> None:
    np.testing.assert_allclose(protection.local_intensity(_layered(), 10), [3.0, 0.0, 0.0])
    np.testing.assert_allclose(protection.local_intensity(_layered(), 100), [4.0, 0.5, 0.0])


def test_events_up_to_the_design_return_period_are_removed() -> None:
    """P = 10: centroid 0 keeps only the 100- and 1000-year depths (4 m, 5 m, unreduced)."""
    out = protection.protect(_layered(), 10).intensity.toarray()
    np.testing.assert_allclose(out[:, 0], [0.0, 0.0, 0.0, 4.0, 5.0])
    np.testing.assert_allclose(out[:, 1], DEPTH[:, 1])  # its 10-yr depth is 0: nothing defended
    np.testing.assert_allclose(out[:, 2], 0.0)


def test_protection_beyond_the_record_defends_everything() -> None:
    out = protection.protect(_layered(), 5000).intensity.toarray()
    np.testing.assert_allclose(out, 0.0)


def test_expected_depth_falls_to_the_overtopping_events() -> None:
    """Σ f·I at centroid 0: undefended 1.7·… vs defended f(100)·4 + f(1000)·5 = 0.041."""
    raw = float(FREQ @ DEPTH[:, 0])
    defended = float(FREQ @ protection.protect(_layered(), 10).intensity.toarray()[:, 0])
    np.testing.assert_allclose(raw, 0.3 * 1 + 0.1 * 2 + 0.09 * 3 + 0.009 * 4 + 0.001 * 5)
    np.testing.assert_allclose(defended, 0.009 * 4 + 0.001 * 5)


def test_frequent_flooding_flag_marks_land_that_must_be_defended() -> None:
    frequent = protection.local_intensity(_layered(), 2) > 0
    assert frequent.tolist() == [True, False, False]
