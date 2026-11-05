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


def pressure_for_vmax(vmax_ms: float, rmax_km: float, lat_deg: float) -> float:
    """Central pressure (hPa) at which CLIMADA's stationary wind field peaks at ``vmax_ms``.

    CLIMADA's Holland (2008) profile computes wind from the pressure deficit, so a fixed
    pressure-wind formula mislabels storms (a power law with c = 6.3 gave only 76-90 % of
    the nominal vmax). Instead the peak wind $W(p)$ is evaluated by CLIMADA itself for a
    ladder of pressures in one call and inverted by linear interpolation (W is monotone).

    Algorithm:
        $$W(p) = \\max_r v_{H08}(r;\\, p, r_{max}, \\varphi),\\qquad
          p_{min} = W^{-1}(v_{max})$$
        ASCII: W(p) = peak CLIMADA wind of a near-stationary storm; pmin = interp(vmax, W, p)

    $p$ central pressure (hPa), $r_{max}$ radius of maximum wind (km), $\\varphi$ latitude
    (deg), $v_{max}$ target peak 1-min sustained wind (m/s).

    Raises:
        ValueError: when ``vmax_ms`` is outside the winds reachable on the ladder.
    """
    from climada.hazard import TCTracks

    ladder = np.linspace(1008.0, 860.0, 75)
    tracks = [
        make_track(
            lat_deg,
            0.0,
            heading_deg=0.0,
            speed_kmh=0.1,
            vmax_ms=0.0,
            pmin_hpa=float(p),
            rmax_km=rmax_km,
            hours=6,
            name=f"P{i:03d}",
        )
        for i, p in enumerate(ladder)
    ]
    for i, t in enumerate(tracks):
        t.attrs["id_no"] = i + 1
    off_km = np.linspace(0.25 * rmax_km, 2.5 * rmax_km, 60)
    lon = off_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat_deg)))
    wind = wind_hazard(
        np.full(off_km.size, lat_deg), lon, TCTracks(data=tracks), np.ones(len(tracks))
    )
    peak = wind.intensity.toarray().max(axis=1)
    if not peak[0] <= vmax_ms <= peak[-1]:
        raise ValueError(f"vmax {vmax_ms} m/s outside reachable {peak[0]:.1f}-{peak[-1]:.1f}")
    return float(np.interp(vmax_ms, peak, ladder))


def synthetic_tracks(spec: dict[str, Any]) -> tuple[Any, np.ndarray]:
    """Seeded synthetic tracks passing near the city, with equal annual frequencies.

    Algorithm:
        $$f_k = \\Lambda / N,\\quad x_k \\sim U(-M, M),\\quad
        p_{min} = W^{-1}(v_{max})$$

        ASCII: f_k = rate/n_tracks ; x_k ~ Uniform(-miss, +miss) ; pmin = inverse of W at vmax

    $\\Lambda$ is ``annual_rate_per_year`` (1/yr), $N$ the track count, $M$ ``miss_distance_km``
    (km), $W(p)$ the peak wind (m/s) CLIMADA produces for a stationary storm of central
    pressure $p$ (hPa) — see :func:`pressure_for_vmax`. Each track aims at the city centre
    displaced $x_k$ east and starts ``start_distance_km`` back along its heading. CLIMADA's
    wind field is driven by the central pressure, not by the track's
    ``max_sustained_wind``, so the pressure is solved so that the storm really reaches
    $v_{max}$ (before the forward-motion asymmetry).

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
    data = []
    for k in range(n):
        vmax = float(rng.uniform(v_lo, v_hi))
        rmax = float(rng.uniform(*ty["rmax_km"]))
        speed = float(rng.uniform(*ty["speed_kmh"]))
        heading = float(rng.uniform(*ty["heading_deg"]))
        offset_km = float(rng.uniform(-ty["miss_distance_km"], ty["miss_distance_km"]))
        pmin = pressure_for_vmax(vmax, rmax, lat_c)
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
