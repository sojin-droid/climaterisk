"""A stand-in for the CLIMADA worker, for the browser (Playwright) tests.

The backend spawns ``<CLIMATERISK_WORKER_PYTHON> -m <CLIMATERISK_WORKER_MODULE> <run_dir>``
and reads ``<run_dir>/result.json`` back. Pointing those two settings at the backend's own
interpreter and this module gives the UI a worker that answers in milliseconds, needs no
conda env and no network, and is deterministic — which is what an end-to-end test of the
*flow* (select → run → download) needs. It is **not** a model: the numbers are a fixed
function of the asset value, the statuses follow the readiness table, and nothing here is
ever used outside ``tests/e2e``. The real worker path is covered by the regression tests
that run in ``.climada-env``.

Only the three-model mode is answered; every other mode gets ``engine_not_ready``, exactly
as the real worker does without CLIMADA.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from climaterisk.physical_risk.metrics import CalculationStatus, ModelId, ResultRow
from climaterisk.physical_risk.models import HAZARD_TAGS, READINESS, readiness
from climaterisk.physical_risk.results_table import comparison_frame

#: Deterministic hazard reads and loss shares per hazard tag.
_INTENSITY = {"RF": (1.5, "m"), "TC": (45.0, "m/s"), "HW": (36.2, "degC")}
_EAL_SHARE = {"RF": 0.002, "TC": 0.0005}


def _row(facility: dict[str, Any], key: str, model: str, scenario: str, year: int) -> ResultRow:
    tag = HAZARD_TAGS[key][0]
    cell = READINESS[key][model]
    value = facility.get("asset_value_usd")
    row = ResultRow(
        facility_id=str(facility["facility_id"]),
        facility_name=str(facility.get("facility_name") or facility["facility_id"]),
        hazard_type=tag,
        model_id=model,
        hazard_source=f"fake worker — {cell['hazard_source']}",
        hazard_dataset=f"fake_{tag}_{model}",
        requested_scenario=scenario,
        served_scenario=scenario,
        scenario=scenario,
        time_horizon=str(year),
        asset_value_usd=float(value) if value is not None else None,
        asset_value_currency=facility.get("asset_value_currency") or "USD",
        property_type=facility.get("property_type"),
        assessment_target=str(facility.get("assessment_target") or "FACILITY"),
        municipality_id=facility.get("municipality_id"),
        municipality_name=facility.get("municipality_name"),
        municipality_level=facility.get("municipality_level"),
        province_name=facility.get("province_name"),
        point_type=facility.get("point_type"),
        latitude=float(facility["latitude"]),
        longitude=float(facility["longitude"]),
        exposure_source="e2e fixture",
    )
    status = cell["status"]
    if status in ("NOT_IMPLEMENTED", "NO_HAZARD_DATA"):
        row.calculation_status = status
        row.status_detail = cell["detail"]
        return row.finalise()
    intensity, unit = _INTENSITY[tag]
    row.hazard_intensity, row.hazard_intensity_unit = intensity, unit
    if status == "HAZARD_ONLY" or tag == "HW":
        row.calculation_status = CalculationStatus.HAZARD_ONLY.value
        row.status_detail = "fake worker: hazard-only tag"
        return row.finalise()
    if value is None or float(value) <= 0:
        row.calculation_status = CalculationStatus.NO_EXPOSURE_DATA.value
        row.status_detail = "no asset value supplied"
        return row.finalise()
    row.impact_function_id = 22 if tag == "RF" else 9
    row.impact_function_name = "fake published function"
    row.impact_function_source = "fake worker (same id under every model)"
    row.eal_usd = float(value) * _EAL_SHARE[tag]
    row.potential_loss_usd = row.eal_usd * 30.0
    row.potential_loss_return_period_years = 100.0
    row.return_period_years = 100.0
    row.calculation_status = CalculationStatus.FULL.value
    return row.finalise()


def physical_risk_models(request: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    facilities = list(request.get("facilities") or [])
    scenario = str(request.get("climate_scenario") or "rcp45")
    year = int(request.get("target_year") or 2050)
    hazards = [h for h in (request.get("hazards") or list(HAZARD_TAGS)) if h in HAZARD_TAGS]
    models = [
        m for m in (request.get("models") or [x.value for x in ModelId]) if m in READINESS["RF"]
    ]
    target = str(request.get("assessment_target") or "FACILITY")
    rows = [_row(f, k, m, scenario, year) for f in facilities for k in hazards for m in models]
    n = len(rows)
    (run_dir / "progress.json").write_text(
        json.dumps({"done": n, "total": n, "current": None}), encoding="utf-8"
    )
    g, c, k = (
        ModelId.GLOBAL_BASELINE.value,
        ModelId.DATA_API_COUNTRY.value,
        ModelId.KOREA_LOCAL.value,
    )
    municipality_dataset = None
    if target == "MUNICIPALITY":
        from climaterisk.physical_risk.municipalities import dataset_summary

        municipality_dataset = dataset_summary()
    return {
        "status": "ok",
        "assessment_target": target,
        "municipality_dataset": municipality_dataset,
        "climate_scenario": scenario,
        "target_year": year,
        "baseline_scenario": None,
        "country": request.get("country") or "KOR",
        "rows": [r.to_dict() for r in rows],
        "baseline_rows": [],
        "climate_change_multipliers": [],
        "exposure_growth": None,
        "comparisons": {
            "global_vs_country": comparison_frame(rows, g, c, "country"),
            "global_vs_korea_local": comparison_frame(rows, g, k, "korea_local"),
        },
        "readiness": readiness(),
        "adapters": [],
        "impact_function_fixed": dict.fromkeys({r.hazard_type for r in rows}, True),
        "detail": "fake worker (tests/e2e) — numbers are fixtures, not a model",
    }


def main(argv: list[str]) -> int:
    run_dir = Path(argv[1])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    if request.get("mode") == "physical_risk_models":
        output = physical_risk_models(request, run_dir)
    else:
        output = {
            "status": "engine_not_ready",
            "climate_scenario": request.get("climate_scenario", ""),
            "results": [],
            "detail": "fake worker (tests/e2e) answers only physical_risk_models",
        }
    (run_dir / "result.json").write_text(json.dumps(output, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
