"""Climate change for the Korea river-flood map: an ISIMIP change factor on event frequency.

The official 환경부 홍수위험지도 gives present-day depth classes only. A future scenario is
served by the same map hazard with every event's **frequency** multiplied by one national
factor read from the ISIMIP river-flood product of the CLIMADA Data API:

Algorithm:
    $$ f = \\frac{\\sum_{c} EAL_c^{\\text{future}}}{\\sum_{c} EAL_c^{\\text{present}}},
       \\qquad EAL_c = \\sum_e \\nu_e \\, \\mathrm{MDD}(I_{ec}) \\,
       \\mathrm{PAA}(I_{ec}) \\, F_{ec} $$
    ASCII: f = sum_c EAL_future[c] / sum_c EAL_present[c];
           EAL[c] = sum_e freq[e] * MDD(I[e,c]) * PAA(I[e,c]) * fraction[e,c].
    Symbols: ``c`` ISIMIP centroid (4.6 km grid cell); ``e`` event; ``nu_e`` event frequency
    (1/year); ``I_ec`` water depth (m); ``MDD`` mean damage degree and ``PAA`` percentage of
    assets affected, both (-) from the JRC Asia residential curve; ``F_ec`` flooded fraction
    of the cell (-). The exposure is one unit of value per centroid, so ``EAL_c`` is a
    damage ratio per year (1/year) and ``f`` is dimensionless. "present" is the same RCP's
    earliest window (2010-2030), the like-for-like rule of ``runner.baseline_groups``.

The depth is never changed. Main method: the national factor. Sub check (diagnostic only,
never alters the hazard): the same ratio per 시도, compared with the national one at the
assets.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: Region label for a point outside every 시도 polygon.
UNASSIGNED = ""


def unit_eal(
    intensity: Any,
    fraction: Any | None,
    frequency: np.ndarray,
    x: np.ndarray,
    mdd: np.ndarray,
    paa: np.ndarray,
) -> np.ndarray:
    """Expected annual damage ratio per centroid for a unit exposure.

    Algorithm:
        $$ EAL_c = \\sum_e \\nu_e \\, \\mathrm{MDD}(I_{ec}) \\, \\mathrm{PAA}(I_{ec}) \\, F_{ec} $$
        ASCII: eal[c] = sum_e freq[e] * interp(I[e,c], x, mdd) * interp(I[e,c], x, paa) * F[e,c].
        ``I`` depth (m), ``x`` curve depth axis (m), ``mdd`` / ``paa`` (-), ``freq`` (1/year).
        Depth outside the curve takes the end value (np.interp), as CLIMADA's impact function.

    Args:
        intensity: sparse events x centroids depth matrix (m).
        fraction: sparse events x centroids flooded fraction, or None for 1.
        frequency: event frequencies (1/year), length n_events.
        x: impact-function intensity axis (m).
        mdd: mean damage degree on ``x`` (-).
        paa: percentage of assets affected on ``x`` (-).

    Returns:
        Array of length n_centroids, damage ratio per year (1/year).
    """
    from scipy import sparse

    inten = sparse.csr_matrix(intensity, copy=True)
    inten.data = np.interp(inten.data, x, mdd) * np.interp(inten.data, x, paa)
    if fraction is not None:
        inten = inten.multiply(sparse.csr_matrix(fraction))
    eal = np.asarray(sparse.csr_matrix(inten).T.dot(np.asarray(frequency, dtype=float)))
    return eal.ravel()


def change_factor(
    present: np.ndarray, future: np.ndarray, regions: np.ndarray, min_wet: int
) -> dict[str, Any]:
    """National and per-region ratio of future to present unit EAL.

    Algorithm:
        $$ f_R = \\frac{\\sum_{c \\in R} EAL_c^{fut}}{\\sum_{c \\in R} EAL_c^{pres}} $$
        ASCII: f_R = sum_{c in R} eal_future / sum_{c in R} eal_present.
        A centroid is wet when it has loss in either window. A region ratio is None when it
        has fewer than ``min_wet`` wet centroids or no present loss; never inf.

    Args:
        present: unit EAL per centroid in the present window (1/year).
        future: unit EAL per centroid in the future window (1/year).
        regions: region label per centroid.
        min_wet: minimum wet centroids for a region ratio.

    Returns:
        ``{"national": float | None, "by_region": {region: float | None},
        "n_wet": {region: int}}``.
    """
    present = np.asarray(present, dtype=float)
    future = np.asarray(future, dtype=float)
    regions = np.asarray(regions)
    wet = (present > 0) | (future > 0)
    p_tot = float(present.sum())
    national = float(future.sum()) / p_tot if p_tot > 0 else None
    by_region: dict[str, float | None] = {}
    n_wet: dict[str, int] = {}
    for r in sorted({str(v) for v in regions}):
        sel = regions == r
        n_wet[r] = int((wet & sel).sum())
        p = float(present[sel].sum())
        by_region[r] = float(future[sel].sum()) / p if n_wet[r] >= min_wet and p > 0 else None
    return {"national": national, "by_region": by_region, "n_wet": n_wet}


def regional_check(
    national: float | None,
    by_region: dict[str, float | None],
    asset_regions: list[str],
    tol: float,
) -> dict[str, Any]:
    """Sub check: the asset-weighted provincial factor against the national one.

    Algorithm:
        $$ \\bar f = \\frac{1}{N} \\sum_{a} f_{R(a)}, \\qquad
           \\text{warn} \\iff \\left| \\bar f / f - 1 \\right| > \\tau $$
        ASCII: f_bar = mean over assets with a provincial factor of f[region(a)];
        warn when |f_bar / f - 1| > tol. ``f`` national factor (-), ``tau`` tolerance (-).
        Assets in a region without a factor are left out and counted. Diagnostic only.

    Args:
        national: the national factor, or None.
        by_region: provincial factors (None = no ratio).
        asset_regions: region of each asset.
        tol: relative tolerance (-).

    Returns:
        Record with ``weighted_factor``, ``national_factor``, ``n_assets``,
        ``n_assets_without_factor``, ``deviation``, ``tolerance``, ``warn`` (True / False /
        None when no verdict is possible) and ``warn_reason``.
    """
    vals = [by_region.get(r) for r in asset_regions]
    have = [v for v in vals if v is not None]
    out: dict[str, Any] = {
        "method": "per-시도 ISIMIP factor, asset-weighted, vs the national factor "
        "(diagnostic only)",
        "national_factor": national,
        "n_assets": len(vals),
        "n_assets_without_factor": len(vals) - len(have),
        "tolerance": tol,
        "weighted_factor": None,
        "deviation": None,
        "warn": None,
        "warn_reason": None,
    }
    if not have or national is None or national <= 0:
        return out
    weighted = float(np.mean(have))
    dev = weighted / national - 1.0
    out.update(weighted_factor=weighted, deviation=dev, warn=abs(dev) > tol)
    if out["warn"]:
        out["warn_reason"] = (
            f"asset-weighted provincial factor {weighted:.2f} vs national {national:.2f} "
            f"({dev:+.0%}, tolerance ±{tol:.0%}): the national factor may not represent "
            "where the assets are"
        )
    return out


def scale_frequency(hazard: Any, factor: float) -> Any:
    """Copy of ``hazard`` with every event frequency multiplied by ``factor``.

    Algorithm:
        $$ \\nu_e' = f \\, \\nu_e $$   ASCII: freq'[e] = f * freq[e]  (1/year; f > 0, -).
        The intensity (depth, m) and fraction are untouched; the input is not mutated.

    Raises:
        ValueError: ``factor`` is not a finite positive number.
    """
    f = float(factor)
    if not np.isfinite(f) or f <= 0:
        raise ValueError(f"frequency factor must be finite and > 0, got {factor!r}")
    out = copy.deepcopy(hazard)
    out.frequency = np.asarray(hazard.frequency, dtype=float) * f
    log.debug("scale_frequency: factor=%.4f events=%d", f, len(out.frequency))
    return out


def assign_regions(lat: np.ndarray, lon: np.ndarray, shp: Path) -> np.ndarray:
    """SGIS 시도 name of each point (``UNASSIGNED`` outside), by STRtree point-in-polygon.

    Polygons are read and indexed as published (no union, no repair); a point on a shared
    border takes the first polygon found.
    """
    import geopandas as gpd
    import shapely
    from shapely import STRtree

    gdf = gpd.read_file(shp).to_crs(4326)
    names = gdf["SIDO_NM"].astype(str).to_numpy()
    tree = STRtree(list(gdf.geometry))
    pts = shapely.points(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    pt_idx, poly_idx = tree.query(pts, predicate="intersects")
    out = np.full(len(pts), UNASSIGNED, dtype=object)
    first, where = np.unique(pt_idx, return_index=True)
    out[first] = names[poly_idx[where]]
    log.debug("assign_regions: %d points, %d assigned", len(pts), len(first))
    return out.astype(str)
