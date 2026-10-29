"""Synthetic city digital twin — city graph, terrain, and assets (no CLIMADA).

Every assumption about the imaginary city lives in
``assets/libraries/synthetic_city_spec.json``. The generator produces a NetworkX street
grid, a terrain model (ground elevation above river datum) and one asset per node/edge
with a lognormal value. No real building or GIS data is used.

The CLIMADA hazard and pricing half lives in :mod:`climaterisk_worker.twin`.
"""

from __future__ import annotations

from climaterisk.twin.city import M_PER_DEG_LAT, build_city, node_kind
from climaterisk.twin.flood import flood_depths, layered_frequencies
from climaterisk.twin.spec import MODEL_ID, SPEC_PATH, load_spec

__all__ = [
    "MODEL_ID",
    "M_PER_DEG_LAT",
    "SPEC_PATH",
    "build_city",
    "flood_depths",
    "layered_frequencies",
    "load_spec",
    "node_kind",
]
