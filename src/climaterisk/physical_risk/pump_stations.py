"""배수·빗물펌프장 library — where Korean flood drainage pumps are, and what they say about levees.

Pure and stdlib-only so both the backend and the CLIMADA worker read it. The table
(``assets/libraries/korea_pump_stations.csv``) is built by ``scripts/build_pump_stations.py``
from data.go.kr files; only coordinates **the publisher gave** are used — nothing is
geocoded.

How the Models tab uses it (``flood_protection.RF.levee_protected`` in the configuration):
a 배수펌프장 lifts water out of land that sits behind a river levee (제내지) — it only exists
where there is a levee to pump over. A river-flood grid cell within ``radius_km`` of a
located pump station is therefore treated as levee-protected urban floodplain and gets the
urban-reach plan scale (하천기본계획 수립지침 B급 100~200년 → its lower bound) instead of the
national default. The pump's own 계획빈도 is an **interior-drainage** (내수) design rainfall
frequency — typically 20–30 years — not the levee's; it is reported beside the result, never
used as the river-flood protection level.

Algorithm:
    $$d(p, q) = 2R\\,\\arcsin\\sqrt{\\sin^2\\tfrac{\\Delta\\varphi}{2}
      + \\cos\\varphi_p\\cos\\varphi_q\\sin^2\\tfrac{\\Delta\\lambda}{2}}$$
    ASCII: great-circle (haversine) distance in km, R = 6371.0088 km (mean Earth radius).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY = _REPO_ROOT / "assets" / "libraries" / "korea_pump_stations.csv"
#: Mean Earth radius (km), IUGG.
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class PumpStation:
    """One facility as published (coordinates only when the publisher gave them)."""

    pump_station_id: str
    name: str
    latitude: float | None
    longitude: float | None
    design_frequency_years: int | None
    address: str
    source_dataset_id: str
    source_title: str
    source_licence: str


def _num(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


@lru_cache(maxsize=1)
def load(path: Path = LIBRARY) -> tuple[PumpStation, ...]:
    """Every row of the library."""
    with path.open(encoding="utf-8", newline="") as fh:
        return tuple(
            PumpStation(
                pump_station_id=r["pump_station_id"],
                name=r["name"],
                latitude=_num(r["latitude"]),
                longitude=_num(r["longitude"]),
                design_frequency_years=int(r["design_frequency_years"])
                if r["design_frequency_years"]
                else None,
                address=r["address"],
                source_dataset_id=r["source_dataset_id"],
                source_title=r["source_title"],
                source_licence=r["source_licence"],
            )
            for r in csv.DictReader(fh)
        )


def located(stations: tuple[PumpStation, ...] | None = None) -> tuple[PumpStation, ...]:
    """The stations with publisher coordinates."""
    return tuple(s for s in (stations or load()) if s.latitude is not None)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def nearest(
    lat: float, lon: float, stations: tuple[PumpStation, ...] | None = None
) -> tuple[PumpStation, float] | None:
    """The nearest located station and its distance (km), or None when none is located."""
    pool = located(stations)
    if not pool:
        return None
    best = min(pool, key=lambda s: distance_km(lat, lon, s.latitude or 0.0, s.longitude or 0.0))
    return best, distance_km(lat, lon, best.latitude or 0.0, best.longitude or 0.0)
