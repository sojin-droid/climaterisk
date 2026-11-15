"""Gate for the 단면 sheet: schema validation and the read-only API (written before the code).

The validator is ``climaterisk.sheets.validate_sheet(dict) -> list[str]`` (empty = valid).
Routes: ``GET /api/sheets`` lists bundled + generated sheets; ``GET /api/sheets/{sheet_id}``
returns one. Bundled sheets live in ``assets/libraries/sheets``; generated ones in
``DATA_ROOT/derived/sheets`` (``climaterisk.sheets.sheet_dirs()`` returns both, bundled first).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from climaterisk.sheets import sheet_dirs, validate_sheet

REPO = Path(__file__).resolve().parents[1]
DM12 = json.loads((REPO / "assets" / "libraries" / "sheets" / "DM-12.json").read_text(encoding="utf-8"))


def test_bundled_example_is_valid() -> None:
    assert validate_sheet(DM12) == []


def test_bundled_dir_comes_first() -> None:
    dirs = sheet_dirs()
    assert dirs[0] == REPO / "assets" / "libraries" / "sheets"


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.update(schema="other/1"), "schema"),
        (lambda d: d.update(sheet_id="../etc"), "sheet_id"),
        (lambda d: d["section"]["z_ground_m"].pop(), "length"),
        (lambda d: d["section"]["km"].__setitem__(3, d["section"]["km"][2]), "increasing"),
        (lambda d: d["section"]["classes"]["rp100"].__setitem__(0, "N399"), "class"),
        (lambda d: d["site"].update(km=99.0), "site.km"),
        (lambda d: d["stamps"]["review"].update(state="maybe"), "review"),
        (lambda d: d["stamps"]["author"].update(state="T9"), "author"),
        (lambda d: d["site"]["classes"].update(rp100="N3"), "class"),
        (lambda d: d.pop("frequency"), "frequency"),
    ],
)
def test_invalid_sheets_are_named(mutate, needle: str) -> None:  # type: ignore[no-untyped-def]
    d = copy.deepcopy(DM12)
    mutate(d)
    errs = validate_sheet(d)
    assert errs and any(needle in e for e in errs), errs


def test_null_heights_are_allowed_and_kept() -> None:
    d = copy.deepcopy(DM12)
    d["section"]["z_ground_m"][5] = None
    d["site"]["check_depth_m"]["rp500"] = None
    assert validate_sheet(d) == []


def test_no_map_geometry_in_a_sheet() -> None:
    text = json.dumps(DM12)
    for word in ("coordinates", "Polygon", "geometry"):
        assert word not in text


def test_list_and_get_routes(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/sheets")
    assert r.status_code == 200
    ids = {s["sheet_id"]: s for s in r.json()}
    assert "DM-12" in ids and ids["DM-12"]["title"] == DM12["title"]
    assert ids["DM-12"]["hazard"] == "river_flood"
    r = client.get("/api/sheets/DM-12")
    assert r.status_code == 200 and r.json() == DM12


@pytest.mark.parametrize("bad", ["DM-99", "..%2Fetc", "dm-12", "DM-12.json"])
def test_unknown_or_unsafe_ids_are_404(client, bad: str) -> None:  # type: ignore[no-untyped-def]
    assert client.get(f"/api/sheets/{bad}").status_code == 404
