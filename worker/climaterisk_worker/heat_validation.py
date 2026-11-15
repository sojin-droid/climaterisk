"""KDCA heat-illness check of the open-field UTCI (heat phase 2, design 2026-11-16 §4.2-§4.3).

Pure functions (numpy, pandas): cleaning of the KDCA notification file, daily and yearly
counts per 시도, Spearman rank correlations, the four tests V1-V4 and the pre-registered
decision :func:`decide`. File and API I/O of the real run is in
``scripts/heat_utci_validate_kdca.py``; the station daily series comes from
:func:`climaterisk_worker.heat_utci.station_daily_series`.

Regions: 16 시도 (세종 is merged into 충남 because it did not exist in 2011). The KDCA region
field is the reporting hospital's location up to 2018 and the place of occurrence from 2019, so
the check runs at 시도 level only. Missing is NaN or None, never 0.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

SIDO16: tuple[str, ...] = (
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
)

# name prefix -> region; 세종 split from 충남 in 2012 and is merged back
_STEMS: tuple[tuple[str, str], ...] = (
    ("서울", "서울"),
    ("부산", "부산"),
    ("대구", "대구"),
    ("인천", "인천"),
    ("광주", "광주"),
    ("대전", "대전"),
    ("울산", "울산"),
    ("세종", "충남"),
    ("경기", "경기"),
    ("강원", "강원"),
    ("충청북", "충북"),
    ("충북", "충북"),
    ("충청남", "충남"),
    ("충남", "충남"),
    ("전라북", "전북"),
    ("전북", "전북"),
    ("전라남", "전남"),
    ("전남", "전남"),
    ("경상북", "경북"),
    ("경북", "경북"),
    ("경상남", "경남"),
    ("경남", "경남"),
    ("제주", "제주"),
)

_EPS = 1e-9  # float slack so that a value exactly on a criterion passes (0.32 - 0.02 != 0.30)


def normalise_sido(name: str | None) -> str | None:
    """Map a 시도 name (old or new official form, short or long) to one of :data:`SIDO16`.

    Args:
        name: Raw name, e.g. ``"강원특별자치도"``; None or blank gives None.

    Returns:
        The region in :data:`SIDO16` (세종 maps to 충남), or None if unknown.
    """
    if name is None:
        return None
    text = str(name).strip()
    if not text:
        return None
    if text.startswith(_MERGED_GWANGJU_JEONNAM):
        return None  # 2026 merger: 광주 or 전남 needs the district (see station_sido)
    for stem, region in _STEMS:
        if text.startswith(stem):
            return region
    return None


# The KMA station table (retrieved 2026-11-15) writes post-2026 names. 전남광주통합특별시
# joins the former 광주광역시 (its five 구) and 전라남도; the KDCA rows of 2011-2019 keep them
# apart, so a station there is placed by its district.
_MERGED_GWANGJU_JEONNAM = "전남광주통합특별시"
_GWANGJU_DISTRICTS = frozenset({"동구", "서구", "남구", "북구", "광산구"})


def station_sido(addr: str) -> str | None:
    """Region of a station from its address (None if unknown or empty).

    The first token names the 시도; a leading ``(산지)`` tag is dropped. Under the merged
    전남광주통합특별시 the second token decides: one of the five former 광주광역시 구 gives
    광주, anything else 전남.
    """
    text = str(addr or "").strip()
    if text.startswith("(산지)"):
        text = text[len("(산지)") :].strip()
    tokens = text.split()
    if not tokens:
        return None
    if tokens[0] == _MERGED_GWANGJU_JEONNAM:
        if len(tokens) < 2:
            return None
        return "광주" if tokens[1] in _GWANGJU_DISTRICTS else "전남"
    return normalise_sido(tokens[0])


def load_kdca(path: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    """Read the KDCA notification file and keep one row per case with a date and a region.

    Args:
        path: CSV with the columns 발생일자 and 발생시도 (UTF-8, BOM allowed).

    Returns:
        ``(cases, meta)``. ``cases`` has the columns ``date`` (datetime64, day) and ``sido``
        (in :data:`SIDO16`). ``meta`` counts ``n_rows`` (all data rows), ``n_blank`` (every
        field empty), ``n_unknown_sido`` (not blank, region missing or unknown) and
        ``n_bad_date`` (not blank, date missing or unreadable). Rows in the last two groups
        are left out of ``cases``, never mapped to a guessed region.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    blank = (raw.apply(lambda col: col.str.strip()) == "").all(axis=1)
    body = raw.loc[~blank]
    sido = body["발생시도"].map(normalise_sido)
    date = pd.to_datetime(body["발생일자"].str.strip(), errors="coerce").dt.normalize()
    unknown = sido.isna()
    bad_date = date.isna()
    keep = ~unknown & ~bad_date
    cases = pd.DataFrame({"date": date[keep].to_numpy(), "sido": sido[keep].to_numpy()})
    cases = cases.reset_index(drop=True)
    meta = {
        "n_rows": len(raw),
        "n_blank": int(blank.sum()),
        "n_unknown_sido": int(unknown.sum()),
        "n_bad_date": int((bad_date & ~unknown).sum()),
    }
    log.info("KDCA loaded: %d rows, %d cases kept", meta["n_rows"], len(cases))
    return cases, meta


def season_days(years: tuple[int, int], months: Sequence[int]) -> pd.DatetimeIndex:
    """Every calendar day of ``months`` in the years ``years[0]..years[1]`` (inclusive)."""
    days = pd.date_range(f"{years[0]}-01-01", f"{years[1]}-12-31", freq="D")
    return days[days.month.isin(list(months))]


def daily_counts(
    cases: pd.DataFrame, years: tuple[int, int], months: Sequence[int]
) -> pd.DataFrame:
    """Cases per day and 시도 over the whole season, zeros included.

    Args:
        cases: Output of :func:`load_kdca`.
        years: First and last year, inclusive.
        months: Calendar months in the season.

    Returns:
        Integer frame, index = every season day, columns = :data:`SIDO16`. Cases dated outside
        the season are ignored.
    """
    days = season_days(years, months)
    inside = cases[cases["date"].isin(days)]
    tab = pd.crosstab(inside["date"], inside["sido"])
    out = tab.reindex(index=days, columns=list(SIDO16), fill_value=0).astype(int)
    out.index.name = None
    return out


def spearman(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float | None:
    """Spearman rank correlation over the finite pairs; None if under 3 pairs or constant.

    Algorithm:
        $$\\rho = \\mathrm{corr}\\big(\\mathrm{rank}(x),\\ \\mathrm{rank}(y)\\big)$$
        ASCII: rho = Pearson correlation of the average ranks (ties share the mean rank)

        $x$, $y$ the finite pairs of the two series (any unit); the result is unitless.
        Implemented with pandas ranks and numpy so that no scipy is needed.
    """
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.ptp(x[ok]) == 0.0 or np.ptp(y[ok]) == 0.0:
        return None
    rx = pd.Series(x[ok]).rank(method="average").to_numpy()
    ry = pd.Series(y[ok]).rank(method="average").to_numpy()
    rho = float(np.corrcoef(rx, ry)[0, 1])
    return rho if np.isfinite(rho) else None


def weighted_national(indicator: pd.DataFrame, pop: pd.DataFrame) -> pd.Series:
    """Population-weighted national mean of a per-region indicator.

    Algorithm:
        $$x_t = \\frac{\\sum_{r\\in R_t} P_{r,y(t)}\\,x_{r,t}}{\\sum_{r\\in R_t} P_{r,y(t)}}$$
        ASCII: x_nat(t) = sum(pop[r, year(t)] * x[r, t]) / sum(pop[r, year(t)]) over regions
        with a value at t

        $x_{r,t}$ the indicator of region $r$ at time $t$ (any unit), $P_{r,y}$ the resident
        population of $r$ in year $y$ (persons), $R_t$ the regions with a finite $x$ and $P$.
        The weights are renormalised over $R_t$; no region at $t$ gives NaN.

    Args:
        indicator: Rows = dates (DatetimeIndex), columns = regions.
        pop: Rows = years, columns = regions.

    Returns:
        Series over the dates of ``indicator``.
    """
    cols = [c for c in indicator.columns if c in pop.columns]
    x = indicator[cols].to_numpy(dtype=float)
    w = pop.reindex(index=indicator.index.year)[cols].to_numpy(dtype=float)
    ok = np.isfinite(x) & np.isfinite(w)
    num = np.where(ok, x * w, 0.0).sum(axis=1)
    den = np.where(ok, w, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 0, num / den, np.nan)
    return pd.Series(out, index=indicator.index)


def rate_per_100k(counts_by_year: pd.DataFrame, pop: pd.DataFrame) -> pd.Series:
    """Mean over years of the yearly cases per 100 000 residents, per region.

    Algorithm:
        $$r_r = \\frac{1}{|Y|}\\sum_{y\\in Y} 10^5\\,\\frac{n_{r,y}}{P_{r,y}}$$
        ASCII: rate[r] = mean over years of 1e5 * cases[r, y] / pop[r, y]

        $n_{r,y}$ cases of region $r$ in year $y$, $P_{r,y}$ residents (persons). A year without
        population is skipped (NaN), not counted as 0.

    Args:
        counts_by_year: Rows = years, columns = regions.
        pop: Rows = years, columns = regions.

    Returns:
        Series by region, cases per 100 000 residents per year.
    """
    p = pop.reindex(index=counts_by_year.index, columns=counts_by_year.columns).astype(float)
    p = p.where(p > 0)
    rate = counts_by_year.astype(float) / p * 1e5
    return rate.mean(axis=0, skipna=True)


def annual_from_daily(
    df: pd.DataFrame,
    years: tuple[int, int],
    min_day_fraction: float,
    heat_threshold_c: float = 33.0,
    utci_threshold_c: float = 38.0,
) -> pd.DataFrame:
    """Yearly D38, 95th percentile of daily maximum temperature and heat days from daily series.

    Algorithm:
        $$D_{38,y} = |\\{d\\in y: U^{max}_d \\ge 38\\}|,\\quad
        T_{95,y} = P_{95}\\{T^{max}_d: d\\in y\\},\\quad
        H_y = |\\{d\\in y: T^{max}_d \\ge 33\\}|$$
        ASCII: D38 = count(utci_max >= 38); T95 = percentile(tmax_grid, 95, linear); H =
        count(tmax_grid >= 33), each over the season days of one year

        $U^{max}_d$ daily maximum UTCI (degC), $T^{max}_d$ daily maximum air temperature of the
        grid cell (degC). A year with fewer finite $U^{max}$ days than ``min_day_fraction`` of
        its season days is NaN in all three columns (so that the three are compared on the
        same years), never 0.

    Args:
        df: Daily frame with ``utci_max`` and ``tmax_grid`` (DatetimeIndex).
        years: First and last year, inclusive.
        min_day_fraction: Share of season days that must have a finite ``utci_max``.
        heat_threshold_c: Heat-day threshold on ``tmax_grid`` (degC).
        utci_threshold_c: UTCI threshold (degC).

    Returns:
        Frame indexed by year with ``d38`` (days), ``t95`` (degC) and ``heat_days`` (days).
    """
    rows: dict[int, dict[str, float]] = {}
    for y in range(years[0], years[1] + 1):
        sub = df[df.index.year == y]
        u = sub["utci_max"].to_numpy(dtype=float)
        t = sub["tmax_grid"].to_numpy(dtype=float)
        if len(sub) == 0 or np.isfinite(u).sum() < min_day_fraction * len(sub):
            rows[y] = {"d38": np.nan, "t95": np.nan, "heat_days": np.nan}
            continue
        tf = t[np.isfinite(t)]
        rows[y] = {
            "d38": float((u[np.isfinite(u)] >= utci_threshold_c).sum()),
            "t95": float(np.percentile(tf, 95)) if tf.size else np.nan,
            "heat_days": float((tf >= heat_threshold_c).sum()) if tf.size else np.nan,
        }
    return pd.DataFrame.from_dict(rows, orient="index")[["d38", "t95", "heat_days"]]


# --------------------------------------------------------------------------- #
# The four tests                                                               #
# --------------------------------------------------------------------------- #
def v1_daily_national(
    counts: pd.DataFrame, utci_max: pd.DataFrame, tmax: pd.DataFrame, pop: pd.DataFrame
) -> dict[str, Any]:
    """V1: daily national cases against the population-weighted national daily maximum.

    Algorithm:
        $$\\rho_U = \\mathrm{Spearman}\\big(N_t,\\ \\bar U_t\\big),\\quad
        \\rho_T = \\mathrm{Spearman}\\big(N_t,\\ \\bar T_t\\big)$$
        ASCII: rho_u = spearman(national cases per day, pop-weighted national daily max UTCI);
        rho_t = same with daily max air temperature

        $N_t$ cases of day $t$ over all regions (count), $\\bar U_t$ and $\\bar T_t$ the
        population-weighted means over regions of the daily maximum UTCI and air temperature
        (degC). Spearman is computed over the days where both are finite.

    Returns:
        ``rho_u``, ``rho_t`` (None if not computable) and ``n`` (days where both U and T are
        finite; both correlations use exactly these days).
    """
    national = counts.sum(axis=1).astype(float)
    nu = weighted_national(utci_max, pop).reindex(national.index).to_numpy(dtype=float)
    nt = weighted_national(tmax, pop).reindex(national.index).to_numpy(dtype=float)
    both = np.isfinite(nu) & np.isfinite(nt)  # U and T compared on the same days
    c = national.to_numpy()[both]
    return {
        "rho_u": spearman(c, nu[both]),
        "rho_t": spearman(c, nt[both]),
        "n": int(both.sum()),
    }


def v2_daily_by_sido(
    counts: pd.DataFrame, utci_max: pd.DataFrame, tmax: pd.DataFrame
) -> dict[str, Any]:
    """V2: per-region daily Spearman, summarised by the median over regions.

    Algorithm:
        $$\\tilde\\rho_U = \\mathrm{median}_r\\ \\mathrm{Spearman}(N_{r,t},\\ U^{max}_{r,t}),\\quad
        \\tilde\\rho_T\\ \\text{likewise}$$
        ASCII: median over regions of spearman(cases[r, t], daily max UTCI[r, t]); same for T

        $N_{r,t}$ cases of region $r$ on day $t$ (count), $U^{max}$ and $T^{max}$ the region's
        mean over its stations of the daily maximum UTCI and air temperature (degC). A region
        with no station, or with a constant series, has no $\\rho$ (None) and is left out of
        both medians; the medians use the regions where both $\\rho_U$ and $\\rho_T$ exist.

    Returns:
        ``median_u``, ``median_t`` (None if no region), ``n`` (regions used) and ``per_sido``
        ``{region: {"rho_u", "rho_t", "n_days"}}`` for all regions of ``counts``.
    """
    per: dict[str, dict[str, Any]] = {}
    for s in counts.columns:
        c = counts[s].to_numpy(dtype=float)
        u = utci_max[s].reindex(counts.index).to_numpy() if s in utci_max.columns else None
        t = tmax[s].reindex(counts.index).to_numpy() if s in tmax.columns else None
        if u is None or t is None:
            per[str(s)] = {"rho_u": None, "rho_t": None, "n_days": 0}
            continue
        both = np.isfinite(u) & np.isfinite(t)  # U and T compared on the same days
        per[str(s)] = {
            "rho_u": spearman(c[both], u[both]),
            "rho_t": spearman(c[both], t[both]),
            "n_days": int(both.sum()),
        }
    both = [v for v in per.values() if v["rho_u"] is not None and v["rho_t"] is not None]
    return {
        "median_u": float(np.median([v["rho_u"] for v in both])) if both else None,
        "median_t": float(np.median([v["rho_t"] for v in both])) if both else None,
        "n": len(both),
        "per_sido": per,
    }


def _mean_valid(by_sido: Mapping[str, pd.DataFrame], col: str) -> pd.Series:
    """Mean of one annual column over the valid years of each region (NaN if none)."""
    return pd.Series(
        {
            s: float(df[col].mean(skipna=True)) if df[col].notna().any() else np.nan
            for s, df in by_sido.items()
        },
        dtype=float,
    )


def v3_spatial(
    counts_by_year: pd.DataFrame, pop: pd.DataFrame, annual_by_sido: Mapping[str, pd.DataFrame]
) -> dict[str, Any]:
    """V3: spatial rank correlation of the case rate with D38, T95 and heat days.

    Algorithm:
        $$\\rho_U = \\mathrm{Spearman}_r\\big(r_r,\\ \\overline{D_{38}}_r\\big)$$
        ASCII: spearman over regions of (cases per 100k residents per year) with (mean over
        valid years of D38); the same with T95 and heat days

        $r_r$ from :func:`rate_per_100k` (cases per 100 000 residents per year),
        $\\overline{D_{38}}_r$ the mean of the valid years of the region's mean station D38
        (days per year). Only regions with all of rate, D38, T95 and heat days enter, so the
        three correlations use the same regions.

    Args:
        counts_by_year: Rows = years, columns = regions.
        pop: Rows = years, columns = regions.
        annual_by_sido: ``{region: annual_from_daily frame}``.

    Returns:
        ``rho_u``, ``rho_t95``, ``rho_h`` (None if not computable), ``n`` (regions) and
        ``per_sido`` ``{region: {"rate", "d38", "t95", "heat_days"}}``.
    """
    rate = rate_per_100k(counts_by_year, pop)
    d38 = _mean_valid(annual_by_sido, "d38")
    t95 = _mean_valid(annual_by_sido, "t95")
    hd = _mean_valid(annual_by_sido, "heat_days")
    tab = pd.DataFrame({"rate": rate, "d38": d38, "t95": t95, "heat_days": hd})
    per = {str(s): {k: _num(v) for k, v in row.items()} for s, row in tab.iterrows()}
    ok = tab.dropna()
    return {
        "rho_u": spearman(ok["rate"], ok["d38"]),
        "rho_t95": spearman(ok["rate"], ok["t95"]),
        "rho_h": spearman(ok["rate"], ok["heat_days"]),
        "n": len(ok),
        "per_sido": per,
    }


def v4_yearly(
    counts_by_year: pd.DataFrame, pop: pd.DataFrame, annual_by_sido: Mapping[str, pd.DataFrame]
) -> dict[str, Any]:
    """V4 (record only): yearly national cases against the population-weighted national D38.

    Algorithm:
        $$\\rho = \\mathrm{Spearman}_y\\big(N_y,\\ \\bar D_{38,y}\\big)$$
        ASCII: spearman over years of (national cases) with (population-weighted D38)

        $N_y$ cases of year $y$ over all regions (count), $\\bar D_{38,y}$ the population-weighted
        mean over regions of the D38 of year $y$ (days). Nine points make the test too weak to
        decide anything; it is recorded only.

    Returns:
        ``rho`` (None if not computable) and ``n`` (years with a finite D38).
    """
    if annual_by_sido:
        d38 = pd.DataFrame({s: df["d38"] for s, df in annual_by_sido.items()})
    else:
        d38 = pd.DataFrame(index=counts_by_year.index)
    ind = d38.copy()
    ind.index = pd.to_datetime([f"{int(y)}-07-01" for y in d38.index])
    nat = weighted_national(ind, pop)
    nat.index = d38.index
    cases = counts_by_year.sum(axis=1).astype(float).reindex(nat.index)
    return {
        "rho": spearman(cases.to_numpy(), nat.to_numpy()),
        "n": int(np.isfinite(nat.to_numpy()).sum()),
    }


def _num(x: Any) -> float | None:
    f = float(x)
    return f if np.isfinite(f) else None


# --------------------------------------------------------------------------- #
# Decision                                                                     #
# --------------------------------------------------------------------------- #
def decide(
    v1: Mapping[str, Any], v2: Mapping[str, Any], v3: Mapping[str, Any], rules: Mapping[str, Any]
) -> dict[str, Any]:
    """Pre-registered switch rule (design §4.2 table): UTCI becomes the headline if V1-V3 pass.

    Algorithm:
        $$V1: \\rho_U \\ge 0.5\\ \\wedge\\ \\rho_U \\ge \\rho_T - 0.02;\\quad
        V2: \\tilde\\rho_U \\ge \\tilde\\rho_T - 0.02;\\quad
        V3: \\rho_U \\ge \\max(\\rho_{T95}, \\rho_H) - 0.10$$
        ASCII: V1 rho_u>=v1_min_rho and rho_u>=rho_t-v1_margin; V2 median_u>=median_t-v2_margin;
        V3 rho_u>=max(rho_t95, rho_h)-v3_margin

        All $\\rho$ are Spearman rank correlations (-); the margins and the floor are the
        pre-registered design values in ``heat_utci.validation``. A missing (None or NaN)
        number fails its test, never passes. A value exactly on a criterion passes (1e-9 slack
        for floating point).

    Args:
        v1: ``rho_u``, ``rho_t`` from :func:`v1_daily_national`.
        v2: ``median_u``, ``median_t`` from :func:`v2_daily_by_sido`.
        v3: ``rho_u``, ``rho_t95``, ``rho_h`` from :func:`v3_spatial`.
        rules: ``v1_min_rho``, ``v1_margin``, ``v2_margin``, ``v3_margin``.

    Returns:
        ``switch`` (all three pass), ``items`` ``{V1, V2, V3: {"pass": bool, numbers...}}`` and
        ``reasons`` (one line per failed test).
    """

    def fin(x: Any) -> bool:
        return x is not None and bool(np.isfinite(float(x)))

    items: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    u1, t1 = v1.get("rho_u"), v1.get("rho_t")
    floor, m1 = float(rules["v1_min_rho"]), float(rules["v1_margin"])
    ok1 = fin(u1) and fin(t1) and u1 >= floor - _EPS and u1 >= t1 - m1 - _EPS
    items["V1"] = {"pass": bool(ok1), "rho_u": u1, "rho_t": t1, "min_rho": floor, "margin": m1}

    u2, t2 = v2.get("median_u"), v2.get("median_t")
    m2 = float(rules["v2_margin"])
    ok2 = fin(u2) and fin(t2) and u2 >= t2 - m2 - _EPS
    items["V2"] = {"pass": bool(ok2), "median_u": u2, "median_t": t2, "margin": m2}

    u3, t3, h3 = v3.get("rho_u"), v3.get("rho_t95"), v3.get("rho_h")
    m3 = float(rules["v3_margin"])
    ok3 = fin(u3) and fin(t3) and fin(h3) and u3 >= max(t3, h3) - m3 - _EPS
    items["V3"] = {
        "pass": bool(ok3),
        "rho_u": u3,
        "rho_t95": t3,
        "rho_h": h3,
        "margin": m3,
    }

    for name, it in items.items():
        if not it["pass"]:
            nums = ", ".join(f"{k}={v}" for k, v in it.items() if k != "pass")
            reasons.append(f"{name} failed or missing: {nums}")
    return {"switch": all(it["pass"] for it in items.values()), "items": items, "reasons": reasons}
