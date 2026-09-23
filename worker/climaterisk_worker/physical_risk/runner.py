"""Run the three hazard models over a portfolio and emit rows + comparisons.

Worker entry for ``mode = "physical_risk_models"``. For every requested hazard and model
the adapter is resolved once, its hazard loaded once (cropped to the portfolio), and every
facility priced against it with :func:`engine.calculate` — the same impact function for
every model by construction. Adapters that cannot load (``NOT_IMPLEMENTED``,
``NO_HAZARD_DATA``) yield rows with that status and null numbers; an adapter that raises
yields ``ERROR`` rows with the exception text. One failing model never aborts the run.

Output (``result.json``)::

    {"status": "ok", "rows": [...ResultRow...],
     "comparisons": {"global_vs_country": [...], "global_vs_korea_local": [...]},
     "readiness": {...}, "adapters": [...descriptions...], "detail": ...}

The comparison frames are built by the backend-side ``results_table`` (pure), so the
worker and the API cannot disagree on their columns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from climaterisk_worker.physical_risk import adapters, engine

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from climaterisk.physical_risk.metrics import (  # noqa: E402
    CalculationStatus,
    ModelId,
    ResultRow,
    attach_climate_multipliers,
)
from climaterisk.physical_risk.models import readiness  # noqa: E402
from climaterisk.physical_risk.results_table import comparison_frame  # noqa: E402

#: Request ``hazards`` keys → CLIMADA tags this runner prices. ``HEAT`` = the heatwave layer.
HAZARD_TAGS: dict[str, str] = {"RF": "RF", "TC": "TC", "HEAT": "HW"}


def _bbox(facilities: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    lats = [float(f["latitude"]) for f in facilities]
    lons = [float(f["longitude"]) for f in facilities]
    if not lats:
        return None
    return min(lats), min(lons), max(lats), max(lons)


def _iso3_for(facilities: list[dict[str, Any]], explicit: str | None) -> str | None:
    """A single ISO3 for the portfolio: explicit, else CLIMADA's own point→country lookup."""
    if explicit:
        return explicit.upper()
    try:
        import numpy as np
        from climada.util import coordinates as u_coord

        lats = np.array([float(f["latitude"]) for f in facilities])
        lons = np.array([float(f["longitude"]) for f in facilities])
        codes = {int(c) for c in u_coord.get_country_code(lats, lons)} - {0}
        if len(codes) == 1:
            return str(u_coord.country_to_iso(list(codes), "alpha3")[0])
    except Exception:
        return None
    return None


def _rows_for(
    adapter: Any,
    tag: str,
    facilities: list[dict[str, Any]],
    iso3: str | None,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[list[ResultRow], dict[str, Any]]:
    """Every facility's row for one adapter, plus the adapter description actually used."""
    desc = adapter.describe()
    common: dict[str, Any] = {
        "model_id": desc.model_id,
        "iso3": iso3,
        "hazard_source": desc.hazard_source,
        "hazard_dataset": desc.hazard_dataset,
        "hazard_data_version": desc.hazard_data_version,
        "hazard_country": desc.hazard_country,
        "requested_scenario": desc.requested_scenario,
        "served_scenario": desc.served_scenario,
        "time_horizon": desc.time_horizon,
    }
    if desc.status in (adapters.NOT_IMPLEMENTED, adapters.NO_HAZARD_DATA):
        status = (
            CalculationStatus.NOT_IMPLEMENTED.value
            if desc.status == adapters.NOT_IMPLEMENTED
            else CalculationStatus.NO_HAZARD_DATA.value
        )
        rows = [
            engine.calculate(
                f, None, tag, status_override=status, status_detail=desc.detail, **common
            )
            for f in facilities
        ]
        return rows, desc.to_dict()

    try:
        hazard = adapter.load(bbox)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        rows = [
            engine.calculate(
                f,
                None,
                tag,
                status_override=CalculationStatus.ERROR.value,
                status_detail=detail,
                **common,
            )
            for f in facilities
        ]
        return rows, {**desc.to_dict(), "load_error": detail}

    desc = adapter.describe()  # the version is known only after the load
    common["hazard_data_version"] = desc.hazard_data_version
    rows = [engine.calculate(f, hazard, tag, **common) for f in facilities]
    return rows, desc.to_dict()


def compute_physical_risk_models(request: dict[str, Any]) -> dict[str, Any]:
    """The worker-side computation for ``mode = "physical_risk_models"``.

    Request fields: ``facilities`` (``facility_id``, ``facility_name``, ``latitude``,
    ``longitude``, ``asset_value_usd``, ``asset_value_currency``, ``property_type``),
    ``climate_scenario``, ``target_year``, optional ``hazards`` (subset of RF/TC/HEAT),
    ``models`` (subset of the three ids), ``country`` (ISO3) and ``baseline_scenario``
    (a second scenario run under the same models, the only source of a
    ``climate_change_multiplier``; its rows are returned separately as ``baseline_rows``).
    """
    facilities: list[dict[str, Any]] = list(request.get("facilities") or [])
    scenario = str(request.get("climate_scenario") or "rcp45")
    year = int(request.get("target_year") or 2050)
    hazards = [h for h in (request.get("hazards") or list(HAZARD_TAGS)) if h in HAZARD_TAGS]
    models = [
        m
        for m in (request.get("models") or [x.value for x in ModelId])
        if m in {x.value for x in ModelId}
    ]
    iso3 = _iso3_for(facilities, request.get("country"))
    bbox = _bbox(facilities)

    notes: list[str] = []
    if not facilities:
        notes.append("portfolio has no facilities")
    if iso3 is None and facilities:
        notes.append(
            "portfolio spans several countries or none could be resolved — DATA_API_COUNTRY "
            "and KOREA_LOCAL need one ISO3; those rows are NO_HAZARD_DATA"
        )

    progress = _Progress(request.get("out_dir"), len(facilities) * len(hazards) * len(models))
    rows, used = _all_model_rows(
        facilities, scenario, year, hazards, models, iso3, bbox, progress=progress
    )

    # Optional same-model baseline run: the only legitimate source of a climate-change
    # multiplier (future_EAL / baseline_EAL within one model_id). Baseline rows are kept
    # apart from ``rows`` so the model comparisons stay one-row-per-model.
    baseline_scenario = request.get("baseline_scenario")
    baseline_rows: list[ResultRow] = []
    multipliers: list[dict[str, Any]] = []
    if baseline_scenario and str(baseline_scenario) != scenario:
        progress.extend(len(facilities) * len(hazards) * len(models))
        baseline_rows, used_base = _all_model_rows(
            facilities, str(baseline_scenario), year, hazards, models, iso3, bbox, progress=progress
        )
        used.extend({**d, "role": "baseline"} for d in used_base)
        multipliers = attach_climate_multipliers(rows, baseline_rows)

    g, c, k = (
        ModelId.GLOBAL_BASELINE.value,
        ModelId.DATA_API_COUNTRY.value,
        ModelId.KOREA_LOCAL.value,
    )
    return {
        "status": "ok",
        "climate_scenario": scenario,
        "target_year": year,
        "baseline_scenario": str(baseline_scenario) if baseline_rows else None,
        "country": iso3,
        "rows": [r.to_dict() for r in rows],
        "baseline_rows": [r.to_dict() for r in baseline_rows],
        "climate_change_multipliers": multipliers,
        "comparisons": {
            "global_vs_country": comparison_frame(rows, g, c, "country"),
            "global_vs_korea_local": comparison_frame(rows, g, k, "korea_local"),
        },
        "readiness": readiness(),
        "adapters": used,
        "impact_function_fixed": _same_impact_function_per_hazard(rows),
        "detail": "; ".join(notes) or None,
    }


def _all_model_rows(
    facilities: list[dict[str, Any]],
    scenario: str,
    year: int,
    hazards: list[str],
    models: list[str],
    iso3: str | None,
    bbox: tuple[float, float, float, float] | None,
    progress: _Progress | None = None,
) -> tuple[list[ResultRow], list[dict[str, Any]]]:
    """Every (hazard x model) adapter once, every facility priced against it."""
    rows: list[ResultRow] = []
    used: list[dict[str, Any]] = []
    for key in hazards:
        tag = HAZARD_TAGS[key]
        for model in models:
            if progress is not None:
                progress.step(f"{key} / {model}", len(facilities))
            if iso3 is None and model != ModelId.GLOBAL_BASELINE.value:
                for f in facilities:
                    rows.append(
                        engine.calculate(
                            f,
                            None,
                            tag,
                            model_id=model,
                            requested_scenario=scenario,
                            status_override=CalculationStatus.NO_HAZARD_DATA.value,
                            status_detail="no single country for the portfolio",
                        )
                    )
                continue
            adapter = adapters.adapter_for(tag, model, scenario, year, iso3 or "")
            model_rows, desc = _rows_for(adapter, tag, facilities, iso3, bbox)
            rows.extend(model_rows)
            used.append(desc)
    return rows, used


class _Progress:
    """Coarse progress for the UI: rewrites ``<out_dir>/progress.json`` per hazard x model.

    Counts *calculations* (facility x hazard x model). Silent when no ``out_dir`` is given
    (CLI, tests) or when the file cannot be written — progress is a convenience, never a
    reason to fail a run.
    """

    def __init__(self, out_dir: str | None, total: int) -> None:
        self.path = Path(out_dir) / "progress.json" if out_dir else None
        self.total = total
        self.done = 0
        self.current: str | None = None
        self._write()

    def extend(self, more: int) -> None:
        self.total += more
        self._write()

    def step(self, label: str, n: int) -> None:
        """Batch ``label`` (``n`` calculations) starts; the previous batch counts as done."""
        self.current = label
        self._write()
        self.done += n
        self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(
                json.dumps({"done": self.done, "total": self.total, "current": self.current}),
                encoding="utf-8",
            )
        except OSError:
            return


def _same_impact_function_per_hazard(rows: list[ResultRow]) -> dict[str, bool]:
    """Per hazard tag: did every priced model use the same impact function **per facility**?

    The invariant is fixed-across-models, not fixed-across-facilities: two facilities with
    different property types legitimately get different JRC sector curves, but one facility
    must see one function id under every model that priced it.
    """
    out: dict[str, bool] = {}
    for tag in {r.hazard_type for r in rows}:
        per_facility: dict[str, set[int]] = {}
        for r in rows:
            if r.hazard_type == tag and r.impact_function_id is not None:
                per_facility.setdefault(r.facility_id, set()).add(int(r.impact_function_id))
        out[tag] = all(len(ids) <= 1 for ids in per_facility.values())
    return out
