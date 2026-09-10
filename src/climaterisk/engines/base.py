"""Canonical request/result contract for the physical-risk engine.

These pydantic models define the JSON the orchestration backend writes for a run
(``request.json``) and reads back (``result.json``). The CLIMADA worker — which
lives in a separate conda env and cannot import this package — implements the
same shapes. Keep the two in sync (the worker mirrors this module).

One run covers the portfolio under one climate scenario, producing one
:class:`PhysicalRunResult` per requested peril.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from climaterisk.core.entities import Portfolio
from climaterisk.data.libraries import load_libraries


class AssetSpec(BaseModel):
    """A single exposure point handed to the engine, with resolved vulnerability params.

    The backend resolves each asset's vulnerability class into concrete per-peril
    curve parameters here, so the CLIMADA worker needs no access to the library.
    """

    id: str
    name: str
    lat: float
    lon: float
    sector: str
    value: float
    currency: str
    headcount: int | None = None  # people on site; exposure for health perils
    vulnerability_class: str
    tc_v_half: float  # Emanuel TC: wind speed (m/s) at 50% mean damage
    wf_max_mdd: float  # wildfire: max mean damage ratio above the fire-intensity threshold
    flood_depth_m: list[float]  # flood depth-damage: depths (m), shared by river + coastal flood
    flood_mdr: list[float]  # flood depth-damage: mean damage ratio at each depth
    eq_mmi: list[float]  # earthquake: Modified Mercalli Intensity breakpoints (shared)
    eq_mdr: list[float]  # earthquake: mean damage ratio at each MMI breakpoint
    geometry: dict[str, Any] | None = None  # GeoJSON footprint; runners disaggregate to points
    # Vulnerability parameters the user set explicitly (session override) rather than
    # inherited from the class default — e.g. ["tc_v_half", "flood_mdr"]. The worker never
    # replaces an explicit value with a geography-aware preset (see
    # worker/climaterisk_worker/vulnerability.py). Empty = every value is a class default.
    explicit_params: list[str] = Field(default_factory=list)


def resolve_asset_specs(portfolio: Portfolio) -> list[AssetSpec]:
    """Resolve each asset's vulnerability class into concrete per-peril curve params.

    Values come from the class (``impact_functions.json``) or the session's per-class
    override; ``explicit_params`` records which of the four came from an override so the
    worker can apply a regional preset only to inherited class defaults.
    """
    libs = load_libraries()
    classes = {c["id"]: c for c in libs["impact_functions"]["classes"]}
    flood_depth_m = list(libs["impact_functions"]["flood_depth_m"])
    eq_mmi = list(libs["impact_functions"]["eq_mmi"])
    sector_default = {s["id"]: s["default_vulnerability_class"] for s in libs["sectors"]["sectors"]}
    fallback = libs["impact_functions"]["classes"][0]

    overrides = portfolio.vulnerability_overrides
    specs: list[AssetSpec] = []
    for a in portfolio.assets:
        vc_id = a.vulnerability_class or sector_default.get(str(a.sector), fallback["id"])
        vc = classes.get(vc_id, fallback)
        ov = overrides.get(vc["id"])
        tc_v_half = (
            float(ov.tc_v_half) if ov and ov.tc_v_half is not None else float(vc["tc_v_half"])
        )
        wf = float(ov.wf_max_mdd) if ov and ov.wf_max_mdd is not None else float(vc["wf_max_mdd"])
        fmdr = [float(x) for x in (ov.flood_mdr if ov and ov.flood_mdr else vc["flood_mdr"])]
        emdr = [float(x) for x in (ov.eq_mdr if ov and ov.eq_mdr else vc["eq_mdr"])]
        explicit: list[str] = []
        if ov is not None:
            if ov.tc_v_half is not None:
                explicit.append("tc_v_half")
            if ov.wf_max_mdd is not None:
                explicit.append("wf_max_mdd")
            if ov.flood_mdr:
                explicit.append("flood_mdr")
            if ov.eq_mdr:
                explicit.append("eq_mdr")
        specs.append(
            AssetSpec(
                id=a.id,
                name=a.name,
                lat=a.lat,
                lon=a.lon,
                sector=a.sector,
                value=a.value,
                currency=a.currency,
                headcount=a.headcount,
                vulnerability_class=vc["id"],
                tc_v_half=tc_v_half,
                wf_max_mdd=wf,
                flood_depth_m=flood_depth_m,
                flood_mdr=fmdr,
                eq_mmi=eq_mmi,
                eq_mdr=emdr,
                geometry=a.geometry,
                explicit_params=explicit,
            )
        )
    return specs


class PhysicalRunRequest(BaseModel):
    """Everything the physical engine needs for one run."""

    session_id: str
    perils: list[str]
    climate_scenario: str
    anchor_years: list[int]
    assets: list[AssetSpec]
    # Free-form run options forwarded to the worker (e.g. {"tc_future_method": "knutson"}).
    options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_portfolio(cls, portfolio: Portfolio) -> PhysicalRunRequest:
        """Project the session model onto an engine request, resolving vulnerability params."""
        return cls(
            session_id=portfolio.id,
            perils=[p.value for p in portfolio.run_config.perils],
            climate_scenario=portfolio.scenario.climate,
            anchor_years=portfolio.scenario.anchor_years,
            assets=resolve_asset_specs(portfolio),
            options=dict(portfolio.run_config.options),
        )


class AssetImpact(BaseModel):
    """Per-asset expected annual impact (the ``eai_exp`` map layer)."""

    id: str
    lat: float
    lon: float
    eai: float = Field(description="Expected annual impact, in the asset's currency.")
    country: str | None = None  # ISO3, for national aggregation


class FreqCurve(BaseModel):
    """Exceedance / return-period curve (CLIMADA ``calc_freq_curve``)."""

    return_periods: list[float]
    impact: list[float]
    # Applied cap: the longest return period this event set supports. Set to half the
    # record, because a 1-in-N-year value estimated from an N-year record rests on a single
    # event. Periods beyond it are dropped, not extrapolated.
    max_resolvable_return_period: float | None = None
    record_years: float | None = None  # length of the event record behind that cap


class Yearset(BaseModel):
    """Sampled annual-loss distribution (CLIMADA ``util.yearsets.impact_yearset``).

    ``mean`` reproduces AAI; the percentiles + ``losses`` series expose the spread
    between a median year and a tail year.
    """

    n_years: int
    mean: float
    p50: float
    p90: float
    p95: float
    p99: float
    max: float
    losses: list[float]


class WarnLevels(BaseModel):
    """Per-asset hazard-intensity warning bands (CLIMADA petals ``Warn.bin_map``).

    ``counts[i]`` = assets in band i+1 (1 = lowest intensity … ``n_levels`` = highest).
    """

    n_levels: int
    counts: list[int]
    thresholds: list[float]
    unit: str


class PhysicalRunResult(BaseModel):
    """Engine output for one peril within a run (future horizon, plus present-day delta)."""

    peril: str
    status: str  # "ok" | "engine_not_ready" | "error"
    target_year: int | None = None
    aai_agg: float = 0.0  # future-horizon average annual impact
    present_aai_agg: float | None = None  # present-day baseline AAI
    delta_pct: float | None = None  # (future - present) / present, in %
    total_value: float = 0.0
    per_asset: list[AssetImpact] = Field(default_factory=list)
    freq_curve: FreqCurve | None = None
    yearset: Yearset | None = None  # sampled annual-loss distribution (CLIMADA yearsets)
    warn_levels: WarnLevels | None = None  # per-asset hazard-intensity warning bands
    # "monetary" (AAI in currency) | "yield" | "productivity" | "mortality" — non-damage
    # perils (heatwave, drought, crop yield) report a fractional/index metric, and
    # heat_mortality reports expected annual deaths (persons). Never currency.
    result_kind: str = "monetary"
    metric_unit: str | None = None  # label for non-monetary metrics (e.g. "% yield loss")
    interpretation: str | None = None  # plain-language meaning (disambiguates a 0 / error)
    detail: str | None = None


class PhysicalRunOutput(BaseModel):
    """The full engine output for one run (one result per requested peril)."""

    status: str  # "ok" | "partial" | "engine_not_ready" | "error"
    climate_scenario: str
    results: list[PhysicalRunResult] = Field(default_factory=list)
    detail: str | None = None


class PhysicalEngine(Protocol):
    """Interface every physical-risk backend (CLIMADA, physrisk, …) implements."""

    name: str

    def run(self, request: PhysicalRunRequest) -> PhysicalRunOutput: ...


# --- Adaptation cost-benefit (CLIMADA CostBenefit / MeasureSet) ---


class MeasureSpec(BaseModel):
    """A user-defined adaptation measure."""

    name: str
    cost: float = Field(default=0.0, ge=0.0)
    damage_reduction: float = Field(default=0.0, ge=0.0, le=1.0, description="Fractional MDD cut.")
    hazard_freq_cutoff: float = Field(default=0.0, ge=0.0)
    risk_transf_attach: float = Field(default=0.0, ge=0.0, description="Insurance deductible.")
    risk_transf_cover: float = Field(default=0.0, ge=0.0, description="Insurance cover/limit.")


class CostBenefitRequest(BaseModel):
    """Inputs for an adaptation cost-benefit run."""

    mode: str = "cost_benefit"
    session_id: str
    peril: str = "tropical_cyclone"
    climate_scenario: str
    anchor_years: list[int]
    discount_rate: float = 0.05
    # Optional year-varying discount schedule {year: rate}; overrides the flat rate.
    discount_schedule: dict[str, float] | None = None
    assets: list[AssetSpec]
    measures: list[MeasureSpec]
    # Run-config options forwarded verbatim (e.g. ``tc_impf_default``) so the worker resolves
    # vulnerability defaults exactly as the physical run does.
    options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_portfolio(
        cls, portfolio: Portfolio, measures: list[MeasureSpec]
    ) -> CostBenefitRequest:
        """Build a cost-benefit request from the session model + adaptation measures."""
        sched = portfolio.run_config.options.get("discount_schedule")
        return cls(
            session_id=portfolio.id,
            climate_scenario=portfolio.scenario.climate,
            anchor_years=portfolio.scenario.anchor_years,
            discount_rate=portfolio.run_config.discount_rate,
            discount_schedule=sched if isinstance(sched, dict) else None,
            assets=resolve_asset_specs(portfolio),
            measures=measures,
            options=dict(portfolio.run_config.options),
        )


class MeasureResult(BaseModel):
    """Per-measure cost-benefit outcome."""

    name: str
    cost: float
    benefit: float  # NPV of averted damage
    benefit_cost_ratio: float | None = None


class CostBenefitResult(BaseModel):
    """Engine output for an adaptation cost-benefit run."""

    status: str
    peril: str = "tropical_cyclone"
    future_year: int | None = None
    discount_rate: float = 0.05
    currency: str = "USD"
    tot_climate_risk: float = 0.0  # NPV of unaverted risk
    measures: list[MeasureResult] = Field(default_factory=list)
    detail: str | None = None


# --- Uncertainty & sensitivity (Monte-Carlo over ImpactCalc) ---


class UncertaintyRequest(BaseModel):
    """Inputs for a Monte-Carlo uncertainty run."""

    mode: str = "uncertainty"
    session_id: str
    climate_scenario: str
    anchor_years: list[int]
    n_samples: int = 50
    assets: list[AssetSpec]
    options: dict[str, Any] = Field(default_factory=dict)  # e.g. tc_impf_default, seed

    @classmethod
    def from_portfolio(cls, portfolio: Portfolio, n_samples: int = 50) -> UncertaintyRequest:
        """Build an uncertainty request from the session model."""
        return cls(
            session_id=portfolio.id,
            climate_scenario=portfolio.scenario.climate,
            anchor_years=portfolio.scenario.anchor_years,
            n_samples=n_samples,
            options=dict(portfolio.run_config.options),
            assets=resolve_asset_specs(portfolio),
        )


class UncertaintyResult(BaseModel):
    """Engine output for a Monte-Carlo uncertainty run."""

    status: str
    peril: str = "tropical_cyclone"
    future_year: int | None = None
    n_samples: int = 0
    currency: str = "USD"
    aai_mean: float = 0.0
    aai_std: float = 0.0
    aai_p5: float = 0.0
    aai_p50: float = 0.0
    aai_p95: float = 0.0
    distribution: list[float] = Field(default_factory=list)
    sensitivity: dict[str, float] = Field(default_factory=dict)  # headline (total-order)
    sensitivity_s1: dict[str, float] = Field(default_factory=dict)  # Sobol first-order
    sensitivity_st: dict[str, float] = Field(default_factory=dict)  # Sobol total-order
    sensitivity_method: str = "sobol"
    # Climate-change delta (future AAI distribution vs present-day baseline AAI).
    present_aai: float | None = None
    delta_mean: float | None = None
    delta_p5: float | None = None
    delta_p95: float | None = None
    # Method transparency: SALib Sobol wrapper around ImpactCalc (not climada unsequa),
    # TC only, with the (indicative) input bounds and the sampling seed actually used.
    method: str | None = None
    scope: str | None = None
    bounds: dict[str, list[float]] = Field(default_factory=dict)
    bounds_provenance: str | None = None
    frequency_treatment: str | None = None
    seed: int | None = None
    detail: str | None = None


# --- LitPop modeled exposure ---


class LitPopRequest(BaseModel):
    """Inputs for a LitPop modeled-exposure run."""

    mode: str = "litpop"
    session_id: str
    country: str
    exposure_source: str = "litpop"  # litpop | blackmarble | gdp | crop | osm | raster
    peril: str = "tropical_cyclone"  # any physical peril runs on the modeled grid
    climate_scenario: str
    anchor_years: list[int]

    @classmethod
    def from_portfolio(
        cls,
        portfolio: Portfolio,
        country: str,
        exposure_source: str = "litpop",
        peril: str = "tropical_cyclone",
    ) -> LitPopRequest:
        return cls(
            session_id=portfolio.id,
            country=country,
            exposure_source=exposure_source,
            peril=peril,
            climate_scenario=portfolio.scenario.climate,
            anchor_years=portfolio.scenario.anchor_years,
        )


# --- Hazard layer preview (render a catalog hazard's intensity field to a map raster) ---


class HazardPreviewRequest(BaseModel):
    """Inputs to render a local-catalog hazard's intensity field as a map raster PNG."""

    mode: str = "hazard_preview"
    session_id: str
    peril: str
    scenario: str = "historical"
    region: str = "global"
    year: int | None = None
    # Optional crop window [south, west, north, east] (deg) — render only this area,
    # with the color scale normalized to it (local contrast instead of nationwide).
    bbox: list[float] | None = None


# --- Data ingestion (download & refine real sources into the local catalog) ---


class IngestRequest(BaseModel):
    """Inputs for a data-ingest run (download + refine a source into the catalog).

    ``points`` are the portfolio's asset coordinates; the worker uses them to bound
    the download (Aqueduct ``/vsicurl`` window) and to compute the catalog region
    key the SAME way the runners do, so the ingested hazard is found automatically.
    """

    mode: str = "ingest"
    session_id: str
    source: str  # "dataapi" | "aqueduct"
    peril: str = "river_flood"  # "tropical_cyclone" | "river_flood"
    scenario: str
    year: int
    points: list[list[float]]  # [[lat, lon], ...]

    @classmethod
    def from_portfolio(
        cls, portfolio: Portfolio, source: str, peril: str, scenario: str, year: int
    ) -> IngestRequest:
        """Build an ingest request scoped to the session's assets."""
        return cls(
            session_id=portfolio.id,
            source=source,
            peril=peril,
            scenario=scenario,
            year=year,
            points=[[a.lat, a.lon] for a in portfolio.assets],
        )


class IngestResult(BaseModel):
    """Engine output for a data-ingest run."""

    status: str
    source: str = ""
    peril: str = ""
    entry: dict[str, Any] | None = None  # the catalog entry written
    detail: str | None = None


class LitPopPoint(BaseModel):
    lat: float
    lon: float
    eai: float


class LitPopResult(BaseModel):
    """Engine output for a LitPop modeled-exposure run."""

    status: str
    country: str = ""
    peril: str = "tropical_cyclone"
    future_year: int | None = None
    total_value: float = 0.0
    aai_agg: float = 0.0
    n_points: int = 0
    currency: str = "USD"
    per_point: list[LitPopPoint] = Field(default_factory=list)
    detail: str | None = None


# --- Supply-chain indirect (macro-economic) impact ---


class SupplyChainRequest(BaseModel):
    """Inputs for a supply-chain indirect-impact run (direct TC impact → MRIO propagation)."""

    mode: str = "supplychain"
    session_id: str
    climate_scenario: str
    anchor_years: list[int]
    assets: list[AssetSpec]
    mriot_type: str = "WIOD16"  # MRIO table: WIOD16 | EXIOBASE3 | OECD21 | …
    mriot_year: int = 2010
    options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_portfolio(
        cls, portfolio: Portfolio, mriot_type: str = "WIOD16", mriot_year: int = 2010
    ) -> SupplyChainRequest:
        """Build a supply-chain request from the session model."""
        return cls(
            session_id=portfolio.id,
            climate_scenario=portfolio.scenario.climate,
            anchor_years=portfolio.scenario.anchor_years,
            assets=resolve_asset_specs(portfolio),
            mriot_type=mriot_type,
            mriot_year=mriot_year,
            options=dict(portfolio.run_config.options),
        )


class SupplyChainSector(BaseModel):
    sector: str
    indirect: float


class SupplyChainResult(BaseModel):
    """Engine output for a supply-chain indirect-impact run."""

    status: str
    mriot: str = ""
    currency: str = "USD"
    total_direct: float = 0.0  # direct AAI on the portfolio
    total_indirect: float = 0.0  # indirect (rippled) impact via the I/O table
    amplification: float | None = None  # indirect / direct
    by_sector: list[SupplyChainSector] = Field(default_factory=list)
    detail: str | None = None


# --- Impact-function calibration (against an observed loss series) ---

#: Observed-loss series a calibration may be fitted against. ``emdat`` is the only one with a
#: reader today; ``disaster_yearbook`` (행정안전부 재해연보) is accepted by the schema so the
#: source-selection plumbing exists, and the worker returns an explicit "not available" error
#: until its loader is written — docs/OBSERVED_LOSSES_KR_SPEC.md §9 keeps F1 behind a data gate.
OBSERVED_SOURCES: tuple[str, ...] = ("emdat", "disaster_yearbook")


class CalibrationRequest(BaseModel):
    """Inputs for an impact-function calibration run (fit TC v_half to observed losses)."""

    mode: str = "calibration"
    session_id: str
    climate_scenario: str
    anchor_years: list[int]
    assets: list[AssetSpec]
    #: Which observed series to fit against; the default reproduces the pre-existing behaviour.
    observed_source: str = "emdat"
    options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_portfolio(
        cls, portfolio: Portfolio, observed_source: str = "emdat"
    ) -> CalibrationRequest:
        """Build a calibration request from the session model.

        A caller that omits ``observed_source`` produces exactly the request this method
        produced before the field existed.
        """
        return cls(
            session_id=portfolio.id,
            climate_scenario=portfolio.scenario.climate,
            anchor_years=portfolio.scenario.anchor_years,
            assets=resolve_asset_specs(portfolio),
            observed_source=observed_source,
            options=dict(portfolio.run_config.options),
        )


class CalibrationResult(BaseModel):
    """Engine output for an impact-function calibration run.

    A successful run also persists the same record under ``data/calibrations/`` (see
    ``persisted_to``); later runs consume it when ``RunConfig.options["tc_impf_default"]
    == "calibrated"``. ``fit_status`` is ``"fitted"`` — the value has been fitted to one
    observed series, not validated against an independent one.
    """

    status: str
    peril: str = "tropical_cyclone"
    country: str = ""
    param: str = "v_half"
    initial: float = 0.0
    calibrated: float = 0.0
    observed_annual_loss: float = 0.0
    # Provenance / reproducibility metadata (optional for older results).
    fit_status: str | None = None  # "fitted" (never "validated" from this runner)
    observed_source: str | None = None  # e.g. "EM-DAT (public.emdat.be) CSV <basename>"
    observed_period: list[int] = Field(default_factory=list)  # [first_year, last_year]
    n_observed_events: int | None = None
    hazard: str | None = None  # present-day hazard the fit ran on
    objective: str | None = None
    method: str | None = None
    bounds: list[float] = Field(default_factory=list)
    modelled_annual_loss_at_calibrated: float | None = None
    calibrated_at: str | None = None  # ISO-8601 UTC
    schema_version: int | None = None
    persisted_to: str | None = None
    applies_when: str | None = None
    detail: str | None = None


# --- Operational forecast (latest ECMWF ensemble TC tracks) ---


class ForecastRequest(BaseModel):
    """Inputs for an operational TC-forecast run (no scenario — uses the live feed)."""

    mode: str = "forecast"
    session_id: str
    assets: list[AssetSpec]
    options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_portfolio(cls, portfolio: Portfolio) -> ForecastRequest:
        """Build a forecast request from the session model."""
        return cls(
            session_id=portfolio.id,
            assets=resolve_asset_specs(portfolio),
            options=dict(portfolio.run_config.options),
        )


class ForecastResult(BaseModel):
    """Engine output for an operational TC-forecast run."""

    status: str
    peril: str = "tropical_cyclone"
    n_tracks: int = 0
    total_impact: float = 0.0  # ensemble-mean forecast impact over the portfolio
    per_asset: list[AssetImpact] = Field(default_factory=list)
    detail: str | None = None
