"""``docs/TIERS.md`` (method tiers T1/T2/T3) must not drift from ``physical_risk.tiers``.

Checks: every runnable peril has a row in both the doc and ``PERIL_TIERS``; the doc's
current/target equal the code; current ≤ target; the summary agrees with the table; input
statuses are known; and ``resolve_tier`` falls back with a reason when inputs are missing.
CLIMADA-free: the peril list is read from the runner source, as in ``test_scope_doc``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from climaterisk.physical_risk.tiers import (
    KOREA_SOURCED,
    PERIL_TIERS,
    STATUSES,
    MethodTier,
    method_tier_for,
    missing_inputs,
    resolve_tier,
)

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "TIERS.md"
PHYSICAL = REPO / "worker" / "climaterisk_worker" / "physical.py"
ORDER = ["T1", "T2", "T3"]


def _runner_perils() -> set[str]:
    src = PHYSICAL.read_text(encoding="utf-8")
    block = src[src.index("_RUNNERS = {") :]
    block = block[: block.index("\n}\n")]
    explicit = set(re.findall(r'^\s*"([a-z_]+)":', block, re.MULTILINE))
    cat = src[src.index("_CATALOG_PERILS: dict[str, Any] = {") :]
    cat = cat[: cat.index("\n}\n")]
    return explicit | set(re.findall(r'^\s*"([a-z_]+)": \($', cat, re.MULTILINE))


def _doc_rows() -> dict[str, tuple[str, str]]:
    """``{peril: (current, target)}`` from the §3 table."""
    section = DOC.read_text(encoding="utf-8").split("## 3. ")[1].split("### 3.1")[0]
    rows = re.findall(
        r"^\| `([a-z_]+)` \| \*\*(T[123])\*\* \| \*\*(T[123])\*\* \|", section, re.MULTILINE
    )
    return {pid: (cur, tgt) for pid, cur, tgt in rows}


def test_every_runnable_peril_has_a_tier_row_in_doc_and_code() -> None:
    perils = _runner_perils()
    assert set(_doc_rows()) == perils, "TIERS.md §3 rows differ from the worker's runners"
    assert set(PERIL_TIERS) == perils, "PERIL_TIERS differs from the worker's runners"


def test_doc_matches_code() -> None:
    for pid, (cur, tgt) in _doc_rows().items():
        entry = PERIL_TIERS[pid]
        assert (entry.current.value, entry.target.value) == (cur, tgt), pid


def test_current_never_exceeds_target() -> None:
    for pid, entry in PERIL_TIERS.items():
        assert ORDER.index(entry.current) <= ORDER.index(entry.target), pid


def test_summary_agrees_with_the_table() -> None:
    doc = DOC.read_text(encoding="utf-8")
    summary = re.split(r"^---$", doc.split("### 3.1")[1], maxsplit=1, flags=re.MULTILINE)[0]
    for tier in ("T3", "T2"):
        line = next(ln for ln in summary.splitlines() if ln.startswith(f"| **{tier}** |"))
        current_cell, target_cell = line.split("|")[2], line.split("|")[3]
        cur = {p for p, e in PERIL_TIERS.items() if e.current == tier}
        tgt = {p for p, e in PERIL_TIERS.items() if e.target == tier}
        assert set(re.findall(r"`([a-z_]+)`", current_cell)) == cur, f"{tier} current"
        assert set(re.findall(r"`([a-z_]+)`", target_cell)) == tgt, f"{tier} target"
    n_t1 = sum(e.current == MethodTier.T1 for e in PERIL_TIERS.values())
    assert f"나머지 {n_t1}개" in summary


def test_inputs_have_known_status_and_t1_needs_none() -> None:
    for pid, entry in PERIL_TIERS.items():
        assert MethodTier.T1 not in entry.inputs, f"{pid}: T1 must need no extra input"
        for tier, inputs in entry.inputs.items():
            assert ORDER.index(tier) <= ORDER.index(entry.target), f"{pid}: inputs above target"
            for i in inputs:
                assert i.status in STATUSES, f"{pid} {tier}: {i.key} status {i.status!r}"


def test_target_above_t1_lists_its_inputs() -> None:
    for pid, entry in PERIL_TIERS.items():
        if entry.target != MethodTier.T1:
            assert entry.inputs.get(entry.target), f"{pid}: target {entry.target} has no inputs"


def test_resolve_tier_reaches_requested_when_inputs_present() -> None:
    keys = {i.key for i in PERIL_TIERS["coastal_flood"].inputs[MethodTier.T2]}
    assert resolve_tier("coastal_flood", MethodTier.T2, keys) == (MethodTier.T2, None)


def test_resolve_tier_falls_back_and_names_missing_inputs() -> None:
    tier, reason = resolve_tier("coastal_flood", MethodTier.T3, {"khoa_harmonics", "dem_glo30"})
    assert tier == MethodTier.T2
    assert reason and "방조제" in reason and "T3 needs" in reason


def test_resolve_tier_falls_to_t1_with_nothing_available() -> None:
    tier, reason = resolve_tier("river_flood", MethodTier.T3, set())
    assert tier == MethodTier.T1
    assert "T3 needs" in reason and "T2 needs" in reason


def test_resolve_tier_caps_at_target() -> None:
    tier, reason = resolve_tier("wildfire", MethodTier.T3, set())
    assert tier == MethodTier.T1 and "above the target" in reason


def test_missing_inputs_lists_labels() -> None:
    assert missing_inputs("river_flood", MethodTier.T2, set()) == [
        PERIL_TIERS["river_flood"].inputs[MethodTier.T2][0].label
    ]


@pytest.mark.parametrize("peril", sorted(KOREA_SOURCED))
def test_korea_sourced_perils_are_t1_outside_korea(peril: str) -> None:
    assert method_tier_for(peril, "KOR") == "T2"
    assert method_tier_for(peril, "DEU") == "T1"
    assert method_tier_for(peril, None) == "T1"


def test_unknown_peril_has_no_tier() -> None:
    assert method_tier_for("not_a_peril") is None
