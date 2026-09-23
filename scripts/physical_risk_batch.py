#!/usr/bin/env python
"""Batch physical-risk run: facilities CSV -> three models -> canonical table -> Excel.

Runs in the CLIMADA worker environment (it prices with CLIMADA and needs the Data API)::

    ./.climada-env/bin/python scripts/physical_risk_batch.py \\
        --facilities facilities.csv --out results.xlsx --json results.json \\
        --scenario rcp60 --year 2050 --country KOR [--baseline-scenario historical]

The CSV needs ``facility_id, facility_name, lat, lon, asset_value_usd`` (``latitude`` /
``longitude`` are accepted aliases); ``property_type`` and ``asset_value_currency`` are
optional. Every facility is priced against every hazard (RF, TC, HEAT) under every model
(GLOBAL_BASELINE, DATA_API_COUNTRY, KOREA_LOCAL) with the same published impact function;
rows that cannot be priced carry a status and null numbers, never zeros.

``--baseline-scenario`` runs the same models for a second scenario and fills
``climate_change_multiplier`` = future_EAL / baseline_EAL **within each model** — the only
form that ratio may take. Without it the column stays empty.

Legacy runners, catalog layers and results are untouched; this is the Phase 3 engine
(``worker/climaterisk_worker/physical_risk``) and the Phase 5 exporter
(``src/climaterisk/physical_risk/excel_export``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

_ALIASES = {
    "facility_id": ("facility_id", "id"),
    "facility_name": ("facility_name", "name"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "asset_value_usd": ("asset_value_usd", "value", "asset_value"),
    "property_type": ("property_type", "type", "sector"),
    "asset_value_currency": ("asset_value_currency", "currency"),
}
_REQUIRED = ("facility_id", "latitude", "longitude", "asset_value_usd")


def read_facilities(path: str | Path) -> list[dict[str, Any]]:
    """Facilities from a CSV, normalised to the engine's field names.

    Raises:
        ValueError: when a required column is missing or a coordinate/value is not numeric.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        lower = {h.lower(): h for h in header}
        picked: dict[str, str] = {}
        for field, names in _ALIASES.items():
            for n in names:
                if n in lower:
                    picked[field] = lower[n]
                    break
        missing = [f for f in _REQUIRED if f not in picked]
        if missing:
            raise ValueError(f"CSV lacks required column(s) {missing}; header was {header}")
        out: list[dict[str, Any]] = []
        for i, raw in enumerate(reader, start=2):
            row = {k: (raw.get(col) or "").strip() for k, col in picked.items()}
            try:
                fac: dict[str, Any] = {
                    "facility_id": row["facility_id"],
                    "facility_name": row.get("facility_name") or row["facility_id"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "asset_value_usd": float(row["asset_value_usd"])
                    if row["asset_value_usd"]
                    else None,
                    "asset_value_currency": row.get("asset_value_currency") or "USD",
                    "property_type": row.get("property_type") or None,
                }
            except ValueError as exc:
                raise ValueError(f"line {i}: {exc}") from exc
            if not fac["facility_id"]:
                raise ValueError(f"line {i}: empty facility_id")
            out.append(fac)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--facilities", required=True, help="CSV of facilities")
    ap.add_argument("--out", required=True, help="Excel workbook to write (.xlsx)")
    ap.add_argument("--json", default=None, help="also write the raw worker output here")
    ap.add_argument("--scenario", default="rcp45", help="requested climate scenario key")
    ap.add_argument("--year", type=int, default=2050, help="target year")
    ap.add_argument("--hazards", nargs="+", default=["RF", "TC", "HEAT"])
    ap.add_argument(
        "--models", nargs="+", default=["GLOBAL_BASELINE", "DATA_API_COUNTRY", "KOREA_LOCAL"]
    )
    ap.add_argument("--country", default=None, help="ISO3; resolved from the points if omitted")
    ap.add_argument(
        "--baseline-scenario",
        default=None,
        help="run the same models for this scenario too and fill climate_change_multiplier",
    )
    args = ap.parse_args(argv)

    facilities = read_facilities(args.facilities)
    print(f"{len(facilities)} facilities from {args.facilities}")

    from climaterisk_worker.physical_risk.runner import compute_physical_risk_models

    from climaterisk.physical_risk.excel_export import frames_from_output, write_workbook

    output = compute_physical_risk_models(
        {
            "facilities": facilities,
            "climate_scenario": args.scenario,
            "target_year": args.year,
            "hazards": args.hazards,
            "models": args.models,
            "country": args.country,
            "baseline_scenario": args.baseline_scenario,
        }
    )
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    frames = frames_from_output(output)
    write_workbook(frames, args.out)
    print(f"wrote {args.out}: " + ", ".join(f"{k}={len(v)}" for k, v in frames.items()))
    statuses: dict[str, int] = {}
    for r in output["rows"]:
        statuses[r["calculation_status"]] = statuses.get(r["calculation_status"], 0) + 1
    print("row statuses:", statuses)
    if output.get("detail"):
        print("detail:", output["detail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
