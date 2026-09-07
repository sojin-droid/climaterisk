"""Calibrate an impact-function parameter against observed losses (EM-DAT) and persist it.

Method (unchanged by the 2026-09-07 audit fix): fit the Emanuel tropical-cyclone ``v_half``
so that the modelled **present-day average annual impact** (CLIMADA ``ImpactCalc`` on the
present-day TC hazard) matches the **observed mean annual loss** from EM-DAT
(``climada.engine.impact_data.emdat_to_impact``), by ``scipy.optimize.minimize_scalar``
(bounded) on the squared error. This is a one-parameter AAI match — **not** the
``climada.util.calibrate`` framework (no per-event cost function, no evaluator).

What the fix adds: the result is written as a JSON record under ``data/calibrations/``
(``vulnerability.save_calibration``) with full provenance (observed source and period,
objective, method, bounds, hazard, timestamp, schema version), and a later physical /
cost-benefit / uncertainty run can consume it by setting
``options["tc_impf_default"] = "calibrated"`` (``vulnerability.resolve_tc_vhalf``). The
record's ``fit_status`` is ``"fitted"``: it matches one observed series and has not been
validated against an independent one.

EM-DAT is login-gated and non-commercial; drop the CSV in and set ``CLIMATERISK_EMDAT_PATH``.
When it is absent this degrades with a clear, actionable error. Worker (CLIMADA) env only.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any

from climaterisk_worker import vulnerability

CALIBRATION_SCHEMA_VERSION = 1
V_HALF_BOUNDS = (25.7, 200.0)  # m/s; lower = Emanuel v_thresh, upper above Eberenz WP4 190.5
OBJECTIVE = (
    "squared error between modelled present-day aai_agg (CLIMADA ImpactCalc, Emanuel "
    "from_emanuel_usa with free v_half) and observed mean annual loss "
    "(EM-DAT total / years spanned)"
)
METHOD = "scipy.optimize.minimize_scalar(method='bounded') — one scalar parameter"
APPLIES_WHEN = (
    "RunConfig.options['tc_impf_default'] == 'calibrated' "
    "(physical, cost-benefit, uncertainty runs)"
)


def calibration_record(
    *,
    country: str,
    initial: float,
    calibrated: float,
    observed_annual_loss: float,
    modelled_annual_loss: float | None,
    observed_source: str,
    observed_period: tuple[int, int] | None,
    n_observed_events: int,
    hazard: str,
    n_assets: int,
    calibrated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the persisted/reported calibration record (pure; no CLIMADA)."""
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "peril": "tropical_cyclone",
        "param": "v_half",
        "country": country,
        "fit_status": "fitted",
        "initial": float(initial),
        "calibrated": float(calibrated),
        "observed_annual_loss": float(observed_annual_loss),
        "modelled_annual_loss_at_calibrated": (
            float(modelled_annual_loss) if modelled_annual_loss is not None else None
        ),
        "observed_source": observed_source,
        "observed_period": list(observed_period) if observed_period else [],
        "n_observed_events": int(n_observed_events),
        "hazard": hazard,
        "objective": OBJECTIVE,
        "method": METHOD,
        "bounds": list(V_HALF_BOUNDS),
        "n_assets": int(n_assets),
        "calibrated_at": calibrated_at
        or _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
        "applies_when": APPLIES_WHEN,
    }


def compute_calibration(request: dict[str, Any]) -> dict[str, Any]:
    """Calibrate TC ``v_half`` to EM-DAT observed losses for the portfolio's country."""
    assets: list[dict[str, Any]] = request["assets"]
    if not assets:
        return {"status": "error", "detail": "portfolio has no assets"}

    emdat = os.environ.get("CLIMATERISK_EMDAT_PATH")
    if not emdat or not os.path.isfile(emdat):
        return {
            "status": "error",
            "detail": (
                "calibration needs an EM-DAT disaster-loss CSV (login-gated, non-commercial). "
                "Download EM-DAT and set CLIMATERISK_EMDAT_PATH (Data tab → EM-DAT)."
            ),
        }

    try:
        import numpy as np
        from climada.engine import ImpactCalc
        from climada.engine.impact_data import emdat_to_impact
        from climada.entity import ImpactFuncSet
        from climada.entity.impact_funcs.trop_cyclone import ImpfTropCyclone

        from climaterisk_worker.cost_benefit import _tc_hazard
        from climaterisk_worker.physical import (
            _build_exposures,
            _per_asset_iso3,
            _single_country_iso3,
        )
    except Exception as exc:
        return {"status": "error", "detail": f"calibration engine unavailable: {exc}"}

    iso3s = _per_asset_iso3([a["lat"] for a in assets], [a["lon"] for a in assets])
    iso3 = _single_country_iso3(iso3s)
    if iso3 is None:
        return {
            "status": "error",
            "detail": "calibration needs a single-country portfolio (EM-DAT is by-country).",
        }

    try:
        import datetime

        # Observed losses from EM-DAT for this country / peril (total over the record).
        obs = emdat_to_impact(emdat, "TC", countries=[iso3])
        imp_emdat = obs[0] if obs else None
        at_event = np.asarray(getattr(imp_emdat, "at_event", []), dtype=float)
        observed = float(np.nansum(at_event)) if at_event.size else 0.0
        if observed <= 0:
            return {
                "status": "error",
                "detail": f"no EM-DAT tropical-cyclone losses found for {iso3}.",
            }

        # Annualise over the calendar-year span the EM-DAT record covers — NOT the
        # event count (the present-day hazard set holds thousands of synthetic events,
        # so dividing by it would yield a per-event, not per-year, loss).
        dates = np.asarray(getattr(imp_emdat, "date", []), dtype=float)
        event_years = [datetime.date.fromordinal(int(d)).year for d in dates if d > 0]
        n_years = max(1, max(event_years) - min(event_years) + 1) if event_years else 1
        period = (min(event_years), max(event_years)) if event_years else None

        # Present-day hazard + exposure; fit v_half so modelled AAI matches observed.
        impf_ids = [1] * len(assets)
        exp, _ = _build_exposures(assets, "impf_TC", impf_ids)
        haz = _tc_hazard(iso3, "None", None)

        def modelled_aai(v_half: float) -> float:
            impf_set = ImpactFuncSet([ImpfTropCyclone.from_emanuel_usa(impf_id=1, v_half=v_half)])
            return float(ImpactCalc(exp, impf_set, haz).impact(assign_centroids=True).aai_agg)

        target = observed / n_years  # observed annual-average loss
        from scipy.optimize import minimize_scalar

        # Starting point reported for context = the v_half the impact run would use today
        # (regional preset or class default); minimize_scalar(bounded) does not take an x0.
        initial_vh, _src, _note = vulnerability.resolve_tc_vhalf(
            assets, iso3s, request.get("options")
        )
        initial = float(initial_vh[0])
        res = minimize_scalar(
            lambda v: (modelled_aai(v) - target) ** 2,
            bounds=V_HALF_BOUNDS,
            method="bounded",
        )
        calibrated = float(res.x)
        record = calibration_record(
            country=iso3,
            initial=initial,
            calibrated=calibrated,
            observed_annual_loss=target,
            modelled_annual_loss=modelled_aai(calibrated),
            observed_source=f"EM-DAT (public.emdat.be) CSV {os.path.basename(emdat)}",
            observed_period=period,
            n_observed_events=int(at_event.size),
            hazard=(
                "CLIMADA Data API synthetic TC, present-day (climate_scenario=None), catalog-first"
            ),
            n_assets=len(assets),
        )
        persisted: str | None = None
        if request.get("persist", True):
            persisted = str(vulnerability.save_calibration(record))
        return {
            "status": "ok",
            **record,
            "persisted_to": persisted,
            "detail": (
                f"{iso3} TC v_half fitted to EM-DAT mean annual loss (~{target:,.0f}/yr, "
                f"{period[0]}–{period[1]}): {initial:.1f} → {calibrated:.1f} m/s. "
                f"Saved to {persisted or 'nowhere (persist=False)'}; used by runs with "
                f"tc_impf_default='calibrated'. Fitted, not validated."
                if period
                else f"{iso3} TC v_half fitted to EM-DAT: {initial:.1f} → {calibrated:.1f} m/s"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "detail": f"calibration failed: {type(exc).__name__}: {str(exc)[:160]}",
        }
