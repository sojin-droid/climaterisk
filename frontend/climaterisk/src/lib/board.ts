// Visual board (docs/VISUAL_BOARD_KO.md): REST client + colour classes for the national
// hazard pieces. Pieces keep their hazard-grid-cell value; nothing is averaged here.

export interface BoardMeta {
  layer: string;
  units: string;
  requested_periods?: number[];
  periods_kept: number[];
  max_supported: number | null;
  record_years: number | null;
  resolution_arcsec: number;
  cell_km_ns: number;
  cell_km_ew: number;
  hazard_source: string;
  boundary_source: string;
  use_tier: string;
  use_tier_reason: string;
}

export type BoardValues = Record<string, number | null>;

export interface BoardLookup {
  id: number;
  code: string;
  name: string;
  centroid: number;
  values: BoardValues;
  cell_km_ns: number;
  cell_km_ew: number;
}

export interface BoardRegion {
  code: string;
  name: string;
  n_pieces: number;
  by_rp: Record<string, { min: number; max: number } | null>;
}

export interface BoardFeature {
  type: "Feature";
  geometry: { type: "Polygon" | "MultiPolygon"; coordinates: unknown };
  properties: { id: number; centroid: number; code: string; name: string; value: number | null };
}

export interface BoardCollection {
  type: "FeatureCollection";
  features: BoardFeature[];
}

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`GET ${url} → ${resp.status}`);
  return (await resp.json()) as T;
}

export const getBoardLayers = () => getJson<BoardMeta[]>("/api/board/layers");

export const getBoardLookup = (layer: string, lon: number, lat: number) =>
  getJson<BoardLookup>(`/api/board/layers/${encodeURIComponent(layer)}/lookup?lon=${lon}&lat=${lat}`);

export const getBoardRegion = (layer: string, code: string) =>
  getJson<BoardRegion>(
    `/api/board/layers/${encodeURIComponent(layer)}/regions/${encodeURIComponent(code)}`,
  );

// The GeoJSON is tens of MB: fetch each (layer, rp) once; a failed fetch is forgotten so a
// retry is possible.
const geojsonCache = new Map<string, Promise<BoardCollection>>();

export function getBoardGeojson(layer: string, rp: number): Promise<BoardCollection> {
  const key = `${layer}|${rp}`;
  let hit = geojsonCache.get(key);
  if (!hit) {
    hit = getJson<BoardCollection>(
      `/api/board/layers/${encodeURIComponent(layer)}/geojson?rp=${rp}`,
    );
    hit.catch(() => geojsonCache.delete(key));
    geojsonCache.set(key, hit);
  }
  return hit;
}

/** Key of a return period in ``values`` — the same ``%g`` form the backend writes ("100"). */
export const rpKey = (rp: number): string => String(rp);

// Sequential single-hue (blue) classes; 0 and null each keep their own colour.
export const NULL_COLOR = "#8a8f98";
export const ZERO_COLOR = "#f7fbff";
export interface ValueClass {
  /** Upper bound (inclusive) in hazard units; Infinity for the last class. */
  upTo: number;
  color: string;
  label: string;
}
export const POSITIVE_CLASSES: ValueClass[] = [
  { upTo: 0.25, color: "#c6dbef", label: "> 0 – 0.25" },
  { upTo: 0.5, color: "#9ecae1", label: "0.25 – 0.5" },
  { upTo: 1, color: "#6baed6", label: "0.5 – 1" },
  { upTo: 2, color: "#3182bd", label: "1 – 2" },
  { upTo: 4, color: "#08519c", label: "2 – 4" },
  { upTo: Infinity, color: "#08306b", label: "> 4" },
];

export function valueColor(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NULL_COLOR;
  if (value === 0) return ZERO_COLOR;
  return (POSITIVE_CLASSES.find((c) => value <= c.upTo) ?? POSITIVE_CLASSES[POSITIVE_CLASSES.length - 1])
    .color;
}
