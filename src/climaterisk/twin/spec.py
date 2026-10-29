"""Synthetic city specification — every assumption lives in this JSON.

No real building or GIS data is used: the city, its terrain, its asset values and its flood
are generated from ``assets/libraries/synthetic_city_spec.json`` with a pinned seed. Change
values here, not in code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from climaterisk.paths import REPO_ROOT

#: Every assumption for the synthetic city lives here (with ``_note`` fields).
SPEC_PATH: Path = REPO_ROOT / "assets" / "libraries" / "synthetic_city_spec.json"

#: Model id written onto every engine ResultRow for the synthetic city.
MODEL_ID: str = "SYNTHETIC_CITY"


def load_spec(path: str | Path = SPEC_PATH) -> dict[str, Any]:
    """The city specification (every assumption lives in this JSON).

    Args:
        path: Path to the spec JSON; defaults to
            ``assets/libraries/synthetic_city_spec.json``.

    Returns:
        The parsed specification as a plain dict.
    """
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data
