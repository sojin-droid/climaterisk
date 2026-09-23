"""Physical-risk metrics and the canonical result row — pure, no CLIMADA, no network.

This is the half of the physical-risk engine that does not need CLIMADA: the risk-level
bands, the EAL-over-asset-value ratio, the probability/return-period relation, the
model-vs-model change arithmetic, and the row every calculation emits. It lives in the
backend package so the API, the batch table and the Excel export can use it without the
worker environment (the GPL boundary — ``docs/ARCHITECTURE.md``).

Rules enforced here rather than left to callers:

* **A missing number is never zero.** Where a quantity cannot be computed the field is
  ``None`` and :attr:`ResultRow.calculation_status` says why. ``0.0`` means a real,
  computed zero loss.
* **Thresholds are configuration.** The bands come from
  ``assets/libraries/physical_risk_config.json``; nothing here hard-codes 0.10 / 0.50.
  They are this project's own methodology, GRESB-informed — not an official GRESB rule.
* **Requested and served scenario are two fields.** A dataset that answers ``rcp45`` with
  ``rcp60`` is recorded as exactly that and the row is ``SCENARIO_MISMATCH``, never filed
  under the requested key (``docs/physical-risk-models.md`` 6).
* **Percent and percentage points are different units.** ``change_pct`` is relative,
  ``change_pp`` is the difference of two percentages. They are never interchanged.
* **A model comparison is not a climate-change multiplier.** The multiplier is defined
  only between two runs of the *same* model over different periods/scenarios.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Repository root, four levels up from ``src/climaterisk/physical_risk/metrics.py``.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class CalculationStatus(StrEnum):
    """Why a row does or does not carry financial numbers.

    ``FULL`` is the only status under which ``eal_usd`` and ``risk_level`` may be non-null,
    with one deliberate exception: ``RETURN_PERIOD_NOT_RESOLVABLE`` keeps EAL (which needs
    no extrapolation) and nulls only the return-period loss.
    """

    FULL = "FULL"
    HAZARD_ONLY = "HAZARD_ONLY"
    NO_IMPACT_FUNCTION = "NO_IMPACT_FUNCTION"
    NO_HAZARD_DATA = "NO_HAZARD_DATA"
    NO_EXPOSURE_DATA = "NO_EXPOSURE_DATA"
    RETURN_PERIOD_NOT_RESOLVABLE = "RETURN_PERIOD_NOT_RESOLVABLE"
    SCENARIO_MISMATCH = "SCENARIO_MISMATCH"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    ERROR = "ERROR"


#: Statuses under which financial fields must be null. Checked by the engine and by tests.
NON_FINANCIAL_STATUSES = frozenset(
    {
        CalculationStatus.HAZARD_ONLY,
        CalculationStatus.NO_IMPACT_FUNCTION,
        CalculationStatus.NO_HAZARD_DATA,
        CalculationStatus.NO_EXPOSURE_DATA,
        CalculationStatus.SCENARIO_MISMATCH,
        CalculationStatus.NOT_IMPLEMENTED,
        CalculationStatus.ERROR,
    }
)


class ModelId(StrEnum):
    """Which hazard source a row was priced against. The impact function is the same for all.

    The three models differ **only** in where the hazard comes from; exposure and the
    published CLIMADA impact function are held fixed so a difference between two rows is
    attributable to the hazard data alone.
    """

    #: CLIMADA Data API hazard with ``spatial_coverage = global`` — the reference.
    GLOBAL_BASELINE = "GLOBAL_BASELINE"
    #: CLIMADA Data API hazard with ``spatial_coverage = country`` (``country_iso3alpha``).
    #: A country-specific cut of the same Data API product — **not** Korea-local source data.
    DATA_API_COUNTRY = "DATA_API_COUNTRY"
    #: A domestic-source hazard (환경부 홍수위험지도, KMA, 국내 공식 태풍 자료) through its
    #: own adapter. Reported only when such a dataset and adapter actually exist.
    KOREA_LOCAL = "KOREA_LOCAL"


#: One-line definitions, repeated in the exports so a sheet is self-describing.
MODEL_DEFINITIONS: dict[str, str] = {
    ModelId.GLOBAL_BASELINE.value: (
        "global reference calculation — CLIMADA Data API hazard, spatial_coverage=global, "
        "user exposure, published CLIMADA impact function"
    ),
    ModelId.DATA_API_COUNTRY.value: (
        "country-specific Data API calculation — CLIMADA Data API hazard, "
        "spatial_coverage=country, same exposure, same impact function. "
        "DATA_API_COUNTRY is not treated as Korea-local source data."
    ),
    ModelId.KOREA_LOCAL.value: (
        "domestic-source calculation — a Korean hazard dataset through a dedicated adapter, "
        "same exposure, same impact function. Korea-local results are reported only when a "
        "domestic hazard dataset and compatible adapter are actually available."
    ),
}


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
# Change arithmetic — three different quantities                               #
# --------------------------------------------------------------------------- #
def change_pct(comparison: float | None, baseline: float | None) -> float | None:
    """Relative change ``(comparison / baseline - 1) * 100``, in percent.

    None when the baseline is zero or either side is missing: a zero denominator is not
    an error to hide behind ``inf`` — it is a comparison that cannot be made.
    """
    if comparison is None or baseline is None or baseline == 0:
        return None
    return (float(comparison) / float(baseline) - 1.0) * 100.0


def change_abs(comparison: float | None, baseline: float | None) -> float | None:
    """Absolute difference ``comparison - baseline`` in the quantity's own unit (USD)."""
    if comparison is None or baseline is None:
        return None
    return float(comparison) - float(baseline)


def change_pp(comparison_pct: float | None, baseline_pct: float | None) -> float | None:
    """Difference of two percentages, in **percentage points**.

    ``0.61 % → 0.77 %`` is ``+0.16 pp``; the relative change of the same pair is
    ``+26.2 %`` and comes from :func:`change_pct`. The two are never interchanged.
    """
    if comparison_pct is None or baseline_pct is None:
        return None
    return float(comparison_pct) - float(baseline_pct)


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

    # hazard provenance
    hazard_source: str | None = None
    hazard_dataset: str | None = None
    hazard_data_version: str | None = None
    hazard_country: str | None = None

    # impact function provenance — the same function for every model, by construction
    impact_function_id: int | None = None
    impact_function_name: str | None = None
    impact_function_source: str | None = None

    # exposure provenance
    exposure_source: str | None = None
    exposure_version: str | None = None

    # scenario — requested by the platform vs actually served by the dataset
    requested_scenario: str | None = None
    served_scenario: str | None = None
    scenario: str | None = None  # == requested_scenario; kept for table/export readability
    time_horizon: str | None = None

    # hazard
    hazard_intensity: float | None = None
    hazard_intensity_unit: str | None = None

    # frequency semantics
    probability: float | None = None
    return_period_years: float | None = None

    # financial
    asset_value_usd: float | None = None
    asset_value_currency: str | None = None
    potential_loss_usd: float | None = None
    potential_loss_return_period_years: float | None = None
    eal_usd: float | None = None
    eal_as_pct_of_assets: float | None = None

    risk_level: str | None = None
    risk_level_criteria: str | None = None

    # set only by pairing two runs of the same model (climate_change_multiplier); never configured
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

        * ``scenario`` mirrors ``requested_scenario`` when not set explicitly.
        * A served scenario that differs from the requested one turns any priced row into
          ``SCENARIO_MISMATCH`` — the number would be for a scenario nobody asked for.
        * Under a non-financial status every financial field is cleared, so a caller cannot
          leave a stale or fabricated number behind.
        * ``eal_as_pct_of_assets``, the risk band and ``probability`` are derived, never set.
        """
        if self.scenario is None:
            self.scenario = self.requested_scenario
        if (
            self.requested_scenario is not None
            and self.served_scenario is not None
            and self.requested_scenario != self.served_scenario
            and self.calculation_status
            in {CalculationStatus.FULL.value, CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value}
        ):
            self.calculation_status = CalculationStatus.SCENARIO_MISMATCH.value
            self.status_detail = (
                f"requested_scenario={self.requested_scenario} but the dataset serves "
                f"served_scenario={self.served_scenario}; the loss is not reported under the "
                "requested key"
            )
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultRow:
        """Rebuild a row from its ``to_dict`` form (worker output crossing the GPL boundary)."""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------- #
# Model-vs-model comparison — source-independent                               #
# --------------------------------------------------------------------------- #
def comparison_row(baseline: ResultRow, comparison: ResultRow) -> dict[str, Any]:
    """Two rows for the same facility and hazard, with every change form kept distinct.

    ``*_change_usd`` is absolute (USD), ``*_change_pct`` is relative (%), and
    ``eal_as_pct_assets_change_pp`` is a difference of percentages (percentage points).
    The rows must describe the same facility and hazard; comparing across them is a caller
    error, not something to paper over. ``same_impact_function`` is reported so a
    comparison that accidentally changed the vulnerability assumption is visible.
    """
    if (baseline.facility_id, baseline.hazard_type) != (
        comparison.facility_id,
        comparison.hazard_type,
    ):
        raise ValueError(
            f"cannot compare {baseline.facility_id}/{baseline.hazard_type} with "
            f"{comparison.facility_id}/{comparison.hazard_type}"
        )
    same_if = (
        baseline.impact_function_id is not None
        and baseline.impact_function_id == comparison.impact_function_id
    )
    return {
        "facility_id": baseline.facility_id,
        "facility_name": baseline.facility_name,
        "hazard_type": baseline.hazard_type,
        "baseline_model_id": baseline.model_id,
        "comparison_model_id": comparison.model_id,
        "baseline_hazard_source": baseline.hazard_source,
        "comparison_hazard_source": comparison.hazard_source,
        "same_impact_function": same_if,
        "impact_function_id": baseline.impact_function_id if same_if else None,
        # hazard
        "baseline_hazard_intensity": baseline.hazard_intensity,
        "comparison_hazard_intensity": comparison.hazard_intensity,
        "hazard_intensity_unit": baseline.hazard_intensity_unit,
        "hazard_intensity_change_pct": change_pct(
            comparison.hazard_intensity, baseline.hazard_intensity
        ),
        # potential loss
        "baseline_potential_loss_usd": baseline.potential_loss_usd,
        "comparison_potential_loss_usd": comparison.potential_loss_usd,
        "potential_loss_change_usd": change_abs(
            comparison.potential_loss_usd, baseline.potential_loss_usd
        ),
        "potential_loss_change_pct": change_pct(
            comparison.potential_loss_usd, baseline.potential_loss_usd
        ),
        # EAL
        "baseline_eal_usd": baseline.eal_usd,
        "comparison_eal_usd": comparison.eal_usd,
        "eal_change_usd": change_abs(comparison.eal_usd, baseline.eal_usd),
        "eal_change_pct": change_pct(comparison.eal_usd, baseline.eal_usd),
        # EAL / assets — percentage points, plus the relative change under its own name
        "baseline_eal_as_pct_of_assets": baseline.eal_as_pct_of_assets,
        "comparison_eal_as_pct_of_assets": comparison.eal_as_pct_of_assets,
        "eal_as_pct_assets_change_pp": change_pp(
            comparison.eal_as_pct_of_assets, baseline.eal_as_pct_of_assets
        ),
        "eal_as_pct_assets_relative_change_pct": change_pct(
            comparison.eal_as_pct_of_assets, baseline.eal_as_pct_of_assets
        ),
        # bands and statuses
        "baseline_risk_level": baseline.risk_level,
        "comparison_risk_level": comparison.risk_level,
        "baseline_calculation_status": baseline.calculation_status,
        "comparison_calculation_status": comparison.calculation_status,
        "baseline_scenario": baseline.served_scenario or baseline.scenario,
        "comparison_scenario": comparison.served_scenario or comparison.scenario,
        "comparison_kind": "hazard source comparison — not a climate-change multiplier",
    }


def compare_models(
    rows: Iterable[ResultRow],
    baseline_model: str,
    comparison_model: str,
    facility_id: str,
    hazard_type: str,
) -> dict[str, Any] | None:
    """The :func:`comparison_row` of two models for one facility and hazard.

    Source-independent: works for ``GLOBAL_BASELINE`` vs ``DATA_API_COUNTRY`` today and
    ``DATA_API_COUNTRY`` vs ``KOREA_LOCAL`` once that adapter exists. Returns None when
    either row is absent — a missing model is reported as "not available", never filled.
    """
    by_model = {
        r.model_id: r for r in rows if r.facility_id == facility_id and r.hazard_type == hazard_type
    }
    base, comp = by_model.get(baseline_model), by_model.get(comparison_model)
    if base is None or comp is None:
        return None
    return comparison_row(base, comp)


def relabel_comparison(cmp: dict[str, Any], label: str) -> dict[str, Any]:
    """Rename ``comparison_*`` keys to ``<label>_*`` for a named export sheet.

    ``global_vs_country`` uses ``label="country"``; ``global_vs_korea_local`` uses
    ``label="korea_local"``. The arithmetic is untouched — only the column names change.
    """
    prefix = "comparison_"
    return {
        (label + "_" + k[len(prefix) :] if k.startswith(prefix) else k): v for k, v in cmp.items()
    }


# --------------------------------------------------------------------------- #
# Climate-change multiplier — same model, two periods                          #
# --------------------------------------------------------------------------- #
def climate_change_multiplier(baseline: ResultRow, future: ResultRow) -> dict[str, Any]:
    """``future_eal / baseline_eal`` from two runs of the **same model**, or null with a reason.

    Refused, with the reason recorded, when the two rows come from different models — that
    ratio is a hazard-source comparison (:func:`comparison_row`), not a climate signal —
    when either run has no EAL, or when the baseline is zero. Never a configured constant.
    """
    value: float | None = None
    detail: str | None = None
    if baseline.model_id != future.model_id:
        detail = (
            f"rows come from different models ({baseline.model_id} vs {future.model_id}); "
            "that is a hazard-source comparison, not a climate-change multiplier"
        )
    elif (baseline.facility_id, baseline.hazard_type) != (future.facility_id, future.hazard_type):
        detail = "rows describe different facilities or hazards"
    elif baseline.eal_usd is None or future.eal_usd is None:
        detail = "one of the two runs produced no EAL"
    elif baseline.eal_usd == 0:
        detail = "baseline EAL is zero — ratio undefined"
    elif (baseline.served_scenario or baseline.scenario, baseline.time_horizon) == (
        future.served_scenario or future.scenario,
        future.time_horizon,
    ):
        detail = "baseline and future runs share scenario and period — nothing to multiply"
    else:
        value = float(future.eal_usd) / float(baseline.eal_usd)
    return {
        "climate_change_multiplier": value,
        "multiplier_definition": (
            "future_EAL / baseline_EAL, both from CLIMADA ImpactCalc under the same model_id"
        ),
        "model_id": baseline.model_id if baseline.model_id == future.model_id else None,
        "baseline_period": baseline.time_horizon,
        "future_period": future.time_horizon,
        "baseline_scenario": baseline.served_scenario or baseline.scenario,
        "future_scenario": future.served_scenario or future.scenario,
        "baseline_eal_usd": baseline.eal_usd,
        "future_eal_usd": future.eal_usd,
        "detail": detail,
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
