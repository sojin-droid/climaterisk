"""The physical-risk calculation: exposure + published impact function + hazard -> CLIMADA.

One facility, one hazard, one model produces one
:class:`~climaterisk.physical_risk.metrics.ResultRow`. The arithmetic is CLIMADA's:
``ImpactCalc(exposures, impfset, hazard).impact()`` gives ``aai_agg`` and
``calc_freq_curve`` gives the return-period loss. Nothing is re-implemented here, and no
damage curve is authored — the functions come from
:mod:`climaterisk_worker.physical_risk.registry` and are the **same for every model**:
the model only decides where the hazard came from
(:mod:`climaterisk_worker.physical_risk.adapters`).

Deliberate refusals, each of which would otherwise produce a plausible-looking number:

* **Heat.** No CLIMADA impact function exists, so heat rows are ``HAZARD_ONLY`` with null
  financial fields. Never ``0.0``.
* **Unresolvable return periods.** ``calc_freq_curve`` saturates at the largest event
  rather than returning NaN, so a 100-year request against a set whose rarest event is a
  10-year event silently yields the 10-year loss. The engine checks
  ``1 / min(frequency)`` first and reports ``RETURN_PERIOD_NOT_RESOLVABLE`` instead.
* **Scenario mismatch.** A dataset that serves a different scenario than requested is
  recorded as ``requested_scenario`` / ``served_scenario`` and the row becomes
  ``SCENARIO_MISMATCH`` in :meth:`ResultRow.finalise` — the number is not filed under the
  requested key.
* **Climate-change multiplier.** Computed only as ``future_eal / baseline_eal`` from two
  real runs of the same model; otherwise null (``metrics.climate_change_multiplier``).

This module is separate from ``physical.py``: the legacy runners keep their behaviour and
their results, and the two are expected to differ — most visibly for flood, where the
legacy path rebuilds the JRC curve from an 8-point resampled JSON while this engine calls
``ImpfRiverFlood`` directly (11 points over 0-12 m).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from climaterisk_worker.physical_risk import registry

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:  # the metrics half lives in the backend package
    sys.path.insert(0, str(_SRC))

from climaterisk.physical_risk.metrics import (  # noqa: E402
    CalculationStatus,
    ResultRow,
    climate_change_multiplier,
    eal_as_pct_of_assets,
    jrc_sector_for,
    load_config,
    max_resolvable_return_period,
    risk_level,
)

__all__ = ["HAZARD_ONLY_HAZARDS", "PRICEABLE_HAZARDS", "calculate", "climate_change_multiplier"]

#: Hazard tags this engine can price, and the registry call that supplies the curve.
PRICEABLE_HAZARDS: tuple[str, ...] = ("RF", "TC")

#: Hazard tags that are computed as exposure only — no published impact function exists.
HAZARD_ONLY_HAZARDS: tuple[str, ...] = ("HM", "HW")


def _facility_exposure(facility: dict[str, Any], impf_col: str, impf_id: int) -> Any:
    """A one-row CLIMADA ``Exposures`` for a facility, valued in its own currency."""
    import pandas as pd
    from climada.entity import Exposures

    frame = pd.DataFrame(
        {
            "latitude": [float(facility["latitude"])],
            "longitude": [float(facility["longitude"])],
            "value": [float(facility.get("asset_value_usd") or 0.0)],
            impf_col: [int(impf_id)],
        }
    )
    return Exposures(frame, value_unit=str(facility.get("asset_value_currency") or "USD"))


def _hazard_intensity_at(hazard: Any, lat: float, lon: float) -> float | None:
    """Peak intensity the hazard set reaches at the nearest centroid to a point."""
    import numpy as np

    lats = np.asarray(hazard.centroids.lat, dtype=float)
    lons = np.asarray(hazard.centroids.lon, dtype=float)
    if lats.size == 0:
        return None
    idx = int(np.argmin((lats - lat) ** 2 + (lons - lon) ** 2))
    column = hazard.intensity[:, idx]
    peak = float(column.max()) if column.nnz else 0.0
    return peak


def _potential_loss(impact: Any, return_period: float) -> tuple[float | None, str | None]:
    """Loss at ``return_period``, or None with a reason when the set cannot resolve it."""
    import numpy as np

    frequencies = [float(f) for f in np.asarray(impact.frequency, dtype=float)]
    ceiling = max_resolvable_return_period(frequencies)
    if ceiling is None:
        return None, "hazard set carries no positive event frequency"
    if return_period > ceiling:
        return None, (
            f"requested {return_period:g}-year loss exceeds the {ceiling:.1f}-year ceiling "
            f"this event set can express (1 / min frequency); calc_freq_curve would return "
            f"the largest event's loss under a {return_period:g}-year label"
        )
    curve = impact.calc_freq_curve([return_period])
    value = float(np.asarray(curve.impact, dtype=float)[0])
    return value, None


def calculate(
    facility: dict[str, Any],
    hazard: Any,
    hazard_type: str,
    *,
    model_id: str,
    iso3: str | None = None,
    hazard_source: str | None = None,
    hazard_dataset: str | None = None,
    hazard_data_version: str | None = None,
    hazard_country: str | None = None,
    requested_scenario: str | None = None,
    served_scenario: str | None = None,
    scenario: str | None = None,
    time_horizon: str | None = None,
    exposure_source: str = "portfolio facility (lat/lon + asset_value_usd)",
    exposure_version: str | None = None,
    flood_region: str = "Asia",
    status_override: str | None = None,
    status_detail: str | None = None,
    config: dict[str, Any] | None = None,
) -> ResultRow:
    """Price one facility against one hazard set, or say why it cannot be priced.

    Args:
        facility: ``facility_id``, ``facility_name``, ``latitude``, ``longitude``,
            ``asset_value_usd``, and optionally ``asset_value_currency``, ``property_type``
            and other metadata.
        hazard: A CLIMADA ``Hazard``, or None when the model has none for this facility.
        hazard_type: Its tag — ``RF`` / ``TC`` are priced, ``HM`` / ``HW`` are hazard-only.
        model_id: Which hazard source this row belongs to (:class:`ModelId`).
        iso3: Country, used to pick the TC regional function from CLIMADA's own table.
        requested_scenario: The platform scenario key the run asked for.
        served_scenario: The scenario the dataset actually carries; a difference is
            recorded and turns a priced row into ``SCENARIO_MISMATCH``.
        status_override: A non-financial status the adapter already decided
            (``NOT_IMPLEMENTED``, ``NO_HAZARD_DATA``) — the row is returned with it and
            no calculation is attempted.

    Returns:
        A finalised :class:`ResultRow`; financial fields are null unless the status is
        FULL (or RETURN_PERIOD_NOT_RESOLVABLE, which keeps EAL only).
    """
    cfg = config or load_config()
    row = ResultRow(
        facility_id=str(facility["facility_id"]),
        facility_name=str(facility.get("facility_name") or facility["facility_id"]),
        hazard_type=hazard_type,
        model_id=model_id,
        hazard_source=hazard_source,
        hazard_dataset=hazard_dataset,
        hazard_data_version=hazard_data_version,
        hazard_country=hazard_country,
        exposure_source=exposure_source,
        exposure_version=exposure_version,
        requested_scenario=requested_scenario,
        served_scenario=served_scenario,
        scenario=scenario,
        time_horizon=time_horizon,
        asset_value_usd=(
            float(facility["asset_value_usd"])
            if facility.get("asset_value_usd") is not None
            else None
        ),
        asset_value_currency=facility.get("asset_value_currency"),
        property_type=facility.get("property_type"),
        floor_area_m2=facility.get("floor_area_m2"),
        year_built=facility.get("year_built"),
        structure_type=facility.get("structure_type"),
        definitions=dict(cfg["definitions"]),
    )
    lat, lon = float(facility["latitude"]), float(facility["longitude"])

    if status_override is not None:
        row.calculation_status = status_override
        row.status_detail = status_detail
        return row.finalise(cfg)

    if hazard is None:
        row.calculation_status = CalculationStatus.NO_HAZARD_DATA.value
        row.status_detail = status_detail or f"no {hazard_type} hazard available for this facility"
        return row.finalise(cfg)

    row.hazard_intensity = _hazard_intensity_at(hazard, lat, lon)
    row.hazard_intensity_unit = str(getattr(hazard, "units", "") or None)

    if hazard_type in HAZARD_ONLY_HAZARDS:
        heat = registry.heat_status()
        row.calculation_status = CalculationStatus.HAZARD_ONLY.value
        row.status_detail = heat["detail"]
        row.impact_function_source = "none available"
        row.confidence = "hazard exposure only — no financial loss computed"
        return row.finalise(cfg)

    if hazard_type not in PRICEABLE_HAZARDS:
        row.calculation_status = CalculationStatus.NO_IMPACT_FUNCTION.value
        row.status_detail = f"no published CLIMADA impact function registered for {hazard_type}"
        return row.finalise(cfg)

    # --- the published impact function, unmodified and model-independent -------------
    if hazard_type == "RF":
        sector = jrc_sector_for(row.property_type, cfg)
        func = registry.flood_impact_function(flood_region, sector)
        row.impact_function_source = (
            "CLIMADA Petals ImpfRiverFlood.from_jrc_region_sector "
            f"(region={flood_region!r}, sector={sector!r}) — Huizinga et al. 2017 JRC"
        )
    else:
        assigned = registry.tc_region_for_country(iso3) if iso3 else None
        if assigned is None:
            func = registry.tc_impact_function(10)  # CLIMADA's own Rest of The World
            row.impact_function_source = (
                "CLIMADA ImpfSetTropCyclone regional calibration (Eberenz et al. 2021) — "
                "country not in CLIMADA's region table, Rest of The World applied"
            )
        else:
            impf_id, code, name = assigned
            func = registry.tc_impact_function(impf_id)
            row.impact_function_source = (
                "CLIMADA ImpfSetTropCyclone regional calibration (Eberenz et al. 2021) — "
                f"{iso3} resolves to {code} {name!r} via "
                "ImpfSetTropCyclone.get_impf_id_regions_per_countries"
            )
    row.impact_function_id = int(func.id)
    row.impact_function_name = str(func.name)

    if row.asset_value_usd is None or row.asset_value_usd <= 0:
        row.calculation_status = CalculationStatus.NO_EXPOSURE_DATA.value
        row.status_detail = "facility carries no positive asset_value_usd"
        return row.finalise(cfg)

    # --- CLIMADA does the arithmetic ------------------------------------------------
    from climada.engine import ImpactCalc
    from climada.entity import ImpactFuncSet

    exposure = _facility_exposure(facility, f"impf_{hazard_type}", int(func.id))
    try:
        impact = ImpactCalc(exposure, ImpactFuncSet([func]), hazard).impact(
            save_mat=False, assign_centroids=True
        )
    except Exception as exc:  # a real failure, reported as one
        row.calculation_status = CalculationStatus.ERROR.value
        row.status_detail = f"{type(exc).__name__}: {exc}"
        return row.finalise(cfg)

    row.eal_usd = float(impact.aai_agg)
    target_rp = float(cfg["potential_loss"]["return_period_years"])
    loss, refusal = _potential_loss(impact, target_rp)
    if loss is None:
        row.calculation_status = CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value
        row.status_detail = refusal
        row.potential_loss_return_period_years = target_rp
        finalised = row.finalise(cfg)
        if finalised.calculation_status == CalculationStatus.RETURN_PERIOD_NOT_RESOLVABLE.value:
            # EAL survives: it uses every event's frequency and needs no extrapolation.
            finalised.eal_usd = float(impact.aai_agg)
            finalised.eal_as_pct_of_assets = eal_as_pct_of_assets(
                finalised.eal_usd, finalised.asset_value_usd
            )
            finalised.risk_level, finalised.risk_level_criteria = risk_level(
                finalised.eal_as_pct_of_assets, cfg
            )
        return finalised

    row.potential_loss_usd = loss
    row.potential_loss_return_period_years = target_rp
    row.return_period_years = target_rp
    row.calculation_status = CalculationStatus.FULL.value
    row.confidence = "published impact function, no local calibration"
    return row.finalise(cfg)
