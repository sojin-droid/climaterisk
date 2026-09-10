// Mirror of the backend domain model (src/climaterisk/core/entities.py).

export type DepthLevel = "asset" | "portfolio" | "national";
export type GeographicScale = "point" | "footprint" | "regional" | "national";

export type ExposureSource = "points" | "litpop" | "osm" | "crop";

export interface Asset {
  id: string;
  name: string;
  lat: number;
  lon: number;
  sector: string;
  geographic_scale: GeographicScale;
  value: number;
  currency: string;
  headcount?: number | null; // people on site; exposure for health perils (heat_mortality)
  annual_emissions_tco2e: number | null;
  vulnerability_class: string | null;
  geometry?: Record<string, unknown> | null; // GeoJSON footprint (Polygon/MultiPolygon)
  properties: Record<string, string | number | boolean>;
  financial_profile?: FinancialProfile | null; // per-asset CRP override
}

export interface Scenario {
  climate: string;
  transition: string;
  anchor_years: number[];
}

export interface RatingThreshold {
  dscr_min: number;
  rating: string;
}
export interface FinancialProfile {
  capex?: number | null;
  annual_ebitda?: number | null;
  horizon_years?: number | null;
  debt_fraction?: number | null;
  debt_tenor_years?: number | null;
  risk_free_rate?: number | null;
  baseline_spread_bps?: number | null;
  baseline_equity_rate?: number | null;
  rating_method?: string | null; // single/primary id (back-compat)
  rating_methods?: string[] | null; // selected ids to compare; first is primary
  custom_rating_thresholds?: RatingThreshold[] | null;
  // Asset financial model: "generic" (default) or "power_gen" (power plant).
  financial_model?: string | null;
  // power_gen generation economics
  capacity_mw?: number | null;
  power_price?: number | null;
  capacity_factor?: number | null;
  plant_fuel?: string | null;
  fixed_opex?: number | null;
  opex_per_mwh?: number | null;
  // power_gen stressed-scenario channel magnitudes (fractions 0..1)
  dispatch_penalty?: number | null;
  outage_rate?: number | null;
  capacity_derate?: number | null;
  efficiency_loss?: number | null;
}
export interface RunConfig {
  perils: string[];
  discount_rate: number;
  exposure_source?: ExposureSource;
  options: Record<string, string | number | boolean>;
  financial_profile?: FinancialProfile | null;
}
export interface FinanceOutcome {
  npv: number;
  irr: number | null;
  min_dscr: number;
  rating: string;
  spread_bps: number;
  wacc: number;
}
export interface FinanceScenario {
  baseline: FinanceOutcome;
  stressed: FinanceOutcome;
  crp_bps: number;
  npv_loss: number;
  npv_loss_pct_capex: number;
  downgrade: boolean;
  annual_climate_loss: number;
}
export interface FinanceAssetResult extends FinanceScenario {
  id: string;
  name: string;
  model?: string | null;
}
export interface PowerGenBreakdown {
  model: string;
  cf_baseline: number;
  cf_effective: number;
  generation_mwh_baseline: number;
  generation_mwh_stressed: number;
  revenue_baseline: number;
  revenue_stressed: number;
  carbon_cost: number;
  annual_aai: number;
  channels: {
    dispatch_penalty: number;
    outage_rate: number;
    capacity_derate: number;
    efficiency_loss: number;
  };
}
export interface FinanceMethodComparison {
  method: string;
  label: string;
  code: string;
  source: string;
  scenario: FinanceScenario;
}
export interface FinanceResult {
  currency: string;
  total_physical_aai: number;
  transition_annual_cost: number;
  rating_method: string;
  rating_method_label: string;
  rating_method_source: string;
  rating_thresholds: RatingThreshold[];
  methods_compared: FinanceMethodComparison[];
  financial_model?: string | null;
  portfolio_breakdown?: PowerGenBreakdown | { model: string; annual_climate_loss: number };
  portfolio: FinanceScenario;
  per_asset: FinanceAssetResult[];
  detail: string;
}

export interface VulnerabilityOverride {
  tc_v_half?: number;
  wf_max_mdd?: number;
  flood_mdr?: number[];
  eq_mdr?: number[];
}

export interface Portfolio {
  id: string;
  name: string;
  depth_level: DepthLevel;
  assets: Asset[];
  scenario: Scenario;
  run_config: RunConfig;
  vulnerability_overrides?: Record<string, VulnerabilityOverride>;
}

// Bundled libraries (assets/libraries/*.json), as served by /api/libraries.
export interface SectorOption {
  id: string;
  label: string;
  default_vulnerability_class: string;
  emission_intensity_tco2e_per_musd: number;
}
export interface PerilOption {
  id: string;
  label: string;
  supported_mvp: boolean;
  future_source?: string;
  reason?: string;
  historical_only?: boolean;
  coverage?: string;
}
export interface ScenarioOption {
  id: string;
  label: string;
}
export interface VulnerabilityClass {
  id: string;
  label: string;
  tc_v_half: number;
  wf_max_mdd: number;
  flood_mdr: number[];
  eq_mdr: number[];
}

export interface DataSourceCategory {
  id: string;
  label: string;
  note?: string;
}
export type FetchMode = "auto" | "auto-download" | "manual" | "needs_login" | "operational";
export type IngestSource = "dataapi" | "aqueduct" | "copdem" | "tctracks" | "tcrain";
export interface DataSourceFetch {
  mode: FetchMode;
  source?: IngestSource;
  peril?: string;
}
export interface DataSource {
  id: string;
  category: string;
  name: string;
  url: string;
  access: string;
  license: string;
  for: string;
  scenarios?: string;
  place_at?: string;
  required?: boolean;
  fetch?: DataSourceFetch;
  download_url?: string;
  dest?: string;
  notes?: string;
}

export interface OpenDataFetchResult {
  status: "ok" | "error";
  source?: string;
  dest?: string;
  path?: string;
  bytes?: number;
  extracted?: string[];
  detail?: string;
}
export interface DataSourcesLib {
  categories: DataSourceCategory[];
  sources: DataSource[];
}

export interface ImpfPreset {
  id: string;
  peril: "tc" | "flood" | "eq";
  label: string;
  tc_v_half?: number;
  flood_mdr?: number[];
  eq_mdr?: number[];
  provenance: string;
}

export interface Libraries {
  sectors: { sectors: SectorOption[] };
  perils: { perils: PerilOption[] };
  scenarios: {
    climate: ScenarioOption[];
    transition: ScenarioOption[];
    anchor_years: number[];
  };
  impact_functions: { classes: VulnerabilityClass[]; flood_depth_m: number[]; eq_mmi: number[] };
  impf_presets?: { presets: ImpfPreset[] };
  finance_reference?: {
    rating_scale?: string[];
    rating_dscr_thresholds: { dscr_min: number; rating: string; source: string }[];
    default_rating_method?: string;
    rating_methods?: Record<
      string,
      {
        label: string;
        short?: string;
        code?: string;
        source: string;
        note?: string;
        thresholds: RatingThreshold[];
      }
    >;
    rating_spreads_bps: { rating: string; spread_bps: number; source: string }[];
    financing_defaults: Record<string, { value: number; source: string }>;
  };
  finance_channels?: {
    generation_defaults?: {
      capacity_factor_by_fuel?: Record<string, number>;
      opex_per_mwh?: { value?: number; source?: string };
    };
    channels?: {
      dispatch?: {
        trajectories?: Record<string, { label?: string; status?: string; source?: string }>;
      };
      efficiency?: { loss_per_degc?: { value?: number; source?: string } };
      outage?: { source_lambda?: string };
      water_derate?: { source?: string };
    };
  };
  carbon_prices?: { prices: Record<string, Record<string, number>> };
  data_sources: DataSourcesLib;
}

export interface HazardCatalogEntry {
  peril: string;
  haz_type: string;
  climate_scenario: string;
  region: string;
  year: number | null;
  units: string;
  n_events: number;
  n_centroids: number;
  source: string;
  license: string;
}
export interface HazardCatalog {
  dir: string;
  entries: HazardCatalogEntry[];
}

// Run results (mirror of src/climaterisk/engines/base.py + runs/store.py).
export interface AssetImpact {
  id: string;
  lat: number;
  lon: number;
  eai: number;
  country: string | null;
}
export interface FreqCurve {
  return_periods: number[];
  impact: number[];
  /** Applied cap (half the record); longer periods were dropped, not extrapolated. */
  max_resolvable_return_period?: number | null;
  /** Length of the event record behind that cap, in years. */
  record_years?: number | null;
}
export interface Yearset {
  n_years: number;
  mean: number;
  p50: number;
  p90: number;
  p95: number;
  p99: number;
  max: number;
  losses: number[];
}
export interface WarnLevels {
  n_levels: number;
  counts: number[];
  thresholds: number[];
  unit: string;
}
export interface HazardPreviewResult {
  status: string;
  peril: string;
  scenario?: string;
  region?: string;
  unit: string;
  vmin: number;
  vmax: number;
  colormap?: string;
  bounds: [[number, number], [number, number]]; // [[south,west],[north,east]]
  n_centroids?: number;
  detail: string | null;
}
export interface PhysicalRunResult {
  peril: string;
  status: string;
  target_year: number | null;
  aai_agg: number;
  present_aai_agg: number | null;
  delta_pct: number | null;
  total_value: number;
  per_asset: AssetImpact[];
  freq_curve: FreqCurve | null;
  yearset?: Yearset | null;
  warn_levels?: WarnLevels | null;
  result_kind?: "monetary" | "yield" | "productivity" | "mortality";
  metric_unit?: string | null;
  // Scientific status of the peril's vulnerability model, when the worker reports one
  // (heat mortality: a climaterisk custom curve whose parameters are indicative).
  parameter_status?: {
    model: string;
    label: string;
    detail: string;
    calibrated_on_observed_mortality?: boolean;
    n_parameters?: number;
    counts?: Record<string, number>;
  } | null;
  interpretation?: string | null;
  detail: string | null;
}
export interface PhysicalRunOutput {
  status: string;
  climate_scenario: string;
  results: PhysicalRunResult[];
  detail: string | null;
}
// Adaptation cost-benefit (mirror of engines/base.py + worker/cost_benefit.py).
export interface MeasureSpec {
  name: string;
  cost: number;
  damage_reduction: number; // 0..1
  hazard_freq_cutoff?: number;
  risk_transf_attach?: number;
  risk_transf_cover?: number;
}
export interface MeasureResult {
  name: string;
  cost: number;
  benefit: number;
  benefit_cost_ratio: number | null;
}
export interface CostBenefitResult {
  status: string;
  peril: string;
  future_year: number | null;
  discount_rate: number;
  currency: string;
  tot_climate_risk: number;
  measures: MeasureResult[];
  detail: string | null;
}

// Monte-Carlo uncertainty (mirror of engines/base.py + worker/uncertainty.py).
export interface UncertaintyResult {
  status: string;
  peril: string;
  future_year: number | null;
  n_samples: number;
  currency: string;
  aai_mean: number;
  aai_std: number;
  aai_p5: number;
  aai_p50: number;
  aai_p95: number;
  distribution: number[];
  sensitivity: Record<string, number>;
  sensitivity_s1?: Record<string, number>;
  sensitivity_st?: Record<string, number>;
  sensitivity_method?: string;
  present_aai?: number | null;
  delta_mean?: number | null;
  delta_p5?: number | null;
  delta_p95?: number | null;
  // Method transparency: SALib Sobol wrapper around ImpactCalc (not climada unsequa), TC only.
  method?: string | null;
  scope?: string | null;
  bounds?: Record<string, number[]>;
  bounds_provenance?: string | null;
  frequency_treatment?: string | null;
  seed?: number | null;
  detail: string | null;
}

// LitPop modeled exposure (mirror of engines/base.py + worker/litpop.py).
export interface LitPopResult {
  status: string;
  interpretation?: string | null;
  country: string;
  exposure_source?: string;
  source_label?: string;
  peril: string;
  future_year: number | null;
  total_value: number;
  aai_agg: number;
  n_points: number;
  currency: string;
  per_point: { lat: number; lon: number; eai: number }[];
  detail: string | null;
}

// Data ingestion (mirror of engines/base.py IngestResult + worker/ingest.py).
export interface IngestResult {
  status: string;
  source: string;
  peril: string;
  entry: HazardCatalogEntry | null;
  detail: string | null;
}

export interface SupplyChainSector {
  sector: string;
  indirect: number;
}
export interface SupplyChainResult {
  status: string;
  mriot: string;
  currency: string;
  total_direct: number;
  total_indirect: number;
  amplification: number | null;
  by_sector: SupplyChainSector[];
  detail: string | null;
}

export interface CalibrationResult {
  status: string;
  peril: string;
  country: string;
  param: string;
  initial: number;
  calibrated: number;
  observed_annual_loss: number;
  // Provenance (optional for older results) — mirrors engines/base.py CalibrationResult.
  fit_status?: string | null; // "fitted" (never "validated" from this runner)
  observed_source?: string | null;
  observed_period?: number[];
  n_observed_events?: number | null;
  hazard?: string | null;
  objective?: string | null;
  method?: string | null;
  bounds?: number[];
  modelled_annual_loss_at_calibrated?: number | null;
  calibrated_at?: string | null;
  schema_version?: number | null;
  persisted_to?: string | null;
  applies_when?: string | null;
  detail: string | null;
  // Comparability metadata (worker, 2026-09-07). A run can finish with status "error" because the
  // gate refused to fit, or with status "ok" and comparison_status "not_comparable" when the
  // caller opted in deliberately. Read by lib/calibration.ts.
  comparison_status?: string | null; // comparable | not_comparable | unknown
  blockers?: string[]; // one entry per failed comparability check
  observed_unit?: string | null;
  observed_currency?: string | null;
  observed_price_basis?: string | null;
  target_covers_subperils?: string[];
  model_covers_subperils?: string[];
}

export interface ForecastResult {
  status: string;
  peril: string;
  n_tracks: number;
  total_impact: number;
  per_asset: AssetImpact[];
  detail: string | null;
}

export interface Run {
  id: string;
  session_id: string;
  status: "queued" | "running" | "done" | "error";
  climate_scenario: string;
  perils: string[];
  output:
    | PhysicalRunOutput
    | CostBenefitResult
    | UncertaintyResult
    | LitPopResult
    | IngestResult
    | SupplyChainResult
    | CalibrationResult
    | ForecastResult
    | null;
  detail: string | null;
  created_at: string;
  updated_at: string;
}

// Transition risk (mirror of src/climaterisk/transition/carbon.py).
export interface AssetCarbon {
  id: string;
  name: string;
  emissions_tco2e: number;
  emissions_source: "reported" | "sector_proxy";
  annual_cost_by_year: Record<string, number>;
  npv: number;
}
export interface TransitionResult {
  scenario: string;
  discount_rate: number;
  base_year: number;
  years: number[];
  total_cost_by_year: number[];
  total_npv: number;
  per_asset: AssetCarbon[];
  method: string;
  detail: string | null;
}
