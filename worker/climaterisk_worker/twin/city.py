"""City graph, terrain and assets — pure generator, no CLIMADA.

The city is a NetworkX grid of intersections. Each node gets a land use, a ground
elevation above river datum, and one asset (a "lot"). Each edge is a road segment with its
own (shorter) asset. Every value is an assumed constant from the spec JSON, not a
measurement.
"""

from __future__ import annotations

import math
from typing import Any

#: Flat-earth metres per degree latitude for the toy grid's lat/lon conversion.
M_PER_DEG_LAT: float = 111_320.0


def _to_latlon(x_m: float, y_m: float, grid: dict[str, Any]) -> tuple[float, float]:
    """Local metres east/north of the origin -> (lat, lon) on a flat-earth approximation.

    Args:
        x_m: Metres east of the grid origin.
        y_m: Metres north of the grid origin.
        grid: The spec's ``grid`` section (uses ``origin_lat``, ``origin_lon``).

    Returns:
        ``(latitude, longitude)`` as decimal degrees.
    """
    lat0, lon0 = float(grid["origin_lat"]), float(grid["origin_lon"])
    lat = lat0 + y_m / M_PER_DEG_LAT
    lon = lon0 + x_m / (M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return lat, lon


def _band(value: int, band: list[int]) -> bool:
    """True when ``value`` falls inside the inclusive ``band`` ``[lo, hi]``."""
    return band[0] <= value <= band[1]


def node_kind(rc: tuple[int, int], spec: dict[str, Any]) -> str:
    """Land use of the intersection at ``(row, col)``.

    Looks up the facility table first (substations, hospital), then the zoning bands.
    Falls back to ``"residential"`` for everything outside the bands.

    Args:
        rc: ``(row, col)`` intersection index in the grid.
        spec: City specification (uses the ``facilities`` and ``zoning`` sections).

    Returns:
        One of ``"substation"``, ``"hospital"``, ``"industrial"``,
        ``"commercial"``, ``"residential"``.
    """
    fac, zone = spec["facilities"], spec["zoning"]
    if [rc[0], rc[1]] in fac["substations"]:
        return "substation"
    if [rc[0], rc[1]] == fac["hospital"]:
        return "hospital"
    if _band(rc[0], zone["industrial_rows"]):
        return "industrial"
    if _band(rc[0], zone["commercial_rows"]) and _band(rc[1], zone["commercial_cols"]):
        return "commercial"
    return "residential"


def build_city(spec: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """Street graph (NetworkX) and the asset list: one lot per node, one road per edge.

    Returns:
        ``(graph, assets)``. Graph nodes are ``(row, col)`` with ``kind``, ``x_m``, ``y_m``
        and ``z_m``; each asset dict carries the engine's facility fields plus ``kind``,
        ``node`` (or ``edge``) and ``ground_m``.

    Algorithm:
        Ground elevation at a point $y$ metres inland from the river:

        $$z = \\max(0,\\; z_b + s\\,y + \\varepsilon),\\quad \\varepsilon \\sim N(0, \\sigma^2)$$

        ASCII: z = max(0, z_b + s*y + eps)

        $z_b$ bank height (m), $s$ slope (m/m), $\\sigma$ terrain noise (m).
        Each lot's replacement value is lognormal around the zone median (USD).
    """
    import networkx as nx
    import numpy as np

    rng = np.random.default_rng(int(spec["seed"]))
    grid, terr, vals, ptype = (
        spec["grid"],
        spec["terrain"],
        spec["values_usd"],
        spec["property_type"],
    )
    rows, cols, dx = int(grid["rows"]), int(grid["cols"]), float(grid["spacing_m"])

    graph = nx.grid_2d_graph(rows, cols)
    noise = rng.normal(0.0, float(terr["noise_sd_m"]), size=(rows, cols))
    for r, c in graph.nodes:
        y = r * dx
        z = float(terr["bank_m"]) + float(terr["slope_m_per_m"]) * y + float(noise[r, c])
        graph.nodes[(r, c)].update(kind=node_kind((r, c), spec), x_m=c * dx, y_m=y, z_m=max(0.0, z))

    sigma = float(vals["lognormal_sigma"])
    assets: list[dict[str, Any]] = []
    for r, c in sorted(graph.nodes):
        attrs = graph.nodes[(r, c)]
        kind = attrs["kind"]
        lat, lon = _to_latlon(attrs["x_m"], attrs["y_m"], grid)
        value = float(vals[kind]) * float(rng.lognormal(0.0, sigma))
        assets.append(
            {
                "facility_id": f"N{r:02d}_{c:02d}",
                "facility_name": f"{kind} lot ({r},{c})",
                "latitude": lat,
                "longitude": lon,
                "asset_value_usd": round(value, 2),
                "asset_value_currency": "USD",
                "property_type": ptype[kind],
                "kind": kind,
                "node": (r, c),
                "ground_m": attrs["z_m"],
            }
        )
    for a, b in sorted(graph.edges):
        na, nb = graph.nodes[a], graph.nodes[b]
        lat, lon = _to_latlon((na["x_m"] + nb["x_m"]) / 2, (na["y_m"] + nb["y_m"]) / 2, grid)
        assets.append(
            {
                "facility_id": f"E{a[0]:02d}_{a[1]:02d}-{b[0]:02d}_{b[1]:02d}",
                "facility_name": f"road {a}-{b}",
                "latitude": lat,
                "longitude": lon,
                "asset_value_usd": round(float(vals["road_per_m"]) * dx, 2),
                "asset_value_currency": "USD",
                "property_type": ptype["road"],
                "kind": "road",
                "edge": (a, b),
                "ground_m": (na["z_m"] + nb["z_m"]) / 2,
            }
        )
    return graph, assets
