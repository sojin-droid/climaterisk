"""Open-field UTCI (heat T2): hourly thermal stress of a person standing on open ground.

Design: ``<DATA_ROOT>/derived/design_utci/DESIGN_UTCI_T2.md`` (§4-§7, §10, §12), changed where
``tests/test_heat_utci.py`` says so (fp argument in degrees, MRT returned in degC, station table
= KMA 관측지점정보 history, ``mean_radiant_temperature_open`` takes split irradiance and cloud
fraction as arrays).

Main method
-----------
Per asset point: hourly KMA ASOS observations (air temperature, humidity, 10 m wind, global
solar radiation) of the nearest station; air temperature shifted to the asset by the 0.05 deg
MK-PRISM daily-maximum difference; mean radiant temperature (MRT) from the ERA5-HEAT formula
(Di Napoli et al. 2020); UTCI by ``pythermalcomfort`` (Brode et al. 2012). The headline is the
mean number of days per year (May-Sep, 2000-2019) whose daily-maximum UTCI is at least 38 degC.
Hazard only: no shade, buildings or indoors (that is tier T3), no loss.

Sub check (diagnostic only): agreement with the existing heat-day count (daily maximum air
temperature at or above 33 degC), three items per asset (see :func:`asset_check`).

The module is pure numpy/pandas on top; file and grid I/O is in the bottom section
(:func:`load_asos_station`, :func:`open_field_utci`). Heavy imports are deferred.

Verified coefficients: Erbs, Klein, Duffie 1982 (pvlib ``irradiance.erbs``); Di Napoli 2020
(thermofeel ``calculate_mean_radiant_temperature``). Not verified against the original
[미확인]: Crawford & Duchon 1999 cloud fraction ``1 - GHI/GHI_clear``, Oke 1987 albedo range,
ASOS ``icsr`` unit (taken as MJ m-2 accumulated over the preceding hour).
"""

from __future__ import annotations

import calendar
import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from climaterisk_worker.heat_days import monthly_mean

log = logging.getLogger(__name__)

SIGMA = 5.670374419e-8  # Stefan-Boltzmann, W m-2 K-4 (CODATA; same as twin.case_heat.SIGMA)
KST_MERIDIAN_DEG = 135.0  # standard meridian of KST (UTC+9), deg east
SOLAR_CONSTANT = 1361.0  # W m-2 (Kopp & Lean 2011)
MIN_SIN_ELEVATION = 0.05  # design value: below this the sun is treated as all-diffuse

ASSUMPTIONS = (
    "A1 grid daily-max difference added to every hour of the day",
    "A2 vapour pressure equal at asset and station",
    "A3 irradiance is the preceding-hour mean, sun position at the interval midpoint",
    "A4 night cloud fraction = that day's daytime mean",
    "A5 ground emissivity 0.95",
    "A6 ground albedo 0.20 (range 0.15-0.25 reported)",
    "A8 humidity, wind and radiation taken unchanged from the nearest station",
    "A9 anemometer height 10 m where the station table has none",
    "A7 (future) humidity, wind, radiation and the ground fit held at present values",
)


# =========================================================================== #
# 1. Pure physics                                                              #
# =========================================================================== #
def saturation_vapour_pressure(t_c: Any) -> Any:
    """Saturation vapour pressure over water (Magnus form).

    Algorithm:
        $$e_{sat}(T) = 6.112\\,\\exp\\!\\Big(\\frac{17.62\\,T}{243.12 + T}\\Big)$$
        ASCII: esat = 6.112 * exp(17.62 T / (243.12 + T))

        $T$ air temperature (degC); $e_{sat}$ in hPa. Same coefficients as
        ``twin.case_heat.sky_emissivity``.

    Args:
        t_c: Air temperature (degC), scalar or array.

    Returns:
        Saturation vapour pressure (hPa), same shape.
    """
    t = np.asarray(t_c, dtype=float)
    out = 6.112 * np.exp(17.62 * t / (243.12 + t))
    return out if out.ndim else out[()]


def shift_air_temperature(ta_c: Any, rh_pct: Any, delta_c: Any) -> tuple[np.ndarray, np.ndarray]:
    """Shift air temperature by ``delta_c`` and recompute humidity at constant vapour pressure.

    Algorithm:
        $$T' = T + \\Delta,\\quad e = \\frac{RH}{100} e_{sat}(T),\\quad
        RH' = \\min\\!\\Big(100,\\ 100\\,\\frac{e}{e_{sat}(T')}\\Big)$$
        ASCII: T2 = T + delta; e = RH/100 esat(T); RH2 = min(100, 100 e / esat(T2))

        $T$ air temperature (degC), $\\Delta$ shift (K), $RH$ relative humidity (%), $e$ vapour
        pressure (hPa).

    Args:
        ta_c: Air temperature (degC).
        rh_pct: Relative humidity (%).
        delta_c: Shift (K), scalar or array broadcastable to ``ta_c``.

    Returns:
        ``(ta_shifted_c, rh_shifted_pct)``.
    """
    ta = np.asarray(ta_c, dtype=float)
    rh = np.asarray(rh_pct, dtype=float)
    e = rh / 100.0 * saturation_vapour_pressure(ta)
    ta2 = ta + np.asarray(delta_c, dtype=float)
    with np.errstate(invalid="ignore"):
        rh2 = np.minimum(100.0, 100.0 * e / saturation_vapour_pressure(ta2))
    return ta2, rh2


def wind_to_10m(v_ms: Any, height_m: float, z0_m: float = 0.01) -> np.ndarray:
    """Convert a wind speed measured at ``height_m`` to 10 m with the log profile.

    Algorithm:
        $$v_{10} = v_{h}\\,\\frac{\\ln(10/z_0)}{\\ln(h/z_0)}$$
        ASCII: v10 = v * ln(10 / z0) / ln(h / z0)

        $v_h$ wind at the anemometer (m/s), $h$ anemometer height (m), $z_0$ roughness length
        (m). UTCI takes 10 m wind and reduces it to 1.1 m internally, so nothing else is done.

    Args:
        v_ms: Wind speed at the anemometer (m/s).
        height_m: Anemometer height above ground (m).
        z0_m: Roughness length (m), 0.01 (Brode et al. 2012).

    Returns:
        Wind speed at 10 m (m/s).
    """
    scale = math.log(10.0 / z0_m) / math.log(height_m / z0_m)
    return np.asarray(v_ms, dtype=float) * scale


def clip_wind(
    v10_ms: Any, low_ms: float = 0.5, high_ms: float = 17.0
) -> tuple[np.ndarray, np.ndarray]:
    """Clip 10 m wind to the UTCI validity range and report which hours were changed.

    Algorithm:
        $$v = \\min(\\max(v_{10},\\ 0.5),\\ 17)$$
        ASCII: v = min(max(v10, 0.5), 17)

        $v_{10}$ wind at 10 m (m/s); the limits are the pythermalcomfort UTCI validity range.

    Args:
        v10_ms: Wind speed at 10 m (m/s); NaN stays NaN.
        low_ms: Lower limit (m/s).
        high_ms: Upper limit (m/s).

    Returns:
        ``(clipped_wind_ms, clipped_mask)``; the mask is True where a value was changed.
    """
    v = np.asarray(v10_ms, dtype=float)
    with np.errstate(invalid="ignore"):
        mask = (v < low_ms) | (v > high_ms)
    return np.clip(v, low_ms, high_ms), mask


def _day_of_year(time_kst: np.ndarray) -> np.ndarray:
    t = np.asarray(time_kst).astype("datetime64[s]")
    year_start = t.astype("datetime64[Y]").astype("datetime64[D]")
    return (t.astype("datetime64[D]") - year_start).astype(np.int64) + 1


def solar_sin_elevation(lat_deg: float, lon_deg: float, time_kst: np.ndarray) -> np.ndarray:
    """Sine of the solar elevation for KST clock times; vector twin of ``case_heat.solar_position``.

    Algorithm:
        $$\\sin\\beta = \\sin\\varphi\\sin\\delta + \\cos\\varphi\\cos\\delta\\cos h,\\quad
        h = 15(t_{sol} - 12),\\quad t_{sol} = t_{KST} + \\frac{4(\\lambda - 135) + E_t}{60}$$
        ASCII: sin(beta) = sin(lat) sin(dec) + cos(lat) cos(dec) cos(h); h = 15 (t_sol - 12)

        $\\varphi$ latitude (deg), $\\lambda$ longitude (deg east), $\\delta$ declination
        (Cooper 1969, deg), $E_t$ equation of time (min, Spencer fit), $t$ in hours. The result
        is clipped to [-1, 1] as the scalar version does through ``asin``.

    Args:
        lat_deg: Latitude (deg).
        lon_deg: Longitude (deg east).
        time_kst: ``datetime64`` array of naive KST clock times.

    Returns:
        $\\sin\\beta$ (-), negative below the horizon.
    """
    t = np.asarray(time_kst).astype("datetime64[s]")
    n = _day_of_year(t).astype(float)
    dec = np.radians(23.45 * np.sin(np.radians(360.0 / 365.0 * (284.0 + n))))
    b = 2.0 * np.pi * (n - 1.0) / 365.0
    eot = 229.18 * (
        0.000075
        + 0.001868 * np.cos(b)
        - 0.032077 * np.sin(b)
        - 0.014615 * np.cos(2.0 * b)
        - 0.04089 * np.sin(2.0 * b)
    )
    seconds = (t - t.astype("datetime64[D]")).astype(np.int64)
    t_clock = seconds / 3600.0
    t_sol = t_clock + (4.0 * (lon_deg - KST_MERIDIAN_DEG) + eot) / 60.0
    h = np.radians(15.0 * (t_sol - 12.0))
    lat = math.radians(lat_deg)
    sin_b = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(h)
    return np.clip(sin_b, -1.0, 1.0)


def extraterrestrial_normal(doy: Any) -> np.ndarray:
    """Extraterrestrial normal irradiance with the eccentricity correction.

    Algorithm:
        $$I_0 = 1361\\,\\big(1 + 0.033\\cos(2\\pi n/365)\\big)$$
        ASCII: I0 = 1361 (1 + 0.033 cos(2 pi n / 365))

        $n$ day of year (-); $I_0$ in W m-2 (Duffie & Beckman).

    Args:
        doy: Day of year (1-366).

    Returns:
        $I_0$ (W m-2).
    """
    n = np.asarray(doy, dtype=float)
    return SOLAR_CONSTANT * (1.0 + 0.033 * np.cos(2.0 * np.pi * n / 365.0))


def erbs_diffuse_fraction(kt: Any) -> np.ndarray:
    """Diffuse fraction of global irradiance from the clearness index (Erbs et al. 1982).

    Algorithm:
        $$F_d = \\begin{cases} 1 - 0.09k_t & k_t \\le 0.22\\\\
        0.9511 - 0.1604k_t + 4.388k_t^2 - 16.638k_t^3 + 12.336k_t^4 & 0.22 < k_t \\le 0.80\\\\
        0.165 & k_t > 0.80 \\end{cases}$$
        ASCII: Fd = 1 - 0.09 kt (kt <= 0.22); quartic (0.22 < kt <= 0.8); 0.165 (kt > 0.8)

        $k_t$ clearness index (-), $F_d$ diffuse fraction (-). NaN stays NaN.

    Args:
        kt: Clearness index (-).

    Returns:
        Diffuse fraction (-).
    """
    k = np.asarray(kt, dtype=float)
    poly = 0.9511 - 0.1604 * k + 4.388 * k**2 - 16.638 * k**3 + 12.336 * k**4
    with np.errstate(invalid="ignore"):
        fd = np.where(k <= 0.22, 1.0 - 0.09 * k, np.where(k <= 0.80, poly, 0.165))
    return np.where(np.isnan(k), np.nan, fd)


def split_irradiance(
    ghi: Any, sin_beta: Any, i0: Any, min_sin: float = MIN_SIN_ELEVATION
) -> tuple[np.ndarray, np.ndarray]:
    """Split global horizontal irradiance into diffuse and beam-normal parts.

    Algorithm:
        $$k_t = \\mathrm{clip}\\Big(\\frac{G}{I_0\\sin\\beta},0,1\\Big),\\quad S_{dif} = F_d G,
        \\quad I_{dn} = \\min\\Big(\\frac{G - S_{dif}}{\\sin\\beta},\\ I_0\\Big)$$
        ASCII: kt = clip(G / (I0 sin b), 0, 1); S_dif = Fd G; I_dn = min((G - S_dif) / sin b, I0)

        $G$ global horizontal irradiance (W m-2), $I_0$ extraterrestrial normal irradiance
        (W m-2), $\\beta$ solar elevation; for $\\sin\\beta \\le 0.05$ the light is all diffuse
        ($S_{dif} = G$, $I_{dn} = 0$) to keep the division bounded.

    Args:
        ghi: Global horizontal irradiance (W m-2).
        sin_beta: Sine of the solar elevation (-).
        i0: Extraterrestrial normal irradiance (W m-2).
        min_sin: Elevation threshold on $\\sin\\beta$.

    Returns:
        ``(s_dif, i_dn)`` in W m-2.
    """
    g = np.maximum(np.asarray(ghi, dtype=float), 0.0)
    sb = np.asarray(sin_beta, dtype=float)
    i0a = np.asarray(i0, dtype=float)
    low = sb <= min_sin
    safe = np.where(low, 1.0, sb)
    kt = np.clip(g / (i0a * safe), 0.0, 1.0)
    s_dif = np.where(low, g, erbs_diffuse_fraction(kt) * g)
    i_dn = np.where(low, 0.0, np.minimum((g - s_dif) / safe, i0a))
    return s_dif, i_dn


def projected_area_factor(elev_deg: Any) -> np.ndarray:
    """Projected-area factor of a standing person for direct beam (as ECMWF thermofeel).

    Algorithm:
        $$f_p = 0.308\\cos\\big(\\gamma\\,(0.998 - \\gamma^2/50000)\\big)$$
        ASCII: fp = 0.308 cos(radians(g (0.998 - g^2 / 50000)))

        $\\gamma$ solar elevation (deg); the whole cosine argument is converted to radians.

    Args:
        elev_deg: Solar elevation (deg).

    Returns:
        $f_p$ (-).
    """
    g = np.asarray(elev_deg, dtype=float)
    return 0.308 * np.cos(np.radians(g * (0.998 - g**2 / 50000.0)))


def clear_sky_ghi(sin_beta: Any) -> np.ndarray:
    """Clear-sky global irradiance (Haurwitz 1945), zero when the sun is at or below the horizon.

    Algorithm:
        $$G_{clr} = 1098\\,\\sin\\beta\\,\\exp(-0.059/\\sin\\beta)$$
        ASCII: Gclr = 1098 sin(b) exp(-0.059 / sin(b))

        $\\beta$ solar elevation; $G_{clr}$ in W m-2. Zero for $\\sin\\beta \\le 0.02$, as
        ``twin.case_heat.clear_sky_irradiance``.

    Args:
        sin_beta: Sine of the solar elevation (-).

    Returns:
        $G_{clr}$ (W m-2).
    """
    sb = np.asarray(sin_beta, dtype=float)
    up = sb > 0.02
    safe = np.where(up, sb, 1.0)
    return np.where(up, 1098.0 * safe * np.exp(-0.059 / safe), 0.0)


def cloud_fraction(ghi: Any, ghi_clear: Any, sin_beta: Any, day_index: Any) -> np.ndarray:
    """Cloud fraction from the clear-sky index; night hours take the day's daytime mean.

    Algorithm:
        $$c = \\mathrm{clip}\\big(1 - G/G_{clr},\\,0,\\,1\\big)\\ (\\sin\\beta > 0.05),\\qquad
        c_{night} = \\overline{c_{day}}$$
        ASCII: c = clip(1 - G / Gclr, 0, 1) in daytime; night c = mean of that day's daytime c

        $G$ measured and $G_{clr}$ clear-sky global irradiance (W m-2). A day without any valid
        daytime value leaves its night hours NaN (no silent assumption).

    Args:
        ghi: Global horizontal irradiance (W m-2).
        ghi_clear: Clear-sky irradiance (W m-2).
        sin_beta: Sine of the solar elevation (-).
        day_index: Non-negative integer day label per hour.

    Returns:
        Cloud fraction (-).
    """
    g = np.asarray(ghi, dtype=float)
    gc = np.asarray(ghi_clear, dtype=float)
    day = np.asarray(day_index, dtype=np.int64)
    daytime = np.asarray(sin_beta, dtype=float) > MIN_SIN_ELEVATION
    safe = np.where(daytime & (gc > 0), gc, 1.0)
    c = np.where(daytime, np.clip(1.0 - g / safe, 0.0, 1.0), np.nan)
    ok = daytime & np.isfinite(c)
    n_days = int(day.max()) + 1 if day.size else 0
    total = np.bincount(day[ok], weights=c[ok], minlength=n_days)
    count = np.bincount(day[ok], minlength=n_days)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    return np.where(daytime, c, mean[day])


def sky_emissivity(ta_c: Any, e_hpa: Any, cloud: Any) -> np.ndarray:
    """Sky emissivity: Prata (1996) clear sky blended with cloud as a black body.

    Algorithm:
        $$\\varepsilon_{clr} = 1 - (1+w)\\exp(-\\sqrt{1.2+3w}),\\ w = 46.5\\,e/T,\\quad
        \\varepsilon_{sky} = c + (1-c)\\,\\varepsilon_{clr}$$
        ASCII: eps_clr = 1 - (1 + w) exp(-sqrt(1.2 + 3 w)); eps_sky = c + (1 - c) eps_clr

        $e$ vapour pressure (hPa), $T$ air temperature (K), $c$ cloud fraction (-).

    Args:
        ta_c: Air temperature (degC).
        e_hpa: Vapour pressure (hPa).
        cloud: Cloud fraction (-).

    Returns:
        $\\varepsilon_{sky}$ (-).
    """
    tk = np.asarray(ta_c, dtype=float) + 273.15
    w = 46.5 * np.asarray(e_hpa, dtype=float) / tk
    clear = 1.0 - (1.0 + w) * np.exp(-np.sqrt(1.2 + 3.0 * w))
    c = np.asarray(cloud, dtype=float)
    return c + (1.0 - c) * clear


def fit_ground_excess(
    ts_c: Any, ta_c: Any, ghi: Any, min_hours: int = 1000
) -> dict[str, float] | None:
    """Least-squares fit of the ground-minus-air temperature on global irradiance.

    Algorithm:
        $$T_g - T_a = a + b\\,G,\\quad (a, b) = \\arg\\min \\sum (T_g - T_a - a - bG)^2$$
        ASCII: ts - ta = a + b * GHI, ordinary least squares over hours with all three finite

        $T_g$ observed ground temperature (degC), $T_a$ air temperature (degC), $G$ global
        irradiance (W m-2); $a$ in K, $b$ in K per W m-2.

    Args:
        ts_c: Observed ground temperature (degC).
        ta_c: Air temperature (degC).
        ghi: Global horizontal irradiance (W m-2).
        min_hours: Minimum sample size; fewer hours returns None.

    Returns:
        ``{"a", "b", "n", "rmse"}`` (rmse in K) or None.
    """
    ts = np.asarray(ts_c, dtype=float)
    ta = np.asarray(ta_c, dtype=float)
    g = np.asarray(ghi, dtype=float)
    ok = np.isfinite(ts) & np.isfinite(ta) & np.isfinite(g)
    n = int(ok.sum())
    if n < min_hours or n < 3:
        log.debug("ground fit skipped: n=%d < min_hours=%d", n, min_hours)
        return None
    y = (ts - ta)[ok]
    design = np.column_stack([np.ones(n), g[ok]])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    return {
        "a": float(coef[0]),
        "b": float(coef[1]),
        "n": n,
        "rmse": float(math.sqrt(float(np.mean(resid**2)))),
    }


def mean_radiant_temperature_open(
    ta_c: Any,
    e_hpa: Any,
    ghi: Any,
    sin_beta: Any,
    s_dif: Any,
    i_dn: Any,
    cloud: Any,
    ground_fit: dict[str, float],
    albedo: float,
    eps_ground: float,
    absorptivity: float = 0.7,
    emissivity_person: float = 0.97,
    angle_factor: float = 0.5,
) -> np.ndarray:
    """Mean radiant temperature of a standing person on open ground (ERA5-HEAT form).

    Algorithm:
        $$T_{mrt} = \\Big\\{\\frac{1}{\\sigma}\\Big[f_a L_\\downarrow + f_a L_\\uparrow +
        \\frac{a_{ir}}{\\varepsilon_p}\\big(f_a S_{dif} + f_a S_\\uparrow + f_p I_{dn}\\big)
        \\Big]\\Big\\}^{1/4} - 273.15$$
        ASCII: Tmrt = ((fa Ldn + fa Lup + (a_ir/eps_p)(fa S_dif + fa S_up + fp I_dn)) / sigma)^0.25
        - 273.15

        $L_\\downarrow = \\varepsilon_{sky}\\sigma T_a^4$, $L_\\uparrow = \\varepsilon_g\\sigma
        T_g^4 + (1-\\varepsilon_g)L_\\downarrow$, $T_g = T_a + a + bG$ (degC, K), $S_\\uparrow =
        \\alpha G$ (W m-2), $f_p$ from :func:`projected_area_factor` at the solar elevation.
        $\\sigma$ W m-2 K-4; $f_a$ angle factor, $a_{ir}$ shortwave absorptivity,
        $\\varepsilon_p$ person emissivity, $\\alpha$ albedo, $\\varepsilon_g$ ground emissivity
        (all -).

    Args:
        ta_c: Air temperature (degC).
        e_hpa: Vapour pressure (hPa).
        ghi: Global horizontal irradiance (W m-2).
        sin_beta: Sine of the solar elevation (-).
        s_dif: Diffuse irradiance (W m-2).
        i_dn: Beam-normal irradiance (W m-2).
        cloud: Cloud fraction (-).
        ground_fit: ``{"a", "b"}`` from :func:`fit_ground_excess`.
        albedo: Ground albedo (-).
        eps_ground: Ground emissivity (-).
        absorptivity: Shortwave absorptivity of the person (-).
        emissivity_person: Longwave emissivity of the person (-).
        angle_factor: Sky/ground view factor of the person (-).

    Returns:
        Mean radiant temperature (degC).
    """
    ta = np.asarray(ta_c, dtype=float)
    g = np.asarray(ghi, dtype=float)
    tk = ta + 273.15
    ldn = sky_emissivity(ta, e_hpa, cloud) * SIGMA * tk**4
    tg_k = ta + ground_fit["a"] + ground_fit["b"] * g + 273.15
    lup = eps_ground * SIGMA * tg_k**4 + (1.0 - eps_ground) * ldn
    gamma = np.degrees(np.arcsin(np.clip(np.asarray(sin_beta, dtype=float), 0.0, 1.0)))
    fp = projected_area_factor(gamma)
    s_up = albedo * g
    fa = angle_factor
    flux = (
        fa * ldn
        + fa * lup
        + (absorptivity / emissivity_person)
        * (fa * np.asarray(s_dif, dtype=float) + fa * s_up + fp * np.asarray(i_dn, dtype=float))
    )
    with np.errstate(invalid="ignore"):
        return (flux / SIGMA) ** 0.25 - 273.15


def utci_hourly(ta_c: Any, tmrt_c: Any, v10_ms: Any, rh_pct: Any) -> np.ndarray:
    """UTCI (degC) with pythermalcomfort 4.8.0 (Brode et al. 2012); NaN outside validity range.

    Algorithm:
        $$\\mathrm{UTCI} = T_a + f(T_a,\\ T_{mrt} - T_a,\\ v_{10},\\ e)$$
        ASCII: UTCI = Ta + polynomial(Ta, Tmrt - Ta, v10, vapour pressure)

        $T_a$ air temperature (degC), $T_{mrt}$ mean radiant temperature (degC), $v_{10}$ wind at
        10 m (m/s, 0.5-17), $e$ vapour pressure from relative humidity (%). Hours with a NaN
        input are NaN and are not sent to the library.

    Args:
        ta_c: Air temperature (degC).
        tmrt_c: Mean radiant temperature (degC).
        v10_ms: Wind speed at 10 m (m/s).
        rh_pct: Relative humidity (%).

    Returns:
        UTCI (degC) in the broadcast shape.
    """
    t, r, w, h = np.broadcast_arrays(
        *(np.asarray(x, dtype=float) for x in (ta_c, tmrt_c, v10_ms, rh_pct))
    )
    out = np.full(t.shape, np.nan)
    ok = np.isfinite(t) & np.isfinite(r) & np.isfinite(w) & np.isfinite(h)
    if ok.any():
        from pythermalcomfort.models import utci as _utci

        res = _utci(tdb=t[ok], tr=r[ok], v=w[ok], rh=h[ok], round_output=False)
        out[ok] = np.asarray(res.utci, dtype=float)
    return out


# =========================================================================== #
# 2. Annual metrics                                                            #
# =========================================================================== #
def _expected_hours(year: int, months: Sequence[int]) -> int:
    return sum(calendar.monthrange(year, m)[1] for m in months) * 24


def _year_slices(
    values: np.ndarray, time_kst: np.ndarray, years: tuple[int, int], months: Sequence[int]
) -> dict[int, tuple[np.ndarray, np.ndarray, int]]:
    """Per year ``(values, day_of_each_value, n_finite)`` restricted to ``months``."""
    t = np.asarray(time_kst).astype("datetime64[h]")
    yr = t.astype("datetime64[Y]").astype(np.int64) + 1970
    mo = t.astype("datetime64[M]").astype(np.int64) % 12 + 1
    day = t.astype("datetime64[D]")
    in_month = np.isin(mo, list(months))
    out: dict[int, tuple[np.ndarray, np.ndarray, int]] = {}
    for y in range(years[0], years[1] + 1):
        sel = (yr == y) & in_month
        v = values[sel]
        out[y] = (v, day[sel], int(np.isfinite(v).sum()))
    return out


def _daily_max(values: np.ndarray, days: np.ndarray) -> np.ndarray:
    return pd.Series(values).groupby(days).max().to_numpy()


def annual_metrics(
    utci: Any,
    time_kst: Any,
    years: tuple[int, int],
    months: Sequence[int],
    thresholds_c: Sequence[float],
    headline_c: float,
    min_hour_fraction: float,
    min_valid_years: int,
) -> dict[str, Any]:
    """Heat-stress days and hours per year from an hourly UTCI series.

    Algorithm:
        $$D_\\theta = \\frac{1}{|Y|}\\sum_{y\\in Y} n_{d,y},
        \\quad n_{d,y} = \\big|\\{d\\in y: U^{max}_d \\ge \\theta\\}\\big|,
        H_\\theta = \\frac{1}{|Y|}\\sum_{y\\in Y}\\big|\\{h\\in y: U_h \\ge \\theta\\}\\big|$$
        ASCII: D = mean over valid years of count(days with daily max UTCI >= theta); H = same
        for hours

        $U_h$ hourly UTCI (degC), $U^{max}_d$ its daily maximum, $\\theta$ threshold (degC), $Y$
        the valid years. A year is valid when at least ``min_hour_fraction`` of its hours in
        ``months`` have a UTCI; fewer than ``min_valid_years`` valid years gives None (never 0).

    Args:
        utci: Hourly UTCI (degC), NaN = missing.
        time_kst: ``datetime64`` hourly times (KST) of ``utci``.
        years: First and last year, inclusive.
        months: Calendar months in the season.
        thresholds_c: Thresholds $\\theta$ (degC).
        headline_c: The threshold whose per-year days are returned.
        min_hour_fraction: Share of hours that must be present for a valid year.
        min_valid_years: Minimum number of valid years.

    Returns:
        ``utci_days_ge<θ>``, ``utci_hours_ge<θ>`` (per year), ``utci_annual_max_mean`` (degC),
        ``utci_days_ge<headline>_by_year`` ({year: days or None}) and ``n_valid_years``.
    """
    u = np.asarray(utci, dtype=float)
    per_year = _year_slices(u, time_kst, years, months)
    by_year: dict[int, int | None] = {}
    days_sum = dict.fromkeys(thresholds_c, 0)
    hours_sum = dict.fromkeys(thresholds_c, 0)
    max_sum, n_valid = 0.0, 0
    for y, (v, d, n_fin) in per_year.items():
        if n_fin == 0 or n_fin / _expected_hours(y, months) < min_hour_fraction:
            by_year[y] = None
            continue
        n_valid += 1
        dmax = _daily_max(v, d)
        max_sum += float(np.nanmax(dmax))
        for th in thresholds_c:
            days_sum[th] += int((dmax >= th).sum())
            hours_sum[th] += int((v >= th).sum())
        by_year[y] = int((dmax >= headline_c).sum())
    out: dict[str, Any] = {
        f"utci_days_ge{headline_c:g}_by_year": by_year,
        "n_valid_years": n_valid,
    }
    enough = n_valid >= max(1, min_valid_years)
    for th in thresholds_c:
        out[f"utci_days_ge{th:g}"] = days_sum[th] / n_valid if enough else None
        out[f"utci_hours_ge{th:g}"] = hours_sum[th] / n_valid if enough else None
    out["utci_annual_max_mean"] = max_sum / n_valid if enough else None
    log.debug("annual_metrics: %d valid years of %d", n_valid, years[1] - years[0] + 1)
    return out


def heat_days_from_hourly(
    ta_c: Any,
    time_kst: Any,
    years: tuple[int, int],
    months: Sequence[int],
    threshold_c: float,
    min_hour_fraction: float,
) -> dict[str, Any]:
    """Heat days (daily maximum air temperature at or above a threshold) from hourly data.

    Algorithm:
        $$N = \\frac{1}{|Y|}\\sum_{y\\in Y}\\big|\\{d\\in y: T^{max}_d \\ge \\theta\\}\\big|$$
        ASCII: N = mean over valid years of count(days with daily max Ta >= theta)

        $T^{max}_d$ daily maximum of the hourly air temperature (degC), $\\theta$ threshold
        (degC); a year is valid under the same completeness rule as :func:`annual_metrics`.

    Args:
        ta_c: Hourly air temperature (degC).
        time_kst: ``datetime64`` hourly times (KST).
        years: First and last year, inclusive.
        months: Calendar months in the season.
        threshold_c: Threshold $\\theta$ (degC).
        min_hour_fraction: Share of hours that must be present for a valid year.

    Returns:
        ``{"by_year": {year: days or None}, "mean": days/yr or None}``.
    """
    ta = np.asarray(ta_c, dtype=float)
    by_year: dict[int, int | None] = {}
    for y, (v, d, n_fin) in _year_slices(ta, time_kst, years, months).items():
        if n_fin == 0 or n_fin / _expected_hours(y, months) < min_hour_fraction:
            by_year[y] = None
        else:
            by_year[y] = int((_daily_max(v, d) >= threshold_c).sum())
    valid = [v for v in by_year.values() if v is not None]
    return {"by_year": by_year, "mean": float(np.mean(valid)) if valid else None}


# =========================================================================== #
# 2b. Future by monthly delta (phase 2)                                        #
# =========================================================================== #
def monthly_delta(
    base_tmax: Any,
    base_months: Any,
    fut_tmax: Any,
    fut_months: Any,
    months: Sequence[int],
) -> np.ndarray:
    """Monthly change of the model daily maximum temperature between two windows.

    Algorithm:
        $$\\delta_{m,c} = \\overline{G}^{fut}_{m,c} - \\overline{G}^{base}_{m,c}$$
        ASCII: delta[m, c] = mean_fut(month m, cell c) - mean_base(month m, cell c)

        $G$ is the model daily maximum air temperature (degC), $m$ the calendar month, $c$ a
        grid cell; each mean is over all days of that month in the window (NaN-aware). A cell
        with no finite day in either window gives NaN (no data), never 0.

    Args:
        base_tmax: Base-window daily maximum ``(n_base, ny, nx)`` in degC.
        base_months: Calendar month (1-12) of each base day, ``(n_base,)``.
        fut_tmax: Future-window daily maximum ``(n_fut, ny, nx)`` in degC.
        fut_months: Calendar month of each future day, ``(n_fut,)``.
        months: Months to return.

    Returns:
        Array ``(len(months), ny, nx)`` in degC.

    Raises:
        ValueError: If the grids differ in shape or a month array does not match its days.
    """
    b = np.asarray(base_tmax, dtype=float)
    f = np.asarray(fut_tmax, dtype=float)
    if b.shape[1:] != f.shape[1:]:
        raise ValueError(f"base grid {b.shape[1:]} and future grid {f.shape[1:]} differ")
    if (
        b.shape[0] != np.asarray(base_months).shape[0]
        or f.shape[0] != np.asarray(fut_months).shape[0]
    ):
        raise ValueError("month arrays do not match the number of days")
    out = np.stack(
        [monthly_mean(f, fut_months, int(m)) - monthly_mean(b, base_months, int(m)) for m in months]
    )
    log.debug("monthly_delta: shape %s dtype %s", out.shape, out.dtype)
    return out


def apply_future(ta_c: Any, time_kst: Any, delta_by_month: Mapping[int, float]) -> np.ndarray:
    """Add the monthly delta to an hourly air-temperature series.

    Algorithm:
        $$T^{fut}(h) = T(h) + \\delta_{m(h)}$$
        ASCII: Ta_fut(h) = Ta(h) + delta[month of h]

        $T$ air temperature (degC), $\\delta_m$ the monthly change at the cell (degC),
        $m(h)$ the calendar month of hour $h$. An hour whose month has no (or a NaN) delta is
        NaN, so that the year falls under the completeness rule; it never keeps the present
        temperature.

    Args:
        ta_c: Hourly air temperature (degC), NaN = missing.
        time_kst: ``datetime64`` hourly times (KST).
        delta_by_month: ``{month: delta (degC)}``.

    Returns:
        Future hourly air temperature (degC), same shape as ``ta_c``.
    """
    ta = np.asarray(ta_c, dtype=float)
    month = np.asarray(time_kst).astype("datetime64[M]").astype(np.int64) % 12 + 1
    table = np.full(13, np.nan)
    for m, d in delta_by_month.items():
        table[int(m)] = float(d)
    return ta + table[month]


def scenario_window(scenario: str, year: int, future_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Which scenario and window is served for a requested scenario and target year.

    Args:
        scenario: Requested scenario key (``historical``, ``rcp26`` ...).
        year: Target year.
        future_cfg: The ``heat_utci.future`` config section.

    Returns:
        ``served_scenario`` (SSP key or ``"historical"``), ``window`` ([first, last] year or
        None) and ``reason`` (why the present was served; None when the request was
        ``historical`` or the future is served).
    """
    if scenario == "historical":
        return {"served_scenario": "historical", "window": None, "reason": None}
    keys = future_cfg["scenario_keys"]
    if scenario not in keys:
        return {
            "served_scenario": "historical",
            "window": None,
            "reason": f"no future grid for scenario {scenario}; present period served",
        }
    min_year = int(future_cfg["min_target_year"])
    if int(year) < min_year:
        return {
            "served_scenario": "historical",
            "window": None,
            "reason": f"target year {int(year)} is before {min_year}; present period served",
        }
    return {
        "served_scenario": str(keys[scenario]),
        "window": [int(v) for v in future_cfg["future_years"]],
        "reason": None,
    }


# =========================================================================== #
# 3. Sub check (diagnostic only)                                               #
# =========================================================================== #
def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    from scipy.stats import spearmanr

    if len(a) < 3:
        return None
    with np.errstate(all="ignore"):
        r = float(spearmanr(a, b).statistic)
    return r if math.isfinite(r) else None


def asset_check(asset: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Two diagnostic items for one asset; they never change the asset's numbers.

    C2 (hourly vs grid heat days) is a property of the station, not of the asset: the
    asset shift $G(c_a) - G(c_s)$ is added to both sides of the comparison, so it is checked
    once per station by :func:`station_check` and is not part of this stamp.

    Algorithm:
        $$C_1:\\ \\rho_s(D^{UTCI}_{38,y},\\,N_y) \\ge \\rho_{min};\\quad
        C_3:\\ d_s \\le d_{s,max}\\ \\wedge\\ d_r \\le d_{r,max}$$
        ASCII: C1 spearman(yearly D38, yearly heat days) >= min; C3 station <= 50 km and
        radiation station <= 75 km

        $D^{UTCI}_{38,y}$ yearly days with UTCI >= 38 degC, $N_y$ yearly grid heat days (days),
        $\\rho_s$ Spearman rank correlation (-), $d$ in km. C1 is None (not checkable, left
        out of the denominator) when either series is constant.

    Args:
        asset: Per-asset row with ``utci_days_ge38_by_year``, ``heat_days_grid_by_year``,
            ``station_distance_km`` and ``radiation_distance_km``.
        check: The ``check`` section of the heat_utci config.

    Returns:
        ``C1_rho``, ``C1_ok``, ``C3_ok`` (True/False/None), ``agree`` ("k/n") and ``warn``
        (any item failed).
    """
    u = asset.get("utci_days_ge38_by_year") or {}
    g = asset.get("heat_days_grid_by_year") or {}
    common = sorted(y for y in u if u[y] is not None and g.get(y) is not None)
    rho = _spearman([u[y] for y in common], [g[y] for y in common])
    c1: bool | None = None if rho is None else bool(rho >= float(check["min_spearman_years"]))

    ds, dr = asset.get("station_distance_km"), asset.get("radiation_distance_km")
    c3: bool | None = None
    if ds is not None and dr is not None:
        c3 = bool(
            float(ds) <= float(check["max_station_km"])
            and float(dr) <= float(check["max_radiation_station_km"])
        )
    known = [x for x in (c1, c3) if x is not None]
    return {
        "C1_rho": rho,
        "C1_ok": c1,
        "C3_ok": c3,
        "agree": f"{sum(known)}/{len(known)}",
        "warn": any(x is False for x in known),
    }


def station_check(
    station_days: dict[int, dict[str, Any]], check: dict[str, Any]
) -> list[dict[str, Any]]:
    """C2 once per station: heat days from its hourly record vs the grid at its own cell.

    Algorithm:
        $$C_2(s):\\ |N^{hourly}_s - N^{grid}_s|
          \\le \\max(\\Delta_{abs},\\ \\Delta_{rel} N^{grid}_s)$$
        ASCII: C2(s) ok when |N_hourly(s) - N_grid(s)| <= max(abs, rel * N_grid(s))

        $N^{hourly}_s$ mean days/yr with the station's hourly maximum air temperature >= 33 degC
        (unshifted), $N^{grid}_s$ mean days/yr with the 0.05 deg daily maximum >= 33 degC at the
        station's cell, $\\Delta_{abs}$ in days/yr, $\\Delta_{rel}$ (-). Hourly sampling misses
        the peak between hours and a point is not a 5 km cell mean, so a negative difference
        is expected; the item is a memo per station and never enters an asset's stamp.

    Args:
        station_days: ``{stnId: {"name", "n_assets", "heat_days_hourly", "heat_days_grid"}}``;
            either day count may be None (not checkable).
        check: The ``check`` section of the heat_utci config.

    Returns:
        One dict per station, sorted by ``station_id``: the inputs plus ``diff_days``
        (hourly - grid, or None) and ``ok`` (True/False/None).
    """
    out: list[dict[str, Any]] = []
    for stn in sorted(station_days):
        s = station_days[stn]
        hourly, grid = s.get("heat_days_hourly"), s.get("heat_days_grid")
        diff: float | None = None
        ok: bool | None = None
        if hourly is not None and grid is not None:
            diff = float(hourly) - float(grid)
            limit = max(
                float(check["warn_abs_heat_days"]), float(check["warn_rel_heat_days"]) * grid
            )
            ok = bool(abs(diff) <= limit)
        out.append(
            {
                "station_id": int(stn),
                "name": s.get("name"),
                "n_assets": s.get("n_assets"),
                "heat_days_hourly": hourly,
                "heat_days_grid": grid,
                "diff_days": diff,
                "ok": ok,
            }
        )
    return out


def sub_check(
    per_asset: Sequence[dict[str, Any]],
    cfg: dict[str, Any],
    station_days: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate the checks over a run; diagnostic only, never alters the rows.

    Algorithm:
        $$\\text{item } k\\in\\{C_1, C_3\\} \\text{ agrees}
          \\iff \\text{no asset fails } C_k;\\qquad
        \\rho_{assets} = \\rho_s(D^{UTCI}_{38},\\ N_{grid})\\ \\text{over assets}$$
        ASCII: item agrees when no asset fails it; asset rank = spearman(D38, grid heat days)
        across assets (needs >= min_assets_for_rank assets); C2 per station, memo only

        $D^{UTCI}_{38}$ days/yr with UTCI >= 38 degC, $N_{grid}$ grid heat days (days/yr),
        $\\rho_s$ Spearman rank correlation (-).

    Args:
        per_asset: Per-asset rows; rows with ``utci_days_ge38`` None count as no data.
        cfg: The ``check`` section of the heat_utci config.
        station_days: Optional per-station heat days for :func:`station_check`.

    Returns:
        ``method``, ``items``, ``n_assets``, ``n_no_data``, ``n_warn_any``, ``agree``, ``warn``,
        ``warn_reason``, ``asset_rank_spearman``, ``station_c2`` (list, memo) and
        ``station_c2_note`` (text or None). ``agree``/``warn`` cover C1, C3 and the asset rank.
    """
    rows = [a for a in per_asset if a.get("utci_days_ge38") is not None]
    n_no_data = len(per_asset) - len(rows)
    checks = [asset_check(a, cfg) for a in rows]
    names = {"C1": "C1_ok", "C3": "C3_ok"}
    items: dict[str, Any] = {}
    reasons: list[str] = []
    agree_n, known_n = 0, 0
    descr = {
        "C1": "UTCI-day vs heat-day yearly rank below minimum",
        "C3": "station farther than limit",
    }
    for key, field in names.items():
        fails = sum(1 for c in checks if c[field] is False)
        na = sum(1 for c in checks if c[field] is None)
        items[f"{key}"] = {"n_fail": fails, "n_not_checkable": na}
        if len(checks) - na > 0:
            known_n += 1
            agree_n += int(fails == 0)
        if fails:
            reasons.append(f"{key}: {fails} asset(s) — {descr[key]}")
    rank: float | None = None
    if len(rows) >= int(cfg["min_assets_for_rank"]):
        paired = [
            (float(a["utci_days_ge38"]), float(a["heat_days_grid"]))
            for a in rows
            if a.get("heat_days_grid") is not None
        ]
        rank = _spearman([p[0] for p in paired], [p[1] for p in paired])
        if rank is not None and rank < float(cfg["min_spearman_assets"]):
            reasons.append(f"asset rank: Spearman {rank:.2f} < {cfg['min_spearman_assets']}")
    stations = station_check(station_days, cfg) if station_days else []
    off = [s for s in stations if s["ok"] is False]
    note = None
    if off:
        names_off = ", ".join(f"{s['station_id']} ({s['diff_days']:+.1f})" for s in off)
        note = (
            f"C2 memo: {len(off)}/{len(stations)} station(s) where hourly heat days differ "
            f"from the grid beyond the limit — {names_off} days/yr. Hourly sampling misses "
            "the daily peak; this is a station property and does not change any asset stamp."
        )
    n_warn = sum(1 for c in checks if c["warn"])
    return {
        "method": "UTCI >=38 degC days vs heat days (daily max >=33 degC) — diagnostic only",
        "items": items,
        "n_assets": len(rows),
        "n_no_data": n_no_data,
        "n_warn_any": n_warn,
        "agree": f"{agree_n}/{known_n}",
        "warn": bool(reasons),
        "warn_reason": "; ".join(reasons) if reasons else None,
        "asset_rank_spearman": rank,
        "station_c2": stations,
        "station_c2_note": note,
    }


# =========================================================================== #
# 4. Stations and files (I/O)                                                  #
# =========================================================================== #
def read_station_table(path: Path) -> pd.DataFrame:
    """Read the KMA 관측지점정보 history table (one row per station per site/period).

    Args:
        path: CSV with ``stnId,start,end,name,...,lat,lon,elev_m,...,h_wd_m``.

    Returns:
        DataFrame with ``start``/``end`` parsed as timestamps (``end`` NaT = still open) and
        numeric coordinate/height columns.
    """
    t = pd.read_csv(path, parse_dates=["start", "end"])
    t["stnId"] = t["stnId"].astype(int)
    for col in ("lat", "lon", "elev_m", "h_pa_m", "h_ta_m", "h_wd_m", "h_rn_m"):
        if col in t:
            t[col] = pd.to_numeric(t[col], errors="coerce")
    return t


def station_locations(table: pd.DataFrame, date: Any) -> pd.DataFrame:
    """The station rows in force on ``date`` (``start <= date < end``), one per station.

    Args:
        table: Output of :func:`read_station_table`.
        date: Date string or timestamp.

    Returns:
        One row per station (the latest period that covers ``date``), index reset.
    """
    d = pd.Timestamp(date)
    active = table[(table["start"] <= d) & (table["end"].isna() | (d < table["end"]))]
    return active.sort_values("start").drop_duplicates("stnId", keep="last").reset_index(drop=True)


def anemometer_height(
    table: pd.DataFrame, station_id: int, date: Any, default_m: float
) -> tuple[float, bool]:
    """Anemometer height of a station on ``date`` and whether it had to be assumed.

    Args:
        table: Output of :func:`read_station_table`.
        station_id: KMA station number.
        date: Date string or timestamp.
        default_m: Height used when the table has none (m).

    Returns:
        ``(height_m, assumed)``; ``assumed`` is True when ``default_m`` was used.
    """
    rows = station_locations(table, date)
    rows = rows[rows["stnId"] == int(station_id)]
    if rows.empty or not np.isfinite(float(rows.iloc[0]["h_wd_m"])):
        return float(default_m), True
    return float(rows.iloc[0]["h_wd_m"]), False


def _haversine_km(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p1, p2 = math.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons - lon)
    h = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(h))


def _rank_stations(lat: float, lon: float, stations: pd.DataFrame) -> pd.DataFrame:
    s = stations.copy()
    s["distance_km"] = _haversine_km(lat, lon, s["lat"].to_numpy(float), s["lon"].to_numpy(float))
    return s.sort_values("distance_km").reset_index(drop=True)


def pick_station(lat: float, lon: float, stations: pd.DataFrame) -> tuple[int, float]:
    """Nearest station by great-circle distance.

    Algorithm:
        $$d = 2R\\arcsin\\sqrt{\\sin^2\\tfrac{\\Delta\\varphi}{2} +
        \\cos\\varphi_1\\cos\\varphi_2\\sin^2\\tfrac{\\Delta\\lambda}{2}}$$
        ASCII: d = 2 R asin(sqrt(sin^2(dlat/2) + cos(lat1) cos(lat2) sin^2(dlon/2)))

        $R$ = 6371 km, $\\varphi$ latitude, $\\lambda$ longitude (rad).

    Args:
        lat: Latitude (deg).
        lon: Longitude (deg east).
        stations: Frame with ``stnId``, ``lat``, ``lon``.

    Returns:
        ``(stnId, distance_km)``.
    """
    ranked = _rank_stations(lat, lon, stations)
    return int(ranked.iloc[0]["stnId"]), float(ranked.iloc[0]["distance_km"])


def load_asos_station(
    station_id: int,
    years: tuple[int, int],
    months: Sequence[int],
    root: Path | None = None,
) -> pd.DataFrame:
    """Read one station's hourly ASOS series for ``years`` x ``months`` (KST).

    Both file names are read (``<y>_MJJAS.csv`` first, else ``<y>.csv``); an empty or absent
    file contributes no rows (missing, never zero) and blank cells are NaN.

    Args:
        station_id: KMA station number.
        years: First and last year, inclusive.
        months: Calendar months to keep.
        root: KMA folder holding ``asos_hourly/`` (default ``paths.kma_dir()``).

    Returns:
        DataFrame indexed by hourly timestamp with columns ``ta, ws, hm, icsr, ts``.
    """
    from climaterisk_worker._paths import paths

    base = (Path(root) if root is not None else paths.kma_dir()) / "asos_hourly" / str(station_id)
    cols = ["tm", "ta", "ws", "hm", "icsr", "ts"]
    frames: list[pd.DataFrame] = []
    for y in range(years[0], years[1] + 1):
        for name in (f"{y}_MJJAS.csv", f"{y}.csv"):
            f = base / name
            if not f.is_file():
                continue
            try:
                df = pd.read_csv(f, usecols=cols, low_memory=False)
            except pd.errors.EmptyDataError:
                break
            if len(df):
                frames.append(df)
            break
    if not frames:
        return pd.DataFrame(columns=cols[1:], index=pd.DatetimeIndex([], name="tm"), dtype=float)
    df = pd.concat(frames, ignore_index=True)
    df["tm"] = pd.to_datetime(df["tm"])
    df = df[df["tm"].dt.month.isin(list(months))]
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("tm").set_index("tm").sort_index()
    log.debug("station %s: %d rows, columns %s", station_id, len(df), list(df.columns))
    return df[cols[1:]]


def build_hazard(points: Sequence[tuple[float, float]], values: Sequence[float | None]) -> Any:
    """A one-event CLIMADA ``Hazard`` whose intensity is the headline at each point.

    Args:
        points: ``(lat, lon)`` per asset.
        values: Days per year with UTCI >= 38 degC, or None where unknown (stored as NaN;
            never as 0).

    Returns:
        ``Hazard(haz_type="HW", units="days/yr")`` with one event of frequency 1/yr.
    """
    from climada.hazard import Hazard
    from scipy import sparse

    from climaterisk_worker.hazard_convert import _centroids

    lat = np.array([p[0] for p in points], dtype=float)
    lon = np.array([p[1] for p in points], dtype=float)
    row = np.array([[np.nan if v is None else float(v) for v in values]])
    hazard = Hazard(
        haz_type="HW",
        units="days/yr",
        centroids=_centroids(lat, lon),
        event_id=np.array([1]),
        event_name=["utci_days_ge38"],
        date=np.zeros(1, dtype=int),
        frequency=np.array([1.0]),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(row),
        fraction=sparse.csr_matrix(np.nan_to_num(row > 0).astype(float)),
    )
    hazard.check()
    return hazard


# =========================================================================== #
# 5. Orchestration (I/O)                                                       #
# =========================================================================== #
def _season_hours(years: tuple[int, int], months: Sequence[int]) -> np.ndarray:
    """All hourly timestamps (datetime64[h], KST) of the season in chronological order."""
    parts = []
    for y in range(years[0], years[1] + 1):
        for m in months:
            start = np.datetime64(f"{y:04d}-{m:02d}-01T00", "h")
            n = calendar.monthrange(y, m)[1] * 24
            parts.append(start + np.arange(n).astype("timedelta64[h]"))
    return np.concatenate(parts)


class _Grid:
    """The 0.05 deg daily-maximum cache, dates reconstructed from its year/month arrays."""

    def __init__(self, path: Path, years: tuple[int, int], months: Sequence[int]) -> None:
        z = np.load(path)
        self.tmax = np.asarray(z["tmax"], dtype=float)
        self.lat = np.asarray(z["lat"], dtype=float)
        self.lon = np.asarray(z["lon"], dtype=float)
        yrs, mos = np.asarray(z["years"]), np.asarray(z["months"])
        days = []
        for y in range(years[0], years[1] + 1):
            for m in months:
                start = np.datetime64(f"{y:04d}-{m:02d}-01", "D")
                days.append(start + np.arange(calendar.monthrange(y, m)[1]))
        self.days = np.concatenate(days)
        want_y = self.days.astype("datetime64[Y]").astype(int) + 1970
        want_m = self.days.astype("datetime64[M]").astype(int) % 12 + 1
        if (
            len(self.days) != self.tmax.shape[0]
            or not np.array_equal(want_y, yrs)
            or not np.array_equal(want_m, mos)
        ):
            raise ValueError("heat_days grid cache does not match the configured years/months")
        self.years = want_y
        self.step = float(np.median(np.diff(self.lat)))
        log.info("grid cache loaded: tmax shape %s dtype %s", self.tmax.shape, self.tmax.dtype)

    def cell(self, lat: float, lon: float) -> tuple[int, int] | None:
        """Nearest cell of a point, or None outside the grid or on a cell with no data."""
        i = round((lat - float(self.lat[0])) / self.step)
        j = round((lon - float(self.lon[0])) / self.step)
        if not (0 <= i < self.lat.size and 0 <= j < self.lon.size):
            return None
        if abs(lat - self.lat[i]) > self.step / 2 + 1e-9 or abs(lon - self.lon[j]) > (
            self.step / 2 + 1e-9
        ):
            return None
        if not np.isfinite(self.tmax[:, i, j]).any():
            return None
        return i, j


class _Run:
    """Per-call caches: station frames, station radiation, ground fits."""

    def __init__(self, cfg: dict[str, Any], root: Path | None) -> None:
        from climaterisk_worker._paths import paths

        self.cfg = cfg
        self.root = Path(root) if root is not None else paths.kma_dir()
        self.years = (int(cfg["obs_years"][0]), int(cfg["obs_years"][1]))
        self.months = [int(m) for m in cfg["months"]]
        self.frac = float(cfg["min_hour_fraction"])
        self.min_years = int(cfg["min_valid_years"])
        self.min_sin = float(cfg["min_sin_elevation"])
        self.t = _season_hours(self.years, self.months)
        self.yr = self.t.astype("datetime64[Y]").astype(int) + 1970
        self.days = self.t.astype("datetime64[D]")
        _, self.day_index = np.unique(self.days, return_inverse=True)
        self.doy = _day_of_year(self.t)
        self.i0 = extraterrestrial_normal(self.doy)
        # sun position at the interval midpoint (hour - 30 min): icsr is the preceding hour
        self.t_mid = self.t.astype("datetime64[m]") - np.timedelta64(30, "m")
        self.table = read_station_table(self.root / str(cfg["station_table"]))
        self.stations = station_locations(self.table, f"{self.years[1]}-09-30")
        have = {int(p.name) for p in (self.root / "asos_hourly").iterdir() if p.name.isdigit()}
        self.stations = self.stations[self.stations["stnId"].isin(have)].reset_index(drop=True)
        self.grid = _Grid(_grid_path(str(cfg["grid_cache"])), self.years, self.months)
        self._frames: dict[int, pd.DataFrame] = {}
        self._rad: dict[int, dict[str, Any]] = {}
        self._fits: dict[tuple[int, int], dict[str, float] | None] = {}
        self._met_ok: dict[int, bool] = {}
        self._future: dict[str, np.ndarray] = {}

    def future_delta(self, key: str) -> np.ndarray:
        """Monthly delta grid ``(len(months), ny, nx)`` of an SSP, cached per run.

        Raises:
            ValueError: If a future cache is not on the observation grid.
        """
        if key not in self._future:
            fcfg = self.cfg["future"]
            base_rel, fut_rel = fcfg["caches"][key]
            by = (int(fcfg["base_years"][0]), int(fcfg["base_years"][1]))
            fy = (int(fcfg["future_years"][0]), int(fcfg["future_years"][1]))
            base = _Grid(_grid_path(str(base_rel)), by, self.months)
            fut = _Grid(_grid_path(str(fut_rel)), fy, self.months)
            for g in (base, fut):
                if (
                    g.lat.shape != self.grid.lat.shape
                    or g.lon.shape != self.grid.lon.shape
                    or not np.allclose(g.lat, self.grid.lat)
                    or not np.allclose(g.lon, self.grid.lon)
                ):
                    raise ValueError("future grid cache is not on the observation grid")
            bm = base.days.astype("datetime64[M]").astype(int) % 12 + 1
            fm = fut.days.astype("datetime64[M]").astype(int) % 12 + 1
            self._future[key] = monthly_delta(base.tmax, bm, fut.tmax, fm, self.months)
        return self._future[key]

    def frame(self, stn: int) -> pd.DataFrame:
        """Station series reindexed to the full season hours (missing = NaN)."""
        if stn not in self._frames:
            df = load_asos_station(stn, self.years, self.months, self.root)
            self._frames[stn] = df.reindex(pd.DatetimeIndex(self.t))
        return self._frames[stn]

    def _loc(self, stn: int) -> tuple[float, float]:
        r = self.stations[self.stations["stnId"] == stn].iloc[0]
        return float(r["lat"]), float(r["lon"])

    def meteo_ok(self, stn: int) -> bool:
        """Station has ta, hm and ws in at least ``min_valid_years`` valid years."""
        if stn not in self._met_ok:
            f = self.frame(stn)
            ok = f[["ta", "hm", "ws"]].notna().all(axis=1).to_numpy()
            n = 0
            for y in range(self.years[0], self.years[1] + 1):
                sel = self.yr == y
                n += int(ok[sel].mean() >= self.frac) if sel.any() else 0
            self._met_ok[stn] = n >= self.min_years
        return self._met_ok[stn]

    def radiation(self, stn: int) -> dict[str, Any]:
        """Station GHI (W m-2) with night = 0, ``ok`` flag for enough daytime coverage."""
        if stn not in self._rad:
            lat, lon = self._loc(stn)
            sinb = solar_sin_elevation(lat, lon, self.t_mid)
            icsr = self.frame(stn)["icsr"].to_numpy(float)
            ghi = icsr * 1e6 / 3600.0
            ghi = np.where(sinb <= 0.0, 0.0, ghi)
            ghi = np.where((sinb <= self.min_sin) & np.isnan(ghi), 0.0, ghi)
            day = sinb > self.min_sin
            n = 0
            for y in range(self.years[0], self.years[1] + 1):
                sel = (self.yr == y) & day
                n += int(np.isfinite(ghi[sel]).mean() >= self.frac) if sel.any() else 0
            self._rad[stn] = {"ghi": ghi, "ok": n >= self.min_years}
        return self._rad[stn]

    def ground_fit(self, stn: int, rad_stn: int) -> dict[str, float] | None:
        """Ground-excess fit of station ``stn`` on the GHI of ``rad_stn`` (fit years only)."""
        key = (stn, rad_stn)
        if key not in self._fits:
            lo, hi = (int(v) for v in self.cfg["ground_fit_years"])
            sel = (self.yr >= lo) & (self.yr <= hi)
            f = self.frame(stn)
            self._fits[key] = fit_ground_excess(
                f["ts"].to_numpy(float)[sel],
                f["ta"].to_numpy(float)[sel],
                self.radiation(rad_stn)["ghi"][sel],
                min_hours=int(self.cfg["ground_fit_min_hours"]),
            )
        return self._fits[key]

    def station_heat_days(self, stn: int) -> dict[str, Any]:
        """Inputs of :func:`station_check` for one station (unshifted hourly vs own cell)."""
        thr = float(self.cfg["heat_day_threshold_c"])
        hourly = heat_days_from_hourly(
            self.frame(stn)["ta"].to_numpy(float), self.t, self.years, self.months, thr, self.frac
        )["mean"]
        cell = self.grid.cell(*self._loc(stn))
        grid: float | None = None
        if cell is not None:
            series = self.grid.tmax[:, cell[0], cell[1]]
            grid = float(
                np.mean(
                    [
                        (series[self.grid.years == y] >= thr).sum()
                        for y in range(self.years[0], self.years[1] + 1)
                    ]
                )
            )
        name = str(self.stations.loc[self.stations["stnId"] == stn, "name"].iloc[0])
        return {"name": name, "heat_days_hourly": hourly, "heat_days_grid": grid}


FUTURE_KEYS = (
    "future_scenario",
    "future_years",
    "utci_days_ge38_future",
    "utci_days_ge38_future_low",
    "utci_days_ge38_future_high",
    "utci_hours_ge38_future",
    "utci_days_ge32_future",
    "future_delta_t_mean_c",
)


def _grid_path(rel: str) -> Path:
    from climaterisk_worker._paths import paths

    return paths.cache_dir(*Path(rel).parts)


def _nan_row(lat: float, lon: float, reason: str, future: bool = False) -> dict[str, Any]:
    keys = (
        "utci_days_ge38",
        "utci_days_ge38_low",
        "utci_days_ge38_high",
        "utci_hours_ge38",
        "utci_days_ge32",
        "utci_hours_ge32",
        "utci_days_ge46",
        "utci_annual_max_mean",
        "utci_days_ge38_by_year",
        "heat_days_grid",
        "heat_days_grid_by_year",
        "heat_days_shift",
        "station_id",
        "station_name",
        "station_distance_km",
        "station_elev_m",
        "radiation_station_id",
        "radiation_distance_km",
        "mean_delta_t_c",
        "n_valid_years",
        "calm_clipped_frac",
        "utci_invalid_frac",
        "wind_height_assumed",
        "hottest_day",
        "hottest_day_hourly",
        "check_C1_rho",
        "check_warn",
    )
    row: dict[str, Any] = dict.fromkeys(keys)
    if future:
        row.update(dict.fromkeys(FUTURE_KEYS))
    row.update(lat=lat, lon=lon, status="NO_HAZARD_DATA", reason=reason)
    return row


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(float(x)) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    return x


def _hourly_inputs(
    run: _Run,
    lat: float,
    lon: float,
    cell: tuple[int, int],
    stn: int,
    s_cell: tuple[int, int],
    rad_stn: int,
    height: float,
) -> dict[str, Any]:
    """Hourly drivers at a point: shifted air temperature, humidity, wind and irradiance.

    Algorithm:
        $$T_a(h) = T_s(h) + G(c_a,d) - G(c_s,d),\\quad e = RH\\,e_s(T_a)/100$$
        ASCII: Ta = Ts + (Grid[point cell, day] - Grid[station cell, day]); e = RH*es(Ta)/100

        $T_s$ station air temperature (degC), $G$ the 0.05 deg daily-maximum grid (degC),
        $c_a$ the point cell, $c_s$ the station cell, $d$ the day, $RH$ relative humidity (%),
        $e_s$ saturation vapour pressure (hPa). At the station itself ($c_a = c_s$) the shift
        is zero. Wind goes to 10 m and is clipped; irradiance is split into diffuse and direct.

    Args:
        run: Per-call caches.
        lat: Point latitude (deg).
        lon: Point longitude (deg east).
        cell: Grid cell of the point.
        stn: Station of the meteorology.
        s_cell: Grid cell of that station.
        rad_stn: Station of the global radiation.
        height: Anemometer height of ``stn`` (m).

    Returns:
        Arrays over the season hours (``ta``, ``rh``, ``e``, ``v10``, ``v``, ``ghi``,
        ``sinb``, ``s_dif``, ``i_dn``, ``cloud``) and ``delta_day`` (degC per day).
    """
    cfg = run.cfg
    g = run.grid
    didx = np.searchsorted(g.days, run.days)
    delta_day = g.tmax[:, cell[0], cell[1]] - g.tmax[:, s_cell[0], s_cell[1]]
    f = run.frame(stn)
    ta, rh = shift_air_temperature(
        f["ta"].to_numpy(float), f["hm"].to_numpy(float), delta_day[didx]
    )
    e = rh / 100.0 * saturation_vapour_pressure(ta)
    v10 = wind_to_10m(f["ws"].to_numpy(float), height, float(cfg["roughness_length_m"]))
    lo_w, hi_w = (float(v) for v in cfg["wind_clip_ms"])
    v, _ = clip_wind(v10, lo_w, hi_w)
    sinb = solar_sin_elevation(lat, lon, run.t_mid)
    ghi = run.radiation(rad_stn)["ghi"].copy()
    ghi[sinb <= 0.0] = 0.0
    s_dif, i_dn = split_irradiance(ghi, sinb, run.i0, run.min_sin)
    cloud = cloud_fraction(ghi, clear_sky_ghi(sinb), sinb, run.day_index)
    log.debug("hourly inputs: %d hours, ta dtype %s", ta.size, ta.dtype)
    return {
        "ta": ta,
        "rh": rh,
        "e": e,
        "v10": v10,
        "v": v,
        "ghi": ghi,
        "sinb": sinb,
        "s_dif": s_dif,
        "i_dn": i_dn,
        "cloud": cloud,
        "delta_day": delta_day,
    }


def _utci_from_inputs(
    run: _Run,
    inp: dict[str, Any],
    fit: dict[str, float],
    alpha: float,
    ta: np.ndarray | None = None,
    e: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """MRT and UTCI (degC) from :func:`_hourly_inputs`; ``ta``/``e`` override the air state.

    Algorithm:
        $$U = \\mathrm{UTCI}\\big(T_a,\\ T_{mrt}(T_a, e, G_h, \\alpha),\\ v_{10},\\ RH\\big)$$
        ASCII: U = UTCI(Ta, Tmrt(Ta, e, GHI, alpha), v, RH)

        Symbols as in :func:`open_field_utci`. A future run passes the warmed ``ta`` and its
        vapour pressure ``e``; humidity RH, wind, irradiance and the ground fit stay at the
        present values (assumption A7).
    """
    cfg = run.cfg
    ta_use = inp["ta"] if ta is None else ta
    e_use = inp["e"] if e is None else e
    tmrt = mean_radiant_temperature_open(
        ta_use,
        e_use,
        inp["ghi"],
        inp["sinb"],
        inp["s_dif"],
        inp["i_dn"],
        inp["cloud"],
        fit,
        alpha,
        float(cfg["emissivity_ground"]),
        float(cfg["absorptivity_shortwave"]),
        float(cfg["emissivity_person"]),
        float(cfg["angle_factor"]),
    )
    return tmrt, utci_hourly(ta_use, tmrt, inp["v"], inp["rh"])


def _asset_row(run: _Run, lat: float, lon: float, future: str | None = None) -> dict[str, Any]:
    cfg = run.cfg
    cell = run.grid.cell(lat, lon)
    if cell is None:
        return _nan_row(lat, lon, "point outside the 0.05 deg land grid", future is not None)
    ranked = _rank_stations(lat, lon, run.stations)
    s_row = next((r for r in ranked.itertuples() if run.meteo_ok(int(r.stnId))), None)
    if s_row is None:
        return _nan_row(lat, lon, "no station with enough valid years", future is not None)
    stn = int(s_row.stnId)
    rad_row = next((r for r in ranked.itertuples() if run.radiation(int(r.stnId))["ok"]), None)
    if rad_row is None:
        return _nan_row(lat, lon, "no station with enough radiation years", future is not None)
    rad_stn = int(rad_row.stnId)
    s_cell = run.grid.cell(float(s_row.lat), float(s_row.lon))
    if s_cell is None:
        return _nan_row(lat, lon, f"station {stn} is outside the land grid", future is not None)
    fit = run.ground_fit(stn, rad_stn)
    if fit is None:
        return _nan_row(lat, lon, f"ground fit of station {stn} unusable", future is not None)
    height, assumed = anemometer_height(
        run.table, stn, f"{run.years[1]}-09-30", float(cfg["anemometer_height_default_m"])
    )

    # --- hourly drivers at the asset (design 4.2)
    inp = _hourly_inputs(run, lat, lon, cell, stn, s_cell, rad_stn, height)
    ta, v10, delta_day = inp["ta"], inp["v10"], inp["delta_day"]
    lo_w, hi_w = (float(v) for v in cfg["wind_clip_ms"])
    finite_w = np.isfinite(v10)
    calm = float(((v10 < lo_w) & finite_w).sum() / max(1, finite_w.sum()))
    g = run.grid

    thresholds = [float(x) for x in cfg["thresholds_c"]]
    head = float(cfg["headline_threshold_c"])
    kw = {
        "years": run.years,
        "months": run.months,
        "thresholds_c": thresholds,
        "headline_c": head,
        "min_hour_fraction": run.frac,
        "min_valid_years": run.min_years,
    }

    def run_utci(alpha: float) -> tuple[np.ndarray, np.ndarray]:
        return _utci_from_inputs(run, inp, fit, alpha)

    tmrt, u = run_utci(float(cfg["albedo"]))
    m = annual_metrics(u, run.t, **kw)
    head_only = {**kw, "thresholds_c": [head]}
    ranges = [
        annual_metrics(run_utci(a)[1], run.t, **head_only)[f"utci_days_ge{head:g}"]
        for a in cfg["albedo_range"]
    ]

    # --- comparison: heat days from the grid cell and from the shifted hourly series
    thr = float(cfg["heat_day_threshold_c"])
    cell_series = g.tmax[:, cell[0], cell[1]]
    grid_by_year = {
        y: int((cell_series[g.years == y] >= thr).sum())
        for y in range(run.years[0], run.years[1] + 1)
    }
    shift = heat_days_from_hourly(ta, run.t, run.years, run.months, thr, run.frac)

    # --- hottest day among valid years (section data)
    hottest, hourly = None, None
    valid_years = [y for y, d in m[f"utci_days_ge{head:g}_by_year"].items() if d is not None]
    if valid_years and np.isfinite(u).any():
        ok = np.isin(run.yr, valid_years)
        daily = pd.Series(np.where(ok, u, np.nan)).groupby(run.days).max()
        if daily.notna().any():
            day = daily.idxmax()
            day = np.datetime64(pd.Timestamp(day), "D")
            sel = run.days == day
            hottest = str(day)
            hourly = {
                "hour": list(range(int(sel.sum()))),
                "ta": ta[sel].tolist(),
                "tmrt": tmrt[sel].tolist(),
                "utci": u[sel].tolist(),
            }

    headline = m[f"utci_days_ge{head:g}"]
    row: dict[str, Any] = {
        "lat": lat,
        "lon": lon,
        "status": "HAZARD_ONLY" if headline is not None else "NO_HAZARD_DATA",
        "reason": None if headline is not None else "fewer valid years than required",
        **m,
        "utci_days_ge38_low": ranges[0],
        "utci_days_ge38_high": ranges[1],
        "heat_days_grid": float(np.mean(list(grid_by_year.values()))),
        "heat_days_grid_by_year": grid_by_year,
        "heat_days_shift": shift["mean"],
        "station_id": stn,
        "station_name": str(run.stations.loc[run.stations["stnId"] == stn, "name"].iloc[0]),
        "station_distance_km": float(s_row.distance_km),
        "station_elev_m": float(s_row.elev_m),
        "radiation_station_id": rad_stn,
        "radiation_distance_km": float(rad_row.distance_km),
        "mean_delta_t_c": float(np.nanmean(delta_day)),
        "calm_clipped_frac": calm,
        "high_wind_clipped_frac": float(((v10 > hi_w) & finite_w).sum() / max(1, finite_w.sum())),
        "utci_invalid_frac": float(np.isnan(u).mean()),
        "wind_height_assumed": bool(assumed),
        "hottest_day": hottest,
        "hottest_day_hourly": hourly,
    }
    if future is not None:
        row.update(_future_metrics(run, inp, fit, cell, future, kw))
    if headline is not None:
        chk = asset_check(row, cfg["check"])
        row.update(
            check_C1_rho=chk["C1_rho"],
            check_warn=chk["warn"],
            check_agree=chk["agree"],
        )
    log.debug(
        "asset (%.4f, %.4f): station %d rad %d, valid years %d",
        lat,
        lon,
        stn,
        rad_stn,
        m["n_valid_years"],
    )
    return _clean(row)


def _future_metrics(
    run: _Run,
    inp: dict[str, Any],
    fit: dict[str, float],
    cell: tuple[int, int],
    future: str,
    kw: dict[str, Any],
) -> dict[str, Any]:
    """Future-period UTCI metrics of one asset by the monthly delta (design 4.1).

    Algorithm:
        $$T^{fut}_a(h) = T_a(h) + \\delta_{m(h),c_a},\\quad e^{fut} = RH\\,e_s(T^{fut}_a)/100$$
        ASCII: Ta_fut = Ta + delta[month, asset cell]; e_fut = RH*es(Ta_fut)/100; UTCI by
        :func:`_utci_from_inputs`

        $\\delta$ monthly change of the model daily maximum (degC), $c_a$ the asset cell,
        $RH$ relative humidity (%), $e_s$ saturation vapour pressure (hPa). RH, wind,
        irradiance and the ground fit stay at present values (A7). A month with no delta
        gives NaN hours, so the year falls under the completeness rule.

    Returns:
        The :data:`FUTURE_KEYS` entries; metrics are None when too few years are valid.
    """
    cfg = run.cfg
    head = float(cfg["headline_threshold_c"])
    delta = run.future_delta(future)[:, cell[0], cell[1]]
    by_month = {m: float(delta[k]) for k, m in enumerate(run.months)}
    ta_f = apply_future(inp["ta"], run.t, by_month)
    e_f = inp["rh"] / 100.0 * saturation_vapour_pressure(ta_f)
    m = annual_metrics(
        _utci_from_inputs(run, inp, fit, float(cfg["albedo"]), ta_f, e_f)[1], run.t, **kw
    )
    head_only = {**kw, "thresholds_c": [head]}
    ranges = [
        annual_metrics(_utci_from_inputs(run, inp, fit, a, ta_f, e_f)[1], run.t, **head_only)[
            f"utci_days_ge{head:g}"
        ]
        for a in cfg["albedo_range"]
    ]
    return {
        "future_scenario": future,
        "future_years": [int(v) for v in cfg["future"]["future_years"]],
        "utci_days_ge38_future": m[f"utci_days_ge{head:g}"],
        "utci_days_ge38_future_low": ranges[0],
        "utci_days_ge38_future_high": ranges[1],
        "utci_hours_ge38_future": m[f"utci_hours_ge{head:g}"],
        "utci_days_ge32_future": m["utci_days_ge32"],
        "future_delta_t_mean_c": float(np.nanmean(delta)) if np.isfinite(delta).any() else None,
    }


def open_field_utci(
    points: Sequence[tuple[float, float]],
    cfg: dict[str, Any],
    root: Path | None = None,
    future: str | None = None,
) -> dict[str, Any]:
    """Open-field UTCI hazard metrics for asset points (design §4.1).

    Algorithm:
        $$T_a(h) = T_s(h) + G(c_a,d) - G(c_s,d),\\quad
        U(h) = \\mathrm{UTCI}\\big(T_a,\\ T_{mrt}(T_a, e, G_h, \\alpha),\\ v_{10},\\ RH\\big)$$
        ASCII: Ta = Ts + (Grid[asset cell, day] - Grid[station cell, day]); U = UTCI(Ta, Tmrt,
        v10, RH); metrics per :func:`annual_metrics`

        $T_s$ station air temperature (degC), $G$ the 0.05 deg daily-maximum grid (degC), $c$
        cells, $d$ day, $G_h$ station global irradiance (W m-2), $\\alpha$ albedo (-), $v_{10}$
        wind at 10 m (m/s), $RH$ relative humidity (%).

    Args:
        points: ``(lat, lon)`` per asset.
        cfg: The ``heat_utci`` section of ``physical_risk_config.json``.
        root: KMA folder (default ``paths.kma_dir()``); tests may point it elsewhere.
        future: SSP key of ``cfg["future"]["caches"]`` (``"ssp245"``, ``"ssp585"``) to add the
            future-period metrics of :func:`_future_metrics` to every row; None keeps the
            present-only output unchanged. Future air temperature = present + monthly delta
            (humidity, wind, radiation held, assumption A7).

    Returns:
        ``{"per_asset": [row, ...], "sub_check": {...}, "record": {...}}``. A point outside
        the land grid, or without a station with enough years, gets ``status`` NO_HAZARD_DATA
        and None metrics (never 0).

    Raises:
        ValueError: If ``future`` is not a configured SSP key.
    """
    if future is not None and future not in cfg.get("future", {}).get("caches", {}):
        raise ValueError(f"unknown future scenario {future!r}")
    run = _Run(cfg, root)
    rows = [_asset_row(run, float(la), float(lo), future) for la, lo in points]
    used: dict[int, int] = {}
    for r in rows:
        if r["station_id"] is not None:
            used[int(r["station_id"])] = used.get(int(r["station_id"]), 0) + 1
    try:
        import pythermalcomfort

        pt_version = getattr(pythermalcomfort, "__version__", "unknown")
    except ImportError:  # pragma: no cover - pythermalcomfort is a worker requirement
        pt_version = "missing"
    record = {
        "ground_fit": {f"{s}<-{rs}": fit for (s, rs), fit in run._fits.items() if fit is not None},
        "stations_used": used,
        "hazard_data_version": (
            f"KMA ASOS {run.years[0]}-{run.years[1]} May-Sep (local copy), "
            f"MK-PRISM v3.1 0.05 deg daily max, pythermalcomfort {pt_version}"
        ),
        "assumptions": list(ASSUMPTIONS),
        "n_assets": len(rows),
    }
    if future is not None:
        record["future"] = {
            "scenario": future,
            "caches": list(cfg["future"]["caches"][future]),
            "base_years": list(cfg["future"]["base_years"]),
            "future_years": list(cfg["future"]["future_years"]),
        }
    log.info("open-field UTCI: %d assets, %d stations", len(rows), len(used))
    station_days = {
        stn: {**run.station_heat_days(stn), "n_assets": n} for stn, n in sorted(used.items())
    }
    return {
        "per_asset": rows,
        "sub_check": sub_check(rows, cfg["check"], station_days),
        "record": record,
    }

