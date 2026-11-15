"""Time viewer for the real site: rain snapshots over OSM footprints (AGENTS.md step R2b).

One self-contained HTML page (deck.gl 9.4.1 and KaTeX 0.16.11 from jsDelivr) that plays the
``--pluvial`` snapshots of the Gangnam station area for two runs, buildings as walls and no
buildings, with a toggle between them:

- terrain cells coloured by bare-earth elevation, water cells (> 1 cm) on top in blue, darker
  for deeper water (drawn with the height exaggerated, see the legend);
- real OSM building footprints (walls only) extruded to ``height_m``, grey when dry, yellow
  when wet, red from 0.5 m, by the deepest water reached so far in the one-cell ring of open
  cells around the building's wall cells;
- the rain hyetograph (mm/h) with a time cursor, live numbers, the governing equations.

Algorithm:
    Frame statistics for cells $(i,j)$ (open cells only)::

        $$A = \\Delta x^2\\,\\#\\{h_{ij} > 0.10\\},\\quad
          V = \\Delta x^2 \\sum_{ij} h_{ij},\\quad h_{\\max} = \\max_{ij} h_{ij}$$

    ASCII: A = dx^2 * count(h > 10 cm); V = dx^2 * sum(h); hmax = max(h).

    Building exposure: with $F_b$ the cells whose centre lies in the footprint and $W$ all wall
    cells, the ring is $R_b = (F_b \\oplus 3\\times3) \\setminus (F_b \\cup W)$ and the colour
    value at frame $t$ is $\\max_{s \\le t}\\max_{c \\in R_b} h_c(s)$.
    ASCII: ring = dilate(footprint cells, 3x3) minus footprint minus walls;
    value(t) = max over frames <= t and ring cells of depth.

    $h$ water depth (m), $\\Delta x$ cell size (m), $A$ flooded area (m^2, shown in km^2), $V$
    stored water (m^3). Depths are whole centimetres (:func:`viewer_time.encode_frames`).
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from climaterisk_worker.twin.viewer3d import DECKGL_URL
from climaterisk_worker.twin.viewer_time import KATEX_BASE, encode_frames

#: Flooded-area threshold (m) for the live numbers and the comparison table.
FLOOD_M: float = 0.10


def _footprint_parts(geom: Any) -> list[Any]:
    """Polygon parts of a footprint geometry (a MultiPolygon gives several)."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon" and not g.is_empty]


def _building_records(grid: Any, buildings: Any, walls: Any) -> list[dict[str, Any]]:
    """Wall buildings as plain dicts: ring, height, labels and the ring cells to watch."""
    import numpy as np
    import shapely

    lat, lon = grid.centres()
    lat_axis, lon_axis = lat[:, 0], lon[0, :]
    ny, nx = grid.ny, grid.nx
    out: list[dict[str, Any]] = []
    for _, row in buildings[buildings["is_wall"]].iterrows():
        for part in _footprint_parts(row.geometry):
            w, s, e, n = part.bounds
            i0, i1 = np.searchsorted(lat_axis, [s, n])
            j0, j1 = np.searchsorted(lon_axis, [w, e])
            foot = np.zeros((ny, nx), dtype=bool)
            if i1 > i0 and j1 > j0:
                sub_lon, sub_lat = np.meshgrid(lon_axis[j0:j1], lat_axis[i0:i1])
                foot[i0:i1, j0:j1] = shapely.contains_xy(part, sub_lon, sub_lat)
            if not foot.any():  # footprint smaller than a cell: take the cell under the centroid
                c = part.centroid
                ci = int(np.clip(np.floor((c.y - grid.south) * 111320.0 / grid.cell_m), 0, ny - 1))
                cj = int(
                    np.clip(
                        np.floor((c.x - grid.west) * grid.lon_m_per_deg / grid.cell_m), 0, nx - 1
                    )
                )
                foot[ci, cj] = True
            grown = foot.copy()
            grown[1:, :] |= foot[:-1, :]
            grown[:-1, :] |= foot[1:, :]
            grown[:, 1:] |= foot[:, :-1]
            grown[:, :-1] |= foot[:, 1:]
            grown[1:, 1:] |= foot[:-1, :-1]
            grown[:-1, :-1] |= foot[1:, 1:]
            grown[1:, :-1] |= foot[:-1, 1:]
            grown[:-1, 1:] |= foot[1:, :-1]
            ring = grown & ~foot & ~walls
            xs, ys = part.exterior.coords.xy
            out.append(
                {
                    "ring": [[round(x, 6), round(y, 6)] for x, y in zip(xs, ys, strict=True)],
                    "h": round(float(row["height_m"]), 1),
                    "kind": str(row["building"]),
                    "src": str(row["height_source"]),
                    "cells": [int(v) for v in np.flatnonzero(ring.ravel())],
                }
            )
    return out


def _run_summary(depth: Any, walls: Any, cell_m: float) -> dict[str, float]:
    """Peak flooded area (km^2, > 10 cm at one snapshot) and maximum depth (m) of a run."""
    import numpy as np

    open_cells = ~np.asarray(walls, dtype=bool)
    d = np.where(open_cells[None, :, :], depth, 0.0)
    area = (d > FLOOD_M).sum(axis=(1, 2)) * cell_m**2 / 1e6
    return {"peak_area_km2": float(area.max()), "max_depth_m": float(d.max())}


def render_site_view(
    with_npz: Path | str,
    without_npz: Path | str,
    grid: Any,
    buildings: Any,
    spec: dict[str, Any],
    out_html: Path,
    title: str,
) -> Path:
    """Write the animated rain-flood page of a real site.

    Args:
        with_npz: ``--pluvial`` snapshots of the run with buildings as walls (``times_s``,
            ``depth`` (n_t, ny, nx), ``bed``, ``cell_m``, ``walls``, optional ``max_froude``).
        without_npz: Same arrays for the run without buildings (``walls`` all False).
        grid: :class:`site.SiteGrid` of the rasters.
        buildings: GeoDataFrame from :func:`site.read_buildings`; only walls are drawn.
        spec: City spec (``rain`` and ``site`` sections).
        out_html: Output HTML path.
        title: Page title and panel heading.

    Returns:
        ``out_html``.
    """
    import numpy as np

    from climaterisk_worker.twin import pluvial

    runs = {}
    for key, path in (("with", with_npz), ("without", without_npz)):
        with np.load(path) as z:
            runs[key] = {
                "times": np.asarray(z["times_s"], dtype=float),
                "depth": np.asarray(z["depth"], dtype=float),
                "bed": np.asarray(z["bed"], dtype=float),
                "cell": float(z["cell_m"]),
                "walls": np.asarray(z["walls"], dtype=bool),
                "fr": float(z["max_froude"]) if "max_froude" in z.files else None,
            }
    w = runs["with"]
    ny, nx = w["bed"].shape
    t_s, cum = pluvial.hyetograph(spec)
    r = spec["rain"]
    cn = float(r["cn_paved"])
    data = {
        "title": title,
        "times_s": [round(float(t), 3) for t in w["times"]],
        "ny": ny,
        "nx": nx,
        "cell_m": w["cell"],
        "west": grid.west,
        "south": grid.south,
        "lon_m_per_deg": grid.lon_m_per_deg,
        "bed": [round(float(v), 2) for v in w["bed"].ravel()],
        "walls": [int(v) for v in np.flatnonzero(w["walls"].ravel())],
        "frames_with": encode_frames(w["depth"]),
        "frames_without": encode_frames(runs["without"]["depth"]),
        "buildings": _building_records(grid, buildings, w["walls"]),
        "rain": {
            "t_s": [float(t) for t in t_s],
            "cum_mm": [float(c) for c in cum],
            "total_mm": float(r["total_mm"]),
            "duration_min": float(r["duration_min"]),
            "cn": cn,
            "q_mm": float(pluvial.runoff_mm(float(r["total_mm"]), cn)),
            "sewer_mm_h": float(r["sewer_capacity_mm_h"]),
        },
        "compare": {
            k: dict(
                _run_summary(runs[k]["depth"], runs[k]["walls"], runs[k]["cell"]), fr=runs[k]["fr"]
            )
            for k in ("with", "without")
        },
    }
    frs = [runs[k]["fr"] for k in ("with", "without") if runs[k]["fr"] is not None]
    if frs:
        fr_text = f"Max Froude {max(frs):.2f} is outside the scheme's validity on steep slopes: "
    else:
        fr_text = "The Froude number is outside the scheme's validity on steep slopes: "
    method = (
        "repo-specific pluvial method (design storm, NRCS CN, uniform sewer sink); "
        "assumptions, not calibrated. " + fr_text + "indicative only."
    )
    text = _PAGE.replace("%TITLE%", html.escape(title))
    text = text.replace("%DECKGL_URL%", DECKGL_URL).replace("%KATEX_BASE%", KATEX_BASE)
    text = text.replace("%METHOD%", html.escape(method))
    text = text.replace("%ATTR_OSM%", "© OpenStreetMap contributors (ODbL)")
    text = text.replace("%ATTR_DEM%", "Copernicus DEM GLO-30 (ESA)")
    text = text.replace("%SEWER%", f"{data['rain']['sewer_mm_h']:g}")
    text = text.replace("%CELL%", f"{w['cell']:g}")
    text = text.replace("%MANNING%", f"{float(spec['hydraulic']['manning_n']):g}")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    text = text.replace("%DATA%", payload)
    out = Path(out_html)
    out.write_text(text, encoding="utf-8")
    return out


_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%TITLE%</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="%KATEX_BASE%/katex.min.css">
<style>
:root{--ink:#1c2733;--mute:#5b6b7b;--line:#d6dde4;--panel:#fbfcfd;--accent:#1f6fb2}
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
  color:var(--ink);background:#10202e}
#app{display:flex;height:100%}
#stage{position:relative;flex:1;min-width:0}
#deck-container{position:absolute;inset:0}
#side{width:400px;max-width:44vw;overflow-y:auto;background:var(--panel);
  padding:16px 18px 24px;border-left:1px solid var(--line);font-size:13px;line-height:1.45}
#side h1{font-size:17px;margin:0 0 6px}
#side h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);
  margin:18px 0 6px}
#side p{margin:6px 0}
.eq{overflow-x:auto;padding:4px 0}
.sym{font-size:12px;color:var(--mute)}
.sym b{color:var(--ink)}
.box{position:absolute;z-index:5;background:rgba(251,252,253,.94);border-radius:10px;
  box-shadow:0 2px 12px rgba(0,0,0,.35)}
#controls{left:12px;right:12px;bottom:12px;display:flex;align-items:center;gap:12px;
  padding:10px 14px}
#playBtn{width:84px;padding:6px 0;font-size:14px;border:0;border-radius:6px;cursor:pointer;
  background:var(--accent);color:#fff}
#frameSlider{flex:1;min-width:80px}
#clock{font-variant-numeric:tabular-nums;font-weight:600;min-width:70px;text-align:right}
#speed{font-size:12px;padding:3px}
#legend{top:12px;left:12px;padding:10px 12px;font-size:12px}
#legend .bar{height:10px;width:180px;border-radius:3px;border:1px solid #aab4be;margin:3px 0}
#legend .ticks{display:flex;justify-content:space-between;color:var(--mute)}
#legend div.t{font-weight:600;margin-top:6px}
#legend div.t:first-child{margin-top:0}
#toggle{top:12px;right:12px;display:flex;padding:4px;gap:4px}
#toggle button{border:0;border-radius:7px;padding:8px 14px;font-size:13px;cursor:pointer;
  background:transparent;color:var(--ink)}
#toggle button.on{background:var(--accent);color:#fff}
#credit{right:12px;bottom:70px;padding:4px 8px;font-size:11px;color:var(--mute)}
.nums{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.num{background:#fff;border:1px solid var(--line);border-radius:8px;padding:7px 9px}
.num .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
.num .l{font-size:11px;color:var(--mute)}
canvas.plot{width:100%;display:block;background:#fff;border:1px solid var(--line);
  border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:right}
th:first-child,td:first-child{text-align:left}
.caveat{color:#7a4b00;background:#fff6e0;border:1px solid #efd9a6;border-radius:8px;
  padding:7px 9px;font-size:12px}
#loading{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:6;
  background:#fff;padding:8px 14px;border-radius:8px}
@media (max-width:800px){#app{flex-direction:column}#side{width:100%;max-width:none;
  max-height:45vh}#stage{min-height:55vh}}
</style>
<script src="%DECKGL_URL%"></script>
<script src="%KATEX_BASE%/katex.min.js"></script>
</head>
<body>
<div id="app">
<div id="stage">
  <div id="deck-container"></div>
  <div id="legend" class="box">
    <div class="t">Water depth (m), height drawn x4</div>
    <div class="bar" style="background:linear-gradient(90deg,#bfe3f7,#4aa3df,#1b5fa8,#0a2f6b)">
    </div>
    <div class="ticks"><span>0.01</span><span>0.5</span><span>1</span><span>2+</span></div>
    <div class="t">Building: deepest water beside it</div>
    <div class="bar" style="background:linear-gradient(90deg,#d9dde1,#f4d24a,#d62d20)"></div>
    <div class="ticks"><span>dry</span><span>wet</span><span>0.5 m+</span></div>
    <div class="t">Ground elevation (m)</div>
    <div class="bar" style="background:linear-gradient(90deg,#2f7f86,#a9bf86,#c9b27a,#8b5e3c)">
    </div>
    <div class="ticks"><span id="zLo">low</span><span id="zHi">high</span></div>
  </div>
  <div id="toggle" class="box">
    <button id="bWith" type="button" class="on">with buildings</button>
    <button id="bWithout" type="button">without buildings</button>
  </div>
  <div id="credit" class="box">%ATTR_OSM% &middot; %ATTR_DEM%</div>
  <div id="controls" class="box">
    <button id="playBtn" type="button">Play</button>
    <input id="frameSlider" type="range" min="0" max="0" step="1" value="0">
    <select id="speed" title="playback speed">
      <option value="0.5">0.5x</option><option value="1" selected>1x</option>
      <option value="2">2x</option><option value="4">4x</option>
    </select>
    <span id="clock">0:00</span>
  </div>
  <div id="loading">loading...</div>
</div>
<div id="side">
  <h1>%TITLE%</h1>
  <p>A design storm falling on the real Gangnam station area, run on a 2D local-inertial
  shallow-water model (Bates et al. 2010) on a %CELL% m raster, Manning
  n = %MANNING% s m<sup>-1/3</sup>. Buildings are OpenStreetMap footprints; use the toggle to
  compare with a run where they are removed. Drag to rotate, scroll to zoom, hover a building.</p>
  <p class="caveat"><b>Method:</b> %METHOD%</p>
  <h2>Rain</h2>
  <canvas id="rain" class="plot" width="720" height="190"></canvas>
  <p class="sym">Rain now: <b id="nRain">0</b> mm/h &middot; fallen so far:
  <b id="nFallen">0</b> of <b id="nTotal">0</b> mm</p>
  <h2>Now (<span id="runName">with buildings</span>)</h2>
  <div class="nums">
    <div class="num"><div class="v" id="nTime">0:00</div><div class="l">time (h:mm)</div></div>
    <div class="num"><div class="v" id="nArea">0.000</div>
      <div class="l">flooded area &gt; 10 cm (km<sup>2</sup>)</div></div>
    <div class="num"><div class="v" id="nVol">0</div>
      <div class="l">stored water (m<sup>3</sup>)</div></div>
    <div class="num"><div class="v" id="nMax">0.00</div>
      <div class="l">maximum depth (m)</div></div>
  </div>
  <p class="sym">Buildings turned yellow or red: <b id="nBld">0</b> of <b id="nBldAll">0</b>.</p>
  <h2>Flooded area over time</h2>
  <canvas id="series" class="plot" width="720" height="190"></canvas>
  <p class="sym"><span style="color:#1f6fb2">&#9632;</span> with buildings
  <span style="color:#d6561f;margin-left:10px">&#9632;</span> without buildings</p>
  <h2>Full runs</h2>
  <table id="cmp"></table>
  <p class="sym">Peak over the saved snapshots (every 5 min); area counts cells deeper
  than 10 cm.</p>
  <h2>Governing equations</h2>
  <p class="sym">Continuity (mass), rain source R and sewer sink S:</p>
  <div class="eq" data-tex="\frac{\partial h}{\partial t} = -\frac{\partial q_x}{\partial x}
    -\frac{\partial q_y}{\partial y} + R - S"></div>
  <p class="sym">Momentum, q-centred, with Manning friction:</p>
  <div class="eq" data-tex="q_x^{n+1} = \frac{q_c - g\,h_f\,\Delta t\,\partial\eta/\partial x}
    {1 + g\,\Delta t\,n^2\,|q_c|\,/\,h_f^{7/3}}"></div>
  <p class="sym">Rain excess, NRCS curve number:</p>
  <div class="eq" data-tex="Q = \frac{(P-I_a)^2}{P-I_a+S_r},\quad I_a = 0.2\,S_r,\quad
    S_r = \frac{25400}{CN} - 254"></div>
  <p class="sym"><b>h</b> depth (m); <b>q</b> discharge per unit width (m<sup>2</sup>/s);
  <b>R</b> rain excess (m/s); <b>S</b> sewer sink, capped at %SEWER% mm/h per unit catchment
  area; <b>&eta;</b> = h + z water surface (m); <b>z</b> ground elevation (m);
  <b>h<sub>f</sub></b> depth at the cell face (m); <b>q<sub>c</sub></b> neighbour-averaged
  discharge (m<sup>2</sup>/s); <b>g</b> 9.81 m/s<sup>2</sup>; <b>&Delta;t</b> time step (s);
  <b>n</b> Manning roughness; <b>P</b> cumulative rain (mm); <b>Q</b> runoff (mm);
  <b>I<sub>a</sub></b> initial abstraction (mm); <b>S<sub>r</sub></b> retention (mm);
  <b>CN</b> curve number (-). Roof rain is moved onto the open cells, so both runs get the same
  rain and sewer.</p>
  <h2>Read this honestly</h2>
  <p class="caveat">Storm shape, sewer capacity and edge slope are assumptions. The ground is a
  30 m satellite surface model with the buildings removed and interpolated, so streets are
  smoothed. The scheme drops the advection term, so fast shallow flow on slopes is only
  indicative. Terrain is drawn flat, coloured by elevation.</p>
  <p class="sym">%ATTR_OSM%. %ATTR_DEM%.</p>
</div>
</div>
<script id="twin-data" type="application/json">%DATA%</script>
<script>
const D = JSON.parse(document.getElementById('twin-data').textContent);
const NY = D.ny, NX = D.nx, CELL = D.cell_m, N = D.times_s.length;
const WET = 0.01, FLOOD = 0.10, VIS = 4;
const M_LAT = 111320;
const $ = id => document.getElementById(id);

function decode(b64) {
  const s = atob(b64), out = new Float32Array(s.length >> 1);
  for (let i = 0; i < out.length; i++) {
    out[i] = (s.charCodeAt(2 * i) | (s.charCodeAt(2 * i + 1) << 8)) / 100;
  }
  return out;
}
const isWall = new Uint8Array(NY * NX);
D.walls.forEach(k => { isWall[k] = 1; });
const bed = Float32Array.from(D.bed);
const bld = D.buildings;
const RUNS = {
  with: {frames: D.frames_with.map(decode)},
  without: {frames: D.frames_without.map(decode)},
};
for (const key of ['with', 'without']) {
  const run = RUNS[key];
  run.stats = run.frames.map(f => {
    let wet = 0, vol = 0, mx = 0;
    for (let k = 0; k < f.length; k++) {
      if (isWall[k]) { continue; }
      const h = f[k];
      if (h > FLOOD) { wet++; }
      vol += h;
      if (h > mx) { mx = h; }
    }
    return {area: wet * CELL * CELL / 1e6, vol: vol * CELL * CELL, max: mx};
  });
  // Deepest water so far in each building's ring of open cells.
  run.reach = new Float32Array(N * bld.length);
  for (let t = 0; t < N; t++) {
    for (let b = 0; b < bld.length; b++) {
      let m = t ? run.reach[(t - 1) * bld.length + b] : 0;
      for (const c of bld[b].cells) { if (run.frames[t][c] > m) { m = run.frames[t][c]; } }
      run.reach[t * bld.length + b] = m;
    }
  }
}

function ramp(stops, t) {
  t = Math.max(0, Math.min(1, t));
  const s = t * (stops.length - 1), i = Math.min(Math.floor(s), stops.length - 2);
  const u = s - i, a = stops[i], b = stops[i + 1];
  return [0, 1, 2].map(k => Math.round(a[k] + (b[k] - a[k]) * u));
}
const WATER = [[191, 227, 247], [74, 163, 223], [27, 95, 168], [10, 47, 107]];
function waterColor(h) {
  const t = h < 0.5 ? 0.33 * Math.sqrt(h / 0.5) : 0.33 + 0.67 * Math.min(1, (h - 0.5) / 1.5);
  return ramp(WATER, t).concat([h < 0.1 ? 175 : 225]);
}
const BUILD = [[217, 221, 225], [244, 210, 74], [214, 45, 32]];
function buildingColor(h) {
  if (h <= WET) { return [205, 209, 214, 255]; }
  return ramp(BUILD, 0.5 + 0.5 * Math.min(1, h / 0.5)).concat([255]);
}
let zMin = Infinity, zMax = -Infinity;
for (const z of bed) { zMin = Math.min(zMin, z); zMax = Math.max(zMax, z); }
$('zLo').textContent = zMin.toFixed(0) + ' m';
$('zHi').textContent = zMax.toFixed(0) + ' m';
const GROUND = [[47, 127, 134], [169, 191, 134], [201, 178, 122], [139, 94, 60]];
function groundColor(z) { return ramp(GROUND, (z - zMin) / Math.max(1e-6, zMax - zMin)); }

// Cell polygons in lng/lat: row i spans south + i*CELL m northward, column j east.
const dLat = CELL / M_LAT, dLon = CELL / D.lon_m_per_deg;
const cells = [];
for (let i = 0; i < NY; i++) {
  for (let j = 0; j < NX; j++) {
    const x0 = D.west + j * dLon, y0 = D.south + i * dLat, idx = i * NX + j;
    cells.push({idx, col: groundColor(bed[idx]).concat([255]),
      ring: [[x0, y0], [x0 + dLon, y0], [x0 + dLon, y0 + dLat], [x0, y0 + dLat]]});
  }
}
const openCells = cells.filter(c => !isWall[c.idx]);

let mode = 'with', cur = 0, playing = false, last = 0, acc = 0, deckgl = null;

function layers() {
  const run = RUNS[mode], f = run.frames[cur];
  const R = run.reach.subarray(cur * bld.length, (cur + 1) * bld.length);
  const wet = openCells.filter(c => f[c.idx] > WET);
  const G = window.deck;
  const out = [
    new G.SolidPolygonLayer({id: 'ground', data: cells, getPolygon: c => c.ring,
      getFillColor: c => c.col, extruded: false}),
    new G.SolidPolygonLayer({id: 'water', data: wet, getPolygon: c => c.ring,
      getElevation: c => f[c.idx] * VIS, getFillColor: c => waterColor(f[c.idx]),
      extruded: true, material: false,
      updateTriggers: {getElevation: [cur, mode], getFillColor: [cur, mode]}}),
  ];
  const items = bld.map((b, n) => Object.assign({n}, b));
  if (mode === 'with') {
    out.push(new G.SolidPolygonLayer({id: 'buildings', data: items, getPolygon: b => b.ring,
      getElevation: b => b.h, getFillColor: b => buildingColor(R[b.n]), extruded: true,
      pickable: true, material: true,
      updateTriggers: {getFillColor: [cur, mode]}}));
  } else {
    out.push(new G.PolygonLayer({id: 'outlines', data: items, getPolygon: b => b.ring,
      filled: false, stroked: true, getLineColor: [230, 235, 240, 150], getLineWidth: 1,
      lineWidthUnits: 'pixels', pickable: false}));
  }
  return out;
}

function rainRate(t) {  // mm/h at time t (s)
  const ts = D.rain.t_s, c = D.rain.cum_mm;
  for (let k = 0; k < ts.length - 1; k++) {
    if (t >= ts[k] && t < ts[k + 1]) { return (c[k + 1] - c[k]) / (ts[k + 1] - ts[k]) * 3600; }
  }
  return 0;
}
function rainFallen(t) {
  const ts = D.rain.t_s, c = D.rain.cum_mm;
  if (t <= ts[0]) { return 0; }
  if (t >= ts[ts.length - 1]) { return c[c.length - 1]; }
  for (let k = 0; k < ts.length - 1; k++) {
    if (t < ts[k + 1]) { return c[k] + (c[k + 1] - c[k]) * (t - ts[k]) / (ts[k + 1] - ts[k]); }
  }
  return 0;
}
function hm(t) {
  const m = Math.round(t / 60);
  return Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0');
}

const TMAX = D.times_s[N - 1] || 1;
function drawRain() {
  const cv = $('rain'), g = cv.getContext('2d'), W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  const ts = D.rain.t_s, c = D.rain.cum_mm, rates = [];
  for (let k = 0; k < ts.length - 1; k++) {
    rates.push((c[k + 1] - c[k]) / (ts[k + 1] - ts[k]) * 3600);
  }
  const rmax = Math.max(...rates, 1e-9), X = t => 8 + (W - 16) * t / TMAX;
  g.fillStyle = '#4aa3df';
  rates.forEach((r, k) => {
    const x0 = X(ts[k]), x1 = X(ts[k + 1]), h = (H - 34) * r / rmax;
    g.fillRect(x0 + 1, H - 18 - h, x1 - x0 - 2, h);
  });
  const x = X(D.times_s[cur]);
  g.beginPath(); g.moveTo(x, 4); g.lineTo(x, H - 18);
  g.strokeStyle = '#1c2733'; g.lineWidth = 2; g.stroke();
  g.fillStyle = '#5b6b7b'; g.font = '19px sans-serif';
  g.fillText('peak ' + rmax.toFixed(0) + ' mm/h', 12, 22);
  g.fillText('0', 8, H - 2);
  g.textAlign = 'right'; g.fillText(hm(TMAX) + ' h', W - 8, H - 2); g.textAlign = 'left';
}

function drawSeries() {
  const cv = $('series'), g = cv.getContext('2d'), W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  const amax = Math.max(1e-9, ...RUNS.with.stats.map(s => s.area),
    ...RUNS.without.stats.map(s => s.area));
  const line = (run, color, dash) => {
    g.beginPath();
    run.stats.forEach((s, t) => {
      const x = 8 + (W - 16) * D.times_s[t] / TMAX, y = H - 10 - (H - 34) * s.area / amax;
      if (t) { g.lineTo(x, y); } else { g.moveTo(x, y); }
    });
    g.setLineDash(dash); g.strokeStyle = color; g.lineWidth = 3; g.stroke(); g.setLineDash([]);
  };
  line(RUNS.with, '#1f6fb2', []);
  line(RUNS.without, '#d6561f', [10, 6]);
  const x = 8 + (W - 16) * D.times_s[cur] / TMAX;
  g.beginPath(); g.moveTo(x, 4); g.lineTo(x, H - 4);
  g.strokeStyle = '#1c2733'; g.lineWidth = 2; g.stroke();
  g.fillStyle = '#5b6b7b'; g.font = '19px sans-serif';
  g.fillText('peak ' + amax.toFixed(3) + ' km²', 12, 22);
}

function show(i) {
  cur = Math.max(0, Math.min(N - 1, i));
  const run = RUNS[mode], s = run.stats[cur], t = D.times_s[cur];
  $('frameSlider').value = String(cur);
  $('clock').textContent = hm(t);
  $('nTime').textContent = hm(t);
  $('nArea').textContent = s.area.toFixed(3);
  $('nVol').textContent = Math.round(s.vol).toLocaleString('en-US');
  $('nMax').textContent = s.max.toFixed(2);
  $('nRain').textContent = rainRate(t).toFixed(0);
  $('nFallen').textContent = rainFallen(t).toFixed(0);
  let hit = 0;
  for (let b = 0; b < bld.length; b++) { if (run.reach[cur * bld.length + b] > WET) { hit++; } }
  $('nBld').textContent = String(hit);
  drawRain();
  drawSeries();
  if (deckgl) { deckgl.setProps({layers: layers()}); }
}

function setMode(m) {
  mode = m;
  $('bWith').className = m === 'with' ? 'on' : '';
  $('bWithout').className = m === 'without' ? 'on' : '';
  $('runName').textContent = m === 'with' ? 'with buildings' : 'without buildings';
  show(cur);
}

function setPlaying(on) {
  playing = on;
  $('playBtn').textContent = on ? 'Pause' : 'Play';
  if (on && cur >= N - 1) { show(0); }
  last = 0;
  if (on) { requestAnimationFrame(tick); }
}
function tick(ts) {
  if (!playing) { return; }
  if (last) { acc += (ts - last) / 1000 * parseFloat($('speed').value); }
  last = ts;
  const step = 0.3;  // seconds of wall clock per frame at 1x
  while (acc >= step) { acc -= step; show(cur + 1); }
  if (cur >= N - 1) { setPlaying(false); return; }
  requestAnimationFrame(tick);
}

function fillTable() {
  const a = D.compare.with, b = D.compare.without;
  const fr = x => x.fr === null ? '-' : x.fr.toFixed(2);
  $('cmp').innerHTML = '<tr><th></th><th>with</th><th>without</th></tr>' +
    '<tr><td>peak flooded area (km²)</td><td>' + a.peak_area_km2.toFixed(3) + '</td><td>' +
    b.peak_area_km2.toFixed(3) + '</td></tr>' +
    '<tr><td>max depth (m)</td><td>' + a.max_depth_m.toFixed(2) + '</td><td>' +
    b.max_depth_m.toFixed(2) + '</td></tr>' +
    '<tr><td>max Froude</td><td>' + fr(a) + '</td><td>' + fr(b) + '</td></tr>';
}

function init() {
  document.querySelectorAll('[data-tex]').forEach(el => {
    try {
      window.katex.render(el.getAttribute('data-tex').replace(/\s+/g, ' '), el,
        {displayMode: true, throwOnError: false});
    } catch (e) { el.textContent = el.getAttribute('data-tex'); }
  });
  $('nBldAll').textContent = String(bld.length);
  $('nTotal').textContent = D.rain.total_mm.toFixed(0);
  fillTable();
  const sl = $('frameSlider');
  sl.max = String(N - 1);
  sl.addEventListener('input', () => { setPlaying(false); show(parseInt(sl.value, 10)); });
  $('playBtn').addEventListener('click', () => setPlaying(!playing));
  $('bWith').addEventListener('click', () => setMode('with'));
  $('bWithout').addEventListener('click', () => setMode('without'));
  const G = window.deck;
  if (!G || !G.Deck) {
    $('loading').textContent = 'deck.gl did not load (check access to cdn.jsdelivr.net).';
    show(0);
    return;
  }
  const lon = D.west + NX * dLon / 2, lat = D.south + NY * dLat / 2;
  deckgl = new G.Deck({
    parent: document.getElementById('deck-container'),  // deck.gl 9: parent, not container
    initialViewState: {longitude: lon, latitude: lat - 0.0012, zoom: 15.6, pitch: 50,
      bearing: 0},
    controller: true,
    backgroundColor: [16, 32, 46],
    getTooltip: ({object}) => object && object.kind && mode === 'with' &&
      ('OSM building=' + object.kind + '\n' + object.h.toFixed(1) + ' m tall (from ' +
      object.src + ')\ndeepest water nearby ' +
      RUNS.with.reach[cur * bld.length + object.n].toFixed(2) + ' m'),
    layers: [],
  });
  $('loading').style.display = 'none';
  show(0);
}
window.addEventListener('load', init);
</script>
</body>
</html>
"""
