#!/usr/bin/env python
"""Batch physical-risk run: facilities CSV -> three models -> canonical table -> Excel.

Runs in the CLIMADA worker environment (it prices with CLIMADA and needs the Data API)::

    ./.climada-env/bin/python scripts/physical_risk_batch.py assets.csv \\
        --hazards flood,typhoon,heatwave --models recommended --output risk_report.xlsx \\
        [--scenario rcp60 --year 2050 --country KOR --baseline-scenario historical --json raw.json]

``--models recommended`` (the default) reads the readiness registry and picks, per hazard,
the most local data scope that can actually run (Korea today: country dataset for flood and
typhoon, KMA local data for heatwave — hazard only). ``all`` runs the three scopes; ``global``,
``country``, ``local`` name them directly. The older ``--facilities`` / ``--out`` spellings and
RF/TC/HEAT hazard keys keep working.

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
            if not (-90.0 <= fac["latitude"] <= 90.0 and -180.0 <= fac["longitude"] <= 180.0):
                raise ValueError(
                    f"line {i}: latitude/longitude out of range "
                    f"({fac['latitude']}, {fac['longitude']}) — expected decimal degrees"
                )
            if fac["asset_value_usd"] is not None and fac["asset_value_usd"] < 0:
                raise ValueError(
                    f"line {i}: asset_value_usd is negative ({fac['asset_value_usd']})"
                )
            out.append(fac)
    dupes = sorted(
        {
            f["facility_id"]
            for f in out
            if [g["facility_id"] for g in out].count(f["facility_id"]) > 1
        }
    )
    if dupes:
        raise ValueError(f"duplicate facility_id(s) {dupes}: each row must have a unique id")
    return out


_HAZARD_ALIASES = {
    "flood": "RF",
    "rf": "RF",
    "river_flood": "RF",
    "typhoon": "TC",
    "tc": "TC",
    "tropical_cyclone": "TC",
    "cyclone": "TC",
    "heatwave": "HEAT",
    "heat": "HEAT",
    "hw": "HEAT",
}
_MODEL_ALIASES = {
    "global": "GLOBAL_BASELINE",
    "global_baseline": "GLOBAL_BASELINE",
    "country": "DATA_API_COUNTRY",
    "data_api_country": "DATA_API_COUNTRY",
    "local": "KOREA_LOCAL",
    "korea_local": "KOREA_LOCAL",
}
ALL_MODELS = ["GLOBAL_BASELINE", "DATA_API_COUNTRY", "KOREA_LOCAL"]


def _split(tokens: list[str]) -> list[str]:
    """``["a,b", "c"]`` -> ``["a", "b", "c"]`` — commas and spaces both separate."""
    out: list[str] = []
    for t in tokens:
        out += [x.strip() for x in t.split(",") if x.strip()]
    return out


def parse_hazards(tokens: list[str]) -> list[str]:
    """Hazard keys from user words (``flood``, ``typhoon``, ``heatwave`` or RF/TC/HEAT)."""
    out: list[str] = []
    for t in _split(tokens):
        key = _HAZARD_ALIASES.get(t.lower(), t.upper())
        if key not in ("RF", "TC", "HEAT"):
            raise ValueError(f"unknown hazard {t!r}; use flood, typhoon or heatwave")
        if key not in out:
            out.append(key)
    return out


def parse_models(tokens: list[str], hazards: list[str]) -> list[str]:
    """Model ids from ``recommended`` / ``all`` / user words (``global``, ``country``, ``local``).

    ``recommended`` reads the readiness registry and returns the union of the models it
    picks for the requested hazards (the runner prices every hazard under every model; a
    model that cannot run a hazard yields a status row, never a number).
    """
    words = [t.lower() for t in _split(tokens)]
    if words in (["recommended"], []):
        from climaterisk.physical_risk.display_copy import recommended_models

        picks = recommended_models(hazards)
        return [m for m in ALL_MODELS if m in set(picks.values())]
    if words == ["all"]:
        return list(ALL_MODELS)
    out: list[str] = []
    for w in words:
        mid = _MODEL_ALIASES.get(w, w.upper())
        if mid not in ALL_MODELS:
            raise ValueError(f"unknown model {w!r}; use recommended, all, global, country or local")
        if mid not in out:
            out.append(mid)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("csv", nargs="?", default=None, help="CSV of facilities (or --facilities)")
    ap.add_argument("--facilities", default=None, help="CSV of facilities")
    ap.add_argument("--out", "--output", dest="out", default=None, help="Excel workbook to write")
    ap.add_argument("--json", default=None, help="also write the raw worker output here")
    ap.add_argument("--scenario", default="rcp45", help="requested climate scenario key")
    ap.add_argument("--year", type=int, default=2050, help="target year")
    ap.add_argument(
        "--hazards",
        nargs="+",
        default=["flood", "typhoon", "heatwave"],
        help="flood typhoon heatwave (comma or space separated; RF/TC/HEAT also accepted)",
    )
    ap.add_argument(
        "--models",
        nargs="+",
        default=["recommended"],
        help="recommended | all | global country local (comma or space separated)",
    )
    ap.add_argument("--country", default=None, help="ISO3; resolved from the points if omitted")
    ap.add_argument(
        "--baseline-scenario",
        default=None,
        help="run the same models for this scenario too and fill climate_change_multiplier",
    )
    args = ap.parse_args(argv)
    src = args.facilities or args.csv
    if not src:
        ap.error("give the facilities CSV as the first argument or with --facilities")
    if not args.out:
        ap.error("--out/--output is required (the .xlsx to write)")

    try:
        facilities = read_facilities(src)
        hazards = parse_hazards(args.hazards)
        models = parse_models(args.models, hazards)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{len(facilities)} facilities from {src}")
    n_calc = len(facilities) * len(hazards) * len(models)
    print(f"hazards {hazards} x models {models} -> {n_calc} calculations")

    from climaterisk_worker.physical_risk.runner import compute_physical_risk_models

    from climaterisk.physical_risk.excel_export import frames_from_output, write_workbook

    output = compute_physical_risk_models(
        {
            "facilities": facilities,
            "climate_scenario": args.scenario,
            "target_year": args.year,
            "hazards": hazards,
            "models": models,
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
