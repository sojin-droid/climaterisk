import { lazy, Suspense, useState } from "react";
import type { Asset, Libraries } from "../types";

// MapLibre is heavy (~1 MB) — load the draw map only when a footprint asset is edited.
const FootprintDrawMap = lazy(() =>
  import("./FootprintDrawMap").then((m) => ({ default: m.FootprintDrawMap })),
);

/** Footprint (polygon) authoring for footprint-scale assets — drawn footprints are
 *  disaggregated to grid points by the worker (value split evenly). Keeps a local text
 *  buffer so invalid-while-typing JSON doesn't fight the controlled field. */
function FootprintField({
  asset,
  onChange,
}: {
  asset: Asset;
  onChange: (patch: Partial<Asset>) => void;
}) {
  const [text, setText] = useState(asset.geometry ? JSON.stringify(asset.geometry) : "");
  const [err, setErr] = useState<string | null>(null);

  const setText2 = (t: string) => {
    setText(t);
    if (t.trim() === "") {
      setErr(null);
      onChange({ geometry: null });
      return;
    }
    try {
      setErr(null);
      onChange({ geometry: JSON.parse(t) });
    } catch {
      setErr("invalid GeoJSON");
    }
  };

  const boxAround = () => {
    const h = 0.05;
    const lo = asset.lon;
    const la = asset.lat;
    const geom = {
      type: "Polygon",
      coordinates: [
        [
          [lo - h, la - h],
          [lo + h, la - h],
          [lo + h, la + h],
          [lo - h, la + h],
          [lo - h, la - h],
        ],
      ],
    };
    setText(JSON.stringify(geom));
    setErr(null);
    onChange({ geometry: geom });
  };

  return (
    <div className="field">
      <label>Footprint / line geometry (draw a polygon or line, or edit GeoJSON)</label>
      <div style={{ marginBottom: 6 }}>
        <Suspense fallback={<p className="hint">Loading map…</p>}>
          <FootprintDrawMap
            lat={asset.lat}
            lon={asset.lon}
            geometry={asset.geometry}
            onChange={(geom) => setText2(geom ? JSON.stringify(geom) : "")}
          />
        </Suspense>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
        <button className="btn secondary" style={{ padding: "4px 8px" }} onClick={boxAround}>
          ▢ Box around location
        </button>
        {text && (
          <button
            className="btn secondary"
            style={{ padding: "4px 8px" }}
            onClick={() => setText2("")}
          >
            Clear
          </button>
        )}
      </div>
      <textarea
        rows={4}
        style={{ width: "100%", fontFamily: "monospace", fontSize: 11 }}
        value={text}
        placeholder='{"type":"Polygon","coordinates":[[[lon,lat],…]]}'
        onChange={(e) => setText2(e.target.value)}
      />
      {err && <p className="hint" style={{ color: "var(--danger)" }}>{err}</p>}
      <p className="hint">Disaggregated to interior grid points; asset value is split across them.</p>
    </div>
  );
}

export function AssetEditor({
  asset,
  libraries,
  onChange,
  onDelete,
  onClose,
}: {
  asset: Asset;
  libraries: Libraries;
  onChange: (patch: Partial<Asset>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const num = (v: string): number => (v === "" ? 0 : Number(v));

  const sectorDefaultClass =
    libraries.sectors.sectors.find((s) => s.id === asset.sector)?.default_vulnerability_class ?? "";
  const classes = libraries.impact_functions.classes;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="section-title">Edit facility</div>
        <button className="btn secondary" style={{ padding: "4px 8px" }} onClick={onClose}>
          ← list
        </button>
      </div>

      <div className="field">
        <label>Name</label>
        <input value={asset.name} onChange={(e) => onChange({ name: e.target.value })} />
      </div>

      <div className="field">
        <label>Sector</label>
        <select value={asset.sector} onChange={(e) => onChange({ sector: e.target.value })}>
          {libraries.sectors.sectors.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Geographic scale</label>
        <select
          value={asset.geographic_scale}
          onChange={(e) =>
            onChange({ geographic_scale: e.target.value as Asset["geographic_scale"] })
          }
        >
          <option value="point">Point</option>
          <option value="footprint">Footprint</option>
          <option value="regional">Regional</option>
          <option value="national">National</option>
        </select>
      </div>

      {asset.geographic_scale === "footprint" && (
        <FootprintField asset={asset} onChange={onChange} />
      )}

      <div className="row2">
        <div className="field">
          <label>Latitude</label>
          <input
            type="number"
            value={asset.lat}
            step="0.0001"
            onChange={(e) => onChange({ lat: num(e.target.value) })}
          />
        </div>
        <div className="field">
          <label>Longitude</label>
          <input
            type="number"
            value={asset.lon}
            step="0.0001"
            onChange={(e) => onChange({ lon: num(e.target.value) })}
          />
        </div>
      </div>

      <div className="row2">
        <div className="field">
          <label>Value</label>
          <input
            type="number"
            value={asset.value}
            onChange={(e) => onChange({ value: num(e.target.value) })}
          />
        </div>
        <div className="field">
          <label>Currency</label>
          <input value={asset.currency} onChange={(e) => onChange({ currency: e.target.value })} />
        </div>
      </div>

      <div className="field">
        <label>People on site (headcount) — optional</label>
        <input
          type="number"
          min={0}
          step={1}
          placeholder="e.g. 1200"
          value={asset.headcount ?? ""}
          onChange={(e) =>
            onChange({ headcount: e.target.value === "" ? null : Math.max(0, num(e.target.value)) })
          }
        />
        <p className="hint">
          Exposure for <strong>health</strong> perils — heat mortality counts deaths among these
          people, not damage to the asset's value. Left empty, that peril falls back to a default
          and says so in its result detail.
        </p>
      </div>

      <div className="field">
        <label>Annual emissions (tCO₂e) — optional</label>
        <input
          type="number"
          value={asset.annual_emissions_tco2e ?? ""}
          placeholder="proxy from sector if blank"
          onChange={(e) =>
            onChange({
              annual_emissions_tco2e: e.target.value === "" ? null : num(e.target.value),
            })
          }
        />
      </div>

      <div className="field">
        <label>Vulnerability class</label>
        <select
          value={asset.vulnerability_class ?? ""}
          onChange={(e) => onChange({ vulnerability_class: e.target.value || null })}
        >
          <option value="">— sector default ({sectorDefaultClass}) —</option>
          {classes.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      <button className="btn danger" onClick={onDelete}>
        Delete facility
      </button>
    </div>
  );
}
