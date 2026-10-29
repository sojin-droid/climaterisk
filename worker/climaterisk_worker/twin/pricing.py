"""Price the synthetic city's assets through the engine, not by hand.

Every asset is priced with a single call to
:func:`climaterisk_worker.physical_risk.engine.calculate` — the same call the batch runner
makes. The published JRC depth-damage curve (``registry``) and CLIMADA's ``ImpactCalc``
do the arithmetic. A portfolio cross-check keeps the per-asset sum equal to the portfolio
``aai_agg`` (rtol 1e-9).
"""

from __future__ import annotations

from typing import Any

from climaterisk_worker.twin.spec import MODEL_ID


def _defences(
    hazard: Any, rp: float, threshold_hazard: Any | None = None
) -> tuple[Any, dict[str, Any]]:
    """Protected hazard and the engine's ``defences`` dict for a uniform design RP.

    Algorithm:
        $$\\tilde{I}_{e,c} = I_{e,c}\\,\\mathbb{1}\\big[I_{e,c} > I^{P}_{c}\\big]$$

        ASCII: keep event intensity at centroid c only where above the local design depth
        at return period P; flood-prone cells below the design standard are zeroed.

    $I_{e,c}$ flood depth of event $e$ at centroid $c$ (m); $P$ design return period
    (years); $I^P_c$ depth at $c$ reached once every $P$ years (m).

    Args:
        hazard: The CLIMADA ``Hazard`` to protect (the future hazard under climate change).
        rp: Uniform design return period (years).
        threshold_hazard: When set (the present hazard), the design depth is derived from it
            and applied fixed to ``hazard`` — defences keep their present height instead of,
            for a future hazard, silently growing with the flood.

    Returns:
        ``(protected_hazard, defences_dict)`` where ``defences_dict`` has the keys the
        engine's ``calculate`` expects: ``undefended``, ``return_period``,
        ``national_return_period``, ``frequent``.
    """
    import numpy as np

    from climaterisk_worker.physical_risk import protection

    n = hazard.centroids.size
    rps = np.full(n, float(rp))
    base = threshold_hazard or hazard
    # floods already at the most frequent layer (the engine's undefended_frequent_flooding)
    frequent = protection.local_intensity(hazard, 1.0 / float(np.max(hazard.frequency))) > 0
    threshold = protection.thresholds(base, rps)
    priced = protection.protect_with_threshold(hazard, threshold)
    return priced, {
        "undefended": hazard,
        "return_period": rps,
        "national_return_period": float(rp),
        "frequent": frequent,
    }


def _levee_rp_array(hazard: Any, assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """Per-centroid design return period for the local bank levee.

    Algorithm:
        $$\\mathrm{RP}_i = \\mathrm{rp}_{lev} \\text{ in the band, } 0 \\text{ elsewhere}$$
        ASCII: RP_i = rp_lev if row_i in levee_rows and col_i in levee_cols else 0

    The band is the floodplain behind the levee, not the bank cells alone: a levee keeps
    water out of the land it fronts. Flanking round the levee ends is ignored.

    $r_i, c_i$ grid row/col of asset $i$'s representative point — nodes use their own
    ``(row, col)``, edges use the midpoint of their two endpoints; $\\mathrm{rp}_{lev}$ is the
    levee design return period (years); $0$ means undefended (the ``stepfunction`` rule in
    :func:`protection.protect` removes nothing where the threshold is 0).
    """
    import numpy as np

    lev = spec["levee"]
    lo_r, hi_r = lev["rows"]
    lo_c, hi_c = lev["cols"]
    rp_lev = float(lev["return_period_years"])
    out = np.zeros(len(assets), dtype=float)
    for i, a in enumerate(assets):
        if "node" in a:
            r, c = a["node"]
        else:
            (r1, c1), (r2, c2) = a["edge"]
            r, c = (r1 + r2) / 2.0, (c1 + c2) / 2.0
        if lo_r <= r <= hi_r and lo_c <= c <= hi_c:
            out[i] = rp_lev
    return out


def _levee_defences(
    hazard: Any,
    assets: list[dict[str, Any]],
    spec: dict[str, Any],
    threshold_hazard: Any | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Protected hazard and engine defences dict for the local bank levee.

    Unlike :func:`_defences` (a single national return period for every cell) this is a
    **per-centroid** standard: only the levee band in the spec (the floodplain behind it) is
    defended to ``levee.return_period_years``, the rest is `0` (undefended). Cells defended to
    a higher standard than the national one are flagged ``levee_protected`` by the engine.

    ``threshold_hazard`` (the present hazard) is the climate-change path: the per-centroid
    design depth is derived from it and applied fixed to ``hazard`` (the future hazard), so
    the levee keeps its present height instead of, for a future hazard, silently growing with
    the flood (see AGENTS.md, step 3).
    """
    import numpy as np

    from climaterisk_worker.physical_risk import protection

    rp_arr = _levee_rp_array(hazard, assets, spec)
    base = threshold_hazard or hazard
    frequent = protection.local_intensity(hazard, 1.0 / float(np.max(hazard.frequency))) > 0
    threshold = protection.thresholds(base, rp_arr)
    priced = protection.protect_with_threshold(hazard, threshold)
    return priced, {
        "undefended": hazard,
        "return_period": rp_arr,
        "national_return_period": 0.0,
        "frequent": frequent,
    }


def defences_for(
    hazard: Any,
    assets: list[dict[str, Any]],
    spec: dict[str, Any],
    protection_rp: float | None = None,
    levee: bool = False,
    threshold_hazard: Any | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """The protected hazard and the engine's ``defences`` dict for a defence scenario.

    Args:
        hazard: The CLIMADA ``Hazard`` to protect (the future hazard under climate change).
        assets: Asset dicts (needed for the levee's per-centroid standard).
        spec: City specification (uses the ``levee`` section when ``levee`` is set).
        protection_rp: Uniform design return period (years); ``None`` = no defences.
        levee: When true, apply the local bank levee from the spec (per-centroid standard)
            instead of a uniform protection standard.
        threshold_hazard: When set (the present hazard), the design depth is derived from it
            and applied fixed to ``hazard`` — defences keep their present height. Without it
            (the default) the design depth is derived from ``hazard`` itself, which silently
            raises the levee on a future hazard (see AGENTS.md, step 3).

    Returns:
        ``(priced_hazard, defences_dict)``. ``defences_dict`` is ``None`` for no defences and
        otherwise carries ``undefended``, ``return_period`` (per-centroid),
        ``national_return_period`` and ``frequent`` — the keys
        :func:`climaterisk_worker.physical_risk.engine.calculate` reads.
    """
    if levee:
        return _levee_defences(hazard, assets, spec, threshold_hazard)
    if protection_rp:
        return _defences(hazard, protection_rp, threshold_hazard)
    return hazard, None


def price_assets(
    assets: list[dict[str, Any]],
    hazard: Any,
    protection_rp: float | None = None,
    levee: bool = False,
    spec: dict[str, Any] | None = None,
    threshold_hazard: Any | None = None,
) -> list[Any]:
    """One engine ``ResultRow`` per asset — the same call the batch runner makes.

    Args:
        assets: Asset dicts from :func:`climaterisk_worker.twin.build_city`.
        hazard: CLIMADA ``Hazard`` from :func:`climaterisk_worker.twin.hazard.build_hazard`.
        protection_rp: When set, the hazard is protected to this uniform design return period
            (years) before pricing; the undefended EAL is still computed for each asset.
        levee: When true, apply the local bank levee from ``spec`` (per-centroid standard)
            instead of a uniform return period.
        spec: City specification (required for ``levee``).
        threshold_hazard: When set (the present hazard), defences derive their design depth
            from it and apply it fixed to ``hazard`` — defences keep their present height
            under climate change (see ``defences_for``).

    Returns:
        A list of :class:`~climaterisk.physical_risk.metrics.ResultRow` objects, one per
        asset, in the same order as ``assets``.
    """
    from climaterisk_worker.physical_risk import engine

    priced_hazard, defences = defences_for(
        hazard, assets, spec or {}, protection_rp, levee, threshold_hazard
    )
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
        assets: Asset dicts from :func:`climaterisk_worker.twin.build_city`.
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
