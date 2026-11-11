"""Board case-twin pages gate (reviewer-written; agents may not edit it).

The board shows computed case twins read-only. A twin is built elsewhere (twin branch) and
leaves ``<DATA_ROOT>/derived/case_twins/<id>/summary.json`` plus figures. The board never
recomputes, never shows money and never puts twin results on the national map.

Config ``assets/libraries/board_cases.json``: ``{"cases": [...]}``; each case has
``id`` (``^[a-z_]+$``), ``name``, ``peril``, ``lon``/``lat`` (inside Korea), ``cell_m`` (> 0),
``figures`` = ``[{"file", "caption_ko"}]`` (plain ``*.png`` names, no path separators),
non-empty ``can_say``, ``cannot_say``, ``next_data`` (lists of strings) and ``source_doc``.
Ids include ``pohang`` and ``yeongdeungpo``.

Module ``src/climaterisk/board/cases.py``:
- ``case_dir(id)`` -> ``paths.derived_case_twins_dir(id)`` (looked up at call time;
  ``paths.derived_case_twins_dir(*parts)`` = ``<DATA_ROOT>/derived/case_twins[/…]``).
- ``list_cases()`` -> per config case ``{"id", "name", "peril", "lon", "lat", "cell_m",
  "badge", "available"}``; ``available`` = summary.json exists; ``badge`` is the summary's
  badge when available, else "미검증" (never anything better than the summary says).
- ``load_case(id)`` -> config fields + ``summary`` (the parsed file) + ``badge`` +
  ``assumptions`` (the summary's ``assumptions`` list, [] if absent) + ``n_assumptions``
  (the summary's count) + ``assumptions_complete`` (len(assumptions) == n_assumptions).
  Keys anywhere in the summary containing ``krw``, ``usd`` or ``loss`` (case-insensitive) are
  dropped before returning. ``KeyError`` for an unknown id, ``FileNotFoundError`` when the
  case is configured but not built.
- ``figure_path(id, file)`` -> Path inside ``case_dir(id)``, only for a file listed in the
  config (``KeyError`` otherwise, also for ``..`` tricks); ``FileNotFoundError`` if missing.

Router (``/api/board``): ``GET /cases``, ``GET /cases/{id}`` (404 unknown or not built),
``GET /cases/{id}/figures/{file}`` (image/png; 404 unlisted or missing).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "assets/libraries/board_cases.json").read_text(encoding="utf-8"))

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def test_config() -> None:
    ids = [c["id"] for c in CFG["cases"]]
    assert {"pohang", "yeongdeungpo"} <= set(ids) and len(ids) == len(set(ids))
    for c in CFG["cases"]:
        assert re.fullmatch(r"[a-z_]+", c["id"])
        assert c["name"] and c["peril"] and c["source_doc"] and c["cell_m"] > 0
        assert 124 < c["lon"] < 132 and 33 < c["lat"] < 39
        assert c["figures"] and all(
            re.fullmatch(r"[A-Za-z0-9_.-]+\.png", f["file"]) and f["caption_ko"]
            for f in c["figures"]
        )
        for k in ("can_say", "cannot_say", "next_data"):
            assert c[k] and all(isinstance(s, str) and s for s in c[k])


@pytest.fixture
def cases_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cases = pytest.importorskip("climaterisk.board.cases")
    root = tmp_path / "case_twins"
    monkeypatch.setattr(cases, "case_dir", lambda cid: root / cid)
    pohang = next(c for c in CFG["cases"] if c["id"] == "pohang")
    d = root / "pohang"
    d.mkdir(parents=True)
    summary = {
        "badge": "미검증",
        "n_assumptions": 3,
        "assumptions": ["a", "b"],
        "peak": 1.0,
        "nested": {"loss_krw": 5, "Damage_USD": 1, "depth_m": 2.0},
        "expected_loss": 9,
    }
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (d / pohang["figures"][0]["file"]).write_bytes(PNG)
    (root / "secret.png").write_bytes(PNG)
    return cases, pohang


def test_cases_module(cases_root) -> None:
    cases, pohang = cases_root
    listed = {c["id"]: c for c in cases.list_cases()}
    assert listed["pohang"]["available"] is True and listed["pohang"]["badge"] == "미검증"
    assert listed["yeongdeungpo"]["available"] is False
    assert listed["yeongdeungpo"]["badge"] == "미검증"
    c = cases.load_case("pohang")
    assert c["assumptions"] == ["a", "b"] and c["n_assumptions"] == 3
    assert c["assumptions_complete"] is False
    flat = json.dumps(c).lower()
    assert "krw" not in flat and "usd" not in flat and "loss" not in flat
    assert c["summary"]["nested"] == {"depth_m": 2.0} and c["summary"]["peak"] == 1.0
    with pytest.raises(KeyError):
        cases.load_case("nope")
    with pytest.raises(FileNotFoundError):
        cases.load_case("yeongdeungpo")
    p = cases.figure_path("pohang", pohang["figures"][0]["file"])
    assert p.read_bytes() == PNG
    for bad in ("../secret.png", "summary.json", "other.png"):
        with pytest.raises(KeyError):
            cases.figure_path("pohang", bad)


def test_cases_routes(cases_root, client: TestClient) -> None:
    _, pohang = cases_root
    r = client.get("/api/board/cases")
    assert r.status_code == 200 and {c["id"] for c in r.json()} >= {"pohang", "yeongdeungpo"}
    one = client.get("/api/board/cases/pohang")
    assert one.status_code == 200 and one.json()["badge"] == "미검증"
    assert client.get("/api/board/cases/yeongdeungpo").status_code == 404
    assert client.get("/api/board/cases/nope").status_code == 404
    fig = client.get(f"/api/board/cases/pohang/figures/{pohang['figures'][0]['file']}")
    assert fig.status_code == 200 and fig.headers["content-type"] == "image/png"
    assert client.get("/api/board/cases/pohang/figures/secret.png").status_code == 404
    assert client.get("/api/board/cases/pohang/figures/summary.json").status_code == 404
