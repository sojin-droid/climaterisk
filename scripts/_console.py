"""UTF-8 console for every script in this folder — import it, nothing else.

Script output carries em-dashes, arrows and Korean. On Windows the console code page is
often cp949, which makes ``print`` (and argparse ``--help``) raise ``UnicodeEncodeError``.
Reconfiguring the standard streams once, at import time, is harmless on macOS/Linux
(already UTF-8) and keeps the scripts runnable everywhere. Scripts import this module by
its bare name: ``python scripts/x.py`` puts this folder first on ``sys.path`` whatever the
working directory.
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
