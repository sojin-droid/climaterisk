// Visual board (docs/VISUAL_BOARD_KO.md): REST client + colour classes for the national
// hazard pieces. Pieces keep their hazard-grid-cell value; nothing is averaged here.

/** "share" 0..1 frequency, "spei" index value, "level" a physical quantity, "ratio" a future / base change. */
export type BoardScale = "share" | "spei" | "level" | "ratio";

export interface BoardKey {
  key: string;
  label: string;
  units: string;
  scale: BoardScale;
  /** Key of the palette spec's `quantities` that colours this key. */
  quantity: string;
  /** false: the values cannot be compared between regions (long-run standardised-index frequencies). */
  comparable_across_regions?: boolean;
}

export interface BoardMeta {
  layer: string;
  label?: string;
  peril?: string;
  /** Return-period layers: quantity colouring every key ("change" = future / base ratio). */
  quantity?: string;
  /** Change layers: the two layers the ratio is taken between. */
  base_layer?: string;
  future_layer?: string;
  /** Metric layers: key shown first. */
  default_key?: string;
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

/** Why a change ratio is undefined: "base_zero" (base 0, future > 0), "both_zero", "null" (no data). */
export type BoardStatus = "base_zero" | "both_zero" | "null";

export const isChangeLayer = (meta: BoardMeta | null | undefined): boolean => meta?.quantity === "change";

export type BoardValues = Record<string, number | null>;

export interface BoardLookup {
  id: number;
  code: string;
  name: string;
  centroid: number;
  values: BoardValues;
  /** Change layers: why a ratio is missing, per key (only non-empty ones are present). */
  status?: Record<string, BoardStatus>;
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
    status?: BoardStatus | null;
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

// ---- colours: one authority, assets/libraries/board_palettes.json (GET /api/board/palettes) ----
// No colour is written in this file. The rule below is the spec's `_meta.class_rule`:
//   null/NaN -> shared.null; 0 with zero_means_none -> shared.zero; value <= breaks[0] ->
//   class 0; breaks[i-1] < value <= breaks[i] -> class i; value > breaks[-1] -> last class;
//   class i uses family ramp[steps[i]]. spei_stage: first stage (driest first) with
//   value <= leq, else shared.zero. change: same break rule on the ratio with change.colors.

export interface PaletteQuantity {
  family: string;
  units: string;
  label: string;
  basis: string;
  status: string;
  breaks?: number[];
  steps?: number[];
  zero_means_none?: boolean;
  stages?: { leq: number; step: number; name: string; label: string }[];
  above_last?: { use: string; name: string; label: string };
}

export interface PaletteSpec {
  shared: {
    null: { color: string; label: string; meaning: string };
    zero: { color: string; label: string; meaning: string };
    base_zero: { color: string; outline: string; label: string; meaning: string };
    piece_outline: { color: string; weight: number };
    region_outline: { color: string; weight: number };
    selected_outline: { color: string; weight: number };
    fill_opacity: number;
  };
  families: Record<string, { label: string; ramp: string[] }>;
  perils: Record<string, string>;
  quantities: Record<string, PaletteQuantity>;
  change: { label: string; basis: string; breaks: number[]; colors: string[]; labels: string[] };
}

export interface PaletteClass {
  /** Upper bound (inclusive); null for the open last class. */
  upTo: number | null;
  color: string;
  label: string;
}

let palettePromise: Promise<PaletteSpec> | null = null;

/** The palette spec, fetched once; a failed fetch is forgotten so a retry is possible. */
export function getBoardPalettes(): Promise<PaletteSpec> {
  if (!palettePromise) {
    palettePromise = getJson<PaletteSpec>("/api/board/palettes");
    palettePromise.catch(() => {
      palettePromise = null;
    });
  }
  return palettePromise;
}

const isNull = (v: number | null | undefined): v is null | undefined =>
  v === null || v === undefined || Number.isNaN(v);

function classIndex(breaks: number[], value: number): number {
  const i = breaks.findIndex((b) => value <= b);
  return i === -1 ? breaks.length : i;
}

function quantityOf(spec: PaletteSpec, quantity: string): PaletteQuantity {
  const q = spec.quantities[quantity];
  if (!q) throw new Error(`unknown colour quantity ${quantity}`);
  return q;
}

/** `family` only for `loss_ratio`, whose own family is "peril" (the hazard's family). */
function rampOf(spec: PaletteSpec, q: PaletteQuantity, family?: string): string[] {
  const f = spec.families[family ?? q.family];
  if (!f) throw new Error(`unknown colour family ${family ?? q.family}`);
  return f.ramp;
}

const num = (x: number) => String(x);

/** Legend classes of a quantity in class order (spei_stage: driest first, then "없음"). */
export function paletteClasses(spec: PaletteSpec, quantity: string, family?: string): PaletteClass[] {
  if (quantity === "change") {
    const ch = spec.change;
    return ch.colors.map((color, i) => ({ upTo: ch.breaks[i] ?? null, color, label: ch.labels[i] }));
  }
  const q = quantityOf(spec, quantity);
  if (q.stages) {
    const ramp = rampOf(spec, q);
    const out: PaletteClass[] = q.stages.map((s) => ({ upTo: s.leq, color: ramp[s.step], label: s.label }));
    out.push({ upTo: null, color: spec.shared.zero.color, label: q.above_last?.label ?? "" });
    return out;
  }
  const ramp = rampOf(spec, q, family);
  const breaks = q.breaks ?? [];
  return (q.steps ?? []).map((step, i) => ({
    upTo: i < breaks.length ? breaks[i] : null,
    color: ramp[step],
    label: i === 0 ? `≤ ${num(breaks[0])}` : i < breaks.length ? `${num(breaks[i - 1])} – ${num(breaks[i])}` : `> ${num(breaks[breaks.length - 1])}`,
  }));
}

/** Hex colour of a value (the identical rule to `climaterisk.board.palette.color`). */
export function paletteColor(
  spec: PaletteSpec,
  quantity: string,
  value: number | null | undefined,
  family?: string,
): string {
  if (quantity === "change") {
    if (isNull(value)) return spec.shared.null.color;
    return spec.change.colors[classIndex(spec.change.breaks, value)];
  }
  const q = quantityOf(spec, quantity);
  if (isNull(value)) return spec.shared.null.color;
  if (q.stages) {
    const ramp = rampOf(spec, q);
    const hit = q.stages.find((s) => value <= s.leq);
    return hit ? ramp[hit.step] : spec.shared.zero.color;
  }
  if (value === 0 && q.zero_means_none) return spec.shared.zero.color;
  return rampOf(spec, q, family)[(q.steps ?? [])[classIndex(q.breaks ?? [], value)]];
}

/**
 * Fill of a change-layer piece: `base_zero` -> shared.base_zero (white, grey outline),
 * `both_zero` -> shared.zero, otherwise the ratio's class (null ratio -> shared.null).
 * `outline` is set only where the spec gives the piece its own outline colour.
 */
export function changePieceStyle(
  spec: PaletteSpec,
  value: number | null | undefined,
  status: BoardStatus | null | undefined,
): { fill: string; outline?: string } {
  if (status === "base_zero") return { fill: spec.shared.base_zero.color, outline: spec.shared.base_zero.outline };
  if (status === "both_zero") return { fill: spec.shared.zero.color };
  return { fill: paletteColor(spec, "change", value) };
}

/** "×1.40" for a ratio; the status text where there is none; "자료 없음" for null. */
export function fmtChange(
  spec: PaletteSpec | null,
  value: number | null | undefined,
  status: BoardStatus | null | undefined,
): string {
  if (value !== null && value !== undefined && !Number.isNaN(value)) return `×${value.toFixed(2)}`;
  if (status === "base_zero") return spec?.shared.base_zero.label ?? "기준 0 · 배율 없음";
  if (status === "both_zero") return spec?.shared.zero.label ?? "0 · 영향 없음";
  return "자료 없음";
}

/** Stage name of a SPEI value (spec `stages[].name`, `above_last.name`). */
export function speiStageName(spec: PaletteSpec, value: number): string {
  const q = quantityOf(spec, "spei_stage");
  return q.stages?.find((s) => value <= s.leq)?.name ?? q.above_last?.name ?? "";
}

/** Quantity colouring `key`: the key's own (metric layers) or `meta.quantity` (return period). */
export function keyQuantity(meta: BoardMeta, key: string | null): string | null {
  if (meta.key_kind === "metric") return meta.keys?.find((k) => k.key === key)?.quantity ?? null;
  return meta.quantity ?? null;
}

// ---- 시군구 panel, resolution ladder, case twins (docs/VISUAL_BOARD_KO.md §3) ----

export interface BoardRegionName {
  code: string;
  name: string;
}

/** One layer's row in the all-layer 시군구 panel: min–max of the headline key, no mean. */
export interface BoardRegionRow {
  layer: string;
  label: string;
  peril?: string | null;
  quantity?: string | null;
  headline_key: string;
  n_pieces: number;
  headline: { min: number; max: number } | null;
  /** Change layers: piece count per status of the headline key (ratio undefined, not "no data"). */
  headline_status?: Partial<Record<BoardStatus, number>>;
}

export interface BoardRegionAll {
  code: string;
  name: string;
  layers: BoardRegionRow[];
}

export type LadderId = "national" | "sigungu" | "point" | "case";

export interface LadderRung {
  id: LadderId;
  label_ko: string;
  label_en: string;
  note_ko: string;
}

export interface BoardCaseSummary {
  id: string;
  name: string;
  peril: string;
  lon: number;
  lat: number;
  cell_m: number;
  badge: string;
  available: boolean;
}

export interface BoardCaseHighlight {
  label_ko: string;
  unit: string;
  value: number | null;
}

export interface BoardCase extends BoardCaseSummary {
  source_doc: string;
  /** National layer of the same peril, for the "national cell vs case cell" line. */
  national_layer?: string;
  figures: { file: string; caption_ko: string }[];
  highlights: BoardCaseHighlight[];
  can_say: string[];
  cannot_say: string[];
  next_data: string[];
  assumptions: string[];
  n_assumptions: number;
  assumptions_complete: boolean;
  summary: { check?: { warning?: boolean; [k: string]: unknown }; [k: string]: unknown };
}

export const getBoardRegionNames = () => getJson<BoardRegionName[]>("/api/board/regions");
export const getBoardRegionAll = (code: string) =>
  getJson<BoardRegionAll>(`/api/board/regions/${encodeURIComponent(code)}`);
export const getBoardLadder = () => getJson<LadderRung[]>("/api/board/ladder");
export const getBoardCases = () => getJson<BoardCaseSummary[]>("/api/board/cases");
export const getBoardCase = (id: string) =>
  getJson<BoardCase>(`/api/board/cases/${encodeURIComponent(id)}`);
export const caseFigureUrl = (id: string, file: string) =>
  `/api/board/cases/${encodeURIComponent(id)}/figures/${encodeURIComponent(file)}`;

/** Headline label: return-period key -> "<n>년 빈도", metric key -> that key's label from the layer meta. */
export function headlineLabel(meta: BoardMeta | undefined, key: string): string {
  if (!meta) return key;
  if (meta.key_kind === "metric") return meta.keys?.find((k) => k.key === key)?.label ?? key;
  return `${key}년 빈도`;
}

/** Units text of a headline key (metric layers: the key's own units; else the layer's). */
export function headlineUnits(meta: BoardMeta | undefined, key: string): string {
  if (!meta) return "";
  if (meta.key_kind === "metric") return meta.keys?.find((k) => k.key === key)?.units ?? meta.units;
  return meta.units;
}

/** Number text of a case highlight: null -> "자료 없음" (never 0), else value with unit. */
export function fmtHighlight(value: number | null, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "자료 없음";
  const text = Number.isInteger(value) ? String(value) : value.toFixed(Math.abs(value) < 10 ? 2 : 1);
  return unit ? `${text} ${unit}` : text;
}

/** Status part of a 시군구-panel row: "0 · 영향 없음 30조각" etc. ("" when there is none). */
export function fmtStatusCounts(
  spec: PaletteSpec | null,
  counts: Partial<Record<BoardStatus, number>> | undefined,
): string {
  const order: BoardStatus[] = ["both_zero", "base_zero", "null"];
  return order
    .filter((st) => (counts?.[st] ?? 0) > 0)
    .map((st) => `${fmtChange(spec, null, st)} ${counts?.[st]}조각`)
    .join(" · ");
}
