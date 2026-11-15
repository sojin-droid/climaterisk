// Pure geometry helpers for the sheet drawings. Nothing here touches React.
import type { RpKey } from "./types";

export const INK = "#0E1113";
export const INK2 = "#2A2F33";
export const MUTED = "#5A6066";
export const RULE = "#C4C8CB";
export const DEEP = "#08306b";
export const RAMP = ["#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c"];
export const RAMP_LABELS = ["≤0.5", "0.5–1", "1–2", "2–5", "5 m 이상"];
export const CERTAIN = "#6baed6";
export const RANGE = "#c6dbef";
export const RP_KEYS: RpKey[] = ["rp100", "rp200", "rp500"];

const CODE_BANDS: Record<string, [number, number | null]> = {
  N330: [0, 0.5],
  N331: [0.5, 1],
  N332: [1, 2],
  N333: [2, 5],
  N334: [5, null],
};

/** Official depth class code -> [lower, upper] metres (upper null = open); null when absent. */
export function classBand(code: string | undefined | null): [number, number | null] | null {
  if (!code) return null;
  return CODE_BANDS[code] ?? null;
}

export function classIndex(code: string | undefined | null): number {
  return code ? Object.keys(CODE_BANDS).indexOf(code) : -1;
}

export function classColor(code: string | undefined | null): string {
  const i = classIndex(code);
  return i < 0 ? "none" : RAMP[i];
}

const fmt = (n: number) => String(n);

/** "a–b m" for a closed band, "a m 이상" for an open one. */
export function formatBand(band: [number, number | null] | null | undefined): string | null {
  if (!band) return null;
  const [lo, hi] = band;
  return hi == null ? `${fmt(lo)} m 이상` : `${fmt(lo)}–${fmt(hi)} m`;
}

export interface Scale {
  (v: number): number;
}

export function linear(d0: number, d1: number, r0: number, r1: number): Scale {
  const k = (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}

/** Contiguous runs of non-null values: [startIndex, endIndex] inclusive. */
export function runs(values: (number | null)[]): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  values.forEach((v, i) => {
    if (v == null) {
      if (start >= 0) out.push([start, i - 1]);
      start = -1;
    } else if (start < 0) start = i;
  });
  if (start >= 0) out.push([start, values.length - 1]);
  return out;
}

/** Polyline path that breaks at nulls (a gap is a gap, never zero). */
export function linePath(
  xs: number[],
  ys: (number | null)[],
  sx: Scale,
  sy: Scale,
  clipTop?: number,
): string {
  const d: string[] = [];
  for (const [a, b] of runs(ys)) {
    for (let i = a; i <= b; i++) {
      let y = ys[i] as number;
      if (clipTop != null) y = Math.min(y, clipTop);
      d.push(`${i === a ? "M" : "L"}${sx(xs[i]).toFixed(1)} ${sy(y).toFixed(1)}`);
    }
  }
  return d.join("");
}

/** Closed fill polygons (one per non-null run) down to `base` (data units). */
export function areaPath(
  xs: number[],
  ys: (number | null)[],
  sx: Scale,
  sy: Scale,
  base: number,
  clipTop?: number,
): string {
  const d: string[] = [];
  for (const [a, b] of runs(ys)) {
    const pts: string[] = [];
    for (let i = a; i <= b; i++) {
      let y = ys[i] as number;
      if (clipTop != null) y = Math.min(y, clipTop);
      pts.push(`${sx(xs[i]).toFixed(1)} ${sy(y).toFixed(1)}`);
    }
    d.push(`M${sx(xs[a]).toFixed(1)} ${sy(base).toFixed(1)}L${pts.join("L")}L${sx(xs[b]).toFixed(1)} ${sy(base).toFixed(1)}Z`);
  }
  return d.join("");
}

/** Cell edges around each sample (midpoints; the first/last extend half a step). */
export function cellEdges(km: number[]): [number, number][] {
  const n = km.length;
  return km.map((k, i) => [
    i === 0 ? k : (km[i - 1] + k) / 2,
    i === n - 1 ? k + (n > 1 ? (k - km[i - 1]) / 2 : 0) : (k + km[i + 1]) / 2,
  ]);
}

/** Round tick values covering [lo, hi] at `step`. */
export function ticks(lo: number, hi: number, step: number): number[] {
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

export function quantile(values: number[], q: number): number {
  const s = [...values].sort((a, b) => a - b);
  if (s.length === 0) return 0;
  return s[Math.min(s.length - 1, Math.floor(q * (s.length - 1)))];
}

/** Auto-fit height range for the section: clip tall terrain at the top, keep site bands visible. */
export function fitHeightRange(
  ground: (number | null)[],
  surface: (number | null)[],
  siteGround: number | null,
  siteUpper: number,
): { lo: number; hi: number; clippedAbove: number | null } {
  const g = ground.filter((v): v is number => v != null);
  const all = g.length ? g : [0];
  const lo = Math.floor(Math.min(...all) / 4) * 4;
  const wanted = Math.max(
    Math.ceil((quantile(all, 0.97) + 1) / 4) * 4,
    siteGround != null ? Math.ceil((siteGround + siteUpper + 3) / 4) * 4 : 0,
    lo + 8,
  );
  const s = surface.filter((v): v is number => v != null);
  const peak = Math.max(...all, ...(s.length ? s : [0]));
  return { lo, hi: wanted, clippedAbove: peak > wanted ? Math.round(peak) : null };
}
