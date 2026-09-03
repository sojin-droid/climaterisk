// Thin REST client. The browser persists only the session id; the whole model
// is synced back to the backend (which owns it).

import type {
  FinanceResult,
  HazardCatalog,
  IngestSource,
  Libraries,
  MeasureSpec,
  OpenDataFetchResult,
  Portfolio,
  Run,
  TransitionResult,
} from "../types";

const SESSION_KEY = "climaterisk.sessionId";

async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    throw new Error(`${init?.method ?? "GET"} ${url} → ${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** Fetch the existing session (from localStorage) or create a fresh one. */
export async function ensureSession(): Promise<Portfolio> {
  const existing = localStorage.getItem(SESSION_KEY);
  if (existing) {
    try {
      return await http<Portfolio>(`/api/session/${existing}`);
    } catch {
      localStorage.removeItem(SESSION_KEY); // stale id (e.g. backend cleared)
    }
  }
  const created = await http<Portfolio>("/api/session", { method: "POST" });
  localStorage.setItem(SESSION_KEY, created.id);
  return created;
}

/** Persist the whole portfolio model (full-model sync). */
export async function saveModel(model: Portfolio): Promise<Portfolio> {
  return http<Portfolio>(`/api/session/${model.id}`, {
    method: "PUT",
    body: JSON.stringify(model),
  });
}

export async function getLibraries(): Promise<Libraries> {
  return http<Libraries>("/api/libraries");
}

export async function getHazardCatalog(): Promise<HazardCatalog> {
  return http<HazardCatalog>("/api/hazard-catalog");
}

/** Submit a hazard-layer raster preview for a catalog entry (peril/scenario/region/year).
 *  Optional bbox [south, west, north, east] crops the render to that window. */
export async function submitHazardPreview(
  sessionId: string,
  peril: string,
  scenario: string,
  region: string,
  year: number | null,
  bbox?: [number, number, number, number],
): Promise<Run> {
  const qs = new URLSearchParams({ peril, scenario, region });
  if (year != null) qs.set("year", String(year));
  if (bbox) qs.set("bbox", bbox.map((v) => v.toFixed(5)).join(","));
  return http<Run>(`/api/session/${sessionId}/hazard-preview?${qs.toString()}`, { method: "POST" });
}

/** URL of the rendered hazard-preview PNG for a finished preview run. */
export function hazardPreviewImageUrl(sessionId: string, runId: string): string {
  return `/api/session/${sessionId}/run/${runId}/preview.png`;
}

/** Climate Risk Premium for a finished physical run (synchronous; reads the portfolio's
 *  financial profile). transitionCost is the annual carbon cost to add to the climate shock. */
export async function computeFinance(
  sessionId: string,
  runId: string,
  transitionCost = 0,
): Promise<FinanceResult> {
  const qs = `run_id=${encodeURIComponent(runId)}&transition_cost=${transitionCost}`;
  return http<FinanceResult>(`/api/session/${sessionId}/finance?${qs}`, { method: "POST" });
}

export async function submitRun(sessionId: string): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/run`, { method: "POST" });
}

export async function getRun(sessionId: string, runId: string): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/run/${runId}`);
}

/**
 * The most recent finished run of each kind for a session.
 *
 * Runs live on the server; the browser only remembers ids it submitted this page-load, so
 * this is what lets a reloaded page re-attach to completed results instead of blanking.
 */
export async function getLatestRuns(sessionId: string): Promise<Record<string, Run>> {
  return http<Record<string, Run>>(`/api/session/${sessionId}/latest-runs`);
}

export async function submitTransition(sessionId: string): Promise<TransitionResult> {
  return http<TransitionResult>(`/api/session/${sessionId}/transition`, { method: "POST" });
}

export async function submitCostBenefit(sessionId: string, measures: MeasureSpec[]): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/cost-benefit`, {
    method: "POST",
    body: JSON.stringify(measures),
  });
}

export async function submitUncertainty(sessionId: string, nSamples = 50): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/uncertainty?n_samples=${nSamples}`, {
    method: "POST",
  });
}

export async function submitLitPop(
  sessionId: string,
  country: string,
  source = "litpop",
  peril = "tropical_cyclone",
): Promise<Run> {
  const qs =
    `country=${encodeURIComponent(country)}&source=${encodeURIComponent(source)}` +
    `&peril=${encodeURIComponent(peril)}`;
  return http<Run>(`/api/session/${sessionId}/litpop?${qs}`, { method: "POST" });
}

export async function submitSupplyChain(
  sessionId: string,
  mriotType = "WIOD16",
  mriotYear = 2010,
): Promise<Run> {
  return http<Run>(
    `/api/session/${sessionId}/supplychain?mriot_type=${mriotType}&mriot_year=${mriotYear}`,
    { method: "POST" },
  );
}

export async function submitCalibration(sessionId: string): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/calibration`, { method: "POST" });
}

export async function submitForecast(sessionId: string): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/forecast`, { method: "POST" });
}

export interface IngestBody {
  source: IngestSource;
  peril?: string;
  scenario?: string;
  year?: number;
}

export async function submitIngest(sessionId: string, body: IngestBody): Promise<Run> {
  return http<Run>(`/api/session/${sessionId}/ingest`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Download a curated open-data source by registry id into its declared destination. */
export async function fetchOpenData(
  sourceId: string,
  country?: string,
): Promise<OpenDataFetchResult> {
  const qs = new URLSearchParams({ source_id: sourceId });
  if (country) qs.set("country", country);
  return http<OpenDataFetchResult>(`/api/data/fetch?${qs.toString()}`, { method: "POST" });
}
