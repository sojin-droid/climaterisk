"""European heatwave mortality risk — a CLIMADA-native analysis (ESP/ITA/FRA/DEU/GRC/PRT).

Runs in the CLIMADA conda env (``./.climada-env``). It builds a real CLIMADA
object graph — ``Hazard`` / ``Exposures`` / ``ImpactFuncSet`` / ``ImpactCalc`` —
for heat-attributable **mortality**, the health end-point that dominates European
heat risk (the platform's built-in ``heatwave`` peril reports *labour productivity*
instead; this tool is the mortality counterpart).

    python scripts/heatwave_europe.py                    # all countries + figure
    python scripts/heatwave_europe.py --country ITA       # one country only
    python scripts/heatwave_europe.py --register           # file per-country hazards in the catalog
    python scripts/heatwave_europe.py --seasons 2000 --seed 7

Why a Monte-Carlo season ensemble (and why many of them)
--------------------------------------------------------
Heat risk is not the "average summer" — it is the *distribution* of summer severity
(most summers mild, a few catastrophic, e.g. 2003). So we simulate many synthetic
summers of the present-day climate and read the risk metrics off that distribution:
the **mean** (expected-annual deaths), the **median**, and **return-period** seasons
(a "1-in-T-year" season is one whose severity is exceeded on average once every T
years). To estimate a 1-in-100-year tail you need far more than 100 seasons, so the
default ensemble is 1000. Each season shares a Europe-wide + per-country temperature
anomaly (continental heat like 2003/2022 hits several countries at once), which is
what gives the tail its shape.

Why mortality and not a single degC->damage curve
--------------------------------------------------
Heat kills through *cumulative* exposure across a whole warm season, and the
dose-response is strongly **age-stratified** (the >=65 slope is several times the
<65 slope) and **locally adapted**. A scalar "temperature -> damage" impact function
cannot carry that non-linearity. So we split the hazard by age band and let the
hazard *intensity* be the season-integrated excess-risk dose, which makes the CLIMADA
impact function exactly linear and physically interpretable (see ``Algorithm`` in
``build_impact_functions``). Two ``ImpactCalc`` passes (one per band) are summed.

The comfort band (minimum-mortality range), not a single point
--------------------------------------------------------------
The temperature-mortality curve is U-shaped: mortality is lowest across a *plateau*
of comfortable temperatures and rises on both sides (cold below, heat above). We
represent that plateau as a **band** ``[mmt_low, mmt_high]`` per city. Heat-
attributable deaths accrue only above the upper edge ``mmt_high``; the cold arm below
``mmt_low`` is out of scope (heat tool). **Where the band sits and how wide it is =
the city's thermal adaptation**: hot-adapted cities (Cordoba, Athina, Alentejo) have a
high, wide band, so even 35 degC adds few deaths; cool low-adaptation cities (Paris,
Berlin, Porto) have a low, narrow band, so the same 35 degC is far above threshold.

Validation
----------
Calibrated against national heat-mortality surveillance — MoMo (ES), SISMG/ISS (IT),
Santé publique France (FR), RKI/UBA (DE), EODY/NOA (GR), DGS/INSA (PT). Each country
here is a **major-metro sample** (not full national coverage), so absolute totals scale
with the sampled population while the **per-100k rate** is the comparable quantity; a
large majority of decedents are >=65 everywhere, matching the surveillance systems.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "worker"))

HAZ_TYPE = "HW"  # matches the platform's heatwave engine tag (perils.json / physical.py)
INTENSITY_UNIT = "excess_RR_days"  # season-integrated dose (dimensionless * days)
RETURN_PERIODS = (2.0, 5.0, 10.0, 20.0, 50.0, 100.0)  # season severity as a rare-tail measure
# Same tail-truncation convention as the platform runner, imported so the two cannot drift.
# The science (reference-city table, comfort bands, age bands, season ensemble, the two
# heat metrics) lives in the CLIMADA worker package so the platform peril runner
# (`physical._run_heat_mortality`) and this CLI share ONE source of truth.
from climaterisk_worker import eobs as hm_eobs  # noqa: E402
from climaterisk_worker.heat_mortality import (  # noqa: E402
    AGE_BANDS,
    COUNTRY_NAMES,
    REF_CITIES,
    SEASON_DAYS,
    AgeBand,
    RefCity,
    comfort_band,
    excess_rr_dose,
    simulate_seasons,
)
from climaterisk_worker.heat_mortality import country_grid as hm_country_grid  # noqa: E402
from climaterisk_worker.heat_mortality import degree_days_chunked as hm_degree_days  # noqa: E402
from climaterisk_worker.heat_mortality import (  # noqa: E402
    exceedance_degree_days as _exceedance_degree_days_per_season,
)
from climaterisk_worker.heat_mortality import (  # noqa: E402
    observed_country_grid as hm_observed_grid,
)
from climaterisk_worker.heat_mortality import project_age_structure as hm_project_age  # noqa: E402
from climaterisk_worker.heat_mortality import (  # noqa: E402
    standardized_grid as hm_grid,
)
from climaterisk_worker.physical import _RP_RECORD_FRACTION as RP_RECORD_FRACTION  # noqa: E402

Province = RefCity  # this CLI's local name for an exposure point
PROVINCES: tuple[RefCity, ...] = REF_CITIES

__all__ = ["AGE_BANDS", "SEASON_DAYS", "AgeBand", "comfort_band", "excess_rr_dose"]


def exceedance_degree_days(tmax: np.ndarray, provinces: Sequence[Province]) -> np.ndarray:
    """**Mean** per-season heat load above each city's comfort band, in degC-days.

    The adaptation-adjusted hazard, averaged over the ensemble so each city gets one
    number for the driver panel. (The worker module's function of the same name returns
    the full per-season matrix, which is what the platform hazard layer stores.)

    Algorithm:
        $$ D_p = \\frac{1}{S}\\sum_s\\sum_d \\max(T_{p,s,d}-H_p,\\ 0) $$
        ASCII: load[p] = mean_over_seasons( sum_days max(Tmax - comfort_high, 0) )
        Units: degC-days per season.

    Args:
        tmax: Daily Tmax array ``(n_provinces, n_seasons, SEASON_DAYS)``, degC.
        provinces: Active exposure points (supply per-cell ``mmt_high``).

    Returns:
        Array of shape ``(n_provinces,)`` in degC-days per season.
    """
    return _exceedance_degree_days_per_season(tmax, tuple(provinces)).mean(axis=1)


def select_provinces(country: str | None) -> tuple[Province, ...]:
    """Return the active exposure set (all reference cities, or one ISO-3 country)."""
    if country in (None, "all", "ALL"):
        return PROVINCES
    code = country.upper()
    subset = tuple(p for p in PROVINCES if p.country == code)
    if not subset:
        raise SystemExit(f"unknown/empty country {country!r}; known: {sorted(COUNTRY_NAMES)}")
    return subset


def build_hazard(dose: np.ndarray, band_key: str, provinces: Sequence[Province]) -> Any:
    """Assemble a CLIMADA ``Hazard`` for one age band (events = simulated seasons).

    Mapping (faithful, lossless): province -> centroid, simulated season -> event,
    dose -> intensity[event, centroid], each event equally likely (1/n_seasons).

    Args:
        dose: ``(n_provinces, n_seasons)`` excess-RR-days per province-season.
        band_key: Age-band id, tags the hazard type as ``HW_<band>``.
        provinces: Active exposure points (supply centroid coordinates).

    Returns:
        A checked ``climada.hazard.Hazard``.
    """
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    lat = np.array([p.lat for p in provinces])
    lon = np.array([p.lon for p in provinces])
    try:
        centroids = Centroids(lat=lat, lon=lon)
    except TypeError:  # older signature
        centroids = Centroids.from_lat_lon(lat, lon)

    intensity = dose.T  # (n_events, n_centroids)
    n_ev = intensity.shape[0]
    haz = Hazard(
        haz_type=f"{HAZ_TYPE}_{band_key}",
        units=INTENSITY_UNIT,
        centroids=centroids,
        event_id=np.arange(1, n_ev + 1),
        event_name=[f"s{i}" for i in range(1, n_ev + 1)],
        date=np.arange(1, n_ev + 1),
        frequency=np.full(n_ev, 1.0 / n_ev),
        intensity=sparse.csr_matrix(intensity),
        fraction=sparse.csr_matrix((intensity > 0).astype(float)),
    )
    haz.check()
    return haz


# --------------------------------------------------------------------------- #
# Exposures + impact functions.                                               #
# --------------------------------------------------------------------------- #
def build_exposures(band: AgeBand, provinces: Sequence[Province]) -> Any:
    """Population exposure for one age band (value = persons in the band)."""
    from climada.entity import Exposures

    frac = np.array(
        [(p.share_over65 if band.key == "o65" else 1.0 - p.share_over65) for p in provinces]
    )
    pop_band = np.array([p.population for p in provinces]) * frac
    exp = Exposures(
        lat=np.array([p.lat for p in provinces]),
        lon=np.array([p.lon for p in provinces]),
        value=pop_band,
        value_unit="persons",
        crs="EPSG:4326",
    )
    exp.gdf[f"impf_{HAZ_TYPE}_{band.key}"] = 1
    return exp


def build_impact_functions(band: AgeBand) -> Any:
    """Linear dose -> attributable-deaths impact function for one age band.

    Algorithm:
        The dose $I$ already carries the full non-linear temperature response, so
        deaths are exactly linear in it: $\\text{deaths} = \\text{pop}\\times m_0\\times I$.
        ASCII: deaths = population * baseline_daily_mortality * dose
        Hence ``mdd(I) = m0 * I`` (through the origin), ``paa = 1``; CLIMADA's
        ``impact = value * mdd * paa`` yields attributable deaths directly.
    """
    from climada.entity import ImpactFunc, ImpactFuncSet

    dose_grid = np.linspace(0.0, 400.0, 41)  # excess_RR_days; 400 >> any real season
    mdd = band.baseline_daily_mortality * dose_grid
    impf = ImpactFunc(
        haz_type=f"{HAZ_TYPE}_{band.key}",
        id=1,
        intensity=dose_grid,
        mdd=mdd,
        paa=np.ones_like(dose_grid),
        intensity_unit=INTENSITY_UNIT,
        name=f"heat_mortality_{band.key}",
    )
    return ImpactFuncSet([impf])


# --------------------------------------------------------------------------- #
# Orchestration.                                                              #
# --------------------------------------------------------------------------- #
def _return_period_curve(at_event: np.ndarray, n_seasons: int) -> dict[float, float]:
    """Season severity (deaths) at each return period the ensemble actually supports.

    Capped at ``RP_RECORD_FRACTION`` of the ensemble length — the same rule the platform
    runner applies (``physical._RP_RECORD_FRACTION``) — because a 1-in-N-year value drawn
    from an N-season sample rests on a single season. Requested periods beyond the cap are
    dropped, not extrapolated.
    """
    cap = RP_RECORD_FRACTION * n_seasons
    sorted_loss = np.sort(at_event)
    exceed = 1.0 - (np.arange(1, n_seasons + 1) - 0.5) / n_seasons  # Hazen plotting position
    curve = {}
    for rp in RETURN_PERIODS:
        if rp > cap:
            continue
        curve[rp] = float(np.interp(1.0 / rp, exceed[::-1], sorted_loss[::-1]))
    return curve


def run(
    n_seasons: int, seed: int, provinces: Sequence[Province], warming_c: float = 0.0
) -> dict[str, Any]:
    """Run the full CLIMADA heat-mortality pipeline and return structured results."""
    from climada.engine import ImpactCalc

    rng = np.random.default_rng(seed)
    tmax = simulate_seasons(rng, n_seasons, provinces, warming_c)
    n_p = len(provinces)

    eai_by_band: dict[str, np.ndarray] = {}
    death_mat = np.zeros((n_seasons, n_p))  # summed over bands
    for band in AGE_BANDS:
        dose = excess_rr_dose(tmax, band, provinces)
        haz = build_hazard(dose, band.key, provinces)
        exp = build_exposures(band, provinces)
        impfset = build_impact_functions(band)
        imp = ImpactCalc(exp, impfset, haz).impact(assign_centroids=True, save_mat=True)
        eai_by_band[band.key] = np.asarray(imp.eai_exp, dtype=float)
        death_mat += np.asarray(imp.imp_mat.todense())

    eai_total = eai_by_band["u65"] + eai_by_band["o65"]

    countries = sorted({p.country for p in provinces})
    by_country: dict[str, Any] = {}
    for c in countries:
        cols = [i for i, p in enumerate(provinces) if p.country == c]
        at_event_c = death_mat[:, cols].sum(axis=1)
        pop_c = sum(provinces[i].population for i in cols)
        eai_c = float(eai_total[cols].sum())
        worst_c = float(at_event_c.max())
        by_country[c] = {
            "name": COUNTRY_NAMES.get(c, c),
            "population": pop_c,
            "eai_deaths": eai_c,
            "eai_per_100k": 1e5 * eai_c / pop_c,
            "eai_by_band": {b: float(eai_by_band[b][cols].sum()) for b in ("u65", "o65")},
            "max_season_deaths": worst_c,
            "max_season_per_100k": 1e5 * worst_c / pop_c,
            "median_season_deaths": float(np.median(at_event_c)),
            "return_period_deaths": _return_period_curve(at_event_c, n_seasons),
        }

    europe_at_event = death_mat.sum(axis=1)
    europe_pop = sum(p.population for p in provinces)

    # Adaptation-adjusted heat load per city — the hazard axis of the driver panel.
    heat_load = exceedance_degree_days(tmax, provinces)

    per_province = []
    for i, p in enumerate(provinces):
        eai = float(eai_total[i])
        low, high, width = comfort_band(p)
        per_province.append(
            {
                "province": p.name,
                "country": p.country,
                "lat": p.lat,
                "lon": p.lon,
                "population": p.population,
                "share_over65": p.share_over65,
                "eai_deaths": eai,
                "eai_per_100k": 1e5 * eai / p.population,
                "eai_u65": float(eai_by_band["u65"][i]),
                "eai_o65": float(eai_by_band["o65"][i]),
                "comfort_low": low,
                "comfort_high": high,
                "comfort_width": width,
                "tmax_jja_mean": p.tmax_jja_mean,
                "heat_load_degc_days": float(heat_load[i]),
            }
        )
    per_province.sort(key=lambda r: r["eai_deaths"], reverse=True)

    return {
        "n_seasons": n_seasons,
        "seed": seed,
        "warming_c": warming_c,
        "countries": countries,
        "europe_population": europe_pop,
        "europe_eai_deaths": float(eai_total.sum()),
        "europe_eai_per_100k": 1e5 * float(eai_total.sum()) / europe_pop,
        "europe_max_season_deaths": float(europe_at_event.max()),
        "europe_return_period_deaths": _return_period_curve(europe_at_event, n_seasons),
        "by_country": by_country,
        "per_province": per_province,
        "europe_per_event_total": [float(x) for x in europe_at_event],
    }


def register_in_catalog(
    seed: int,
    provinces: Sequence[Province],
    n_seasons: int,
    warming_c: float = 0.0,
    grid_res_deg: float = 0.25,
    use_eobs: bool = True,
    eobs_year_start: int = 1980,
) -> list[str]:
    """Build a degC heat hazard **per country** and file each in the local catalog.

    Lights up the platform's built-in ``heatwave`` peril for each country: the worker
    resolves ``(heatwave, historical, <ISO3>)`` and runs its own indicative
    *productivity* ramp (30/35/40/45 degC). That ramp expects a **temperature**
    intensity, so the per-cell-season intensity here is the season's 95th-percentile
    daily Tmax (degC). (The mortality analysis uses its own ``excess_RR_days`` dose.)

    Returns:
        The catalog-relative file paths of the written HDF5 hazards (one/country).
    """
    from climaterisk_worker import catalog
    from climaterisk_worker.hazard_convert import convert_grid_to_catalog

    rng = np.random.default_rng(seed)
    n_cat = min(n_seasons, 200)  # keep catalog hazards compact
    tmax = simulate_seasons(rng, n_cat, provinces, warming_c)
    tmax_p95 = np.percentile(tmax, 95, axis=2)  # (n_provinces, n_seasons), degC

    written = []
    for c in sorted({p.country for p in provinces}):
        idx = [i for i, p in enumerate(provinces) if p.country == c]
        cells = [
            {"cell_id": f"c{j}", "lat": provinces[i].lat, "lon": provinces[i].lon}
            for j, i in enumerate(idx)
        ]
        observations = [
            {"cell_id": f"c{j}", "year": s + 1, "intensity": float(tmax_p95[i, s]), "valid": True}
            for j, i in enumerate(idx)
            for s in range(n_cat)
        ]
        grid = {
            "peril": "heatwave",
            "haz_type": HAZ_TYPE,
            "units": "degC",
            "climate_scenario": "historical",
            "region": c,
            "year": 2020,
            "source": "heatwave_europe.py synthetic season-p95 Tmax (E-OBS/ERA5-ready)",
            "license": "synthetic; replace with E-OBS/ERA5 for production",
            "cells": cells,
            "observations": observations,
        }
        entry = convert_grid_to_catalog(grid, catalog.catalog_dir())
        catalog.register(entry)
        written.append(str(entry["file"]))

        # …and the `heat_mortality` layer the platform's health peril consumes: season
        # exceedance degree-days above each location's comfort band (degC-days).
        # Preference order:
        #   1. OBSERVED E-OBS summers — real grid, real interannual variability, real years
        #   2. synthetic seasons on a land-masked country grid at `grid_res_deg`
        #   3. synthetic seasons on the reference points only (grid_res_deg = 0)
        hm_years = None
        hm_source = None
        if use_eobs and hm_eobs.available():
            cities, dd, obs_years = hm_observed_grid(c, year_start=eobs_year_start)
            hm_years, hm_source = obs_years, hm_eobs.resolve_tx_file().name
        elif grid_res_deg:
            cities = hm_country_grid(c, res_deg=grid_res_deg)
            dd = hm_degree_days(cities, n_seasons=n_cat, seed=seed, warming_c=warming_c)
        else:
            cities = tuple(provinces[i] for i in idx)
            dd = _exceedance_degree_days_per_season(tmax[idx, :, :], cities)
        hm_entry = convert_grid_to_catalog(
            hm_grid(
                dd,
                cities,
                region=c,
                ref_year=2020,
                years=hm_years,
                source=(
                    f"E-OBS observed exceedance degree-days ({hm_source})" if hm_source else None
                ),
            ),
            catalog.catalog_dir(),
        )
        catalog.register(hm_entry)
        kind = "observed E-OBS" if hm_years is not None else "synthetic"
        written.append(
            f"{hm_entry['file']} ({hm_entry['n_events']}ev x {hm_entry['n_centroids']}ce, {kind})"
        )
    return written


# Consistent country colours for the figure.
_COUNTRY_COLOR = {
    "ESP": "#d62728",
    "ITA": "#2ca02c",
    "FRA": "#1f77b4",
    "DEU": "#9467bd",
    "GRC": "#ff7f0e",
    "PRT": "#8c564b",
}


def _pearson(x: list[float], y: list[float]) -> float:
    """Pearson correlation coefficient (0 if degenerate)."""
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _driver_panel(
    ax: Any,
    rows: list[dict[str, Any]],
    xkey: str,
    xlabel: str,
    title: str,
    highlight: set[str],
    ylim: tuple[float, float],
) -> float:
    """One panel of the naive-vs-adaptation comparison.

    Both panels are drawn by this same function with an identical y-axis, identical
    marker size and colour, and the same labelled cities — so the ONLY thing that
    differs between them is the x variable. That is what makes the contrast readable.

    Returns:
        Pearson r between the x variable and deaths per 100k.
    """
    x = [r[xkey] for r in rows]
    y = [r["eai_per_100k"] for r in rows]
    is_hi = [r["province"] in highlight for r in rows]

    ax.scatter(
        [xv for xv, h in zip(x, is_hi, strict=True) if not h],
        [yv for yv, h in zip(y, is_hi, strict=True) if not h],
        s=46,
        c="#9aa4b0",
        edgecolor="#5b6570",
        linewidth=0.6,
        zorder=2,
    )
    ax.scatter(
        [xv for xv, h in zip(x, is_hi, strict=True) if h],
        [yv for yv, h in zip(y, is_hi, strict=True) if h],
        s=70,
        c="#b03a2e",
        edgecolor="#5b1a12",
        linewidth=0.8,
        zorder=3,
    )
    for r in rows:
        if r["province"] in highlight:
            ax.annotate(
                r["province"],
                (r[xkey], r["eai_per_100k"]),
                fontsize=7,
                color="#333333",
                zorder=4,
                xytext=(5, 3),
                textcoords="offset points",
            )

    r_val = _pearson(x, y)
    if len(x) >= 3 and np.std(x) > 0:  # least-squares trend to show the relationship
        slope, icpt = np.polyfit(x, y, 1)
        xs = np.linspace(min(x), max(x), 50)
        ax.plot(xs, slope * xs + icpt, color="#b03a2e", lw=1.4, ls="--", alpha=0.8, zorder=1)

    ax.set_ylim(*ylim)
    ax.set_title(f"{title}\n(r = {r_val:+.2f}, same y-axis as the other panel)", fontsize=10)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel("Heat deaths per 100k / yr", fontsize=9)
    ax.grid(alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return r_val


def _draw_basemap(ax: Any, rows: list[dict[str, Any]], margin: float = 1.6) -> None:
    """Draw land + country borders under the city markers (a real map, not a scatter).

    Boundaries come from the repo's own **Natural Earth 110m** layer
    (``data/downloads/ne_110m_admin_0_countries.geojson``, the reference boundary
    source registered in ``assets/libraries/data_sources.json``) so the figure
    renders offline. If that file has not been fetched, this falls back to
    cartopy's bundled/downloadable ``LAND``/``BORDERS``/``COASTLINE`` features —
    the same features CLIMADA's own ``climada.util.plot`` map helpers use.

    Args:
        ax: A cartopy ``GeoAxes`` (PlateCarree).
        rows: Per-city result rows, used to frame the map extent.
        margin: Degrees of padding around the city bounding box.
    """
    import cartopy.crs as ccrs

    lons = [r["lon"] for r in rows]
    lats = [r["lat"] for r in rows]
    ax.set_extent(
        [min(lons) - margin, max(lons) + margin, min(lats) - margin, max(lats) + margin],
        crs=ccrs.PlateCarree(),
    )
    ax.set_facecolor("#e8f0f6")  # sea

    local = REPO / "data" / "downloads" / "ne_110m_admin_0_countries.geojson"
    if local.is_file():
        import geopandas as gpd

        world = gpd.read_file(local)
        ax.add_geometries(
            world.geometry,
            crs=ccrs.PlateCarree(),
            facecolor="#f4f1ea",
            edgecolor="#9aa0a6",
            linewidth=0.7,
            zorder=1,
        )
    else:  # pragma: no cover - only when the boundary layer was never fetched
        import cartopy.feature as cfeature

        ax.add_feature(cfeature.LAND, facecolor="#f4f1ea", zorder=1)
        ax.add_feature(cfeature.BORDERS, edgecolor="#9aa0a6", linewidth=0.7, zorder=2)
        ax.add_feature(cfeature.COASTLINE, edgecolor="#7f868c", linewidth=0.7, zorder=2)

    gl = ax.gridlines(draw_labels=True, linewidth=0.4, color="#b9c0c6", alpha=0.7)
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 7}
    gl.ylabel_style = {"size": 7}


def make_figure(results: dict[str, Any], out_path: Path) -> dict[str, float]:
    """Render the four-panel summary figure.

    Layout — the argument the figure makes, top-left to bottom-right:
        (1) WHERE the risk is (a real map: land/borders + per-capita risk colour)
        (2) WHY cities differ (the comfort band = thermal adaptation)
        (3) the NAIVE explanation: absolute summer temperature — weak
        (4) the CORRECT explanation: adaptation-adjusted heat load — strong
    Panels 3 and 4 share one y-axis and differ only in the x variable.

    Returns:
        ``{"r_abs_temp": ..., "r_heat_load": ...}`` — the two correlations.
    """
    import matplotlib

    matplotlib.use("Agg")
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt

    rows = results["per_province"]
    fig = plt.figure(figsize=(13.5, 10.5))
    # Panel 1 is a projected map, so the axes are added individually.
    ax_map = fig.add_subplot(2, 2, 1, projection=ccrs.PlateCarree())
    axes = np.array(
        [[ax_map, fig.add_subplot(2, 2, 2)], [fig.add_subplot(2, 2, 3), fig.add_subplot(2, 2, 4)]],
        dtype=object,
    )

    # ---- (1) Map: sequential single hue = magnitude of per-capita risk --------- #
    ax = axes[0, 0]
    _draw_basemap(ax, rows)
    pop_max = max(r["population"] for r in rows)
    sizes = [40 + 420 * (r["population"] / pop_max) for r in rows]
    sc = ax.scatter(
        [r["lon"] for r in rows],
        [r["lat"] for r in rows],
        c=[r["eai_per_100k"] for r in rows],
        s=sizes,
        cmap="Reds",
        edgecolor="#3f3f3f",
        linewidth=0.7,
        transform=ccrs.PlateCarree(),
        zorder=5,
    )
    cb = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("Heat deaths per 100k / yr", fontsize=9)
    cb.ax.tick_params(labelsize=7)
    top = sorted(rows, key=lambda r: -r["eai_per_100k"])[:5]
    big = sorted(rows, key=lambda r: -r["population"])[:3]
    for r in {q["province"]: q for q in top + big}.values():
        ax.annotate(
            r["province"],
            xy=(r["lon"], r["lat"]),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
            fontsize=7,
            color="#1f1f1f",
            xytext=(6, 4),
            textcoords="offset points",
            zorder=6,
            path_effects=None,
        )
    ax.set_title(
        "Where the risk is — per-capita heat mortality\n"
        "(colour = deaths per 100k, size = population)",
        fontsize=10,
    )

    # ---- (2) Comfort bands: the adaptation signature -------------------------- #
    ax = axes[0, 1]
    prov = sorted(rows, key=lambda r: r["comfort_high"])
    for i, r in enumerate(prov):
        ax.plot(
            [r["comfort_low"], r["comfort_high"]],
            [i, i],
            color=_COUNTRY_COLOR.get(r["country"], "#777777"),
            lw=2.4,
            solid_capstyle="round",
        )
    step = max(1, len(prov) // 18)
    idx = np.arange(len(prov))
    ax.set_yticks(idx[::step])
    ax.set_yticklabels([prov[i]["province"] for i in idx[::step]], fontsize=6)
    ax.set_title(
        "Thermal adaptation — minimum-mortality 'comfort band'\n"
        "(higher & wider = more adapted; heat deaths start above the right edge)",
        fontsize=10,
    )
    ax.set_xlabel("Temperature (degC)", fontsize=9)
    ax.grid(alpha=0.25, lw=0.5, axis="x")
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    # ---- (3)+(4) The comparison: same y-axis, only the x variable changes ----- #
    # Label the cities that carry the flip: hottest, highest load, and the risk
    # extremes — the SAME set in both panels so the eye can track the movement.
    highlight = set()
    highlight.update(r["province"] for r in sorted(rows, key=lambda r: -r["tmax_jja_mean"])[:2])
    highlight.update(
        r["province"] for r in sorted(rows, key=lambda r: -r["heat_load_degc_days"])[:2]
    )
    highlight.update(r["province"] for r in sorted(rows, key=lambda r: -r["eai_per_100k"])[:2])
    highlight.update(r["province"] for r in sorted(rows, key=lambda r: r["eai_per_100k"])[:1])

    y_all = [r["eai_per_100k"] for r in rows]
    pad = 0.08 * (max(y_all) - min(y_all) or 1.0)
    ylim = (min(y_all) - pad, max(y_all) + pad)

    r_abs = _driver_panel(
        axes[1, 0],
        rows,
        "tmax_jja_mean",
        "Mean summer daily Tmax (degC)  —  raw climate",
        "NAIVE: absolute temperature",
        highlight,
        ylim,
    )
    r_load = _driver_panel(
        axes[1, 1],
        rows,
        "heat_load_degc_days",
        "Heat load above the comfort band (degC-days/season)  —  climate MINUS adaptation",
        "ADAPTATION-ADJUSTED: exceedance load",
        highlight,
        ylim,
    )

    scope = "+".join(results["countries"])
    fig.suptitle(
        f"Heat-attributable mortality [{scope}] — CLIMADA, "
        f"{results['n_seasons']} simulated seasons\n"
        f"Absolute temperature explains little (r={r_abs:+.2f}); "
        f"temperature relative to local adaptation explains it (r={r_load:+.2f})",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return {"r_abs_temp": r_abs, "r_heat_load": r_load}


def decompose_climate_vs_ageing(
    provinces: Sequence[Province],
    n_seasons: int,
    seed: int,
    warming_c: float,
    demography_year: int,
) -> dict[str, Any]:
    """Split a future change in heat deaths into a **climate** and an **ageing** part.

    97% of modelled heat deaths fall on the >=65 band, so an ageing population raises heat
    mortality with no warming at all. Reporting a future total without this split silently
    credits warming for demography.

    Runs the same pipeline four times on a 2x2 of (present/warmed climate) x
    (present/projected age structure), reusing one seed so the differences are signal rather
    than Monte-Carlo noise:

        climate_only = warmed(today's people)  - baseline
        ageing_only  = today's climate(older people) - baseline
        interaction  = both - baseline - climate_only - ageing_only

    The interaction term is positive by construction here: warming and ageing multiply (more
    hot days landing on more vulnerable people), so their joint effect exceeds the sum.

    Args:
        provinces: Active exposure points.
        n_seasons: Seasons per corner of the 2x2 (kept modest — four full runs).
        seed: Shared RNG seed, so all four corners see the same weather draws.
        warming_c: Uniform warming applied to the "future climate" corners, degC.
        demography_year: Projection year for the "older people" corners.

    Returns:
        Dict of the four corner totals plus the decomposition, in deaths/yr.
    """
    older = hm_project_age(tuple(provinces), demography_year)

    base = run(n_seasons, seed, provinces, 0.0)["europe_eai_deaths"]
    clim = run(n_seasons, seed, provinces, warming_c)["europe_eai_deaths"]
    age = run(n_seasons, seed, older, 0.0)["europe_eai_deaths"]
    both = run(n_seasons, seed, older, warming_c)["europe_eai_deaths"]

    climate_only = clim - base
    ageing_only = age - base
    return {
        "baseline": base,
        "climate_only_total": clim,
        "ageing_only_total": age,
        "both_total": both,
        "climate_effect": climate_only,
        "ageing_effect": ageing_only,
        "interaction": both - base - climate_only - ageing_only,
        "warming_c": warming_c,
        "demography_year": demography_year,
        "n_seasons": n_seasons,
    }


def _print_decomposition(d: dict[str, Any]) -> None:
    """Print the climate-vs-ageing split (see :func:`decompose_climate_vs_ageing`)."""
    base = d["baseline"]
    total = d["both_total"] - base
    print(
        f"\nwhat drives the future increase? (+{d['warming_c']}degC vs "
        f"{d['demography_year']} age structure, {d['n_seasons']} seasons/corner)"
    )
    print(f"  today's climate, today's people   : {base:8,.0f} deaths/yr  (baseline)")
    print(f"  warmer climate, today's people    : {d['climate_only_total']:8,.0f}")
    print(f"  today's climate, older people     : {d['ageing_only_total']:8,.0f}")
    print(f"  warmer climate, older people      : {d['both_total']:8,.0f}")
    if total <= 0:
        print("  (no net increase to attribute)")
        return
    for label, key in (
        ("climate (warming)", "climate_effect"),
        ("ageing (demography)", "ageing_effect"),
        ("interaction (they multiply)", "interaction"),
    ):
        v = d[key]
        print(f"    {label:28} {v:+8,.0f} deaths/yr  ({100 * v / total:+5.0f}% of the increase)")
    print("  ageing raises heat mortality even with zero warming — hence the split.")


def _print_surge_planning(results: dict[str, Any], observed_years: int | None = None) -> None:
    """Health-system surge basis: the EXCESS over a normal summer, by return period.

    What a planner needs is not the total but the **increment** a rare summer adds on top of
    a median one, since baseline capacity already absorbs the median.

    Two different limits apply, and the tighter one wins:

    * the **ensemble** — a synthetic run of N seasons supports return periods up to N/2;
    * the **observational record** — enlarging a synthetic ensemble does not create evidence.
      With E-OBS present the real record is ~45 summers, so nothing past ~22 years is
      supported no matter how many seasons were simulated.

    Deaths are the modelled quantity; converting deaths to beds needs clinical ratios this
    tool does not own, so those are left to the user rather than buried in a constant.
    """
    obs_cap = RP_RECORD_FRACTION * observed_years if observed_years else None
    print("\nhealth-system surge basis (excess over a MEDIAN summer):")
    print(f"  {'country':10} {'median/yr':>10} {'1-in-T':>8} {'that season':>12} {'excess':>9}")
    for c in results["countries"]:
        cc = results["by_country"][c]
        med = cc["median_season_deaths"]
        curve = {
            rp: v
            for rp, v in cc["return_period_deaths"].items()
            if obs_cap is None or rp <= obs_cap
        }
        if not curve:
            print(f"  {cc['name']:10} {med:10,.0f}   (no return period the record supports)")
            continue
        rp = max(curve)
        sev = curve[rp]
        print(f"  {cc['name']:10} {med:10,.0f} {rp:7.0f}y {sev:12,.0f} {sev - med:+9,.0f}")
    print(
        "  Plan surge (beds, staff, ambulances) against the EXCESS, not the total — baseline\n"
        "  capacity already covers a median summer. Deaths are the modelled quantity; the\n"
        "  deaths -> hospitalisations -> beds ratios are local clinical parameters and are\n"
        "  deliberately NOT assumed here."
    )
    if obs_cap:
        print(
            f"  Truncated at {obs_cap:.0f}y = half of the {observed_years}-summer "
            "OBSERVED record.\n"
            "  A larger synthetic ensemble does not extend what the observations can support."
        )
    else:
        print(
            "  No observed record loaded (synthetic ensemble only), so the cap is half the\n"
            "  ensemble length — treat rare-tail values as model output, not evidence."
        )


def _print_report(results: dict[str, Any], n_points: int) -> None:
    """Human-readable summary (per-capita table, return periods, comfort bands)."""
    warm = f", +{results['warming_c']}degC" if results["warming_c"] else ""
    print(
        f"scope: {', '.join(results['countries'])}   ({n_points} cities, "
        f"{results['n_seasons']} simulated seasons, seed {results['seed']}{warm})\n"
    )

    # Per-country table: absolute + per-100k (deaths per 100,000 sampled people).
    print("heat-attributable deaths (sampled major-metro population):")
    print(
        f"{'country':10} {'pop(M)':>7} {'mean/yr':>8} {'per100k':>8} {'>=65%':>6} "
        f"{'max-yr':>8} {'max/100k':>9}"
    )
    for c in results["countries"]:
        cc = results["by_country"][c]
        pct = 100 * cc["eai_by_band"]["o65"] / cc["eai_deaths"] if cc["eai_deaths"] else 0.0
        print(
            f"{cc['name']:10} {cc['population'] / 1e6:7.1f} {cc['eai_deaths']:8.0f} "
            f"{cc['eai_per_100k']:8.2f} {pct:5.0f}% "
            f"{cc['max_season_deaths']:8.0f} {cc['max_season_per_100k']:9.2f}"
        )
    if len(results["countries"]) > 1:
        print(
            f"{'EUROPE':10} {results['europe_population'] / 1e6:7.1f} "
            f"{results['europe_eai_deaths']:8.0f} {results['europe_eai_per_100k']:8.2f} "
            f"{'':6} {results['europe_max_season_deaths']:8.0f}"
        )
    print(
        "  (per100k = deaths per 100,000 of the sampled population; max-yr = worst single season)"
    )

    # Return-period block — how bad is a RARE season.
    scope_rp = (
        results["europe_return_period_deaths"]
        if len(results["countries"]) > 1
        else results["by_country"][results["countries"][0]]["return_period_deaths"]
    )
    label = "Europe" if len(results["countries"]) > 1 else results["countries"][0]
    print(f"\nhow severe is a RARE season for {label}? (a 1-in-T-year summer)")
    for rp in sorted(scope_rp):
        print(f"  1-in-{int(rp):>3}yr : {scope_rp[rp]:8.0f} heat deaths in that single season")

    # Absolute vs per-capita leaders.
    print("\ntop cities by ABSOLUTE burden (deaths/yr):")
    for r in results["per_province"][:6]:
        print(
            f"  {r['province']:12} {r['country']}  {r['eai_deaths']:7.1f}   "
            f"({r['eai_per_100k']:5.2f}/100k)"
        )
    print("top cities by PER-CAPITA intensity (deaths/100k/yr):")
    for r in sorted(results["per_province"], key=lambda x: x["eai_per_100k"], reverse=True)[:6]:
        print(
            f"  {r['province']:12} {r['country']}  {r['eai_per_100k']:5.2f}/100k   "
            f"({r['eai_deaths']:6.1f} total)"
        )

    # Comfort-band (adaptation) leaders.
    print("\nthermal adaptation — minimum-mortality comfort band [low..heat-onset degC]:")
    hi = sorted(results["per_province"], key=lambda r: r["comfort_high"], reverse=True)[:4]
    lo = sorted(results["per_province"], key=lambda r: r["comfort_high"])[:4]
    for tag, rows in (("most adapted (high & wide)", hi), ("least adapted (low & narrow)", lo)):
        cells = ", ".join(
            f"{r['province']} {r['comfort_low']:.0f}-{r['comfort_high']:.0f}"
            f"(w{r['comfort_width']:.1f})"
            for r in rows
        )
        print(f"  {tag:28}: {cells}")
    print("  (heat deaths accrue only above the upper edge; a higher/wider band = more adapted)")

    # The comparison the bottom row of the figure makes: raw climate vs
    # climate-minus-adaptation, as an explanation of per-capita risk.
    rows = results["per_province"]
    r_abs = _pearson([r["tmax_jja_mean"] for r in rows], [r["eai_per_100k"] for r in rows])
    r_load = _pearson([r["heat_load_degc_days"] for r in rows], [r["eai_per_100k"] for r in rows])
    print("\nwhat explains per-capita risk? (one independent variable at a time)")
    print(f"  absolute summer Tmax             -> r = {r_abs:+.2f}  (raw climate)")
    print(f"  heat load above the comfort band -> r = {r_load:+.2f}  (climate MINUS adaptation)")
    print("  same cities, same y-axis; only the x variable changes.")

    print("\n  city         Tmax  band-top   load  >=65%  pop(k)  per100k")
    drivers = sorted(rows, key=lambda r: r["eai_per_100k"], reverse=True)
    for r in drivers[:5] + drivers[-3:]:
        print(
            f"  {r['province']:12} {r['tmax_jja_mean']:5.1f} {r['comfort_high']:8.1f} "
            f"{r['heat_load_degc_days']:7.0f} {100 * r['share_over65']:5.0f}% "
            f"{r['population'] / 1e3:7.0f} {r['eai_per_100k']:8.2f}"
        )
    hot = max(rows, key=lambda r: r["tmax_jja_mean"])
    risky = max(rows, key=lambda r: r["eai_per_100k"])
    print(
        f"  read it this way: {hot['province']} is the hottest city "
        f"({hot['tmax_jja_mean']:.1f}degC) yet carries {hot['eai_per_100k']:.1f}/100k,\n"
        f"  while {risky['province']} is {hot['tmax_jja_mean'] - risky['tmax_jja_mean']:.1f}degC "
        f"cooler yet carries {risky['eai_per_100k']:.1f}/100k — because its comfort band sits "
        f"{hot['comfort_high'] - risky['comfort_high']:.0f}degC lower,\n"
        f"  so the same weather lands far above the local threshold "
        f"(load {risky['heat_load_degc_days']:.0f} vs {hot['heat_load_degc_days']:.0f} degC-days)."
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--country",
        default="all",
        help="ESP | ITA | FRA | DEU | GRC | PRT | all (default: all)",
    )
    ap.add_argument(
        "--seasons",
        type=int,
        default=1000,
        help="number of Monte-Carlo summers to simulate (more = stabler rare tails)",
    )
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (reproducibility)")
    ap.add_argument(
        "--warming-c",
        type=float,
        default=0.0,
        help="uniform degC offset for a warmer-climate scenario (0 = present)",
    )
    ap.add_argument(
        "--demography-year",
        type=int,
        default=2020,
        help="project the >=65 share to this year (2020 = present); 97%% of heat deaths are >=65",
    )
    ap.add_argument(
        "--decompose",
        action="store_true",
        help="attribute a future increase to climate vs ageing (runs a 2x2; use with "
        "--warming-c and --demography-year)",
    )
    ap.add_argument(
        "--decompose-seasons",
        type=int,
        default=200,
        help="seasons per corner of the --decompose 2x2 (four runs, so keep it modest)",
    )
    ap.add_argument(
        "--register", action="store_true", help="also file per-country hazards in the local catalog"
    )
    ap.add_argument(
        "--no-eobs",
        action="store_true",
        help="ignore an available E-OBS file and use the synthetic season generator",
    )
    ap.add_argument(
        "--eobs-year-start", type=int, default=1980, help="first observed E-OBS summer to use"
    )
    ap.add_argument(
        "--grid-res-deg",
        type=float,
        default=0.25,
        help="heat_mortality hazard grid spacing in degrees (0 = reference points only)",
    )
    ap.add_argument("--out-dir", type=Path, default=REPO / "data" / "heatwave_europe")
    args = ap.parse_args()

    provinces = select_provinces(args.country)
    # The main run uses the requested demography; --decompose then isolates its contribution.
    exposure = (
        provinces
        if args.demography_year == 2020
        else hm_project_age(provinces, args.demography_year)
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = run(args.seasons, args.seed, exposure, args.warming_c)
    results["demography_year"] = args.demography_year

    (args.out_dir / "results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    make_figure(results, args.out_dir / "heatwave_europe.png")
    _print_report(results, len(provinces))
    # The observational record, when one is loaded, bounds the planning tail regardless of
    # how many synthetic seasons were simulated.
    observed_years = None
    if hm_eobs.available():
        try:
            _, _, _obs_years = hm_observed_grid(results["countries"][0])
            observed_years = len(_obs_years)
        except Exception:
            observed_years = None
    _print_surge_planning(results, observed_years)
    if args.decompose:
        _print_decomposition(
            decompose_climate_vs_ageing(
                provinces,
                args.decompose_seasons,
                args.seed,
                args.warming_c,
                args.demography_year,
            )
        )

    if args.register:
        for rel in register_in_catalog(
            args.seed,
            provinces,
            args.seasons,
            args.warming_c,
            args.grid_res_deg,
            use_eobs=not args.no_eobs,
            eobs_year_start=args.eobs_year_start,
        ):
            print(f"\nregistered platform hazard: {rel}")
        print("  (peril=heatwave, scenario=historical, region=<ISO3> per country)")

    print(f"\nwrote {args.out_dir / 'results.json'} and {args.out_dir / 'heatwave_europe.png'}")


if __name__ == "__main__":
    main()
