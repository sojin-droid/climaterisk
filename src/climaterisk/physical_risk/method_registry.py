"""Loader for the method registry (``assets/libraries/method_registry.json``).

The registry fixes, per hazard tag, the ranked main methods, the ordered checks and the
supplementary results. Rule: rank 1 only; no filling from other models or ranks; checks
never change the main value. This module only reads it; it changes no behaviour.
Pure, no CLIMADA, no network, so the worker environment can import it.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Hazard tags covered by the registry.
TAGS: tuple[str, ...] = ("RF", "PF", "CF", "TC", "HW", "HM")


def registry_path() -> Path:
    """Location of the registry; ``CLIMATERISK_LIBRARY_DIR`` overrides like the config."""
    override = os.environ.get("CLIMATERISK_LIBRARY_DIR")
    base = Path(override) if override else _REPO_ROOT / "assets" / "libraries"
    if not base.is_absolute():
        base = _REPO_ROOT / base
    return base / "method_registry.json"


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """The registry, read once per path.

    Args:
        path: Override for the file location (tests).
    """
    return _load(path if path is not None else registry_path())


def _entry(tag: str) -> dict[str, Any]:
    entry: dict[str, Any] = load_registry()["tags"][tag]
    return entry


def main_method(tag: str, rank: int = 1) -> dict[str, Any] | None:
    """The main-method entry of ``tag`` at ``rank``, or None when there is none."""
    for m in _entry(tag)["main"]:
        if m["rank"] == rank:
            return dict(m)
    return None


def rank1_model(tag: str) -> str | None:
    """Model id of the rank-1 main method of ``tag``."""
    m = main_method(tag, 1)
    return None if m is None else str(m["model_id"])


def method_for(tag: str, model_id: str) -> dict[str, Any]:
    """Method identity of a (tag, model) cell: a ranked main method or a reference."""
    for m in _entry(tag)["main"]:
        if m["model_id"] == model_id:
            return {"method_id": m["method_id"], "method_rank": m["rank"], "method_role": "main"}
    return {
        "method_id": f"{tag.lower()}.reference.{model_id.lower()}",
        "method_rank": None,
        "method_role": "reference",
    }


def defences_allowed(tag: str, model_id: str) -> bool:
    """False only for a main entry that says ``"defences": false``."""
    for m in _entry(tag)["main"]:
        if m["model_id"] == model_id:
            return bool(m.get("defences", True))
    return True


def checks(tag: str) -> list[dict[str, Any]]:
    """Checks of ``tag`` sorted by ``order``."""
    return sorted((dict(c) for c in _entry(tag)["checks"]), key=lambda c: c["order"])


def supplementary(tag: str) -> list[dict[str, Any]]:
    """Supplementary results of ``tag`` (discussion only, never checks)."""
    return [dict(s) for s in _entry(tag)["supplementary"]]


def readiness_key(tag: str) -> str:
    """Key into ``models.READINESS``: ``HEAT`` for HW/HM, else the tag."""
    return "HEAT" if tag in ("HW", "HM") else tag


def _dig(diagnostics: dict[str, Any], dotted: str) -> Any:
    """Value at a dot path in nested dicts, or None when any step is absent."""
    node: Any = diagnostics
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def run_checks(tag: str, model_id: str, diagnostics: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Run the registry checks of ``tag`` in order and record what each one did.

    Checks are diagnostic only; they never change a hazard or a loss. Reference
    models (not a ranked main method) have no checks.

    Args:
        tag: Peril tag, e.g. ``"RF"``.
        model_id: Model that produced the hazard.
        diagnostics: The adapter's diagnostics dict, or None.

    Returns:
        One dict per registry check, in order, with keys ``check_id``, ``order``,
        ``state`` (``ran`` / ``warn`` / ``skipped`` / ``error``) and ``reason``
        (None when the check ran cleanly).
    """
    if method_for(tag, model_id)["method_role"] != "main":
        return []
    diag = diagnostics or {}
    out: list[dict[str, Any]] = []
    for check in checks(tag):
        state, reason = _state_of(check, diag)
        out.append(
            {
                "check_id": check["check_id"],
                "order": check["order"],
                "state": state,
                "reason": reason,
            }
        )
    return out


def _state_of(check: dict[str, Any], diag: dict[str, Any]) -> tuple[str, str | None]:
    """State and reason of one check from the adapter diagnostics.

    Mapping: ``to_build`` -> skipped ("not built yet"); path absent or not a dict ->
    skipped ("no diagnostics for this run"); ``error`` -> error; ``warn`` True ->
    warn (reason: ``warn_reason``, then ``reason``, then ``note``); ``warn`` False ->
    ran; no ``warn`` key at all -> ran without a verdict (reason: ``note``); an
    explicit ``warn`` None -> skipped (reason: ``note`` or "not enough points").
    """
    if check["status"] == "to_build":
        return "skipped", "not built yet"
    value = _dig(diag, check["diagnostics_key"])
    if not isinstance(value, dict):
        return "skipped", "no diagnostics for this run"
    if value.get("error"):
        return "error", str(value["error"])
    note = value.get("note")
    if "warn" not in value:
        return "ran", None if note is None else str(note)
    warn = value["warn"]
    if warn is True:
        reason = value.get("warn_reason") or value.get("reason") or note
        return "warn", None if reason is None else str(reason)
    if warn is False:
        return "ran", None
    return "skipped", str(note) if note else "not enough points"
