"""Highlight paths of the case pages: dict keys and list indexes, missing -> None (not 0)."""

from __future__ import annotations

import pytest

cases = pytest.importorskip("climaterisk.board.cases")


def test_dig_keys_and_indexes() -> None:
    s = {"a": {"b": [1.5, -30.7, 0.0]}, "n": 3}
    assert cases._dig(s, ["a", "b", 1]) == -30.7
    assert cases._dig(s, ["a", "b", 2]) == 0.0
    assert cases._dig(s, ["n"]) == 3
    assert cases._dig(s, ["a", "b", 5]) is None
    assert cases._dig(s, ["a", "x"]) is None
    assert cases._dig(s, ["n", 0]) is None
