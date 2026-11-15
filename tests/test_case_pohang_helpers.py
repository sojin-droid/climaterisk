"""Helpers of the Pohang case twin (the protected gate is tests/test_case_pohang.py).

Analytical checks: log-log depth-duration interpolation, the sub-hour uniform spread, the
backwater profile against uniform flow, the pit-filling of a bowl and the monotone burn.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

cp = pytest.importorskip("climaterisk_worker.twin.case_pohang")

DD = {"60": 77.0, "120": 147.9, "180": 203.2, "360": 314.5, "720": 378.7}


def test_depth_at_loglog_and_uniform_hour() -> None:
    durs, depths = cp._curve(DD)
    assert cp.depth_at(60.0, durs, depths) == pytest.approx(77.0)
    assert cp.depth_at(30.0, durs, depths) == pytest.approx(38.5)  # uniform spread below 60 min
    b = math.log(147.9 / 77.0) / math.log(2.0)
    np.testing.assert_allclose(cp.depth_at(90.0, durs, depths), 77.0 * 1.5**b, rtol=1e-12)
    assert cp.depth_at(5000.0, durs, depths) == pytest.approx(378.7)


def test_hyetograph_peak_in_the_middle_and_alternating() -> None:
    inc = cp.nested_hyetograph(DD, 10)
    assert int(np.argmax(inc)) == (len(inc) - 1) // 2
    # the blocks fall off on both sides
    centre = int(np.argmax(inc))
    assert (np.diff(inc[centre:]) <= 1e-12).all()
    assert (np.diff(inc[: centre + 1]) >= -1e-12).all()


def test_hyetograph_rejects_non_multiple() -> None:
    with pytest.raises(ValueError):
        cp.nested_hyetograph({"60": 10.0, "125": 20.0}, 10)


def test_stage_profile_uniform_flow_far_from_the_boundary() -> None:
    n, w, q, s0, ds = 0.05, 90.0, 300.0, 0.001, 30.0
    h_normal = (q * n / (w * math.sqrt(s0))) ** 0.6
    k = np.arange(400)
    bed = (400 - k) * s0 * ds  # falls with slope s0 towards the end
    eta = cp._stage_profile(bed, np.full(400, w), np.full(400, q), n, bed[-1] + h_normal, ds)
    np.testing.assert_allclose(eta[0] - bed[0], h_normal, rtol=1e-2)  # normal depth upstream


def test_fill_depressions_closes_a_pit() -> None:
    z = np.add.outer(np.arange(8.0), np.zeros(8))[::-1].copy()  # z = 7 - row
    z[4, 4] -= 3.0  # a pit of 3 m; its lowest neighbour (row 5) is at 2 m
    f = cp.fill_depressions(z)
    np.testing.assert_allclose(f[4, 4], 2.0 + 1e-4, atol=1e-9)
    from climaterisk_worker.twin import runon

    recv = runon.d8_receivers(f)
    interior = np.zeros(z.shape, bool)
    interior[1:-1, 1:-1] = True
    assert (recv[interior] >= 0).all()
