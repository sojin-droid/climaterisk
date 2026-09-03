import { useEffect, useRef, useState } from "react";
import {
  CircleMarker,
  ImageOverlay,
  MapContainer,
  Polygon,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import { LatLngBounds } from "leaflet";
import type {
  Asset,
  HazardCatalog,
  HazardCatalogEntry,
  HazardPreviewResult,
  Libraries,
  LitPopResult,
  Portfolio,
  Run,
} from "../types";
import { AssetEditor } from "../components/AssetEditor";
import { getHazardCatalog, getRun, hazardPreviewImageUrl, submitHazardPreview } from "../lib/api";
import { formatScenario, money } from "../lib/format";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Turbo colormap stops (match the worker's hazard raster + the legend gradient).
const TURBO_CSS = "linear-gradient(90deg,#30123b,#28829b,#a2fc3c,#fb8023,#7a0403)";
const TURBO_STOPS = [
  [48, 18, 59],
  [40, 130, 155],
  [162, 252, 60],
  [251, 128, 35],
  [122, 4, 3],
];
/** Turbo color at fraction f∈[0,1] — used to color exposure markers by value. */
function turboAt(f: number): string {
  const x = Math.max(0, Math.min(1, f)) * (TURBO_STOPS.length - 1);
  const i = Math.floor(x);
  const t = x - i;
  const a = TURBO_STOPS[i];
  const b = TURBO_STOPS[Math.min(i + 1, TURBO_STOPS.length - 1)];
  const c = a.map((v, k) => Math.round(v + (b[k] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

const EXPOSURE_LAYER = "__exposure__";

/** GeoJSON Polygon/LineString → leaflet [lat,lon][] positions (null for points/other). */
function geomPositions(geom: unknown): { kind: "polygon" | "line"; pos: [number, number][] } | null {
  const g = geom as { type?: string; coordinates?: unknown } | null;
  if (!g?.type) return null;
  if (g.type === "Polygon")
    return { kind: "polygon", pos: (g.coordinates as number[][][])[0].map(([lo, la]) => [la, lo]) };
  if (g.type === "LineString")
    return { kind: "line", pos: (g.coordinates as number[][]).map(([lo, la]) => [la, lo]) };
  return null;
}

function newAsset(lat: number, lon: number): Asset {
  return {
    id: crypto.randomUUID(),
    name: "New facility",
    lat: Number(lat.toFixed(5)),
    lon: Number(lon.toFixed(5)),
    sector: "real_estate",
    geographic_scale: "point",
    value: 0,
    currency: "USD",
    annual_emissions_tco2e: null,
    vulnerability_class: null, // follow sector default
    properties: {},
  };
}

function ClickToAdd({ onAdd }: { onAdd: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(e) {
      onAdd(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

/** Fly the map to a search result ([lat, lon, zoom]) whenever it changes. */
function FlyTo({ target }: { target: [number, number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (target) map.flyTo([target[0], target[1]], target[2]);
  }, [target, map]);
  return null;
}

/**
 * Frame the portfolio on first load, so a saved session opens where its assets are.
 *
 * Runs **once** per mount: after that the camera belongs to the user. Re-fitting on every
 * asset change would yank the view back while they are panning or placing points, and a
 * single asset gets a sensible zoom instead of Leaflet's maximum.
 */
function FitToAssets({ assets }: { assets: Asset[] }) {
  const map = useMap();
  const done = useRef(false);
  useEffect(() => {
    if (done.current || assets.length === 0) return;
    const fit = () => {
      const el = map.getContainer();
      // A 0-size container (pane collapsed, tab not yet laid out) makes Leaflet fit the
      // whole world instead. Do not consume the one-shot until the box is real, or the map
      // stays stuck on the Atlantic for the rest of the session.
      if (el.clientWidth === 0 || el.clientHeight === 0) return false;
      map.invalidateSize();
      if (assets.length === 1) {
        map.setView([assets[0].lat, assets[0].lon], 9);
      } else {
        map.fitBounds(new LatLngBounds(assets.map((a) => [a.lat, a.lon])).pad(0.4), {
          maxZoom: 9,
        });
      }
      done.current = true;
      return true;
    };
    if (fit()) return;
    const obs = new ResizeObserver(() => {
      if (fit()) obs.disconnect();
    });
    obs.observe(map.getContainer());
    return () => obs.disconnect();
  }, [map, assets]);
  return null;
}

interface PlaceHit {
  name: string;
  lat: number;
  lon: number;
}

/** Geocode a place name via OSM Nominatim (same provider as the base tiles). */
async function searchPlaces(query: string): Promise<PlaceHit[]> {
  const qs = new URLSearchParams({
    format: "jsonv2",
    limit: "5",
    "accept-language": "ko,en",
    q: query,
  });
  const resp = await fetch(`https://nominatim.openstreetmap.org/search?${qs.toString()}`, {
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) throw new Error(`place search failed (${resp.status})`);
  const rows = (await resp.json()) as { display_name: string; lat: string; lon: string }[];
  return rows.map((r) => ({ name: r.display_name, lat: Number(r.lat), lon: Number(r.lon) }));
}

export function MapView({
  model,
  libraries,
  patchModel,
  litpopRun,
  litpopBusy,
  litpopErr,
  onRunLitpop,
}: {
  model: Portfolio;
  libraries: Libraries;
  patchModel: (patch: Partial<Portfolio>) => void;
  litpopRun: Run | null;
  litpopBusy: boolean;
  litpopErr: string | null;
  onRunLitpop: (country: string, source: string, peril: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState<"point" | "polygon" | "line">("point");
  const [draft, setDraft] = useState<[number, number][]>([]); // [lat, lon] vertices in progress
  const [litpopCountry, setLitpopCountry] = useState("KOR");

  // Place search (Nominatim) — pans the map to the picked result.
  const [searchQ, setSearchQ] = useState("");
  const [searchHits, setSearchHits] = useState<PlaceHit[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [flyTarget, setFlyTarget] = useState<[number, number, number] | null>(null);

  async function doSearch() {
    const q = searchQ.trim();
    if (!q || searchBusy) return;
    setSearchBusy(true);
    setSearchErr(null);
    setSearchHits([]);
    try {
      const hits = await searchPlaces(q);
      setSearchHits(hits);
      if (hits.length === 0) setSearchErr("No results.");
    } catch (e) {
      setSearchErr(String(e));
    } finally {
      setSearchBusy(false);
    }
  }
  const [litpopSource, setLitpopSource] = useState("litpop");
  const [litpopPeril, setLitpopPeril] = useState("tropical_cyclone");

  // Hazard-layer preview (raster overlay of the raw hazard intensity, pre-calculation).
  const [catalog, setCatalog] = useState<HazardCatalog | null>(null);
  const [layerKey, setLayerKey] = useState("");
  const [layer, setLayer] = useState<{ data: HazardPreviewResult; url: string } | null>(null);
  const [layerBusy, setLayerBusy] = useState(false);
  const [layerErr, setLayerErr] = useState<string | null>(null);
  const [opacity, setOpacity] = useState(0.75);
  const [cropToAssets, setCropToAssets] = useState(true);
  useEffect(() => {
    getHazardCatalog()
      .then(setCatalog)
      .catch(() => setCatalog(null));
  }, []);

  const entryKey = (e: HazardCatalogEntry) =>
    `${e.peril}|${e.climate_scenario}|${e.region}|${e.year ?? ""}`;

  /** Padded bbox [south, west, north, east] around all assets (points + footprint vertices). */
  function assetsBbox(pad = 0.5): [number, number, number, number] | null {
    const lats: number[] = [];
    const lons: number[] = [];
    for (const a of model.assets) {
      lats.push(a.lat);
      lons.push(a.lon);
      const g = geomPositions(a.geometry);
      if (g)
        for (const [la, lo] of g.pos) {
          lats.push(la);
          lons.push(lo);
        }
    }
    if (lats.length === 0) return null;
    return [
      Math.min(...lats) - pad,
      Math.min(...lons) - pad,
      Math.max(...lats) + pad,
      Math.max(...lons) + pad,
    ];
  }

  async function showLayer(key: string, crop: boolean = cropToAssets) {
    setLayerKey(key);
    setLayer(null);
    setLayerErr(null);
    if (!key || key === EXPOSURE_LAYER) return; // exposure layer is client-side (asset values)
    const e = catalog?.entries.find((x) => entryKey(x) === key);
    if (!e) return;
    setLayerBusy(true);
    try {
      const bbox = crop ? assetsBbox() : null;
      let r = await submitHazardPreview(
        model.id,
        e.peril,
        e.climate_scenario,
        e.region,
        e.year,
        bbox ?? undefined,
      );
      for (let i = 0; i < 120 && (r.status === "queued" || r.status === "running"); i++) {
        await sleep(1500);
        r = await getRun(model.id, r.id);
      }
      const out = r.output as HazardPreviewResult | null;
      if (r.status === "done" && out?.status === "ok") {
        setLayer({ data: out, url: hazardPreviewImageUrl(model.id, r.id) });
      } else {
        setLayerErr(out?.detail ?? r.detail ?? "Preview failed.");
      }
    } catch (err) {
      setLayerErr(String(err));
    } finally {
      setLayerBusy(false);
    }
  }
  const selected = model.assets.find((a) => a.id === selectedId) ?? null;
  const litpop = litpopRun?.status === "done" ? (litpopRun.output as LitPopResult | null) : null;
  const litpopRunning = litpopRun?.status === "queued" || litpopRun?.status === "running";

  // Modeled-exposure result points: the top cells of a country-scale run, colored by their
  // computed impact. The worker already caps this list, so it is safe to draw directly.
  const litpopPoints = litpop?.per_point ?? [];
  const lpMax = litpopPoints.length ? Math.max(...litpopPoints.map((p) => p.eai)) : 0;
  const lpFrac = (v: number) => (lpMax > 0 ? v / lpMax : 0);
  const litpopIsMortality = litpop?.peril === "heat_mortality";

  // Exposure-value layer: color asset markers by value (client-side; no run needed).
  const exposureLayer = layerKey === EXPOSURE_LAYER;
  const vals = model.assets.map((a) => a.value).filter((v) => v > 0);
  const vMin = vals.length ? Math.min(...vals) : 0;
  const vMax = vals.length ? Math.max(...vals) : 0;
  const valueFrac = (v: number) => (vMax > vMin ? (v - vMin) / (vMax - vMin) : 0.5);

  const addAsset = (
    lat: number,
    lon: number,
    geometry?: Record<string, unknown>,
    scale: Asset["geographic_scale"] = "point",
  ) => {
    const asset = { ...newAsset(lat, lon), geographic_scale: scale, geometry: geometry ?? null };
    patchModel({ assets: [...model.assets, asset] });
    setSelectedId(asset.id);
  };

  // Draw directly on the map: in polygon/line mode, clicks accumulate vertices; Finish
  // builds the geometry asset. (Point mode = the original click-to-place.)
  const onMapClick = (lat: number, lon: number) => {
    if (drawMode === "point") addAsset(lat, lon);
    else setDraft((d) => [...d, [Number(lat.toFixed(5)), Number(lon.toFixed(5))]]);
  };
  const cancelDraw = () => setDraft([]);
  const finishDraw = () => {
    const need = drawMode === "polygon" ? 3 : 2;
    if (draft.length < need) return;
    const ll = draft.map(([la, lo]) => [lo, la]); // GeoJSON is [lon, lat]
    const geometry =
      drawMode === "polygon"
        ? { type: "Polygon", coordinates: [[...ll, ll[0]]] }
        : { type: "LineString", coordinates: ll };
    const cLat = draft.reduce((s, [la]) => s + la, 0) / draft.length;
    const cLon = draft.reduce((s, [, lo]) => s + lo, 0) / draft.length;
    addAsset(cLat, cLon, geometry, "footprint");
    setDraft([]);
    setDrawMode("point");
  };
  const updateAsset = (id: string, patch: Partial<Asset>) => {
    patchModel({ assets: model.assets.map((a) => (a.id === id ? { ...a, ...patch } : a)) });
  };
  const deleteAsset = (id: string) => {
    patchModel({ assets: model.assets.filter((a) => a.id !== id) });
    if (selectedId === id) setSelectedId(null);
  };

  return (
    <div className="mapview">
      <div className="map">
        <MapContainer center={[36.2, 127.9]} zoom={7} scrollWheelZoom>
          {/* The literal center above is only the cold-start view (no assets yet). */}
          <FitToAssets assets={model.assets} />
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <ClickToAdd onAdd={onMapClick} />
          <FlyTo target={flyTarget} />
          {draft.length > 0 &&
            (drawMode === "polygon" ? (
              <Polygon positions={draft} pathOptions={{ color: "#3aa0ff", dashArray: "5" }} />
            ) : (
              <Polyline positions={draft} pathOptions={{ color: "#3aa0ff", dashArray: "5" }} />
            ))}
          {draft.map((v, i) => (
            <CircleMarker key={`draft-${i}`} center={v} radius={4} pathOptions={{ color: "#3aa0ff", fillColor: "#3aa0ff", fillOpacity: 1 }} />
          ))}
          {layer && (
            <ImageOverlay
              key={layer.url}
              url={layer.url}
              bounds={layer.data.bounds}
              opacity={opacity}
              zIndex={400}
            />
          )}
          {model.assets.map((a) => {
            const g = geomPositions(a.geometry);
            if (!g) return null;
            const opts = { color: "#2f9e8f", weight: 2, fillOpacity: 0.15 };
            return g.kind === "polygon" ? (
              <Polygon key={`g-${a.id}`} positions={g.pos} pathOptions={opts} />
            ) : (
              <Polyline key={`g-${a.id}`} positions={g.pos} pathOptions={{ ...opts, weight: 3 }} />
            );
          })}
          {/* Country-scale modeled-exposure result: top cells by computed impact. Drawn
              before the asset markers so hand-placed facilities stay on top. */}
          {litpopPoints.map((pt, i) => (
            <CircleMarker
              key={`lp-${i}`}
              center={[pt.lat, pt.lon]}
              radius={3 + 5 * lpFrac(pt.eai)}
              pathOptions={{
                color: turboAt(lpFrac(pt.eai)),
                fillColor: turboAt(lpFrac(pt.eai)),
                fillOpacity: 0.8,
                weight: 0.5,
              }}
            >
              <Tooltip>
                {litpopIsMortality
                  ? `${pt.eai.toFixed(2)} deaths/yr`
                  : `${money(pt.eai, litpop?.currency ?? "USD")}/yr`}
                {` · ${(litpop?.peril ?? "").replace(/_/g, " ")}`}
              </Tooltip>
            </CircleMarker>
          ))}
          {model.assets.map((a) => (
            <CircleMarker
              key={a.id}
              center={[a.lat, a.lon]}
              radius={a.id === selectedId ? 11 : 8}
              pathOptions={{
                color:
                  a.id === selectedId
                    ? "#3aa0ff"
                    : exposureLayer && a.value > 0
                      ? turboAt(valueFrac(a.value))
                      : "#2f9e8f",
                fillColor:
                  a.id === selectedId
                    ? "#3aa0ff"
                    : exposureLayer && a.value > 0
                      ? turboAt(valueFrac(a.value))
                      : "#2f9e8f",
                fillOpacity: exposureLayer ? 0.85 : 0.7,
                weight: 2,
              }}
              eventHandlers={{ click: () => setSelectedId(a.id) }}
            >
              <Tooltip>
                {a.name} · {a.sector}
                {exposureLayer ? ` · ${money(a.value, a.currency)}` : ""}
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>

      <aside className="sidepanel">
        {selected ? (
          <AssetEditor
            asset={selected}
            libraries={libraries}
            onChange={(patch) => updateAsset(selected.id, patch)}
            onDelete={() => deleteAsset(selected.id)}
            onClose={() => setSelectedId(null)}
          />
        ) : (
          <>
            <div style={{ marginBottom: 4 }}>
              <div className="section-title">Search location</div>
              <div className="form-row" style={{ marginTop: 6 }}>
                <input
                  className="field-inline"
                  style={{ flex: 1, minWidth: 0 }}
                  value={searchQ}
                  placeholder="Region, address, or place…"
                  onChange={(e) => setSearchQ(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") doSearch();
                  }}
                />
                <button className="btn" onClick={doSearch} disabled={searchBusy || !searchQ.trim()}>
                  {searchBusy ? <span className="spinner" /> : "Search"}
                </button>
              </div>
              {searchErr && (
                <p className="hint" style={{ color: "var(--danger)", marginTop: 4 }}>
                  {searchErr}
                </p>
              )}
              {searchHits.length > 0 && (
                <div className="assetlist" style={{ marginTop: 6 }}>
                  {searchHits.map((h, i) => (
                    <div
                      key={`${h.lat},${h.lon},${i}`}
                      className="row"
                      onClick={() => {
                        setFlyTarget([h.lat, h.lon, 11]);
                        setSearchHits([]);
                      }}
                    >
                      <div>
                        <div className="nm">{h.name}</div>
                        <div className="sub">
                          {h.lat.toFixed(4)}, {h.lon.toFixed(4)}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div style={{ marginBottom: 4 }}>
              <div className="section-title">Draw on map</div>
              <div className="form-row" style={{ marginTop: 6 }}>
                {(["point", "polygon", "line"] as const).map((m) => (
                  <button
                    key={m}
                    className={`btn ${drawMode === m ? "" : "secondary"}`}
                    style={{ padding: "5px 10px", textTransform: "capitalize" }}
                    onClick={() => {
                      setDraft([]);
                      setDrawMode(m);
                    }}
                  >
                    {m === "point" ? "📍 Point" : m === "polygon" ? "▢ Polygon" : "／ Line"}
                  </button>
                ))}
              </div>
              {drawMode === "point" ? (
                <p className="hint" style={{ marginTop: 6 }}>
                  Click anywhere on the map to place a facility.
                </p>
              ) : (
                <div className="control-group" style={{ marginTop: 6 }}>
                  <span className="hint" style={{ flexBasis: "100%" }}>
                    Click to add {drawMode} vertices ({draft.length} so far), then Finish — it
                    becomes a footprint asset (value split across it).
                  </span>
                  <button
                    className="btn"
                    onClick={finishDraw}
                    disabled={draft.length < (drawMode === "polygon" ? 3 : 2)}
                  >
                    Finish {drawMode}
                  </button>
                  <button className="btn secondary" onClick={cancelDraw} disabled={!draft.length}>
                    Cancel
                  </button>
                </div>
              )}
            </div>
            {((catalog?.entries.length ?? 0) > 0 || model.assets.length > 0) && (
              <div style={{ marginBottom: 4 }}>
                <div className="section-title">Map layer (preview)</div>
                <p className="hint">
                  Color the map by a raw value — before any run — so you can see what's there:
                  a peril's hazard footprint, or your exposure by value.
                </p>
                <div className="form-row" style={{ marginTop: 6 }}>
                  <select
                    className="field-inline"
                    value={layerKey}
                    onChange={(e) => showLayer(e.target.value)}
                    disabled={layerBusy}
                    style={{ flex: 1, minWidth: 0 }}
                  >
                    <option value="">No layer</option>
                    {model.assets.length > 0 && (
                      <option value={EXPOSURE_LAYER}>Exposure value (your assets)</option>
                    )}
                    {(catalog?.entries ?? []).map((e) => (
                      <option key={entryKey(e)} value={entryKey(e)}>
                        {e.peril.replace(/_/g, " ")} · {e.region} ·{" "}
                        {formatScenario(e.climate_scenario)}
                        {e.year ? ` ${e.year}` : ""}
                      </option>
                    ))}
                  </select>
                  {layerBusy && <span className="spinner" />}
                </div>
                {model.assets.length > 0 && (
                  <label className="checkrow" style={{ marginTop: 6 }}>
                    <input
                      type="checkbox"
                      checked={cropToAssets}
                      onChange={(e) => {
                        setCropToAssets(e.target.checked);
                        if (layerKey && layerKey !== EXPOSURE_LAYER)
                          showLayer(layerKey, e.target.checked);
                      }}
                    />
                    <span className="hint">
                      Only around my assets (faster; color scale rescales to the local area)
                    </span>
                  </label>
                )}
                {layerErr && (
                  <div className="status-box error" style={{ marginTop: 6 }}>
                    {layerErr}
                  </div>
                )}
                {layer && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ height: 12, borderRadius: 3, background: TURBO_CSS }} />
                    <div
                      className="hint"
                      style={{ display: "flex", justifyContent: "space-between", marginTop: 2 }}
                    >
                      <span>{layer.data.vmin}</span>
                      <span>
                        {layer.data.peril.replace(/_/g, " ")} ({layer.data.unit})
                      </span>
                      <span>{layer.data.vmax}</span>
                    </div>
                    <label className="hint" style={{ display: "block", marginTop: 6 }}>
                      Overlay opacity
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={opacity}
                        onChange={(e) => setOpacity(Number(e.target.value))}
                        style={{ width: "100%" }}
                      />
                    </label>
                  </div>
                )}
                {exposureLayer && vals.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ height: 12, borderRadius: 3, background: TURBO_CSS }} />
                    <div
                      className="hint"
                      style={{ display: "flex", justifyContent: "space-between", marginTop: 2 }}
                    >
                      <span>{money(vMin, model.assets[0]?.currency ?? "USD")}</span>
                      <span>asset value</span>
                      <span>{money(vMax, model.assets[0]?.currency ?? "USD")}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="section-title">Facilities</div>
            <p className="hint">
              Click anywhere on the map to place a facility. Select one to edit its sector,
              scale, value, and emissions.
            </p>
            {model.assets.length === 0 ? (
              <div className="empty-state" style={{ padding: "24px 12px" }}>
                <div className="empty-icon">📍</div>
                <div className="empty-title">No facilities yet</div>
                <div className="empty-hint">
                  Click anywhere on the map to place your first facility — or model a whole
                  country's exposure below.
                </div>
              </div>
            ) : (
              <div className="assetlist">
                {model.assets.map((a) => (
                  <div key={a.id} className="row" onClick={() => setSelectedId(a.id)}>
                    <div>
                      <div className="nm">{a.name}</div>
                      <div className="sub">
                        {a.sector} · {a.lat.toFixed(2)}, {a.lon.toFixed(2)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="section-divider">
              <div className="section-title">Modeled exposure</div>
              <p className="hint">
                Model a whole country's exposure instead of hand-entering assets, then run any
                peril on the grid. <strong>Population raster (WorldPop)</strong> and{" "}
                <strong>reference-city population</strong> need no login and are the right exposure
                for health perils such as heat mortality. LitPop, BlackMarble, GDP2Asset and crop
                need login-gated or large downloads and will tell you exactly what to fetch.
              </p>
              <div className="form-row" style={{ marginTop: 8 }}>
                <select
                  className="field-inline"
                  value={litpopSource}
                  onChange={(e) => setLitpopSource(e.target.value)}
                  title="Modeled-exposure data source"
                >
                  <option value="litpop">LitPop (nightlight × pop)</option>
                  <option value="blackmarble">BlackMarble (nightlights)</option>
                  <option value="gdp">GDP2Asset (gridded GDP)</option>
                  <option value="crop">Crop production (ISIMIP/SPAM)</option>
                  <option value="osm">OSM buildings (osm-flex)</option>
                  <option value="raster">Population raster (WorldPop/GHSL) — no login</option>
                  <option value="population_ref">Reference-city population — no data needed</option>
                </select>
                <select
                  className="field-inline"
                  value={litpopPeril}
                  onChange={(e) => setLitpopPeril(e.target.value)}
                  title="Peril to run on the modeled grid"
                >
                  {libraries.perils.perils
                    .filter((p) => p.supported_mvp)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                </select>
                <input
                  className="field-inline"
                  value={litpopCountry}
                  onChange={(e) => setLitpopCountry(e.target.value.toUpperCase())}
                  maxLength={3}
                  placeholder="ISO3"
                  style={{ width: 70 }}
                />
                <button
                  className="btn"
                  onClick={() => onRunLitpop(litpopCountry, litpopSource, litpopPeril)}
                  disabled={litpopBusy || litpopRunning || litpopCountry.length !== 3}
                  title={litpopCountry.length !== 3 ? "Enter a 3-letter ISO country code" : ""}
                >
                  {litpopRunning ? (
                    <>
                      <span className="spinner" /> Modeling…
                    </>
                  ) : (
                    "Model exposure"
                  )}
                </button>
              </div>
              {litpopErr && <div className="status-box error" style={{ marginTop: 8 }}>{litpopErr}</div>}
              {litpopRun?.status === "error" && (
                <div className="status-box error" style={{ marginTop: 8 }}>{litpopRun.detail}</div>
              )}
              {litpop && litpop.status === "error" && (
                <div className="status-box error" style={{ marginTop: 8 }}>{litpop.detail}</div>
              )}
              {litpop && litpop.status === "ok" && (
                <>
                  <p className="hint" style={{ marginTop: 8 }}>
                    {litpop.source_label ?? "LitPop"} · {litpop.country}:{" "}
                    {litpop.n_points.toLocaleString()} cells · exposed{" "}
                    {money(litpop.total_value, litpop.currency)} · AAI{" "}
                    {money(litpop.aai_agg, litpop.currency)}/yr ({litpop.peril.replace(/_/g, " ")}
                    {litpop.future_year ? ` ${litpop.future_year}` : ""})
                  </p>
                  {!litpop.aai_agg && litpop.interpretation && (
                    <div className="status-box info" style={{ marginTop: 6 }}>
                      {litpop.interpretation}
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
