// Mirrors docs/SHEET_SCHEMA.md (danmyeon.sheet/1). Unknown values are null, never 0.
export type RpKey = "rp100" | "rp200" | "rp500";

export interface SheetSummary {
  sheet_id: string;
  title: string;
  place: string;
  hazard: string;
  date: string;
}

export interface SheetStamp {
  state: string;
  label: string;
  agree?: number;
  of?: number;
}

export interface GlobalPeriod {
  rp: number[];
  depth_m: number[];
}

export interface Sheet {
  schema: string;
  sheet_id: string;
  hazard: string;
  title: string;
  place: string;
  use: string;
  date: string;
  site: {
    lat: number;
    lon: number;
    km: number;
    z_ground_m: number | null;
    z_surface_m: number | null;
    /** "" = outside every polygon (0 m); null = no map file for that return period (unknown). */
    classes: Record<RpKey, string | null>;
    /** Why a class is null: no map file for that period, or a file that cannot be read.
     * Absent on sheets built before 2026-11-16. */
    reasons?: Record<RpKey, "no_file_for_rp" | "map_unreadable" | null>;
    bands_m: Record<RpKey, [number, number | null] | null>;
    depth_m: Record<RpKey, number | null>;
    check_depth_m: Record<RpKey, number | null> | null;
    check_dem?: string | null;
  };
  section: {
    cut: { lon0: number; lon1: number; lat: number; bearing_deg: number; length_km: number };
    km: number[];
    z_ground_m: (number | null)[];
    z_surface_m: (number | null)[];
    ground_source?: string;
    surface_source?: string;
    classes: Record<RpKey, (string | null)[]>;
    vertical_exaggeration: number;
  };
  frequency: {
    return_periods: number[];
    official: { tier: string; classes: (string | null)[] };
    global: Record<string, unknown>;
  };
  stamps: { author: SheetStamp; review: SheetStamp; approve: SheetStamp };
  sources: string[];
}
