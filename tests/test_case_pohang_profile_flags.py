"""Pohang figure-pack gate 2 (reviewer-written; agents may not edit it): suspect cells in
the long profile, and Korean labels for the literature rows.

The 30 m local-inertial run leaves a few isolated cells in the channel whose maximum water
surface stands far above their neighbours (A26: unstable in steep, shallow flow). They are
NOT removed and NOT smoothed: ``max_depth.tif`` and ``long_profile`` stay as computed. The
figure only marks them, and the pack records how many.

Module ``climaterisk_worker.twin.case_pohang_figures``:
- ``flag_profile_spikes(surface_m, window, tol_m)`` -> list[bool], same length. Cell i is
  flagged when
  $$ s_i - \\mathrm{median}(s_{i-k..i+k}) > tol $$ with k = (window - 1) / 2, the window
  truncated at both ends (ASCII: series minus the centred running median exceeds tol_m).
  ``build_pack`` marks a cell only when BOTH the maximum surface and the maximum depth
  (max_surface - bed) are flagged: the surface alone marks steep bed steps, the depth alone
  marks pools behind bed steps.
  ``window`` must be odd and >= 3, ``tol_m`` > 0 (``ValueError`` otherwise).

Config ``assets/libraries/case_pohang_spec.json`` ``figures``: ``profile_spike_window`` (odd,
>= 3), ``profile_spike_tol_m`` (> 0).
Config ``assets/libraries/case_pohang_observations.json``: every observation has ``label_ko``.

Built output (skipped when absent): ``figures_pack.json`` ``profile_flags`` = {"n_cells",
"n_flagged", "window", "tol_m", "max_excess_m", "note_ko"} (window / tol_m equal the config;
note_ko says the cells are kept, only marked); ``summary.json`` ``literature_check.rows``
each carry ``label_ko`` (non-empty) and ``unit`` ("m" for depth_at_time, "시각" for
overtopped_by). ``fig_long_profile.png`` draws the flagged cells as their own legend entry.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
SPEC = json.loads((ROOT / "assets/libraries/case_pohang_spec.json").read_text(encoding="utf-8"))
OBS = json.loads(
    (ROOT / "assets/libraries/case_pohang_observations.json").read_text(encoding="utf-8")
)


def test_config() -> None:
    fg = SPEC["figures"]
    assert fg["profile_spike_window"] >= 3 and fg["profile_spike_window"] % 2 == 1
    assert fg["profile_spike_tol_m"] > 0
    assert all(o.get("label_ko") for o in OBS["observations"])


def test_flag_profile_spikes() -> None:
    f = pytest.importorskip("climaterisk_worker.twin.case_pohang_figures")
    assert f.flag_profile_spikes([1, 1, 1, 9, 1, 1, 1], 5, 2.0) == [
        False, False, False, True, False, False, False,
    ]  # fmt: skip
    # two adjacent suspect cells are both caught (median of 5 is still the base level)
    assert f.flag_profile_spikes([1, 1, 9, 9, 1, 1, 1], 5, 2.0) == [
        False, False, True, True, False, False, False,
    ]  # fmt: skip
    # a steady slope is not a spike, also at the truncated ends
    assert not any(f.flag_profile_spikes([float(i) for i in range(11)], 5, 2.0))
    # a dip is not flagged (only cells standing above their neighbours)
    assert not any(f.flag_profile_spikes([5, 5, 0, 5, 5], 3, 2.0))
    # exactly tol is not flagged
    assert not any(f.flag_profile_spikes([1, 1, 3, 1, 1], 3, 2.0))
    assert f.flag_profile_spikes([], 3, 1.0) == []
    for bad in ((4, 1.0), (1, 1.0), (3, 0.0)):
        with pytest.raises(ValueError):
            f.flag_profile_spikes([1, 2, 3], *bad)


CASE = Path(os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk")))
CASE = CASE / "derived/case_twins/pohang"


def test_built_flags_and_labels() -> None:
    pack_file = CASE / "figures_pack.json"
    if not pack_file.exists():
        pytest.skip("figure pack not built here")
    pack = json.loads(pack_file.read_text(encoding="utf-8"))
    if "profile_flags" not in pack and os.environ.get("CI"):
        pytest.skip("older pack")
    pf = pack["profile_flags"]
    assert 0 <= pf["n_flagged"] <= pf["n_cells"] and pf["n_cells"] > 0
    assert pf["window"] == SPEC["figures"]["profile_spike_window"]
    assert pf["tol_m"] == SPEC["figures"]["profile_spike_tol_m"]
    assert pf["note_ko"] and (pf["max_excess_m"] is None) == (pf["n_flagged"] == 0)
    rows = json.loads((CASE / "summary.json").read_text(encoding="utf-8"))["literature_check"][
        "rows"
    ]
    for r in rows:
        assert r["label_ko"]
        assert r["unit"] == ("m" if r["kind"] == "depth_at_time" else "시각")
