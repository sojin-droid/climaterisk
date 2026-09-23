import { useEffect, useMemo, useState } from "react";
import {
  getPhysicalRiskReadiness,
  getPhysicalRiskTable,
  getRun,
  getRunProgress,
  listRuns,
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
 * Physical Risk Assessment — the final portfolio screen for non-experts:
 *   Select your assets → Select hazards → Run → Download Excel.
 * Nothing is computed here: every number comes from the worker's canonical rows, the
 * summary/matrix frames from the backend, and the words from the display bundle. Thresholds
 * are never hard-coded — the row's own `risk_level_criteria` and the configured band copy
 * are shown.
 */

const MODELS = ["GLOBAL_BASELINE", "DATA_API_COUNTRY", "KOREA_LOCAL"] as const;
const HAZARD_KEYS = ["RF", "TC", "HEAT"] as const;
const TAG_OF: Record<string, string> = { RF: "RF", TC: "TC", HEAT: "HW" };
const LOCAL_FIRST = ["KOREA_LOCAL", "DATA_API_COUNTRY", "GLOBAL_BASELINE"];
const PAGE = 25;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const fmtUsdFull = (v: number | null | undefined, ccy = "USD") =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: ccy, maximumFractionDigits: 0 }).format(v);
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(3)}%`);
const fmtDelta = (v: number | null | undefined, unit: "%" | "pp") =>
  v == null ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(unit === "pp" ? 4 : 1)} ${unit}`;
const fmtDate = (iso: string | undefined) => (iso ? iso.slice(0, 10) : "");

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

/** Availability sentence for one data scope × hazard, from the readiness table. */
function scopeStatus(readiness: PhysicalRiskReadiness | null, model: string, hazardKey: string): string {
  const status = readiness?.hazards?.[hazardKey]?.[model]?.status;
  switch (status) {
    case "READY":
      return "Available";
    case "HAZARD_ONLY":
      return "Available — hazard only";
    case "NOT_IMPLEMENTED":
      return "Unavailable — domestic source adapter not implemented";
    case "NO_HAZARD_DATA":
      return "Unavailable — no dataset of this kind";
    default:
      return "Unavailable";
  }
}
const canRun = (readiness: PhysicalRiskReadiness | null, model: string, hazardKey: string) =>
  ["READY", "HAZARD_ONLY"].includes(readiness?.hazards?.[hazardKey]?.[model]?.status ?? "");

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

function reportFilename(out: PhysicalRiskModelsOutput | null, created: string | undefined) {
  const n = out ? new Set(out.rows.map((r) => r.facility_id)).size : 0;
  const stamp = (created ?? new Date().toISOString()).slice(0, 10).replace(/-/g, "");
  return `Physical_Risk_Report_${n}_Assets_${stamp}.xlsx`;
}

export function ModelsView({ model }: { model: Portfolio }) {
  const [readiness, setReadiness] = useState<PhysicalRiskReadiness | null>(null);
  const display = readiness?.display;

  // 1 — assets
  const [selected, setSelected] = useState<Set<string>>(() => new Set(model.assets.map((a) => a.id)));
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  // 2 — hazards
  const [hazards, setHazards] = useState<Set<string>>(() => new Set(HAZARD_KEYS));
  // 3 — analysis
  const [mode, setMode] = useState<"recommended" | "custom">("recommended");
  const [scopes, setScopes] = useState<Set<string>>(() => new Set(["DATA_API_COUNTRY", "KOREA_LOCAL"]));
  const [baseline, setBaseline] = useState(false);
  // run
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [out, setOut] = useState<PhysicalRiskModelsOutput | null>(null);
  const [table, setTable] = useState<PhysicalRiskTable | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  // results
  const [detailId, setDetailId] = useState<string | null>(null);
  const [showLimits, setShowLimits] = useState(false);

  const refreshHistory = () =>
    listRuns(model.id)
      .then(setHistory)
      .catch(() => undefined);

  useEffect(() => {
    getPhysicalRiskReadiness().then(setReadiness).catch(() => setReadiness(null));
  }, []);
  useEffect(() => {
    refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.id]);
  useEffect(() => {
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
    return MODELS.filter((m) => scopes.has(m) && [...hazards].some((h) => canRun(readiness, m, h)));
  }, [mode, hazards, scopes, recommended, readiness]);
  const nCalc = selected.size * hazards.size * modelsToRun.length * (mode === "custom" && baseline ? 2 : 1);

  async function openRun(r: Run) {
    const o = r.output as PhysicalRiskModelsOutput | null;
    if (r.status !== "done" || !o || !o.rows) return;
    setRun(r);
    setOut(o);
    setTable(await getPhysicalRiskTable(model.id, r.id));
    setDetailId(null);
    setStatus("done");
    setMsg(o.detail);
  }

  async function runAssessment() {
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
        ...(mode === "custom" && baseline ? { baseline_scenario: "historical" } : {}),
      });
      setRun(r);
      for (let i = 0; i < 1800 && (r.status === "queued" || r.status === "running"); i++) {
        await sleep(2000);
        r = await getRun(model.id, r.id);
        if (r.status === "running") getRunProgress(model.id, r.id).then(setProgress).catch(() => undefined);
      }
      const o = r.output as PhysicalRiskModelsOutput | null;
      if (r.status === "done" && o && o.rows) {
        await openRun(r);
        refreshHistory();
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
  const assetById = useMemo(() => new Map(model.assets.map((a) => [a.id, a])), [model.assets]);
  const detailAsset = detailId ? assetById.get(detailId) : undefined;
  const excelHref = run ? `/api/session/${model.id}/run/${run.id}/physical-risk-export.xlsx` : "";
  const excelName = reportFilename(out, run?.created_at);

  return (
    <div className="panelview">
      {/* -------------------------------------------------------------- header */}
      <div className="card">
        <div className="section-title">Physical Risk Assessment</div>
        <p className="hint">
          <b>Select your assets → Select hazards → Run → Download Excel.</b> The calculation underneath is CLIMADA
          with published impact functions and no local calibration; every value is a modeled expected loss under the
          selected hazard and impact-function assumptions, and every row says which data and which function produced
          it.
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

      {/* -------------------------------------------------------------- 1 assets */}
      <div className="card">
        <div className="section-title">1 · Select assets</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "8px 0" }}>
          <input
            placeholder="Search assets…"
            aria-label="Search assets"
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
                <th>Asset</th>
                <th>Type</th>
                <th>Asset value</th>
                <th>Location</th>
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
                  <td>
                    {a.name} <span className="hint">{a.id.slice(0, 12)}</span>
                  </td>
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

      {/* -------------------------------------------------------------- 2 hazards */}
      <div className="card">
        <div className="section-title">2 · Select hazards</div>
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

      {/* -------------------------------------------------------------- 3 analysis */}
      <div className="card">
        <div className="section-title">3 · Analysis</div>
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
            The most local data that can actually run is used for each hazard:{" "}
            {[...hazards]
              .map(
                (h) =>
                  `${display?.hazard_label[h] ?? h} → ${display?.scope_short[recommended[h]] ?? "—"}${
                    h === "HEAT" ? " (hazard only)" : ""
                  }`,
              )
              .join(" · ")}
            . No large global dataset is downloaded in this mode.
          </p>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {MODELS.map((m) => {
              const runnable = [...hazards].some((h) => canRun(readiness, m, h));
              return (
                <label key={m} style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: runnable ? "pointer" : "not-allowed", opacity: runnable ? 1 : 0.6 }}>
                  <input
                    type="checkbox"
                    disabled={!runnable}
                    checked={scopes.has(m) && runnable}
                    onChange={(e) => {
                      const n = new Set(scopes);
                      if (e.target.checked) n.add(m);
                      else n.delete(m);
                      setScopes(n);
                    }}
                  />
                  <span>
                    <b>{display?.scope_label[m] ?? m}</b> <span className="hint">({display?.scope_short[m]})</span>
                    <div className="hint">{display?.scope_copy[m]}</div>
                    <div style={{ marginTop: 4 }}>
                      {HAZARD_KEYS.filter((h) => hazards.has(h)).map((h) => (
                        <div key={h} className="hint">
                          {display?.hazard_label[h]}: {scopeStatus(readiness, m, h)}
                        </div>
                      ))}
                    </div>
                    {m === "GLOBAL_BASELINE" && (
                      <div className="hint">A global set can be 1–2 GB on first use; later runs reuse the cache.</div>
                    )}
                  </span>
                </label>
              );
            })}
            <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
              <input type="checkbox" checked={baseline} onChange={(e) => setBaseline(e.target.checked)} />
              <span>
                <b>Also run the historical baseline</b>
                <div className="hint">
                  Enables the climate change multiplier (future EAL ÷ baseline EAL within the same data scope). Doubles
                  the calculations.
                </div>
              </span>
            </label>
          </div>
        )}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 12 }}>
          <button className="btn" onClick={runAssessment} disabled={status === "running" || nCalc === 0}>
            {status === "running" ? "Analyzing…" : "RUN ASSESSMENT"}
          </button>
          <span className="hint">
            {selected.size} assets × {hazards.size} hazards × {modelsToRun.length} data scopes
            {mode === "custom" && baseline ? " × 2 periods" : ""} = <b>{nCalc}</b> calculations · scenario{" "}
            {model.scenario.climate}
          </span>
        </div>
        {status === "running" && (
          <div className="status-box running" style={{ marginTop: 10 }}>
            <span className="spinner" />{" "}
            {progress?.total
              ? `${Math.min(progress.done ?? 0, progress.total)} / ${progress.total} calculations complete`
              : `Analyzing ${selected.size} assets…`}
            {progress?.current ? <span className="hint"> · {progress.current}</span> : null}
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

      {/* -------------------------------------------------------------- results */}
      {out && table && run && (
        <>
          <div
            className="card"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}
          >
            <div>
              <div className="section-title">Physical Risk Summary</div>
              <div className="kpi-grid">
                <Kpi label="Assets analyzed" value={counts?.assets_analyzed} />
                <Kpi label="High-risk hazards" value={counts?.high_risk} tone="High" />
                <Kpi label="Medium-risk hazards" value={counts?.medium_risk} tone="Medium" />
                <Kpi label="Low-risk hazards" value={counts?.low_risk} tone="Low" />
                <Kpi label="Hazard-only results" value={counts?.hazard_only} />
                <Kpi label="Not available" value={counts?.not_available} />
              </div>
              <div className="hint">{display?.summary_note}</div>
            </div>
            <a
              className="btn"
              href={excelHref}
              download={excelName}
              title="Portfolio Summary · Asset Risk Matrix · Hazard Results · Global vs Country · Global vs Korea Local · Methodology"
            >
              Download Excel
            </a>
          </div>

          <div className="card">
            <div className="section-title">Asset risk matrix</div>
            <div className="hint" style={{ marginBottom: 6 }}>
              One row per asset. Click a row for the expected loss, the potential loss and why each hazard got its
              level.
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Asset value</th>
                    <th>Flood</th>
                    <th>Tropical Cyclone</th>
                    <th>Heatwave</th>
                    <th>Calculated</th>
                  </tr>
                </thead>
                <tbody>
                  {matrix.map((m) => {
                    const a = assetById.get(m["Facility ID"]);
                    return (
                      <tr
                        key={m["Facility ID"]}
                        onClick={() => setDetailId(m["Facility ID"])}
                        style={{ cursor: "pointer", background: detailId === m["Facility ID"] ? "var(--panel-2)" : undefined }}
                      >
                        <td>{m["Facility"]}</td>
                        <td>{a && a.value > 0 ? money(a.value, a.currency) : "—"}</td>
                        {["Flood", "Typhoon", "Heatwave"].map((h) => (
                          <td key={h}>
                            <span style={riskStyle(m[h])}>{m[h]}</span>
                          </td>
                        ))}
                        <td className="hint">{m["Overall"]}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {detailAsset && (
            <AssetDetail
              asset={detailAsset}
              out={out}
              recommended={recommended}
              display={display}
              onClose={() => setDetailId(null)}
            />
          )}

          <details className="card">
            <summary className="section-title" style={{ cursor: "pointer" }}>
              Technical table ({rows.length} rows: asset × hazard × data scope)
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

      {/* -------------------------------------------------------------- history */}
      {history.length > 0 && (
        <div className="card">
          <div className="section-title">Recent assessments</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Date</th>
                  <th>Assets</th>
                  <th>Hazards</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => {
                  const o = h.output as PhysicalRiskModelsOutput | null;
                  const nAssets = o?.rows ? new Set(o.rows.map((r) => r.facility_id)).size : "—";
                  const hz = o?.rows ? [...new Set(o.rows.map((r) => display?.hazard_label[r.hazard_type] ?? r.hazard_type))].join(", ") : "—";
                  return (
                    <tr key={h.id} style={{ background: run?.id === h.id ? "var(--panel-2)" : undefined }}>
                      <td className="hint">{h.id.slice(0, 8)}</td>
                      <td>{fmtDate(h.created_at)}</td>
                      <td>{nAssets}</td>
                      <td className="hint">{hz}</td>
                      <td>
                        <span className="pill">{h.status}</span>
                      </td>
                      <td style={{ display: "flex", gap: 6 }}>
                        <button className="btn secondary" disabled={h.status !== "done"} onClick={() => openRun(h)}>
                          Open
                        </button>
                        {h.status === "done" && (
                          <a
                            className="btn secondary"
                            href={`/api/session/${model.id}/run/${h.id}/physical-risk-export.xlsx`}
                            download={reportFilename(o, h.created_at)}
                          >
                            Excel
                          </a>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
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

function Line({ k, v, muted }: { k: string; v: React.ReactNode; muted?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span className="hint">{k}</span>
      <span style={muted ? { color: "var(--muted)" } : { fontWeight: 600 }}>{v}</span>
    </div>
  );
}

function AssetDetail({
  asset,
  out,
  recommended,
  display,
  onClose,
}: {
  asset: Asset;
  out: PhysicalRiskModelsOutput;
  recommended: Record<string, string>;
  display: PhysicalRiskReadiness["display"] | undefined;
  onClose: () => void;
}) {
  const [why, setWhy] = useState<string | null>(null);
  const rows = out.rows;
  const gvc = (out.comparisons?.global_vs_country ?? []) as Record<string, unknown>[];
  const multipliers = (out.climate_change_multipliers ?? []) as Record<string, unknown>[];
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div className="section-title">{asset.name}</div>
          <div className="hint">
            Asset value <b>{asset.value > 0 ? fmtUsdFull(asset.value, asset.currency) : "not set"}</b> ·{" "}
            {asset.lat.toFixed(3)}, {asset.lon.toFixed(3)}
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
          const ccy = r.asset_value_currency ?? "USD";
          const g = rows.find((x) => x.facility_id === asset.id && x.hazard_type === tag && x.model_id === "GLOBAL_BASELINE");
          const c = rows.find((x) => x.facility_id === asset.id && x.hazard_type === tag && x.model_id === "DATA_API_COUNTRY");
          const cmp = gvc.find((x) => x.facility_id === asset.id && x.hazard_type === tag && x.available === true);
          const mult = multipliers.find((x) => x.facility_id === asset.id && x.hazard_type === tag && x.model_id === r.model_id);
          return (
            <div key={h} className="card nested" style={{ margin: 0, display: "grid", gap: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <b>{label}</b>
                <span style={riskStyle(r.risk_level ?? (r.calculation_status === "HAZARD_ONLY" ? "Hazard only" : null))}>
                  {r.risk_level ?? display?.status_short[r.calculation_status] ?? r.calculation_status}
                </span>
              </div>
              {priced ? (
                <>
                  <Line k="Risk" v={r.risk_level ?? "—"} />
                  <Line k="EAL (expected annual loss)" v={fmtUsdFull(r.eal_usd, ccy)} />
                  <Line k="EAL / Asset value" v={fmtPct(r.eal_as_pct_of_assets)} />
                  <Line
                    k="Potential loss"
                    v={r.potential_loss_usd != null ? fmtUsdFull(r.potential_loss_usd, ccy) : "not resolvable from this data"}
                    muted={r.potential_loss_usd == null}
                  />
                  <Line k="Return period" v={`${r.potential_loss_return_period_years ?? r.return_period_years ?? 100} years`} />
                  <Line k="Data source" v={display?.scope_label[r.model_id] ?? r.model_id} muted />
                  <Line k="Impact function" v={r.impact_function_name ?? "—"} muted />
                </>
              ) : (
                <>
                  <Line k="Status" v={display?.status_short[r.calculation_status] ?? r.calculation_status} />
                  {r.calculation_status === "HAZARD_ONLY" && r.hazard_intensity != null && (
                    <Line k="Modeled hazard intensity (season p95 Tmax)" v={`${r.hazard_intensity.toFixed(1)} ${r.hazard_intensity_unit ?? "°C"}`} />
                  )}
                  <Line k="Financial loss" v="Not available" />
                  <Line k="Data source" v={display?.scope_label[r.model_id] ?? r.model_id} muted />
                  <div className="hint">Reason: {display?.status_copy[r.calculation_status] ?? r.status_detail}</div>
                  {r.status_detail && display?.status_copy[r.calculation_status] && <div className="hint">Detail: {r.status_detail}</div>}
                </>
              )}
              <button className="btn secondary" style={{ marginTop: 6, fontSize: 12 }} onClick={() => setWhy(why === h ? null : h)}>
                {why === h ? "Hide" : r.risk_level ? `Why ${r.risk_level}?` : "Why?"}
              </button>
              {why === h && (
                <div className="hint" style={{ lineHeight: 1.6, display: "grid", gap: 3 }}>
                  {r.risk_level && (
                    <>
                      <div>
                        EAL / Asset value <b>{fmtPct(r.eal_as_pct_of_assets)}</b> · {r.risk_level} band: <b>{r.risk_level_criteria}</b>
                      </div>
                      <div>{display?.risk_level_copy[r.risk_level]}</div>
                    </>
                  )}
                  <div>{display?.method_copy[tag]}</div>
                  {r.impact_function_name && (
                    <div>
                      Impact function: {r.impact_function_name} (id {r.impact_function_id})
                    </div>
                  )}
                  {r.impact_function_source && <div>Source: {r.impact_function_source}</div>}
                  {r.hazard_dataset && (
                    <div>
                      Hazard source: {r.hazard_dataset}
                      {r.hazard_data_version ? ` · dataset version ${r.hazard_data_version}` : ""}
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

              {/* Global vs Country */}
              {g && c && cmp ? (
                <div style={{ marginTop: 8 }}>
                  <div className="hint">
                    <b>Global vs Country</b>
                  </div>
                  <table style={{ fontSize: 12 }}>
                    <thead>
                      <tr>
                        <th></th>
                        <th>Global</th>
                        <th>Country</th>
                        <th>Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td className="hint">Modeled hazard intensity</td>
                        <td>{g.hazard_intensity?.toFixed(2)}</td>
                        <td>{c.hazard_intensity?.toFixed(2)}</td>
                        <td>{fmtDelta(cmp.hazard_intensity_change_pct as number | null, "%")}</td>
                      </tr>
                      <tr>
                        <td className="hint">EAL</td>
                        <td>{fmtUsdFull(g.eal_usd, ccy)}</td>
                        <td>{fmtUsdFull(c.eal_usd, ccy)}</td>
                        <td>{fmtDelta(cmp.eal_change_pct as number | null, "%")}</td>
                      </tr>
                      <tr>
                        <td className="hint">EAL / Assets</td>
                        <td>{fmtPct(g.eal_as_pct_of_assets)}</td>
                        <td>{fmtPct(c.eal_as_pct_of_assets)}</td>
                        <td>{fmtDelta(cmp.eal_as_pct_assets_change_pp as number | null, "pp")}</td>
                      </tr>
                      <tr>
                        <td className="hint">Potential loss</td>
                        <td>{fmtUsdFull(g.potential_loss_usd, ccy)}</td>
                        <td>{fmtUsdFull(c.potential_loss_usd, ccy)}</td>
                        <td>{fmtDelta(cmp.potential_loss_change_pct as number | null, "%")}</td>
                      </tr>
                      <tr>
                        <td className="hint">Risk</td>
                        <td>{g.risk_level ?? "—"}</td>
                        <td>{c.risk_level ?? "—"}</td>
                        <td className="hint">{g.risk_level === c.risk_level ? "same" : "differs"}</td>
                      </tr>
                    </tbody>
                  </table>
                  <div className="hint">{display?.comparison_note}</div>
                  <div className="hint">{display?.comparison_purpose}</div>
                </div>
              ) : null}

              {/* Korea Local */}
              <div className="hint" style={{ marginTop: 6 }}>
                {h === "HEAT"
                  ? "Korea Local: hazard available (KMA TAMAX) · financial comparison unavailable — no applicable CLIMADA Impact Function."
                  : `Korea Local: not implemented for ${label}.`}
              </div>

              {/* Climate change multiplier */}
              {mult && (
                <div className="hint" style={{ marginTop: 6 }}>
                  <b>Climate change</b> ({String(mult.baseline_scenario)} {String(mult.baseline_period ?? "")} → {String(mult.future_scenario)} {String(mult.future_period ?? "")})
                  <div>Baseline EAL {fmtUsdFull(mult.baseline_eal_usd as number | null, ccy)} · Future EAL {fmtUsdFull(mult.future_eal_usd as number | null, ccy)}</div>
                  {mult.climate_change_multiplier != null ? (
                    <div>
                      Multiplier <b>{(mult.climate_change_multiplier as number).toFixed(2)}</b> · Change{" "}
                      {fmtDelta(((mult.climate_change_multiplier as number) - 1) * 100, "%")} — future EAL ÷ baseline EAL within the same data scope
                    </div>
                  ) : (
                    <div>Multiplier not available — {String(mult.detail)}</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
