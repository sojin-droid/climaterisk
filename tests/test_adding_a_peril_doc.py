"""``docs/ADDING_A_PERIL.md`` must point at files that exist and at an invariant that holds.

The checklist is only trustworthy if (a) every repository path it names exists and (b) the
three peril registries it says must agree — the ``Peril`` enum, ``perils.json`` and the
worker's ``_RUNNERS`` — actually do. CLIMADA-free: the runner keys are read from source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from climaterisk.core.enums import Peril

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "ADDING_A_PERIL.md"
PERILS_JSON = REPO / "assets" / "libraries" / "perils.json"
PHYSICAL = REPO / "worker" / "climaterisk_worker" / "physical.py"


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


def test_the_three_peril_registries_agree() -> None:
    enum = {p.value for p in Peril}
    library = {p["id"] for p in json.loads(PERILS_JSON.read_text(encoding="utf-8"))["perils"]}
    runners = _runner_perils()
    assert enum == library, f"Peril enum vs perils.json: {enum ^ library}"
    assert library == runners, f"perils.json vs worker _RUNNERS: {library ^ runners}"


def test_every_repository_path_named_in_the_checklist_exists() -> None:
    """Backticked tokens that look like repo paths (contain a slash and an extension)."""
    tokens = set(re.findall(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_.-]+\.[a-z]+)`", _doc()))
    missing = sorted(t for t in tokens if not (REPO / t).exists())
    assert not missing, f"checklist names paths that do not exist: {missing}"


def test_every_symbol_the_checklist_relies_on_exists() -> None:
    """The entry points a contributor is told to edit, grepped in their files."""
    expectations = {
        "worker/climaterisk_worker/physical.py": (
            "_CATALOG_PERILS",
            "_RUNNERS",
            "_resolvable_return_periods",
            "def compute_physical_risk",
        ),
        "worker/climaterisk_worker/ingest.py": ("_REFINERS", "def run_ingest"),
        "worker/climaterisk_worker/hazard_convert.py": (
            "def grid_to_hazard",
            "def convert_grid_to_catalog",
        ),
        "src/climaterisk/api/routers/run.py": ("INGEST_SOURCES",),
        "src/climaterisk/engines/base.py": ("class AssetSpec", "def resolve_asset_specs"),
        "src/climaterisk/core/entities.py": ("class VulnerabilityOverride",),
        "src/climaterisk/physical_risk/models.py": ("HAZARD_KEYS", "HAZARD_TAGS", "READINESS"),
        "src/climaterisk/physical_risk/coverage.py": ("COVERAGE_KEYS", "EXTENDED_HAZARDS"),
        "src/climaterisk/physical_risk/display_copy.py": (
            "HAZARD_LABEL",
            "HAZARD_COPY",
            "def method_copy",
            "LIMITATIONS",
        ),
        "frontend/climaterisk/src/views/ResultsView.tsx": ("function perilProvenance",),
        "frontend/climaterisk/src/views/ModelsView.tsx": ("const HAZARD_KEYS",),
        "frontend/climaterisk/src/lib/format.ts": ("impactFormatter",),
        "tests/test_catalog_perils.py": ("_PERILS",),
    }
    for rel, symbols in expectations.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        for sym in symbols:
            assert sym in text, f"{rel}: checklist relies on {sym!r}, not found"


def test_the_catalog_ramp_tuple_has_the_documented_shape() -> None:
    """The checklist spells the eight-field tuple; the runner unpacks exactly eight."""
    src = PHYSICAL.read_text(encoding="utf-8")
    unpack = re.search(r"^\s*([a-z_, ]+) = _CATALOG_PERILS\[peril\]", src, re.MULTILINE)
    assert unpack, "runner no longer unpacks _CATALOG_PERILS[peril]"
    assert len([f for f in unpack.group(1).split(",") if f.strip()]) == 8
    assert "`(haz_type, intensity[], mdd_shape[], unit, scenario_key or None" in _doc()
