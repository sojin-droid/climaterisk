"""단면 asset sheets (``danmyeon.sheet/1``): validation and read-only lookup.

Stdlib only, so the CLIMADA worker (no pydantic) can import it. The format is documented in
``docs/SHEET_SCHEMA.md``. A sheet holds class codes and heights only, never map polygons.
Bundled sheets live in ``assets/libraries/sheets``; generated ones in
``<DATA_ROOT>/derived/sheets``. When an id exists in both, the bundled one wins.
"""

from __future__ import annotations

import json
import os
import re
from itertools import pairwise
from pathlib import Path
from typing import Any

from climaterisk import paths

SCHEMA = "danmyeon.sheet/1"
HAZARDS = ("river_flood",)
SHEET_ID_RE = re.compile(r"[A-Z]{2}-[0-9A-Za-z_-]{1,40}")
CLASS_CODES = frozenset({"N330", "N331", "N332", "N333", "N334", ""})
RP_KEYS = ("rp100", "rp200", "rp500")
#: Why a site class is ``null``: no map file for that period, or a file that cannot be read.
SITE_REASONS = ("no_file_for_rp", "map_unreadable")
RETURN_PERIODS = [100, 200, 500]
STAMP_STATES = {
    "author": ("T1", "T2", "T3"),
    "review": ("agree", "partial", "disagree", "empty"),
    "approve": ("approved", "empty"),
}
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num_or_none(v: Any) -> bool:
    return v is None or _num(v)


def _check_rp_map(errs: list[str], where: str, m: Any, check: Any, what: str) -> None:
    if not isinstance(m, dict):
        errs.append(f"{where}: must be an object with {RP_KEYS}")
        return
    for k in RP_KEYS:
        if k not in m:
            errs.append(f"{where}.{k}: missing")
        elif not check(m[k]):
            errs.append(f"{where}.{k}: invalid {what}")


def _is_class(v: Any) -> bool:
    """A class code, ``""`` (outside every polygon) or ``None`` (no map file: unknown)."""
    return v is None or (isinstance(v, str) and v in CLASS_CODES)


def validate_sheet(d: dict[str, Any]) -> list[str]:
    """Check a sheet against ``docs/SHEET_SCHEMA.md``.

    Args:
        d: Parsed sheet JSON.

    Returns:
        Error messages; empty when the sheet is valid.
    """
    errs: list[str] = []
    if not isinstance(d, dict):
        return ["schema: sheet must be an object"]
    if d.get("schema") != SCHEMA:
        errs.append(f"schema: expected {SCHEMA!r}")
    sid = d.get("sheet_id")
    if not isinstance(sid, str) or not SHEET_ID_RE.fullmatch(sid):
        errs.append("sheet_id: must match ^[A-Z]{2}-[0-9A-Za-z_-]{1,40}$")
    if d.get("hazard") not in HAZARDS:
        errs.append(f"hazard: must be one of {HAZARDS}")
    for k in ("title", "place", "use", "date"):
        if not isinstance(d.get(k), str):
            errs.append(f"{k}: must be a string")
    src = d.get("sources")
    if not isinstance(src, list) or not all(isinstance(s, str) for s in src):
        errs.append("sources: must be a list of strings")

    # --- section ---------------------------------------------------------- #
    sec = d.get("section")
    length_km: float | None = None
    if not isinstance(sec, dict):
        errs.append("section: missing or not an object")
    else:
        cut = sec.get("cut")
        if not isinstance(cut, dict) or not all(
            _num(cut.get(k)) for k in ("lon0", "lon1", "lat", "bearing_deg", "length_km")
        ):
            errs.append("section.cut: needs numeric lon0, lon1, lat, bearing_deg, length_km")
        else:
            length_km = float(cut["length_km"])
        km = sec.get("km")
        n = -1
        if not isinstance(km, list) or not km or not all(_num(v) for v in km):
            errs.append("section.km: must be a non-empty list of numbers")
        else:
            n = len(km)
            if any(b <= a for a, b in pairwise(km)):
                errs.append("section.km: must be strictly increasing")
        for k in ("z_ground_m", "z_surface_m"):
            z = sec.get(k)
            if not isinstance(z, list) or not all(_num_or_none(v) for v in z):
                errs.append(f"section.{k}: must be a list of numbers or null")
            elif n >= 0 and len(z) != n:
                errs.append(f"section.{k}: length {len(z)} != section.km length {n}")
        for k in ("ground_source", "surface_source"):
            if not isinstance(sec.get(k), str):
                errs.append(f"section.{k}: must be a string")
        cl = sec.get("classes")
        if not isinstance(cl, dict):
            errs.append("section.classes: must be an object")
        else:
            for k in RP_KEYS:
                row = cl.get(k)
                if not isinstance(row, list):
                    errs.append(f"section.classes.{k}: must be a list")
                    continue
                if n >= 0 and len(row) != n:
                    errs.append(f"section.classes.{k}: length {len(row)} != section.km length {n}")
                if not all(_is_class(v) for v in row):
                    errs.append(f"section.classes.{k}: invalid class code (N330-N334 or '')")
        if not _num(sec.get("vertical_exaggeration")):
            errs.append("section.vertical_exaggeration: must be a number")

    # --- site ------------------------------------------------------------- #
    site = d.get("site")
    if not isinstance(site, dict):
        errs.append("site: missing or not an object")
    else:
        for k in ("lat", "lon"):
            if not _num(site.get(k)):
                errs.append(f"site.{k}: must be a number")
        skm: Any = site.get("km")
        if not _num(skm):
            errs.append("site.km: must be a number")
        elif skm < 0 or (length_km is not None and skm > length_km):
            errs.append("site.km: must satisfy 0 <= km <= section.cut.length_km")
        for k in ("z_ground_m", "z_surface_m"):
            if not _num_or_none(site.get(k)):
                errs.append(f"site.{k}: must be a number or null")
        _check_rp_map(errs, "site.classes", site.get("classes"), _is_class, "class code")

        def _band(v: Any) -> bool:
            if v is None:  # unknown: no map file for this return period
                return True
            return isinstance(v, list) and len(v) == 2 and _num(v[0]) and _num_or_none(v[1])

        _check_rp_map(
            errs, "site.bands_m", site.get("bands_m"), _band, "band [lo, hi|null] or null"
        )
        _check_rp_map(errs, "site.depth_m", site.get("depth_m"), _num_or_none, "depth")
        if "reasons" in site:  # optional: sheets built before 2026-11-16 do not carry it
            _check_rp_map(
                errs,
                "site.reasons",
                site.get("reasons"),
                lambda v: v is None or v in SITE_REASONS,
                f"reason (null or one of {SITE_REASONS})",
            )
        _check_rp_map(errs, "site.check_depth_m", site.get("check_depth_m"), _num_or_none, "depth")
        if not isinstance(site.get("check_dem"), str):
            errs.append("site.check_dem: must be a string")

    # --- frequency -------------------------------------------------------- #
    fr = d.get("frequency")
    if not isinstance(fr, dict):
        errs.append("frequency: missing or not an object")
    else:
        if fr.get("return_periods") != RETURN_PERIODS:
            errs.append(f"frequency.return_periods: must be {RETURN_PERIODS}")
        off = fr.get("official")
        if (
            not isinstance(off, dict)
            or off.get("tier") != "T2"
            or not isinstance(off.get("classes"), list)
            or not all(_is_class(c) for c in off["classes"])
        ):
            errs.append("frequency.official: needs tier 'T2' and a list of class codes")
        gl = fr.get("global")
        if (
            not isinstance(gl, dict)
            or gl.get("tier") != "T1"
            or not isinstance(gl.get("source"), str)
        ):
            errs.append("frequency.global: needs tier 'T1' and a source string")
        else:
            for per, v in gl.items():
                if per in ("tier", "source"):
                    continue
                if not (
                    isinstance(v, dict)
                    and isinstance(v.get("rp"), list)
                    and isinstance(v.get("depth_m"), list)
                    and len(v["rp"]) == len(v["depth_m"])
                    and all(_num(x) for x in v["rp"] + v["depth_m"])
                ):
                    errs.append(
                        f"frequency.global.{per}: needs rp and depth_m lists of equal length"
                    )

    # --- stamps ----------------------------------------------------------- #
    st = d.get("stamps")
    if not isinstance(st, dict):
        errs.append("stamps: missing or not an object")
    else:
        for box, allowed in STAMP_STATES.items():
            s = st.get(box)
            if not isinstance(s, dict) or s.get("state") not in allowed:
                errs.append(f"stamps.{box}: state must be one of {allowed}")
                continue
            if not isinstance(s.get("label", ""), str):
                errs.append(f"stamps.{box}: label must be a string")
            if box == "review" and s["state"] in ("agree", "partial", "disagree"):
                a, of = s.get("agree"), s.get("of")
                if not (isinstance(a, int) and isinstance(of, int) and not isinstance(a, bool)):
                    errs.append("stamps.review: agree and of must be integers")
    return errs


# --------------------------------------------------------------------------- #
# Lookup                                                                       #
# --------------------------------------------------------------------------- #
def sheet_dirs() -> list[Path]:
    """Folders searched for sheets: bundled first, then generated.

    ``CLIMATERISK_LIBRARY_DIR`` relocates the bundled libraries, as for the other loaders.
    """
    override = os.environ.get("CLIMATERISK_LIBRARY_DIR")
    base = Path(override) if override else _REPO_ROOT / "assets" / "libraries"
    if not base.is_absolute():
        base = _REPO_ROOT / base
    return [base / "sheets", paths.derived_dir("sheets")]


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_sheet(sheet_id: str) -> dict[str, Any] | None:
    """The sheet with ``sheet_id``, or ``None`` if the id is unsafe, unknown or invalid.

    The id is checked against the schema pattern before any path is built.
    """
    if not isinstance(sheet_id, str) or not SHEET_ID_RE.fullmatch(sheet_id):
        return None
    for folder in sheet_dirs():
        data = _read(folder / f"{sheet_id}.json")
        if data is not None and data.get("sheet_id") == sheet_id and not validate_sheet(data):
            return data
    return None


def list_sheets() -> list[dict[str, str]]:
    """Summary rows ``{sheet_id, title, place, hazard, date}`` of every valid sheet."""
    seen: dict[str, dict[str, str]] = {}
    for folder in sheet_dirs():
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json")):
            sid = f.stem
            if sid in seen or not SHEET_ID_RE.fullmatch(sid):
                continue
            data = load_sheet(sid)
            if data is not None:
                seen[sid] = {k: data[k] for k in ("sheet_id", "title", "place", "hazard", "date")}
    return list(seen.values())
