"""Street-canyon heat proxy and labour-productivity pricing for the synthetic city.

No UTCI library is installed, so the heat index here is a **repo-specific proxy**, not
UTCI: air temperature from a return-period law, an urban-heat-island offset, and the sky-view
factor and sunlit fraction of an infinite street canyon give a mean radiant temperature, and
a weighted feels-like temperature drives the repo's heat-productivity curve
(``physical._CATALOG_PERILS["heatwave"]``). Heat is hazard-only in damage terms: nothing
here touches the asset replacement value; the priced exposure is the payroll at risk.
"""

from __future__ import annotations

from typing import Any

import numpy as np

ArrayLike = Any  # float or numpy array; every function broadcasts.


def sky_view_factor(h_over_w: ArrayLike) -> Any:
    """Sky-view factor at the centre of a symmetric infinite street canyon (Oke 1987).

    Algorithm:
        $$\\mathrm{SVF} = \\cos\\bigl(\\arctan(2H/W)\\bigr)$$
        ASCII: SVF = cos(arctan(2 * H / W))

        $H$ is the building height (m), $W$ the street width (m); $H/W$ and SVF are
        dimensionless.

    Args:
        h_over_w: Canyon aspect ratio $H/W$ (dimensionless), scalar or array.

    Returns:
        The sky-view factor in [0, 1], same shape as the input.
    """
    return np.cos(np.arctan(2.0 * np.asarray(h_over_w, dtype=float)))


def sunlit_fraction(h_over_w: ArrayLike, solar_elevation_deg: float) -> Any:
    """Fraction of the street floor in direct sun, sun perpendicular to the canyon.

    Algorithm:
        $$f = \\mathrm{clip}\\bigl(1 - (H/W)/\\tan\\beta,\; 0,\; 1\\bigr)$$
        ASCII: f = clip(1 - (H/W) / tan(beta), 0, 1)

        $\\beta$ is the solar elevation (degrees), $H/W$ the aspect ratio (dimensionless);
        $f$ is dimensionless.

    Args:
        h_over_w: Canyon aspect ratio $H/W$ (dimensionless), scalar or array.
        solar_elevation_deg: Solar elevation $\\beta$ (degrees above the horizon).

    Returns:
        The sunlit fraction in [0, 1], same shape as ``h_over_w``.
    """
    shade = np.asarray(h_over_w, dtype=float) / np.tan(np.radians(solar_elevation_deg))
    return np.clip(1.0 - shade, 0.0, 1.0)


def mean_radiant_temperature(
    t_air_c: ArrayLike, h_over_w: ArrayLike, solar_elevation_deg: float, spec: dict[str, Any]
) -> Any:
    """Street-level mean radiant temperature of the canyon proxy (repo-specific, not UTCI).

    Algorithm:
        $$\\mathrm{MRT} = T + a_{sun}\\,f + a_{sky}\\,\\mathrm{SVF}$$
        ASCII: MRT = T + a_sun * f + a_sky * SVF

        $T$ air temperature (degC), $a_{sun}$ and $a_{sky}$ the sun and sky excesses
        (degC, ``spec["heat"]``), $f$ the sunlit fraction and SVF the sky-view factor
        (both dimensionless).

    Args:
        t_air_c: Local air temperature $T$ (degC), scalar or array (broadcast with
            ``h_over_w``).
        h_over_w: Canyon aspect ratio $H/W$ (dimensionless).
        solar_elevation_deg: Solar elevation $\\beta$ (degrees).
        spec: City specification (uses ``heat.mrt_sun_excess_c`` and ``heat.mrt_sky_excess_c``).

    Returns:
        Mean radiant temperature (degC), broadcast shape.
    """
    hp = spec["heat"]
    return (
        np.asarray(t_air_c, dtype=float)
        + hp["mrt_sun_excess_c"] * sunlit_fraction(h_over_w, solar_elevation_deg)
        + hp["mrt_sky_excess_c"] * sky_view_factor(h_over_w)
    )


def feels_like(t_air_c: ArrayLike, mrt_c: ArrayLike, spec: dict[str, Any]) -> Any:
    """Weighted feels-like temperature (repo-specific proxy, not UTCI).

    Algorithm:
        $$T_{feel} = T + w\\,(\\mathrm{MRT} - T)$$
        ASCII: T_feel = T + w * (MRT - T)

        $T$ air temperature (degC), MRT mean radiant temperature (degC), $w$ the
        dimensionless radiant weight ``spec["heat"]["mrt_weight"]``.

    Args:
        t_air_c: Air temperature $T$ (degC), scalar or array.
        mrt_c: Mean radiant temperature (degC), scalar or array.
        spec: City specification (uses ``heat.mrt_weight``).

    Returns:
        Feels-like temperature (degC), broadcast shape.
    """
    t = np.asarray(t_air_c, dtype=float)
    return t + spec["heat"]["mrt_weight"] * (np.asarray(mrt_c, dtype=float) - t)


def heat_events(spec: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Heatwave events: return periods, layered frequencies and peak air temperatures.

    Algorithm:
        $$f_i = 1/T_i - 1/T_{i+1}\\ (i<n),\\quad f_n = 1/T_n$$
        $$T_{peak}(T) = t_2 + s\\,\\ln(T/2)$$
        ASCII: f_i = 1/T_i - 1/T_(i+1); f_n = 1/T_n; T_peak = t2 + s * ln(T / 2)

        $T_i$ return period (years, ascending), $t_2$ the 2-year peak air temperature
        (degC), $s$ the growth per ln-year (degC), $f_i$ frequency (1/year).

    Args:
        spec: City specification (uses the ``heat`` section).

    Returns:
        ``(return_periods, frequency, t_air_peak_c)`` as float arrays of length ``n_events``.
    """
    hp = spec["heat"]
    rps = np.array(sorted(hp["return_periods"]), dtype=float)
    freq = 1.0 / rps
    freq[:-1] -= 1.0 / rps[1:]
    t_peak = hp["t_air_2y_c"] + hp["t_air_slope_c"] * np.log(rps / 2.0)
    return rps, freq, t_peak


def street_canyons(assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """Canyon aspect ratio $H/W$ per asset from the synthetic building heights.

    Algorithm:
        $$H/W = H_{lot}/W\\ \\text{(lot)},\\quad H/W = \\tfrac12 (H_{n_1}+H_{n_2})/W\\
        \\text{(road)}$$
        ASCII: lot: H/W = H_lot / W; road: H/W = 0.5 * (H_n1 + H_n2) / W

        $H$ building height (m), $W$ street width (m, ``spec["heat"]["street_width_m"]``).

    Args:
        assets: Asset dicts from :func:`climaterisk_worker.twin.build_city` (lots carry
            ``node``, roads carry ``edge`` = two end nodes).
        spec: City specification.

    Returns:
        Array of $H/W$ (dimensionless), one per asset in order.
    """
    from climaterisk_worker.twin.viewer3d import generate_buildings

    height = {b["node"]: b["height_m"] for b in generate_buildings(spec, assets)}
    w = spec["heat"]["street_width_m"]
    out = np.zeros(len(assets))
    for i, a in enumerate(assets):
        if "node" in a:
            out[i] = height[a["node"]] / w
        else:
            n1, n2 = a["edge"]
            out[i] = 0.5 * (height[n1] + height[n2]) / w
    return out


def utci_c(t_air_c: Any, mrt_c: Any, spec: dict[str, Any]) -> np.ndarray:
    """Universal Thermal Climate Index (degC) from ``pythermalcomfort`` (Broede et al. 2012).

    UTCI is the air temperature of a reference environment (10 m wind 0.5 m/s, MRT equal to
    air temperature, 50 % relative humidity) producing the same physiological strain as the
    actual one. It is evaluated with the official operational polynomial; inputs outside its
    validity range (e.g. MRT - T > 70 K, wind outside 0.5-17 m/s) raise instead of being
    silently dropped.

    Algorithm:
        $$\\mathrm{UTCI} = T + f(T,\\; \\mathrm{MRT} - T,\\; v_{10},\\; e)$$
        ASCII: UTCI = T + offset polynomial(T, MRT - T, v10, vapour pressure e)

    $T$ air temperature (degC), MRT mean radiant temperature (degC), $v_{10}$ wind speed at
    10 m (m/s, ``spec["heat"]["wind_10m_ms"]``), $e$ vapour pressure from relative humidity
    ``spec["heat"]["rh_pct"]`` (%).

    Raises:
        ValueError: when any input is outside the UTCI validity range.
    """
    from pythermalcomfort.models import utci

    hp = spec["heat"]
    t = np.asarray(t_air_c, dtype=float)
    tr = np.broadcast_to(np.asarray(mrt_c, dtype=float), np.broadcast(t, mrt_c).shape)
    t = np.broadcast_to(t, tr.shape)
    out = np.asarray(
        utci(
            tdb=t,
            tr=tr,
            v=np.full(t.shape, float(hp["wind_10m_ms"])),
            rh=np.full(t.shape, float(hp["rh_pct"])),
            round_output=False,
        ).utci,
        dtype=float,
    )
    if np.isnan(out).any():
        raise ValueError("UTCI inputs outside the validity range (see pythermalcomfort.utci)")
    return out


def build_heat_hazard(assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """A CLIMADA heat ``Hazard`` (``HW``, degC) whose intensity is street-level UTCI.

    Algorithm:
        $$T_{loc} = T_{peak}(T_i) + u_{kind},\\quad
        I_{ik} = \\mathrm{UTCI}\\big(T_{loc},\\; \\mathrm{MRT}(T_{loc}, H/W_k, \\beta),
          v_{10}, \\mathrm{RH}\\big)$$
        ASCII: T_loc = T_peak + uhi[kind]; I = UTCI(T_loc, MRT(T_loc, H/W, beta), v10, RH)

        $u_{kind}$ urban-heat-island excess (degC), $\\beta$ solar elevation (degrees). The
        MRT is the street-canyon approximation of :func:`mean_radiant_temperature`; UTCI
        itself is the official index (:func:`utci_c`).

    Args:
        assets: Asset dicts from :func:`climaterisk_worker.twin.build_city`.
        spec: City specification.

    Returns:
        A :class:`climada.hazard.Hazard` with ``haz_type="HW"``, ``units="degC"``, one
        event per return period and one centroid per asset.
    """
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    hp = spec["heat"]
    rps, freq, t_peak = heat_events(spec)
    uhi = np.array([hp["uhi_c"][a["kind"]] for a in assets])
    t_loc = t_peak[:, None] + uhi[None, :]
    mrt = mean_radiant_temperature(
        t_loc, street_canyons(assets, spec)[None, :], hp["solar_elevation_deg"], spec
    )
    feel = utci_c(t_loc, mrt, spec)
    return Hazard(
        haz_type="HW",
        units="degC",
        centroids=Centroids(
            lat=np.array([a["latitude"] for a in assets]),
            lon=np.array([a["longitude"] for a in assets]),
        ),
        event_id=np.arange(1, len(rps) + 1),
        event_name=[f"rp{int(r)}" for r in rps],
        date=np.zeros(len(rps), dtype=int),
        frequency=freq,
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(feel),
        fraction=sparse.csr_matrix(np.ones_like(feel)),
    )


def productivity_impact_function(max_mdd: float) -> Any:
    """The repo's heat-productivity curve scaled to a maximum loss fraction.

    Built like ``physical._run_catalog_peril``: intensity points and ramp come from
    ``physical._CATALOG_PERILS["heatwave"]``.

    Algorithm:
        $$\\mathrm{MDD}(I) = m\\,\\mathrm{ramp}(I),\\quad \\mathrm{PAA} = 1$$
        ASCII: mdd = max_mdd * ramp; paa = 1

        $I$ feels-like temperature (degC), $m$ the maximum productivity loss fraction
        (dimensionless), ramp the repo's dimensionless shape.

    Args:
        max_mdd: Maximum loss fraction $m$ (dimensionless).

    Returns:
        A :class:`climada.entity.ImpactFunc` with ``haz_type="HW"``, ``id=1``.
    """
    from climada.entity import ImpactFunc

    from climaterisk_worker import physical

    haz_type, intens, ramp, unit, *_ = physical._CATALOG_PERILS["heatwave"]
    intens_arr = np.array(intens, dtype=float)
    return ImpactFunc(
        haz_type=haz_type,
        id=1,
        intensity=intens_arr,
        mdd=np.array(ramp, dtype=float) * float(max_mdd),
        paa=np.ones_like(intens_arr),
        intensity_unit=unit,
        name=f"heatwave_{max_mdd:g}",
    )


def price_productivity(
    assets: list[dict[str, Any]], hazard: Any, spec: dict[str, Any]
) -> list[dict[str, Any]]:
    """Expected annual labour-productivity loss per asset through CLIMADA ``ImpactCalc``.

    Algorithm:
        $$E_k = n_{kind}\\,w_d\\,D,\\qquad \\mathrm{EAL}_k = \\sum_i f_i\\,E_k\\,
        \\mathrm{MDR}_{kind}(I_{ik})$$
        ASCII: E = workers * daily_wage * days; EAL = sum_i f_i * E * MDR(I_i)

        $n$ workers (people), $w_d$ daily wage (USD/person/day), $D$ heatwave days (days/year),
        $E$ exposure (USD), $f_i$ event frequency (1/year), MDR the productivity curve
        (dimensionless).

    Args:
        assets: Asset dicts from :func:`climaterisk_worker.twin.build_city`.
        hazard: Heat hazard from :func:`build_heat_hazard`.
        spec: City specification.

    Returns:
        One dict per asset, in order: ``kind``, ``exposure_usd``, ``eal_usd``.
    """
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet

    hp = spec["heat"]
    maxes = sorted({round(float(hp["max_mdd"][a["kind"]]), 6) for a in assets})
    id_by = {m: i + 1 for i, m in enumerate(maxes)}
    funcs = []
    for m, fid in id_by.items():
        f = productivity_impact_function(m)
        f.id = fid
        funcs.append(f)
    exposure = np.array(
        [hp["workers"][a["kind"]] * hp["daily_wage_usd"] * hp["heatwave_days"] for a in assets],
        dtype=float,
    )
    frame = pd.DataFrame(
        {
            "latitude": [a["latitude"] for a in assets],
            "longitude": [a["longitude"] for a in assets],
            "value": exposure,
            "impf_HW": [id_by[round(float(hp["max_mdd"][a["kind"]]), 6)] for a in assets],
            # Centroid k is asset k: assign explicitly so coincident coordinates cannot mix up.
            "centr_HW": np.arange(len(assets)),
        }
    )
    exp = Exposures(frame, value_unit="USD")
    imp = ImpactCalc(exp, ImpactFuncSet(funcs), hazard).impact(
        save_mat=False, assign_centroids=False
    )
    eal = np.asarray(imp.eai_exp, dtype=float)
    return [
        {"kind": a["kind"], "exposure_usd": float(exposure[k]), "eal_usd": float(eal[k])}
        for k, a in enumerate(assets)
    ]
