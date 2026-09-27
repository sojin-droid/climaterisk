import { useEffect, useMemo, useState } from "react";
import {
  getMunicipalities,
  getPhysicalRiskReadiness,
  getPhysicalRiskTable,
  getRun,
  getRunProgress,
  listRuns,
  submitPhysicalRiskModels,
} from "../lib/api";
import { money } from "../lib/format";
import { MunicipalityMap, type PointStatus } from "../components/MunicipalityMap";
import type {
  Asset,
  Municipality,
  PhysicalRiskModelsOutput,
  PhysicalRiskReadiness,
  PhysicalRiskRow,
  PhysicalRiskTable,
  Portfolio,
  Run,
  RunProgress,
} from "../types";

/**
 * Physical Risk Assessment — the portfolio screen for non-experts:
 *   Select municipalities (or your assets) → Select hazards → Run → Download Excel.
 * Nothing is computed here: every number comes from the worker's canonical rows, the
 * summary/matrix frames from the backend, and the words from the display bundle. Thresholds
 * are never hard-coded — the row's own `risk_level_criteria` and the configured band copy
 * are shown.
 *
 * V0.2 adds the MUNICIPALITY target: official representative points (an interior point of
 * each boundary) are screened for hazard, and priced only when the user types an asset
 * value for that municipality. Every municipality result is a representative-point
 * assessment, never a municipality-wide aggregate — the fixed warning sentence says so.
 */

const MODELS = ["GLOBAL_BASELINE", "DATA_API_COUNTRY", "KOREA_LOCAL"] as const;
const HAZARD_KEYS = ["RF", "TC", "HEAT"] as const;
const TAG_OF: Record<string, string> = { RF: "RF", TC: "TC", HEAT: "HW" };
const LOCAL_FIRST = ["KOREA_LOCAL", "DATA_API_COUNTRY", "GLOBAL_BASELINE"];
const PAGE = 25;
type Target = "MUNICIPALITY" | "FACILITY";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const fmtUsdFull = (v: number | null | undefined, ccy = "USD") =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: ccy, maximumFractionDigits: 0 }).format(v);
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(3)}%`);
const fmtDelta = (v: number | null | undefined, unit: "%" | "pp") =>
  v == null ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(unit === "pp" ? 4 : 1)} ${unit}`;
const fmtDate = (iso: string | undefined) => (iso ? iso.slice(0, 10) : "");
const fmtIntensity = (r: PhysicalRiskRow) =>
  r.hazard_intensity == null ? "—" : `${r.hazard_intensity.toFixed(2)} ${r.hazard_intensity_unit ?? ""}`.trim();
const fmtResolution = (r: PhysicalRiskRow) =>
  r.spatial_resolution == null
    ? "—"
    : `${r.spatial_resolution} ${r.spatial_resolution_unit ?? ""} (${r.spatial_unit_type ?? "grid"})`.trim();

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
const riskColor = (level: string | null | undefined, status?: string) => {
  switch (level) {
    case "High":
      return "#e5534b";
    case "Medium":
      return "#e0a32e";
    case "Low":
      return "#2f9e8f";
    default:
      return status === "HAZARD_ONLY" ? "#3aa0ff" : "#8a94a6";
  }
};

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

/** The row that represents a point × hazard: recommended model first, then most local, informative only. */
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
  if (out?.assessment_target === "MUNICIPALITY") return `Municipality_Physical_Risk_Report_${n}_Municipalities_${stamp}.xlsx`;
  return `Physical_Risk_Report_${n}_Assets_${stamp}.xlsx`;
}
const targetOf = (out: PhysicalRiskModelsOutput | null | undefined): Target =>
  out?.assessment_target === "MUNICIPALITY" ? "MUNICIPALITY" : "FACILITY";

const LEVEL_LABEL: Record<string, string> = {
  METROPOLITAN: "Metropolitan city",
  PROVINCE: "Province",
  CITY: "City (시)",
  COUNTY: "County (군)",
  DISTRICT: "District (구)",
};

export function ModelsView({ model }: { model: Portfolio }) {
  const [readiness, setReadiness] = useState<PhysicalRiskReadiness | null>(null);
  const display = readiness?.display;
  const muniCopy = display?.municipality;

  // 0 — target
  const [target, setTarget] = useState<Target>("MUNICIPALITY");
  // 1a — assets
  const [selected, setSelected] = useState<Set<string>>(() => new Set(model.assets.map((a) => a.id)));
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  // 1b — municipalities
  const [municipalities, setMunicipalities] = useState<Municipality[]>([]);
  const [muniError, setMuniError] = useState<string | null>(null);
  const [muniSelected, setMuniSelected] = useState<Set<string>>(() => new Set());
  const [muniValues, setMuniValues] = useState<Record<string, string>>({});
  const [muniQuery, setMuniQuery] = useState("");
  const [muniTier, setMuniTier] = useState<"ALL" | "SIDO" | "SIGUNGU">("ALL");
  const [muniPage, setMuniPage] = useState(0);
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
  const [showCoverage, setShowCoverage] = useState(false);

  const refreshHistory = () =>
    listRuns(model.id)
      .then(setHistory)
      .catch(() => undefined);

  useEffect(() => {
    getPhysicalRiskReadiness().then(setReadiness).catch(() => setReadiness(null));
    getMunicipalities()
      .then((r) => setMunicipalities(r.municipalities))
      .catch((e) => setMuniError(String(e)));
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

  const muniFiltered = useMemo(() => {
    const q = muniQuery.trim().toLowerCase();
    return municipalities.filter(
      (m) =>
        (muniTier === "ALL" || m.tier === muniTier) &&
        (!q ||
          m.municipality_name.toLowerCase().includes(q) ||
          m.province_name.toLowerCase().includes(q) ||
          m.municipality_id.toLowerCase().includes(q)),
    );
  }, [municipalities, muniQuery, muniTier]);
  const muniPageRows = muniFiltered.slice(muniPage * PAGE, (muniPage + 1) * PAGE);
  const muniPages = Math.max(1, Math.ceil(muniFiltered.length / PAGE));
  const muniById = useMemo(() => new Map(municipalities.map((m) => [m.municipality_id, m])), [municipalities]);
  const selectedMunis = useMemo(
    () => municipalities.filter((m) => muniSelected.has(m.municipality_id)),
    [municipalities, muniSelected],
  );
  const valuedCount = selectedMunis.filter((m) => Number(muniValues[m.municipality_id]) > 0).length;

  const recommended = display?.recommended_models ?? {};
  const modelsToRun = useMemo(() => {
    if (mode === "recommended") {
      const picks = new Set([...hazards].map((h) => recommended[h]).filter(Boolean));
      return MODELS.filter((m) => picks.has(m));
    }
    return MODELS.filter((m) => scopes.has(m) && [...hazards].some((h) => canRun(readiness, m, h)));
  }, [mode, hazards, scopes, recommended, readiness]);
  const nPoints = target === "MUNICIPALITY" ? muniSelected.size : selected.size;
  const nCalc = nPoints * hazards.size * modelsToRun.length * (mode === "custom" && baseline ? 2 : 1);

  async function openRun(r: Run) {
    const o = r.output as PhysicalRiskModelsOutput | null;
    if (r.status !== "done" || !o || !o.rows) return;
    setRun(r);
    setOut(o);
    setTable(await getPhysicalRiskTable(model.id, r.id));
    setDetailId(null);
    setStatus("done");
    setMsg(o.detail);
    setTarget(targetOf(o));
  }

  async function runAssessment() {
    setStatus("running");
    setMsg(null);
    setProgress(null);
    setOut(null);
    setTable(null);
    setDetailId(null);
    try {
      const common = {
        hazards: [...hazards],
        models: [...modelsToRun],
        ...(mode === "custom" && baseline ? { baseline_scenario: "historical" } : {}),
      };
      const body =
        target === "MUNICIPALITY"
          ? {
              ...common,
              assessment_target: "MUNICIPALITY" as const,
              municipality_ids: [...muniSelected],
              asset_values: Object.fromEntries(
                [...muniSelected].filter((id) => Number(muniValues[id]) > 0).map((id) => [id, Number(muniValues[id])]),
              ),
            }
          : { ...common, assessment_target: "FACILITY" as const, facility_ids: [...selected] };
      let r: Run = await submitPhysicalRiskModels(model.id, body);
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
  const resultTarget = targetOf(out);
  const matrix = (table?.frames?.asset_risk_matrix ?? []) as Record<string, string>[];
  const muniMatrix = (table?.frames?.municipality_risk_matrix ?? []) as Record<string, string>[];
  const assetById = useMemo(() => new Map(model.assets.map((a) => [a.id, a])), [model.assets]);
  const detailAsset = detailId && resultTarget === "FACILITY" ? assetById.get(detailId) : undefined;
  const excelHref = run ? `/api/session/${model.id}/run/${run.id}/physical-risk-export.xlsx` : "";
  const excelName = reportFilename(out, run?.created_at);

  /** Map marker colour/label for a municipality from the run's primary rows. */
  const resultPoints = useMemo(() => {
    if (!out || resultTarget !== "MUNICIPALITY") return [] as Municipality[];
    const ids = [...new Set(out.rows.map((r) => r.facility_id))];
    return ids.map((id) => muniById.get(id)).filter((m): m is Municipality => !!m);
  }, [out, resultTarget, muniById]);
  const statusOf = (m: Municipality): PointStatus => {
    const lines: string[] = [];
    let worst: string | null = null;
    let worstStatus: string | undefined;
    for (const h of HAZARD_KEYS) {
      const r = primaryRow(rows, m.municipality_id, TAG_OF[h], recommended[h]);
      if (!r) continue;
      const short = r.risk_level ?? muniCopy?.status_short[r.calculation_status] ?? display?.status_short[r.calculation_status] ?? r.calculation_status;
      lines.push(`${display?.hazard_label[h] ?? h}: ${short}`);
      const rank = { High: 3, Medium: 2, Low: 1 }[r.risk_level ?? ""] ?? 0;
      const worstRank = { High: 3, Medium: 2, Low: 1 }[worst ?? ""] ?? 0;
      if (rank > worstRank) worst = r.risk_level;
      if (!worstStatus || r.calculation_status === "HAZARD_ONLY") worstStatus = r.calculation_status;
    }
    return {
      color: riskColor(worst, worstStatus),
      label: worst ? `Highest priced level: ${worst}` : "No priced hazard (screening / hazard only)",
      lines,
    };
  };

  return (
    <div className="panelview">
      {/* -------------------------------------------------------------- header */}
      <div className="card">
        <div className="section-title">Physical Risk Assessment</div>
        <p className="hint">
          <b>Select municipalities or your assets → Select hazards → Run → Download Excel.</b> The calculation
          underneath is CLIMADA with published impact functions and no local calibration; every value is a modeled
          expected loss under the selected hazard and impact-function assumptions, and every row says which data,
          which function and which hazard grid produced it.
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn secondary" onClick={() => setShowCoverage((v) => !v)} style={{ fontSize: 12 }}>
            {showCoverage ? "Hide coverage" : "What this tool covers today"}
          </button>
          <button className="btn secondary" onClick={() => setShowLimits((v) => !v)} style={{ fontSize: 12 }}>
            {showLimits ? "Hide limitations" : "Limitations"}
          </button>
        </div>
        {display && (
          <ul className="hint" style={{ marginTop: 8, marginBottom: 0 }} aria-label="coverage lines">
            {display.coverage_lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        )}
        {showCoverage && display && (
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table style={{ fontSize: 12 }} aria-label="hazard coverage">
              <thead>
                <tr>
                  <th>Hazard</th>
                  <th>Hazard data</th>
                  <th>Financial loss</th>
                  <th>Risk level</th>
                  <th>Resolution</th>
                  <th>Status</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {display.coverage_table.map((c) => (
                  <tr key={c.hazard_key}>
                    <td>
                      <b>{c.Hazard}</b>
                    </td>
                    <td>{c["Hazard Data"]}</td>
                    <td>{c["Financial Loss"]}</td>
                    <td>{c["Risk Level"]}</td>
                    <td>{c.Resolution}</td>
                    <td>
                      <span className="pill">{c.Status}</span>
                    </td>
                    <td className="hint">{c.Why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="hint" style={{ marginTop: 6 }}>
              {display.resolution_note}
            </div>
          </div>
        )}
        {showLimits && display && (
          <ul className="hint" style={{ marginTop: 8 }}>
            {display.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        )}
      </div>

      {/* -------------------------------------------------------------- 1 target + points */}
      <div className="card">
        <div className="section-title">1 · Assessment target</div>
        <div style={{ display: "flex", gap: 16, margin: "8px 0" }}>
          <label style={{ cursor: "pointer" }}>
            <input type="radio" checked={target === "MUNICIPALITY"} onChange={() => setTarget("MUNICIPALITY")} />{" "}
            {muniCopy?.target_municipalities ?? "Municipalities"}
          </label>
          <label style={{ cursor: "pointer" }}>
            <input type="radio" checked={target === "FACILITY"} onChange={() => setTarget("FACILITY")} />{" "}
            {muniCopy?.target_facilities ?? "My Assets"}
          </label>
        </div>

        {target === "MUNICIPALITY" ? (
          <>
            <p className="hint" style={{ marginTop: 0 }}>
              {muniCopy?.screening_vs_financial}{" "}
              {muniCopy?.point_definition}
            </p>
            {muniError && <div className="status-box error">{muniError}</div>}
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "8px 0" }}>
              <input
                placeholder="Search municipality…"
                aria-label="Search municipality"
                value={muniQuery}
                onChange={(e) => {
                  setMuniQuery(e.target.value);
                  setMuniPage(0);
                }}
                style={{ minWidth: 220 }}
              />
              <select
                aria-label="Municipality level"
                value={muniTier}
                onChange={(e) => {
                  setMuniTier(e.target.value as "ALL" | "SIDO" | "SIGUNGU");
                  setMuniPage(0);
                }}
              >
                <option value="ALL">All levels</option>
                <option value="SIDO">시·도 (metropolitan / province)</option>
                <option value="SIGUNGU">시·군·구</option>
              </select>
              <button
                className="btn secondary"
                onClick={() => setMuniSelected(new Set([...muniSelected, ...muniFiltered.map((m) => m.municipality_id)]))}
              >
                Select all
              </button>
              <button className="btn secondary" onClick={() => setMuniSelected(new Set())}>
                Clear
              </button>
              <span className="pill" aria-label="selected municipalities">
                {muniSelected.size} of {municipalities.length} municipalities selected
                {valuedCount > 0 ? ` · ${valuedCount} with an asset value` : ""}
              </span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        aria-label="select page"
                        checked={muniPageRows.length > 0 && muniPageRows.every((m) => muniSelected.has(m.municipality_id))}
                        onChange={(e) => {
                          const next = new Set(muniSelected);
                          muniPageRows.forEach((m) => (e.target.checked ? next.add(m.municipality_id) : next.delete(m.municipality_id)));
                          setMuniSelected(next);
                        }}
                      />
                    </th>
                    <th>Municipality</th>
                    <th>Level</th>
                    <th>Province</th>
                    <th>Asset value (USD, optional)</th>
                    <th>Representative point</th>
                  </tr>
                </thead>
                <tbody>
                  {muniPageRows.map((m) => (
                    <tr
                      key={m.municipality_id}
                      onClick={() => {
                        const n = new Set(muniSelected);
                        if (n.has(m.municipality_id)) n.delete(m.municipality_id);
                        else n.add(m.municipality_id);
                        setMuniSelected(n);
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <input type="checkbox" checked={muniSelected.has(m.municipality_id)} onChange={() => undefined} aria-label={m.municipality_name} />
                      </td>
                      <td>
                        {m.municipality_name} <span className="hint">{m.municipality_id}</span>
                      </td>
                      <td className="hint">{LEVEL_LABEL[m.municipality_level] ?? m.municipality_level}</td>
                      <td className="hint">{m.province_name}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <input
                          type="number"
                          min={0}
                          step={1000000}
                          placeholder="not supplied"
                          aria-label={`asset value ${m.municipality_name}`}
                          value={muniValues[m.municipality_id] ?? ""}
                          onChange={(e) => setMuniValues({ ...muniValues, [m.municipality_id]: e.target.value })}
                          style={{ width: 140 }}
                        />
                      </td>
                      <td className="hint">
                        {m.latitude.toFixed(3)}, {m.longitude.toFixed(3)} · boundary interior point
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {muniPages > 1 && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
                <button className="btn secondary" disabled={muniPage === 0} onClick={() => setMuniPage(muniPage - 1)}>
                  ‹
                </button>
                <span className="hint">
                  page {muniPage + 1} / {muniPages}
                </span>
                <button className="btn secondary" disabled={muniPage >= muniPages - 1} onClick={() => setMuniPage(muniPage + 1)}>
                  ›
                </button>
              </div>
            )}
            {selectedMunis.length > 0 && muniCopy && (
              <div style={{ marginTop: 10 }}>
                <MunicipalityMap
                  points={selectedMunis}
                  activeId={null}
                  statusOf={() => ({ color: "#3aa0ff", label: "selected — not assessed yet" })}
                  onSelect={() => undefined}
                  height={260}
                  pointLabel={muniCopy.point_label}
                  warning={muniCopy.warning}
                />
              </div>
            )}
          </>
        ) : (
          <>
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
          </>
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
          {display &&
            display.coverage_table
              .filter((c) => c.Status === "NOT_READY")
              .map((c) => (
                <label key={c.hazard_key} style={{ display: "flex", gap: 10, alignItems: "flex-start", opacity: 0.6, cursor: "not-allowed" }}>
                  <input type="checkbox" disabled checked={false} readOnly aria-label={`${c.Hazard} not ready`} />
                  <span>
                    <b>{c.Hazard}</b> <span className="pill">not ready</span>
                    <div className="hint">Why? {c.Why}</div>
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
            {nPoints} {target === "MUNICIPALITY" ? "municipalities" : "assets"} × {hazards.size} hazards ×{" "}
            {modelsToRun.length} data scopes
            {mode === "custom" && baseline ? " × 2 periods" : ""} = <b>{nCalc}</b> calculations · scenario{" "}
            {model.scenario.climate}
          </span>
        </div>
        {status === "running" && (
          <div className="status-box running" style={{ marginTop: 10 }}>
            <span className="spinner" />{" "}
            {progress?.total
              ? `${Math.min(progress.done ?? 0, progress.total)} / ${progress.total} calculations complete`
              : `Analyzing ${nPoints} ${target === "MUNICIPALITY" ? "municipalities" : "assets"}…`}
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
              <div className="section-title">
                {resultTarget === "MUNICIPALITY" ? "Municipality Physical Risk Summary" : "Physical Risk Summary"}
              </div>
              <div className="kpi-grid">
                <Kpi label={resultTarget === "MUNICIPALITY" ? "Municipalities assessed" : "Assets analyzed"} value={counts?.assets_analyzed} />
                <Kpi label="High-risk hazards" value={counts?.high_risk} tone="High" />
                <Kpi label="Medium-risk hazards" value={counts?.medium_risk} tone="Medium" />
                <Kpi label="Low-risk hazards" value={counts?.low_risk} tone="Low" />
                <Kpi label="Hazard-only results" value={counts?.hazard_only} />
                <Kpi label="Not available" value={counts?.not_available} />
              </div>
              <div className="hint">{display?.summary_note}</div>
              {resultTarget === "MUNICIPALITY" && (
                <div className="status-box info" style={{ marginTop: 8 }} aria-label="representative point warning">
                  {muniCopy?.warning ?? out.municipality_dataset?.warning}
                </div>
              )}
            </div>
            <a
              className="btn"
              href={excelHref}
              download={excelName}
              title={
                resultTarget === "MUNICIPALITY"
                  ? "Municipality Summary · Municipality Risk Matrix · Hazard Results · Spatial Resolution · Hazard Coverage · Methodology · Run Info"
                  : "Portfolio Summary · Asset Risk Matrix · Hazard Results · Global vs Country · Global vs Korea Local · Methodology"
              }
            >
              Download Excel
            </a>
          </div>

          {resultTarget === "MUNICIPALITY" && muniCopy && (
            <div className="card">
              <div className="section-title">Map — representative points</div>
              <div className="hint" style={{ marginBottom: 6 }}>
                Click a point to open its row; click a row below to highlight its point. Colour = highest priced risk
                level among the selected hazards (blue = hazard only, grey = no priced hazard).
              </div>
              <MunicipalityMap
                points={resultPoints}
                activeId={detailId}
                statusOf={statusOf}
                onSelect={(id) => setDetailId(id)}
                pointLabel={muniCopy.point_label}
                warning={muniCopy.warning}
              />
            </div>
          )}

          <div className="card">
            <div className="section-title">
              {resultTarget === "MUNICIPALITY" ? "Municipality risk table" : "Asset risk matrix"}
            </div>
            <div className="hint" style={{ marginBottom: 6 }}>
              One row per {resultTarget === "MUNICIPALITY" ? "municipality" : "asset"}. Click a row for the expected
              loss, the potential loss and why each hazard got its level.
            </div>
            <div className="table-wrap">
              <table aria-label="risk matrix">
                <thead>
                  <tr>
                    <th>{resultTarget === "MUNICIPALITY" ? "Municipality" : "Asset"}</th>
                    <th>Asset value</th>
                    <th>Flood</th>
                    <th>Tropical Cyclone</th>
                    <th>Heatwave</th>
                    <th>Calculated</th>
                  </tr>
                </thead>
                <tbody>
                  {resultTarget === "MUNICIPALITY"
                    ? muniMatrix.map((m) => {
                        const id = m["Municipality ID"];
                        const value = rows.find((r) => r.facility_id === id && r.asset_value_usd)?.asset_value_usd;
                        return (
                          <tr
                            key={id}
                            onClick={() => setDetailId(id)}
                            style={{ cursor: "pointer", background: detailId === id ? "var(--panel-2)" : undefined }}
                          >
                            <td>{m["Municipality"]}</td>
                            <td>{value ? fmtUsdFull(value) : <span className="hint">not supplied</span>}</td>
                            {["Flood", "Tropical Cyclone", "Heatwave"].map((h) => (
                              <td key={h}>
                                <span style={riskStyle(m[h])}>{m[h]}</span>
                              </td>
                            ))}
                            <td className="hint">{m["Calculated"]}</td>
                          </tr>
                        );
                      })
                    : matrix.map((m) => {
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
            <PointDetail
              title={detailAsset.name}
              subtitle={`Asset value ${detailAsset.value > 0 ? fmtUsdFull(detailAsset.value, detailAsset.currency) : "not set"} · ${detailAsset.lat.toFixed(3)}, ${detailAsset.lon.toFixed(3)}`}
              pointId={detailAsset.id}
              out={out}
              recommended={recommended}
              display={display}
              municipality={null}
              onClose={() => setDetailId(null)}
            />
          )}
          {detailId && resultTarget === "MUNICIPALITY" && muniById.get(detailId) && (
            <PointDetail
              title={muniById.get(detailId)!.municipality_name}
              subtitle={`${LEVEL_LABEL[muniById.get(detailId)!.municipality_level] ?? ""} · ${muniById.get(detailId)!.province_name} · representative point ${muniById
                .get(detailId)!
                .latitude.toFixed(4)}, ${muniById.get(detailId)!.longitude.toFixed(4)}`}
              pointId={detailId}
              out={out}
              recommended={recommended}
              display={display}
              municipality={muniById.get(detailId)!}
              onClose={() => setDetailId(null)}
            />
          )}

          <details className="card">
            <summary className="section-title" style={{ cursor: "pointer" }}>
              Technical table ({rows.length} rows: {resultTarget === "MUNICIPALITY" ? "municipality" : "asset"} × hazard ×
              data scope)
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
                  <th>Target</th>
                  <th>Points</th>
                  <th>Hazards</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => {
                  const o = h.output as PhysicalRiskModelsOutput | null;
                  const nPts = o?.rows ? new Set(o.rows.map((r) => r.facility_id)).size : "—";
                  const hz = o?.rows ? [...new Set(o.rows.map((r) => display?.hazard_label[r.hazard_type] ?? r.hazard_type))].join(", ") : "—";
                  return (
                    <tr key={h.id} style={{ background: run?.id === h.id ? "var(--panel-2)" : undefined }}>
                      <td className="hint">{h.id.slice(0, 8)}</td>
                      <td>{fmtDate(h.created_at)}</td>
                      <td className="hint">{targetOf(o) === "MUNICIPALITY" ? "Municipalities" : "Assets"}</td>
                      <td>{nPts}</td>
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

/** Detail panel for one point (asset or municipality): one card per hazard, with "Why?". */
function PointDetail({
  title,
  subtitle,
  pointId,
  out,
  recommended,
  display,
  municipality,
  onClose,
}: {
  title: string;
  subtitle: string;
  pointId: string;
  out: PhysicalRiskModelsOutput;
  recommended: Record<string, string>;
  display: PhysicalRiskReadiness["display"] | undefined;
  municipality: Municipality | null;
  onClose: () => void;
}) {
  const [why, setWhy] = useState<string | null>(null);
  const rows = out.rows;
  const gvc = (out.comparisons?.global_vs_country ?? []) as Record<string, unknown>[];
  const multipliers = (out.climate_change_multipliers ?? []) as Record<string, unknown>[];
  const muniCopy = display?.municipality;
  const valued = rows.some((r) => r.facility_id === pointId && r.asset_value_usd);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div className="section-title">{title}</div>
          <div className="hint">{subtitle}</div>
          {municipality && (
            <div className="hint">
              <b>Representative point</b> · {muniCopy?.point_label} {muniCopy?.point_definition}
            </div>
          )}
        </div>
        <button className="btn secondary" onClick={onClose}>
          Close
        </button>
      </div>
      {municipality && !valued && (
        <div className="status-box info" style={{ marginTop: 8 }}>
          {muniCopy?.financial_unavailable} {muniCopy?.status_copy?.NO_EXPOSURE_DATA}
        </div>
      )}
      <div className="kpi-grid" style={{ marginTop: 10 }}>
        {HAZARD_KEYS.map((h) => {
          const tag = TAG_OF[h];
          const r = primaryRow(rows, pointId, tag, recommended[h]);
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
          const g = rows.find((x) => x.facility_id === pointId && x.hazard_type === tag && x.model_id === "GLOBAL_BASELINE");
          const c = rows.find((x) => x.facility_id === pointId && x.hazard_type === tag && x.model_id === "DATA_API_COUNTRY");
          const cmp = gvc.find((x) => x.facility_id === pointId && x.hazard_type === tag && x.available === true);
          const mult = multipliers.find((x) => x.facility_id === pointId && x.hazard_type === tag && x.model_id === r.model_id);
          const statusShort =
            (municipality ? muniCopy?.status_short[r.calculation_status] : undefined) ??
            display?.status_short[r.calculation_status] ??
            r.calculation_status;
          const statusCopy =
            (municipality ? muniCopy?.status_copy[r.calculation_status] : undefined) ??
            display?.status_copy[r.calculation_status] ??
            r.status_detail;
          return (
            <div key={h} className="card nested" style={{ margin: 0, display: "grid", gap: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <b>{label}</b>
                <span style={riskStyle(r.risk_level ?? (r.calculation_status === "HAZARD_ONLY" ? "Hazard only" : null))}>
                  {r.risk_level ?? statusShort}
                </span>
              </div>
              <Line
                k={h === "HEAT" ? "Tmax indicator (season p95 daily max)" : "Modeled hazard intensity"}
                v={fmtIntensity(r)}
              />
              <Line k="Status" v={statusShort} />
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
                  <Line k="Impact function" v={r.impact_function_name ?? "—"} muted />
                </>
              ) : (
                <>
                  <Line
                    k="Financial loss"
                    v={municipality && r.calculation_status === "NO_EXPOSURE_DATA" ? "Not available — asset value not supplied" : "Not available"}
                    muted
                  />
                  <div className="hint">Why? {statusCopy}</div>
                  {r.status_detail && statusCopy !== r.status_detail && <div className="hint">Detail: {r.status_detail}</div>}
                </>
              )}
              <Line k="Data source" v={display?.scope_label[r.model_id] ?? r.model_id} muted />
              <Line k="Spatial resolution" v={fmtResolution(r)} muted />
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
                  {r.spatial_resolution_description && <div>Hazard grid: {r.spatial_resolution_description}</div>}
                  {display?.resolution_note && <div>{display.resolution_note}</div>}
                  {r.served_scenario && r.served_scenario !== r.requested_scenario && (
                    <div>
                      Requested {r.requested_scenario}, dataset serves {r.served_scenario}.
                    </div>
                  )}
                  <div>
                    Status {r.calculation_status}
                    {r.status_detail ? ` — ${r.status_detail}` : ""}
                  </div>
                  {municipality && <div>{muniCopy?.warning}</div>}
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
      {municipality && <div className="hint" style={{ marginTop: 8 }}>{muniCopy?.warning}</div>}
    </div>
  );
}
