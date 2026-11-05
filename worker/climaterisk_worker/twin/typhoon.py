"""Synthetic typhoons: seeded tracks -> CLIMADA ``TropCyclone`` wind -> engine TC pricing.

Tracks are straight, constant-intensity, hourly paths in CLIMADA ``TCTracks`` format. The
wind field is CLIMADA's Holland (2008) parametric profile with its translation-speed
asymmetry (:meth:`climada.hazard.TropCyclone.from_tracks`); no data is downloaded. Pricing
goes through :func:`climaterisk_worker.physical_risk.engine.calculate` (hazard type ``TC``,
Korea -> CLIMADA region WP4, impact function 9); the damage arithmetic is never repeated here.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from climaterisk_worker.twin.spec import MODEL_ID

KM_PER_DEG_LAT = 111.32  # km per degree of latitude (flat-earth step)
KN_PER_MS = 1.943844  # knots per m/s
KM_PER_NM = 1.852  # km per nautical mile
ENV_PRESSURE_HPA = 1010.0  # environmental pressure (hPa), CLIMADA default order of magnitude


def make_track(
    lat0: float,
    lon0: float,
    *,
    heading_deg: float,
    speed_kmh: float,
    vmax_ms: float,
    pmin_hpa: float,
    rmax_km: float,
    hours: int,
    name: str,
) -> Any:
    """One hourly straight-line track as a CLIMADA ``TCTracks`` dataset.

    Algorithm:
        $$\\Delta\\phi = \\frac{d\\cos\\theta}{111.32},\\quad
        \\Delta\\lambda = \\frac{d\\sin\\theta}{111.32\\cos\\phi}$$

        ASCII: dlat = d*cos(heading)/111.32 ; dlon = d*sin(heading)/(111.32*cos(lat))

    $d$ is the hourly step (km) = ``speed_kmh`` x 1 h; $\\theta$ the heading (rad, 0 = north,
    clockwise); $\\phi$, $\\lambda$ latitude and longitude (deg). Intensity is constant.

    Args:
        lat0: Start latitude (deg N).
        lon0: Start longitude (deg E).
        heading_deg: Direction of motion (deg clockwise from north).
        speed_kmh: Translation speed (km/h).
        vmax_ms: Maximum sustained wind (m/s).
        pmin_hpa: Central pressure (hPa).
        rmax_km: Radius of maximum wind (km).
        hours: Track length (h); the dataset has ``hours + 1`` hourly points.
        name: Track name, also used as the unique ``sid``.

    Returns:
        An ``xarray.Dataset`` accepted by ``TCTracks(data=[ds])``.
    """
    import pandas as pd
    import xarray as xr

    n = hours + 1
    theta = math.radians(heading_deg)
    lat = np.empty(n)
    lon = np.empty(n)
    lat[0], lon[0] = lat0, lon0
    for i in range(1, n):
        lat[i] = lat[i - 1] + speed_kmh * math.cos(theta) / KM_PER_DEG_LAT
        lon[i] = lon[i - 1] + speed_kmh * math.sin(theta) / (
            KM_PER_DEG_LAT * math.cos(math.radians(lat[i - 1]))
        )
    time = pd.date_range("2020-08-01", periods=n, freq="h")
    return xr.Dataset(
        {
            "time_step": ("time", np.ones(n)),
            "max_sustained_wind": ("time", np.full(n, vmax_ms * KN_PER_MS)),
            "central_pressure": ("time", np.full(n, pmin_hpa)),
            "radius_max_wind": ("time", np.full(n, rmax_km / KM_PER_NM)),
            "environmental_pressure": ("time", np.full(n, ENV_PRESSURE_HPA)),
            "basin": ("time", np.array(["WP"] * n)),
        },
        coords={"time": time, "lat": ("time", lat), "lon": ("time", lon)},
        attrs={
            "max_sustained_wind_unit": "kn",
            "central_pressure_unit": "mb",
            "name": name,
            "sid": name,
            "orig_event_flag": True,
            "data_provider": "synthetic",
            "id_no": abs(hash(name)) % 10**9 or 1,
            "category": 3,
        },
    )


def _city_centre(spec: dict[str, Any]) -> tuple[float, float]:
    """Latitude and longitude (deg) of the middle of the street grid."""
    g = spec["grid"]
    half_n = (g["rows"] - 1) * g["spacing_m"] / 2.0 / 1000.0
    half_e = (g["cols"] - 1) * g["spacing_m"] / 2.0 / 1000.0
    lat = g["origin_lat"] + half_n / KM_PER_DEG_LAT
    lon = g["origin_lon"] + half_e / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return lat, lon


def synthetic_tracks(spec: dict[str, Any]) -> tuple[Any, np.ndarray]:
    """Seeded synthetic tracks passing near the city, with equal annual frequencies.

    Algorithm:
        $$f_k = \\Lambda / N,\\quad x_k \\sim U(-M, M),\\quad
        p_{min} = p_{env} - (v_{max}/c)^2$$

        ASCII: f_k = rate/n_tracks ; x_k ~ Uniform(-miss, +miss) ; pmin = 1010 - (vmax/c)^2

    $\\Lambda$ is ``annual_rate_per_year`` (1/yr), $N$ the track count, $M$ ``miss_distance_km``
    (km), $c$ ``pressure_wind_coeff`` (m/s per sqrt(hPa)), $p_{env}$ 1010 hPa. Each track aims
    at the city centre displaced $x_k$ east and starts ``start_distance_km`` back along its
    heading. Central pressure is set from $v_{max}$ by the Holland-type relation above,
    because CLIMADA's wind field is driven by the central pressure, not by the track's
    ``max_sustained_wind``.

    Args:
        spec: City spec with a ``typhoon`` section and the ``seed``.

    Returns:
        ``(TCTracks, frequency)`` with ``frequency`` in 1/year, one per track.
    """
    from climada.hazard import TCTracks

    ty = spec["typhoon"]
    rng = np.random.default_rng(int(spec["seed"]) + 7)
    lat_c, lon_c = _city_centre(spec)
    n = int(ty["n_tracks"])
    v_lo, v_hi = ty["vmax_ms"]
    coeff = float(ty["pressure_wind_coeff"])
    data = []
    for k in range(n):
        vmax = float(rng.uniform(v_lo, v_hi))
        rmax = float(rng.uniform(*ty["rmax_km"]))
        speed = float(rng.uniform(*ty["speed_kmh"]))
        heading = float(rng.uniform(*ty["heading_deg"]))
        offset_km = float(rng.uniform(-ty["miss_distance_km"], ty["miss_distance_km"]))
        pmin = ENV_PRESSURE_HPA - (vmax / coeff) ** 2
        theta = math.radians(heading)
        d = float(ty["start_distance_km"])
        north_km = -d * math.cos(theta)
        east_km = offset_km - d * math.sin(theta)
        lat0 = lat_c + north_km / KM_PER_DEG_LAT
        lon0 = lon_c + east_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat_c)))
        data.append(
            make_track(
                lat0,
                lon0,
                heading_deg=heading,
                speed_kmh=speed,
                vmax_ms=vmax,
                pmin_hpa=pmin,
                rmax_km=rmax,
                hours=int(ty["hours"]),
                name=f"SYN{k + 1:03d}",
            )
        )
        data[-1].attrs["id_no"] = k + 1
    frequency = np.full(n, float(ty["annual_rate_per_year"]) / n)
    return TCTracks(data=data), frequency


def wind_hazard(lat: Any, lon: Any, tracks: Any, frequency: Any) -> Any:
    """CLIMADA ``TropCyclone`` hazard (``TC``, m/s) at the given points.

    Args:
        lat: Point latitudes (deg).
        lon: Point longitudes (deg).
        tracks: A ``TCTracks`` object.
        frequency: Annual frequency per track (1/year).

    Returns:
        The ``Hazard``; ``intensity`` is the peak 1-min sustained wind (m/s), shape
        ``(n_tracks, n_points)``. No coast-distance download is attempted.
    """
    from climada.hazard import Centroids, TropCyclone

    hazard = TropCyclone.from_tracks(
        tracks,
        centroids=Centroids(lat=np.asarray(lat, float), lon=np.asarray(lon, float)),
        ignore_distance_to_coast=True,
    )
    hazard.frequency = np.asarray(frequency, dtype=float)
    return hazard


def build_tc_hazard(assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """Wind hazard of the synthetic tracks on the city's asset points."""
    tracks, frequency = synthetic_tracks(spec)
    return wind_hazard(
        [a["latitude"] for a in assets], [a["longitude"] for a in assets], tracks, frequency
    )


def price_tc(assets: list[dict[str, Any]], hazard: Any) -> list[Any]:
    """One engine ``ResultRow`` per asset for the TC hazard (Korea, region WP4)."""
    from climaterisk_worker.physical_risk import engine

    rows = []
    for a in assets:
        facility = {k: v for k, v in a.items() if k not in ("node", "edge", "ground_m", "kind")}
        rows.append(
            engine.calculate(
                facility,
                hazard,
                "TC",
                model_id=MODEL_ID,
                iso3="KOR",
                hazard_source="synthetic tracks (synthetic_city_spec.json)",
                hazard_dataset="synthetic_city",
                exposure_source="synthetic virtual city (NetworkX grid)",
            )
        )
    return rows
