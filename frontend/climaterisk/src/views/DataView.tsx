import { useEffect, useState } from "react";
import { fetchOpenData, getHazardCatalog, getKoreaStatus, getRun, submitIngest } from "../lib/api";
import { formatScenario } from "../lib/format";
import type {
  DataSource,
  HazardCatalog,
  KoreaStatus,
  IngestResult,
  Libraries,
  OpenDataFetchResult,
  Portfolio,
  Run,
} from "../types";

function accessColor(access: string): string {
  const a = access.toLowerCase();
  if (a.includes("paid") || a.includes("academic")) return "var(--danger)";
  if (a.includes("login") || a.includes("registration")) return "var(--accent-2)";
  return "var(--accent)";
}

const FETCH_LABEL: Record<string, string> = {
  manual: "manual download",
  needs_login: "login required — manual",
  operational: "live feed — not a one-time download",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** One-click download + refine of an auto-ingestable source into the local catalog. */
function IngestControls({
  source,
  model,
  libraries,
  onIngested,
}: {
  source: DataSource;
  model: Portfolio;
  libraries: Libraries;
  onIngested: () => void;
}) {
  const climate = libraries.scenarios.climate;
  const years = libraries.scenarios.anchor_years;
  const [scenario, setScenario] = useState(model.scenario.climate || climate[0]?.id || "rcp85");
  const [year, setYear] = useState(
    model.scenario.anchor_years?.[model.scenario.anchor_years.length - 1] ??
      years[years.length - 1] ??
      2050,
  );
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);

  const noAssets = model.assets.length === 0;

  async function run() {
    setStatus("running");
    setMsg(null);
    try {
      let r: Run = await submitIngest(model.id, {
        source: source.fetch!.source!,
        peril: source.fetch!.peril,
        scenario,
        year,
      });
      for (let i = 0; i < 150 && (r.status === "queued" || r.status === "running"); i++) {
        await sleep(2000);
        r = await getRun(model.id, r.id);
      }
      const out = r.output as IngestResult | null;
      if (r.status === "done" && out?.status === "ok") {
        setStatus("done");
        setMsg(out.detail ?? "Ingested into the local catalog.");
        onIngested();
      } else {
        setStatus("error");
        setMsg(out?.detail ?? r.detail ?? "Ingest failed — see worker log.");
      }
    } catch (e) {
      setStatus("error");
      setMsg(String(e));
    }
  }

  return (
    <div
      style={{
        marginTop: 8,
        padding: "8px 10px",
        background: "var(--surface-2, rgba(255,255,255,0.03))",
        borderRadius: 6,
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        alignItems: "center",
      }}
    >
      <span className="pill" style={{ color: "var(--accent)" }}>
        ⤓ auto-ingest
      </span>
      <label className="hint">
        scenario{" "}
        <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
          {climate.map((c) => (
            <option key={c.id} value={c.id}>
              {formatScenario(c.id)}
            </option>
          ))}
        </select>
      </label>
      <label className="hint">
        year{" "}
        <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </label>
      <button
        className="btn"
        onClick={run}
        disabled={status === "running" || noAssets}
        title={noAssets ? "Place an asset on the Map first" : "Download + refine into the catalog"}
      >
        {status === "running" ? "Fetching…" : "Fetch & ingest"}
      </button>
      {noAssets && <span className="hint">place an asset on the Map first</span>}
      {msg && (
        <span
          className="hint"
          style={{
            flexBasis: "100%",
            color:
              status === "error"
                ? "var(--danger)"
                : status === "done"
                  ? "var(--accent)"
                  : "var(--muted)",
          }}
        >
          {status === "done" ? "✓ " : status === "error" ? "✕ " : ""}
          {msg}
        </span>
      )}
    </div>
  );
}

/** Direct one-click download of a curated open file into its destination (no CLIMADA refine). */
function DownloadControls({ source }: { source: DataSource }) {
  const templated = (source.download_url ?? "").includes("{");
  const [country, setCountry] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const destLabel =
    source.dest === "climada"
      ? "~/climada/data"
      : source.dest === "catalog"
        ? "hazard catalog"
        : "data/downloads";

  async function run() {
    setStatus("running");
    setMsg(null);
    try {
      const out: OpenDataFetchResult = await fetchOpenData(
        source.id,
        templated ? country.trim() : undefined,
      );
      setStatus(out.status === "ok" ? "done" : "error");
      setMsg(out.detail ?? (out.status === "ok" ? "Downloaded." : "Download failed."));
    } catch (e) {
      setStatus("error");
      setMsg(String(e));
    }
  }

  return (
    <div
      style={{
        marginTop: 8,
        padding: "8px 10px",
        background: "var(--surface-2, rgba(255,255,255,0.03))",
        borderRadius: 6,
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        alignItems: "center",
      }}
    >
      <span className="pill" style={{ color: "var(--accent)" }}>
        ⤓ download → {destLabel}
      </span>
      {templated && (
        <label className="hint">
          ISO3{" "}
          <input
            value={country}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
            placeholder="e.g. KOR"
            maxLength={3}
            style={{ width: 64, textTransform: "uppercase" }}
          />
        </label>
      )}
      <button
        className="btn"
        onClick={run}
        disabled={status === "running" || (templated && country.trim().length !== 3)}
        title={
          templated && country.trim().length !== 3
            ? "Enter a 3-letter ISO country code"
            : `Download into ${destLabel}`
        }
      >
        {status === "running" ? "Downloading…" : "Download here"}
      </button>
      {msg && (
        <span
          className="hint"
          style={{
            flexBasis: "100%",
            color:
              status === "error"
                ? "var(--danger)"
                : status === "done"
                  ? "var(--accent)"
                  : "var(--muted)",
          }}
        >
          {status === "done" ? "✓ " : status === "error" ? "✕ " : ""}
          {msg}
        </span>
      )}
    </div>
  );
}

function SourceRow({
  s,
  model,
  libraries,
  onIngested,
}: {
  s: DataSource;
  model: Portfolio;
  libraries: Libraries;
  onIngested: () => void;
}) {
  const auto = s.fetch?.mode === "auto";
  const autoDownload = s.fetch?.mode === "auto-download";
  const modeLabel =
    s.fetch && s.fetch.mode !== "auto" && s.fetch.mode !== "auto-download"
      ? FETCH_LABEL[s.fetch.mode]
      : null;
  return (
    <div style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", justifyContent: "space-between" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>
            {s.name}{" "}
            {s.required && (
              <span className="pill" style={{ color: "var(--danger)" }}>
                required
              </span>
            )}
            {auto && (
              <span className="pill" style={{ color: "var(--accent)" }}>
                one-click
              </span>
            )}
            {autoDownload && (
              <span className="pill" style={{ color: "var(--accent)" }}>
                direct download
              </span>
            )}
          </div>
          <div className="hint" style={{ marginTop: 2 }}>
            {s.for}
            {s.scenarios && ` · scenarios: ${s.scenarios}`}
            {s.place_at && (
              <>
                {" · place at "}
                <code>{s.place_at}</code>
              </>
            )}
          </div>
          <div style={{ marginTop: 4 }}>
            <span className="pill" style={{ color: accessColor(s.access) }}>
              {s.access}
            </span>{" "}
            <span className="pill">{s.license}</span>
            {modeLabel && <span className="pill">{modeLabel}</span>}
          </div>
          {s.notes && (
            <div className="hint" style={{ marginTop: 3 }}>
              {s.notes}
            </div>
          )}
        </div>
        <a
          className="btn secondary"
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ textDecoration: "none", whiteSpace: "nowrap", flexShrink: 0 }}
        >
          {auto || autoDownload ? "Source ↗" : "Download ↗"}
        </a>
      </div>
      {auto && (
        <IngestControls source={s} model={model} libraries={libraries} onIngested={onIngested} />
      )}
      {autoDownload && <DownloadControls source={s} />}
    </div>
  );
}

export function DataView({ model, libraries }: { model: Portfolio; libraries: Libraries }) {
  const [catalog, setCatalog] = useState<HazardCatalog | null>(null);
  const refreshCatalog = () => {
    getHazardCatalog()
      .then(setCatalog)
      .catch(() => setCatalog(null));
  };
  useEffect(refreshCatalog, []);

  // 현황 체크 — opened by the button, or on arrival from the launcher (?status=1).
  const [status, setStatus] = useState<KoreaStatus | null | "loading" | "error">(null);
  const runStatusCheck = () => {
    setStatus("loading");
    getKoreaStatus()
      .then(setStatus)
      .catch(() => setStatus("error"));
  };
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("status") === "1") runStatusCheck();
  }, []);

  const { categories, sources } = libraries.data_sources;

  return (
    <div className="panelview">
      <h2>Data sources &amp; catalog</h2>

      <div className="card" id="korea-status">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0 }}>현황 체크 — 한국 1 km 준비 상태</h3>
          <button className="btn secondary" onClick={runStatusCheck} disabled={status === "loading"}>
            {status === "loading" ? "확인 중…" : status && status !== "error" ? "다시 확인" : "현황 체크"}
          </button>
        </div>
        <p className="hint">
          디스크·카탈로그·환경변수를 <b>읽기만</b> 합니다 — CLIMADA 파싱 없음, 값 추정 없음, 키 값 미표시.
          등급은 <code>docs/KOREA_1KM_IMPLEMENTATION_GAP.md</code>의 정의를 따르며 <code>1km-ready</code>는
          메타데이터 조건이 충족되기 전까지 나올 수 없습니다.
        </p>
        {status === "error" && <div className="status-box">상태를 읽지 못했습니다 — 백엔드가 켜져 있는지 확인하세요.</div>}
        {status && status !== "loading" && status !== "error" && <KoreaStatusPanel s={status} />}
      </div>
      <p className="hint">
        Download links for the public datasets each capability needs. Sources marked{" "}
        <span className="pill" style={{ color: "var(--accent)" }}>
          one-click
        </span>{" "}
        can be <strong>downloaded &amp; refined into a CLIMADA-ready hazard</strong> right here
        ("Fetch &amp; ingest") — scoped to your portfolio's region and written to the local catalog
        below, so runs use it automatically. The rest link out (login-gated, live feeds, or large
        drop-ins).
      </p>

      {categories.map((cat) => {
        const rows = sources.filter((s) => s.category === cat.id);
        if (rows.length === 0) return null;
        return (
          <div className="card" key={cat.id}>
            <div className="section-title">{cat.label}</div>
            {cat.note && <p className="hint">{cat.note}</p>}
            {rows.map((s) => (
              <SourceRow
                key={s.id}
                s={s}
                model={model}
                libraries={libraries}
                onIngested={refreshCatalog}
              />
            ))}
          </div>
        );
      })}

      <div className="card">
        <div className="section-title">Local hazard catalog (perils database)</div>
        <p className="hint">
          CLIMADA-ready hazards already ingested locally (used before the Data API). Add more with
          "Fetch &amp; ingest" above, or via <code>scripts/build_hazard.py</code>.
        </p>
        {catalog === null ? (
          <p className="hint">Loading…</p>
        ) : catalog.entries.length === 0 ? (
          <p className="hint">
            Empty — <code>{catalog.dir}</code>. Nothing ingested yet.
          </p>
        ) : (
          <table className="source-table">
            <thead>
              <tr>
                <th>Peril</th>
                <th>Scenario</th>
                <th>Region</th>
                <th>Year</th>
                <th>Events × cent.</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {catalog.entries.map((e, i) => (
                <tr key={i}>
                  <td>{e.peril.replace(/_/g, " ")}</td>
                  <td>{formatScenario(e.climate_scenario)}</td>
                  <td>{e.region}</td>
                  <td>{e.year ?? "—"}</td>
                  <td className="num">
                    {e.n_events} × {e.n_centroids}
                  </td>
                  <td>{e.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function KoreaStatusPanel({ s }: { s: KoreaStatus }) {
  const grade = (g: string) =>
    g === "5km-current" ? "var(--accent)" : g === "1km-data-only" ? "var(--warn, #b7791f)" : "var(--muted, #888)";
  return (
    <div>
      <h4>KMA 남한상세 파일 · {s.kma.dir}</h4>
      {!s.kma.dir_exists && <p className="hint">폴더가 없습니다.</p>}
      <p className="hint">
        제공 변수 {s.kma.variables_offered.join(" · ")} — 코드가 소비: <b>{s.kma.variables_consumed_by_code.join(", ")}</b>
        {" "}— 디스크에 있음: <b>{s.kma.variables_present.join(", ") || "없음"}</b>
      </p>
      {s.kma.files.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>파일</th><th>변수</th><th>시나리오</th><th>기간</th><th>형식</th><th>MB</th></tr></thead>
            <tbody>
              {s.kma.files.map((f) => (
                <tr key={f.file}>
                  <td><code>{f.file}</code></td><td>{f.variable}</td><td>{f.scenario_token}</td>
                  <td>{f.years[0]}–{f.years[1]}</td><td>{f.format} ({f.kind})</td><td>{f.size_mb}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4>KOR 카탈로그 레이어 ({s.catalog.kor_layers.length})</h4>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>페릴</th><th>요청 시나리오</th><th>제공 시나리오</th><th>센트로이드</th><th>등급</th><th>비고</th></tr>
          </thead>
          <tbody>
            {s.catalog.kor_layers.map((l) => (
              <tr key={l.file}>
                <td><code>{l.peril}</code></td>
                <td>{l.requested_scenario}</td>
                <td style={{ color: l.scenario_mismatch ? "var(--danger, #c0392b)" : undefined }}>
                  {l.served_scenario ?? "—"}{l.scenario_mismatch ? " ≠ 요청" : ""}
                </td>
                <td>{l.n_centroids ?? "—"}</td>
                <td><span className="pill" style={{ color: grade(l.grade === "not-kma" ? "missing" : l.grade) }}>
                  {l.grade === "not-kma" ? "not KMA" : l.grade}
                </span></td>
                <td className="hint">
                  {l.stored_resolution_note ?? ""}
                  {l.label_variable_conflict ? " · 라벨 Tmax / 실제 TA" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>1 km 등급</h4>
      <p className="hint">{s.one_km.rule}</p>
      <p>
        {Object.entries(s.one_km.grades).map(([peril, g]) => (
          <span key={peril} className="pill" style={{ color: grade(g), marginRight: 6 }}>
            {peril}: {g}
          </span>
        ))}
        {s.one_km.ready.length === 0 && <span className="pill"><b>1km-ready: 0</b></span>}
      </p>

      <h4>자격증명 · 환경</h4>
      <ul className="hint">
        {s.credentials.map((c) => (
          <li key={c.env}>
            <code>{c.env}</code> — {c.present ? `있음 (${c.length}자)` : "없음"} · {c.purpose}
          </li>
        ))}
        <li>CLIMADA 워커 환경: {s.environment.worker_env_present ? "있음" : "없음"} · git: <code>{s.environment.git_branch ?? "?"}</code></li>
      </ul>
    </div>
  );
}
