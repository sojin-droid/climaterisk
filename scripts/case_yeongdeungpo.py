#!/usr/bin/env python
"""Yeongdeungpo 쪽방촌 street-heat case twin: UTCI maps, S-DoT rank check, height sensitivity.

Thin CLI over :mod:`climaterisk_worker.twin.case_heat`. Spec:
``assets/libraries/case_yeongdeungpo_spec.json``. Runs in the CLIMADA worker environment::

    CLIMATERISK_DATA_ROOT=~/Data/climaterisk ./.climada-env/bin/python scripts/case_yeongdeungpo.py

Outputs under ``<DATA_ROOT>/derived/case_twins/yeongdeungpo``: ``summary.json``,
``utci_map.png``, ``sensor_check.png``, ``maps.npz`` and the S-DoT subset cache. No raw S-DoT
row is written into the repo.

Algorithm:
    Main method: background air temperature (median of the 1-3 km S-DoT ring at the hour of
    the ring's daily maximum) plus a radiative street environment (cast shadow, SVF, surface
    type) -> mean radiant temperature -> UTCI. Sub check (diagnostic only): Spearman rank of
    modelled UTCI vs observed S-DoT air temperature anomaly at the site sensors.
    $$\\rho=\\mathrm{Spearman}\\bigl(\\overline{U}_s,\\ \\overline{T_s-T_{ring}}\\bigr)$$
    ASCII: rho = Spearman(mean_day UTCI at sensor s, mean_day (T_s - T_ring))

    $s$ sensor, $T_s$ observed air temperature (degC), $T_{ring}$ ring median (degC).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import warnings  # noqa: E402

import numpy as np  # noqa: E402
from climaterisk_worker.twin import case_heat as ch  # noqa: E402
from climaterisk_worker.twin.landcover import green_mask, read_green  # noqa: E402
from climaterisk_worker.twin.site import M_PER_DEG, site_grid  # noqa: E402

from climaterisk import paths  # noqa: E402

logger = logging.getLogger("case_yeongdeungpo")
SPEC_PATH = REPO / "assets" / "libraries" / "case_yeongdeungpo_spec.json"
UTCI_STRONG, UTCI_VERY_STRONG = 32.0, 38.0


def _f(x: float | None) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def main() -> int:
    """Build the twin, run main + low + high height scenarios and write the outputs."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None, help="output dir (default DATA_ROOT/derived/...)")
    args = ap.parse_args()
    warnings.filterwarnings("ignore", message="Mean of empty slice")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    root = paths.data_root()
    out = Path(args.out) if args.out else paths.derived_dir("case_twins", "yeongdeungpo")
    out.mkdir(parents=True, exist_ok=True)
    p = spec["model"]
    ftf = float(spec["floor_to_floor_m"])
    min_rho = float(spec["check"]["min_spearman"])

    # --- S-DoT: sensors, ring profile, background at the daily-max hour -----------------
    sensors, hourly = ch.load_sdot(spec, root, out / "sdot_subset.parquet")
    site_s = sensors[sensors["zone"] == "site"].reset_index(drop=True)
    n_ring = int((sensors["zone"] == "ring").sum())
    prof = ch.ring_profile(sensors, hourly, int(spec["sdot"]["min_ring_sensors"]))
    lo, hi = spec["daylight_hours"]
    days: list[dict[str, Any]] = []
    for date in spec["days"]:
        day = prof.loc[date].reset_index()
        day = day[(day["hour"] >= lo) & (day["hour"] <= hi)]
        row = day.loc[day["t"].idxmax()]
        days.append(
            {
                "date": date,
                "hour": int(row["hour"]),
                "t_ring": float(row["t"]),
                "rh_ring": float(row["rh"]),
                "n_ring_reporting": int(row["n"]),
            }
        )
    background = {d["date"]: {"t": d["t_ring"], "rh": d["rh_ring"]} for d in days}

    # --- domain grid covering the site box and every site sensor, plus a skirt ------------
    w, s, e, n = spec["site"]["bbox"]
    buf = spec["site"]["domain_buffer_m"]
    lat_c = (s + n) / 2
    dlat = buf / M_PER_DEG
    dlon = buf / (M_PER_DEG * np.cos(np.radians(lat_c)))
    dom = (
        min(w, float(site_s["lon"].min())) - dlon,
        min(s, float(site_s["lat"].min())) - dlat,
        max(e, float(site_s["lon"].max())) + dlon,
        max(n, float(site_s["lat"].max())) + dlat,
    )
    grid = site_grid(dom, float(spec["site"]["cell_m"]))
    logger.info("domain grid ny=%d nx=%d cell=%.1f m", grid.ny, grid.nx, grid.cell_m)

    # --- buildings (OSM only), roads, green -------------------------------------------
    osm = paths.external_dir("osm", "south-korea-261013.osm.pbf")
    bld = ch.read_site_buildings(osm, dom, ftf)
    ids = ch.rasterise_ids(grid, bld.geometry)
    footprint = ids > 0
    open_mask = ~footprint
    cen = bld.geometry.centroid
    in_site = ((cen.x >= w) & (cen.x <= e) & (cen.y >= s) & (cen.y <= n)).to_numpy()
    known = bld["height_source"].ne("unknown").to_numpy()
    n_site, n_unknown_site = int(in_site.sum()), int((in_site & ~known).sum())
    p90 = float(np.nanpercentile(bld.loc[in_site & known, "height_m"], 90))
    hts = {
        "main": np.where(known, bld["height_m"].to_numpy(), 0.0),
        "low": np.where(known, bld["height_m"].to_numpy(), ftf),
        "high": np.where(known, bld["height_m"].to_numpy(), p90),
    }
    logger.info(
        "buildings domain=%d site=%d unknown_site=%d p90=%.1f",
        len(bld),
        n_site,
        n_unknown_site,
        p90,
    )

    green = green_mask(grid, read_green(osm, dom, spec)) & open_mask
    road_ids = ch.rasterise_ids(grid, ch.road_geometries(osm, dom, spec["road_widths_m"]))
    road = (road_ids > 0) & open_mask & ~green
    sp = p["surface"]
    surface = {
        k: np.select([green, road], [sp["green"][v], sp["road"][v]], sp["other"][v])
        for k, v in (("albedo", "albedo"), ("dT_sun", "dT_sun_c"), ("dT_shade", "dT_shade_c"))
    }

    # --- sun --------------------------------------------------------------------------
    lat_dom = (dom[1] + dom[3]) / 2
    lon_dom = (dom[0] + dom[2]) / 2
    sun = {}
    for d in days:
        when = dt.datetime.fromisoformat(d["date"]) + dt.timedelta(hours=d["hour"], minutes=7)
        d["elevation_deg"], d["azimuth_deg"] = (
            round(v, 2) for v in ch.solar_position(lat_dom, lon_dom, when)
        )
        sun[d["date"]] = (d["elevation_deg"], d["azimuth_deg"])

    # --- scenarios --------------------------------------------------------------------
    runs: dict[str, Any] = {}
    for name, hv in hts.items():
        hgrid = np.where(footprint, np.concatenate([[0.0], hv])[ids], 0.0)
        runs[name] = ch.run_scenario(grid, hgrid, surface, open_mask, green, background, sun, spec)
        logger.info("scenario %s done", name)

    # --- site statistics --------------------------------------------------------------
    lat, lon = grid.centres()
    site_cells = open_mask & (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)

    def stats(run: dict[str, Any]) -> dict[str, Any]:
        per_day = {}
        for date, r in run["days"].items():
            u = r["utci"][site_cells]
            per_day[date] = {
                "utci_mean_c": _f(np.nanmean(u)),
                "utci_p90_c": _f(np.nanpercentile(u, 90)),
                "share_ge_32": _f(np.mean(u >= UTCI_STRONG)),
                "share_ge_38": _f(np.mean(u >= UTCI_VERY_STRONG)),
                "sunlit_share": _f(np.mean(r["sunlit"][site_cells])),
            }
        keys = next(iter(per_day.values())).keys()
        mean = {k: _f(np.mean([v[k] for v in per_day.values()])) for k in keys}
        return {
            "mean_over_days": mean,
            "per_day": per_day,
            "svf_mean": _f(np.mean(run["svf"][site_cells])),
        }

    # --- sensors: model vs observation (sub check, diagnostic only) ------------------------
    r_cell = float(p["sensor_search_radius_m"])
    obs_anom = np.full((len(site_s), len(days)), np.nan)
    obs_abs = np.full_like(obs_anom, np.nan)
    for j, d in enumerate(days):
        h = hourly[(hourly["date"] == d["date"]) & (hourly["hour"] == d["hour"])]
        t = h.set_index("serial")["t"]
        for i, ser in enumerate(site_s["serial"]):
            if ser in t.index and np.isfinite(t[ser]):
                obs_abs[i, j] = t[ser]
                obs_anom[i, j] = t[ser] - d["t_ring"]

    def model_at_sensors(run: dict[str, Any]) -> np.ndarray:
        cols = [
            ch.sample_at_sensors(
                grid,
                run["days"][d["date"]]["utci"],
                open_mask,
                site_s["lat"].to_numpy(),
                site_s["lon"].to_numpy(),
                r_cell,
            )
            for d in days
        ]
        return np.stack(cols, axis=1)

    def check(run: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
        m = model_at_sensors(run)
        mm = np.where(np.isfinite(obs_anom), m, np.nan)
        with np.errstate(all="ignore"):
            m_mean = np.nanmean(mm, axis=1)
            o_mean = np.nanmean(obs_anom, axis=1)
        res = ch.rank_check(m_mean, o_mean, min_rho)
        res["per_day"] = {
            d["date"]: ch.rank_check(mm[:, j], obs_anom[:, j], min_rho)["spearman"]
            for j, d in enumerate(days)
        }
        res["per_day"] = {k: _f(v) for k, v in res["per_day"].items()}
        res["spearman"] = _f(res["spearman"])
        return res, m_mean

    main_check, m_mean = check(runs["main"])
    main_check.update(
        {
            "min_spearman": min_rho,
            "model_variable": "UTCI",
            "obs_variable": "S-DoT hourly mean air temperature minus ring median",
        }
    )
    sens = {}
    for name in ("low", "high"):
        c, _ = check(runs[name])
        sens[name] = {
            **stats(runs[name]),
            "check_spearman": c["spearman"],
            "unknown_height_set_to_m": ftf if name == "low" else round(p90, 2),
        }
    main_stats = stats(runs["main"])
    spread = {
        k: _f(sens["high"]["mean_over_days"][k] - sens["low"]["mean_over_days"][k])
        for k in main_stats["mean_over_days"]
    }

    # observed vs modelled site-minus-background
    obs_diff_by_day = np.nanmean(obs_anom, axis=0)
    obs_diff = float(np.nanmean(obs_diff_by_day))
    ref_vals = []
    for d in days:
        elev = d["elevation_deg"]
        one = np.ones((1, 1))
        mrt = ch.mean_radiant_temperature_grid(
            one * d["t_ring"],
            one,
            one.astype(bool),
            one * sp["green"]["albedo"],
            one * sp["green"]["dT_sun_c"],
            one * sp["green"]["dT_shade_c"],
            elev,
            d["rh_ring"],
            p,
        )
        ref_vals.append(float(ch.utci(d["t_ring"], mrt, p["wind_10m_ms"], d["rh_ring"]).item()))
    model_utci_site = np.nanmean(model_at_sensors(runs["main"]), axis=0)
    model_diff_utci = float(np.nanmean(model_utci_site - np.array(ref_vals)))
    t_offset_max = abs(p["t_air_offset_per_built_fraction_c"]) + abs(
        p["t_air_offset_per_green_fraction_c"]
    )
    site_minus_bg = {
        "observed_air_temp_c_mean_over_days": _f(obs_diff),
        "observed_by_day_c": {d["date"]: _f(v) for d, v in zip(days, obs_diff_by_day, strict=True)},
        "modelled_air_temp_c": 0.0 if t_offset_max == 0 else None,
        "modelled_air_temp_share_of_observed": 0.0 if t_offset_max == 0 else None,
        "modelled_utci_minus_open_sunlit_grass_utci_c": _f(model_diff_utci),
        "note": "Air-temperature offset coefficients are 0 (no citable Seoul regression), so "
        "the model reproduces none of the observed air-temperature difference by "
        "construction; the radiative signal is in UTCI only. S-DoT reads about "
        "1-1.15 degC above AWS (sensor at ~3 m).",
    }

    assumptions = spec["assumptions"]
    summary = {
        "site": spec["site"]["name"],
        "bbox": spec["site"]["bbox"],
        "cell_m": grid.cell_m,
        "badge": "미검증",
        "days": days,
        "days_rule": spec["days_rule"],
        "n_sensors": len(site_s),
        "n_ring_sensors": n_ring,
        "n_sensors_with_obs": int((np.isfinite(obs_anom).sum(axis=1) > 0).sum()),
        "sensors": [
            {
                "serial": r.serial,
                "address": r.address,
                "dist_m": round(float(r.dist_m), 0),
                "model_utci_mean_c": _f(m),
                "obs_anom_mean_c": _f(np.nanmean(oa)),
                "obs_air_temp_mean_c": _f(np.nanmean(ob)),
                "n_hours_obs": int(np.isfinite(oa).sum()),
            }
            for r, m, oa, ob in zip(site_s.itertuples(), m_mean, obs_anom, obs_abs, strict=True)
        ],
        "check": main_check,
        "site_minus_background": site_minus_bg,
        "n_buildings": n_site,
        "n_buildings_height_known": n_site - n_unknown_site,
        "n_buildings_height_unknown": n_unknown_site,
        "unknown_share": _f(n_unknown_site / max(1, n_site)),
        "n_buildings_by_height_source": {
            k: int((in_site & bld["height_source"].eq(k).to_numpy()).sum())
            for k in ("height", "levels", "unknown")
        },
        "main": {
            **main_stats,
            "unknown_height_treatment": "no shadow (height 0), still a building footprint",
        },
        "height_sensitivity": {**sens, "spread_high_minus_low": spread},
        "wind_10m_ms_assumed": p["wind_10m_ms"],
        "n_assumptions": len(assumptions),
        "assumptions": assumptions,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    np.savez_compressed(
        out / "maps.npz",
        open_mask=open_mask,
        **{
            f"utci_{d['date']}": runs["main"]["days"][d["date"]]["utci"].astype("float32")
            for d in days
        },
    )
    _plots(
        out,
        grid,
        bld,
        known,
        runs["main"],
        days,
        site_s,
        spec,
        m_mean,
        obs_anom,
        obs_diff_by_day,
        main_check,
        (w, s, e, n),
    )
    logger.info(
        "wrote %s (check rho=%s warning=%s)", out, main_check["spearman"], main_check["warning"]
    )
    return 0


def _plots(
    out: Path,
    grid: Any,
    bld: Any,
    known: np.ndarray,
    run: dict[str, Any],
    days: list[dict[str, Any]],
    site_s: Any,
    spec: dict[str, Any],
    m_mean: np.ndarray,
    obs_anom: np.ndarray,
    obs_diff_by_day: np.ndarray,
    chk: dict[str, Any],
    site_bbox: tuple[float, float, float, float],
) -> None:
    """UTCI map (buildings, hatched unknown heights, sensors) and the sensor comparison."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    w, s, e, n = site_bbox
    n_d = len(days)
    extent = (
        grid.west,
        grid.west + grid.nx * grid.cell_m / grid.lon_m_per_deg,
        grid.south,
        grid.south + grid.ny * grid.cell_m / M_PER_DEG,
    )
    # Fixed UTCI thermal-stress classes (Bröde et al. 2012: 26 moderate, 32 strong, 38 very
    # strong, 46 extreme), never the min/max on screen, in the heat family (ColorBrewer Reds,
    # docs/BOARD_COLORS_KO.md in the board branch).
    from matplotlib import colors as mcolors

    utci_bounds = [26.0, 32.0, 38.0, 42.0, 46.0, 50.0]
    utci_cmap = plt.get_cmap("Reds", len(utci_bounds) + 1)
    utci_norm = mcolors.BoundaryNorm(utci_bounds, utci_cmap.N, extend="both")
    cols = min(3, n_d)
    rows = -(-n_d // cols)
    fig, axs = plt.subplots(rows, cols, figsize=(5.2 * cols, 5.0 * rows), squeeze=False)
    im = None
    for k, d in enumerate(days):
        ax = axs[k // cols][k % cols]
        im = ax.imshow(
            run["days"][d["date"]]["utci"],
            origin="lower",
            extent=extent,
            cmap=utci_cmap,
            norm=utci_norm,
        )
        bld[known].boundary.plot(ax=ax, color="0.15", linewidth=0.3)
        bld[~known].plot(ax=ax, facecolor="none", edgecolor="0.25", hatch="////", linewidth=0.2)
        ax.scatter(
            site_s["lon"], site_s["lat"], marker="^", s=60, c="white", edgecolors="k", zorder=5
        )
        ax.set_xlim(w, e)
        ax.set_ylim(s, n)
        ax.set_title(
            f"{d['date']} {d['hour']:02d}h Ta {d['t_ring']:.1f} C sun {d['elevation_deg']:.0f} deg",
            fontsize=9,
        )
        ax.set_xticks([])
        ax.set_yticks([])
    for k in range(n_d, rows * cols):
        axs[k // cols][k % cols].axis("off")
    fig.colorbar(im, ax=axs, label="UTCI (degC)", shrink=0.7)
    fig.suptitle(
        "Yeongdeungpo (jjokbang-chon) area UTCI at the daily-max hour (main run; hatched = "
        "building height unknown; triangles = S-DoT)  [unverified]",
        fontsize=10,
    )
    fig.savefig(out / "utci_map.png", dpi=130)
    plt.close(fig)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
    a1.scatter(m_mean, np.nanmean(obs_anom, axis=1), c="C3")
    for r, x, y in zip(site_s.itertuples(), m_mean, np.nanmean(obs_anom, axis=1), strict=True):
        a1.annotate(r.serial[-3:], (x, y), fontsize=8, xytext=(3, 3), textcoords="offset points")
    rho = chk["spearman"]
    a1.set_xlabel("model UTCI at sensor cell, mean over days (degC)")
    a1.set_ylabel("S-DoT air temp minus ring median, mean over days (degC)")
    a1.set_title(f"Rank check: Spearman {rho}  n={chk['n']}  warning={chk['warning']}", fontsize=9)
    a2.bar([d["date"][5:] for d in days], obs_diff_by_day, color="C0")
    a2.axhline(0, color="k", linewidth=0.6)
    a2.set_ylabel("observed site-sensor mean minus ring median (degC)")
    a2.set_title("Observed site minus background, daily-max hour", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "sensor_check.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
