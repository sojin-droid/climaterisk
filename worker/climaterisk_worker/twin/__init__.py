"""Synthetic city twin — city graph, terrain, assets, CLIMADA hazard and pricing.

This package runs in the CLIMADA worker environment. It prices the synthetic city's assets
through the same engine the batch runner uses
(:func:`climaterisk_worker.physical_risk.engine.calculate`), so every loss number comes
from the published JRC depth-damage curve via CLIMADA's ``ImpactCalc`` — never
re-implemented. The generator half (``spec``, ``city``, ``flood``) needs only NumPy and
NetworkX, which the backend environment does not carry, so it lives here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the backend package (src/) is importable: the twin prices through
# climaterisk.paths and climaterisk.physical_risk.metrics.
_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from climaterisk_worker.twin.city import M_PER_DEG_LAT, build_city, node_kind  # noqa: E402
from climaterisk_worker.twin.climate import build_future_hazard, future_scenarios  # noqa: E402
from climaterisk_worker.twin.coastal import (  # noqa: E402
    coast_centroids,
    coast_latitude,
    price_surge,
    surge_hazard,
    write_dem,
)
from climaterisk_worker.twin.flood import flood_depths, layered_frequencies  # noqa: E402
from climaterisk_worker.twin.hazard import build_hazard  # noqa: E402
from climaterisk_worker.twin.heat import (  # noqa: E402
    build_heat_hazard,
    feels_like,
    heat_events,
    mean_radiant_temperature,
    price_productivity,
    productivity_impact_function,
    sky_view_factor,
    street_canyons,
    sunlit_fraction,
)
from climaterisk_worker.twin.network import network_failure  # noqa: E402
from climaterisk_worker.twin.pipeline import compare_climate, compare_defences, run  # noqa: E402
from climaterisk_worker.twin.plot import plot_city  # noqa: E402
from climaterisk_worker.twin.pricing import (  # noqa: E402
    defences_for,
    portfolio_impact,
    price_assets,
)
from climaterisk_worker.twin.spec import MODEL_ID, SPEC_PATH, load_spec  # noqa: E402
from climaterisk_worker.twin.typhoon import (  # noqa: E402
    build_tc_hazard,
    make_track,
    price_tc,
    synthetic_tracks,
    wind_hazard,
)
from climaterisk_worker.twin.viewer import render_map  # noqa: E402
from climaterisk_worker.twin.viewer3d import generate_buildings, render_3d  # noqa: E402

__all__ = [
    "MODEL_ID",
    "M_PER_DEG_LAT",
    "SPEC_PATH",
    "build_city",
    "build_future_hazard",
    "build_hazard",
    "build_heat_hazard",
    "build_tc_hazard",
    "coast_centroids",
    "coast_latitude",
    "compare_climate",
    "compare_defences",
    "defences_for",
    "feels_like",
    "flood_depths",
    "future_scenarios",
    "generate_buildings",
    "heat_events",
    "layered_frequencies",
    "load_spec",
    "make_track",
    "mean_radiant_temperature",
    "network_failure",
    "node_kind",
    "plot_city",
    "portfolio_impact",
    "price_assets",
    "price_productivity",
    "price_surge",
    "price_tc",
    "productivity_impact_function",
    "render_3d",
    "render_map",
    "run",
    "sky_view_factor",
    "street_canyons",
    "sunlit_fraction",
    "surge_hazard",
    "synthetic_tracks",
    "wind_hazard",
    "write_dem",
]
