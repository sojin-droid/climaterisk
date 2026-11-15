"""Gangnam case card: the ratio helper (analytical) and the written summary (money-free)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
card = pytest.importorskip("case_gangnam_card")

SUMMARY = Path.home() / "Data/climaterisk/derived/case_twins/gangnam/summary.json"


def test_eal_ratio_analytical() -> None:
    ratio = card.eal_ratio([5.0, 0.0, 3.0, 1.0], [10.0, 4.0, 0.0, 1.0])
    np.testing.assert_allclose(ratio[:2], [0.5, 0.0], rtol=1e-12)
    assert np.isnan(ratio[2])  # base 0 -> undefined
    np.testing.assert_allclose(ratio[3], 1.0)


def test_site_change_pct_is_sum_ratio_minus_one() -> None:
    # sums 6 / 10 -> -40 %; unit of EAL cancels
    np.testing.assert_allclose(card.site_change_pct([1.0, 5.0], [4.0, 6.0]), -40.0, rtol=1e-12)


def test_class_index_follows_the_palette_break_rule() -> None:
    breaks = [0.5, 0.667, 0.909, 1.1, 1.5, 2.0]
    assert card.class_index(0.5, breaks) == 0  # v <= breaks[0]
    assert card.class_index(0.51, breaks) == 1
    assert card.class_index(1.0, breaks) == 3
    assert card.class_index(5.0, breaks) == 6


@pytest.mark.skipif(not SUMMARY.exists(), reason="card not produced")
def test_summary_has_no_money_and_consistent_assumptions() -> None:
    text = SUMMARY.read_text(encoding="utf-8")
    data = json.loads(text)

    def keys(o: object) -> list[str]:
        if isinstance(o, dict):
            return [k for k, v in o.items()] + [k for v in o.values() for k in keys(v)]
        if isinstance(o, list):
            return [k for v in o for k in keys(v)]
        return []

    for k in keys(data):
        assert not any(w in k.lower() for w in ("usd", "krw", "loss")), k
    assert data["n_assumptions"] == len(data["assumptions"])
    assert all(a.endswith("]") for a in data["assumptions"])
    assert data["n_evaluated"] == 243 and data["n_excluded"] == 637
    assert data["badge"] == "미검증"
