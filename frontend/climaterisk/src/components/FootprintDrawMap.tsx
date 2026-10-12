import { useEffect, useRef } from "react";
import { useOffline } from "../lib/offline";
import maplibregl from "maplibre-gl";
import MapboxDraw from "@mapbox/mapbox-gl-draw";
import "maplibre-gl/dist/maplibre-gl.css";
import "@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css";

// Token-free raster basemap (no Mapbox account needed for a local-first tool).
// Offline mode: a blank style — the draw tools work, there is just no basemap.
const EMPTY_STYLE = { version: 8, sources: {}, layers: [] };

const OSM_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

/** MapLibre GL + mapbox-gl-draw polygon editor for an asset footprint. Drawing/editing a
 *  polygon emits its GeoJSON geometry (or null when cleared). Defensive: any init failure
 *  is swallowed so the surrounding asset editor (and its GeoJSON textarea) keeps working. */
export function FootprintDrawMap({
  lat,
  lon,
  geometry,
  onChange,
}: {
  lat: number;
  lon: number;
  geometry: Record<string, unknown> | null | undefined;
  onChange: (geom: Record<string, unknown> | null) => void;
}) {
  const offline = useOffline();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    let map: maplibregl.Map | undefined;
    try {
      map = new maplibregl.Map({
        container: ref.current,
        style: (offline ? EMPTY_STYLE : OSM_STYLE) as maplibregl.StyleSpecification,
        center: [lon || 0, lat || 0],
        zoom: lat || lon ? 11 : 1,
        attributionControl: false,
      });
      const draw = new MapboxDraw({
        displayControlsDefault: false,
        controls: { polygon: true, line_string: true, trash: true },
      });
      // mapbox-gl-draw is an IControl; MapLibre's addControl accepts it at runtime.
      map.addControl(draw as unknown as maplibregl.IControl);

      const emit = () => {
        const fc = draw.getAll();
        const f = fc.features[fc.features.length - 1];
        onChange(f ? (f.geometry as unknown as Record<string, unknown>) : null);
      };
      map.on("load", () => {
        if (geometry) {
          try {
            draw.add({ type: "Feature", properties: {}, geometry } as never);
          } catch {
            /* ignore malformed existing geometry */
          }
        }
      });
      map.on("draw.create", emit);
      map.on("draw.update", emit);
      map.on("draw.delete", () => onChange(null));
    } catch {
      /* MapLibre/draw unavailable — the GeoJSON textarea remains the fallback. */
    }
    return () => map?.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      ref={ref}
      style={{ height: 240, borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}
    />
  );
}
