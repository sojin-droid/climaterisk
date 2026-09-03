"""Domain entities — the backend-owned model, serialized per session.

The ``Portfolio`` is the canonical session document: the frontend persists only a
session id and syncs this whole document back via PUT (debounced). All numeric
inputs that the physical/transition engines consume originate here.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from climaterisk.core.enums import (
    ClimateScenario,
    DepthLevel,
    ExposureSource,
    GeographicScale,
    Peril,
    Sector,
    TransitionScenario,
)


def _new_id() -> str:
    return uuid.uuid4().hex


class RatingThreshold(BaseModel):
    """One row of a DSCR→rating grid: a rating applies when DSCR ≥ ``dscr_min``."""

    dscr_min: float = Field(description="Minimum DSCR for this rating (descending grid).")
    rating: str = Field(description="Credit rating label, e.g. 'AA'.")


class FinancialProfile(BaseModel):
    """Project economics for the climate-risk-premium engine. Used as a portfolio-level
    default and (optionally) overridden per asset. Unset fields fall back to the cited
    ``finance_reference.json`` financing defaults."""

    capex: float | None = Field(default=None, ge=0.0, description="Total capital outlay.")
    annual_ebitda: float | None = Field(default=None, description="Baseline annual EBITDA.")
    horizon_years: int | None = Field(default=None, ge=1, le=60)
    debt_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    debt_tenor_years: int | None = Field(default=None, ge=1, le=60)
    risk_free_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    baseline_spread_bps: float | None = Field(default=None, ge=0.0)
    baseline_equity_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    rating_method: str | None = Field(
        default=None,
        description="Single DSCR→rating methodology id (back-compat / primary). Superseded by "
        "rating_methods when that is set. None uses the library default.",
    )
    rating_methods: list[str] | None = Field(
        default=None,
        description="Selected DSCR→rating methodology ids to compare (from "
        "finance_reference.rating_methods, plus 'custom'). The first is the primary used for the "
        "headline and per-asset ratings; all are compared at the portfolio level.",
    )
    custom_rating_thresholds: list[RatingThreshold] | None = Field(
        default=None,
        description="User-defined DSCR→rating grid; used when 'custom' is selected.",
    )

    # Asset financial model — the one sector-specific seam. "generic" (default) reduces a flat
    # EBITDA by the climate loss; "power_gen" builds EBITDA from generation and stresses the
    # capacity factor through the operational channels (see finance/models.py).
    financial_model: str | None = Field(
        default=None,
        description="Financial model id: 'generic' (default, any sector) or 'power_gen' "
        "(power plant — uses the generation fields and operational channels below).",
    )
    # --- power_gen generation economics (only used when financial_model == 'power_gen') ---
    capacity_mw: float | None = Field(default=None, ge=0.0, description="Nameplate capacity (MW).")
    power_price: float | None = Field(
        default=None, ge=0.0, description="Realised power price per MWh."
    )
    capacity_factor: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Baseline (no-stress) capacity factor."
    )
    plant_fuel: str | None = Field(
        default=None, description="Fuel/type (coal, lng, nuclear, …) — seeds a default CF."
    )
    fixed_opex: float | None = Field(default=None, ge=0.0, description="Annual fixed O&M.")
    opex_per_mwh: float | None = Field(default=None, ge=0.0, description="Variable O&M per MWh.")
    # --- power_gen stressed-scenario channel magnitudes (fractions in [0, 1]) ---
    dispatch_penalty: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Policy capacity-factor reduction (dispatch)."
    )
    outage_rate: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Forced-outage fraction (wildfire/storm)."
    )
    capacity_derate: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Capacity/water derate (drought)."
    )
    efficiency_loss: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Efficiency derate (heat)."
    )


class Asset(BaseModel):
    """A located facility — one CLIMADA exposure point (or footprint)."""

    id: str = Field(default_factory=_new_id)
    name: str = "Untitled asset"
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    sector: Sector = Sector.REAL_ESTATE
    geographic_scale: GeographicScale = GeographicScale.POINT
    value: float = Field(default=0.0, ge=0.0, description="Asset value at risk, in `currency`.")
    currency: str = "USD"
    headcount: int | None = Field(
        default=None,
        ge=0,
        description="People present on site (workforce). Exposure for health perils such "
        "as `heat_mortality`; when unset those runners fall back to a stated default.",
    )
    annual_emissions_tco2e: float | None = Field(
        default=None, ge=0.0, description="Scope-1 emissions; if None, proxied from sector factors."
    )
    vulnerability_class: str | None = Field(
        default=None,
        description="Vulnerability-class id (peril-agnostic); if None, the sector default is used.",
    )
    geometry: dict[str, Any] | None = Field(
        default=None,
        description="Optional GeoJSON geometry (Polygon/MultiPolygon for a footprint, or "
        "LineString/MultiLineString for a pipeline/transmission/road); when set, runners "
        "disaggregate it to representative points (interior grid for polygons, along-line "
        "samples for lines).",
    )
    properties: dict[str, float | str | bool] = Field(default_factory=dict)
    financial_profile: FinancialProfile | None = None  # per-asset CRP override (optional)


class Scenario(BaseModel):
    """The scenario context a run is evaluated under."""

    climate: ClimateScenario = ClimateScenario.RCP45
    transition: TransitionScenario = TransitionScenario.NET_ZERO_2050
    anchor_years: list[int] = Field(default_factory=lambda: [2030, 2040, 2050])


class RunConfig(BaseModel):
    """Run-time options for an analysis."""

    perils: list[Peril] = Field(default_factory=lambda: [Peril.TROPICAL_CYCLONE])
    discount_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    exposure_source: ExposureSource = ExposureSource.POINTS
    options: dict[str, float | str | bool] = Field(default_factory=dict)
    financial_profile: FinancialProfile | None = None  # portfolio-level CRP project economics


class VulnerabilityOverride(BaseModel):
    """User edits to a vulnerability class (impact-function studio). Unset fields fall
    back to the bundled library values."""

    tc_v_half: float | None = Field(default=None, gt=0.0)
    wf_max_mdd: float | None = Field(default=None, ge=0.0, le=1.0)
    flood_mdr: list[float] | None = None
    eq_mdr: list[float] | None = None  # earthquake: mean damage ratio at each MMI breakpoint


class Portfolio(BaseModel):
    """The session model — a named set of assets plus its scenario + run config."""

    id: str = Field(default_factory=_new_id)
    name: str = "Untitled portfolio"
    depth_level: DepthLevel = DepthLevel.ASSET
    assets: list[Asset] = Field(default_factory=list)
    scenario: Scenario = Field(default_factory=Scenario)
    run_config: RunConfig = Field(default_factory=RunConfig)
    # Impact-function studio: per-class curve overrides (class id -> override).
    vulnerability_overrides: dict[str, VulnerabilityOverride] = Field(default_factory=dict)

    @classmethod
    def empty(cls) -> Portfolio:
        """Create a fresh, empty portfolio (used when a new session is created)."""
        return cls()
