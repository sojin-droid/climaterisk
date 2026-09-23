import { useEffect, useMemo, useState } from "react";
import {
  getPhysicalRiskReadiness,
  getPhysicalRiskTable,
  getRun,
  getRunProgress,
  submitPhysicalRiskModels,
} from "../lib/api";
import { money } from "../lib/format";
import type {
  Asset,
  PhysicalRiskModelsOutput,
  PhysicalRiskReadiness,
  PhysicalRiskRow,
  PhysicalRiskTable,
  Portfolio,
  Run,
  RunProgress,
} from "../types";

/**
 * Physical Risk Assessment — the non-expert flow:
 *   select assets → select hazards → analysis scope (Recommended) → Run → results → Excel.
 * Nothing is computed here: every number comes from the worker's canonical rows, the
 * summary/matrix frames from the backend, and the words from the display bundle.
 */

const MODELS = ["GLOBAL_BASELINE", "DATA_API_COUNTRY", "KOREA_LOCAL"] as const;
const HAZARD_KEYS = ["RF", "TC", "HEAT"] as const;
const TAG_OF: Record<string, string> = { RF: "RF", TC: "TC", HEAT: "HW" };
const LOCAL_FIRST = ["KOREA_LOCAL", "DATA_API_COUNTRY", "GLOBAL_BASELINE"];
const PAGE = 25;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
/** Full figures for the detail panel: "$3,576", never "$3.5K". */
const fmtUsdFull = (v: number | null | undefined, ccy = "USD") =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: ccy, maximumFractionDigits: 0 }).format(v);
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(3)}%`);
const fmtDelta = (v: number | null | undefined, unit: "%" | "pp") =>
  v == null ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(unit === "pp" ? 4 : 1)} ${unit}`;

function riskStyle(level: string | null | undefined): React.CSSProperties {
  const base: React.CSSProperties = { padding: "2px 8px", borderRadius: 999, fontSize: 12, fontWeight: 600 };
  switch (level) {
    case "High":
      return { ...base, background: "rgba(229,83,75,0.22)", color: "var(--danger)" };
    case "Medium":
      return { ...base, background: "rgba(224,163,46,0.22)", color: "var(--warn)" };
    case "Low":
      return { ...base, background: "rgba(47,158,143,0.22)", color: "var(--accent)" };
    default:
      return { ...base, background: "var(--panel-2)", color: "var(--muted)", fontWeight: 500 };
  }
}

/** The row that represents a facility × hazard: recommended model first, then most local, informative only. */
function primaryRow(rows: PhysicalRiskRow[], fid: string, tag: string, recommended: string | undefined) {
  const cands = rows.filter((r) => r.facility_id === fid && r.hazard_type === tag);
  const informative = (r: PhysicalRiskRow) => r.eal_usd != null || r.calculation_status === "HAZARD_ONLY";
  const order = [...(recommended ? [recommended] : []), ...LOCAL_FIRST];
  for (const m of order) {
    const r = cands.find((c) => c.model_id === m);
    if (r && informative(r)) return r;
  }
  for (const m of order) {
    const r = cands.find((c) => c.model_id === m);
    if (r) return r;
  }
  return cands[0];
}

export function ModelsView({ model }: { model: Portfolio }) {
  const [readiness, setReadiness] = useState<PhysicalRiskReadiness | null>(null);
  const display = readiness?.display;

  // STEP 1 — assets
  const [selected, setSelected] = useState<Set<string>>(() => new Set(model.assets.map((a) => a.id)));
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  // STEP 2 — hazards
  const [hazards, setHazards] = useState<Set<string>>(() => new Set(HAZARD_KEYS));
  // STEP 3 — scope
  const [mode, setMode] = useState<"recommended" | "custom">("recommended");
  const [scopes, setScopes] = useState<Set<string>>(() => new Set(["DATA_API_COUNTRY", "KOREA_LOCAL"]));
  // STEP 4 — run
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [out, setOut] = useState<PhysicalRiskModelsOutput | null>(null);
  const [table, setTable] = useState<PhysicalRiskTable | null>(null);
  // STEP 5 — results
  const [detailId, setDetailId] = useState<string | null>(null);
  const [showLimits, setShowLimits] = useState(false);

  useEffect(() => {
    getPhysicalRiskReadiness().then(setReadiness).catch(() => setReadiness(null));
  }, []);
  useEffect(() => {
    // keep the selection in step with the portfolio (assets added/removed on the Map tab)
    setSelected((prev) => new Set(model.assets.filter((a) => prev.has(a.id)).map((a) => a.id)));
  }, [model.assets]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return model.assets.filter(
      (a) =>
        !q ||
        a.id.toLowerCase().includes(q) ||
        a.name.toLowerCase().includes(q) ||
        String(a.properties.property_type ?? a.sector).toLowerCase().includes(q),
    );
  }, [model.assets, query]);
  const pageRows = filtered.slice(page * PAGE, (page + 1) * PAGE);
  const nPages = Math.max(1, Math.ceil(filtered.length / PAGE));

  const recommended = display?.recommended_models ?? {};
  const modelsToRun = useMemo(() => {
    if (mode === "recommended") {
      const picks = new Set([...hazards].map((h) => recommended[h]).filter(Boolean));
      return MODELS.filter((m) => picks.has(m));
    }
    return MODELS.filter((m) => scopes.has(m));
  }, [mode, hazards, scopes, recommended]);
  const nCalc = selected.size * hazards.size * modelsToRun.length;

  async function run() {
    setStatus("running");
    setMsg(null);
    setProgress(null);
    setOut(null);
    setTable(null);
    setDetailId(null);
    try {
      let r: Run = await submitPhysicalRiskModels(model.id, {
        facility_ids: [...selected],
        hazards: [...hazards],
        models: [...modelsToRun],
      });
      setRunId(r.id);
      for (let i = 0; i < 1800 && (r.status === "queued" || r.status === "running"); i++) {
        await sleep(2000);
        r = await getRun(model.id, r.id);
        if (r.status === "running") getRunProgress(model.id, r.id).then(setProgress).catch(() => undefined);
      }
      const o = r.output as PhysicalRiskModelsOutput | null;
      if (r.status === "done" && o && o.rows) {
        setOut(o);
        setTable(await getPhysicalRiskTable(model.id, r.id));
        setStatus("done");
        setMsg(o.detail);
      } else {
        setStatus("error");
        setMsg(o?.detail ?? r.detail ?? "The assessment failed — see the worker log.");
      }
    } catch (e) {
      setStatus("error");
      setMsg(String(e));
    }
  }

  const rows = out?.rows ?? [];
  const counts = table?.summary_counts;
  const matrix = (table?.frames?.asset_risk_matrix ?? []) as Record<string, string>[];
  const detailAsset = detailId ? model.assets.find((a) => a.id === detailId) : undefined;
  const gvc = (out?.comparisons?.global_vs_country ?? []) as Record<string, unknown>[];

  return (
    <div className="panelview">
      {/* ---------------------------------------------------------------- header */}
      <div className="card">
        <div className="section-title">Physical Risk Assessment</div>
        <p className="hint">
          Select your assets, pick the hazards, run one assessment, read the results, download Excel. The
          calculation underneath is CLIMADA — published impact functions, no local calibration — and every number
          in the export says which data and which function produced it.
        </p>
        <button className="btn secondary" onClick={() => setShowLimits((v) => !v)} style={{ fontSize: 12 }}>
          {showLimits ? "Hide" : "What this tool covers today"}
        </button>
        {showLimits && display && (
          <ul className="hint" style={{ marginTop: 8 }}>
            {display.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        )}
      </div>

      {/* ---------------------------------------------------------------- step 1 */}
      <div className="card">
        <div className="section-title">Step 1 · Select assets</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "8px 0" }}>
          <input
            placeholder="Search id, name, type…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            style={{ minWidth: 220 }}
          />
          <button className="btn secondary" onClick={() => setSelected(new Set(model.assets.map((a) => a.id)))}>
            Select all
          </button>
          <button className="btn secondary" onClick={() => setSelected(new Set())}>
            Clear
          </button>
          <span className="pill">
            {selected.size} of {model.assets.length} assets selected
          </span>
          {model.assets.length === 0 && <span className="hint">Add assets on the Map tab first.</span>}
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="select page"
                    checked={pageRows.length > 0 && pageRows.every((a) => selected.has(a.id))}
                    onChange={(e) => {
                      const next = new Set(selected);
                      pageRows.forEach((a) => (e.target.checked ? next.add(a.id) : next.delete(a.id)));
                      setSelected(next);
                    }}
                  />
                </th>
                <th>facility_id</th>
                <th>facility_name</th>
                <th>property_type</th>
                <th>asset_value_usd</th>
                <th>location</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((a: Asset) => (
                <tr
                  key={a.id}
                  onClick={() => {
                    const n = new Set(selected);
                    if (n.has(a.id)) n.delete(a.id);
                    else n.add(a.id);
                    setSelected(n);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <td>
                    <input type="checkbox" checked={selected.has(a.id)} onChange={() => undefined} aria-label={a.name} />
                  </td>
                  <td className="hint">{a.id.slice(0, 12)}</td>
                  <td>{a.name}</td>
                  <td>{String(a.properties.property_type ?? a.sector)}</td>
                  <td>{a.value > 0 ? money(a.value, a.currency) : <span className="hint">no value</span>}</td>
                  <td className="hint">
                    {a.lat.toFixed(3)}, {a.lon.toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {nPages > 1 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
            <button className="btn secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
              ‹
            </button>
            <span className="hint">
              page {page + 1} / {nPages}
            </span>
            <button className="btn secondary" disabled={page >= nPages - 1} onClick={() => setPage(page + 1)}>
              ›
            </button>
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------- step 2 */}
      <div className="card">
        <div className="section-title">Step 2 · Select risks</div>
        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
          {HAZARD_KEYS.map((h) => (
            <label key={h} style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={hazards.has(h)}
                onChange={(e) => {
                  const n = new Set(hazards);
                  if (e.target.checked) n.add(h);
                  else n.delete(h);
                  setHazards(n);
                }}
              />
              <span>
                <b>{display?.hazard_label[h] ?? h}</b>
                <div className="hint">{display?.hazard_copy[h]}</div>
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* ---------------------------------------------------------------- step 3 */}
      <div className="card">
        <div className="section-title">Step 3 · Analysis data</div>
        <div style={{ display: "flex", gap: 16, margin: "8px 0" }}>
          <label style={{ cursor: "pointer" }}>
            <input type="radio" checked={mode === "recommended"} onChange={() => setMode("recommended")} /> Recommended
          </label>
          <label style={{ cursor: "pointer" }}>
            <input type="radio" checked={mode === "custom"} onChange={() => setMode("custom")} /> Custom
          </label>
        </div>
        {mode === "recommended" ? (
          <p className="hint">
            The system uses the most local data that can actually run for each risk:{" "}
            {[...hazards]
              .map(
                (h) =>
                  `${display?.hazard_label[h] ?? h} → ${display?.scope_short[recommended[h]] ?? "—"}${
                    h === "HEAT" ? " (hazard only)" : ""
                  }`,
              )
              .join(" · ")}
            . Add <b>Global</b> under Custom to compare data sources.
          </p>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {MODELS.map((m) => (
              <label key={m} style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={scopes.has(m)}
                  onChange={(e) => {
                    const n = new Set(scopes);
                    if (e.target.checked) n.add(m);
                    else n.delete(m);
                    setScopes(n);
                  }}
                />
                <span>
                  <b>{display?.scope_label[m] ?? m}</b>
                  <div className="hint">{display?.scope_copy[m]}</div>
                  <div style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
                    {HAZARD_KEYS.filter((h) => hazards.has(h)).map((h) => (
                      <span key={h} className="pill">
                        {display?.hazard_label[h]}: {display?.scope_availability[m]?.[h] ?? "—"}
                      </span>
                    ))}
                  </div>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------- step 4 */}
      <div className="card">
        <div className="section-title">Step 4 · Run assessment</div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
          <button className="btn" onClick={run} disabled={status === "running" || nCalc === 0}>
            {status === "running" ? "Analyzing…" : "Run Assessment"}
          </button>
          <span className="hint">
            {selected.size} assets × {hazards.size} risks × {modelsToRun.length} data scopes = <b>{nCalc}</b>{" "}
            calculations · scenario {model.scenario.climate}
          </span>
        </div>
        {status === "running" && (
          <div className="status-box running" style={{ marginTop: 10 }}>
            <span className="spinner" />{" "}
            {progress?.total
              ? `${Math.min(progress.done ?? 0, progress.total)} / ${progress.total} calculations complete`
              : `Analyzing ${selected.size} assets…`}
            {progress?.current ? <span className="hint"> · {progress.current}</span> : null}
            <div className="hint">A global Data API set can be 1–2 GB on first use; later runs reuse the cache.</div>
          </div>
        )}
        {status === "error" && (
          <div className="status-box error" style={{ marginTop: 10 }}>
            {msg}
          </div>
        )}
        {status === "done" && msg && (
          <div className="status-box info" style={{ marginTop: 10 }}>
            {msg}
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------- results */}
      {out && table && runId && (
        <>
          <div
            className="card"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}
          >
            <div>
              <div className="section-title">Physical Risk Summary</div>
              <div className="kpi-grid">
                <Kpi label="Assets analyzed" value={counts?.assets_analyzed} />
                <Kpi label="High risk" value={counts?.high_risk} tone="High" />
                <Kpi label="Medium risk" value={counts?.medium_risk} tone="Medium" />
                <Kpi label="Low risk" value={counts?.low_risk} tone="Low" />
                <Kpi label="Hazard only" value={counts?.hazard_only} />
                <Kpi label="Not available" value={counts?.not_available} />
              </div>
              <div className="hint">Counts are per asset × risk. No overall portfolio score is computed.</div>
            </div>
            <a
              className="btn"
              href={`/api/session/${model.id}/run/${runId}/physical-risk-export.xlsx`}
              download={`physical_risk_${runId}.xlsx`}
              title="Portfolio Summary · Asset Risk Matrix · Hazard Results · Global vs Country · Global vs Korea Local · Methodology"
            >
              Download Excel
            </a>
          </div>

          <div className="card">
            <div className="section-title">Asset risk matrix</div>
            <div className="hint" style={{ marginBottom: 6 }}>
              Click an asset for details and the method behind each number.
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Flood</th>
                    <th>Typhoon</th>
                    <th>Heatwave</th>
                    <th>Calculated</th>
                  </tr>
                </thead>
                <tbody>
                  {matrix.map((m) => (
                    <tr
                      key={m["Facility ID"]}
                      onClick={() => setDetailId(m["Facility ID"])}
                      style={{ cursor: "pointer", background: detailId === m["Facility ID"] ? "var(--panel-2)" : undefined }}
                    >
                      <td>{m["Facility"]}</td>
                      {["Flood", "Typhoon", "Heatwave"].map((h) => (
                        <td key={h}>
                          <span style={riskStyle(m[h])}>{m[h]}</span>
                        </td>
                      ))}
                      <td className="hint">{m["Overall"]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {detailAsset && (
            <AssetDetail
              asset={detailAsset}
              rows={rows}
              recommended={recommended}
              display={display}
              gvc={gvc}
              onClose={() => setDetailId(null)}
            />
          )}

          <details className="card">
            <summary className="section-title" style={{ cursor: "pointer" }}>
              Technical table ({rows.length} rows: asset × risk × data scope)
            </summary>
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table>
                <thead>
                  <tr>
                    {table.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((r, i) => (
                    <tr key={i}>
                      {table.columns.map((c) => (
                        <td key={c}>{r[c] == null ? "—" : String(r[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: number | undefined; tone?: string }) {
  return (
    <div className="card nested" style={{ margin: 0 }}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={tone ? { color: (riskStyle(tone) as { color?: string }).color } : undefined}>
        {value ?? "—"}
      </div>
    </div>
  );
}

function AssetDetail({
  asset,
  rows,
  recommended,
  display,
  gvc,
  onClose,
}: {
  asset: Asset;
  rows: PhysicalRiskRow[];
  recommended: Record<string, string>;
  display: PhysicalRiskReadiness["display"] | undefined;
  gvc: Record<string, unknown>[];
  onClose: () => void;
}) {
  const [why, setWhy] = useState<string | null>(null);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div className="section-title">{asset.name}</div>
          <div className="hint">
            Asset value {asset.value > 0 ? money(asset.value, asset.currency) : "not set"} · {asset.lat.toFixed(3)},{" "}
            {asset.lon.toFixed(3)}
          </div>
        </div>
        <button className="btn secondary" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="kpi-grid" style={{ marginTop: 10 }}>
        {HAZARD_KEYS.map((h) => {
          const tag = TAG_OF[h];
          const r = primaryRow(rows, asset.id, tag, recommended[h]);
          const label = display?.hazard_label[h] ?? h;
          if (!r)
            return (
              <div key={h} className="card nested" style={{ margin: 0 }}>
                <b>{label}</b>
                <div className="hint">Not assessed in this run.</div>
              </div>
            );
          const priced = r.eal_usd != null;
          const cmp = gvc.find((c) => c.facility_id === asset.id && c.hazard_type === tag);
          return (
            <div key={h} className="card nested" style={{ margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <b>{label}</b>
                <span style={riskStyle(r.risk_level ?? (r.calculation_status === "HAZARD_ONLY" ? "Hazard only" : null))}>
                  {r.risk_level ?? display?.status_short[r.calculation_status] ?? r.calculation_status}
                </span>
              </div>
              {priced ? (
                <div style={{ marginTop: 6, lineHeight: 1.7 }}>
                  <div>
                    Expected Annual Loss <b>{fmtUsdFull(r.eal_usd, r.asset_value_currency ?? "USD")}</b>
                  </div>
                  <div>
                    EAL / Asset Value <b>{fmtPct(r.eal_as_pct_of_assets)}</b>
                  </div>
                  <div>
                    Potential Loss{" "}
                    {r.potential_loss_usd != null ? (
                      <>
                        <b>{fmtUsdFull(r.potential_loss_usd, r.asset_value_currency ?? "USD")}</b>{" "}
                        <span className="hint">({r.potential_loss_return_period_years ?? 100}-year)</span>
                      </>
                    ) : (
                      <span className="hint">not resolvable from this data</span>
                    )}
                  </div>
                  <div className="hint">Data: {display?.scope_label[r.model_id] ?? r.model_id}</div>
                </div>
              ) : (
                <div style={{ marginTop: 6, lineHeight: 1.7 }}>
                  <div>
                    Status <b>{display?.status_short[r.calculation_status] ?? r.calculation_status}</b>
                  </div>
                  {r.calculation_status === "HAZARD_ONLY" && r.hazard_intensity != null && (
                    <div>
                      Tmax p95 indicator{" "}
                      <b>
                        {r.hazard_intensity.toFixed(1)} {r.hazard_intensity_unit ?? "°C"}
                      </b>
                    </div>
                  )}
                  <div>
                    Financial loss <b>Not available</b> <span className="hint">(not $0)</span>
                  </div>
                  <div className="hint">Reason: {display?.status_copy[r.calculation_status] ?? r.status_detail}</div>
                  {r.status_detail && display?.status_copy[r.calculation_status] && (
                    <div className="hint">Detail: {r.status_detail}</div>
                  )}
                </div>
              )}
              <button className="btn secondary" style={{ marginTop: 8, fontSize: 12 }} onClick={() => setWhy(why === h ? null : h)}>
                {why === h ? "Hide" : "Why?"}
              </button>
              {why === h && (
                <div className="hint" style={{ marginTop: 6, lineHeight: 1.6 }}>
                  {r.risk_level && <div>{display?.risk_level_copy[r.risk_level]}</div>}
                  <div>{display?.method_copy[tag]}</div>
                  {r.impact_function_name && (
                    <div>
                      Impact Function: {r.impact_function_name} (id {r.impact_function_id})
                    </div>
                  )}
                  {r.impact_function_source && <div>Source: {r.impact_function_source}</div>}
                  {r.hazard_dataset && (
                    <div>
                      Hazard dataset: {r.hazard_dataset} {r.hazard_data_version ? `(${r.hazard_data_version})` : ""}
                    </div>
                  )}
                  {r.served_scenario && r.served_scenario !== r.requested_scenario && (
                    <div>
                      Requested {r.requested_scenario}, dataset serves {r.served_scenario}.
                    </div>
                  )}
                  <div>
                    Status {r.calculation_status}
                    {r.status_detail ? ` — ${r.status_detail}` : ""}
                  </div>
                </div>
              )}
              {cmp && cmp.available === true && (
                <div className="hint" style={{ marginTop: 8 }}>
                  <b>Compare data</b> — Global vs Country: EAL {fmtDelta(cmp.eal_change_pct as number | null, "%")},
                  EAL/assets {fmtDelta(cmp.eal_as_pct_assets_change_pp as number | null, "pp")}, hazard intensity{" "}
                  {fmtDelta(cmp.hazard_intensity_change_pct as number | null, "%")}.
                  <div>{display?.comparison_note}</div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
