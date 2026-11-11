"""Class shares, binned statistics and the literature check of the Yeongdeungpo heat twin.

Pure numpy helpers behind ``scripts/case_yeongdeungpo_figures.py``. The published values come
from one open paper (Kim et al. 2022, Remote Sens. 14(2), 260, doi:10.3390/rs14020260); they
are fixed reference values, never tuned to. The comparison is diagnostic only.

Algorithm:
    Class shares for edges $e_0 < \\dots < e_{K-1}$ and a counted set $C$ of finite values:
    $$s_k = \\frac{\\#\\{i\\in C: e_{k-1} < v_i \\le e_k\\}}{\\#C}$$
    with $e_{-1}=-\\infty$ and $e_K=\\infty$.
    ASCII: s_k = count(e_{k-1} < v <= e_k) / count(C).

    SVF contrast of mean radiant temperature (degC), with $M$ the mask of open cells:
    $$\\Delta T_{mr} = \\mathrm{median}\\{T_{mr,i}: i\\in M,\\ \\psi_i\\ge\\psi_h\\}
      - \\mathrm{median}\\{T_{mr,i}: i\\in M,\\ \\psi_i<\\psi_l\\}$$
    ASCII: dT = median(Tmr[M & svf >= svf_high]) - median(Tmr[M & svf < svf_low]).
    $\\psi$ sky view factor (-), $\\psi_h=0.6$, $\\psi_l=0.4$.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


def class_shares(
    values: Any, edges: Sequence[float], mask: Any | None = None
) -> list[dict[str, float | None]]:
    """Share of finite values in each class ``(-inf, e0], (e0, e1], ..., (e_last, inf)``.

    Args:
        values: Array of values (any shape); non-finite values are not counted.
        edges: Increasing class edges.
        mask: Optional boolean array of the same shape; only ``True`` cells are counted.

    Returns:
        One dict per class with ``lo`` (None for the first), ``hi`` (None for the last) and
        ``share`` (sums to 1; all 0 when nothing is counted).
    """
    v = np.asarray(values, dtype=float)
    ok = np.isfinite(v)
    if mask is not None:
        ok &= np.asarray(mask, dtype=bool)
    e = [float(x) for x in edges]
    counted = v[ok]
    # side="left": a value equal to an edge falls into the lower class (right-closed classes)
    idx = np.searchsorted(np.asarray(e), counted, side="left")
    counts = np.bincount(idx, minlength=len(e) + 1).astype(float)
    total = float(counts.sum())
    shares = counts / total if total > 0 else np.zeros(len(e) + 1)
    los: list[float | None] = [None, *e]
    his: list[float | None] = [*e, None]
    return [
        {"lo": lo, "hi": hi, "share": float(s)} for lo, hi, s in zip(los, his, shares, strict=True)
    ]


def binned_stats(
    x: Any, y: Any, edges: Sequence[float], min_n: int = 1
) -> dict[str, list[float] | list[int]]:
    """Median and inter-quartile range of ``y`` in bins of ``x`` (bins ``[e_i, e_i+1)``).

    The last bin is closed. NaN ``y`` (or ``x``) are ignored; statistics are NaN when a bin
    holds fewer than ``min_n`` values.

    Args:
        x: Values to bin on (e.g. sky view factor).
        y: Values summarised per bin (e.g. mean radiant temperature, degC).
        edges: Increasing bin edges.
        min_n: Minimum count for a bin to report statistics.

    Returns:
        Dict of lists ``centre``, ``median``, ``p25``, ``p75`` and ``n`` (one per bin).
    """
    xs = np.asarray(x, dtype=float).ravel()
    ys = np.asarray(y, dtype=float).ravel()
    e = np.asarray(edges, dtype=float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[ok], ys[ok]
    out: dict[str, Any] = {"centre": [], "median": [], "p25": [], "p75": [], "n": []}
    nb = len(e) - 1
    for i in range(nb):
        sel = (xs >= e[i]) & ((xs < e[i + 1]) if i < nb - 1 else (xs <= e[i + 1]))
        n = int(sel.sum())
        out["centre"].append(float(0.5 * (e[i] + e[i + 1])))
        out["n"].append(n)
        if n >= max(1, min_n):
            q = np.percentile(ys[sel], [25, 50, 75])
            out["p25"].append(float(q[0]))
            out["median"].append(float(q[1]))
            out["p75"].append(float(q[2]))
        else:
            for k in ("p25", "median", "p75"):
                out[k].append(float("nan"))
    return out  # type: ignore[no-any-return]


def _median(a: np.ndarray) -> float:
    """Median of the finite values; NaN when there are none."""
    a = a[np.isfinite(a)]
    return float(np.median(a)) if a.size else float("nan")


def svf_contrast(mrt: Any, svf: Any, mask: Any, svf_high: float, svf_low: float) -> float:
    """Median MRT of open sky (``svf >= svf_high``) minus that of enclosed cells (``< svf_low``).

    Args:
        mrt: Mean radiant temperature (degC).
        svf: Sky view factor (-), same shape.
        mask: Boolean mask of the cells counted.
        svf_high: Lower SVF bound of the open class.
        svf_low: Upper SVF bound (exclusive) of the enclosed class.

    Returns:
        Difference in degC; NaN when either side is empty.
    """
    m, s, k = (np.asarray(a) for a in (mrt, svf, mask))
    k = k.astype(bool)
    hi = _median(m[k & (s >= svf_high)].astype(float))
    lo = _median(m[k & (s < svf_low)].astype(float))
    return float(hi - lo)


def literature_rows(
    days: Mapping[str, Mapping[str, Any]], svf: Any, open_site: Any, lit: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The two claims of Kim et al. (2022) that a hotter day cannot fake.

    Row 1: on every day the open cells (SVF >= 0.6) are hotter in MRT than the enclosed ones
    (SVF < 0.4). Row 2: sunlit open cells (SVF >= 0.6) have a median MRT above the paper's
    highest risk-class edge (59.4 degC). Nothing is tuned to either.

    Args:
        days: ``date -> {"mrt": array, "sunlit": bool array}``.
        svf: Sky view factor raster.
        open_site: Boolean mask of open cells inside the site.
        lit: Spec ``literature`` block (``mrt_class_edges_c``, ``svf_high``, ``svf_low``).

    Returns:
        Exactly two row dicts (``mrt_svf_contrast``, ``mrt_sunlit_open``).
    """
    s = np.asarray(svf, dtype=float)
    site = np.asarray(open_site, dtype=bool)
    hi, lo = float(lit["svf_high"]), float(lit["svf_low"])
    edge = float(lit["mrt_class_edges_c"][-1])
    contrast: dict[str, float] = {}
    sunny: dict[str, float] = {}
    for date, d in days.items():
        mrt = np.asarray(d["mrt"], dtype=float)
        lit_mask = np.asarray(d["sunlit"], dtype=bool)
        contrast[date] = svf_contrast(mrt, s, site, hi, lo)
        sunny[date] = _median(mrt[site & lit_mask & (s >= hi)])

    def _min(v: Mapping[str, float]) -> float:
        a = np.array(list(v.values()), dtype=float)
        if a.size == 0 or not np.isfinite(a).all():
            return float("nan")
        return float(a.min())

    m1, m2 = _min(contrast), _min(sunny)
    return [
        {
            "id": "mrt_svf_contrast",
            "kind": "contrast_c",
            "label_ko": (
                f"MRT 차이: 하늘이 많이 보이는 칸(SVF≥{hi:g}) − 적게 보이는 칸(SVF<{lo:g})"
            ),
            "unit": "°C",
            "observed": "> 0",
            "model": m1,
            "per_day": contrast,
            "ok": bool(np.isfinite(m1) and m1 > 0),
        },
        {
            "id": "mrt_sunlit_open",
            "kind": "above_c",
            "label_ko": (
                f"햇빛 드는 개방 칸(SVF≥{hi:g})의 MRT 중앙값 > {edge:g} °C "
                "(논문 최고 위험 등급 경계)"
            ),
            "unit": "°C",
            "observed": edge,
            "model": m2,
            "per_day": sunny,
            "ok": bool(np.isfinite(m2) and m2 > edge),
            "note_ko": "논문(6월 오전)보다 더운 날이라 일치 쪽으로 유리한 비교입니다",
        },
    ]
