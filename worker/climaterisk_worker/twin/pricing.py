"""Price the synthetic city's assets through the engine, not by hand.

Every asset is priced with a single call to
:func:`climaterisk_worker.physical_risk.engine.calculate` — the same call the batch runner
makes. The published JRC depth-damage curve (``registry``) and CLIMADA's ``ImpactCalc``
do the arithmetic. A portfolio cross-check keeps the per-asset sum equal to the portfolio
``aai_agg`` (rtol 1e-9).
"""

from __future__ import annotations

from typing import Any

from climaterisk.twin.spec import MODEL_ID


def _defences(hazard: Any, rp: float) -> tuple[Any, dict[str, Any]]:
    """Protected hazard and the engine's ``defences`` dict for a uniform design RP.

    Algorithm:
        $$\\tilde{I}_{e,c} = I_{e,c}\\,\\mathbb{1}\\big[I_{e,c} > I^{P}_{c}\\big]$$

        ASCII: keep event intensity at centroid c only where above the local design depth
        at return period P; flood-prone cells below the design standard are zeroed.

    $I_{e,c}$ flood depth of event $e$ at centroid $c$ (m); $P$ design return period
    (years); $I^P_c$ depth at $c$ reached once every $P$ years (m).

    Args:
        hazard: The undefended CLIMADA ``Hazard``.
        rp: Uniform design return period (years).

    Returns:
        ``(protected_hazard, defences_dict)`` where ``defences_dict`` has the keys the
        engine's ``calculate`` expects: ``undefended``, ``return_period``,
        ``national_return_period``, ``frequent``.
    """
    import numpy as np

    from climaterisk_worker.physical_risk import protection

    n = hazard.centroids.size
    rps = np.full(n, float(rp))
    # floods already at the most frequent layer (the engine's undefended_frequent_flooding)
    frequent = protection.local_intensity(hazard, 1.0 / float(np.max(hazard.frequency))) > 0
    return protection.protect(hazard, rps), {
        "undefended": hazard,
        "return_period": rps,
        "national_return_period": float(rp),
        "frequent": frequent,
    }


def price_assets(
    assets: list[dict[str, Any]], hazard: Any, protection_rp: float | None = None
) -> list[Any]:
    """One engine ``ResultRow`` per asset — the same call the batch runner makes.

    Args:
        assets: Asset dicts from :func:`climaterisk.twin.build_city`.
        hazard: CLIMADA ``Hazard`` from :func:`climaterisk_worker.twin.hazard.build_hazard`.
        protection_rp: When set, the hazard is protected to this uniform design return
            period (years) before pricing; the undefended EAL is still computed for each
            asset.

    Returns:
        A list of :class:`~climaterisk.physical_risk.metrics.ResultRow` objects, one per
        asset, in the same order as ``assets``.
    """
    from climaterisk_worker.physical_risk import engine

    priced_hazard, defences = hazard, None
    if protection_rp:
        priced_hazard, defences = _defences(hazard, protection_rp)
    rows = []
    for a in assets:
        facility = {k: v for k, v in a.items() if k not in ("node", "edge", "ground_m", "kind")}
        rows.append(
            engine.calculate(
                facility,
                priced_hazard,
                "RF",
                model_id=MODEL_ID,
                hazard_source="synthetic stage law (synthetic_city_spec.json)",
                hazard_dataset="synthetic_city",
                exposure_source="synthetic virtual city (NetworkX grid)",
                defences=defences,
            )
        )
    return rows


def portfolio_impact(assets: list[dict[str, Any]], hazard: Any) -> tuple[Any, Any]:
    """All assets in one ``Exposures`` priced in one ``ImpactCalc`` (cross-check + curve).

    Uses the same JRC curves from :mod:`climaterisk_worker.physical_risk.registry` as
    :func:`price_assets`, so the portfolio ``aai_agg`` must equal the sum of per-asset EAL
    — verified by the caller with rtol 1e-9.

    Args:
        assets: Asset dicts from :func:`climaterisk.twin.build_city`.
        hazard: CLIMADA ``Hazard`` to price against.

    Returns:
        ``(exposures, impact)`` — the CLIMADA ``Exposures`` (with a ``.gdf`` GeoDataFrame)
        and the ``Impact`` object.
    """
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
    exp = Exposures(frame, value_unit="USD")
    impact = ImpactCalc(exp, ImpactFuncSet(list(funcs.values())), hazard).impact(
        save_mat=False, assign_centroids=True
    )
    return exp, impact
