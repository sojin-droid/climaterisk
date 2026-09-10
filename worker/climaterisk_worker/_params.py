"""Shared year-anchoring constants + helpers for the CLIMADA worker runners.

Single source of truth so the ingest layer (``ingest.py``) and the physical
runners (``physical.py``) compute the SAME catalog key ``(peril, scenario, region,
year)``. If these drifted between the two files, an ingested hazard would be
written under one key and looked up under another — a silent lookup miss.

Pure constants/helpers, no CLIMADA import, so this stays importable everywhere.
"""

from __future__ import annotations

# CLIMADA Data API tropical-cyclone future reference years.
TC_REF_YEARS = (2040, 2060, 2080)
# River-flood future window midpoints available from the Data API.
RF_YEAR_RANGES = ("2010_2030", "2030_2050", "2050_2070", "2070_2090")
# RF publishes rcp26/60/85 only; map the platform's climate scenario to the nearest.
RF_SCENARIO_MAP = {"rcp26": "rcp26", "rcp45": "rcp60", "rcp60": "rcp60", "rcp85": "rcp85"}
# CLIMADA ``haz_type`` tag of the platform's ``heatwave`` (season p95 Tmax, degC) layer. The
# catalog builders (scripts/heat_korea.py, scripts/heatwave_europe.py) and the runner's
# ``ImpactFunc``/``impf_<HT>`` column must agree, or ImpactCalc finds no impact function.
HEATWAVE_HAZ_TYPE = "HW"


def nearest(options: tuple[int, ...], target: int) -> int:
    """Return the option closest to ``target`` (ties resolve to the smaller value)."""
    return min(options, key=lambda y: (abs(y - target), y))
