"""``docs/KOREA_1KM_IMPLEMENTATION_GAP.md`` must trace the code it describes, not paraphrase it.

The document derives "where to change to get a true 1 km calculation" from the resolution
chain KMA source → transform → stored grid → hazard object → exposure grid → ImpactCalc →
output. These tests re-read the constants that chain hinges on and fail if the document and
the code disagree — a changed ``coarsen`` default, TC centroid resolution, crop pad or preview
width would otherwise leave the audit describing a platform that no longer exists. CLIMADA-free.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker" / "climaterisk_worker"
if str(REPO / "worker") not in sys.path:
    sys.path.insert(0, str(REPO / "worker"))

DOC = REPO / "docs" / "KOREA_1KM_IMPLEMENTATION_GAP.md"

#: Perils that have a KOR catalog layer today; each needs a full chain section and a
#: Korea Physical Risk Standard card.
KOR_LAYER_PERILS = (
    "heat_mortality",
    "heatwave",
    "river_flood",
    "tropical_cyclone",
    "wildfire",
    "earthquake",
)

#: Fields of the Korea Physical Risk Standard card, in the order the user fixed them.
STANDARD_FIELDS = (
    "Hazard source",
    "Source resolution",
    "Stored resolution",
    "Calculation resolution",
    "Exposure resolution",
    "Impact function",
    "Scenario",
    "Temporal scale",
    "Validation source",
)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _const(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{path.name}: {pattern!r} not found — the audit's anchor moved"
    return match.group(1)


def test_every_kor_layer_peril_has_a_chain_section_and_a_standard_card() -> None:
    doc = _doc()
    for peril in KOR_LAYER_PERILS:
        assert f"`{peril}`" in doc, f"{peril} not covered"
    for field in STANDARD_FIELDS:
        assert field in doc, f"standard field {field!r} missing"
    # the standard card is the deliverable: one per KOR-layer peril at minimum
    assert doc.count("Korea Physical Risk Standard") >= 1
    assert doc.count("| Hazard source |") >= len(KOR_LAYER_PERILS)


def test_the_chain_constants_in_the_doc_match_the_code() -> None:
    doc = _doc()
    coarsen = _const(REPO / "scripts" / "heat_korea.py", r'"--coarsen", type=int, default=(\d+)')
    assert f"coarsen={coarsen}" in doc or f"`coarsen` 기본값 {coarsen}" in doc
    pad = _const(WORKER / "physical.py", r"^_CROP_PAD_DEG = ([\d.]+)")
    assert f"{float(pad):g}°" in doc  # crop pad around the portfolio
    tc_res = _const(WORKER / "ingest.py", r"Centroids\.from_pnt_bounds\(.*?, res=([\d.]+)\)")
    assert f"res={tc_res}" in doc  # TC/TCRain ingest centroid resolution
    px = _const(WORKER / "hazard_preview.py", r"^_GRID_PX = (\d+)")
    assert f"{px} px" in doc or f"{px}px" in doc  # preview re-grid is a rendering, not a calc
    dem = _const(WORKER / "ingest.py", r"^_DEM_MAX_PIXELS = (\d+)")
    assert f"{dem} px" in doc or f"{dem}px" in doc
    res_arcsec = _const(WORKER / "exposures.py", r"def build_exposure\(.*res_arcsec: int = (\d+)")
    assert f"res_arcsec={res_arcsec}" in doc


def test_the_three_resolutions_are_named_and_kept_apart() -> None:
    doc = _doc()
    for phrase in ("source resolution", "stored resolution", "calculation resolution"):
        assert phrase in doc.lower()
    assert "0.05°" in doc and "0.0417°" in doc
    # the wording rule: the platform may not be called "1 km 지원" for Korea today
    assert "1km-data-only" in doc and "5km-current" in doc
    # the phrase may appear only as a quotation of what must NOT be said, i.e. immediately
    # closed by a quote mark ("…지원한다"고 말할 때); anywhere else it is a claim
    for m in re.finditer(r"1 ?km (physical risk|물리위험)(을|를)? 지원한다", doc):
        closing = doc[m.end() : m.end() + 1]
        assert closing in ('"', "”"), f"the doc must not claim 1 km support: {m.group(0)!r}"


def test_the_manifest_has_no_resolution_field_and_the_doc_says_so() -> None:
    src = (WORKER / "hazard_convert.py").read_text(encoding="utf-8")
    block = src[src.index("def convert_grid_to_catalog") :]
    block = block[: block.index("\ndef ", 10) if "\ndef " in block[10:] else None]
    assert '"resolution' not in block and '"res_deg' not in block, (
        "hazard_convert now records a resolution — update the gap document"
    )
    assert "해상도 필드" in _doc() or "resolution field" in _doc().lower()


def test_the_heatwave_ramp_origin_is_traced_to_its_commit() -> None:
    """The ramp's intended input is a matter of record, not interpretation."""
    doc = _doc()
    assert "762ad5a" in doc and "ERA5-HEAT/UTCI" in doc
    assert "54f52f7" in doc  # the commit that fed daily-mean p95 into it
    # and git still agrees on which commit introduced the ramp
    out = subprocess.run(
        [
            "git",
            "log",
            "--format=%h",
            "-S",
            "[0.0, 30.0, 35.0, 40.0, 45.0]",
            "--",
            "worker/climaterisk_worker/physical.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode == 0 and out.stdout.strip():
        assert out.stdout.strip().splitlines()[-1].startswith("762ad5a")


def test_the_doc_does_not_prescribe_tamax_as_the_fix() -> None:
    """Per the review: settle the ramp's intended input first; wiring TAMAX now is arbitrary."""
    doc = _doc()
    assert "TAMAX" in doc
    assert not re.search(r"TAMAX(을|를) (연결|배선)한다\.", doc), (
        "the doc must present TAMAX vs redefinition as an open decision, not a prescription"
    )
