"""R6 twin with everything held fixed (sewer = Seoul design 95 mm/h); threshold on vs off."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for e in (REPO / "worker", REPO / "src", REPO / "scripts"):
    sys.path.insert(0, str(e))
import synthetic_city as sc  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

spec = load_spec()
spec["calibration"]["sewer_mm_h"] = [95.0]  # no calibration: one fixed, design-level sewer
out = Path.home() / "Data/climaterisk/derived/synthetic_city/fixed95"
raise SystemExit(sc._print_real(spec, out))
