"""Physical-risk metrics and the canonical result row — pure, no CLIMADA, no network.

This is the half of the physical-risk engine that does not need CLIMADA: the risk-level
bands, the EAL-over-asset-value ratio, the probability/return-period relation, the
global-vs-Korea change arithmetic, and the row every calculation emits. It lives in the
backend package so the API, the batch table and the Excel export can use it without the
worker environment (the GPL boundary — ``docs/ARCHITECTURE.md``).

Two rules are enforced here rather than left to callers:

* **A missing number is never zero.** Where a quantity cannot be computed the field is
  ``None`` and :attr:`ResultRow.calculation_status` says why. ``0.0`` means a real,
  computed zero loss.
* **Thresholds are configuration.** The bands come from
  ``assets/libraries/physical_risk_config.json``; nothing here hard-codes 0.10 / 0.50.
  They are this project's own methodology, GRESB-informed — not an official GRESB rule.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Repository root, four levels up from ``src/climaterisk/physical_risk/metrics.py``.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class CalculationStatus(StrEnum):
    """Why a row does or does not carry financial numbers.

    ``FULL`` is the only status under which ``eal_usd`` and ``risk_level`` may be non-null.
    """

    FULL = "FULL"
    HAZARD_ONLY = "HAZARD_ONLY"
    NO_IMPACT_FUNCTION = "NO_IMPACT_FUNCTION"
    NO_HAZARD_DATA = "NO_HAZARD_DATA"
    NO_EXPOSURE_DATA = "NO_EXPOSURE_DATA"
    RETURN_PERIOD_NOT_RESOLVABLE = "RETURN_PERIOD_NOT_RESOLVABLE"
    ERROR = "ERROR"


#: Statuses under which financial fields must be null. Checked by the engine and by tests.
NON_FINANCIAL_STATUSES = frozenset(
    {
        CalculationStatus.HAZARD_ONLY,
        CalculationStatus.NO_IMPACT_FUNCTION,
        CalculationStatus.NO_HAZARD_DATA,
        CalculationStatus.NO_EXPOSURE_DATA,
        CalculationStatus.ERROR,
    }
)


class ModelId(StrEnum):
    """Which data substitution a row belongs to, so baselines are never mixed with locals."""

    GLOBAL_BASELINE = "GLOBAL_BASELINE"
    KOREA_HAZARD = "KOREA_HAZARD"
    KOREA_HAZARD_EXPOSURE = "KOREA_HAZARD_EXPOSURE"


def config_path() -> Path:
    """Location of the physical-risk configuration.

    Resolved without importing the backend settings: this module must stay importable from
    the CLIMADA worker environment, which has no ``pydantic``. ``CLIMATERISK_LIBRARY_DIR``
    overrides, matching the backend's own setting name.
    """
    override = os.environ.get("CLIMATERISK_LIBRARY_DIR")
    base = Path(override) if override else _REPO_ROOT / "assets" / "libraries"
    if not base.is_absolute():
        base = _REPO_ROOT / base
    return base / "physical_risk_config.json"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """The physical-risk configuration, read once."""
    data: dict[str, Any] = json.loads(config_path().read_text(encoding="utf-8"))
    return data


# --------------------------------------------------------------------------- #
# Risk level                                                                   #
# --------------------------------------------------------------------------- #
def risk_level(
    eal_pct: float | None, config: dict[str, Any] | None = None
) -> tuple[str | None, str | None]:
    """Risk band for an EAL-over-asset-value percentage.

    Args:
        eal_pct: ``eal_usd / asset_value_usd * 100``, or None when it could not be computed.
        config: Override for the configuration (tests).

    Returns:
        ``(level, criteria)`` — both None when ``eal_pct`` is None, so an uncomputable
        ratio never silently becomes "Low".
    """
    if eal_pct is None:
        return None, None
    bands = (config or load_config())["risk_levels"]["bands"]
    for band in bands:
        lo, hi = band["min_pct"], band["max_pct"]
        if (lo is None or eal_pct >= lo) and (hi is None or eal_pct < hi):
            return str(band["level"]), str(band["criteria"])
    return None, None  # pragma: no cover - bands are exhaustive by construction


def eal_as_pct_of_assets(eal_usd: float | None, asset_value_usd: float | None) -> float | None:
    """``eal / value * 100``, or None when the ratio has no meaning.

    A zero or missing asset value yields None rather than a division error or a fabricated
    percentage: the loss may be real, but as a *share of assets* it is undefined.
    """
    if eal_usd is None or asset_value_usd is None or asset_value_usd <= 0:
        return None
    return float(eal_usd) / float(asset_value_usd) * 100.0


# --------------------------------------------------------------------------- #
# Probability / return period                                                  #
# --------------------------------------------------------------------------- #
def probability_from_return_period(return_period_years: float | None) -> float | None:
    """Annual exceedance frequency of a return period: ``1 / T``.

    The inverse of :func:`return_period_from_probability`, so the two can never disagree —
    the engine derives one from the other rather than storing them independently.
    """
    if return_period_years is None or return_period_years <= 0:
        return None
    return 1.0 / float(return_period_years)


def return_period_from_probability(probability: float | None) -> float | None:
    """``1 / p``. See :func:`probability_from_return_period`."""
    if probability is None or probability <= 0:
        return None
    return 1.0 / float(probability)


def max_resolvable_return_period(frequencies: list[float]) -> float | None:
    """Longest return period an event set can express: ``1 / min(frequency)``.

    CLIMADA's ``Impact.calc_freq_curve`` does **not** return NaN beyond the record — it
    saturates at the largest event's loss. Asking a 4-event set at 0.1/yr for a 100-year
    loss returns the 10-year loss labelled 100-year. The engine therefore refuses a
    return period longer than this and records
    :attr:`CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE`.
    """
    positive = [f for f in frequencies if f and f > 0]
    if not positive:
        return None
    return 1.0 / min(positive)


# --------------------------------------------------------------------------- #
# Comparison                                                                   #
# --------------------------------------------------------------------------- #
def change_pct(local: float | None, baseline: float | None) -> float | None:
    """``(local / baseline - 1) * 100``; None when the baseline is zero or missing.

    A zero denominator is not an error to hide — it is a comparison that cannot be made.
    """
    if local is None or baseline is None or baseline == 0:
        return None
    return (float(local) / float(baseline) - 1.0) * 100.0


def change_abs(local: float | None, baseline: float | None) -> float | None:
    """``local - baseline``, or None when either side is missing."""
    if local is None or baseline is None:
        return None
    return float(local) - float(baseline)


# --------------------------------------------------------------------------- #
# The canonical row                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class ResultRow:
    """One (facility x hazard x model) result.

    Financial fields default to None. :meth:`finalise` fills the derived ones and enforces
    the invariant that a non-FULL status carries no financial numbers.
    """

    facility_id: str
    facility_name: str
    hazard_type: str
    model_id: str

    # provenance
    hazard_source: str | None = None
    exposure_source: str | None = None
    impact_function_source: str | None = None
    impact_function_id: int | None = None
    impact_function_name: str | None = None
    scenario: str | None = None
    time_horizon: str | None = None
    data_version: str | None = None

    # hazard
    hazard_intensity: float | None = None
    hazard_intensity_unit: str | None = None

    # frequency semantics
    probability: float | None = None
    return_period_years: float | None = None

    # financial
    asset_value_usd: float | None = None
    potential_loss_usd: float | None = None
    potential_loss_return_period_years: float | None = None
    eal_usd: float | None = None
    eal_as_pct_of_assets: float | None = None

    risk_level: str | None = None
    risk_level_criteria: str | None = None

    climate_change_multiplier: float | None = None

    calculation_status: str = CalculationStatus.ERROR.value
    status_detail: str | None = None
    confidence: str | None = None

    # asset metadata (recorded, not used by the impact functions unless stated)
    property_type: str | None = None
    floor_area_m2: float | None = None
    year_built: int | None = None
    structure_type: str | None = None

    definitions: dict[str, str] = field(default_factory=dict)

    def finalise(self, config: dict[str, Any] | None = None) -> ResultRow:
        """Derive the dependent fields and enforce the null-not-zero invariant.

        ``eal_as_pct_of_assets`` and the risk band are computed from ``eal_usd`` and
        ``asset_value_usd``; ``probability`` from ``return_period_years``. Under a
        non-financial status every financial field is cleared, so a caller cannot leave a
        stale or fabricated number behind.
        """
        if self.calculation_status in {s.value for s in NON_FINANCIAL_STATUSES}:
            self.potential_loss_usd = None
            self.potential_loss_return_period_years = None
            self.eal_usd = None
            self.eal_as_pct_of_assets = None
            self.risk_level = None
            self.risk_level_criteria = None
            self.climate_change_multiplier = None
            return self
        self.eal_as_pct_of_assets = eal_as_pct_of_assets(self.eal_usd, self.asset_value_usd)
        self.risk_level, self.risk_level_criteria = risk_level(self.eal_as_pct_of_assets, config)
        if self.return_period_years is not None:
            self.probability = probability_from_return_period(self.return_period_years)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def comparison_row(baseline: ResultRow, local: ResultRow) -> dict[str, Any]:
    """Global-baseline vs local row, with both absolute and percentage change.

    The two rows must describe the same facility and hazard; comparing across hazards or
    facilities is a caller error, not something to paper over.
    """
    if (baseline.facility_id, baseline.hazard_type) != (local.facility_id, local.hazard_type):
        raise ValueError(
            f"cannot compare {baseline.facility_id}/{baseline.hazard_type} with "
            f"{local.facility_id}/{local.hazard_type}"
        )
    return {
        "facility_id": baseline.facility_id,
        "facility_name": baseline.facility_name,
        "hazard_type": baseline.hazard_type,
        "baseline_model_id": baseline.model_id,
        "local_model_id": local.model_id,
        "baseline_eal_usd": baseline.eal_usd,
        "local_eal_usd": local.eal_usd,
        "eal_change_usd": change_abs(local.eal_usd, baseline.eal_usd),
        "eal_change_pct": change_pct(local.eal_usd, baseline.eal_usd),
        "baseline_potential_loss_usd": baseline.potential_loss_usd,
        "local_potential_loss_usd": local.potential_loss_usd,
        "potential_loss_change_usd": change_abs(
            local.potential_loss_usd, baseline.potential_loss_usd
        ),
        "potential_loss_change_pct": change_pct(
            local.potential_loss_usd, baseline.potential_loss_usd
        ),
        "baseline_hazard_intensity": baseline.hazard_intensity,
        "local_hazard_intensity": local.hazard_intensity,
        "hazard_intensity_change_pct": change_pct(
            local.hazard_intensity, baseline.hazard_intensity
        ),
        "baseline_calculation_status": baseline.calculation_status,
        "local_calculation_status": local.calculation_status,
    }


def jrc_sector_for(property_type: str | None, config: dict[str, Any] | None = None) -> str:
    """JRC sector for a portfolio ``property_type``.

    The mapping is a platform choice recorded in the configuration, not something the JRC
    curves define; an unmapped type falls back to the configured default rather than
    guessing.
    """
    cfg = (config or load_config())["property_type_to_jrc_sector"]
    if not property_type:
        return str(cfg["default"])
    return str(cfg["map"].get(str(property_type).strip().lower(), cfg["default"]))
