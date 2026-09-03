"""CLIMADA physical-risk computation.

Perils (climada v6.x, all hazard data from the CLIMADA Data API — no petals needed):
  - tropical_cyclone (TC): Emanuel damage function, per-asset ``v_half`` (vulnerability).
  - river_flood (RF): flood-depth hazard + per-asset depth-damage curve.

Each peril computes the FUTURE horizon and a PRESENT-day baseline, and reports the
delta. Future estimation comes from the Data API's future hazard sets:
  - TC: climate_scenario (rcp26/45/60/85) × ref_year (2040/2060/2080); present = "None".
  - RF: climate_scenario (rcp26/60/85) × year_range; present = historical / 1980_2000.

Per-asset vulnerability params (``tc_v_half``, flood depth-damage curve) are resolved
by the backend and arrive in each asset dict — this module reads them directly.

Returns plain dicts matching ``climaterisk.engines.base``.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from climaterisk_worker import catalog
from climaterisk_worker._dataapi import resilient_get_hazard
from climaterisk_worker._params import (
    RF_SCENARIO_MAP as _RF_SCENARIO_MAP,
)
from climaterisk_worker._params import (
    RF_YEAR_RANGES as _RF_YEAR_RANGES,
)
from climaterisk_worker._params import (
    TC_REF_YEARS as _TC_REF_YEARS,
)
from climaterisk_worker._params import (
    nearest as _nearest,
)

_RETURN_PERIODS = [10, 25, 50, 100, 250]


# Fraction of the event record a return period may reach. Estimating a 1-in-N-year value
# from a record of exactly N years rests on a single event, so the tail is unstable well
# before the record ends; half the record is the conservative convention.
_RP_RECORD_FRACTION = 0.5


def _resolvable_return_periods(imp, requested: list[float] | None = None):  # type: ignore[no-untyped-def]
    """Trim the requested return periods to what the event set can actually support.

    Two separate limits matter:

    * **the record** — where the rarest event carries annual frequency ``f_min``, nothing
      beyond ``1 / f_min`` years was ever observed, yet ``calc_freq_curve`` will happily
      keep extending the curve;
    * **the sampling error** — near the end of the record a return period is estimated from
      one or two events, so it is noise. We therefore stop at
      ``_RP_RECORD_FRACTION`` of the record.

    An observed 45-summer E-OBS hazard is reported to 22 years, not 45 and certainly not
    250. A Data-API tropical-cyclone set (tens of thousands of low-frequency events) is
    unaffected — its record is far longer than any requested period.

    Args:
        imp: A CLIMADA ``Impact`` (or ``Hazard``) — anything carrying per-event
            ``frequency``. Taken from the impact so every runner calls this identically.
        requested: Return periods in years (defaults to ``_RETURN_PERIODS``).

    Returns:
        ``(periods, max_supported, record_years)`` — the kept periods (never empty: the
        shortest requested period survives so the curve still renders), the applied cap in
        years, and the record length behind it. The last two are None when unknown.
    """
    import numpy as np

    want = list(requested if requested is not None else _RETURN_PERIODS)
    freq = np.asarray(getattr(imp, "frequency", []), dtype=float)
    freq = freq[freq > 0]
    if freq.size == 0:  # pragma: no cover - a computed Impact always has frequencies
        return want, None, None
    record_years = float(1.0 / freq.min())
    max_rp = _RP_RECORD_FRACTION * record_years
    kept = [rp for rp in want if rp <= max_rp]
    return (kept or [min(want)]), max_rp, record_years


def climada_available() -> bool:
    """Return True if the CLIMADA package can be imported in this environment."""
    try:
        import climada  # noqa: F401
    except ImportError:
        return False
    return True


def _per_asset_iso3(lats: list[float], lons: list[float]) -> list[str | None]:
    """Return the ISO3 country for each asset (None where unresolved/ocean)."""
    import numpy as np
    from climada.util import coordinates as u_coord

    codes = [int(c) for c in u_coord.get_country_code(np.array(lats), np.array(lons))]
    out: list[str | None] = []
    for c in codes:
        if c == 0:
            out.append(None)
        else:
            out.append(u_coord.country_to_iso([c], "alpha3")[0])
    return out


def _single_country_iso3(iso3s: list[str | None]) -> str | None:
    """Return the common ISO3 if all assets share one country, else None."""
    uniq = {c for c in iso3s if c is not None}
    return next(iter(uniq)) if len(uniq) == 1 and None not in iso3s else None


def _majority_iso3(iso3s: list[str | None]) -> str | None:
    """Return the most common non-null ISO3, or None if there are none.

    A gridded country exposure inevitably includes cells whose point-in-country lookup
    returns None (coastline, offshore, small islands), so the strict
    :func:`_single_country_iso3` is too brittle for resolving a country hazard layer.
    """
    from collections import Counter

    counts = Counter(c for c in iso3s if c is not None)
    return counts.most_common(1)[0][0] if counts else None


def _footprint_points(geom: dict[str, Any], res_deg: float = 0.02, max_points: int = 64):  # type: ignore[no-untyped-def]
    """Disaggregate a GeoJSON footprint to representative points (lat, lon).

    - Polygon / MultiPolygon → a regular ``res_deg`` grid kept inside the shape.
    - LineString / MultiLineString → points interpolated ALONG the line at ~``res_deg``
      spacing (a line has no interior area, so a grid-contains test would find nothing —
      this is how pipelines / transmission / roads disaggregate).
    Falls back to the centroid for tiny/degenerate shapes; evenly subsamples to ``max_points``.
    """
    import numpy as np
    from shapely.geometry import Point, shape

    g = shape(geom)
    pts: list[tuple[float, float]] = []
    if "LineString" in g.geom_type:
        lines = list(g.geoms) if g.geom_type.startswith("Multi") else [g]
        for line in lines:
            if line.length <= 0:
                continue
            n = max(2, round(line.length / res_deg) + 1)
            pts += [
                (float(p.y), float(p.x))
                for p in (line.interpolate(i / (n - 1), normalized=True) for i in range(n))
            ]
    else:  # Polygon / MultiPolygon
        minx, miny, maxx, maxy = g.bounds
        xs = np.arange(minx, maxx + 1e-9, res_deg)
        ys = np.arange(miny, maxy + 1e-9, res_deg)
        pts = [(float(y), float(x)) for x in xs for y in ys if g.contains(Point(x, y))]
    if not pts:
        c = g.centroid
        return [(float(c.y), float(c.x))]
    if len(pts) > max_points:
        step = len(pts) / max_points
        pts = [pts[int(i * step)] for i in range(max_points)]
    return pts


def _build_exposures(assets: list[dict[str, Any]], impf_col: str, impf_ids: list[int]):  # type: ignore[no-untyped-def]
    """Build a CLIMADA ``Exposures`` and a per-row source-asset index.

    Point assets contribute one row; footprint assets (carrying a GeoJSON ``geometry``)
    are disaggregated to interior grid points with the asset value split evenly across
    them. The returned ``source_idx[row]`` maps each exposure row back to its asset, so
    per-asset impact can be re-aggregated (see ``_eai_by_asset``).
    """
    import numpy as np
    import pandas as pd
    from climada.entity import Exposures

    lats: list[float] = []
    lons: list[float] = []
    vals: list[float] = []
    impfs: list[int] = []
    source_idx: list[int] = []
    for i, a in enumerate(assets):
        geom = a.get("geometry")
        pts = _footprint_points(geom) if geom else None
        if pts:
            v = float(a["value"]) / len(pts)
            for plat, plon in pts:
                lats.append(plat)
                lons.append(plon)
                vals.append(v)
                impfs.append(impf_ids[i])
                source_idx.append(i)
        else:
            lats.append(float(a["lat"]))
            lons.append(float(a["lon"]))
            vals.append(float(a["value"]))
            impfs.append(impf_ids[i])
            source_idx.append(i)

    exp = Exposures(
        pd.DataFrame({"latitude": lats, "longitude": lons, "value": vals, impf_col: impfs}),
        value_unit=assets[0]["currency"] if assets else "USD",
    )
    return exp, np.array(source_idx, dtype=int)


def _eai_by_asset(impact, source_idx, n_assets: int) -> list[float]:  # type: ignore[no-untyped-def]
    """Sum per-row expected-annual-impact back to each original asset (footprint-aware)."""
    import numpy as np

    eai = np.asarray(impact.eai_exp, dtype=float)
    out = np.zeros(n_assets)
    for row, s in enumerate(source_idx):
        out[int(s)] += eai[row]
    return [float(x) for x in out]


def _warn_levels(haz, exp, n_levels: int = 5):  # type: ignore[no-untyped-def]
    """Bin each asset's hazard intensity into ``n_levels`` warning bands (petals ``Warn``).

    Uses CLIMADA petals ``Warn.bin_map`` on the per-asset max hazard intensity (quantile
    thresholds of the positive intensities), returning how many assets fall in each band —
    level 1 = lowest intensity, ``n_levels`` = highest. Returns None when no asset is exposed.
    """
    import numpy as np

    try:
        from climada_petals.engine.warn import Warn

        col = next((c for c in exp.gdf.columns if str(c).startswith("centr_")), None)
        if col is None:
            return None
        centr = exp.gdf[col].to_numpy().astype(int)
        per_centroid = np.asarray(haz.intensity.max(axis=0).todense()).ravel()
        safe = np.clip(centr, 0, len(per_centroid) - 1)
        inten = np.where(centr >= 0, per_centroid[safe], 0.0)
        pos = inten[inten > 0]
        if pos.size == 0:
            return None
        qs = sorted({float(np.quantile(pos, q)) for q in np.linspace(0.0, 0.8, n_levels)})
        thresholds = [*qs, float(pos.max()) + 1.0]  # bin_map needs an upper edge
        binned = np.asarray(Warn.bin_map(inten, thresholds)).ravel()
        counts = [int((binned == lvl).sum()) for lvl in range(1, len(thresholds))]
        return {
            "n_levels": len(counts),
            "counts": counts,
            "thresholds": [round(t, 3) for t in thresholds[:-1]],
            "unit": str(getattr(haz, "units", "") or ""),
        }
    except Exception:
        return None


# Padding (deg) around the exposure bbox when cropping a hazard. Must stay above the
# ~100 km centroid-assignment threshold (≈2° of longitude at 60° latitude) so every
# asset keeps its nearest centroid and the impact is identical to the uncropped run.
_CROP_PAD_DEG = 2.0


def _crop_hazard(haz, lats, lons, pad_deg: float = _CROP_PAD_DEG):  # type: ignore[no-untyped-def]
    """Crop a hazard's centroids to a padded bbox around the given points.

    National/global hazard sets carry far more centroids than a compact portfolio
    touches; ``Hazard.select(extent=...)`` keeps every event and frequency but drops
    the distant centroids, so centroid assignment and warn-level scans run on the
    local window only. Returns the original hazard when cropping fails or is moot.
    """
    import numpy as np

    try:
        la = np.asarray(lats, dtype=float)
        lo = np.asarray(lons, dtype=float)
        if la.size == 0:
            return haz
        sel = haz.select(
            extent=(
                float(lo.min()) - pad_deg,
                float(lo.max()) + pad_deg,
                float(la.min()) - pad_deg,
                float(la.max()) + pad_deg,
            )
        )
        if sel is None or sel.centroids.lat.size == 0:
            return haz
        n0, n1 = haz.centroids.lat.size, sel.centroids.lat.size
        if n1 < n0:
            print(f"cropped hazard to assets bbox ±{pad_deg}°: {n0} → {n1} centroids", flush=True)
        return sel
    except Exception:
        return haz  # any cropping issue → fall back to the full hazard (correct, just slower)


def _impact(exp, impf_set, haz):  # type: ignore[no-untyped-def]
    from climada.engine import ImpactCalc

    haz = _crop_hazard(haz, exp.latitude, exp.longitude)
    imp = ImpactCalc(exp, impf_set, haz).impact(save_mat=True, assign_centroids=True)
    imp._warn = _warn_levels(haz, exp)  # stash warn-level breakdown for the result builder
    return imp


_YEARSET_SEED = 1789  # fixed → reproducible annual-loss sampling (CLAUDE.md: pin seeds)


def _yearset_summary(imp, n_years: int = 100, seed: int = _YEARSET_SEED):  # type: ignore[no-untyped-def]
    """Sample ``n_years`` of annual losses from a per-event impact (CLIMADA yearsets).

    Poisson-samples events into years using the impact's event frequencies, so the mean
    annual loss reproduces AAI while exposing the *distribution* (a bad year vs a median
    year). Returns a summary + the sampled annual-loss series for plotting, or None when
    the impact has no positive-loss events.

    Algorithm:
        $$L_y = \\sum_{e \\in \\text{events sampled into year } y} \\ell_e,\\quad
          n_e \\sim \\text{Poisson}(\\lambda),\\ \\lambda = \\sum_e f_e$$
        ASCII: annual loss = sum of event losses for events Poisson-sampled into that year.
    """
    import numpy as np
    from climada.util.yearsets import impact_yearset

    try:
        at_event = np.asarray(getattr(imp, "at_event", []), dtype=float)
        if at_event.size == 0 or float(np.nansum(at_event)) <= 0:
            return None
        yimp, _ = impact_yearset(imp, sampled_years=list(range(1, n_years + 1)), seed=seed)
        losses = np.asarray(yimp.at_event, dtype=float)
        return {
            "n_years": int(losses.size),
            "mean": float(losses.mean()),
            "p50": float(np.percentile(losses, 50)),
            "p90": float(np.percentile(losses, 90)),
            "p95": float(np.percentile(losses, 95)),
            "p99": float(np.percentile(losses, 99)),
            "max": float(losses.max()),
            "losses": [float(x) for x in losses],
        }
    except Exception:
        return None


def _run_tropical_cyclone(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from climada.entity import ImpactFuncSet
    from climada.entity.impact_funcs.trop_cyclone import ImpfTropCyclone
    from climada.util.api_client import Client

    ref_year = _nearest(_TC_REF_YEARS, max(anchor_years) if anchor_years else _TC_REF_YEARS[0])
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)

    # One Emanuel impact function per distinct v_half; assign each asset to its id.
    v_halves = sorted({round(float(a["tc_v_half"]), 1) for a in assets})
    impf_id_by_v = {v: i + 1 for i, v in enumerate(v_halves)}
    impf_set = ImpactFuncSet(
        [ImpfTropCyclone.from_emanuel_usa(impf_id=i + 1, v_half=v) for i, v in enumerate(v_halves)]
    )
    impf_ids = [impf_id_by_v[round(float(a["tc_v_half"]), 1)] for a in assets]
    exp, src_idx = _build_exposures(assets, "impf_TC", impf_ids)

    client = Client()

    def fetch(scenario: str, year: int | None):  # type: ignore[no-untyped-def]
        props: dict[str, str] = {
            "event_type": "synthetic",
            "model_name": "random_walk",
            "climate_scenario": scenario,
        }
        if year is not None:
            props["ref_year"] = str(year)
        if iso3 is not None:
            try:
                return resilient_get_hazard(
                    client,
                    "tropical_cyclone",
                    properties={**props, "spatial_coverage": "country", "country_iso3alpha": iso3},
                )
            except Exception:
                pass
        return resilient_get_hazard(
            client, "tropical_cyclone", properties={**props, "spatial_coverage": "global"}
        )

    # Future-hazard resolution: local catalog first; then either the Data API's future
    # set (default) or Knutson/Jewson climate-change scaling of the present hazard
    # (opt-in via options["tc_future_method"]=="knutson" — derives a future for any
    # scenario/year, frequency-scaled per Jewson 2022).
    use_knutson = bool(options and options.get("tc_future_method") == "knutson")
    cat_haz = catalog.load_hazard("tropical_cyclone", climate_scenario, iso3 or "global", ref_year)
    present_haz = fetch("None", None)
    if cat_haz is not None:
        future_haz, src = cat_haz, "local catalog"
    elif use_knutson:
        rcp = {"rcp26": "2.6", "rcp45": "4.5", "rcp60": "6.0", "rcp85": "8.5"}.get(
            climate_scenario, "4.5"
        )
        future_haz = present_haz.apply_climate_scenario_knu(scenario=rcp, target_year=ref_year)
        src = f"Knutson/Jewson scaling (rcp{rcp}, {ref_year})"
    else:
        future_haz, src = fetch(climate_scenario, ref_year), f"{iso3 or 'global'} Data API"
    future = _impact(exp, impf_set, future_haz)
    present = _impact(exp, impf_set, present_haz)

    eai = _eai_by_asset(future, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(future)
    fc = future.calc_freq_curve(_rps)
    _ys = _yearset_summary(future)
    _warn = getattr(future, "_warn", None)
    present_aai = float(present.aai_agg)
    future_aai = float(future.aai_agg)
    delta = ((future_aai - present_aai) / present_aai * 100.0) if present_aai > 0 else None

    return {
        "peril": "tropical_cyclone",
        "status": "ok",
        "target_year": ref_year,
        "aai_agg": future_aai,
        "present_aai_agg": present_aai,
        "delta_pct": delta,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"{src}; Emanuel v_half {v_halves}",
    }


def _run_river_flood(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import numpy as np
    from climada.entity import ImpactFunc, ImpactFuncSet
    from climada.util.api_client import Client

    scenario = _RF_SCENARIO_MAP.get(climate_scenario, "rcp60")
    target = max(anchor_years) if anchor_years else 2050
    year_range = next(
        (r for r in _RF_YEAR_RANGES if int(r[:4]) <= target <= int(r[5:])), "2030_2050"
    )
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)

    # One depth-damage ImpactFunc per distinct curve; assign each asset to its id.
    curve_key = [tuple(round(float(x), 4) for x in a["flood_mdr"]) for a in assets]
    distinct = sorted(set(curve_key))
    id_by_curve = {c: i + 1 for i, c in enumerate(distinct)}
    funcs = []
    for curve, fid in id_by_curve.items():
        depths = np.array(assets[curve_key.index(curve)]["flood_depth_m"], dtype=float)
        funcs.append(
            ImpactFunc(
                haz_type="RF",
                id=fid,
                intensity=depths,
                mdd=np.array(curve, dtype=float),
                paa=np.ones_like(depths),
                intensity_unit="m",
                name=f"flood_class_{fid}",
            )
        )
    impf_set = ImpactFuncSet(funcs)
    impf_ids = [id_by_curve[c] for c in curve_key]
    exp, src_idx = _build_exposures(assets, "impf_RF", impf_ids)

    client = Client()

    def fetch(scen: str, yr: str):  # type: ignore[no-untyped-def]
        props = {"climate_scenario": scen, "year_range": yr}
        if iso3 is not None:
            try:
                return resilient_get_hazard(
                    client,
                    "river_flood",
                    properties={**props, "spatial_coverage": "country", "country_iso3alpha": iso3},
                )
            except Exception:
                pass
        return resilient_get_hazard(
            client, "river_flood", properties={**props, "spatial_coverage": "global"}
        )

    cat_haz = catalog.load_hazard("river_flood", climate_scenario, iso3 or "global", target)
    future = _impact(exp, impf_set, cat_haz or fetch(scenario, year_range))
    present = _impact(exp, impf_set, fetch("historical", "1980_2000"))
    src = "local catalog" if cat_haz is not None else f"{iso3 or 'global'} {scenario} {year_range}"

    eai = _eai_by_asset(future, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(future)
    fc = future.calc_freq_curve(_rps)
    _ys = _yearset_summary(future)
    _warn = getattr(future, "_warn", None)
    present_aai = float(present.aai_agg)
    future_aai = float(future.aai_agg)
    delta = ((future_aai - present_aai) / present_aai * 100.0) if present_aai > 0 else None

    return {
        "peril": "river_flood",
        "status": "ok",
        "target_year": int(year_range[5:]),
        "aai_agg": future_aai,
        "present_aai_agg": present_aai,
        "delta_pct": delta,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"{src} RF set",
    }


def _run_wildfire(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from climada.entity import ImpactFunc, ImpactFuncSet
    from climada.util.api_client import Client

    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    if iso3 is None:
        raise ValueError("wildfire requires a single-country portfolio (global set too large)")

    cat_haz = catalog.load_hazard("wildfire", "historical", iso3, 2020)
    haz = cat_haz or resilient_get_hazard(
        Client(), "wildfire", properties={"spatial_coverage": "country", "country_iso3alpha": iso3}
    )
    wf_src = "local catalog" if cat_haz is not None else "Data API"
    htype = haz.haz_type  # "WFseason"; intensity is brightness temperature (K)

    # CLIMADA's published damage form: a logistic (sigmoid) response in brightness temperature
    # (ImpactFunc.from_sigmoid_impf), one per distinct class max. The per-class wf_max_mdd is the
    # asymptote L; midpoint x0=325 K, steepness k over the WFseason intensity range. Replaces the
    # prior hand-rolled step ramp. (ImpfWildfire.from_sigmoid_impf is broken in petals 6.2 — its
    # constructor rejects the `id` the base classmethod passes — so we use the base sigmoid.)
    maxes = sorted({round(float(a["wf_max_mdd"]), 3) for a in assets})
    id_by = {m: i + 1 for i, m in enumerate(maxes)}
    impf_set = ImpactFuncSet(
        [
            ImpactFunc.from_sigmoid_impf(
                intensity=(295.0, 500.0, 5.0), L=m, k=0.035, x0=325.0, haz_type=htype, impf_id=fid
            )
            for m, fid in id_by.items()
        ]
    )
    impf_ids = [id_by[round(float(a["wf_max_mdd"]), 3)] for a in assets]
    exp, src_idx = _build_exposures(assets, f"impf_{htype}", impf_ids)
    imp = _impact(exp, impf_set, haz)
    eai = _eai_by_asset(imp, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(imp)
    fc = imp.calc_freq_curve(_rps)
    _ys = _yearset_summary(imp)
    _warn = getattr(imp, "_warn", None)
    return {
        "peril": "wildfire",
        "status": "ok",
        "target_year": 2020,
        "aai_agg": float(imp.aai_agg),
        "present_aai_agg": None,  # historical-only: no future wildfire set in CLIMADA
        "delta_pct": None,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"{iso3} wildfire ({wf_src}; historical 2001–2020, no future set)",
    }


def _run_european_windstorm(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from climada.entity import ImpactFuncSet
    from climada.entity.impact_funcs.storm_europe import ImpfStormEurope
    from climada.util.api_client import Client

    ssp = {"rcp26": "ssp126", "rcp45": "ssp245", "rcp60": "ssp370", "rcp85": "ssp585"}.get(
        climate_scenario, "ssp585"
    )
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])

    client = Client()
    # CMIP6 future windstorm hazard is published with Europe-wide coverage (not per-country);
    # it covers any European asset. Pick one GCM dataset and fetch by its exact properties.
    base = {"data_source": "CMIP6", "spatial_coverage": "Europe"}
    fut_infos = client.list_dataset_infos(
        data_type="storm_europe", properties={**base, "climate_scenario": ssp}
    )
    if not fut_infos:
        raise ValueError(f"no storm_europe CMIP6 Europe dataset under {ssp}")
    fut = fut_infos[0]
    gcm = fut.properties.get("gcm")

    # Calibrated EU windstorm damage function: Schwierz et al. (default) or Welker et al.,
    # both published/calibrated for WISC-style winter storms (haz_type WS, m/s, id 1).
    which = str((options or {}).get("windstorm_impf", "schwierz")).lower()
    impf = ImpfStormEurope.from_welker() if which == "welker" else ImpfStormEurope.from_schwierz()
    impf_set = ImpactFuncSet([impf])
    exp, src_idx = _build_exposures(assets, "impf_WS", [1] * len(assets))
    future = _impact(
        exp, impf_set, resilient_get_hazard(client, "storm_europe", properties=fut.properties)
    )

    present_aai: float | None = None
    pre_infos = client.list_dataset_infos(
        data_type="storm_europe", properties={**base, "climate_scenario": "None"}
    )
    if pre_infos:
        try:
            present = resilient_get_hazard(
                client, "storm_europe", properties=pre_infos[0].properties
            )
            present_aai = float(_impact(exp, impf_set, present).aai_agg)
        except Exception:
            present_aai = None

    eai = _eai_by_asset(future, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(future)
    fc = future.calc_freq_curve(_rps)
    _ys = _yearset_summary(future)
    _warn = getattr(future, "_warn", None)
    future_aai = float(future.aai_agg)
    delta = (
        ((future_aai - present_aai) / present_aai * 100.0)
        if present_aai and present_aai > 0
        else None
    )
    return {
        "peril": "european_windstorm",
        "status": "ok",
        "target_year": None,
        "aai_agg": future_aai,
        "present_aai_agg": present_aai,
        "delta_pct": delta,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"Europe storm_europe CMIP6 {gcm} {ssp} ({which} impf; SSP future vs present)",
    }


def _run_earthquake(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Earthquake (geophysical — observed catalogue, no climate scenario; MMI intensity)."""
    import numpy as np
    from climada.entity import ImpactFunc, ImpactFuncSet
    from climada.util.api_client import Client

    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    if iso3 is None:
        raise ValueError("earthquake requires a single-country portfolio (global set too large)")

    cat_haz = catalog.load_hazard("earthquake", "observed", iso3, 2020)
    eq_props = {"spatial_coverage": "country", "country_iso3alpha": iso3, "event_type": "observed"}
    haz = cat_haz or resilient_get_hazard(Client(), "earthquake", properties=eq_props)
    htype = haz.haz_type  # "EQ"; intensity is Modified Mercalli Intensity (MMI)
    # Per-class MMI damage function: shared eq_mmi breakpoints, per-asset eq_mdr curve
    # (resolved from the vulnerability class / studio override / EQ preset).
    mmi = np.array(assets[0]["eq_mmi"], dtype=float)
    curve_key = [tuple(round(float(x), 4) for x in a["eq_mdr"]) for a in assets]
    id_by = {c: i + 1 for i, c in enumerate(sorted(set(curve_key)))}
    impf_set = ImpactFuncSet(
        [
            ImpactFunc(
                haz_type=htype,
                id=fid,
                intensity=mmi,
                mdd=np.array(c, dtype=float),
                paa=np.ones_like(mmi),
                intensity_unit="MMI",
                name=f"earthquake_{fid}",
            )
            for c, fid in id_by.items()
        ]
    )
    impf_ids = [id_by[c] for c in curve_key]
    exp, src_idx = _build_exposures(assets, f"impf_{htype}", impf_ids)
    imp = _impact(exp, impf_set, haz)
    eai = _eai_by_asset(imp, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(imp)
    fc = imp.calc_freq_curve(_rps)
    _ys = _yearset_summary(imp)
    _warn = getattr(imp, "_warn", None)
    return {
        "peril": "earthquake",
        "status": "ok",
        "target_year": None,
        "aai_agg": float(imp.aai_agg),
        "present_aai_agg": None,
        "delta_pct": None,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"{iso3} earthquake (observed catalogue; geophysical, no climate scenario)",
    }


def _run_coastal_flood(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coastal flood / sea-level rise — depth-damage on a locally-ingested Aqueduct hazard.

    Coastal flood is not in the CLIMADA Data API, so this runner is catalog-only: ingest
    WRI Aqueduct coastal layers first (``source='aqueduct'``, ``peril='coastal_flood'``).
    Uses the same depth-damage curve fields as river flood (intensity = inundation m).
    """
    import numpy as np
    from climada.entity import ImpactFunc, ImpactFuncSet

    target = max(anchor_years) if anchor_years else 2050
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    region = iso3 or "global"

    future = catalog.load_hazard("coastal_flood", climate_scenario, region, target)
    if future is None:
        raise ValueError(
            "coastal flood has no local hazard for this portfolio — ingest WRI Aqueduct "
            "coastal layers first (Data tab → Fetch & ingest, source 'aqueduct')."
        )

    # One depth-damage ImpactFunc per distinct curve; assign each asset to its id.
    curve_key = [tuple(round(float(x), 4) for x in a["flood_mdr"]) for a in assets]
    distinct = sorted(set(curve_key))
    id_by_curve = {c: i + 1 for i, c in enumerate(distinct)}
    funcs = []
    for curve, fid in id_by_curve.items():
        depths = np.array(assets[curve_key.index(curve)]["flood_depth_m"], dtype=float)
        funcs.append(
            ImpactFunc(
                haz_type="CF",
                id=fid,
                intensity=depths,
                mdd=np.array(curve, dtype=float),
                paa=np.ones_like(depths),
                intensity_unit="m",
                name=f"coastal_flood_{fid}",
            )
        )
    impf_set = ImpactFuncSet(funcs)
    impf_ids = [id_by_curve[c] for c in curve_key]
    exp, src_idx = _build_exposures(assets, "impf_CF", impf_ids)

    fut = _impact(exp, impf_set, future)
    present = catalog.load_hazard("coastal_flood", "historical", region, None)
    present_aai = float(_impact(exp, impf_set, present).aai_agg) if present is not None else None

    eai = _eai_by_asset(fut, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(fut)
    fc = fut.calc_freq_curve(_rps)
    _ys = _yearset_summary(fut)
    _warn = getattr(fut, "_warn", None)
    future_aai = float(fut.aai_agg)
    delta = (
        ((future_aai - present_aai) / present_aai * 100.0)
        if present_aai and present_aai > 0
        else None
    )
    return {
        "peril": "coastal_flood",
        "status": "ok",
        "target_year": target,
        "aai_agg": future_aai,
        "present_aai_agg": present_aai,
        "delta_pct": delta,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"local catalog coastal flood (WRI Aqueduct), region {region}",
    }


def _run_tc_surge(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tropical-cyclone storm surge (climada_petals ``TCSurgeBathtub``).

    Derives a coastal surge-height hazard from the TC wind hazard (catalog-first, same
    resolver as the TC runner) plus a Copernicus DEM (``CLIMATERISK_DEM_PATH``, a manual
    drop-in). Impact uses the depth-damage curve (surge height in m). Degrades with a clear
    error when the DEM is absent.
    """
    import os

    # DEM resolution (cheap; lets the graceful-degradation path run without CLIMADA):
    # explicit env path, else an ingested Copernicus DEM in the local catalog.
    dem = os.environ.get("CLIMATERISK_DEM_PATH")
    if not dem or not os.path.isfile(dem):
        catalog_dem = catalog.catalog_dir() / "dem" / "portfolio_dem.tif"
        dem = str(catalog_dem) if catalog_dem.is_file() else None
    if not dem:
        raise ValueError(
            "TC storm surge needs a Copernicus DEM — ingest one (Data tab → Fetch & ingest, "
            "source 'copdem') or set CLIMATERISK_DEM_PATH."
        )

    import numpy as np
    from climada.entity import ImpactFunc, ImpactFuncSet
    from climada_petals.hazard import TCSurgeBathtub

    from climaterisk_worker.cost_benefit import _tc_hazard  # TC wind resolver (catalog-first)

    ref_year = _nearest(_TC_REF_YEARS, max(anchor_years) if anchor_years else _TC_REF_YEARS[0])
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    slr = float((options or {}).get("sea_level_rise_m", 0.0))

    # Crop the wind field before deriving surge — the bathtub DEM sampling is the
    # expensive step, so restrict it to the assets' window up front.
    wind = _crop_hazard(
        _tc_hazard(iso3, climate_scenario, ref_year),
        [a["lat"] for a in assets],
        [a["lon"] for a in assets],
    )
    surge = TCSurgeBathtub.from_tc_winds(wind, topo_path=dem, add_sea_level_rise=slr)
    htype = surge.haz_type  # surge height (m)

    curve_key = [tuple(round(float(x), 4) for x in a["flood_mdr"]) for a in assets]
    distinct = sorted(set(curve_key))
    id_by_curve = {c: i + 1 for i, c in enumerate(distinct)}
    funcs = []
    for curve, fid in id_by_curve.items():
        depths = np.array(assets[curve_key.index(curve)]["flood_depth_m"], dtype=float)
        funcs.append(
            ImpactFunc(
                haz_type=htype,
                id=fid,
                intensity=depths,
                mdd=np.array(curve, dtype=float),
                paa=np.ones_like(depths),
                intensity_unit="m",
                name=f"tc_surge_{fid}",
            )
        )
    impf_set = ImpactFuncSet(funcs)
    impf_ids = [id_by_curve[c] for c in curve_key]
    exp, src_idx = _build_exposures(assets, f"impf_{htype}", impf_ids)
    imp = _impact(exp, impf_set, surge)
    eai = _eai_by_asset(imp, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(imp)
    fc = imp.calc_freq_curve(_rps)
    _ys = _yearset_summary(imp)
    _warn = getattr(imp, "_warn", None)
    return {
        "peril": "tc_surge",
        "status": "ok",
        "target_year": ref_year,
        "aai_agg": float(imp.aai_agg),
        "present_aai_agg": None,
        "delta_pct": None,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "detail": f"{iso3 or 'global'} TC surge (TCSurgeBathtub bathtub model, SLR +{slr} m)",
    }


# Catalog-first perils that share one shape: load a locally-ingested hazard, apply an
# indicative per-class ramp (max from wf_max_mdd), report the expected-annual metric. Not
# in the CLIMADA Data API → require ingestion first. Damage perils report monetary AAI;
# the yield/productivity perils report a value-equivalent loss flagged via result_kind.
#   peril -> (haz_type, breakpoints, ramp(0..1), unit, scenario_key, hint, result_kind, metric_unit)
_CATALOG_PERILS: dict[str, Any] = {
    "hail": (
        "HL",
        [0.0, 2.0, 4.0, 6.0, 8.0, 10.0],
        [0.0, 0.0, 0.1, 0.3, 0.6, 1.0],
        "cm",
        None,
        "ingest a hail hazard (e.g. MeteoSwiss MESHS) first.",
        "monetary",
        None,
    ),
    "landslide": (
        "LS",
        [0.0, 0.25, 0.5, 0.75, 1.0],
        [0.0, 0.1, 0.3, 0.6, 1.0],
        "probability",
        "historical",
        "ingest a landslide hazard (NASA COOLR) first.",
        "monetary",
        None,
    ),
    "tc_rain": (
        "TR",
        [0.0, 50.0, 100.0, 200.0, 400.0],
        [0.0, 0.05, 0.2, 0.5, 0.9],
        "mm",
        None,
        "ingest a TC-rainfall hazard (climada_petals TCRain) first.",
        "monetary",
        None,
    ),
    "drought": (
        "DR",
        [0.0, 1.0, 2.0, 3.0, 4.0],
        [0.0, 0.1, 0.3, 0.6, 1.0],
        "SPEI",
        "historical",
        "ingest a drought (SPEI) hazard first.",
        "productivity",
        "expected annual productivity loss",
    ),
    "crop_yield": (
        "CY",
        [0.0, 0.1, 0.2, 0.4, 0.6],
        [0.0, 0.25, 0.5, 0.8, 1.0],
        "yield-frac",
        None,
        "ingest a crop-yield (ISIMIP relative_cropyield) hazard first.",
        "yield",
        "expected annual yield loss",
    ),
    "low_flow": (
        "LF",
        [0.0, 0.25, 0.5, 0.75, 1.0],
        [0.0, 0.1, 0.3, 0.6, 1.0],
        "deficit",
        "historical",
        "ingest a low-flow (GloFAS) hazard first.",
        "productivity",
        "expected annual low-flow impact",
    ),
    "heatwave": (
        "HW",
        [0.0, 30.0, 35.0, 40.0, 45.0],
        [0.0, 0.0, 0.1, 0.3, 0.6],
        "degC",
        None,
        "ingest a heat (ERA5-HEAT/UTCI) hazard first.",
        "productivity",
        "expected annual heat-productivity loss",
    ),
}


def _run_catalog_peril(
    peril: str,
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generic catalog-first damage peril (indicative per-class ramp; needs ingestion)."""
    import numpy as np
    from climada.entity import ImpactFunc, ImpactFuncSet

    haz_type, intens, shape, unit, scen_key, hint, result_kind, metric_unit = _CATALOG_PERILS[peril]
    scenario = scen_key or climate_scenario
    target = max(anchor_years) if anchor_years else 2050
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    region = iso3 or "global"

    haz = catalog.load_hazard(peril, scenario, region, target)
    if haz is None:
        raise ValueError(f"{peril} has no local hazard for this portfolio — {hint}")

    intens_arr = np.array(intens, dtype=float)
    shape_arr = np.array(shape, dtype=float)
    maxes = sorted({round(float(a["wf_max_mdd"]), 3) for a in assets})
    id_by = {m: i + 1 for i, m in enumerate(maxes)}
    impf_set = ImpactFuncSet(
        [
            ImpactFunc(
                haz_type=haz_type,
                id=fid,
                intensity=intens_arr,
                mdd=shape_arr * m,
                paa=np.ones_like(intens_arr),
                intensity_unit=unit,
                name=f"{peril}_{fid}",
            )
            for m, fid in id_by.items()
        ]
    )
    impf_ids = [id_by[round(float(a["wf_max_mdd"]), 3)] for a in assets]
    exp, src_idx = _build_exposures(assets, f"impf_{haz_type}", impf_ids)
    imp = _impact(exp, impf_set, haz)
    eai = _eai_by_asset(imp, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(imp)
    fc = imp.calc_freq_curve(_rps)
    _ys = _yearset_summary(imp)
    _warn = getattr(imp, "_warn", None)
    return {
        "peril": peril,
        "status": "ok",
        "target_year": target,
        "aai_agg": float(imp.aai_agg),
        "present_aai_agg": None,
        "delta_pct": None,
        "total_value": float(sum(a["value"] for a in assets)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": _ys,
        "warn_levels": _warn,
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "result_kind": result_kind,
        "metric_unit": metric_unit,
        "detail": f"{region} {peril} (local catalog; indicative {unit} ramp, {result_kind})",
    }


_DEFAULT_HEADCOUNT = 250  # people per site when an asset carries no headcount


def _run_heat_mortality(
    assets: list[dict[str, Any]],
    climate_scenario: str,
    anchor_years: list[int],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Heat-attributable **deaths** among each site's exposed people (not a money loss).

    The health counterpart of the ``heatwave`` peril (which reports productivity). The
    hazard is a season **exceedance degree-days** layer above each location's
    minimum-mortality comfort band; the CLIMADA impact functions carry the age-stratified
    non-linear dose-response, so ``impact = headcount * mdd * paa`` comes out in deaths.

    Exposure headcount comes from ``asset["headcount"]`` when set, else
    ``options["default_headcount"]``, else ``_DEFAULT_HEADCOUNT`` — the assumption used is
    always stated in ``detail``. Each site is split into <65 / >=65 rows using the age
    structure of the nearest reference location, and both bands are computed in one
    ``ImpactCalc`` pass.

    Returns:
        A PhysicalRunResult dict with ``result_kind="mortality"``; ``aai_agg`` and every
        ``per_asset.eai`` are **expected annual deaths**, not currency.
    """
    import numpy as np
    import pandas as pd
    from climada.entity import Exposures

    from climaterisk_worker import heat_mortality as hm

    opts = options or {}
    target = max(anchor_years) if anchor_years else 2050
    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    # A country-wide population grid contains coastal/offshore cells whose ISO3 resolves to
    # None, which would collapse `_single_country_iso3` to None. Callers that already know
    # the country (the modeled-exposure path) state it explicitly; otherwise fall back to
    # the assets' common ISO3, then to the majority non-null ISO3.
    region = str(opts.get("country_iso3") or iso3 or _majority_iso3(iso3s) or "global")

    # Resolve the hazard FIRST so a missing layer fails fast with the standard message.
    haz = catalog.load_hazard("heat_mortality", "historical", region, target)
    if haz is None:
        raise ValueError(
            "heat_mortality has no local hazard for this portfolio — ingest a heat "
            "exceedance-degree-days hazard first (scripts/heatwave_europe.py --register)."
        )

    default_headcount = float(opts.get("default_headcount") or _DEFAULT_HEADCOUNT)
    assumed = [a for a in assets if not float(a.get("headcount") or 0.0) > 0.0]

    impf_set, impf_ids = hm.build_impact_functions(haz_type=haz.haz_type)
    impf_col = f"impf_{haz.haz_type}"

    # One exposure row per (site, age band); value = people in that band at that site.
    lats: list[float] = []
    lons: list[float] = []
    vals: list[float] = []
    impfs: list[int] = []
    source_idx: list[int] = []
    for i, a in enumerate(assets):
        head = float(a.get("headcount") or 0.0) or default_headcount
        share65 = hm.share_over65_for(
            float(a["lat"]), float(a["lon"]), iso3s[i], opts.get("share_over65")
        )
        for band in hm.AGE_BANDS:
            frac = share65 if band.key == "o65" else 1.0 - share65
            lats.append(float(a["lat"]))
            lons.append(float(a["lon"]))
            vals.append(head * frac)
            impfs.append(impf_ids[band.key])
            source_idx.append(i)

    exp = Exposures(
        pd.DataFrame({"latitude": lats, "longitude": lons, "value": vals, impf_col: impfs}),
        value_unit="persons",
    )
    src_idx = np.array(source_idx, dtype=int)
    imp = _impact(exp, impf_set, haz)
    eai = _eai_by_asset(imp, src_idx, len(assets))
    _rps, _max_rp, _rec_yrs = _resolvable_return_periods(imp)
    fc = imp.calc_freq_curve(_rps)
    head_note = (
        f"headcount assumed {default_headcount:.0f}/site for {len(assumed)} of {len(assets)} assets"
        if assumed
        else "headcount taken from every asset"
    )
    return {
        "peril": "heat_mortality",
        "status": "ok",
        "target_year": target,
        "aai_agg": float(imp.aai_agg),
        "present_aai_agg": None,
        "delta_pct": None,
        "total_value": float(sum(v for v in vals)),
        "per_asset": [
            {"id": a["id"], "lat": a["lat"], "lon": a["lon"], "eai": eai[i], "country": iso3s[i]}
            for i, a in enumerate(assets)
        ],
        "yearset": None,
        "warn_levels": getattr(imp, "_warn", None),
        "freq_curve": {
            "return_periods": [float(x) for x in fc.return_per],
            "impact": [float(x) for x in fc.impact],
            # Applied cap and the record behind it: periods past the cap were dropped
            # rather than extrapolated (see _resolvable_return_periods).
            "max_resolvable_return_period": _max_rp,
            "record_years": _rec_yrs,
        },
        "result_kind": "mortality",
        "metric_unit": "expected annual heat-attributable deaths",
        "detail": (
            f"{region} heat mortality (local catalog; exceedance degree-days above the "
            f"minimum-mortality comfort band, age-stratified dose-response; {head_note})"
        ),
    }


_RUNNERS = {
    "tropical_cyclone": _run_tropical_cyclone,
    "heat_mortality": _run_heat_mortality,
    "river_flood": _run_river_flood,
    "wildfire": _run_wildfire,
    "european_windstorm": _run_european_windstorm,
    "earthquake": _run_earthquake,
    "coastal_flood": _run_coastal_flood,
    "tc_surge": _run_tc_surge,
    # Catalog-first damage perils (need ingestion); bound via a shared runner.
    **{p: partial(_run_catalog_peril, p) for p in _CATALOG_PERILS},
}


def _interpret_result(r: dict[str, Any]) -> str:
    """Plain-language meaning of a peril result — so a 0 (or error) is never ambiguous.

    Distinguishes: no hazard data / not implemented (error), asset outside the hazard
    footprint (0 + no warning bands), hazard present but below the damage threshold
    (0 + warning bands), and a genuine modeled loss (>0).
    """
    status = r.get("status")
    if status == "error":
        return f"Not computed — {(r.get('detail') or 'run failed').rstrip('.')}."
    if status == "engine_not_ready":
        return f"Not available — {(r.get('detail') or 'engine not implemented').rstrip('.')}."
    kind = r.get("result_kind", "monetary")
    aai = float(r.get("aai_agg", 0.0) or 0.0)
    metric = r.get("metric_unit") or ("expected annual loss" if kind == "monetary" else kind)
    if aai > 0:
        return f"Modeled {metric}: the hazard reaches your assets and produces a non-zero impact."
    if not r.get("warn_levels"):
        return (
            "Zero — your assets fall OUTSIDE this hazard's modeled footprint (no hazard "
            "intensity at their locations). Not the same as 'no data': the hazard ran."
        )
    return (
        "Zero — the hazard does reach your assets, but its intensity stays BELOW the "
        "damage threshold of the vulnerability curve (no expected loss)."
    )


def compute_physical_risk(request: dict[str, Any]) -> dict[str, Any]:
    """Run the physical-risk engine for each requested peril.

    Returns a PhysicalRunOutput as a plain dict (one result per peril).
    """
    perils: list[str] = request["perils"]
    scenario: str = request["climate_scenario"]
    anchor_years: list[int] = request["anchor_years"]
    assets: list[dict[str, Any]] = request["assets"]
    options: dict[str, Any] = request.get("options", {})

    results: list[dict[str, Any]] = []
    for i, peril in enumerate(perils, start=1):
        runner = _RUNNERS.get(peril)
        if runner is None:
            results.append(
                {
                    "peril": peril,
                    "status": "engine_not_ready",
                    "detail": f"peril '{peril}' is not yet implemented (Phase 2+).",
                }
            )
            continue
        # Progress line per peril (worker stdout is unbuffered) so a long multi-peril run
        # is observable — the first fetch of each hazard can take a while.
        print(f"[{i}/{len(perils)}] running peril '{peril}' …", flush=True)
        try:
            res = runner(assets, scenario, anchor_years, options)
            results.append(res)
            print(f"[{i}/{len(perils)}] peril '{peril}' → {res.get('status')}", flush=True)
        except Exception as exc:
            results.append(
                {"peril": peril, "status": "error", "detail": f"{type(exc).__name__}: {exc}"}
            )
            print(f"[{i}/{len(perils)}] peril '{peril}' → error: {exc}", flush=True)

    for r in results:  # plain-language meaning per peril (disambiguates every 0 / error)
        r["interpretation"] = _interpret_result(r)

    ok = [r for r in results if r["status"] == "ok"]
    if ok and len(ok) == len(results):
        overall = "ok"
    elif ok:
        overall = "partial"
    elif any(r["status"] == "error" for r in results):
        overall = "error"
    else:
        overall = "engine_not_ready"

    return {"status": overall, "climate_scenario": scenario, "results": results, "detail": None}
