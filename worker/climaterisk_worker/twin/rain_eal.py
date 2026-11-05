"""Expected annual loss over a return-period ladder of storms (step R3b).

A ladder of storms with return periods $T_1 < \\dots < T_K$ is priced as one river-flood
hazard with $K$ events, each carrying the annual frequency of its layer, so CLIMADA's
average annual impact is the step integral of the loss-exceedance curve.

Algorithm:
    $$f_k = \\frac1{T_k} - \\frac1{T_{k+1}},\\quad f_K = \\frac1{T_K},\\qquad
    \\mathrm{EAL}_b = \\sum_{k=1}^{K} f_k\\,L_b(T_k)$$
    ASCII: f_k = 1/T_k - 1/T_(k+1); f_K = 1/T_K; EAL_b = sum_k f_k L_b(T_k).

    $T_k$ return period (years), $f_k$ annual frequency of the layer (1/year), $L_b(T_k)$
    loss (USD) of building $b$ in the $T_k$ storm. Storms more frequent than $T_1$ cause no
    loss (so $\\sum f = 1/T_1$); rarer than $T_K$ are not counted.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

import geopandas as gpd
import numpy as np

from climaterisk_worker.twin import site_loss
from climaterisk_worker.twin.spec import MODEL_ID

logger = logging.getLogger(__name__)


def layered_frequencies(rps: Any) -> np.ndarray:
    """Annual frequency (1/year) of each return-period layer (see module Algorithm).

    Args:
        rps: Ascending return periods (years).

    Returns:
        Float array, same length as ``rps``.
    """
    inv = 1.0 / np.asarray(rps, dtype=float)
    f = inv.copy()
    f[:-1] = inv[:-1] - inv[1:]
    return f


def spec_for_rain(spec: dict[str, Any], total_mm: float) -> dict[str, Any]:
    """Deep copy of ``spec`` with ``rain.total_mm`` replaced (``spec`` is not changed)."""
    out = copy.deepcopy(spec)
    out["rain"]["total_mm"] = float(total_mm)
    return out


def price_ladder(
    buildings: gpd.GeoDataFrame,
    depth_m: np.ndarray,
    value_usd: np.ndarray,
    rps: Any,
    spec: dict[str, Any],
    facility_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Price a ladder of storms through the engine as ONE hazard with ``K`` events.

    Args:
        buildings: Buildings (EPSG:4326).
        depth_m: Entry depth (m), shape ``(K, n)``; row k is the ``T_k`` storm.
        value_usd: Exposed value (USD) per building, length ``n``.
        rps: Ascending return periods (years), length ``K``.
        spec: City spec with a ``loss`` section.
        facility_extra: Optional extra engine facility keys applied to every building (for
            example ``{"flood_zero_damage_threshold_m": 0.15}``).

    Returns:
        ``rows`` (engine ``ResultRow`` per building), ``eal_usd`` ``(n,)``, ``event_loss_usd``
        ``(K, n)`` (per-storm loss of each building) and ``portfolio_eal_usd`` (ImpactCalc
        ``aai_agg``).
    """
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    from climaterisk_worker.physical_risk import engine
    from climaterisk_worker.twin.pricing import portfolio_impact

    depth = np.asarray(depth_m, dtype=float)
    value = np.asarray(value_usd, dtype=float)
    k_events = depth.shape[0]
    freq = layered_frequencies(rps)
    points = [g.representative_point() for g in buildings.geometry]
    lat = np.array([p.y for p in points])
    lon = np.array([p.x for p in points])
    kinds = site_loss._types(buildings, spec)
    assets = [
        {
            "facility_id": f"b{i}",
            "facility_name": f"building {i}",
            "latitude": float(lat[i]),
            "longitude": float(lon[i]),
            "asset_value_usd": float(value[i]),
            "property_type": kinds[i],
            **(facility_extra or {}),
        }
        for i in range(len(buildings))
    ]
    hazard = Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=lat, lon=lon),
        event_id=np.arange(1, k_events + 1),
        event_name=[f"rp{int(t)}" if float(t).is_integer() else f"rp{t}" for t in rps],
        date=np.zeros(k_events, dtype=int),
        frequency=freq,
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(depth),
        fraction=sparse.csr_matrix((depth > 0).astype(float)),
    )
    rows = [
        engine.calculate(
            a,
            hazard,
            "RF",
            model_id=MODEL_ID,
            flood_region="Asia",
            hazard_source="2D pluvial peak depth, return-period ladder (twin/rain_eal.py)",
            hazard_dataset="gangnam_rain_ladder",
            exposure_source="OSM footprints x assumed cost per m2 (spec loss)",
        )
        for a in assets
    ]
    eal = np.array([0.0 if r.eal_usd is None else float(r.eal_usd) for r in rows], dtype=float)
    _, impact = portfolio_impact(assets, hazard)
    event_loss = _event_losses(assets, hazard, k_events)
    logger.debug(
        "ladder priced: %d storms x %d buildings, EAL %.6g vs ImpactCalc %.6g",
        k_events,
        len(rows),
        eal.sum(),
        float(impact.aai_agg),
    )
    return {
        "rows": rows,
        "eal_usd": eal,
        "event_loss_usd": event_loss,
        "portfolio_eal_usd": float(impact.aai_agg),
    }


def _event_losses(assets: list[dict[str, Any]], hazard: Any, k_events: int) -> np.ndarray:
    """Per-event, per-building loss (USD) via ImpactCalc with the full matrix kept."""
    import pandas as pd
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet

    from climaterisk.physical_risk.metrics import jrc_sector_for
    from climaterisk_worker.physical_risk import registry

    funcs: dict[str, Any] = {}
    ids: list[int] = []
    for a in assets:
        sector = jrc_sector_for(a["property_type"])
        if sector not in funcs:
            funcs[sector] = registry.flood_impact_function("Asia", sector)
        ids.append(int(funcs[sector].id))
    frame = pd.DataFrame(
        {
            "latitude": [a["latitude"] for a in assets],
            "longitude": [a["longitude"] for a in assets],
            "value": [a["asset_value_usd"] for a in assets],
            "impf_RF": ids,
        }
    )
    imp = ImpactCalc(
        Exposures(frame, value_unit="USD"), ImpactFuncSet(list(funcs.values())), hazard
    )
    mat = imp.impact(save_mat=True, assign_centroids=True).imp_mat
    return np.asarray(mat.todense()).reshape(k_events, len(assets))


__all__ = ["layered_frequencies", "price_ladder", "spec_for_rain"]
