"""Static HTML 3D viewer of the synthetic city (deck.gl).

A single self-contained HTML page (no server) showing:
- extruded buildings (footprint + storey-count geometry from the spec, per zone),
  coloured by EAL % of asset value;
- a semi-transparent water surface at the river stage $h(T)$ of a selectable
  return period;
- the synthetic terrain grid.

Buildings are generated with the spec seed from ``synthetic_city_spec.json``. In
THIS step (AGENTS.md step 5) the storey count is **geometry only**: it does not
feed the pricing/damage model and no EAL number changes (the ground-floor /
basement exposure it would drive is deferred — see AGENTS.md). deck.gl is loaded
from ``cdn.jsdelivr.net`` at the exact pinned version in ``DECKGL_VERSION``;
nothing is installed into the environment.

Algorithm:
    River stage at return period $T$ (bathtub water surface)::

        h(T) = h_2 + b\\,\\ln(T / 2)

    ASCII: h(T) = h_2 + b*ln(T/2)

    $h_2$ 2-year stage (m), $b$ stage growth (m per ln-year), $T$ return period
    (years). Each lot's building sits on its ground elevation $z$ (m above river
    datum) and rises $n_{\\text{storey}} \\times h_{\\text{storey}}$; the water
    plane at $h(T)$ covers every building whose top $z + n h_{\\text{storey}} <
    h(T)$ — exactly the synthetic depth model $d(T) = \\max(0, h(T) - z)$.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from climaterisk_worker.twin.city import M_PER_DEG_LAT

#: Exact pinned deck.gl UMD bundle (global ``deck``) served from jsDelivr.
DECKGL_VERSION: str = "9.4.1"
DECKGL_URL: str = f"https://cdn.jsdelivr.net/npm/deck.gl@{DECKGL_VERSION}/dist.min.js"


def generate_buildings(spec: dict[str, Any], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One synthetic 3D building per node lot, reproducible from the spec seed.

    Footprint size and storey count are drawn per zone (``spec['buildings']``)
    with a fresh ``numpy.random.default_rng`` seeded by ``spec['seed']``. The
    building's ground elevation, kind, node index and lat/lon are taken straight
    from the matching lot asset, so the geometry sits exactly on its priced lot
    and the water surface below aligns with the synthetic depth model.

    Stores (roads / edges) are skipped — they are the street network, not
    buildings. The storey count is geometry only here (see module docstring);
    no value or exposure field is changed by it.

    Args:
        spec: City specification (uses the ``buildings`` and ``grid`` sections).
        assets: Lot assets from :func:`build_city`; one ``node`` entry per
            intersection (edges are ignored).

    Returns:
        One building dict per node asset, in the same order, each carrying
        ``node``, ``kind``, ``x_m``, ``y_m``, ``ground_m``, ``latitude``,
        ``longitude``, ``footprint_x_m``, ``footprint_y_m``, ``storeys`` and
        ``height_m`` (storeys * ``floor_to_floor_m``).
    """
    import numpy as np

    rng = np.random.default_rng(int(spec["seed"]))
    grid = spec["grid"]
    dx = float(grid["spacing_m"])
    bld = spec["buildings"]
    ftm = float(bld["floor_to_floor_m"])
    per_kind = bld["per_kind"]
    out: list[dict[str, Any]] = []
    for a in assets:
        if "node" not in a:
            continue
        r, c = a["node"]
        kind = a["kind"]
        k = per_kind[kind]
        fx = float(rng.uniform(k["footprint_m"][0], k["footprint_m"][1]))
        fy = float(rng.uniform(k["footprint_m"][0], k["footprint_m"][1]))
        storeys = int(rng.integers(k["storeys"][0], k["storeys"][1] + 1))
        out.append(
            {
                "node": (r, c),
                "kind": kind,
                "x_m": c * dx,
                "y_m": r * dx,
                "ground_m": float(a["ground_m"]),
                "latitude": float(a["latitude"]),
                "longitude": float(a["longitude"]),
                "footprint_x_m": fx,
                "footprint_y_m": fy,
                "storeys": storeys,
                "height_m": storeys * ftm,
            }
        )
    return out


def _ring(b: dict[str, Any]) -> list[list[float]]:
    """Closed 3D footprint ring ``[x_m, y_m, ground_m]`` for one building."""
    x, y, z = b["x_m"], b["y_m"], b["ground_m"]
    hx = b["footprint_x_m"] / 2.0
    hy = b["footprint_y_m"] / 2.0
    return [
        [x - hx, y - hy, z],
        [x + hx, y - hy, z],
        [x + hx, y + hy, z],
        [x - hx, y + hy, z],
        [x - hx, y - hy, z],
    ]


def _lerp_color(lo: list[int], hi: list[int], t: float, alpha: int) -> list[int]:
    """Linear interpolate an RGB colour by ``t`` in [0, 1], append ``alpha``."""
    t = max(0.0, min(1.0, t))
    return [
        round(lo[0] + (hi[0] - lo[0]) * t),
        round(lo[1] + (hi[1] - lo[1]) * t),
        round(lo[2] + (hi[2] - lo[2]) * t),
        alpha,
    ]


def _palette_html(eal_max: float) -> str:
    """Inline legend swatches for the EAL % ramp (0 .. eal_max)."""
    steps = 5
    lo = [255, 255, 200]
    hi = [139, 0, 0]
    out = ['<div class="legend"><b>EAL % of value</b>']
    for i in range(steps):
        t = i / (steps - 1)
        col = _lerp_color(lo, hi, t, 230)
        pct = t * eal_max
        out.append(
            '<div class="row">'
            f'<span class="swatch" style="background:'
            f'rgb({col[0]},{col[1]},{col[2]})"></span>'
            f"{pct:.2f}%</div>"
        )
    out.append("</div>")
    return "".join(out)


def _stages(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return-period list with the synthetic stage law $h(T)=h_2+b\\ln(T/2)$."""
    fl = spec["flood"]
    h2 = float(fl["stage_2y_m"])
    b = float(fl["stage_slope_m"])
    rps = sorted(fl["return_periods"], key=float)
    return [
        {
            "rp": int(float(rp)),
            "label": f"{int(float(rp))} yr",
            "stage": h2 + b * math.log(float(rp) / 2.0),
        }
        for rp in rps
    ]


def render_3d(
    assets: list[dict[str, Any]],
    rows: list[Any],
    spec: dict[str, Any],
    path: Path,
) -> None:
    """Render a static deck.gl HTML page (city_3d.html).

    Layers:
        - terrain grid (coloured by elevation),
        - a semi-transparent water surface at the stage of a selectable return period,
        - extruded buildings coloured by EAL % of asset value, each sitting on its
          own ground elevation (storey count is geometry only — see module docstring).

    The water surface follows the synthetic stage law
    $h(T) = h_2 + b\\ln(T/2)$; a per-lot building is submerged wherever
    $h(T) > z + n_{\\text{storey}} h_{\\text{storey}}$, i.e. where the synthetic
    depth $d(T)=\\max(0,h(T)-z)$ exceeds the building height.

    Args:
        assets: Lot assets from :func:`build_city` (node assets carry the
            per-lot ground elevation and lat/lon).
        rows: Engine ``ResultRow`` list, one per asset in the same order
            (uses ``eal_as_pct_of_assets``). May be ``None`` to render geometry
            only (all lots coloured as unpriced).
        spec: City specification (uses ``grid``, ``flood`` and ``buildings``).
        path: Output ``.html`` path.
    """

    import math

    grid = spec["grid"]
    rows_n = int(grid["rows"])
    cols_n = int(grid["cols"])
    dx = float(grid["spacing_m"])
    lat0 = float(grid["origin_lat"])
    lon0 = float(grid["origin_lon"])
    bld_spec = spec["buildings"]
    ftm = float(bld_spec["floor_to_floor_m"])

    stages = _stages(spec)
    default_idx = len(stages) - 1  # largest return period = widest flooding
    default_stage = stages[default_idx]["stage"]

    buildings = generate_buildings(spec, assets)
    node_rows = [rows[i] for i, a in enumerate(assets) if "node" in a] if rows else []
    max_eal = max(
        (float(r.eal_as_pct_of_assets or 0.0) for r in node_rows),
        default=1e-9,
    )
    if max_eal <= 0.0:
        max_eal = 1e-9
    eal_lo = [255, 255, 200]
    eal_hi = [139, 0, 0]
    for bld, row in zip(buildings, node_rows, strict=True):
        eal = float(row.eal_as_pct_of_assets or 0.0)
        bld["eal_pct"] = eal
        if eal <= 0.0:
            bld["col"] = [190, 190, 190, 210]
        else:
            t = min(1.0, eal / max_eal)
            bld["col"] = _lerp_color(eal_lo, eal_hi, t, 210)
        bld["c"] = _ring(bld)
        bld["h"] = bld["height_m"]

    # --- terrain TIN: one quad per grid cell, coloured by elevation ---
    node_grid = {bld["node"]: bld for bld in buildings}
    zmin = min(bld["ground_m"] for bld in buildings)
    zmax = max(bld["ground_m"] for bld in buildings)
    t_lo = [170, 140, 90]
    t_hi = [120, 180, 120]
    terrain: list[dict[str, Any]] = []
    for r in range(rows_n - 1):
        for c in range(cols_n - 1):
            cells = [(r, c), (r + 1, c), (r + 1, c + 1), (r, c + 1)]
            if any(p not in node_grid for p in cells):
                continue
            vs = [node_grid[p] for p in cells]
            ring = [[v["x_m"], v["y_m"], v["ground_m"]] for v in vs]
            ring.append(ring[0])
            zmean = sum(v["ground_m"] for v in vs) / 4.0
            t = (zmean - zmin) / (zmax - zmin + 1e-9)
            terrain.append({"ring": ring, "col": _lerp_color(t_lo, t_hi, t, 235)})

    # city centre for the initial camera (flat-earth meters -> degrees)
    half_lat = (rows_n * dx) / 2.0 / M_PER_DEG_LAT
    half_lon = (cols_n * dx) / 2.0 / (M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    center_lat = lat0 + half_lat
    center_lon = lon0 + half_lon
    zoom = 14.0
    # fit the city width into the viewport width at this latitude
    mpp = 156543.0335 / (2.0**zoom)  # metres/pixel at the equator, zoom z
    fit = 0.8 * 1000.0 * mpp / max(cols_n * dx, 1.0)
    if fit > 0:
        zoom = max(10.0, min(18.0, 14.0 + math.log2(fit)))

    water_w = cols_n * dx
    water_h = rows_n * dx
    water_poly = [
        [0.0, 0.0, default_stage],
        [water_w, 0.0, default_stage],
        [water_w, water_h, default_stage],
        [0.0, water_h, default_stage],
        [0.0, 0.0, default_stage],
    ]

    data = {
        "origin": [lon0, lat0, 0.0],
        "center": [center_lon, center_lat],
        "grid_w_m": water_w,
        "grid_h_m": water_h,
        "zoom": zoom,
        "default_idx": default_idx,
        "water_init": {"poly": water_poly, "stage": default_stage},
        "stages": stages,
        "eal_max": max_eal,
        "n_buildings": len(buildings),
        "n_terrain": len(terrain),
        "floor_to_floor_m": ftm,
        "buildings": [
            {
                "c": bld["c"],
                "h": bld["h"],
                "col": bld["col"],
                "eal": bld.get("eal_pct", 0.0),
                "storeys": bld["storeys"],
                "kind": bld["kind"],
            }
            for bld in buildings
        ],
        "terrain": terrain,
    }
    text = _PAGE_TEMPLATE
    text = text.replace("%DECKGL_VERSION%", DECKGL_VERSION)
    text = text.replace("%DECKGL_URL%", DECKGL_URL)
    text = text.replace("%LEGEND%", _palette_html(max_eal))
    text = text.replace("%CITYDATA%", json.dumps(data, separators=(",", ":")))
    Path(path).write_text(text, encoding="utf-8")


_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Synthetic city 3D (deck.gl)</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
html,body{margin:0;height:100%;overflow:hidden;background:#111;font-family:sans-serif}
#deck-container{position:absolute;inset:0}
#ui{position:absolute;top:12px;left:12px;z-index:10;background:rgba(255,255,255,0.93);
    border-radius:10px;padding:12px 16px;box-shadow:0 2px 12px rgba(0,0,0,0.4);
    font-size:13px;max-width:256px}
#ui h2{font-size:13px;margin:0 0 8px;color:#222}
#ui .row{display:flex;align-items:center;gap:8px;margin:3px 0}
#ui .swatch{display:inline-block;width:14px;height:10px;border:1px solid #999;border-radius:2px}
#ui select{padding:2px 4px;font-size:12px}
#ui .note{color:#555;font-size:11px;margin-top:6px}
#loading{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
         z-index:9;background:#fff;border-radius:8px;padding:6px 12px;font-size:12px;
         box-shadow:0 2px 10px rgba(0,0,0,0.4)}
</style>
<script src="%DECKGL_URL%"></script>
</head>
<body>
<div id="deck-container"></div>
<div id="ui">
  <div style="font-weight:bold;margin-bottom:6px">Synthetic city 3D</div>
  <div>Return period</div>
  <select id="rpSelect"></select>
  <div id="stageInfo" style="margin-top:4px;font-size:12px;color:#333"></div>
  <div id="buildingsInfo" style="margin-top:2px;font-size:12px;color:#555"></div>
  <div class="note">Buildings coloured by EAL % of asset value. Water surface =
    river stage h(T) (bathtub). Stage law geometry only; storeys do not affect pricing.
    <br>deck.gl %DECKGL_VERSION%</div>
  %LEGEND%
</div>
<div id="loading">loading…</div>
<script>
const D = %CITYDATA%;
function init3d() {
  const GL = window.deck;
  if (!GL || !GL.Deck || !GL.SolidPolygonLayer) {
    document.getElementById('loading').textContent =
      'deck.gl did not load (check network to cdn.jsdelivr.net).';
    return;
  }
  const Deck = GL.Deck, SolidPolygonLayer = GL.SolidPolygonLayer;
  const effects = [];
  try { if (GL.LightingEffect) { effects.push(new GL.LightingEffect()); } } catch (e) {}

  const ORIGIN = D.origin;
  const W = D.grid_w_m, H = D.grid_h_m;

  const terrainLayer = new SolidPolygonLayer({
    id: 'terrain',
    data: D.terrain,
    getPolygon: d => d.ring,
    positionFormat: 'XYZ',
    coordinateSystem: 'meter-offsets',
    coordinateOrigin: ORIGIN,
    getFillColor: d => d.col,
    pickable: false,
    extruded: false,
    wireframe: false,
  });

  const buildingLayer = new SolidPolygonLayer({
    id: 'buildings',
    data: D.buildings,
    getPolygon: d => d.c,
    positionFormat: 'XYZ',
    coordinateSystem: 'meter-offsets',
    coordinateOrigin: ORIGIN,
    extruded: true,
    getElevation: d => d.h,
    elevationScale: 1,
    getFillColor: d => d.col,
    pickable: true,
    getTooltip: d => `${d.kind} lot\nEAL ${d.eal.toFixed(3)}% of value\n` +
      `${d.storeys} storeys, ${d.h.toFixed(1)} m`,
    material: true,
    opacity: 1,
  });

  function waterPoly(s) {
    return [[0, 0, s], [W, 0, s], [W, H, s], [0, H, s], [0, 0, s]];
  }
  const waterLayer = new SolidPolygonLayer({
    id: 'water',
    data: [{poly: waterPoly(D.water_init.stage)}],
    getPolygon: d => d.poly,
    positionFormat: 'XYZ',
    coordinateSystem: 'meter-offsets',
    coordinateOrigin: ORIGIN,
    extruded: false,
    getFillColor: [100, 149, 237, 130],
    pickable: false,
    wireframe: false,
  });

  const sel = document.getElementById('rpSelect');
  const stageInfo = document.getElementById('stageInfo');
  const bldInfo = document.getElementById('buildingsInfo');
  D.stages.forEach((st, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${st.label} — stage ${st.stage.toFixed(2)} m`;
    sel.add(opt);
  });
  sel.value = String(D.default_idx);
  stageInfo.textContent = `river stage ${D.water_init.stage.toFixed(2)} m above datum`;
  bldInfo.textContent = `${D.n_buildings} buildings · ${D.n_terrain} terrain cells`;

  sel.addEventListener('change', () => {
    const i = parseInt(sel.value, 10);
    const s = D.stages[i].stage;
    waterLayer.setProps({
      data: [{poly: waterPoly(s)}],
      updateTriggers: {getPolygon: s},
    });
    stageInfo.textContent = `river stage ${s.toFixed(2)} m above datum`;
  });

  const deckgl = new Deck({
    container: 'deck-container',
    initialViewState: {
      longitude: D.center[0],
      latitude: D.center[1],
      zoom: D.zoom,
      pitch: 45,
      bearing: 0,
    },
    controller: true,
    backgroundColor: [174, 203, 250],
    layers: [terrainLayer, waterLayer, buildingLayer],
    effects: effects,
  });
  document.getElementById('loading').style.display = 'none';
}
// Run on load (not setTimeout(0)) so the blocking deck.gl script and the DOM
// are ready before any layer is built -- see AGENTS.md known trap for step 4.
window.addEventListener('load', init3d);
</script>
</body>
</html>
"""
