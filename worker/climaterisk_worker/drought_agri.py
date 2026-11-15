"""Agricultural drought v0: SPEI, stages and farmland per hazard piece.

Method note: ``docs/DROUGHT_AGRI_KO.md``. Configuration (scale, reference period, season,
stage thresholds, farmland classes): ``assets/libraries/drought_agri.json``.

Standard-library logging only (the worker environment has no pydantic). Heavy imports
(scipy, geopandas) are deferred into the functions.
"""

from __future__ import annotations

import json
import logging
import os
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

_CONFIG = "drought_agri.json"


def config_path() -> Path:
    """Location of ``drought_agri.json`` (``CLIMATERISK_LIBRARY_DIR`` overrides the default)."""
    repo = Path(__file__).resolve().parents[2]
    override = os.environ.get("CLIMATERISK_LIBRARY_DIR")
    base = Path(override) if override else repo / "assets" / "libraries"
    if not base.is_absolute():
        base = repo / base
    return base / _CONFIG


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read and validate the drought configuration.

    Args:
        path: Explicit file; defaults to :func:`config_path`.

    Returns:
        Parsed JSON. Raises ``ValueError`` when the SPEI scale is not a positive integer, the
        reference period or output years are not ``[first, last]`` with first <= last, the
        season months are not in 1..12, the stage thresholds are not strictly decreasing
        negative numbers with stages numbered 1..n, a farmland class code is missing, or
        ``future.reference_years`` differs from the present reference period.
    """
    cfg = json.loads((path or config_path()).read_text(encoding="utf-8"))
    spei_cfg = cfg.get("spei", {})
    scale = spei_cfg.get("scale_months")
    if not isinstance(scale, int) or scale < 1:
        raise ValueError("drought_agri.json: spei.scale_months must be a positive integer")
    for key, span in (("spei.reference_years", spei_cfg.get("reference_years")),
                      ("output_years", cfg.get("output_years"))):  # fmt: skip
        if not (isinstance(span, list) and len(span) == 2 and span[0] <= span[1]):
            raise ValueError(f"drought_agri.json: {key} must be [first, last]")
    months = spei_cfg.get("season_end_months")
    if not months or any(not (isinstance(m, int) and 1 <= m <= 12) for m in months):
        raise ValueError("drought_agri.json: spei.season_end_months must be months 1..12")
    if not float(spei_cfg.get("clip", 0)) > 0:
        raise ValueError("drought_agri.json: spei.clip must be positive")
    stages = cfg.get("stages")
    if not stages or [s["stage"] for s in stages] != list(range(1, len(stages) + 1)):
        raise ValueError("drought_agri.json: stages must be numbered 1..n in order")
    thr = [float(s["spei_le"]) for s in stages]
    if any(t >= 0 for t in thr) or any(b >= a for a, b in pairwise(thr)):
        raise ValueError("drought_agri.json: stage thresholds must be negative and decreasing")
    classes = cfg.get("farmland_classes", {})
    if set(classes.values()) != {"rice", "upland", "orchard", "greenhouse"}:
        raise ValueError("drought_agri.json: farmland_classes must map to the four classes")
    fut = cfg.get("future")
    if fut is not None:
        if fut.get("reference_years") != spei_cfg.get("reference_years"):
            raise ValueError("drought_agri.json: future.reference_years must equal the present's")
        if not fut.get("scenarios") or not fut.get("source"):
            raise ValueError("drought_agri.json: future needs scenarios and source")
    return cfg


def accumulate(x: Any, scale: int) -> np.ndarray:
    """Rolling sum over ``scale`` consecutive steps along axis 0 (time first).

    Algorithm:
        $$A_t = \\sum_{j=0}^{k-1} x_{t-j}$$

        ASCII: A[t] = sum_{j=0..k-1} x[t-j]; the first k-1 steps have no full window and are NaN.
        A NaN anywhere in a window makes that window NaN.

    Args:
        x: Array with time on axis 0 (any trailing shape).
        scale: Window length $k$ in steps (>= 1).

    Returns:
        Float array of the same shape as ``x``.
    """
    arr = np.asarray(x, dtype=float)
    if scale < 1:
        raise ValueError("scale must be >= 1")
    out = np.full(arr.shape, np.nan)
    n = arr.shape[0]
    if n < scale:
        return out
    acc = np.zeros((n - scale + 1, *arr.shape[1:]))
    for j in range(scale):
        acc += arr[scale - 1 - j : n - j]
    out[scale - 1 :] = acc
    return out


def _fit_loglogistic_pwm(sorted_x: np.ndarray, n: np.ndarray) -> tuple[Any, Any, Any]:
    """Three-parameter log-logistic fit by unbiased PWMs for many series at once.

    Args:
        sorted_x: ``(n_max, N)`` ascending along axis 0, NaN last.
        n: ``(N,)`` number of finite values per series.

    Returns:
        ``(alpha, beta, gamma)`` each ``(N,)``; NaN where the fit is undefined.
    """
    from scipy.special import gamma as gamma_fn

    rows = np.arange(1, sorted_x.shape[0] + 1, dtype=float)[:, None]  # rank i = 1..n_max
    nn = n.astype(float)[None, :]
    valid = rows <= nn
    x = np.where(valid, sorted_x, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        c0 = np.where(valid, 1.0 / nn, 0.0)
        c1 = np.where(valid, (nn - rows) / (nn - 1.0) / nn, 0.0)
        c2 = np.where(valid, (nn - rows) * (nn - rows - 1.0) / ((nn - 1.0) * (nn - 2.0)) / nn, 0.0)
        w0 = (c0 * x).sum(axis=0)
        w1 = (c1 * x).sum(axis=0)
        w2 = (c2 * x).sum(axis=0)
        beta = (2.0 * w1 - w0) / (6.0 * w1 - w0 - 6.0 * w2)
        g = gamma_fn(1.0 + 1.0 / beta) * gamma_fn(1.0 - 1.0 / beta)
        alpha = (w0 - 2.0 * w1) * beta / g
        gam = w0 - alpha * g
    ok = (n >= 4) & np.isfinite(beta) & (beta > 1.0) & np.isfinite(alpha) & (alpha > 0)
    return (
        np.where(ok, alpha, np.nan),
        np.where(ok, beta, np.nan),
        np.where(ok, gam, np.nan),
    )


def _fit_gev_lmom(sorted_x: np.ndarray, n: np.ndarray) -> tuple[Any, Any, Any]:
    """GEV fit by L-moments (Hosking 1990; Hosking & Wallis 1997) for many series at once.

    Args:
        sorted_x: ``(n_max, N)`` ascending along axis 0, NaN last.
        n: ``(N,)`` number of finite values per series.

    Returns:
        ``(xi, alpha, k)`` each ``(N,)`` (Hosking sign: ``k > 0`` bounded above, ``k < 0``
        bounded below, ``k = 0`` Gumbel); NaN where the fit is undefined (fewer than 4
        values, non-positive L-scale, non-finite moments).
    """
    from scipy.special import gamma as gamma_fn

    rows = np.arange(1, sorted_x.shape[0] + 1, dtype=float)[:, None]
    nn = n.astype(float)[None, :]
    valid = rows <= nn
    x = np.where(valid, sorted_x, 0.0)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        # unbiased PWMs b_r = (1/N) sum_i [C(i-1, r) / C(N-1, r)] x_(i)
        b0 = np.where(valid, 1.0 / nn, 0.0)
        b1 = np.where(valid, (rows - 1.0) / (nn - 1.0) / nn, 0.0)
        b2 = np.where(valid, (rows - 1.0) * (rows - 2.0) / ((nn - 1.0) * (nn - 2.0)) / nn, 0.0)
        p0 = (b0 * x).sum(axis=0)
        p1 = (b1 * x).sum(axis=0)
        p2 = (b2 * x).sum(axis=0)
        l1 = p0
        l2 = 2.0 * p1 - p0
        l3 = 6.0 * p2 - 6.0 * p1 + p0
        t3 = l3 / l2
        c = 2.0 / (3.0 + t3) - np.log(2.0) / np.log(3.0)
        k = 7.8590 * c + 2.9554 * c * c
        small = np.abs(k) < 1e-6
        ks = np.where(small, 1.0, k)
        g = gamma_fn(1.0 + ks)
        alpha = np.where(small, l2 / np.log(2.0), l2 * ks / ((1.0 - 2.0 ** (-ks)) * g))
        xi = np.where(small, l1 - 0.5772156649015329 * alpha, l1 - alpha * (1.0 - g) / ks)
        k = np.where(small, 0.0, k)
    ok = (n >= 4) & np.isfinite(alpha) & (alpha > 0) & np.isfinite(xi) & np.isfinite(k)
    return np.where(ok, xi, np.nan), np.where(ok, alpha, np.nan), np.where(ok, k, np.nan)


def _gev_cdf(x: Any, xi: Any, alpha: Any, k: Any) -> Any:
    """GEV CDF (Hosking sign); 0 below a lower bound, 1 above an upper bound."""
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        z = (x - xi) / alpha
        arg = 1.0 - k * z
        safe_k = np.where(k == 0, 1.0, k)
        y = np.where(k == 0, z, -np.log(np.where(arg > 0, arg, np.nan)) / safe_k)
        f = np.exp(-np.exp(-y))
        outside = (k != 0) & ~(arg > 0)
        bound = np.where(k > 0, 1.0, 0.0)  # k > 0: above the upper bound; k < 0: below lower
        return np.where(outside, bound, f)


def spei(
    d: Any,
    months: Any,
    scale: int,
    ref_mask: Any,
    clip: float | None = None,
    distribution: str | None = None,
    diagnostics: dict[str, int] | None = None,
) -> np.ndarray:
    """Standardised Precipitation-Evapotranspiration Index (Vicente-Serrano et al. 2010).

    References: S. M. Vicente-Serrano, S. Begueria, J. I. Lopez-Moreno, 2010, J. Climate 23,
    1696-1718 (index definition, log-logistic); J. H. Stagge, L. M. Tallaksen, L. Gudmundsson,
    A. F. Van Loon, K. Stahl, 2015, "Candidate distributions for climatological drought
    indices (SPI and SPEI)", Int. J. Climatol. 35, 4027-4040 (recommend GEV for SPEI);
    J. R. M. Hosking & J. R. Wallis, 1997, Regional Frequency Analysis (GEV by L-moments).

    Algorithm:
        The climatic water balance $D$ (mm/month, precipitation minus reference
        evapotranspiration) is accumulated over $k$ months, $D^{(k)}_t = \\sum_{j<k} D_{t-j}$.
        For each calendar month separately the reference-period values give the sample
        L-moments $\\lambda_1, \\lambda_2$ and L-skewness $\\tau_3 = \\lambda_3/\\lambda_2$
        (from unbiased PWMs). Default ``distribution="gev"``:

        $$c = \\frac{2}{3+\\tau_3} - \\frac{\\ln 2}{\\ln 3},\\quad \\kappa = 7.8590c + 2.9554c^2,
        \\quad \\alpha = \\frac{\\lambda_2 \\kappa}{(1-2^{-\\kappa})\\Gamma(1+\\kappa)},\\quad
        \\xi = \\lambda_1 - \\frac{\\alpha\\,(1-\\Gamma(1+\\kappa))}{\\kappa}$$

        $$F(x) = \\exp\\left[-\\left(1 - \\kappa\\frac{x-\\xi}{\\alpha}\\right)^{1/\\kappa}
        \\right]$$

        ($\\kappa = 0$: Gumbel, $\\alpha = \\lambda_2/\\ln 2$,
        $\\xi = \\lambda_1 - 0.5772\\alpha$). The shape $\\kappa$ takes either
        sign, so a sample with a long DRY tail (negative skew) is fitted by the same law as
        one with a long wet tail; the log-logistic is undefined there.
        $\\mathrm{SPEI} = \\Phi^{-1}(F(D^{(k)}))$, clipped to $[-c_{\\max}, c_{\\max}]$; values
        beyond a bound of the fitted law get $F = 0$ or $1$ and so the clip value.
        ``distribution="loglogistic"`` keeps the original three-parameter log-logistic (PWM
        fit) for comparison; it is NaN wherever its shape is undefined. Symbols: $D$ mm/month;
        $\\xi$ location (mm), $\\alpha$ scale (mm), $\\kappa$ shape (-); SPEI dimensionless.

        ASCII: c = 2/(3+t3) - ln2/ln3; k = 7.8590 c + 2.9554 c^2;
        alpha = l2 k / ((1-2^-k) G(1+k)); xi = l1 - alpha (1 - G(1+k))/k;
        F = exp(-(1 - k (x-xi)/alpha)^(1/k)); SPEI = PhiInv(F), clipped.

    Args:
        d: Monthly water balance, shape ``(T,)`` or ``(T, N)``, time first, consecutive
            months.
        months: ``(T,)`` calendar month 1..12 of each step.
        scale: Accumulation length $k$ in months.
        ref_mask: ``(T,)`` bool, True on the reference-period steps used for the fit.
        clip: Clip magnitude; defaults to ``spei.clip`` of ``drought_agri.json``.
        distribution: ``"gev"`` or ``"loglogistic"``; defaults to ``spei.distribution``.
        diagnostics: Optional dict filled with ``series_months`` (series x calendar months
            with a usable reference sample), ``fitted``, ``undefined`` and
            ``beyond_bound`` (values that fell outside the fitted law's support).

    Returns:
        SPEI with the shape of ``d``; NaN where the accumulation is NaN or the fit failed.
    """
    return spei_against_reference(d, d, months, scale, ref_mask, clip, distribution, diagnostics)


def spei_against_reference(
    d_ref: Any,
    d_target: Any,
    months: Any,
    scale: int,
    ref_mask: Any,
    clip: float | None = None,
    distribution: str | None = None,
    diagnostics: dict[str, int] | None = None,
) -> np.ndarray:
    """SPEI of a target series standardised with the distribution fitted to a reference series.

    Used for scenario series (e.g. TerraClimate +2 C / +4 C): the fit comes from the PRESENT
    water balance, so the stage frequencies of the target are comparable with the present
    ones. :func:`spei` is the special case ``d_target = d_ref``; the fitting is shared.

    Algorithm:
        Per calendar month $m$, fit the chosen law $F_m$ (GEV by L-moments or log-logistic by
        PWMs, as in :func:`spei`) to the accumulated reference values
        $D^{(k)}_{\\mathrm{ref},t}$ with $t$ in the reference period; then

        $$\\mathrm{SPEI}_t = \\Phi^{-1}\\left(F_{m(t)}\\left(D^{(k)}_{\\mathrm{tgt},t}\\right)
        \\right),\\quad \\text{clipped to } [-c_{\\max}, c_{\\max}]$$

        $D$ in mm/month, accumulated over $k$ months; SPEI dimensionless. A target drier than
        the reference gives a lower SPEI because $F_m$ is monotonic increasing.

        ASCII: SPEI[t] = PhiInv(F_m(accum(D_tgt)[t])), F_m fitted on accum(D_ref) in the
        reference period; clipped.

    Args:
        d_ref: Reference-climate monthly water balance, ``(T,)`` or ``(T, N)``, time first.
        d_target: Target water balance, same shape and time axis as ``d_ref``.
        months: ``(T,)`` calendar month 1..12 of each step.
        scale: Accumulation length $k$ in months.
        ref_mask: ``(T,)`` bool, True on the reference-period steps used for the fit.
        clip: Clip magnitude; defaults to ``spei.clip`` of ``drought_agri.json``.
        distribution: ``"gev"`` or ``"loglogistic"``; defaults to ``spei.distribution``.
        diagnostics: Optional dict filled as in :func:`spei`.

    Returns:
        SPEI of ``d_target`` with its shape; NaN where the target accumulation is NaN or the
        reference fit failed.
    """
    from scipy.stats import norm

    sp = load_config()["spei"] if clip is None or distribution is None else {}
    clip = float(sp["clip"]) if clip is None else clip
    dist = str(sp.get("distribution", "gev")) if distribution is None else distribution
    if dist not in ("gev", "loglogistic"):
        raise ValueError(f"unknown SPEI distribution {dist!r}")
    arr_ref = np.asarray(d_ref, dtype=float)
    arr = np.asarray(d_target, dtype=float)
    if arr_ref.shape != arr.shape:
        raise ValueError("d_ref and d_target must have the same shape")
    one_d = arr.ndim == 1
    flat_ref = arr_ref.reshape(arr_ref.shape[0], -1)
    flat = arr.reshape(arr.shape[0], -1)
    mon = np.asarray(months, dtype=int)
    ref = np.asarray(ref_mask, dtype=bool)
    if mon.shape != (flat.shape[0],) or ref.shape != mon.shape:
        raise ValueError("months and ref_mask must have one entry per time step")
    acc_ref = accumulate(flat_ref, scale)
    acc = accumulate(flat, scale)
    out = np.full(acc.shape, np.nan)
    diag = {"series_months": 0, "fitted": 0, "undefined": 0, "beyond_bound": 0}

    for m in range(1, 13):
        sel = mon == m
        if not sel.any():
            continue
        ref_vals = acc_ref[sel & ref]
        n = np.isfinite(ref_vals).sum(axis=0)
        x = acc[sel]
        srt = np.sort(ref_vals, axis=0)
        if dist == "gev":
            xi, alpha, k = _fit_gev_lmom(srt, n)
            f = _gev_cdf(x, xi, alpha, k)
            usable = np.isfinite(k)
        else:
            a, b, g = _fit_loglogistic_pwm(srt, n)
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                ratio = np.where(x > g, a / (x - g), np.inf)
                f = 1.0 / (1.0 + ratio**b)
            usable = np.isfinite(b)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.clip(norm.ppf(f), -clip, clip)
        good = np.isfinite(x) & usable[None, :]
        out[sel] = np.where(good, z, np.nan)
        has_ref = n > 0
        diag["series_months"] += int(has_ref.sum())
        diag["fitted"] += int((usable & has_ref).sum())
        diag["undefined"] += int((~usable & has_ref).sum())
        diag["beyond_bound"] += int((good & ((f <= 0.0) | (f >= 1.0))).sum())
    log.debug("spei shape=%s scale=%d dist=%s %s", out.shape, scale, dist, diag)
    if diagnostics is not None:
        diagnostics.update(diag)
    return out[:, 0] if one_d else out.reshape(arr.shape)


def compare_rankings(
    main_by_year: dict[int, float], check_by_year: dict[int, float], top_n: int
) -> dict[str, Any]:
    """Do the main and the check method rank the same years as the worst?

    Args:
        main_by_year: Year -> affected area (ha) from the main method.
        check_by_year: Year -> affected area (ha) from the check method.
        top_n: How many top years to compare.

    Returns:
        ``main_top`` / ``check_top`` (years, descending), ``same_set`` and ``same_order``.
    """

    def top(d: dict[int, float]) -> list[int]:
        return [int(y) for y, _ in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]]

    a, b = top(main_by_year), top(check_by_year)
    return {"main_top": a, "check_top": b, "same_set": set(a) == set(b), "same_order": a == b}


def stage_disagreement(stage_main: Any, stage_check: Any) -> dict[str, Any]:
    """Share of (year, cell) values where both methods give a stage and the stages differ.

    Args:
        stage_main: Stage array from :func:`stage` (``-1`` = no value).
        stage_check: Same shape, from the check method.

    Returns:
        ``n_both`` (both defined), ``n_check_undefined`` (main defined, check not) and
        ``share_differ`` (NaN when ``n_both`` is 0).
    """
    m = np.asarray(stage_main)
    c = np.asarray(stage_check)
    both = (m >= 0) & (c >= 0)
    n_both = int(both.sum())
    differ = int((both & (m != c)).sum())
    return {
        "n_both": n_both,
        "n_check_undefined": int(((m >= 0) & (c < 0)).sum()),
        "share_differ": differ / n_both if n_both else float("nan"),
    }


def stage(s: Any, thresholds: Any = None) -> np.ndarray:
    """Drought stage from SPEI.

    Algorithm:
        stage = number of thresholds $\\theta_j$ (default -1, -1.5, -2) with $s \\le \\theta_j$;
        0 when $s > -1$; NaN gives -1 (no data).

        ASCII: stage = count(s <= theta_j); NaN -> -1.

    Args:
        s: SPEI values (dimensionless).
        thresholds: Descending negative thresholds; default from ``stages`` in
            ``drought_agri.json``.

    Returns:
        Integer array: 0 none, 1 관심, 2 주의, 3 경계 이상, -1 no data.
    """
    if thresholds is None:
        thresholds = [float(x["spei_le"]) for x in load_config()["stages"]]
    arr = np.asarray(s, dtype=float)
    out = np.zeros(arr.shape, dtype=int)
    for t in thresholds:
        out += (arr <= t).astype(int)
    return np.where(np.isnan(arr), -1, out)


def assign_parcels(parcels: Any, pieces: Any, classes: dict[str, str] | None = None) -> Any:
    """Assign parcels to pieces by representative point; the shared core of the farmland sums.

    A parcel is assigned wholly to the one piece that contains its representative point
    (``point_on_surface``, always inside the polygon); a parcel crossing a cell or 시군구 edge
    is NOT split. A point on a shared edge goes to the first piece (lowest index).

    Args:
        parcels: GeoDataFrame (any CRS) with ``CLSF_CD`` and ``AREA`` (m^2).
        pieces: GeoDataFrame with ``id`` and polygons.
        classes: Mapping class code -> column; default from ``drought_agri.json``.

    Returns:
        ``(assigned, counts)``: ``assigned`` is a DataFrame ``piece_id, cls, area_m2`` of the
        kept parcels; ``counts`` has ``read``, ``excluded_by_code`` (code -> n),
        ``outside`` (farmland parcels in no piece), ``assigned`` (+ ``by_class``) and
        ``area_read_m2`` (AREA of all kept-class parcels read, by class).
    """
    import geopandas as gpd
    import pandas as pd

    class_map = classes or load_config()["farmland_classes"]
    codes = parcels["CLSF_CD"].astype(str).str.strip()
    keep = codes.isin(class_map)
    excluded = codes[~keep].value_counts().to_dict()
    counts: dict[str, Any] = {
        "read": len(parcels),
        "excluded_by_code": {str(k): int(v) for k, v in excluded.items()},
        "outside": 0,
        "assigned": 0,
        "by_class": dict.fromkeys(class_map.values(), 0),
        "area_read_m2": dict.fromkeys(class_map.values(), 0.0),
    }
    farm = parcels.loc[keep]
    for code, area in farm["AREA"].astype(float).groupby(codes[keep]).sum().items():
        counts["area_read_m2"][class_map[str(code)]] += float(area)
    if farm.empty:
        return pd.DataFrame({"piece_id": [], "cls": [], "area_m2": []}), counts
    points = gpd.GeoDataFrame(
        {"cls": codes[keep].map(class_map).to_numpy(), "area_m2": farm["AREA"].to_numpy(float)},
        geometry=farm.geometry.representative_point().to_numpy(),
        crs=parcels.crs,
    ).to_crs(pieces.crs)
    joined = gpd.sjoin(points, pieces[["id", "geometry"]], how="inner", predicate="intersects")
    joined = joined[~joined.index.duplicated(keep="first")]
    assigned = pd.DataFrame(
        {
            "piece_id": joined["id"].to_numpy(),
            "cls": joined["cls"].to_numpy(),
            "area_m2": joined["area_m2"].to_numpy(),
        }
    )
    counts["outside"] = int(len(points) - len(assigned))
    counts["assigned"] = len(assigned)
    for c, n in assigned["cls"].value_counts().items():
        counts["by_class"][str(c)] = int(n)
    return assigned, counts


def farm_area_by_piece(parcels: Any, pieces: Any) -> Any:
    """Farmland area per piece and class from 팜맵 parcels.

    Algorithm:
        $$A_{p,c} = \\sum_{j:\\ \\mathrm{rep}(j)\\in p,\\ \\mathrm{class}(j)=c} \\mathrm{AREA}_j$$

        where $\\mathrm{rep}(j)$ is the parcel's representative point (guaranteed inside the
        polygon), $\\mathrm{AREA}_j$ the 팜맵 ``AREA`` attribute (m^2, summed as given, never
        recomputed) and $c \\in$ {01 rice, 02 upland, 03 orchard, 04 greenhouse}.
        ASCII: A[p,c] = sum(AREA[j]) over parcels j with rep_point(j) in p and class(j) = c.

    Args:
        parcels: GeoDataFrame (any CRS) with ``CLSF_CD`` and ``AREA`` (m^2).
        pieces: GeoDataFrame with ``id`` and polygons.

    Returns:
        DataFrame indexed by piece ``id`` with columns ``rice, upland, orchard, greenhouse``
        (m^2). 비경지 (06) and unknown codes are excluded; parcels outside every piece are
        dropped; pieces without farmland are 0.
    """
    import pandas as pd

    cols = ["rice", "upland", "orchard", "greenhouse"]
    assigned, _ = assign_parcels(parcels, pieces)
    if len(assigned):
        table = assigned.pivot_table(
            index="piece_id", columns="cls", values="area_m2", aggfunc="sum", fill_value=0.0
        )
    else:
        table = pd.DataFrame(index=pd.Index([], name="piece_id"), columns=cols, dtype=float)
    out = table.reindex(index=pieces["id"].to_numpy(), columns=cols, fill_value=0.0).fillna(0.0)
    out.index.name = "id"
    return out.astype(float)
