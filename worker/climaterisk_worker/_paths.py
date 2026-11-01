"""The worker's handle on :mod:`climaterisk.paths` — the project's single path authority.

``climaterisk.paths`` is stdlib-only, so the CLIMADA worker (no pydantic; GPL boundary) can
import it. The worker is started with ``cwd=worker/`` and ``worker/`` on ``sys.path``; this
shim adds ``src/`` found from this file's location, never from the working directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from climaterisk import paths  # noqa: E402

__all__ = ["paths"]
