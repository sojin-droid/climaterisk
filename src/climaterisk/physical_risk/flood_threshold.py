"""Zero-damage flood threshold from site-survey inputs — the staged 무피해 문턱 rule.

The JRC depth-damage curve is kept as published; the survey only decides up to which
*outside* water depth ``T`` the building takes no damage. Above ``T`` the curve applies
unchanged (the FLOPROS overtopping rule: water that tops a barrier floods with its full
depth). The CLIMADA side of the rule — the curve with the step applied — lives in the worker
(``registry.thresholded_flood_impact_function``); this module is CLIMADA-free and importable
from both environments.

Defaults and coefficients come from ``assets/libraries/flood_threshold_defaults.json``, each
with a cited source; nothing here hard-codes a Korean standard.

Algorithm:
    $$T = \\max\\big(s,\\; b\\,\\mathbb{1}[\\text{board installed}],\\; w,\\;
      h_1\\,\\mathbb{1}[\\text{no basement opening}]\\big), \\qquad
      \\text{MDR}_T(d) = \\text{JRC}(d)\\,\\mathbb{1}[d > T]$$
    $$\\Delta H = \\Big(\\frac{Q}{C\\,B\\,3600}\\Big)^{2/3}$$
    ASCII: T = max(sill, installed board, wall, 1F floor if nothing opens to the basement);
    damage = JRC(d) if d > T else 0. Pump head dH = (Q / (C * B * 3600)) ** (2/3).

    ``s``: lowest opening sill (m, default ``SILL_DEF`` = 0.05 = the curve's own zero);
    ``b``: flood-board height (m), counted only when installed; ``w``: flood-wall height (m);
    ``h_1``: first-floor height above grade (m), counted only when no opening leads below it;
    ``d``: outside flood depth (m); ``Q``: rain / flood pump capacity (m³/h); ``C``: weir
    coefficient (m^0.5/s); ``B``: ramp entrance width (m); ``ΔH``: water head above the sill
    the pumps keep up with (m) — street (pluvial) flooding only, never river or coastal,
    because river and coastal water backflows through the drains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from climaterisk.physical_risk.metrics import config_path

#: Survey vocabulary for the flood-board state. Only an installed board protects today; a
#: planned or recommended one counts only in the "if installed" value.
BOARD_INSTALLED: tuple[str, ...] = ("설치됨",)
BOARD_PLANNED: tuple[str, ...] = ("설치됨", "설치 계획", "설치 권고")
#: Survey vocabulary for "none" (wall absent, no opening leads to the basement).
NONE = "없음"

#: Stage labels — which survey input set ``T``.
STAGE_DEFAULT = "기본값 (기존 곡선)"
STAGE_SILL = "① 문턱 실측"
STAGE_BOARD = "② 차수판"
STAGE_WALL = "③ 차수벽"
STAGE_FIRST_FLOOR = "④ 1층 바닥"


def defaults_path() -> Path:
    """The defaults file, beside ``physical_risk_config.json`` (same library override)."""
    return config_path().parent / "flood_threshold_defaults.json"


@lru_cache(maxsize=1)
def defaults() -> dict[str, Any]:
    """The defaults file (values + sources), read once."""
    data: dict[str, Any] = json.loads(defaults_path().read_text(encoding="utf-8"))
    return data


def default(name: str) -> float:
    """One default value by name (``SILL_DEF``, ``RAMP_W``, ``WEIR``, ``BOARD_PLAN``)."""
    return float(defaults()["values"][name]["value"])


@dataclass(frozen=True)
class Threshold:
    """A facility's zero-damage threshold now, and if its planned board were installed.

    Attributes:
        t_m: Zero-damage threshold ``T`` (m) applied to river / coastal flood.
        stage: Which survey input set ``T`` (one of the ``STAGE_*`` labels).
        t_if_board_m: ``T`` if the planned / recommended board were installed (m).
        pump_head_m: Head above the sill the pumps keep up with (m) — street flooding only.
    """

    t_m: float
    stage: str
    t_if_board_m: float
    pump_head_m: float

    @property
    def road_m(self) -> float:
        """Street-flood threshold: ``T`` plus the pump head (reference only, never priced)."""
        return self.t_m + self.pump_head_m


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def pump_head_m(q_m3h: float, ramp_width_m: float | None = None) -> float:
    """Head above the sill at which street inflow over the ramp equals the pump capacity.

    Args:
        q_m3h: Rain / flood pump capacity ``Q`` (m³/h); 0 or less gives no head.
        ramp_width_m: Ramp entrance width ``B`` (m); the ``RAMP_W`` default when absent.

    Returns:
        ``ΔH`` (m) from the broad-crested weir equation inverted for head.
    """
    if q_m3h <= 0:
        return 0.0
    b = ramp_width_m if ramp_width_m and ramp_width_m > 0 else default("RAMP_W")
    return float((q_m3h / (default("WEIR") * b * 3600.0)) ** (2.0 / 3.0))


def zero_damage_threshold(
    *,
    sill_m: Any = None,
    board_status: Any = None,
    board_m: Any = None,
    wall_status: Any = None,
    wall_m: Any = None,
    first_floor_m: Any = None,
    basement_opening: Any = None,
    pump_q_m3h: float = 0.0,
    ramp_width_m: Any = None,
) -> Threshold:
    """The staged threshold — default, ① sill, ② board, ③ wall, ④ first floor.

    A board counts only when ``board_status`` is 설치됨 and a height is given; a wall when a
    height is given and ``wall_status`` is not 없음; the first floor only when
    ``basement_opening`` is 없음 (water reaching the floor otherwise also enters below it).
    Each stage raises ``T`` only when it is higher than the stage before. Non-numeric or
    blank inputs are treated as not measured, never as 0.

    Args:
        sill_m: Lowest opening sill height (m).
        board_status: Flood-board state (설치됨 / 설치 계획 / 설치 권고 / 없음).
        board_m: Flood-board height (m).
        wall_status: Flood-wall state (없음 means no wall).
        wall_m: Flood-wall height (m).
        first_floor_m: First-floor height above grade (m).
        basement_opening: Whether any opening leads to the basement (있음 / 없음).
        pump_q_m3h: Rain / flood pump capacity (m³/h) — street-flood reference only.
        ramp_width_m: Ramp entrance width (m) for the pump head.

    Returns:
        The :class:`Threshold` with ``T``, its stage, the if-installed ``T`` and pump head.
    """
    s = _num(sill_m)
    t, stage = (s, STAGE_SILL) if s is not None else (default("SILL_DEF"), STAGE_DEFAULT)
    bh, wh, fh = _num(board_m), _num(wall_m), _num(first_floor_m)
    if bh is not None and board_status in BOARD_INSTALLED and bh > t:
        t, stage = bh, STAGE_BOARD
    if wh is not None and wall_status != NONE and wh > t:
        t, stage = wh, STAGE_WALL
    if basement_opening == NONE and fh is not None and fh > t:
        t, stage = fh, STAGE_FIRST_FLOOR
    planned = t
    if board_status in BOARD_PLANNED:
        planned = max(t, bh if bh is not None else default("BOARD_PLAN"))
    return Threshold(
        t_m=t,
        stage=stage,
        t_if_board_m=planned,
        pump_head_m=pump_head_m(pump_q_m3h, _num(ramp_width_m)),
    )
