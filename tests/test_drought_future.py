"""A6 drought future gate (reviewer-written; agents may not edit it).

TerraClimate +2 °C / +4 °C series (pseudo-warming of the same 1981–2025 months) are
standardised with the PRESENT reference fit (1991–2020), so the future frequency of each
SPEI stage is comparable with the present one. Present frequencies are ~uniform by
construction; their CHANGE is the informative map.

Worker ``climaterisk_worker.drought_agri``:
- ``spei_against_reference(d_ref, d_target, months, scale, ref_mask, clip=None,
  distribution=None)``: fit per calendar month on the accumulated ``d_ref`` over
  ``ref_mask`` (exactly as ``spei``), evaluate the accumulated ``d_target`` with that fit.
  ``spei_against_reference(d, d, ...) == spei(d, ...)`` (same args, NaN-equal).
Config ``assets/libraries/drought_agri.json``: ``future`` block with
``scenarios`` ["plus2C", "plus4C"], ``source`` (non-empty), ``reference_years``
[1991, 2020] (same as the present fit) and ``check`` (log-logistic, as in the present).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
CFG = json.loads((ROOT / "assets/libraries/drought_agri.json").read_text(encoding="utf-8"))


def test_future_config() -> None:
    fut = CFG["future"]
    assert fut["scenarios"] == ["plus2C", "plus4C"] and fut["source"]
    assert fut["reference_years"] == [1991, 2020]
    assert fut["check"]["distribution"] == "loglogistic"


def _series() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    n_years = 40
    months = np.tile(np.arange(1, 13), n_years)
    d = rng.gamma(2.0, 40.0, size=(months.size, 3)) - 60.0
    ref = (np.arange(months.size) // 12 >= 5) & (np.arange(months.size) // 12 < 35)
    return d, months, ref


def test_identity_and_drier_shift() -> None:
    da = pytest.importorskip("climaterisk_worker.drought_agri")
    d, months, ref = _series()
    a = da.spei_against_reference(d, d, months, 3, ref)
    b = da.spei(d, months, 3, ref)
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-9, equal_nan=True)
    drier = da.spei_against_reference(d, d - 20.0, months, 3, ref)
    ok = np.isfinite(a) & np.isfinite(drier)
    assert ok.sum() > 100
    assert (drier[ok] <= a[ok] + 1e-12).all() and (drier[ok] < a[ok]).mean() > 0.9
