// Offline (closed-network) mode, read once from /api/health and handed down as context.
// Maps drop their basemap tiles, place search is disabled and the Data tab explains what
// is refused — the backend enforces the rest (CLIMATERISK_OFFLINE).
import { createContext, useContext } from "react";

export const OfflineContext = createContext<boolean>(false);

export function useOffline(): boolean {
  return useContext(OfflineContext);
}

/** Caption shown where a basemap would be. */
export const OFFLINE_MAP_NOTE = "Offline mode: no basemap tiles (OpenStreetMap tiles are online-only)";
