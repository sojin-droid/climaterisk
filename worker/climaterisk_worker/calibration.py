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

from climaterisk_worker import validation, vulnerability

#: Observed series this runner can read. Mirrors ``engines.base.OBSERVED_SOURCES``; the 재해연보
#: loader (spec F1) is behind a data gate, so selecting it returns an explicit "not available"
#: error instead of quietly falling back to EM-DAT.
OBSERVED_SOURCES: tuple[str, ...] = ("emdat", "disaster_yearbook")

#: Sub-perils the TC runner actually models: the Emanuel curve is wind only — surge is a separate
#: peril and rain is not represented (docs/OBSERVED_LOSSES_KR_SPEC.md §6-1).
MODEL_COVERS_SUBPERILS: tuple[str, ...] = ("wind",)
#: What an EM-DAT tropical-cyclone loss contains: the whole event's damage.
EMDAT_COVERS_SUBPERILS: tuple[str, ...] = validation.TC_AGGREGATE_SUBPERILS
#: Unit of ``emdat_to_impact`` output — CLIMADA converts EM-DAT's "'000 US$" to US dollars. The
#: reference-year/CPI basis of that conversion is not verified here, hence price_basis "unknown".
EMDAT_OUTPUT_UNIT = "USD"

#: Option keys. The caller must *declare* the modelled exposure's spatial scope, and may
#: explicitly accept an incomparable observed/modelled pair — which is then recorded as such.
EXPOSURE_SCOPE_KEY = "exposure_scope"
ALLOW_INCOMPARABLE_KEY = "allow_incomparable_calibration"

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
    observed_unit: str = "",
    observed_currency: str = "",
    observed_price_basis: str = "",
    target_covers_subperils: tuple[str, ...] | list[str] = (),
    model_covers_subperils: tuple[str, ...] | list[str] = (),
    scope: dict[str, Any] | None = None,
    comparison_status: str = validation.COMPARISON_UNKNOWN,
    comparability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the persisted/reported calibration record (pure; no CLIMADA).

    ``fit_status`` and ``comparison_status`` are **different questions** and must not be
    collapsed. ``fit_status="fitted"`` says the optimiser converged; ``comparison_status``
    says whether the observed and modelled quantities were comparable at all. A converged fit
    on an incomparable pair is ``fitted`` + ``not_comparable`` — optimisation success is not
    scientific validity (docs/OBSERVED_LOSSES_KR_SPEC.md §6-1, §8).

    The metadata arguments default to empty/unknown so records written before they existed, and
    callers that do not supply them, keep the original keys and values.
    """
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
        # --- comparability metadata (2026-09-07); "unknown" until a caller states it ---
        "observed_unit": observed_unit,
        "observed_currency": observed_currency,
        "observed_price_basis": observed_price_basis or "unknown",
        "target_covers_subperils": list(target_covers_subperils),
        "model_covers_subperils": list(model_covers_subperils),
        "scope": dict(scope) if scope else {},
        "comparison_status": comparison_status,
        "comparability": comparability or {},
    }


def portfolio_currency(assets: list[dict[str, Any]]) -> str:
    """The single currency the modelled losses are in, or ``""`` when assets disagree.

    An empty result makes the unit check report *unknown* rather than assume USD.
    """
    currencies = {str(a.get("currency") or "").strip().upper() for a in assets}
    currencies.discard("")
    return next(iter(currencies)) if len(currencies) == 1 else ""


def calibration_gate(
    *,
    observed: validation.ObservedSeries,
    modelled_unit: str,
    modelled_scope: str,
    allow_incomparable: bool = False,
) -> dict[str, Any]:
    """Decide whether this observed/modelled pair may be fitted at all.

    Wraps :func:`validation.comparability_report` so the calibration runner and the validation
    path refuse the same pairs for the same reasons. Blocking is the default: a national observed
    total against an undeclared or point-scale exposure, or an aggregate observed loss against a
    wind-only model, is not a calibration — it is a parameter absorbing someone else's damage.
    ``allow_incomparable`` lets a caller proceed deliberately; the verdict is still recorded.
    """
    report = validation.comparability_report(
        observed=observed,
        modelled_unit=modelled_unit,
        modelled_covers=MODEL_COVERS_SUBPERILS,
        modelled_scope=modelled_scope,
    )
    report["allow_incomparable"] = bool(allow_incomparable)
    report["proceed"] = bool(report["calibration_allowed"] or allow_incomparable)
    return report


def _blocked(report: dict[str, Any], observed: validation.ObservedSeries) -> dict[str, Any]:
    """The refusal payload — never phrased as a completed calibration."""
    return {
        "status": "error",
        "fit_status": "not_attempted",
        "comparison_status": report["comparison_status"],
        "blockers": report["blockers"],
        "comparability": report,
        "observed_source": observed.source,
        "detail": (
            "calibration blocked — observed and modelled quantities are not comparable: "
            + "; ".join(report["blockers"])
            + f". Fix the mismatch, or set options['{ALLOW_INCOMPARABLE_KEY}']=true to fit "
            "anyway (the record is then stamped comparison_status="
            f"'{validation.COMPARISON_NOT_COMPARABLE}'). See docs/OBSERVED_LOSSES_KR_SPEC.md "
            "§6-1 (sub-peril coverage) and §8 (spatial scope)."
        ),
    }


def compute_calibration(request: dict[str, Any]) -> dict[str, Any]:
    """Calibrate TC ``v_half`` to an observed loss series for the portfolio's country.

    Only ``observed_source="emdat"`` has a reader; ``disaster_yearbook`` is refused with an
    explicit data-gate message. Before fitting, :func:`calibration_gate` checks that the observed
    and modelled quantities are actually comparable (units, sub-peril coverage, spatial scope).
    """
    assets: list[dict[str, Any]] = request["assets"]
    if not assets:
        return {"status": "error", "detail": "portfolio has no assets"}

    source = str(request.get("observed_source") or "emdat")
    if source not in OBSERVED_SOURCES:
        return {
            "status": "error",
            "detail": f"unknown observed_source '{source}' (expected {OBSERVED_SOURCES})",
        }
    if source == "disaster_yearbook":
        return {
            "status": "error",
            "detail": (
                "행정안전부 재해연보 (disaster_yearbook) has no loader yet. The field names ARE "
                "confirmed (컬럼정의서 + Swagger, 2026-09-07: wrttimeid, seq, typhoon, …), but "
                "three things still block a loader: no data.go.kr service key for 15107318; the "
                "API publishes no unit element (the source publication uses 천원 in detail "
                "tables and 백만원 in summary tables, and that cannot be transferred to the "
                "API); and `seq` (분류 일련번호) has no label, so a property-damage row cannot "
                "be told from a casualty row. No substitute series is used. "
                "See docs/OBSERVED_LOSSES_KR_SPEC.md §4, §5, §12."
            ),
        }

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

    # --- comparability gate, before the expensive fit -------------------------------------
    # EM-DAT reports a country total for the whole event (wind+surge+rain); the modelled side is
    # this request's exposure under a wind-only curve. Both facts are declared here so the gate
    # can refuse the pair instead of the optimiser silently reconciling them.
    options = request.get("options") or {}
    observed_series = validation.ObservedSeries(
        source=f"EM-DAT (public.emdat.be) CSV {os.path.basename(emdat)}",
        unit=EMDAT_OUTPUT_UNIT,
        peril="tropical_cyclone",
        covers_subperils=EMDAT_COVERS_SUBPERILS,
        scope=f"national:{iso3}",
        price_basis="unknown",
        notes="emdat_to_impact output; CPI/reference-year basis not verified in this runner",
    )
    gate = calibration_gate(
        observed=observed_series,
        modelled_unit=portfolio_currency(assets),
        modelled_scope=str(options.get(EXPOSURE_SCOPE_KEY) or ""),
        allow_incomparable=bool(options.get(ALLOW_INCOMPARABLE_KEY)),
    )
    if not gate["proceed"]:
        return _blocked(gate, observed_series)

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
            observed_unit=observed_series.unit,
            observed_currency=observed_series.unit_spec().currency or "",
            observed_price_basis=observed_series.price_basis,
            target_covers_subperils=EMDAT_COVERS_SUBPERILS,
            model_covers_subperils=MODEL_COVERS_SUBPERILS,
            scope=gate["scope"],
            comparison_status=gate["comparison_status"],
            comparability=gate,
        )
        persisted: str | None = None
        if request.get("persist", True):
            persisted = str(vulnerability.save_calibration(record))
        span = f" ({period[0]}–{period[1]})" if period else ""
        detail = (
            f"{iso3} TC v_half fitted to EM-DAT mean annual loss (~{target:,.0f}/yr{span}): "
            f"{initial:.1f} → {calibrated:.1f} m/s. "
            f"Saved to {persisted or 'nowhere (persist=False)'}; used by runs with "
            f"tc_impf_default='calibrated'. Fitted, not validated."
        )
        if gate["comparison_status"] != validation.COMPARISON_COMPARABLE:
            # The optimiser converged on a pair the gate judged incomparable (the caller opted
            # in). Say so in the same sentence as the number, so the value cannot be read as a
            # completed calibration.
            detail += (
                f" WARNING — comparison_status={gate['comparison_status']}: "
                + "; ".join(gate["blockers"])
                + ". The fitted value absorbs whatever the model does not represent; treat it as "
                "a diagnostic, not a calibrated parameter."
            )
        return {"status": "ok", **record, "persisted_to": persisted, "detail": detail}
    except Exception as exc:
        return {
            "status": "error",
            "detail": f"calibration failed: {type(exc).__name__}: {str(exc)[:160]}",
        }
