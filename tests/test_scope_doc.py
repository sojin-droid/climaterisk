"""``docs/SCOPE.md`` (use-tier declaration, GAP G8) must not drift from the code.

The declaration is only honest if every peril the worker can run has a tier row, every tier
is one of S / I / D, the summary agrees with the table, and the gap / register ids the rows
cite exist. CLIMADA-free: the peril list is read from the runner source, as in the other
doc tests.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "SCOPE.md"
PHYSICAL = REPO / "worker" / "climaterisk_worker" / "physical.py"
GAP = REPO / "docs" / "GAP_ANALYSIS_KO.md"
REGISTER = REPO / "docs" / "RISK_REGISTER.md"

TIERS = ("S", "I", "D")


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _runner_perils() -> set[str]:
    src = PHYSICAL.read_text(encoding="utf-8")
    block = src[src.index("_RUNNERS = {") :]
    block = block[: block.index("\n}\n")]
    explicit = set(re.findall(r'^\s*"([a-z_]+)":', block, re.MULTILINE))
    cat = src[src.index("_CATALOG_PERILS: dict[str, Any] = {") :]
    cat = cat[: cat.index("\n}\n")]
    return explicit | set(re.findall(r'^\s*"([a-z_]+)": \($', cat, re.MULTILINE))


def _tier_rows() -> dict[str, str]:
    """``{peril_id: tier cell}`` from the §3 table (rows start with a backticked id)."""
    section = _doc().split("## 3. ")[1].split("### 3.1")[0]
    rows = re.findall(r"^\| `([a-z_]+)` \| [^|]+ \| ([^|]+) \|", section, re.MULTILINE)
    return {pid: cell.strip() for pid, cell in rows}


def test_every_runnable_peril_has_a_tier_row() -> None:
    rows = _tier_rows()
    missing = sorted(_runner_perils() - set(rows))
    extra = sorted(set(rows) - _runner_perils())
    assert not missing, f"perils without a use-tier row: {missing}"
    assert not extra, f"tier rows for perils the worker cannot run: {extra}"


def test_every_tier_cell_names_only_known_tiers() -> None:
    for pid, cell in _tier_rows().items():
        tiers = re.findall(r"\*\*([A-Z])\*\*", cell)
        assert tiers, f"{pid}: tier cell has no bold tier letter: {cell!r}"
        assert set(tiers) <= set(TIERS), f"{pid}: unknown tier in {cell!r}"


def test_no_peril_is_decision_grade_until_a_domestic_validation_series_exists() -> None:
    """RISK_REGISTER C5 still records no observed series; tier D would contradict it."""
    register = REGISTER.read_text(encoding="utf-8")
    assert "관측 계열은 아직 없음" in register
    graded_d = sorted(pid for pid, cell in _tier_rows().items() if "**D**" in cell)
    assert not graded_d, f"tier D declared without a validation series: {graded_d}"


def test_summary_agrees_with_the_table() -> None:
    doc = _doc()
    # Cut at the next horizontal rule ("---" on its own line), not at a table divider.
    summary = re.split(r"^---$", doc.split("### 3.1")[1], maxsplit=1, flags=re.MULTILINE)[0]
    rows = _tier_rows()
    for tier in TIERS:
        line = next(ln for ln in summary.splitlines() if ln.startswith(f"| **{tier}** |"))
        listed = set(re.findall(r"`([a-z_]+)`", line))
        from_table = {pid for pid, cell in rows.items() if f"**{tier}**" in cell}
        assert listed == from_table, f"tier {tier}: summary {listed} vs table {from_table}"
    assert "| **D** | 없음 |" in summary


def test_cited_gap_and_register_ids_exist() -> None:
    doc = _doc()
    gap = GAP.read_text(encoding="utf-8")
    register = REGISTER.read_text(encoding="utf-8")
    for gid in sorted(set(re.findall(r"\bG([1-8])\b", doc))):
        assert f"### G{gid}." in gap, f"G{gid} cited but not a GAP_ANALYSIS_KO section"
    for rid in sorted(set(re.findall(r"\b([ABC]\d{1,2})\b", doc))):
        assert f"| {rid} " in register, f"{rid} cited but not a RISK_REGISTER row"


def test_the_gap_document_points_at_this_declaration() -> None:
    gap = GAP.read_text(encoding="utf-8")
    g8 = gap.split("### G8.")[1].split("### ")[0]
    assert "SCOPE.md" in g8 and "2026-10-12" in g8
