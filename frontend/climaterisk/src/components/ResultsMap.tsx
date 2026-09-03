import { useEffect } from "react";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import { LatLngBounds } from "leaflet";
import type { AssetImpact } from "../types";
import { money } from "../lib/format";

function riskColor(frac: number): string {
  if (frac < 0.33) return "#2f9e8f";
  if (frac < 0.66) return "#e0a32e";
  return "#e5534b";
}

/**
 * Frame the assets — and keep framing them until the container actually has a size.
 *
 * The results map usually mounts below the fold, so its container can be 0x0 when the
 * effect first runs. Leaflet then fits the bounds against an empty viewport and silently
 * falls back to the whole world, which is what "why is my Spanish portfolio showing the
 * Atlantic?" looks like. So: tell Leaflet to re-measure, and re-fit on every resize until
 * the box is real.
 */
function FitBounds({ impacts }: { impacts: AssetImpact[] }) {
  const map = useMap();
  useEffect(() => {
    if (impacts.length === 0) return;
    const bounds = new LatLngBounds(impacts.map((a) => [a.lat, a.lon])).pad(0.4);
    const fit = () => {
      const el = map.getContainer();
      if (el.clientWidth === 0 || el.clientHeight === 0) return false;
      map.invalidateSize();
      map.fitBounds(bounds, { maxZoom: 8 });
      return true;
    };
    if (fit()) return;
    const obs = new ResizeObserver(() => {
      if (fit()) obs.disconnect();
    });
    obs.observe(map.getContainer());
    return () => obs.disconnect();
  }, [map, impacts]);
  return null;
}

export function ResultsMap({
  impacts,
  currency,
  format,
}: {
  impacts: AssetImpact[];
  currency: string;
  /** Overrides currency formatting — health/index perils are not money. */
  format?: (value: number) => string;
}) {
  const fmt = format ?? ((v: number) => money(v, currency));
  const maxEai = Math.max(1, ...impacts.map((a) => a.eai));

  return (
    <div style={{ height: 320, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
      <MapContainer center={[20, 15]} zoom={2} scrollWheelZoom style={{ height: "100%" }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds impacts={impacts} />
        {impacts.map((a) => {
          const frac = a.eai / maxEai;
          const color = riskColor(frac);
          return (
            <CircleMarker
              key={a.id}
              center={[a.lat, a.lon]}
              radius={6 + frac * 16}
              pathOptions={{ color, fillColor: color, fillOpacity: 0.6, weight: 2 }}
            >
              <Tooltip>EAI: {fmt(a.eai)}/yr</Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
