"""Heat days (폭염일수): days with daily maximum temperature at or above a threshold, May-Sep.

Visual-board A5. The hazard is the count of days with ``TAMAX >= 33 degC`` (the KMA 폭염 criterion
on *air temperature*, not 체감온도) in May-Sep, as a mean per year, on the 0.05 deg block grid
that the heatwave hazards use (``kma_scenario._block_mean`` of the 0.01 deg 남한상세 grid).

Main method: delta change
-------------------------
The future daily fields of the KMA AR6 scenarios (``5ENSMN``) are an **ensemble mean of five
regional models, day by day**. Averaging removes each model's own hot spells, so the daily
spread is damped (measured: the July daily standard deviation falls from 3.2 to 2.6 degC), and
the model summer mean also differs from the observed one by a level offset (measured: about
+1.7 degC in July against MK-PRISM, more than 20 years of warming explain). A direct count of
``TAMAX >= 33`` on these days therefore misses the observed count in either direction (the
sub check below measures it). The *delta* of monthly means cancels the level offset and does
not damp the observed variability, so the main method keeps the observed daily series and
adds only the model's change:

.. math::

    T^{\\mathrm{fut}}_{d,c} = T^{\\mathrm{obs}}_{d,c} + \\Delta_{m(d),c},\\qquad
    \\Delta_{m,c} = \\overline{T}^{\\,\\mathrm{fut}}_{m,c} - \\overline{T}^{\\,\\mathrm{base}}_{m,c}

with $d$ an observed day (2000-2019), $c$ a 0.05 deg cell, $m(d)$ its calendar month,
$\\overline{T}^{\\mathrm{mod,base}}$ the model mean of month $m$ over 2021-2030 and
$\\overline{T}^{\\mathrm{mod,fut}}$ over 2051-2060 (degC). The heat days per year of a series are

.. math::

    N_c = \\frac{1}{|Y|}\\sum_{y \\in Y}\\big|\\{d \\in y : T_{d,c} \\ge \\theta\\}\\big|

with $\\theta$ = 33 degC and $Y$ the set of years. ASCII: T_fut = T_obs + (mean_fut(m) -
mean_base(m)); N = mean over years of count(T >= 33).

Sub check (diagnostic only, never a second result)
--------------------------------------------------
* ``bias_check``: the direct count on the model 2021-2030 days against the observed count
  2000-2019, recorded with a warning above ``check.warn_rel_bias``. Its sign is not known in
  advance: the ensemble mean damps the day-to-day spread (fewer extremes) while the model
  summer mean can sit above the observed one, and the two push the count in opposite
  directions. ``climate_bias`` records both (monthly mean offset in degC and spread ratio).
* the direct model count ratio 2051-2060 / 2021-2030 against the delta-method ratio, recorded
  in the metadata.

Heavy imports are deferred; the pure functions need only numpy.
"""

from __future__ import annotations

import datetime as dt
import logging
import tarfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

VARIABLE = "TAMAX"


# ------------------------------------------------------------------------- pure math --
def count_days(tmax: Any, years: Any, threshold: float) -> Any:
    """Mean over distinct years of the number of days with ``tmax >= threshold``.

    Algorithm:
        $$N_c = \\frac{1}{|Y|}\\sum_{y\\in Y}\\sum_{d\\in y}\\mathbb{1}[T_{d,c} \\ge \\theta]$$
        with $T$ in degC, $\\theta$ the threshold (degC), $Y$ the distinct years and $N$ in
        days per year. A NaN day is never counted; a cell with no finite day is NaN (no data),
        not 0. ASCII: N = mean_over_years(count(T >= theta)); NaN if no finite day.

    Args:
        tmax: Daily maximum temperature ``(n_days, ...)`` in degC.
        years: Calendar year of each day, ``(n_days,)``.
        threshold: $\\theta$ in degC.

    Returns:
        Array of shape ``tmax.shape[1:]`` in days per year.
    """
    t = np.asarray(tmax, dtype=float)
    yr = np.asarray(years)
    if t.shape[0] != yr.shape[0]:
        raise ValueError(f"tmax has {t.shape[0]} days but years has {yr.shape[0]}")
    uniq = np.unique(yr)
    out = np.zeros(t.shape[1:], dtype=float)
    with np.errstate(invalid="ignore"):
        for y in uniq:
            out += (t[yr == y] >= threshold).sum(axis=0)
    out /= uniq.size
    out[~np.isfinite(t).any(axis=0)] = np.nan
    return out


def monthly_mean(tmax: Any, months: Any, month: int) -> Any:
    """NaN-aware mean of ``tmax`` over the days of one calendar month (NaN if none)."""
    t = np.asarray(tmax, dtype=float)
    sel = np.asarray(months) == month
    if not sel.any():
        return np.full(t.shape[1:], np.nan)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN (sea) cells -> NaN
        return np.nanmean(t[sel], axis=0)


def delta_change(
    obs: Any, obs_months: Any, base: Any, base_months: Any, fut: Any, fut_months: Any
) -> Any:
    """Observed days shifted by the model's monthly change (the delta method).

    Algorithm:
        $$T^{\\mathrm{fut}}_{d,c} = T^{\\mathrm{obs}}_{d,c} + \\Delta_{m(d),c},\\quad
          \\Delta_{m,c} = \\overline{T}^{\\mathrm{fut}}_{m,c} -
          \\overline{T}^{\\mathrm{base}}_{m,c}$$
        with the monthly means over all model days of that calendar month (degC); a month
        with no model day, or a cell with none, gives NaN (no data). ASCII: T_fut = T_obs +
        mean_fut(month) - mean_base(month).

    Args:
        obs: Observed daily series ``(n_days, ...)`` in degC.
        obs_months: Calendar month (1-12) of each observed day.
        base: Model daily series of the base window ``(n_base, ...)``.
        base_months: Month of each base day.
        fut: Model daily series of the future window ``(n_fut, ...)``.
        fut_months: Month of each future day.

    Returns:
        Array shaped like ``obs`` in degC.
    """
    o = np.asarray(obs, dtype=float)
    om = np.asarray(obs_months)
    out = np.full(o.shape, np.nan)
    for m in np.unique(om):
        delta = monthly_mean(fut, fut_months, int(m)) - monthly_mean(base, base_months, int(m))
        out[om == m] = o[om == m] + delta
    return out


def climate_bias(
    model: Any, model_months: Any, obs: Any, obs_months: Any, months: list[int], mask: Any
) -> dict[str, dict[str, float]]:
    """Level offset and spread ratio of the model daily field against the observed one, per month.

    Algorithm:
        $$\\delta_m = \\overline{T}^{\\mathrm{mod}}_m - \\overline{T}^{\\mathrm{obs}}_m,\\qquad
          \\rho_m = \\overline{s}^{\\mathrm{mod}}_m / \\overline{s}^{\\mathrm{obs}}_m$$
        with $\\overline{T}_m$ the mean over the days of month $m$ and the masked cells (degC),
        and $\\overline{s}_m$ the cell mean of the per-cell standard deviation over those days
        (degC). ASCII: offset = mean_mod(m) - mean_obs(m); spread_ratio = std_mod(m) / std_obs(m).

    Args:
        model: Model daily series ``(n_mod, n_lat, n_lon)`` in degC.
        model_months: Month of each model day.
        obs: Observed daily series ``(n_obs, n_lat, n_lon)``.
        obs_months: Month of each observed day.
        months: Months to report.
        mask: Cells to use, ``(n_lat, n_lon)`` bool.

    Returns:
        ``{month: {"mean_offset_degC", "spread_ratio"}}``.
    """
    mt = np.asarray(model)[:, np.asarray(mask)]
    ot = np.asarray(obs)[:, np.asarray(mask)]
    out: dict[str, dict[str, float]] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for m in months:
            a, b = mt[np.asarray(model_months) == m], ot[np.asarray(obs_months) == m]
            out[str(m)] = {
                "mean_offset_degC": float(np.nanmean(a) - np.nanmean(b)),
                "spread_ratio": float(
                    np.nanmean(np.nanstd(a, axis=0)) / np.nanmean(np.nanstd(b, axis=0))
                ),
            }
    return out


def bias_check(model: Any, obs: Any, warn_rel: float) -> dict[str, Any]:
    """Relative bias of a model count against the observed count (sub check).

    Algorithm:
        $$b = \\frac{\\overline{N}_{\\mathrm{mod}} - \\overline{N}_{\\mathrm{obs}}}
                   {\\overline{N}_{\\mathrm{obs}}}$$
        with the means over the cells finite in both (days per year); warning when
        $|b| > \\tau$. ASCII: b = (mean(model) - mean(obs)) / mean(obs) over common cells.

    Args:
        model: Model heat days per cell.
        obs: Observed heat days per cell (same shape).
        warn_rel: $\\tau$, dimensionless.

    Returns:
        ``{"rel_bias", "warning", "n_cells", "mean_model", "mean_obs"}``.
    """
    m = np.asarray(model, dtype=float).ravel()
    o = np.asarray(obs, dtype=float).ravel()
    if m.shape != o.shape:
        raise ValueError(f"shapes differ: {m.shape} vs {o.shape}")
    ok = np.isfinite(m) & np.isfinite(o)
    if not ok.any():
        return {
            "rel_bias": float("nan"), "warning": True, "n_cells": 0,
            "mean_model": float("nan"), "mean_obs": float("nan"),
        }  # fmt: skip
    mm, mo = float(m[ok].mean()), float(o[ok].mean())
    rel = (mm - mo) / mo if mo != 0 else float("nan")
    return {
        "rel_bias": rel,
        "warning": bool(not np.isfinite(rel) or abs(rel) > warn_rel),
        "n_cells": int(ok.sum()),
        "mean_model": mm,
        "mean_obs": mo,
    }


def count_ratio(base: Any, future: Any) -> dict[str, Any]:
    """National ratio of mean heat days, future over base, on the cells finite in both.

    Algorithm:
        $$r = \\overline{N}_{\\mathrm{fut}} / \\overline{N}_{\\mathrm{base}}$$ over common
        finite cells (dimensionless); NaN if the base mean is 0.
    """
    b = np.asarray(base, dtype=float).ravel()
    f = np.asarray(future, dtype=float).ravel()
    ok = np.isfinite(b) & np.isfinite(f)
    mb, mf = (float(b[ok].mean()), float(f[ok].mean())) if ok.any() else (np.nan, np.nan)
    return {
        "ratio": mf / mb if mb > 0 else float("nan"),
        "mean_base": mb,
        "mean_future": mf,
        "n_cells": int(ok.sum()),
    }


# ----------------------------------------------------------------------------- reading --
@dataclass
class DailyBlock:
    """Daily TAMAX on the 0.05 deg block grid for the chosen months.

    Attributes:
        tmax: ``(n_days, n_lat, n_lon)`` float32, degC (NaN = no data).
        years: Calendar year per day. months: calendar month per day.
        lat, lon: Block-centre coordinates, 1-D (degrees).
    """

    tmax: np.ndarray
    years: np.ndarray
    months: np.ndarray
    lat: np.ndarray
    lon: np.ndarray


def _month_slices(year: int, months: list[int]) -> list[tuple[int, int, int]]:
    """``(month, first day index, one past last)`` per month, zero-based day-of-year."""
    out = []
    for m in months:
        i0 = dt.date(year, m, 1).timetuple().tm_yday - 1
        nxt = dt.date(year + (m == 12), m % 12 + 1, 1)
        out.append((m, i0, (nxt - dt.date(year, 1, 1)).days))
    return out


def _block_coords(lat: np.ndarray, lon: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    from climaterisk_worker.kma_scenario import _block_mean

    shape = (lat.size, lon.size)
    lat_b = _block_mean(np.broadcast_to(lat[:, None], shape), k)[:, 0]
    lon_b = _block_mean(np.broadcast_to(lon[None, :], shape), k)[0, :]
    return lat_b, lon_b


def _kma_files(directory: Path | None) -> tuple[list[Any], list[Any]]:
    """TAMAX ``.nc`` files and ``_asc.tar.gz`` year entries, without opening any archive."""
    from climaterisk_worker import kma_scenario as ks

    d = directory or ks.kma_dir()
    nc = [f for p in sorted(d.glob("*.nc")) if (f := ks.parse_name(p)) and f.variable == VARIABLE]
    asc = [f for a in sorted(d.glob("*_asc.tar.gz")) for f in ks.asc_entries(a)]
    asc = [f for f in asc if f.variable == VARIABLE]
    return nc, asc


def read_observed(
    years: tuple[int, int], months: list[int], block: float, directory: Path | None = None
) -> DailyBlock:
    """MK-PRISM v3.1 TAMAX days of ``months`` for ``years`` (inclusive), block-meaned.

    The 0.005 deg files are subsampled to the 0.01 deg 남한상세 nodes (``kma_scenario._to_native``)
    and block-meaned over ``block / 0.01`` nodes exactly as the heatwave hazards are.
    """
    from climaterisk_worker import kma_scenario as ks

    k = round(block / ks.GRID_RES_DEG)
    nc, _ = _kma_files(directory)
    by_year = {f.year_start: f for f in nc if f.platform_scenario == "historical"}
    chunks: list[np.ndarray] = []
    yrs: list[int] = []
    mons: list[int] = []
    lat_b = lon_b = None
    import xarray as xr

    for y in range(years[0], years[1] + 1):
        f = by_year.get(y)
        if f is None:
            raise FileNotFoundError(f"no MK-PRISM {VARIABLE} file for {y} in the KMA folder")
        with xr.open_dataset(f.path) as ds:
            var, tname, latname, lonname = ks._find_names(ds)
            da = ds[var]
            lat, lon = ds[latname].values, ds[lonname].values
            flip = lat.size > 1 and lat[1] < lat[0]
            fill = da.attrs.get("_FillValue", da.encoding.get("_FillValue"))
            for m, i0, i1 in _month_slices(y, months):
                arr = da.isel({tname: slice(i0, i1)}).transpose(tname, latname, lonname)
                arr = arr.values.astype(np.float32)
                if flip:
                    arr = arr[:, ::-1, :]
                arr, la, lo = ks._to_native(
                    ks._mask_missing(arr, fill), lat[::-1] if flip else lat, lon
                )
                if lat_b is None:
                    lat_b, lon_b = _block_coords(la, lo, k)
                chunks.append(ks._block_mean(arr, k).astype(np.float32))
                yrs += [y] * arr.shape[0]
                mons += [m] * arr.shape[0]
        log.debug("observed %d read, days so far %d", y, len(yrs))
    assert lat_b is not None and lon_b is not None
    return DailyBlock(np.concatenate(chunks), np.array(yrs), np.array(mons), lat_b, lon_b)


def read_model(
    scenario_key: str,
    years: tuple[int, int],
    months: list[int],
    block: float,
    directory: Path | None = None,
) -> DailyBlock:
    """AR6 5ENSMN TAMAX days of ``months`` for ``years``, streamed from the ``_asc.tar.gz``.

    ``scenario_key`` is the platform key (``rcp45`` = SSP245, ``rcp85`` = SSP585). Only the
    lines of the wanted months are parsed; nothing is unpacked to disk.
    """
    from climaterisk_worker import kma_scenario as ks

    k = round(block / ks.GRID_RES_DEG)
    _, asc = _kma_files(directory)
    files = [
        f
        for f in asc
        if f.platform_scenario == scenario_key and years[0] <= f.year_start <= years[1]
    ]
    if not files:
        raise FileNotFoundError(f"no {VARIABLE} archive for {scenario_key} {years}")
    lat, lon = ks.native_grid()
    lat_b, lon_b = _block_coords(lat, lon, k)
    n_lat, n_lon = ks.GRID_SHAPE_LAT_LON
    chunks: list[np.ndarray] = []
    yrs: list[int] = []
    mons: list[int] = []
    for archive in sorted({f.path for f in files}):
        members = {f.member: f.year_start for f in files if f.path == archive and f.member}
        remaining = set(members.values())
        with tarfile.open(archive, "r|gz") as tf:
            for m in tf:
                if not remaining:
                    break
                y = members.get(Path(m.name).name)
                if not m.isfile() or y is None or y not in remaining:
                    continue
                remaining.discard(y)
                src = tf.extractfile(m)
                if src is None:
                    continue
                slices = _month_slices(y, months)
                i_lo, i_hi = slices[0][1], slices[-1][2]
                rows: list[np.ndarray] = []
                for i, line in enumerate(ks._iter_lines(src)):
                    if i < i_lo:
                        continue
                    if i >= i_hi:
                        break
                    rows.append(np.fromstring(line.decode("ascii"), sep=" ", dtype=np.float32))
                if len(rows) != i_hi - i_lo:
                    raise ValueError(f"{m.name}: {len(rows)} day lines, expected {i_hi - i_lo}")
                arr = ks._mask_missing(np.stack(rows).reshape(len(rows), n_lat, n_lon))
                chunks.append(ks._block_mean(arr, k).astype(np.float32))
                for mo, a, b in slices:
                    yrs += [y] * (b - a)
                    mons += [mo] * (b - a)
                log.debug("model %s %d read", scenario_key, y)
        if remaining:
            raise ValueError(f"{archive.name}: years {sorted(remaining)} not found in the archive")
    order = np.argsort(np.array(yrs), kind="stable")
    tmax = np.concatenate(chunks)[order]
    return DailyBlock(tmax, np.array(yrs)[order], np.array(mons)[order], lat_b, lon_b)


def valid_cells(tmax: Any, max_missing_frac: float) -> Any:
    """Cells with data on at least ``1 - max_missing_frac`` of the days (the heatwave rule)."""
    t = np.asarray(tmax)
    return np.isnan(t).mean(axis=0) <= max_missing_frac
