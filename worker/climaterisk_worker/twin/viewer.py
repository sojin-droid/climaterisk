"""Static HTML map of the synthetic city (Folium).

A Folium 0.20 map with:
- Lots coloured by EAL % of asset value.
- A return-period slider for flood depth.
- Out-of-service nodes per return period.

All inputs are synthetic (synthetic_city_spec.json). Runs in the CLIMADA
worker environment (Folium 0.20 is in .climada-env).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_map(
    graph: Any,
    assets: list[dict[str, Any]],
    hazard: Any,
    rows: list[Any],
    net: list[dict[str, Any]],
    spec: dict[str, Any],
    path: Path,
) -> None:
    """Render a static Folium HTML map of the synthetic city.

    Layers:
    - EAL % by lot (permanent, always visible).
    - Flood depth per return period, toggled by an HTML range slider
      (one FeatureGroup per RP event).
    - Out-of-service nodes per return period, toggled with the same slider.

    The known-trap service cut-off indicator is shown alongside EAL in the
    slider info panel: a defence that floods out nodes can *reduce* the
    cut-off count, so the two move together here rather than being compared on
    the cut-off value alone.

    Args:
        graph: NetworkX street graph (nodes carry ``x_m``, ``y_m``, ``kind``).
        assets: Asset dicts from :func:`build_city` (need ``latitude``,
            ``longitude``, ``kind``, ``asset_value_usd``, ``node``/``edge``).
        hazard: CLIMADA ``Hazard`` — the priced/defended hazard (uses
            ``event_name``, ``frequency``, ``intensity``).
        rows: Engine ``ResultRow`` list, one per asset (uses
            ``eal_as_pct_of_assets``).
        net: Network-failure records per event (from :func:`network_failure`).
        spec: City specification (uses ``network.failure_depth_m``).
        path: Output ``.html`` path.
    """
    import json

    import folium
    import numpy as np
    from branca.colormap import linear

    # --- hazard data (n_events x n_assets depth matrix) ---
    event_names: list[str] = list(hazard.event_name)
    intensity = hazard.intensity
    depth = intensity.toarray() if hasattr(intensity, "toarray") else np.asarray(intensity)
    n_rp = len(event_names)

    # --- EAL % per asset ---
    eal_pcts = np.array([float(r.eal_as_pct_of_assets or 0.0) for r in rows])

    # --- lat/lon for all assets ---
    lats = [a["latitude"] for a in assets]
    lons = [a["longitude"] for a in assets]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    # --- node positions for road lines ---
    node_latlon = {a["node"]: (a["latitude"], a["longitude"]) for a in assets if "node" in a}

    # --- base map ---
    m = folium.Map(
        location=center,
        zoom_start=14,
        tiles="OpenStreetMap",
    )

    # --- colormaps ---
    max_eal = float(max(eal_pcts.max(), 1e-6))
    eal_cmap = linear.OrRd_09.scale(0, max_eal)
    eal_cmap.caption = "EAL % of asset value"
    eal_cmap.add_to(m)

    max_depth = float(max(depth.max() if depth.size else 0.0, 0.1))
    depth_cmap = linear.Blues_09.scale(0, max_depth)
    depth_cmap.caption = "Flood depth (m)"
    depth_cmap.add_to(m)

    # --- road network ---
    roads = folium.FeatureGroup(name="Road network", overlay=True, show=True)
    for a, b in graph.edges:
        if a in node_latlon and b in node_latlon:
            folium.PolyLine(
                locations=[node_latlon[a], node_latlon[b]],
                color="#999999",
                weight=1.0,
                opacity=0.4,
            ).add_to(roads)
    roads.add_to(m)

    # --- EAL % layer (permanent) ---
    eal_fg = folium.FeatureGroup(name="EAL % by lot", overlay=True, show=True)
    for a, pct in zip(assets, eal_pcts, strict=True):
        color = eal_cmap(float(pct)) if pct > 0 else "#cccccc"
        folium.CircleMarker(
            location=[a["latitude"], a["longitude"]],
            radius=3.5,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(
                f"{a['facility_id']}<br>{a['kind']}<br>"
                f"EAL {float(pct):.3f}%<br>"
                f"Value ${a['asset_value_usd']:,.0f}",
                max_width=220,
            ),
        ).add_to(eal_fg)
    eal_fg.add_to(m)

    # --- depth + out-of-service layers per return period ---
    limit = float(spec["network"]["failure_depth_m"])
    depth_fg_names: list[str] = []
    oos_fg_names: list[str] = []

    for e, rp_name in enumerate(event_names):
        # Depth layer.
        d_fg = folium.FeatureGroup(name=f"depth_{rp_name}", overlay=True, show=False, control=False)
        for i, a in enumerate(assets):
            d = float(depth[e, i])
            if d > 0:
                folium.CircleMarker(
                    location=[a["latitude"], a["longitude"]],
                    radius=6,
                    color=depth_cmap(d),
                    fill=True,
                    fill_color=depth_cmap(d),
                    fill_opacity=0.7,
                ).add_to(d_fg)
        d_fg.add_to(m)
        depth_fg_names.append(d_fg.get_name())

        # Out-of-service layer.
        o_fg = folium.FeatureGroup(name=f"oos_{rp_name}", overlay=True, show=False, control=False)
        for i, a in enumerate(assets):
            if "node" in a and float(depth[e, i]) > limit:
                folium.CircleMarker(
                    location=[a["latitude"], a["longitude"]],
                    radius=8,
                    color="red",
                    fill=True,
                    fill_color="red",
                    fill_opacity=0.8,
                    popup=f"Out of service ({rp_name})",
                ).add_to(o_fg)
        o_fg.add_to(m)
        oos_fg_names.append(o_fg.get_name())

    folium.LayerControl(collapsed=False).add_to(m)

    # --- return-period slider ---
    map_name = m.get_name()
    depth_names_js = json.dumps(depth_fg_names)
    oos_names_js = json.dumps(oos_fg_names)
    rp_labels_js = json.dumps(event_names)
    net_info_js = json.dumps(
        [
            {
                "event": n["event"],
                "rp": n["return_period_years"],
                "failed": n["failed_nodes"],
                "cut_off_value": round(n["residential_cut_off_value_usd"], 2),
            }
            for n in net
        ]
    )

    slider_html = (
        '<div id="rpSliderWrapper" style="'
        "position:fixed;bottom:20px;left:50%;"
        "transform:translateX(-50%);z-index:9999;"
        "background:rgba(255,255,255,0.95);"
        "padding:10px 20px;border-radius:8px;"
        "box-shadow:0 2px 10px rgba(0,0,0,0.3);"
        "font-family:sans-serif;display:flex;"
        'align-items:center;gap:10px;">'
        '<span style="font-weight:bold;">Return period:</span>'
        f'<input type="range" id="rpSlider" min="0" max="{n_rp - 1}" '
        f'value="0" step="1" style="width:200px;">'
        f'<span id="rpLabel" style="font-weight:bold;"> {event_names[0]}</span>'
        '<span style="color:#444;">&bull; Flood depth</span>'
        '<span style="color:red;">&bull; Out-of-service nodes</span>'
        '<span id="rpInfo" style="margin-left:10px;font-size:12px;color:#444;">'
        f"Failed: {net[0]['failed_nodes'] if net else 0}"
        " | Cut-off: $0"
        "</span>"
        "</div>"
    )

    slider_js = (
        "<script>\n"
        "setTimeout(function() {\n"
        f"    var mapObj = {map_name};\n"
        f"    var depthLayers = {depth_names_js};\n"
        f"    var oosLayers = {oos_names_js};\n"
        f"    var rpLabels = {rp_labels_js};\n"
        f"    var netData = {net_info_js};\n"
        "\n"
        "    function showRP(idx) {\n"
        "        for (var i = 0; i < depthLayers.length; i++) {\n"
        "            var dl = window[depthLayers[i]];\n"
        "            var ol = window[oosLayers[i]];\n"
        "            if (i === idx) {\n"
        "                if (dl && !mapObj.hasLayer(dl)) mapObj.addLayer(dl);\n"
        "                if (ol && !mapObj.hasLayer(ol)) mapObj.addLayer(ol);\n"
        "            } else {\n"
        "                if (dl && mapObj.hasLayer(dl)) mapObj.removeLayer(dl);\n"
        "                if (ol && mapObj.hasLayer(ol)) mapObj.removeLayer(ol);\n"
        "            }\n"
        "        }\n"
        "        var label = document.getElementById('rpLabel');\n"
        "        if (label) label.textContent = ' ' + rpLabels[idx];\n"
        "        var info = document.getElementById('rpInfo');\n"
        "        if (info && netData[idx]) {\n"
        "            var nd = netData[idx];\n"
        "            info.textContent = 'Failed: ' + nd.failed +\n"
        "                ' | Cut-off: $' + Math.round(nd.cut_off_value).toLocaleString();\n"
        "        }\n"
        "    }\n"
        "\n"
        "    showRP(0);\n"
        "\n"
        "    var slider = document.getElementById('rpSlider');\n"
        "    if (slider) {\n"
        "        slider.addEventListener('input', function() {\n"
        "            showRP(parseInt(this.value));\n"
        "        });\n"
        "    }\n"
        "    var wrapper = document.getElementById('rpSliderWrapper');\n"
        "    if (wrapper) wrapper.style.display = 'flex';\n"
        "}, 0);\n"
        "</script>"
    )

    m.get_root().html.add_child(folium.Element(slider_html))
    m.get_root().html.add_child(folium.Element(slider_js))
    m.save(str(path))
