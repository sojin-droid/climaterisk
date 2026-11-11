import type { Asset } from "../types";

/** Default sector of a new asset (shared by the Map tab and the visual board). */
export const DEFAULT_SECTOR = "real_estate";

/** A new point asset at (lat, lon), rounded to 5 decimals; value 0 until the user sets it. */
export function newAsset(lat: number, lon: number): Asset {
  return {
    id: crypto.randomUUID(),
    name: "New facility",
    lat: Number(lat.toFixed(5)),
    lon: Number(lon.toFixed(5)),
    sector: DEFAULT_SECTOR,
    geographic_scale: "point",
    value: 0,
    currency: "USD",
    annual_emissions_tco2e: null,
    vulnerability_class: null, // follow sector default
    properties: {},
  };
}
