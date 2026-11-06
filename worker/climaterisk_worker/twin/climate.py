"""Climate change — future river stage for the synthetic city twin.

The future stage shifts the stage law ``h(T) = stage_2y_m + stage_slope_m*ln(T/2)`` by
scenario-specific metres. Defence design depths are derived from the **present** hazard and
applied fixed to the future hazard (defences keep their present height) — see
:func:`climaterisk_worker.twin.pipeline.compare_climate`.

Nothing here re-implements pricing: the future hazard is a CLIMADA ``Hazard`` priced
through the same :func:`climaterisk_worker.physical_risk.engine.calculate` path as the
present, only its stage law is shifted and its defences keep their present design depth.
"""

from __future__ import annotations

from typing import Any


def future_scenarios(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The climate-change scenarios from the spec (``climate.future_scenarios``)."""
    return list(spec["climate"]["future_scenarios"])


def build_future_hazard(
    assets: list[dict[str, Any]], spec: dict[str, Any], scenario: dict[str, Any]
) -> Any:
    """CLIMADA river-flood ``Hazard`` (``RF``, metres) for a climate stage scenario.

    The stage law is shifted by the scenario's ``stage_2y_m_shift`` / ``stage_slope_m_shift``;
    return periods and centroid positions are unchanged from the present hazard, so the
    future hazard is directly comparable event-for-event with the present.

    Algorithm:
        $$h^{future}(T) = (h_2 + \\Delta h_2) + (b + \\Delta b)\\,\\ln(T / 2),\\\\qquad
          d(T) = \\max(0,\\; h^{future}(T) - z)$$
        ASCII: h_future(T) = (h_2 + dh2) + (b + db)*ln(T/2); d(T) = max(0, h_future(T) - z)

        $h_2$ present 2-year stage (m), $b$ present stage growth (m / ln-year),
        $\\Delta h_2$, $\\Delta b$ scenario shifts (m, m / ln-year), $T$ return period
        (years), $z$ ground elevation (m), $d$ flood depth (m, floored at 0).

    Args:
        assets: Asset dicts (need ``latitude``, ``longitude``, ``ground_m``).
        spec: City specification (uses the ``flood`` and ``climate`` sections).
        scenario: One entry from ``spec["climate"]["future_scenarios"]`` (shifts the stage
            law; ``name`` is the scenario label).

    Returns:
        A :class:`climada.hazard.Hazard` with ``haz_type="RF"``, ``units="m"`` and the
        event axis of the present hazard, depths shifted to the scenario.
    """
    import copy

    from climaterisk_worker.twin.hazard import build_hazard

    shifted = copy.deepcopy(spec)
    fl = shifted["flood"]
    fl["stage_2y_m"] = float(fl["stage_2y_m"]) + float(scenario["stage_2y_m_shift"])
    fl["stage_slope_m"] = float(fl["stage_slope_m"]) + float(scenario["stage_slope_m_shift"])
    return build_hazard(assets, shifted)
