"""Network service loss — an indicator, not a monetary damage.

An asset node fails when its flood depth exceeds the assumed passability limit. A residential
lot is then "cut off" when, in the street graph with failed nodes removed, it reaches no
working hospital or no working substation. The expected cut-off value is the frequency-
weighted loss of those disconnected lots.
"""

from __future__ import annotations

from typing import Any


def network_failure(
    graph: Any,
    assets: list[dict[str, Any]],
    hazard: Any,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Per return period: failed nodes and residential value cut off from services.

    Algorithm:
        $d > d_{fail}$: a node is out of service. A residential node is cut off when, in
        the street graph without failed nodes, it reaches neither a working hospital nor a
        working substation::

            E[\\text{cut-off value}] = \\\\sum_i f_i\\\\, V_{cut}(T_i)

        ASCII: E[cutoff] = sum_i f_i * V_cut(T_i)

        $d$ flood depth at a node (m), $d_{fail}$ failure depth threshold (m), $V_{cut}$
        value of cut-off residential lots (USD), $f_i$ frequency (1/year), $T_i$ return
        period (years).

    Args:
        graph: The NetworkX street graph from
            :func:`climaterisk.twin.build_city`.
        assets: Asset dicts (used for ``node`` index, ``asset_value_usd``).
        hazard: CLIMADA ``Hazard`` (``RF``); its intensity matrix is read row-wise per
            event.
        spec: City specification (uses the ``network`` section for
            ``failure_depth_m``).

    Returns:
        One record per event with ``failed_nodes``, ``substations_failed``,
        ``hospital_failed``, ``residential_flooded``, ``residential_cut_off``,
        ``residential_cut_off_value_usd`` and the event metadata.
    """
    import networkx as nx
    import numpy as np

    limit = float(spec["network"]["failure_depth_m"])
    node_idx = {a["node"]: i for i, a in enumerate(assets) if "node" in a}
    value = {a["node"]: a["asset_value_usd"] for a in assets if "node" in a}
    kind = nx.get_node_attributes(graph, "kind")
    depth = hazard.intensity.toarray()
    out = []
    for e, (name, freq) in enumerate(zip(hazard.event_name, hazard.frequency, strict=True)):
        failed = {n for n, i in node_idx.items() if depth[e, i] > limit}
        alive = graph.subgraph([n for n in graph.nodes if n not in failed])
        reach_h: set[Any] = set()
        reach_s: set[Any] = set()
        for comp in nx.connected_components(alive):
            kinds = {kind[n] for n in comp}
            if "hospital" in kinds:
                reach_h |= comp
            if "substation" in kinds:
                reach_s |= comp
        homes = [n for n in graph.nodes if kind[n] == "residential"]
        cut = [n for n in homes if n not in failed and (n not in reach_h or n not in reach_s)]
        out.append(
            {
                "event": name,
                "return_period_years": float(name[2:]),
                "frequency_per_year": float(freq),
                "failed_nodes": len(failed),
                "substations_failed": sum(kind[n] == "substation" for n in failed),
                "hospital_failed": any(kind[n] == "hospital" for n in failed),
                "residential_flooded": sum(kind[n] == "residential" for n in failed),
                "residential_cut_off": len(cut),
                "residential_cut_off_value_usd": float(np.sum([value[n] for n in cut])),
            }
        )
    return out
