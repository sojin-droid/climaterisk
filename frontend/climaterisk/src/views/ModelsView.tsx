import { useEffect, useMemo, useState } from "react";
import { getPhysicalRiskReadiness, getRun, submitPhysicalRiskModels } from "../lib/api";
import { money } from "../lib/format";
import type {
  PhysicalRiskModelsOutput,
  PhysicalRiskReadiness,
  PhysicalRiskRow,
  Portfolio,
  Run,
} from "../types";

/**
 * Models — the same facility, the same published CLIMADA impact function, three hazard
 * sources: GLOBAL_BASELINE (Data API, global), DATA_API_COUNTRY (Data API, country cut —
 * not Korea-local data) and KOREA_LOCAL (domestic source through its own adapter).
 * A model with no row is shown as "Not available", never as zero.
 */

const MODELS = ["GLOBAL_BASELINE", "DATA_API_COUNTRY", "KOREA_LOCAL"] as const;
const MODEL_LABEL: Record<string, string> = {
  GLOBAL_BASELINE: "Global baseline",
  DATA_API_COUNTRY: "Data API country",
  KOREA_LOCAL: "Korea local",
};
const HAZARD_LABEL: Record<string, string> = { RF: "Flood", TC: "Tropical cyclone", HW: "Heatwave", HM: "Heat mortality" };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const fmtUsd = (v: number | null | undefined, ccy = "USD") => (v == null ? "—" : money(v, ccy));
const fmtPct = (v: number | null | undefined, digits = 4) => (v == null ? "—" : `${v.toFixed(digits)}%`);
const fmtDelta = (v: number | null | undefined, unit: "%" | "pp") =>
  v == null ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(unit === "pp" ? 4 : 1)} ${unit}`;

export function ModelsView({ model }: { model: Portfolio }) {
  const [readiness, setReadiness] = useState<PhysicalRiskReadiness | null>(null);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const [out, setOut] = useState<PhysicalRiskModelsOutput | null>(null);
  const [fHazard, setFHazard] = useState("");
  const [fModel, setFModel] = useState("");
  const [fRisk, setFRisk] = useState("");
  const [fScenario, setFScenario] = useState("");

  useEffect(() => {
    getPhysicalRiskReadiness().then(setReadiness).catch(() => setReadiness(null));
  }, []);

  async function run() {
    setStatus("running");
    setMsg(null);
    try {
      let r: Run = await submitPhysicalRiskModels(model.id, {});
      for (let i = 0; i < 900 && (r.status === "queued" || r.status === "running"); i++) {
        await sleep(2000);
        r = await getRun(model.id, r.id);
      }
      const o = r.output as PhysicalRiskModelsOutput | null;
      if (r.status === "done" && o && o.rows) {
        setOut(o);
        setStatus("done");
        setMsg(o.detail);
      } else {
        setStatus("error");
        setMsg(o?.detail ?? r.detail ?? "Run failed — see worker log.");
      }
    } catch (e) {
      setStatus("error");
      setMsg(String(e));
    }
  }

  const rows = out?.rows ?? [];
  const facilities = useMemo(() => {
    const seen = new Map<string, string>();
    rows.forEach((r) => seen.set(r.facility_id, r.facility_name));
    return [...seen.entries()];
  }, [rows]);
  const hazards = useMemo(() => [...new Set(rows.map((r) => r.hazard_type))], [rows]);
  const scenarios = useMemo(() => [...new Set(rows.map((r) => r.scenario ?? ""))].filter(Boolean), [rows]);

  const filtered = rows.filter(
    (r) =>
      (!fHazard || r.hazard_type === fHazard) &&
      (!fModel || r.model_id === fModel) &&
      (!fRisk || r.risk_level === fRisk) &&
      (!fScenario || r.scenario === fScenario),
  );

  const byKey = (fid: string, haz: string, m: string) =>
    rows.find((r) => r.facility_id === fid && r.hazard_type === haz && r.model_id === m);
  const gvc = (out?.comparisons?.global_vs_country ?? []) as Record<string, unknown>[];
  const cmpFor = (fid: string, haz: string) =>
    gvc.find((c) => c.facility_id === fid && c.hazard_type === haz && c.available === true);

  return (
    <div className="panelview">
      <div className="card">
        <div className="section-title">Hazard models — same exposure, same impact function</div>
        <p className="hint">
          Three hazard sources for every facility: <b>Global baseline</b> (CLIMADA Data API,
          global), <b>Data API country</b> (the same Data API product cut to the country — <i>not</i>{" "}
          Korea-local data) and <b>Korea local</b> (a domestic dataset through its own adapter,
          reported only when one is actually connected). The impact function never changes between
          models, so a difference is attributable to the hazard data alone.
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button className="btn" onClick={run} disabled={status === "running" || model.assets.length === 0}>
            {status === "running" ? "Running…" : "Run three models"}
          </button>
          <span className="hint">
            scenario {model.scenario.climate} · {model.assets.length} asset{model.assets.length === 1 ? "" : "s"}
            {model.assets.length === 0 ? " — add assets on the Map tab first" : ""}
          </span>
        </div>
        {status === "running" && (
          <div className="status-box running" style={{ marginTop: 10 }}>
            <span className="spinner" /> Fetching hazards and pricing — a global Data API set can be 1–2 GB on first use.
          </div>
        )}
        {status === "error" && <div className="status-box error" style={{ marginTop: 10 }}>{msg}</div>}
        {status === "done" && msg && <div className="status-box info" style={{ marginTop: 10 }}>{msg}</div>}
      </div>

      {readiness && <ReadinessCard r={readiness} />}

      {out && (
        <>
          {facilities.map(([fid, name]) => (
            <div className="card" key={fid}>
              <div className="section-title">{name}</div>
              {hazards.map((haz) => (
                <FacilityHazard key={haz} fid={fid} haz={haz} byKey={byKey} cmp={cmpFor(fid, haz)} />
              ))}
            </div>
          ))}

          <div className="card">
            <div className="section-title">Batch table</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0" }}>
              <select value={fHazard} onChange={(e) => setFHazard(e.target.value)}>
                <option value="">All hazards</option>
                {hazards.map((h) => (
                  <option key={h} value={h}>{HAZARD_LABEL[h] ?? h}</option>
                ))}
              </select>
              <select value={fModel} onChange={(e) => setFModel(e.target.value)}>
                <option value="">All models</option>
                {MODELS.map((m) => (
                  <option key={m} value={m}>{MODEL_LABEL[m]}</option>
                ))}
              </select>
              <select value={fRisk} onChange={(e) => setFRisk(e.target.value)}>
                <option value="">All risk levels</option>
                {["Low", "Medium", "High"].map((l) => (
                  <option key={l} value={l}>{l}</option>
                ))}
              </select>
              <select value={fScenario} onChange={(e) => setFScenario(e.target.value)}>
                <option value="">All scenarios</option>
                {scenarios.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <span className="hint" style={{ alignSelf: "center" }}>{filtered.length} / {rows.length} rows</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>facility</th><th>hazard</th><th>model</th><th>risk</th><th>PML</th><th>EAL</th>
                    <th>EAL/assets</th><th>hazard source</th><th>IF id</th><th>scenario</th><th>horizon</th><th>status</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r, i) => (
                    <tr key={i}>
                      <td>{r.facility_name}</td>
                      <td>{HAZARD_LABEL[r.hazard_type] ?? r.hazard_type}</td>
                      <td>{MODEL_LABEL[r.model_id] ?? r.model_id}</td>
                      <td>{r.risk_level ?? "—"}</td>
                      <td>{fmtUsd(r.potential_loss_usd, r.asset_value_currency ?? "USD")}</td>
                      <td>{fmtUsd(r.eal_usd, r.asset_value_currency ?? "USD")}</td>
                      <td>{fmtPct(r.eal_as_pct_of_assets)}</td>
                      <td className="hint" title={r.hazard_dataset ?? ""}>{r.hazard_source ?? "—"}</td>
                      <td>{r.impact_function_id ?? "—"}</td>
                      <td>{r.served_scenario && r.served_scenario !== r.scenario ? `${r.scenario} → ${r.served_scenario}` : r.scenario ?? "—"}</td>
                      <td>{r.time_horizon ?? "—"}</td>
                      <td title={r.status_detail ?? ""}><span className="pill">{r.calculation_status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function FacilityHazard({
  fid,
  haz,
  byKey,
  cmp,
}: {
  fid: string;
  haz: string;
  byKey: (fid: string, haz: string, m: string) => PhysicalRiskRow | undefined;
  cmp: Record<string, unknown> | undefined;
}) {
  return (
    <div className="card nested">
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{HAZARD_LABEL[haz] ?? haz}</div>
      <div className="kpi-grid">
        {MODELS.map((m) => {
          const r = byKey(fid, haz, m);
          return (
            <div key={m} className="card nested" style={{ margin: 0 }}>
              <div className="kpi-label">{MODEL_LABEL[m]}</div>
              {!r || r.calculation_status === "NOT_IMPLEMENTED" ? (
                <>
                  <div className="kpi-value">Not available</div>
                  <div className="hint">{r?.status_detail ?? "no adapter connected"}</div>
                </>
              ) : (
                <>
                  <div>EAL: <b>{fmtUsd(r.eal_usd, r.asset_value_currency ?? "USD")}</b></div>
                  <div>EAL/Assets: <b>{fmtPct(r.eal_as_pct_of_assets)}</b></div>
                  <div>Risk: <b>{r.risk_level ?? "—"}</b> · PML: {fmtUsd(r.potential_loss_usd, r.asset_value_currency ?? "USD")}</div>
                  <div className="hint" title={r.status_detail ?? ""}>
                    <span className="pill">{r.calculation_status}</span>{" "}
                    {r.hazard_intensity != null ? `${r.hazard_intensity.toFixed(2)} ${r.hazard_intensity_unit ?? ""}` : ""}
                    {r.served_scenario && r.served_scenario !== r.requested_scenario
                      ? ` · served ${r.served_scenario}, requested ${r.requested_scenario}`
                      : ""}
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
      <div className="hint" style={{ marginTop: 6 }}>
        Δ Data API country vs global baseline — EAL {fmtDelta(cmp?.eal_change_pct as number | null, "%")}, EAL/assets{" "}
        {fmtDelta(cmp?.eal_as_pct_assets_change_pp as number | null, "pp")}, hazard intensity{" "}
        {fmtDelta(cmp?.hazard_intensity_change_pct as number | null, "%")}
        {cmp ? " · hazard-source comparison, not a climate-change multiplier" : " · comparison not available"}
      </div>
    </div>
  );
}

function ReadinessCard({ r }: { r: PhysicalRiskReadiness }) {
  const hazards = Object.keys(r.summary);
  return (
    <div className="card">
      <div className="section-title">Readiness — what each model can produce today</div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>hazard</th>
              {MODELS.map((m) => <th key={m}>{MODEL_LABEL[m]}</th>)}
            </tr>
          </thead>
          <tbody>
            {hazards.map((h) => (
              <tr key={h}>
                <td>{h}</td>
                {MODELS.map((m) => {
                  const cell = r.hazards[h]?.[m];
                  return (
                    <td key={m} title={cell ? `${cell.hazard_source} — ${cell.detail}` : ""}>
                      <span className="pill">{cell?.status ?? "—"}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">{r.rule}</p>
    </div>
  );
}
