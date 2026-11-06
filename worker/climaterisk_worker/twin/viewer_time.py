"""Time viewer: 2D flood snapshots animated over the 3D synthetic city (AGENTS.md step 9b).

One self-contained HTML page (deck.gl 9.4.1 and KaTeX 0.16.11 from jsDelivr, nothing else)
that plays the depth snapshots written by ``--coastal2d`` (``surge_snapshots.npz``):

- the terrain raster coloured by bed elevation, and every wet cell (> 1 cm) of the current
  frame as a blue column from the bed up to the water surface, darker for deeper water;
- the synthetic buildings (:func:`viewer3d.generate_buildings`) extruded and coloured by the
  deepest water reached at their cell up to the current frame (grey when dry, yellow when
  wet, red from 1.5 m);
- a side panel with the local-inertial equations of :mod:`hydraulics` and live numbers for
  the current frame, plus the time series of flooded area and maximum depth.

Algorithm:
    Frame statistics, for cells $(i,j)$ of the land raster (row 0 is the boundary row)::

        $$A = \\Delta x^2\\,\\#\\{h_{ij} > 0.01\\},\\quad
          V = \\Delta x^2 \\sum_{ij} h_{ij},\\quad h_{\\max} = \\max_{ij} h_{ij}$$

    ASCII: A = dx^2 * count(h > 1 cm); V = dx^2 * sum(h); hmax = max(h).

    $h$ water depth (m), $\\Delta x$ cell size (m), $A$ flooded area (m^2, shown in km^2),
    $V$ stored water volume on land (m^3). Depths are stored as whole centimetres in
    little-endian ``uint16`` (:func:`encode_frames`), so every number on the page is exact to
    0.5 cm per cell.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from climaterisk_worker.twin.city import M_PER_DEG_LAT
from climaterisk_worker.twin.viewer3d import DECKGL_URL, generate_buildings

#: Exact pinned KaTeX build served from jsDelivr.
KATEX_VERSION: str = "0.16.11"
KATEX_BASE: str = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist"

#: Wet threshold (m) used for the flooded area; matches the page JavaScript.
WET_M: float = 0.01


def encode_frames(depth: Any) -> list[str]:
    """Encode a depth stack as one base64 string per time frame.

    Depth is rounded to whole centimetres, clipped to ``[0, 65535]`` cm, stored row-major
    as little-endian ``uint16`` and base64-encoded.

    Args:
        depth: Water depth (m), shape ``(n_t, ny, nx)``.

    Returns:
        ``n_t`` base64 strings (standard alphabet).
    """
    import numpy as np

    cm = np.clip(np.round(np.asarray(depth, dtype=float) * 100.0), 0, 65535).astype("<u2")
    return [base64.b64encode(np.ascontiguousarray(f).tobytes()).decode("ascii") for f in cm]


def decode_frame(text: str, ny: int, nx: int) -> Any:
    """Inverse of :func:`encode_frames` for one frame.

    Args:
        text: One base64 string from :func:`encode_frames`.
        ny: Raster rows.
        nx: Raster columns.

    Returns:
        Depth (m) as a float array of shape ``(ny, nx)``.
    """
    import numpy as np

    raw = np.frombuffer(base64.b64decode(text), dtype="<u2")
    return raw.reshape(ny, nx).astype(float) / 100.0


def _buildings_payload(spec: dict[str, Any], k: int) -> list[dict[str, Any]]:
    """Buildings as plain dicts with their raster node cell, for the page."""
    from climaterisk_worker.twin.city import build_city

    _, assets = build_city(spec)
    out: list[dict[str, Any]] = []
    for b in generate_buildings(spec, assets):
        r, c = b["node"]
        out.append(
            {
                "x": round(b["x_m"], 2),
                "y": round(b["y_m"], 2),
                "z": round(b["ground_m"], 3),
                "fx": round(b["footprint_x_m"], 2),
                "fy": round(b["footprint_y_m"], 2),
                "h": round(b["height_m"], 2),
                "kind": b["kind"],
                "row": 1 + r * k,
                "col": c * k,
            }
        )
    return out


def render_time_view(
    npz_path: Path | str, spec: dict[str, Any], out_html: Path, title: str
) -> Path:
    """Write the animated flood page for a snapshot file.

    Args:
        npz_path: ``.npz`` with ``times_s``, ``depth`` (n_t, ny, nx), ``bed`` (ny, nx) and
            ``cell_m`` as written by ``--coastal2d``.
        spec: City specification (grid, buildings and hydraulic sections).
        out_html: Output HTML path.
        title: Page title and panel heading.

    Returns:
        ``out_html``.
    """
    import math

    import numpy as np

    with np.load(npz_path) as z:
        times = np.asarray(z["times_s"], dtype=float)
        depth = np.asarray(z["depth"], dtype=float)
        bed = np.asarray(z["bed"], dtype=float)
        cell = float(z["cell_m"])
    ny, nx = bed.shape
    grid = spec["grid"]
    k = round(float(grid["spacing_m"]) / cell)
    lat0, lon0 = float(grid["origin_lat"]), float(grid["origin_lon"])
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    width_m, height_m = (nx - 1) * cell, (ny - 2) * cell
    data = {
        "title": title,
        "times_s": [round(float(t), 3) for t in times],
        "ny": ny,
        "nx": nx,
        "cell_m": cell,
        "origin": [lon0, lat0, 0.0],
        "center": [lon0 + width_m / 2 / m_per_deg_lon, lat0 + height_m / 2 / M_PER_DEG_LAT],
        "width_m": width_m,
        "height_m": height_m,
        "manning_n": float(spec["hydraulic"]["manning_n"]),
        "bed": [round(float(v), 3) for v in bed.ravel()],
        "frames": encode_frames(depth),
        "buildings": _buildings_payload(spec, k),
    }
    text = _PAGE.replace("%TITLE%", html.escape(title))
    text = text.replace("%DECKGL_URL%", DECKGL_URL).replace("%KATEX_BASE%", KATEX_BASE)
    text = text.replace("%MANNING%", f"{data['manning_n']:g}")
    text = text.replace("%CELL%", f"{cell:g}")
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
#side{width:380px;max-width:42vw;overflow-y:auto;background:var(--panel);
  padding:16px 18px 24px;border-left:1px solid var(--line);font-size:13px;line-height:1.45}
#side h1{font-size:17px;margin:0 0 6px}
#side h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);
  margin:18px 0 6px}
#side p{margin:6px 0}
.eq{overflow-x:auto;padding:4px 0}
.sym{font-size:12px;color:var(--mute)}
.sym b{color:var(--ink)}
#controls{position:absolute;left:12px;right:12px;bottom:12px;z-index:5;display:flex;
  align-items:center;gap:12px;background:rgba(251,252,253,.94);padding:10px 14px;
  border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.35)}
#playBtn{width:84px;padding:6px 0;font-size:14px;border:0;border-radius:6px;cursor:pointer;
  background:var(--accent);color:#fff}
#frameSlider{flex:1;min-width:80px}
#clock{font-variant-numeric:tabular-nums;font-weight:600;min-width:84px;text-align:right}
#speed{font-size:12px;padding:3px}
#legend{position:absolute;top:12px;left:12px;z-index:5;background:rgba(251,252,253,.94);
  padding:10px 12px;border-radius:10px;font-size:12px;box-shadow:0 2px 12px rgba(0,0,0,.35)}
#legend .bar{height:10px;width:170px;border-radius:3px;border:1px solid #aab4be;margin:3px 0}
#legend .ticks{display:flex;justify-content:space-between;color:var(--mute)}
#legend div.t{font-weight:600;margin-top:6px}
#legend div.t:first-child{margin-top:0}
.nums{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.num{background:#fff;border:1px solid var(--line);border-radius:8px;padding:7px 9px}
.num .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
.num .l{font-size:11px;color:var(--mute)}
#series{width:100%;height:92px;display:block;background:#fff;border:1px solid var(--line);
  border-radius:8px}
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
  <div id="legend">
    <div class="t">Water depth (m)</div>
    <div class="bar" style="background:linear-gradient(90deg,#bfe3f7,#4aa3df,#1b5fa8,#0a2f6b)">
    </div>
    <div class="ticks"><span>0.01</span><span>1</span><span>3</span><span>6+</span></div>
    <div class="t">Building: deepest water so far</div>
    <div class="bar" style="background:linear-gradient(90deg,#d9dde1,#f4d24a,#d62d20)"></div>
    <div class="ticks"><span>dry</span><span>wet</span><span>1.5 m+</span></div>
    <div class="t">Ground</div>
    <div class="bar" style="background:linear-gradient(90deg,#1f4e5f,#c9b98a,#6f9a5b,#8d8f6e)">
    </div>
    <div class="ticks"><span>sea bed</span><span>0 m</span><span>high</span></div>
  </div>
  <div id="controls">
    <button id="playBtn" type="button">Play</button>
    <input id="frameSlider" type="range" min="0" max="0" step="1" value="0">
    <select id="speed" title="playback speed">
      <option value="0.5">0.5x</option><option value="1" selected>1x</option>
      <option value="2">2x</option><option value="4">4x</option>
    </select>
    <span id="clock">t = 0.00 h</span>
  </div>
  <div id="loading">loading...</div>
</div>
<div id="side">
  <h1>%TITLE%</h1>
  <p>A sea surge on a low coast, run on a 2D local-inertial shallow-water model
  (Bates et al. 2010) over a synthetic city, with Manning roughness
  n = %MANNING% s m<sup>-1/3</sup> on a %CELL% m raster. Drag to rotate, scroll to zoom.</p>
  <h2>Governing equations</h2>
  <p class="sym">Continuity (mass):</p>
  <div class="eq" data-tex="\frac{\partial h}{\partial t} = -\frac{\partial q_x}{\partial x}
    -\frac{\partial q_y}{\partial y} + R - I"></div>
  <p class="sym">Momentum, q-centred, with Manning friction:</p>
  <div class="eq" data-tex="q_x^{n+1} = \frac{q_c - g\,h_f\,\Delta t\,\partial\eta/\partial x}
    {1 + g\,\Delta t\,n^2\,|q_c|\,/\,h_f^{7/3}}"></div>
  <p class="sym"><b>h</b> water depth (m); <b>q</b> = (q<sub>x</sub>, q<sub>y</sub>) discharge
  per unit width (m<sup>2</sup>/s); <b>R, I</b> rain and infiltration (m/s, zero here);
  <b>&eta;</b> = h + z water surface (m); <b>z</b> bed elevation (m); <b>h<sub>f</sub></b>
  depth at the cell face (m); <b>q<sub>c</sub></b> neighbour-averaged discharge (m<sup>2</sup>/s);
  <b>g</b> 9.81 m/s<sup>2</sup>; <b>&Delta;t</b> time step (s); <b>n</b> Manning roughness
  (s/m<sup>1/3</sup>).</p>
  <h2>Now</h2>
  <div class="nums">
    <div class="num"><div class="v" id="nTime">0.00</div><div class="l">time (h)</div></div>
    <div class="num"><div class="v" id="nArea">0.000</div>
      <div class="l">flooded area (km<sup>2</sup>)</div></div>
    <div class="num"><div class="v" id="nVol">0</div>
      <div class="l">stored water on land (m<sup>3</sup>)</div></div>
    <div class="num"><div class="v" id="nMax">0.00</div>
      <div class="l">maximum land depth (m)</div></div>
  </div>
  <p class="sym">Flooded = cells deeper than 1 cm; the sea boundary row is excluded.
  Buildings turned yellow or red: <b id="nBld">0</b> of <b id="nBldAll">0</b>.</p>
  <h2>Over time</h2>
  <canvas id="series" width="680" height="184"></canvas>
  <p class="sym"><span style="color:#1f6fb2">&#9632;</span> flooded area
  <span style="color:#d6561f;margin-left:10px">&#9632;</span> maximum land depth</p>
  <h2>Read this honestly</h2>
  <p class="caveat">These numbers come from a synthetic city with assumed inputs (terrain,
  surge height and duration, roughness). They show how the model behaves, not what would
  happen in a real place. The scheme drops the advection term, so fast shallow fronts are
  less accurate than the bulk flow.</p>
</div>
</div>
<script id="twin-data" type="application/json">%DATA%</script>
<script>
const D = JSON.parse(document.getElementById('twin-data').textContent);
const NY = D.ny, NX = D.nx, CELL = D.cell_m, WET = 0.01, N = D.times_s.length;
const $ = id => document.getElementById(id);

function decode(b64) {
  const s = atob(b64), out = new Float32Array(s.length >> 1);
  for (let i = 0; i < out.length; i++) {
    out[i] = (s.charCodeAt(2 * i) | (s.charCodeAt(2 * i + 1) << 8)) / 100;
  }
  return out;
}
const frames = D.frames.map(decode);
const bed = Float32Array.from(D.bed);

// Per-frame statistics (land rows only: raster row 0 is the boundary row).
const stats = frames.map(f => {
  let wet = 0, vol = 0, mx = 0;
  for (let i = NX; i < f.length; i++) {
    const h = f[i];
    if (h > WET) { wet++; }
    vol += h;
    if (h > mx) { mx = h; }
  }
  return {area: wet * CELL * CELL / 1e6, vol: vol * CELL * CELL, max: mx};
});
// Deepest water at each building's node cell up to each frame.
const bld = D.buildings;
const reach = new Float32Array(N * bld.length);
for (let t = 0; t < N; t++) {
  for (let b = 0; b < bld.length; b++) {
    const h = frames[t][bld[b].row * NX + bld[b].col];
    const prev = t ? reach[(t - 1) * bld.length + b] : 0;
    reach[t * bld.length + b] = Math.max(prev, h);
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
  const t = h < 1 ? 0.33 * Math.sqrt(h) : 0.33 + 0.67 * Math.min(1, (h - 1) / 5);
  return ramp(WATER, t).concat([h < 0.1 ? 150 : 205]);
}
const BUILD = [[217, 221, 225], [244, 210, 74], [214, 45, 32]];
function buildingColor(h) {
  if (h <= WET) { return [217, 221, 225, 255]; }
  return ramp(BUILD, 0.5 + 0.5 * Math.min(1, h / 1.5)).concat([255]);
}
function groundColor(z) {
  if (z <= -0.2) { return ramp([[31, 78, 95], [60, 120, 135]], (z + 5) / 4.8).concat([255]); }
  if (z < 0.8) { return ramp([[201, 185, 138], [160, 175, 110]], z / 0.8).concat([255]); }
  return ramp([[111, 154, 91], [141, 143, 110]], (z - 0.8) / 8).concat([255]);
}

// Cell geometry: row i sits (i-1) cells north of the origin, column j j cells east.
const cells = [];
for (let i = 0; i < NY; i++) {
  for (let j = 0; j < NX; j++) {
    const x = j * CELL, y = (i - 1) * CELL, h = CELL / 2, z = bed[i * NX + j];
    cells.push({i, j, idx: i * NX + j, z,
      ring: [[x - h, y - h, z], [x + h, y - h, z], [x + h, y + h, z], [x - h, y + h, z]],
      col: groundColor(z)});
  }
}

let cur = 0, playing = false, last = 0, acc = 0, deckgl = null;

function layers() {
  const f = frames[cur], R = reach.subarray(cur * bld.length, (cur + 1) * bld.length);
  const wet = cells.filter(c => f[c.idx] > WET);
  const common = {coordinateSystem: 'meter-offsets', coordinateOrigin: D.origin,
    positionFormat: 'XYZ'};
  const G = window.deck;
  return [
    new G.SolidPolygonLayer(Object.assign({id: 'ground', data: cells,
      getPolygon: c => c.ring, getFillColor: c => c.col, extruded: false}, common)),
    new G.SolidPolygonLayer(Object.assign({id: 'water', data: wet,
      getPolygon: c => c.ring, getElevation: c => f[c.idx],
      getFillColor: c => waterColor(f[c.idx]), extruded: true, material: false,
      updateTriggers: {getElevation: cur, getFillColor: cur}}, common)),
    new G.SolidPolygonLayer(Object.assign({id: 'buildings', data: bld.map((b, n) =>
      Object.assign({n}, b)),
      getPolygon: b => {
        const hx = b.fx / 2, hy = b.fy / 2;
        return [[b.x - hx, b.y - hy, b.z], [b.x + hx, b.y - hy, b.z],
          [b.x + hx, b.y + hy, b.z], [b.x - hx, b.y + hy, b.z]];
      },
      getElevation: b => b.h, getFillColor: b => buildingColor(R[b.n]), extruded: true,
      pickable: true, material: true,
      updateTriggers: {getFillColor: cur}}, common)),
  ];
}

function drawSeries() {
  const cv = $('series'), g = cv.getContext('2d'), W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  const tmax = D.times_s[N - 1] || 1;
  const amax = Math.max(1e-9, ...stats.map(s => s.area));
  const dmax = Math.max(1e-9, ...stats.map(s => s.max));
  const line = (key, vmax, color) => {
    g.beginPath();
    stats.forEach((s, t) => {
      const x = 8 + (W - 16) * D.times_s[t] / tmax, y = H - 10 - (H - 22) * s[key] / vmax;
      if (t) { g.lineTo(x, y); } else { g.moveTo(x, y); }
    });
    g.strokeStyle = color; g.lineWidth = 3; g.stroke();
  };
  line('area', amax, '#1f6fb2');
  line('max', dmax, '#d6561f');
  const x = 8 + (W - 16) * D.times_s[cur] / tmax;
  g.beginPath(); g.moveTo(x, 4); g.lineTo(x, H - 4);
  g.strokeStyle = '#1c2733'; g.lineWidth = 2; g.stroke();
  g.fillStyle = '#5b6b7b'; g.font = '20px sans-serif';
  g.fillText(amax.toFixed(2) + ' km² / ' + dmax.toFixed(1) + ' m max', 12, 22);
}

function show(i) {
  cur = Math.max(0, Math.min(N - 1, i));
  const s = stats[cur], th = D.times_s[cur] / 3600;
  $('frameSlider').value = String(cur);
  $('clock').textContent = 't = ' + th.toFixed(2) + ' h';
  $('nTime').textContent = th.toFixed(2);
  $('nArea').textContent = s.area.toFixed(3);
  $('nVol').textContent = Math.round(s.vol).toLocaleString('en-US');
  $('nMax').textContent = s.max.toFixed(2);
  let hit = 0;
  for (let b = 0; b < bld.length; b++) { if (reach[cur * bld.length + b] > WET) { hit++; } }
  $('nBld').textContent = String(hit);
  drawSeries();
  if (deckgl) { deckgl.setProps({layers: layers()}); }
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
  const step = 0.45;  // seconds of wall clock per frame at 1x
  while (acc >= step) { acc -= step; show(cur + 1); }
  if (cur >= N - 1) { setPlaying(false); return; }
  requestAnimationFrame(tick);
}

function init() {
  document.querySelectorAll('[data-tex]').forEach(el => {
    try {
      window.katex.render(el.getAttribute('data-tex').replace(/\s+/g, ' '), el,
        {displayMode: true, throwOnError: false});
    } catch (e) { el.textContent = el.getAttribute('data-tex'); }
  });
  $('nBldAll').textContent = String(bld.length);
  const sl = $('frameSlider');
  sl.max = String(N - 1);
  sl.addEventListener('input', () => { setPlaying(false); show(parseInt(sl.value, 10)); });
  $('playBtn').addEventListener('click', () => setPlaying(!playing));
  const G = window.deck;
  if (!G || !G.Deck) {
    $('loading').textContent = 'deck.gl did not load (check access to cdn.jsdelivr.net).';
    show(0);
    return;
  }
  deckgl = new G.Deck({
    parent: document.getElementById('deck-container'),  // deck.gl 9: parent, not container
    initialViewState: {longitude: D.center[0], latitude: D.center[1] - 0.004,
      zoom: 14.4, pitch: 58, bearing: 15},
    controller: true,
    backgroundColor: [16, 32, 46],
    getTooltip: ({object}) => object && object.kind &&
      (object.kind + ': ' + object.h.toFixed(1) + ' m tall, deepest water ' +
      reach[cur * bld.length + object.n].toFixed(2) + ' m'),
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
