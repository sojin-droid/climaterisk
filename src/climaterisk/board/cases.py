"""Case-twin pages for the visual board (read-only).

A case twin is built elsewhere (the twin branch) and leaves
``<DATA_ROOT>/derived/case_twins/<id>/summary.json`` plus PNG figures. The board only reads
them: it never recomputes, never shows money (any key containing ``krw``, ``usd`` or ``loss``
is dropped) and never puts twin results on the national map. Only figures listed in
``assets/libraries/board_cases.json`` can be served.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from climaterisk import paths
from climaterisk.config import get_settings

_CONFIG_FILE = "board_cases.json"
_MONEY_WORDS = ("krw", "usd", "loss")
_DEFAULT_BADGE = "미검증"


def case_dir(case_id: str) -> Path:
    """Directory of one case's build output (resolved at call time)."""
    return paths.derived_case_twins_dir(case_id)


def _config() -> list[dict[str, Any]]:
    data = json.loads((get_settings().library_path / _CONFIG_FILE).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def _cfg(case_id: str) -> dict[str, Any]:
    for case in _config():
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def _summary_path(case_id: str) -> Path:
    return case_dir(case_id) / "summary.json"


def _strip_money(obj: Any) -> Any:
    """Copy of ``obj`` without dict keys that name money (krw / usd / loss, any case)."""
    if isinstance(obj, dict):
        return {
            k: _strip_money(v)
            for k, v in obj.items()
            if not any(w in str(k).lower() for w in _MONEY_WORDS)
        }
    if isinstance(obj, list):
        return [_strip_money(v) for v in obj]
    return obj


def _read_summary(case_id: str) -> dict[str, Any]:
    summary: dict[str, Any] = json.loads(_summary_path(case_id).read_text(encoding="utf-8"))
    return summary


def list_cases() -> list[dict[str, Any]]:
    """Configured cases with ``available`` (summary.json exists) and the summary's badge."""
    out = []
    for case in _config():
        path = _summary_path(case["id"])
        available = path.is_file()
        badge = _DEFAULT_BADGE
        if available:
            try:
                badge = str(_read_summary(case["id"]).get("badge") or _DEFAULT_BADGE)
            except (OSError, ValueError):
                available = False
        out.append(
            {
                "id": case["id"],
                "name": case["name"],
                "peril": case["peril"],
                "lon": case["lon"],
                "lat": case["lat"],
                "cell_m": case["cell_m"],
                "badge": badge,
                "available": available,
            }
        )
    return out


def _dig(summary: dict[str, Any], path: list[str | int]) -> Any:
    """Value at ``path`` (dict keys, or int indexes into lists); ``None`` when absent."""
    cur: Any = summary
    for key in path:
        if isinstance(key, int) and not isinstance(key, bool):
            if not isinstance(cur, list) or not -len(cur) <= key < len(cur):
                return None
            cur = cur[key]
        elif isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def load_case(case_id: str) -> dict[str, Any]:
    """Config fields + money-free ``summary`` + badge, assumptions and resolved highlights.

    ``highlights`` resolve each config ``path`` into the summary (missing -> ``None``, never 0).

    Raises:
        KeyError: Unknown case id.
        FileNotFoundError: The case is configured but not built.
    """
    cfg = _cfg(case_id)
    path = _summary_path(case_id)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    summary = _strip_money(_read_summary(case_id))
    assumptions = list(summary.get("assumptions") or [])
    n_assumptions = int(summary.get("n_assumptions", len(assumptions)))
    highlights = []
    for h in cfg.get("highlights", []):
        value = _dig(summary, h["path"])
        highlights.append(
            {
                "label_ko": h["label_ko"],
                "unit": h.get("unit", ""),
                "value": value
                if isinstance(value, int | float) and not isinstance(value, bool)
                else None,
            }
        )
    return {
        **cfg,
        "summary": summary,
        "badge": str(summary.get("badge") or _DEFAULT_BADGE),
        "assumptions": assumptions,
        "n_assumptions": n_assumptions,
        "assumptions_complete": len(assumptions) == n_assumptions,
        "highlights": highlights,
    }


def figure_path(case_id: str, file: str) -> Path:
    """Path of a figure listed in the config for ``case_id``.

    Raises:
        KeyError: Unknown case, or ``file`` is not a configured figure (also ``..`` tricks).
        FileNotFoundError: Listed but missing on disk.
    """
    cfg = _cfg(case_id)
    if file not in {f["file"] for f in cfg["figures"]}:
        raise KeyError(file)
    path = case_dir(case_id) / file
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path
