"""Synthetic city twin — CLIMADA hazard construction and pricing.

This package runs in the CLIMADA worker environment. It prices the synthetic city's assets
through the same engine the batch runner uses
(:func:`climaterisk_worker.physical_risk.engine.calculate`), so every loss number comes
from the published JRC depth-damage curve via CLIMADA's ``ImpactCalc`` — never
re-implemented. The city graph, terrain and assets come from the no-CLIMADA
:mod:`climaterisk.twin` package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the backend package (src/) is importable: the twin prices through
# climaterisk.twin (city graph, terrain, assets) and climaterisk.physical_risk.metrics.
_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from climaterisk_worker.twin.hazard import build_hazard  # noqa: E402
from climaterisk_worker.twin.network import network_failure  # noqa: E402
from climaterisk_worker.twin.pipeline import run  # noqa: E402
from climaterisk_worker.twin.plot import plot_city  # noqa: E402
from climaterisk_worker.twin.pricing import portfolio_impact, price_assets  # noqa: E402

__all__ = [
    "build_hazard",
    "network_failure",
    "plot_city",
    "portfolio_impact",
    "price_assets",
    "run",
]
