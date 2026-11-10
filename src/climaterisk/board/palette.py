"""Colour authority of the visual board: reads ``assets/libraries/board_palettes.json``.

Colour = peril family, darkness = magnitude; class breaks are fixed per quantity and never
derived from the data on screen. This module only applies the rule written in the spec's
``_meta.class_rule``; it holds no colour of its own.

Algorithm:
    For a value $v$ of a quantity with breaks $b_0 < \\dots < b_{n-1}$ and ramp steps
    $s_0 \\dots s_n$: $v$ null/NaN -> null colour; $v = 0$ and ``zero_means_none`` -> zero
    colour; else class $i = \\min\\{ j : v \\le b_j \\}$ ($i = n$ if $v > b_{n-1}$), colour
    ``ramp[s_i]``. ``spei_stage``: first stage (driest first) with $v \\le leq$, else zero.
    ``change``: the same rule on the ratio with ``change.colors``.
    ASCII: class = number of breaks strictly below v; colour = ramp[steps[class]].
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

from climaterisk.config import get_settings

PALETTE_FILE = "board_palettes.json"


@lru_cache(maxsize=1)
def load_palettes() -> dict[str, Any]:
    """The palette spec (the JSON file as is), cached for the process lifetime."""
    path = get_settings().library_path / PALETTE_FILE
    with path.open(encoding="utf-8") as fh:
        spec: dict[str, Any] = json.load(fh)
    return spec


def _fmt(x: float) -> str:
    return f"{x:g}"


def _class_index(breaks: list[float], value: float) -> int:
    """Index of the class of ``value``: upper bounds inclusive, open last class."""
    for i, upper in enumerate(breaks):
        if value <= upper:
            return i
    return len(breaks)


def _is_null(value: float | None) -> bool:
    return value is None or math.isnan(value)


def _quantity(spec: dict[str, Any], quantity: str) -> dict[str, Any]:
    q: dict[str, Any] = spec["quantities"][quantity]  # KeyError for an unknown quantity
    return q


def _ramp(spec: dict[str, Any], q: dict[str, Any], family: str | None = None) -> list[str]:
    ramp: list[str] = spec["families"][family or q["family"]]["ramp"]
    return ramp


def classes(quantity: str, family: str | None = None) -> list[dict[str, Any]]:
    """Legend classes of a quantity in class order.

    Args:
        quantity: Key of ``quantities`` (or ``"change"``).
        family: Family whose ramp to use for the ``loss_ratio`` quantity (whose own family is
            ``"peril"``, i.e. chosen by the hazard); ignored otherwise.

    Returns:
        ``[{"up_to": float | None, "color": str, "label": str}, ...]``; ``up_to`` is None for
        the open last class. ``spei_stage``: driest stage first, then the "none" class.

    Raises:
        KeyError: Unknown quantity (or a ``loss_ratio`` call without a known family).
    """
    spec = load_palettes()
    if quantity == "change":
        ch = spec["change"]
        uppers: list[float | None] = [*ch["breaks"], None]
        return [
            {"up_to": u, "color": c, "label": lab}
            for u, c, lab in zip(uppers, ch["colors"], ch["labels"], strict=True)
        ]
    q = _quantity(spec, quantity)
    if "stages" in q:
        ramp = _ramp(spec, q)
        out: list[dict[str, Any]] = [
            {"up_to": s["leq"], "color": ramp[s["step"]], "label": s["label"]} for s in q["stages"]
        ]
        out.append(
            {
                "up_to": None,
                "color": spec["shared"]["zero"]["color"],
                "label": q["above_last"]["label"],
            }
        )
        return out
    ramp = _ramp(spec, q, family)
    breaks = q["breaks"]
    out = []
    for i, step in enumerate(q["steps"]):
        if i == 0:
            label = f"≤ {_fmt(breaks[0])}"
        elif i < len(breaks):
            label = f"{_fmt(breaks[i - 1])} – {_fmt(breaks[i])}"
        else:
            label = f"> {_fmt(breaks[-1])}"
        out.append(
            {"up_to": breaks[i] if i < len(breaks) else None, "color": ramp[step], "label": label}
        )
    return out


def color(quantity: str, value: float | None, family: str | None = None) -> str:
    """Hex colour of ``value`` for ``quantity`` by ``_meta.class_rule``.

    Args:
        quantity: Key of ``quantities``, or ``"change"`` (``value`` is a ratio).
        value: The value; None / NaN -> the shared no-data colour.
        family: Family override, only for ``loss_ratio`` (see ``classes``).

    Raises:
        KeyError: Unknown quantity.
    """
    spec = load_palettes()
    shared = spec["shared"]
    if quantity == "change":
        if _is_null(value):
            return str(shared["null"]["color"])
        assert value is not None
        ch = spec["change"]
        return str(ch["colors"][_class_index(ch["breaks"], value)])
    q = _quantity(spec, quantity)
    if _is_null(value):
        return str(shared["null"]["color"])
    assert value is not None
    if "stages" in q:
        ramp = _ramp(spec, q)
        for stage in q["stages"]:
            if value <= stage["leq"]:
                return str(ramp[stage["step"]])
        return str(shared["zero"]["color"])
    if value == 0 and q["zero_means_none"]:
        return str(shared["zero"]["color"])
    ramp = _ramp(spec, q, family)
    return str(ramp[q["steps"][_class_index(q["breaks"], value)]])
