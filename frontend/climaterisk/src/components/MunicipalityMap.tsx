import { useEffect } from "react";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import { LatLngBounds } from "leaflet";
import type { Municipality } from "../types";

/**
 * Municipality representative points on a map (V0.2).
 *
 * Every marker is a *representative point* — an interior point of the official boundary
 * used to read the hazard grid cell there. It is not the city hall and it is not the
 * municipality's area; the legend says so on the map itself. Colours come from the
 * caller (`statusOf`), which derives them from the worker's rows; nothing is computed
 * here.
 */

export interface PointStatus {
  color: string;
  label: string;
  lines?: string[];
}

const KOREA_CENTER: [number, number] = [36.3, 127.8];

function FitTo({ points, activeId }: { points: Municipality[]; activeId: string | null }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    const bounds = new LatLngBounds(points.map((m) => [m.latitude, m.longitude])).pad(0.3);
    const fit = () => {
      const el = map.getContainer();
      if (el.clientWidth === 0 || el.clientHeight === 0) return false;
      map.invalidateSize();
      if (points.length === 1) map.setView([points[0].latitude, points[0].longitude], 9);
      else map.fitBounds(bounds, { maxZoom: 9 });
      return true;
    };
    if (fit()) return;
    const obs = new ResizeObserver(() => {
      if (fit()) obs.disconnect();
    });
    obs.observe(map.getContainer());
    return () => obs.disconnect();
    // fit only when the set of points changes, not when the highlight moves
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, points.map((m) => m.municipality_id).join(",")]);
  useEffect(() => {
    const m = activeId ? points.find((p) => p.municipality_id === activeId) : undefined;
    if (m) map.panTo([m.latitude, m.longitude]);
  }, [map, points, activeId]);
  return null;
}

export function MunicipalityMap({
  points,
  activeId,
  statusOf,
  onSelect,
  height = 360,
  pointLabel,
  warning,
}: {
  points: Municipality[];
  activeId: string | null;
  statusOf: (m: Municipality) => PointStatus;
  onSelect: (id: string) => void;
  height?: number;
  /** "Representative point for municipality-level hazard screening." */
  pointLabel: string;
  /** The fixed representative-point warning sentence. */
  warning: string;
}) {
  return (
    <div>
      <div style={{ height, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
        <MapContainer center={KOREA_CENTER} zoom={7} scrollWheelZoom style={{ height: "100%" }}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitTo points={points} activeId={activeId} />
          {points.map((m) => {
            const s = statusOf(m);
            const active = m.municipality_id === activeId;
            return (
              <CircleMarker
                key={m.municipality_id}
                center={[m.latitude, m.longitude]}
                radius={active ? 11 : 7}
                pathOptions={{
                  color: active ? "#ffffff" : s.color,
                  fillColor: s.color,
                  fillOpacity: active ? 0.95 : 0.7,
                  weight: active ? 3 : 1.5,
                }}
                eventHandlers={{ click: () => onSelect(m.municipality_id) }}
              >
                <Tooltip>
                  <b>{m.municipality_name}</b> · representative point
                  <br />
                  {s.label}
                  {(s.lines ?? []).map((l) => (
                    <span key={l}>
                      <br />
                      {l}
                    </span>
                  ))}
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>
      <div className="hint" style={{ marginTop: 6 }}>
        <b>●</b> {pointLabel} {warning}
      </div>
    </div>
  );
}
