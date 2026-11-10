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
        negative numbers with stages numbered 1..n, or a farmland class code is missing.
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


def spei(
    d: Any,
    months: Any,
    scale: int,
    ref_mask: Any,
    clip: float | None = None,
    reflect: bool | None = None,
    diagnostics: dict[str, int] | None = None,
) -> np.ndarray:
    """Standardised Precipitation-Evapotranspiration Index (Vicente-Serrano et al. 2010).

    Reference: S. M. Vicente-Serrano, S. Begueria, J. I. Lopez-Moreno, 2010, "A multiscalar
    drought index sensitive to global warming: the Standardized Precipitation
    Evapotranspiration Index", J. Climate 23, 1696-1718, section 2b; probability-weighted
    moment forms of the log-logistic after Singh, Guo & Yu (1993), Stoch. Hydrol. Hydraul. 7.

    Algorithm:
        The climatic water balance $D$ (mm/month, precipitation minus reference
        evapotranspiration) is accumulated over $k$ months, $D^{(k)}_t = \\sum_{j<k} D_{t-j}$.
        For each calendar month separately the reference-period values, sorted ascending
        $x_{(1)} \\le \\dots \\le x_{(N)}$, give the unbiased probability-weighted moments

        $$w_s = \\frac{1}{N}\\sum_{i=1}^{N}\\frac{\\binom{N-i}{s}}{\\binom{N-1}{s}}\\,x_{(i)},
        \\quad s = 0, 1, 2$$

        and the three-parameter log-logistic parameters

        $$\\beta = \\frac{2 w_1 - w_0}{6 w_1 - w_0 - 6 w_2},\\quad
        \\alpha = \\frac{(w_0 - 2 w_1)\\,\\beta}{\\Gamma(1+1/\\beta)\\,\\Gamma(1-1/\\beta)},\\quad
        \\gamma = w_0 - \\alpha\\,\\Gamma(1+1/\\beta)\\,\\Gamma(1-1/\\beta)$$

        with cumulative distribution

        $$F(x) = \\left[1 + \\left(\\frac{\\alpha}{x - \\gamma}\\right)^{\\beta}\\right]^{-1}$$

        and $\\mathrm{SPEI} = \\Phi^{-1}(F(D^{(k)}))$ (standard normal quantile), clipped to
        $[-c, c]$. Symbols: $D$ mm/month; $x_{(i)}$ mm (accumulated); $N$ reference years;
        $\\alpha$ scale (mm), $\\beta$ shape (-), $\\gamma$ origin (mm); SPEI dimensionless.

        ASCII: w_s = (1/N) sum_i [C(N-i,s)/C(N-1,s)] x_(i); beta = (2 w1 - w0)/(6 w1 - w0 - 6 w2);
        alpha = (w0 - 2 w1) beta / (G(1+1/beta) G(1-1/beta)); gamma = w0 - alpha G(1+1/beta)
        G(1-1/beta); F(x) = 1/(1 + (alpha/(x-gamma))^beta); SPEI = PhiInv(F), clip +-c.

        $x \\le \\gamma$ gives $F = 0$, which clips to $-c$.

        The log-logistic is right-skewed; a reference sample with negative skew gives
        $\\beta < 0$ and the fit is undefined (also $\\beta \\le 1$, fewer than 4 reference
        values, non-finite moments). Extension beyond the paper, switched by ``reflect``:
        such a series is fitted to $-x$ instead and $\\mathrm{SPEI} = -\\Phi^{-1}(F_{-x}(-D))$,
        i.e. a mirrored log-logistic (left tail heavy). Without it, or when the mirrored fit
        also fails, the value is NaN. ASCII: SPEI = -PhiInv(F_fit(-D)) with the fit on -x.

    Args:
        d: Monthly water balance, shape ``(T,)`` or ``(T, N)``, time first, consecutive
            months.
        months: ``(T,)`` calendar month 1..12 of each step.
        scale: Accumulation length $k$ in months.
        ref_mask: ``(T,)`` bool, True on the reference-period steps used for the fit.
        clip: Clip magnitude $c$; defaults to ``spei.clip`` of ``drought_agri.json``.
        reflect: Use the mirrored fit where the direct fit is undefined; defaults to
            ``spei.reflect_when_fit_undefined`` of ``drought_agri.json``.
        diagnostics: Optional dict filled with ``series_months`` (series x calendar months with
            a usable reference sample), ``direct``, ``reflected`` and ``undefined`` counts.

    Returns:
        SPEI with the shape of ``d``; NaN where the accumulation is NaN or the fit failed.
    """
    from scipy.stats import norm

    if clip is None or reflect is None:
        sp = load_config()["spei"]
        clip = float(sp["clip"]) if clip is None else clip
        reflect = bool(sp.get("reflect_when_fit_undefined", False)) if reflect is None else reflect
    arr = np.asarray(d, dtype=float)
    one_d = arr.ndim == 1
    flat = arr.reshape(arr.shape[0], -1)
    mon = np.asarray(months, dtype=int)
    ref = np.asarray(ref_mask, dtype=bool)
    if mon.shape != (flat.shape[0],) or ref.shape != mon.shape:
        raise ValueError("months and ref_mask must have one entry per time step")
    acc = accumulate(flat, scale)
    out = np.full(acc.shape, np.nan)
    diag = {"series_months": 0, "direct": 0, "reflected": 0, "undefined": 0}

    def _z(x: Any, alpha: Any, beta: Any, gamma: Any) -> Any:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            ratio = np.where(x > gamma, alpha / (x - gamma), np.inf)
            return norm.ppf(1.0 / (1.0 + ratio**beta))

    for m in range(1, 13):
        sel = mon == m
        if not sel.any():
            continue
        ref_vals = acc[sel & ref]
        n = np.isfinite(ref_vals).sum(axis=0)
        alpha, beta, gamma = _fit_loglogistic_pwm(np.sort(ref_vals, axis=0), n)
        x = acc[sel]
        z = _z(x, alpha, beta, gamma)
        direct = np.isfinite(beta)
        if reflect:
            ra, rb, rg = _fit_loglogistic_pwm(np.sort(-ref_vals, axis=0), n)
            zr = -_z(-x, ra, rb, rg)
            use_ref = ~direct & np.isfinite(rb)
            z = np.where(use_ref, zr, z)
        else:
            use_ref = np.zeros_like(direct)
        usable = direct | use_ref
        z = np.clip(z, -clip, clip)
        out[sel] = np.where(np.isfinite(x) & usable[None, :], z, np.nan)
        has_ref = n > 0
        diag["series_months"] += int(has_ref.sum())
        diag["direct"] += int((direct & has_ref).sum())
        diag["reflected"] += int((use_ref & has_ref).sum())
        diag["undefined"] += int((~usable & has_ref).sum())
    log.debug("spei shape=%s scale=%d %s", out.shape, scale, diag)
    if diagnostics is not None:
        diagnostics.update(diag)
    return out[:, 0] if one_d else out.reshape(arr.shape)


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
