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


# --------------------------------------------------------------------------- #
# Part 2 — S7 / S1 / S4 / S2-S3 design and the 1km-ready definition            #
# --------------------------------------------------------------------------- #
import json  # noqa: E402

CATALOG_JSON = REPO / "data" / "hazard_db" / "catalog.json"
PERILS_JSON = REPO / "assets" / "libraries" / "perils.json"


def _kor_entries() -> list[dict]:  # type: ignore[type-arg]
    import pytest

    if not CATALOG_JSON.is_file():
        pytest.skip("local hazard catalog not present (gitignored)")
    data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    return [e for e in data.get("entries", []) if e.get("region") == "KOR"]


def _status_table(doc: str) -> dict[str, str]:
    """peril → status from the '페릴별 현재 상태' table (first cell → last cell)."""
    start = doc.index("### 페릴별 현재 상태")
    end = doc.index("\n## ", start)
    rows = {}
    for line in doc[start:end].splitlines():
        if line.startswith("| `"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            for name in re.findall(r"`([a-z_]+)`", cells[0]):
                rows[name] = cells[-1]
    return rows


def test_s7_documents_three_options_with_the_same_fields_and_chooses_none() -> None:
    doc = _doc()
    sec = doc[
        doc.index("## Heatwave intensity definition") : doc.index("## Resolution metadata design")
    ]
    for opt in ("Option A", "Option B", "Option C"):
        assert opt in sec
    for field in (
        "물리적 의미",
        "필요 KMA 변수",
        "시간 집계",
        "공간 해상도",
        "임팩트 함수 호환",
        "필요한 과학적 근거",
        "현재 구현",
        "다음 결정",
    ):
        assert sec.count(f"| {field} |") == 3, f"{field!r} must appear once per option"
    assert "어느\n안도 선택하지 않는다" in sec or "어느 안도 선택하지 않는다" in sec
    assert "762ad5a" in sec and "UTCI" in sec


def test_every_kor_layer_has_source_stored_and_calculation_resolution() -> None:
    doc = _doc()
    table = _status_table(doc)
    for e in _kor_entries():
        assert e["peril"] in table, f"{e['peril']} missing from the status table"
    start = doc.index("### 페릴별 현재 상태")
    header = doc[start : doc.index("\n|---", start)]
    for col in ("source", "stored", "calculation", "exposure", "metadata"):
        assert col in header


def test_requested_vs_served_scenario_is_represented_explicitly() -> None:
    doc = _doc()
    assert "`requested_scenario`" in doc and "`served_scenario`" in doc
    assert "requested_scenario = rcp45" in doc and "served_scenario = rcp60" in doc
    for e in _kor_entries():
        served = re.findall(r"rcp\d\d|SSP\d{3}|historical|observed", str(e.get("source", "")))
        if served and served[0] != e["climate_scenario"] and served[0].startswith("rcp"):
            assert f"served_scenario = {served[0]}" in doc, e["file"]


def test_heatwave_label_cannot_claim_tmax_when_the_array_is_ta() -> None:
    doc = _doc()
    assert "18.7–32.6" in doc and "일평균 TA" in doc and "daily Tmax" in doc
    for e in _kor_entries():
        if e["peril"] == "heatwave":
            src = str(e.get("source", ""))
            assert "TA" in src and "Tmax" in src, (
                "the on-disk label/variable discrepancy vanished — update the doc"
            )
    # the doc must never assert the heatwave layer IS daily max
    assert not re.search(r"heatwave 레이어(는|가) (일최고|daily Tmax)(기온)?(이다|다)\.", doc)


def test_one_km_ready_requires_exposure_and_metadata_and_nothing_is_ready() -> None:
    doc = _doc()
    sec = doc[doc.index("## 1km-ready 정의") :]
    for cond in (
        "hazard source/grid",
        "stored grid",
        "calculation grid",
        "exposure matched",
        "metadata records",
    ):
        assert cond in sec, cond
    assert "**1km-ready: 0.**" in sec
    assert not any(v.startswith("**1km-ready") for v in _status_table(doc).values())


def test_tc_0_1_deg_is_not_counted_as_1km() -> None:
    doc = _doc()
    assert "res=0.1" in doc
    assert "TC = 1 km" in doc  # quoted as the thing not to say
    assert _status_table(doc)["tropical_cyclone"].startswith("**missing")


def test_unimplemented_perils_are_never_marked_current_or_ready() -> None:
    doc = _doc()
    table = _status_table(doc)
    data = json.loads(PERILS_JSON.read_text(encoding="utf-8"))
    items = data["perils"] if isinstance(data, dict) and "perils" in data else data
    items = items if isinstance(items, list) else list(items.values())
    assert all(p.get("supported_mvp") for p in items)  # the flag means selectable, not implemented
    assert "`supported_mvp`는 한국 구현이 아니다" in doc
    kor_perils = {e["peril"] for e in _kor_entries()}
    for peril, status in table.items():
        if peril not in kor_perils:
            assert not (status.startswith("**5km") or status.startswith("**1km-ready")), (
                peril,
                status,
            )
