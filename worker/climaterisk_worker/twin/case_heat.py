"""Street-level heat twin of a real site (Yeongdeungpo 쪽방촌): shade, SVF, MRT, UTCI.

A 5 m grid over a real neighbourhood is built from OpenStreetMap footprints. Buildings
without an OSM ``height`` or ``building:levels`` tag keep height ``None`` (never a default
storey count); the main run lets them cast no shadow and two explicit sensitivity runs set them
low and high. The main output is UTCI at the hour of the daily maximum for each chosen day.
The sub check ranks modelled UTCI against the observed S-DoT air temperature at the sensor
cells (diagnostic only: it records a warning, it never changes the result). Indoor
temperature is not modelled.

Algorithm:
    Sky-view factor from 24 horizon angles $\\theta_k$ (Dozier and Frew 1990):
    $$\\mathrm{SVF}=\\frac{1}{N}\\sum_{k=1}^{N}\\cos^2\\theta_k$$
    ASCII: SVF = mean_k cos(theta_k)^2

    Cast shadow: an open cell is shaded when a building at distance $d$ toward the sun has
    $H > d\\tan\\beta$. Clear-sky global irradiance (Haurwitz 1945) with a fixed diffuse share:
    $$G = 1098\\cos z\\,e^{-0.059/\\cos z},\\quad D=f_d G,\\quad I_{dn}=(G-D)/\\sin\\beta$$
    ASCII: G = 1098 cos(z) exp(-0.059 / cos(z)); D = f_d G; I_dn = (G - D) / sin(beta)

    Mean radiant temperature of a standing person (Lindberg et al. 2008 weights, simplified):
    $$K=a_k\\bigl(I_{dn}f_p s + \\tfrac12 D\\,\\mathrm{SVF}+\\tfrac12 K_{ref}\\bigr),\\quad
    L=a_l\\,\\tfrac12\\bigl(\\varepsilon_{sky}\\sigma T_a^4\\mathrm{SVF}
    +\\varepsilon_s\\sigma T_{env}^4(1-\\mathrm{SVF})+\\varepsilon_s\\sigma T_g^4\\bigr)$$
    $$T_{mrt}=\\bigl((K+L)/(a_l\\sigma)\\bigr)^{1/4}-273.15$$
    ASCII: K = a_k (I_dn f_p s + D SVF / 2 + K_ref / 2);
    L = a_l (eps_sky sig Ta^4 SVF + eps_s sig Tenv^4 (1 - SVF) + eps_s sig Tg^4) / 2;
    Tmrt = ((K + L) / (a_l sig))^0.25 - 273.15

    $\\beta$ solar elevation (deg), $z$ zenith angle, $s$ sunlit (0/1), $f_p$ projected-area
    factor (-), $a_k,a_l$ short/long-wave absorptivity (-), $T_a,T_{env},T_g$ air, wall and
    ground temperature (K), $\\varepsilon$ emissivity (-), $\\sigma$ Stefan-Boltzmann
    (W m-2 K-4), $K,L$ absorbed flux (W m-2), $H$ building height (m). Then
    $\\mathrm{UTCI}=\\mathrm{UTCI}(T_a,T_{mrt},v,\\mathrm{RH})$ (Brode et al. 2012).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from climaterisk_worker.twin.site import (
    M_PER_DEG,
    SiteGrid,
    _height_from_tag,
    _levels_from_tag,
    _parse_tags,
)

logger = logging.getLogger(__name__)

SIGMA = 5.670374419e-8  # Stefan-Boltzmann, W m-2 K-4
KST_MERIDIAN_DEG = 135.0
N_HORIZON_AZIMUTHS = 24

# ----------------------------------------------------------------------------- gate API


def building_height(tags: dict[str, str], floor_to_floor_m: float) -> float | None:
    """Height (m) from OSM tags; ``None`` when neither height nor levels is usable.

    Order: ``height`` ("12", "12 m", "12.5m") wins; else ``building:levels`` times
    ``floor_to_floor_m``; else ``None``. No typology default is ever applied.

    Algorithm:
        $$H = h_{tag}\\ \\text{or}\\ n_{levels}\\,\\Delta_{ff}\\ \\text{or}\\ \\varnothing$$
        ASCII: H = height_tag, else levels * floor_to_floor, else None

        $h_{tag}$ OSM height (m), $n_{levels}$ storeys (-), $\\Delta_{ff}$ floor-to-floor (m).

    Args:
        tags: OSM tag dictionary.
        floor_to_floor_m: Assumed storey height (m).

    Returns:
        Height in metres or ``None`` (unknown).
    """
    h = _height_from_tag(tags.get("height"))
    if h is not None:
        return h
    n = _levels_from_tag(tags.get("building:levels"))
    return None if n is None else float(n * floor_to_floor_m)


def rank_check(model: np.ndarray, obs: np.ndarray, min_spearman: float) -> dict[str, Any]:
    """Spearman rank correlation of model vs observation over sensors finite in both.

    Algorithm:
        $$\\rho = 1 - \\frac{6\\sum d_i^2}{n(n^2-1)}$$ (average ranks for ties)
        ASCII: rho = Spearman(rank(model), rank(obs))

        $d_i$ rank difference at sensor $i$, $n$ number of sensors (-).

    Args:
        model: Modelled value per sensor.
        obs: Observed value per sensor.
        min_spearman: Warn below this $\\rho$.

    Returns:
        ``{"spearman", "n", "warning"}``; warning when $\\rho$ < ``min_spearman``, $n$ < 3 or
        $\\rho$ undefined (a constant input); ``spearman`` is ``None`` then if undefined.
    """
    m = np.asarray(model, dtype=float)
    o = np.asarray(obs, dtype=float)
    ok = np.isfinite(m) & np.isfinite(o)
    n = int(ok.sum())
    rho: float | None = None
    if n >= 3:
        with np.errstate(all="ignore"):
            r = float(spearmanr(m[ok], o[ok]).statistic)
        rho = r if math.isfinite(r) else None
    warning = bool(n < 3 or rho is None or rho < min_spearman)
    return {"spearman": rho, "n": n, "warning": warning}


def utci(tdb: Any, tr: Any, v: Any, rh: Any) -> np.ndarray:
    """UTCI (degC) with pythermalcomfort (Brode et al. 2012), vectorised.

    Algorithm:
        $$\\mathrm{UTCI}=T_a+f(T_a,\\,T_{mrt}-T_a,\\,v,\\,e)$$
        ASCII: UTCI = Ta + polynomial(Ta, Tmrt - Ta, v, vapour pressure)

        $T_a$ air temperature (degC), $T_{mrt}$ mean radiant temperature (degC), $v$ wind at
        10 m (m/s, 0.5-17), $e$ vapour pressure from RH (%); outside the validity range the
        result is NaN.

    Args:
        tdb: Air temperature (degC), scalar or array.
        tr: Mean radiant temperature (degC).
        v: Wind speed at 10 m (m/s).
        rh: Relative humidity (%).

    Returns:
        UTCI (degC) in the broadcast shape (NaN outside the validity range).
    """
    from pythermalcomfort.models import utci as _utci

    t, r, w, h = np.broadcast_arrays(*(np.asarray(x, dtype=float) for x in (tdb, tr, v, rh)))
    out = _utci(tdb=t.ravel(), tr=r.ravel(), v=w.ravel(), rh=h.ravel(), round_output=False)
    return np.asarray(out.utci, dtype=float).reshape(t.shape)


# ----------------------------------------------------------------------------- S-DoT


def _read_locations(path: Path) -> pd.DataFrame:
    """S-DoT sensor table with ``serial``, ``lat``, ``lon``, ``address`` and old serials."""
    loc = pd.read_excel(path)
    out = pd.DataFrame(
        {
            "serial": loc["모델 시리얼(*)"],
            "address": loc["주소"],
            "lat": loc["위도"],
            "lon": loc["경도"],
            "old_serial": loc["변경 전 시리얼(데이터 상 표기)"],
        }
    )
    return out.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def distance_m(lat: Any, lon: Any, lat0: float, lon0: float) -> np.ndarray:
    """Flat-earth distance (m) from ``(lat0, lon0)``."""
    dy = (np.asarray(lat, dtype=float) - lat0) * M_PER_DEG
    dx = (np.asarray(lon, dtype=float) - lon0) * M_PER_DEG * math.cos(math.radians(lat0))
    return np.hypot(dx, dy)


def load_sdot(
    spec: dict[str, Any], data_root: Path, cache: Path | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly S-DoT air temperature and humidity for sensors within the ring outer radius.

    Reads the weekly cp949 CSVs (columns 시리얼, 센서 시간, 평균 기온, 평균 상대습도) and keeps
    sensors within ``ring_outer_m`` of the site centre. Implausible temperatures (outside
    5-50 degC in summer) are dropped. A parquet cache of the subset is kept under DATA_ROOT.

    Args:
        spec: Case spec.
        data_root: ``CLIMATERISK_DATA_ROOT``.
        cache: Optional cache path of the filtered hourly rows.

    Returns:
        ``(sensors, hourly)``: sensors with ``dist_m`` and ``zone`` (site/ring/other), and
        hourly rows with ``serial`` (current serial), ``date``, ``hour``, ``t``, ``rh``.
    """
    sd = spec["sdot"]
    west, south, east, north = spec["site"]["bbox"]
    lat0, lon0 = (south + north) / 2, (west + east) / 2
    base = data_root / "external" / "seoul_sdot" / "raw"
    sensors = _read_locations(base / sd["locations_file"])
    sensors["dist_m"] = distance_m(sensors["lat"], sensors["lon"], lat0, lon0)
    sensors = sensors[sensors["dist_m"] <= sd["ring_outer_m"]].copy()
    sensors["zone"] = np.where(sensors["dist_m"] <= sd["radius_m"], "site", "ring")
    to_new = {
        o: s
        for s, o in zip(sensors["serial"], sensors["old_serial"], strict=True)
        if isinstance(o, str)
    }
    keep = set(sensors["serial"]) | set(to_new)
    if cache is not None and cache.exists():
        hourly = pd.read_parquet(cache)
        logger.info("S-DoT hourly cache %s rows=%d", cache.name, len(hourly))
        return sensors.reset_index(drop=True), hourly
    frames = []
    for f in sorted(base.glob(sd["files_glob"])):
        d = pd.read_csv(f, encoding="cp949", usecols=[1, 2, 7, 10], low_memory=False)
        d.columns = ["serial", "stamp", "t", "rh"]
        d = d[d["serial"].isin(keep)].copy()
        d["t"] = pd.to_numeric(d["t"], errors="coerce")
        d["rh"] = pd.to_numeric(d["rh"], errors="coerce")
        frames.append(d)
        logger.debug("%s kept rows=%d", f.name, len(d))
    hourly = pd.concat(frames, ignore_index=True)
    hourly["serial"] = hourly["serial"].replace(to_new)
    stamp = pd.to_datetime(hourly["stamp"], format="%Y-%m-%d_%H:%M:%S", errors="coerce")
    hourly = hourly[stamp.notna()].copy()  # a few rows use a malformed stamp (HH-MM-SS)
    stamp = stamp[stamp.notna()]
    hourly["date"] = stamp.dt.strftime("%Y-%m-%d")
    hourly["hour"] = stamp.dt.hour.astype(int)
    hourly = hourly.drop(columns="stamp")
    hourly.loc[(hourly["t"] < 5) | (hourly["t"] > 50), "t"] = np.nan
    hourly = hourly.drop_duplicates(["serial", "date", "hour"])
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        hourly.to_parquet(cache)
    logger.info("S-DoT hourly rows=%d sensors=%d", len(hourly), hourly["serial"].nunique())
    return sensors.reset_index(drop=True), hourly


def ring_profile(sensors: pd.DataFrame, hourly: pd.DataFrame, min_sensors: int) -> pd.DataFrame:
    """Median of the ring sensors per date and hour (temperature, humidity, sensor count).

    Args:
        sensors: Sensor table from :func:`load_sdot`.
        hourly: Hourly rows from :func:`load_sdot`.
        min_sensors: Hours with fewer reporting ring sensors are dropped.

    Returns:
        Frame indexed by ``(date, hour)`` with ``t``, ``rh``, ``n``.
    """
    ring = set(sensors.loc[sensors["zone"] == "ring", "serial"])
    h = hourly[hourly["serial"].isin(ring)]
    g = h.groupby(["date", "hour"]).agg(t=("t", "median"), rh=("rh", "median"), n=("t", "count"))
    return g[g["n"] >= min_sensors]


def choose_days(
    sensors: pd.DataFrame,
    hourly: pd.DataFrame,
    n_days: int,
    min_sensors: int,
    hour_range: tuple[int, int],
) -> list[dict[str, Any]]:
    """Hottest days by the ring-median daily maximum, with the hour of that maximum.

    The rule uses only the 1-3 km ring sensors (never the site sensors, never the model).

    Args:
        sensors: Sensor table.
        hourly: Hourly rows.
        n_days: Number of days to return.
        min_sensors: Minimum ring sensors reporting in an hour.
        hour_range: Inclusive local hours searched for the daily maximum.

    Returns:
        Hottest-first list of ``{"date", "hour", "ring_tmax_c"}``.
    """
    prof = ring_profile(sensors, hourly, min_sensors).reset_index()
    prof = prof[(prof["hour"] >= hour_range[0]) & (prof["hour"] <= hour_range[1])]
    idx = prof.groupby("date")["t"].idxmax()
    best = prof.loc[idx].sort_values("t", ascending=False).head(n_days)
    return [
        {"date": r.date, "hour": int(r.hour), "ring_tmax_c": round(float(r.t), 2)}
        for r in best.itertuples()
    ]


# ----------------------------------------------------------------------------- sun


def solar_position(lat_deg: float, lon_deg: float, when: dt.datetime) -> tuple[float, float]:
    """Solar elevation and azimuth (deg, azimuth clockwise from north) for KST clock time.

    Algorithm:
        $$\\sin\\beta=\\sin\\varphi\\sin\\delta+\\cos\\varphi\\cos\\delta\\cos h,\\quad
        h=15(t_{sol}-12),\\quad t_{sol}=t_{KST}+\\tfrac{4(\\lambda-135)+E_t}{60}$$
        ASCII: sin(beta) = sin(lat) sin(dec) + cos(lat) cos(dec) cos(h); h = 15 (t_sol - 12)

        $\\varphi$ latitude, $\\delta$ declination (Cooper 1969), $\\lambda$ longitude (deg),
        $E_t$ equation of time (min, Spencer fit), $t$ in hours.

    Args:
        lat_deg: Latitude (deg).
        lon_deg: Longitude (deg east).
        when: Naive local (KST, UTC+9) datetime.

    Returns:
        ``(elevation_deg, azimuth_deg)``.
    """
    n = when.timetuple().tm_yday
    dec = math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (284 + n))))
    b = 2 * math.pi * (n - 1) / 365.0
    eot = 229.18 * (
        0.000075
        + 0.001868 * math.cos(b)
        - 0.032077 * math.sin(b)
        - 0.014615 * math.cos(2 * b)
        - 0.04089 * math.sin(2 * b)
    )
    t_clock = when.hour + when.minute / 60.0 + when.second / 3600.0
    t_sol = t_clock + (4.0 * (lon_deg - KST_MERIDIAN_DEG) + eot) / 60.0
    h = math.radians(15.0 * (t_sol - 12.0))
    lat = math.radians(lat_deg)
    sin_b = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(h)
    beta = math.asin(max(-1.0, min(1.0, sin_b)))
    cos_az = (math.sin(dec) - sin_b * math.sin(lat)) / max(1e-9, math.cos(beta) * math.cos(lat))
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if h > 0:
        az = 360.0 - az
    return math.degrees(beta), az


# ----------------------------------------------------------------------------- grid physics


def _shifted(padded: np.ndarray, di: int, dj: int, pad: int, shape: tuple[int, int]) -> np.ndarray:
    """View of ``padded`` shifted by ``(di, dj)`` cells (row north, column east)."""
    return padded[pad + di : pad + di + shape[0], pad + dj : pad + dj + shape[1]]


def _pad_heights(height: np.ndarray, reach_cells: int) -> np.ndarray:
    return np.pad(height, reach_cells, mode="constant", constant_values=0.0)


def sky_view_factor_grid(height: np.ndarray, cell_m: float, max_dist_m: float) -> np.ndarray:
    """Sky-view factor per cell from horizon angles over ``N_HORIZON_AZIMUTHS`` directions.

    Algorithm:
        $$\\theta_k=\\max_d\\arctan(H(\\mathbf{x}+d\\hat e_k)/d),\\quad
        \\mathrm{SVF}=\\overline{\\cos^2\\theta_k}$$
        ASCII: theta_k = max_d atan(H(x + d e_k) / d); SVF = mean(cos(theta_k)^2)

        $H$ building height (m), $d$ distance (m), $\\hat e_k$ unit vector of azimuth $k$.
        For an infinite street of width $W$ between walls of height $H$ this equals
        $1/\\sqrt{1+(2H/W)^2}$ at the centre (Oke 1987).

    Args:
        height: ``(ny, nx)`` obstacle height (m), 0 where open.
        cell_m: Cell size (m).
        max_dist_m: Horizon search distance (m).

    Returns:
        SVF array of the same shape (values at building cells are not meaningful).
    """
    shape = height.shape
    steps = max(1, int(max_dist_m // cell_m))
    pad = steps + 1
    hp = _pad_heights(height, pad)
    acc = np.zeros(shape)
    for k in range(N_HORIZON_AZIMUTHS):
        az = 2 * math.pi * k / N_HORIZON_AZIMUTHS
        tan_max = np.zeros(shape)
        for s in range(1, steps + 1):
            d = s * cell_m
            di = round(d * math.cos(az) / cell_m)
            dj = round(d * math.sin(az) / cell_m)
            tan_max = np.maximum(tan_max, _shifted(hp, di, dj, pad, shape) / d)
        acc += 1.0 / (1.0 + tan_max**2)
    return acc / N_HORIZON_AZIMUTHS


def sunlit_mask(
    height: np.ndarray, cell_m: float, elevation_deg: float, azimuth_deg: float, max_dist_m: float
) -> np.ndarray:
    """True where the ground is in direct sun (no building toward the sun blocks it).

    Algorithm:
        $$\\text{shaded}\\iff\\exists d:\\ H(\\mathbf{x}+d\\hat s)>d\\tan\\beta$$
        ASCII: shaded if any d: H(x + d * s_hat) > d * tan(beta)

        $\\hat s$ horizontal unit vector toward the sun, $\\beta$ solar elevation (deg).

    Args:
        height: ``(ny, nx)`` obstacle height (m).
        cell_m: Cell size (m).
        elevation_deg: Solar elevation (deg); at or below 0 everything is shaded.
        azimuth_deg: Solar azimuth (deg clockwise from north).
        max_dist_m: Search distance (m).

    Returns:
        Boolean ``(ny, nx)`` array.
    """
    shape = height.shape
    if elevation_deg <= 0:
        return np.zeros(shape, dtype=bool)
    steps = max(1, int(max_dist_m // cell_m))
    pad = steps + 1
    hp = _pad_heights(height, pad)
    az = math.radians(azimuth_deg)
    tan_b = math.tan(math.radians(elevation_deg))
    blocked = np.zeros(shape, dtype=bool)
    for s in range(1, steps + 1):
        d = s * cell_m
        di = round(d * math.cos(az) / cell_m)
        dj = round(d * math.sin(az) / cell_m)
        blocked |= _shifted(hp, di, dj, pad, shape) > d * tan_b
    return ~blocked


def clear_sky_irradiance(
    elevation_deg: float, diffuse_fraction: float
) -> tuple[float, float, float]:
    """Haurwitz (1945) clear-sky global, diffuse and direct-normal irradiance (W m-2).

    Algorithm:
        $$G=1098\\cos z\\,e^{-0.059/\\cos z},\\quad D=f_dG,\\quad I_{dn}=(G-D)/\\sin\\beta$$
        ASCII: G = 1098 cos(z) exp(-0.059/cos(z)); D = f_d G; I_dn = (G - D) / sin(beta)

    Args:
        elevation_deg: Solar elevation $\\beta$ (deg).
        diffuse_fraction: $f_d$ (-), share of $G$ that is diffuse.

    Returns:
        ``(G, D, I_dn)`` in W m-2 (zeros when the sun is below the horizon).
    """
    sin_b = math.sin(math.radians(elevation_deg))
    if sin_b <= 0.02:
        return 0.0, 0.0, 0.0
    g = 1098.0 * sin_b * math.exp(-0.059 / sin_b)
    d = diffuse_fraction * g
    return g, d, (g - d) / sin_b


def sky_emissivity(t_air_c: float, rh_pct: float) -> float:
    """Clear-sky emissivity (Prata 1996) from air temperature and humidity.

    Algorithm:
        $$\\varepsilon=1-(1+w)\\exp(-\\sqrt{1.2+3w}),\\quad w=46.5\\,e/T$$
        ASCII: eps = 1 - (1 + w) exp(-sqrt(1.2 + 3 w)); w = 46.5 e / T

        $e$ vapour pressure (hPa), $T$ air temperature (K).

    Args:
        t_air_c: Air temperature (degC).
        rh_pct: Relative humidity (%).

    Returns:
        Emissivity (-).
    """
    e = rh_pct / 100.0 * 6.112 * math.exp(17.62 * t_air_c / (243.12 + t_air_c))
    w = 46.5 * e / (t_air_c + 273.15)
    return 1.0 - (1.0 + w) * math.exp(-math.sqrt(1.2 + 3.0 * w))


def mean_radiant_temperature_grid(
    t_air_c: np.ndarray,
    svf: np.ndarray,
    sunlit: np.ndarray,
    albedo: np.ndarray,
    ground_excess_sun_c: np.ndarray,
    ground_excess_shade_c: np.ndarray,
    elevation_deg: float,
    rh_pct: float,
    p: dict[str, float],
) -> np.ndarray:
    """Mean radiant temperature (degC) of a standing person per cell (radiation balance).

    See the module docstring for the balance. Ground temperature is
    $T_g=T_a+\\Delta T_{sun}$ on sunlit cells and $T_a+\\Delta T_{shade}$ otherwise.

    Algorithm:
        $$T_g=T_a+s\\,\\Delta T_{sun}+(1-s)\\,\\Delta T_{shade},\\quad
        K_{ref}=\\alpha(s\\,I_{dn}\\sin\\beta+D\\,\\mathrm{SVF})$$
        ASCII: Tg = Ta + s dT_sun + (1 - s) dT_shade; Kref = albedo (s I_dn sin(beta) + D SVF)

    Args:
        t_air_c: Air temperature (degC), array.
        svf: Sky-view factor (-).
        sunlit: Sunlit cells (bool).
        albedo: Surface albedo (-).
        ground_excess_sun_c: Surface-minus-air excess in sun (K).
        ground_excess_shade_c: Surface-minus-air excess in shade (K).
        elevation_deg: Solar elevation (deg).
        rh_pct: Relative humidity (%).
        p: Model constants (``spec["model"]``).

    Returns:
        Mean radiant temperature (degC), same shape as ``t_air_c``.
    """
    _g, d, i_dn = clear_sky_irradiance(elevation_deg, p["diffuse_fraction"])
    sin_b = math.sin(math.radians(max(elevation_deg, 0.0)))
    s = sunlit.astype(float)
    alpha = max(elevation_deg, 0.0)
    f_p = 0.308 * math.cos(math.radians(alpha * (0.998 - alpha**2 / 50000.0)))
    k_ref = albedo * (s * i_dn * sin_b + d * svf)
    k_abs = p["abs_short"] * (i_dn * f_p * s + 0.5 * d * svf + 0.5 * k_ref)
    t_k = t_air_c + 273.15
    t_ground = t_k + s * ground_excess_sun_c + (1 - s) * ground_excess_shade_c
    t_env = t_k + p["wall_excess_c"]
    eps_sky = sky_emissivity(float(np.nanmean(t_air_c)), rh_pct)
    l_abs = (
        p["abs_long"]
        * 0.5
        * SIGMA
        * (
            eps_sky * t_k**4 * svf
            + p["emissivity_surface"] * t_env**4 * (1 - svf)
            + p["emissivity_surface"] * t_ground**4
        )
    )
    return ((k_abs + l_abs) / (p["abs_long"] * SIGMA)) ** 0.25 - 273.15


# ----------------------------------------------------------------------------- site data


def read_site_buildings(
    osm_path: Path, bbox: tuple[float, float, float, float], floor_to_floor_m: float
) -> Any:
    """OSM building footprints with ``height_m`` (None = unknown) and its source.

    Underground structures (``layer`` < 0, ``location=underground``, ``tunnel=yes``) and open
    structures (roof, carport, canopy, bridge) are dropped: they are not solid walls.

    Args:
        osm_path: OSM pbf.
        bbox: ``(west, south, east, north)``.
        floor_to_floor_m: Assumed storey height (m).

    Returns:
        GeoDataFrame (EPSG:4326) with ``height_m`` (NaN when unknown) and ``height_source``
        in {"height", "levels", "unknown"}.
    """
    import geopandas as gpd
    import pyogrio

    from climaterisk_worker.twin.site import NOT_WALLS

    raw = pyogrio.read_dataframe(
        str(osm_path), layer="multipolygons", bbox=bbox, columns=["building", "other_tags"]
    )
    raw = raw[raw["building"].notna() & ~raw.geometry.isna()].reset_index(drop=True)
    heights: list[float] = []
    sources: list[str] = []
    keep: list[bool] = []
    for kind, other in zip(raw["building"], raw["other_tags"], strict=True):
        tags = _parse_tags(other)
        try:
            layer = float(tags.get("layer", "0"))
        except ValueError:
            layer = 0.0
        solid = not (
            layer < 0
            or tags.get("location") == "underground"
            or tags.get("tunnel") == "yes"
            or str(kind) in NOT_WALLS
        )
        keep.append(solid)
        h = building_height(tags, floor_to_floor_m)
        heights.append(float("nan") if h is None else h)
        sources.append(
            "unknown"
            if h is None
            else ("height" if _height_from_tag(tags.get("height")) else "levels")
        )
    out = gpd.GeoDataFrame(
        {"height_m": heights, "height_source": sources},
        geometry=raw.geometry.values,
        crs="EPSG:4326",
    )
    return out[np.asarray(keep)].reset_index(drop=True)


def rasterise_ids(grid: SiteGrid, geoms: Any, values: Any | None = None) -> np.ndarray:
    """Integer raster ``(ny, nx)`` of polygon index + 1 (0 = none); later polygons win.

    Args:
        grid: Site grid.
        geoms: Iterable of shapely geometries in EPSG:4326.
        values: Optional per-geometry values to burn instead of index + 1.

    Returns:
        Array (int, or float if ``values`` given), row ``i`` northward.
    """
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    dlon = grid.cell_m / grid.lon_m_per_deg
    dlat = grid.cell_m / M_PER_DEG
    transform = from_origin(grid.west, grid.south + grid.ny * dlat, dlon, dlat)
    geoms = list(geoms)
    vals = values if values is not None else range(1, len(geoms) + 1)
    shapes = [(g, v) for g, v in zip(geoms, vals, strict=True) if g is not None and not g.is_empty]
    dtype = "float64" if values is not None else "int32"
    arr = rasterize(shapes, out_shape=(grid.ny, grid.nx), transform=transform, fill=0, dtype=dtype)
    return np.asarray(arr)[::-1]


def road_geometries(
    osm_path: Path, bbox: tuple[float, float, float, float], widths_m: dict[str, float]
) -> list[Any]:
    """Road polygons: OSM ``highway`` lines buffered by half the class width (assumption)."""
    import geopandas as gpd
    import pyogrio

    raw = pyogrio.read_dataframe(str(osm_path), layer="lines", bbox=bbox, columns=["highway"])
    raw = raw[raw["highway"].isin(list(widths_m))]
    if len(raw) == 0:
        return []
    metric = gpd.GeoSeries(raw.geometry.values, crs="EPSG:4326").to_crs("EPSG:5179")
    half = raw["highway"].map(widths_m).to_numpy(dtype=float) / 2.0
    poly = gpd.GeoSeries(
        [g.buffer(h) for g, h in zip(metric, half, strict=True)], crs="EPSG:5179"
    ).to_crs("EPSG:4326")
    return list(poly)


def sample_at_sensors(
    grid: SiteGrid,
    field: np.ndarray,
    open_mask: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    radius_m: float,
) -> np.ndarray:
    """Mean of ``field`` over open cells within ``radius_m`` of each sensor (NaN if none).

    Args:
        grid: Site grid.
        field: ``(ny, nx)`` values.
        open_mask: True where the cell is open ground.
        lat: Sensor latitudes (deg).
        lon: Sensor longitudes (deg).
        radius_m: Search radius (m).

    Returns:
        One value per sensor.
    """
    out = np.full(len(lat), np.nan)
    r = math.ceil(radius_m / grid.cell_m)
    for k, (la, lo) in enumerate(zip(lat, lon, strict=True)):
        i0 = int((la - grid.south) * M_PER_DEG / grid.cell_m)
        j0 = int((lo - grid.west) * grid.lon_m_per_deg / grid.cell_m)
        i1, i2 = max(0, i0 - r), min(grid.ny, i0 + r + 1)
        j1, j2 = max(0, j0 - r), min(grid.nx, j0 + r + 1)
        if i1 >= i2 or j1 >= j2:
            continue
        ii, jj = np.mgrid[i1:i2, j1:j2]
        near = np.hypot((ii - i0) * grid.cell_m, (jj - j0) * grid.cell_m) <= radius_m
        sel = near & open_mask[i1:i2, j1:j2]
        if sel.any():
            out[k] = float(np.nanmean(field[i1:i2, j1:j2][sel]))
    return out


def local_fraction(mask: np.ndarray, cell_m: float, radius_m: float) -> np.ndarray:
    """Share of ``mask`` cells inside a square window of half-width ``radius_m``."""
    from scipy.ndimage import uniform_filter

    size = int(2 * round(radius_m / cell_m) + 1)
    return uniform_filter(mask.astype(float), size=size, mode="nearest")


def run_scenario(
    grid: SiteGrid,
    height: np.ndarray,
    surface: dict[str, np.ndarray],
    open_mask: np.ndarray,
    green: np.ndarray,
    background: dict[str, dict[str, float]],
    sun: dict[str, tuple[float, float]],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """One building-height scenario: SVF, then per chosen day sunlit, MRT, Ta and UTCI.

    Air temperature is the background plus ``a_built f_built + a_green f_green`` (both
    coefficients 0 unless the spec cites a published value), so with zero coefficients all
    spatial structure in UTCI is radiative.

    Args:
        grid: Site grid.
        height: Obstacle height raster (m).
        surface: ``albedo``, ``dT_sun``, ``dT_shade`` rasters.
        open_mask: Open-ground cells.
        green: Green mask.
        background: ``date -> {"t": Ta degC, "rh": %}``.
        sun: ``date -> (elevation, azimuth)``.
        spec: Case spec.

    Returns:
        Dict with ``svf`` and per-date ``utci``, ``t_air``, ``mrt``, ``sunlit`` arrays.
    """
    p = spec["model"]
    reach = p["shade_reach_m"]
    svf = sky_view_factor_grid(height, grid.cell_m, reach)
    f_built = local_fraction(~open_mask, grid.cell_m, p["offset_radius_m"])
    f_green = local_fraction(green, grid.cell_m, p["offset_radius_m"])
    offset = p["t_air_offset_per_built_fraction_c"] * f_built + (
        p["t_air_offset_per_green_fraction_c"] * f_green
    )
    out: dict[str, Any] = {"svf": svf, "days": {}}
    for date, (elev, az) in sun.items():
        lit = sunlit_mask(height, grid.cell_m, elev, az, reach)
        t_air = background[date]["t"] + offset
        mrt = mean_radiant_temperature_grid(
            t_air,
            svf,
            lit,
            surface["albedo"],
            surface["dT_sun"],
            surface["dT_shade"],
            elev,
            background[date]["rh"],
            p,
        )
        u = utci(t_air, mrt, p["wind_10m_ms"], background[date]["rh"])
        u = np.where(open_mask, u, np.nan)
        logger.debug("day %s utci shape=%s dtype=%s", date, u.shape, u.dtype)
        out["days"][date] = {"utci": u, "t_air": t_air, "mrt": mrt, "sunlit": lit}
    return out
