import { useCallback, useEffect, useRef, useState } from "react";
import { ensureSession, getLibraries, saveModel } from "./lib/api";
import { useResults } from "./lib/useResults";
import type { Libraries, Portfolio } from "./types";
import { ActivityBar, VIEW_IDS, type ViewId } from "./layout/ActivityBar";
import { MapView } from "./views/MapView";
import { ScenariosView } from "./views/ScenariosView";
import { ResultsView } from "./views/ResultsView";
import { AdaptationView } from "./views/AdaptationView";
import { FinanceView } from "./views/FinanceView";
import { SupplyChainView } from "./views/SupplyChainView";
import { ForecastView } from "./views/ForecastView";
import { VulnerabilityView } from "./views/VulnerabilityView";
import { DataView } from "./views/DataView";
import { MethodView } from "./views/MethodView";

export function App() {
  const [model, setModel] = useState<Portfolio | null>(null);
  const [libraries, setLibraries] = useState<Libraries | null>(null);
  // A launcher may land the user on a specific tab (Physical Risk.app → 현황 체크 opens
  // ?view=data&status=1). Anything not a known ViewId falls back to the map.
  const [view, setView] = useState<ViewId>(() => {
    const wanted = new URLSearchParams(window.location.search).get("view");
    return wanted && VIEW_IDS.includes(wanted as ViewId) ? (wanted as ViewId) : "map";
  });
  const [sync, setSync] = useState<"idle" | "saving" | "error">("idle");
  const initialLoad = useRef(true);
  const saveTimer = useRef<number | undefined>(undefined);

  // Bootstrap: load libraries + the session model, dropping any unsupported perils
  // that a previous session may have persisted (e.g. river_flood before Phase 2+).
  useEffect(() => {
    (async () => {
      const [libs, portfolio] = await Promise.all([getLibraries(), ensureSession()]);
      const supported = new Set(
        libs.perils.perils.filter((p) => p.supported_mvp).map((p) => p.id),
      );
      const kept = portfolio.run_config.perils.filter((p) => supported.has(p));
      const perils = kept.length > 0 ? kept : ["tropical_cyclone"];
      const changed = perils.length !== portfolio.run_config.perils.length;
      const model = changed
        ? { ...portfolio, run_config: { ...portfolio.run_config, perils } }
        : portfolio;
      setLibraries(libs);
      setModel(model);
      if (changed) saveModel(model).catch(() => {});
    })().catch((e) => {
      console.error("bootstrap failed", e);
    });
  }, []);

  // Debounced autosave on any model change (skip the initial load).
  useEffect(() => {
    if (!model) return;
    if (initialLoad.current) {
      initialLoad.current = false;
      return;
    }
    window.clearTimeout(saveTimer.current);
    setSync("saving");
    saveTimer.current = window.setTimeout(() => {
      saveModel(model)
        .then(() => setSync("idle"))
        .catch(() => setSync("error"));
    }, 600);
    return () => window.clearTimeout(saveTimer.current);
  }, [model]);

  const patchModel = useCallback((patch: Partial<Portfolio>) => {
    setModel((m) => (m ? { ...m, ...patch } : m));
  }, []);

  // Run state + polling live here so results survive tab switches.
  const results = useResults(model?.id ?? "");

  if (!model || !libraries) {
    return (
      <div className="empty-state" style={{ paddingTop: "20vh" }}>
        <div className="empty-icon">
          <span className="spinner" style={{ width: 28, height: 28, borderWidth: 3 }} />
        </div>
        <div className="empty-title">Loading climaterisk…</div>
        <div className="empty-hint">Restoring your portfolio and methodology libraries.</div>
      </div>
    );
  }

  return (
    <div className="app">
      <ActivityBar view={view} onChange={setView} />
      <div className="workspace">
        <div className="topbar">
          <input
            className="title"
            value={model.name}
            onChange={(e) => patchModel({ name: e.target.value })}
            spellCheck={false}
          />
          <span className="meta">
            {model.assets.length} asset{model.assets.length === 1 ? "" : "s"} · {model.depth_level}
          </span>
          <span className={`sync ${sync === "saving" ? "saving" : ""}`}>
            {sync === "saving" ? "saving…" : sync === "error" ? "save failed" : "saved"}
          </span>
        </div>
        <div className="content">
          {view === "map" && (
            <MapView
              model={model}
              libraries={libraries}
              patchModel={patchModel}
              litpopRun={results.litpopRun}
              litpopBusy={results.litpopBusy}
              litpopErr={results.litpopErr}
              onRunLitpop={results.runLitpop}
            />
          )}
          {view === "scenarios" && (
            <ScenariosView model={model} libraries={libraries} patchModel={patchModel} />
          )}
          {view === "vulnerability" && (
            <VulnerabilityView
              model={model}
              libraries={libraries}
              patchModel={patchModel}
              calRun={results.calRun}
              calBusy={results.calBusy}
              calErr={results.calErr}
              onCalibrate={results.runCalibration}
            />
          )}
          {view === "results" && (
            <ResultsView
              model={model}
              run={results.physRun}
              transition={results.transition}
              busy={results.physBusy}
              error={results.physErr}
              onRun={results.runPhysical}
              uncRun={results.uncRun}
              uncBusy={results.uncBusy}
              uncErr={results.uncErr}
              onRunUncertainty={results.runUncertainty}
              cbRunId={results.cbRun?.id}
              scRunId={results.scRun?.id}
              fcRunId={results.fcRun?.id}
              calRunId={results.calRun?.id}
            />
          )}
          {view === "adaptation" && (
            <AdaptationView
              model={model}
              measures={results.cbMeasures}
              setMeasures={results.setCbMeasures}
              run={results.cbRun}
              busy={results.cbBusy}
              error={results.cbErr}
              onRun={results.runCostBenefit}
            />
          )}
          {view === "finance" && (
            <FinanceView
              model={model}
              libraries={libraries}
              patchModel={patchModel}
              physRun={results.physRun}
              transition={results.transition}
            />
          )}
          {view === "supplychain" && (
            <SupplyChainView
              model={model}
              run={results.scRun}
              busy={results.scBusy}
              error={results.scErr}
              onRun={results.runSupplyChain}
            />
          )}
          {view === "forecast" && (
            <ForecastView
              model={model}
              run={results.fcRun}
              busy={results.fcBusy}
              error={results.fcErr}
              onRun={results.runForecast}
            />
          )}
          {view === "data" && <DataView model={model} libraries={libraries} />}
          {view === "method" && <MethodView libraries={libraries} />}
        </div>
      </div>
    </div>
  );
}
