"""``docs/KOREA_1KM_PHYSICAL_RISK_COVERAGE.md`` must not drift from the code it audits.

The audit is only worth reading if every peril the platform can run appears in it, if the
KMA variables it lists are the real ones, and if its central claim — that only ``TA`` is
consumed — is still true of the code. These tests re-derive those facts from the registry,
the runner table and a source grep, and compare. CLIMADA-free.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

DOC = REPO / "docs" / "KOREA_1KM_PHYSICAL_RISK_COVERAGE.md"
PERILS_JSON = REPO / "assets" / "libraries" / "perils.json"
PHYSICAL = REPO / "worker" / "climaterisk_worker" / "physical.py"

#: The seven 기후요소 the 남한상세 grid offers (RISK_REGISTER E절 / KMA 활용매뉴얼 v5.1).
KMA_VARIABLES = ("TA", "TAMAX", "TAMIN", "RN", "RHM", "WS", "SI")


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _registered_perils() -> set[str]:
    data = json.loads(PERILS_JSON.read_text(encoding="utf-8"))
    items = data["perils"] if isinstance(data, dict) and "perils" in data else data
    items = items if isinstance(items, list) else list(items.values())
    return {p["id"] for p in items}


def _runner_perils() -> set[str]:
    """Keys of ``physical._RUNNERS``, read from source so no CLIMADA import is needed."""
    src = PHYSICAL.read_text(encoding="utf-8")
    block = src[src.index("_RUNNERS = {") :]
    block = block[: block.index("\n}\n")]
    explicit = set(re.findall(r'^\s*"([a-z_]+)":', block, re.MULTILINE))
    cat = src[src.index("_CATALOG_PERILS: dict[str, Any] = {") :]
    cat = cat[: cat.index("\n}\n")]
    return explicit | set(re.findall(r'^\s*"([a-z_]+)": \($', cat, re.MULTILINE))


def test_every_registered_and_runnable_peril_is_audited() -> None:
    doc = _doc()
    missing = sorted(p for p in _registered_perils() | _runner_perils() if f"`{p}`" not in doc)
    assert not missing, f"perils absent from the coverage audit: {missing}"


def test_the_seven_kma_variables_are_each_listed() -> None:
    doc = _doc()
    for var in KMA_VARIABLES:
        assert re.search(rf"\b{var}\b", doc), f"KMA variable {var} not listed"


def test_the_only_consumed_kma_variable_is_still_ta() -> None:
    """The audit's central claim, re-derived: no code path reads any KMA variable but TA."""
    consumed: set[str] = set()
    for path in list((WORKER / "climaterisk_worker").glob("*.py")) + list(
        (REPO / "scripts").glob("*.py")
    ):
        text = path.read_text(encoding="utf-8")
        # strip comments and docstrings crudely: only look at quoted tokens in code lines
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            for var in KMA_VARIABLES:
                if re.search(rf'["\']{var}["\']', code):
                    consumed.add(var)
    # "SI" in build_impf_presets.py is the South-Indian TC basin code, not the KMA variable.
    consumed.discard("SI")
    assert consumed == {"TA"}, (
        f"code now references KMA variables {sorted(consumed)}; update the audit"
    )
    assert "소비하는 것은 `TA` 하나" in _doc()


def test_the_audit_names_its_grades_and_declares_no_korean_validation() -> None:
    doc = _doc()
    for grade in ("Native", "Custom", "Partial", "Missing", "Not validated"):
        assert grade in doc
    # the honest headline: zero perils validated against Korean observed losses/mortality
    assert re.search(r"한국 실측\(손실·사망\) 검증 완료 페릴\*\* \| \*\*0\*\*", doc)


def test_the_audit_records_the_label_inconsistencies_it_found() -> None:
    doc = _doc()
    assert "season p95 daily Tmax" in doc  # heatwave layer label vs TA input
    assert "rcp60" in doc  # river_flood KOR layer filed as rcp45
    assert "소비자 없이" in doc or "소비자 0건" in doc  # 홍수위험지도 fetched, never ingested
