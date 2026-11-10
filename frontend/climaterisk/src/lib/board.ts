// Visual board (docs/VISUAL_BOARD_KO.md): REST client + colour classes for the national
// hazard pieces. Pieces keep their hazard-grid-cell value; nothing is averaged here.

export type BoardScale = "share" | "spei";

export interface BoardKey {
  key: string;
  label: string;
  units: string;
  scale: BoardScale;
}

export interface BoardMeta {
  layer: string;
  label?: string;
  /** "metric" = chronic layer with named keys (drought); default return-period layer. */
  key_kind?: "return_period" | "metric";
  keys?: BoardKey[];
  notes?: string[];
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
  exposure?: BoardExposure | null;
  key_kind?: "return_period" | "metric";
}

export type BoardExposure = Record<string, number>;

export interface BoardRegion {
  code: string;
  name: string;
  n_pieces: number;
  by_rp: Record<string, { min: number; max: number } | null>;
  by_key?: Record<string, { min: number; max: number } | null>;
  exposure_sum?: BoardExposure;
}

export interface BoardFeature {
  type: "Feature";
  geometry: { type: "Polygon" | "MultiPolygon"; coordinates: unknown };
  properties: {
    id: number;
    centroid: number;
    code: string;
    name: string;
    value: number | null;
    exposure?: BoardExposure | null;
  };
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

export function getBoardGeojson(
  layer: string,
  key: string | number,
  metric = false,
): Promise<BoardCollection> {
  const cacheKey = `${layer}|${key}`;
  let hit = geojsonCache.get(cacheKey);
  if (!hit) {
    const query = metric ? `key=${encodeURIComponent(String(key))}` : `rp=${key}`;
    hit = getJson<BoardCollection>(`/api/board/layers/${encodeURIComponent(layer)}/geojson?${query}`);
    hit.catch(() => geojsonCache.delete(cacheKey));
    geojsonCache.set(cacheKey, hit);
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

// ---- chronic metric layers (drought): share and SPEI palettes ---------------------------
// Share of years (0..1): single-hue brown/orange; 0 is its own lightest class, null grey.
export const SHARE_ZERO_COLOR = "#fff7ec";
export const SHARE_CLASSES: ValueClass[] = [
  { upTo: 0.1, color: "#fdd49e", label: "> 0 – 0.1" },
  { upTo: 0.2, color: "#fdbb84", label: "0.1 – 0.2" },
  { upTo: 0.3, color: "#fc8d59", label: "0.2 – 0.3" },
  { upTo: 0.4, color: "#d94801", label: "0.3 – 0.4" },
  { upTo: Infinity, color: "#7f2704", label: "> 0.4" },
];

export function shareColor(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NULL_COLOR;
  if (value === 0) return SHARE_ZERO_COLOR;
  return (SHARE_CLASSES.find((c) => value <= c.upTo) ?? SHARE_CLASSES[SHARE_CLASSES.length - 1]).color;
}

// SPEI-3 stage classes (docs/DROUGHT_AGRI_KO.md): drier = darker brown.
export interface SpeiStage {
  /** Stage applies when SPEI ≤ `leq` (checked from the driest class down). */
  leq: number;
  color: string;
  name: string;
  label: string;
}
export const SPEI_STAGES: SpeiStage[] = [
  { leq: -2, color: "#7f2704", name: "경계 이상", label: "경계 이상: SPEI-3 ≤ −2" },
  { leq: -1.5, color: "#d94801", name: "주의", label: "주의: −2 < SPEI-3 ≤ −1.5" },
  { leq: -1, color: "#fdbb84", name: "관심", label: "관심: −1.5 < SPEI-3 ≤ −1" },
  { leq: Infinity, color: "#fff7ec", name: "없음", label: "없음: SPEI-3 > −1" },
];

export function speiStage(value: number): SpeiStage {
  return SPEI_STAGES.find((s) => value <= s.leq) ?? SPEI_STAGES[SPEI_STAGES.length - 1];
}

export function speiColor(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NULL_COLOR;
  return speiStage(value).color;
}

export function scaleColor(scale: BoardScale | undefined, value: number | null | undefined): string {
  return scale === "share" ? shareColor(value) : scale === "spei" ? speiColor(value) : valueColor(value);
}
