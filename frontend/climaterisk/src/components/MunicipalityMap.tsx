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

/** V0.3 POC — a government-published official office point (a second, separate anchor). */
export interface OfficeMarker {
  facility_id: string;
  municipality_id: string;
  municipality_name: string;
  office_name: string;
  latitude: number;
  longitude: number;
  source: string;
}

const KOREA_CENTER: [number, number] = [36.3, 127.8];

function FitTo({
  points,
  offices,
  activeId,
}: {
  points: Municipality[];
  offices: OfficeMarker[];
  activeId: string | null;
}) {
  const map = useMap();
  const all: [number, number][] = [
    ...points.map((m) => [m.latitude, m.longitude] as [number, number]),
    ...offices.map((o) => [o.latitude, o.longitude] as [number, number]),
  ];
  useEffect(() => {
    if (all.length === 0) return;
    const bounds = new LatLngBounds(all).pad(0.3);
    const fit = () => {
      const el = map.getContainer();
      if (el.clientWidth === 0 || el.clientHeight === 0) return false;
      map.invalidateSize();
      if (all.length === 1) map.setView(all[0], 9);
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
  }, [map, [...points.map((m) => m.municipality_id), ...offices.map((o) => o.facility_id)].join(",")]);
  useEffect(() => {
    const m = activeId ? points.find((p) => p.municipality_id === activeId) : undefined;
    const o = activeId ? offices.find((p) => p.facility_id === activeId) : undefined;
    if (m) map.panTo([m.latitude, m.longitude]);
    else if (o) map.panTo([o.latitude, o.longitude]);
  }, [map, points, offices, activeId]);
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
  offices = [],
  officeStatusOf,
  officeLabel,
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
  /** V0.3 POC — official office points (drawn as dashed, dark-outlined circles). */
  offices?: OfficeMarker[];
  officeStatusOf?: (o: OfficeMarker) => PointStatus;
  /** Legend text for the office marker. */
  officeLabel?: string;
}) {
  return (
    <div>
      <div style={{ height, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
        <MapContainer center={KOREA_CENTER} zoom={7} scrollWheelZoom style={{ height: "100%" }}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitTo points={points} offices={offices} activeId={activeId} />
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
          {offices.map((o) => {
            const s = officeStatusOf ? officeStatusOf(o) : { color: "#3aa0ff", label: "selected — not assessed yet" };
            const active = o.facility_id === activeId;
            return (
              <CircleMarker
                key={o.facility_id}
                center={[o.latitude, o.longitude]}
                radius={active ? 11 : 8}
                pathOptions={{
                  color: active ? "#ffffff" : "#111827",
                  fillColor: s.color,
                  fillOpacity: active ? 0.95 : 0.85,
                  weight: active ? 3 : 2.5,
                  dashArray: "3 2",
                }}
                eventHandlers={{ click: () => onSelect(o.facility_id) }}
              >
                <Tooltip>
                  <b>{o.office_name}</b> · official office point
                  <br />
                  {o.municipality_name} · government-published ({o.source})
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
        {offices.length > 0 && (
          <>
            <br />
            <b>◌</b> {officeLabel ?? "Official office point (government-published) — a separate location, not a replacement."}
          </>
        )}
      </div>
    </div>
  );
}
