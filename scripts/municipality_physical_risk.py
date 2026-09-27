#!/usr/bin/env python
"""Municipality physical-risk batch: representative points -> same engine -> Excel.

Runs in the CLIMADA worker environment (the same engine and the same adapters as the
Models tab and ``physical_risk_batch.py``)::

    ./.climada-env/bin/python scripts/municipality_physical_risk.py municipalities.csv \\
        --hazards flood,typhoon,heatwave --models recommended \\
        --output Municipality_Physical_Risk_Report.xlsx \\
        [--scenario rcp85 --year 2040 --json raw.json]

    # or select from the bundled dataset directly
    ./.climada-env/bin/python scripts/municipality_physical_risk.py --ids SGIS-24 -o out.xlsx
    ./.climada-env/bin/python scripts/municipality_physical_risk.py --level PROVINCE -o out.xlsx
    ./.climada-env/bin/python scripts/municipality_physical_risk.py --all -o out.xlsx

The CSV needs a ``municipality_id`` column (ids from ``assets/libraries/korea_municipalities.csv``,
e.g. ``SGIS-24``) or a ``municipality_name`` column (exact official name, unique in the
dataset); ``asset_value_usd`` and ``property_type`` are optional. A municipality **without**
an asset value is screened only — hazard intensity, data source, spatial resolution and
status ``NO_EXPOSURE_DATA`` ("No asset value") — and no value is ever assumed for it.

Every result is a representative-point assessment: the hazard grid cell at an interior
point of the official boundary. It is not a municipality-wide aggregation.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _batch_module() -> Any:
    """``scripts/physical_risk_batch.py`` — the hazard/model word parsers are shared."""
    spec = importlib.util.spec_from_file_location(
        "physical_risk_batch", REPO / "scripts" / "physical_risk_batch.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_selection(path: str | Path) -> list[tuple[str, float | None, str | None]]:
    """``(municipality_id, asset_value_usd | None, property_type | None)`` per CSV line.

    Raises:
        ValueError: unknown id/name, ambiguous name, non-numeric or negative value,
            duplicate municipality.
    """
    from climaterisk.physical_risk.municipalities import load_municipalities

    by_id = {m.municipality_id: m for m in load_municipalities()}
    by_name: dict[str, list[str]] = {}
    for m in load_municipalities():
        by_name.setdefault(m.municipality_name, []).append(m.municipality_id)
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = {h.strip().lower(): h for h in (reader.fieldnames or [])}
        id_col = header.get("municipality_id") or header.get("id")
        name_col = header.get("municipality_name") or header.get("name")
        val_col = header.get("asset_value_usd") or header.get("value") or header.get("asset_value")
        type_col = header.get("property_type") or header.get("type")
        if not id_col and not name_col:
            raise ValueError(
                "CSV needs a municipality_id or municipality_name column; header was "
                f"{list(header.values())}"
            )
        out: list[tuple[str, float | None, str | None]] = []
        seen: set[str] = set()
        for i, raw in enumerate(reader, start=2):
            mid = (raw.get(id_col) or "").strip() if id_col else ""
            if not mid and name_col:
                name = (raw.get(name_col) or "").strip()
                ids = by_name.get(name, [])
                if len(ids) != 1:
                    raise ValueError(
                        f"line {i}: municipality_name {name!r} is "
                        + ("unknown" if not ids else f"ambiguous ({ids}); give municipality_id")
                    )
                mid = ids[0]
            if mid not in by_id:
                raise ValueError(f"line {i}: unknown municipality_id {mid!r}")
            if mid in seen:
                raise ValueError(f"line {i}: duplicate municipality {mid}")
            seen.add(mid)
            value: float | None = None
            if val_col and (raw.get(val_col) or "").strip():
                try:
                    value = float(str(raw[val_col]).strip())
                except ValueError as exc:
                    raise ValueError(f"line {i}: asset_value_usd is not numeric") from exc
                if value <= 0:
                    raise ValueError(f"line {i}: asset_value_usd must be positive or blank")
            ptype = (raw.get(type_col) or "").strip() if type_col else ""
            out.append((mid, value, ptype or None))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("csv", nargs="?", default=None, help="CSV of municipalities (see above)")
    ap.add_argument("--ids", default=None, help="comma-separated municipality ids")
    ap.add_argument("--level", default=None, help="every municipality of this level")
    ap.add_argument("--all", action="store_true", help="every municipality in the dataset")
    ap.add_argument("--output", "--out", "-o", dest="out", default=None, help="Excel workbook")
    ap.add_argument("--json", default=None, help="also write the raw worker output here")
    ap.add_argument("--scenario", default="rcp45", help="requested climate scenario key")
    ap.add_argument("--year", type=int, default=2050, help="target year")
    ap.add_argument("--hazards", nargs="+", default=["flood", "typhoon", "heatwave"])
    ap.add_argument("--models", nargs="+", default=["recommended"])
    ap.add_argument("--country", default="KOR", help="ISO3 (municipalities are Korean)")
    ap.add_argument("--baseline-scenario", default=None)
    ap.add_argument(
        "--anchors",
        nargs="+",
        default=["REPRESENTATIVE_POINT"],
        choices=["REPRESENTATIVE_POINT", "OFFICIAL_OFFICE_POINT"],
        help="V0.3 POC: add OFFICIAL_OFFICE_POINT to also assess the government-published "
        "city-hall point (only where one exists; never a substitute)",
    )
    args = ap.parse_args(argv)
    if not args.out:
        ap.error("--output is required (the .xlsx to write)")

    from climaterisk.physical_risk.municipalities import by_id, load_municipalities

    batch = _batch_module()
    try:
        if args.csv:
            selection = read_selection(args.csv)
        elif args.ids:
            selection = [(i.strip(), None, None) for i in args.ids.split(",") if i.strip()]
        elif args.level:
            selection = [
                (m.municipality_id, None, None)
                for m in load_municipalities()
                if m.municipality_level == args.level.upper()
            ]
        elif args.all:
            selection = [(m.municipality_id, None, None) for m in load_municipalities()]
        else:
            ap.error("give a CSV, --ids, --level or --all")
        municipalities = by_id([mid for mid, _, _ in selection])
        hazards = batch.parse_hazards(args.hazards)
        models = batch.parse_models(args.models, hazards)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from climaterisk.physical_risk.official_offices import (
        ANCHOR_OFFICIAL_OFFICE,
        ANCHOR_REPRESENTATIVE,
        NOT_AVAILABLE_LABEL,
        office_facility,
        offices_by_id,
    )

    offices = offices_by_id() if ANCHOR_OFFICIAL_OFFICE in args.anchors else {}
    facilities = []
    for m, (_, value, ptype) in zip(municipalities, selection, strict=True):
        if ANCHOR_REPRESENTATIVE in args.anchors:
            facilities.append(m.as_facility(value, ptype))
        if ANCHOR_OFFICIAL_OFFICE in args.anchors:
            office = offices.get(m.municipality_id)
            if office is not None and office.available:
                facilities.append({**office_facility(m, office, value), "property_type": ptype})
            else:
                print(f"{m.municipality_name}: Official Office {NOT_AVAILABLE_LABEL}")
    if not facilities:
        print("error: no point to assess for the chosen anchors", file=sys.stderr)
        return 2
    n_valued = sum(1 for f in facilities if f["asset_value_usd"])
    n_office = sum(1 for f in facilities if f["point_type"] == "OFFICIAL_OFFICE_POINT")
    if n_office:
        print(
            f"{len(facilities)} points: {len(facilities) - n_office} representative, "
            f"{n_office} official office ({n_valued} with a supplied asset value, "
            f"{len(facilities) - n_valued} screening only)"
        )
    else:
        print(
            f"{len(facilities)} municipalities ({n_valued} with a supplied asset value, "
            f"{len(facilities) - n_valued} screening only)"
        )
    n_calc = len(facilities) * len(hazards) * len(models)
    print(f"hazards {hazards} x models {models} -> {n_calc} calculations")

    from climaterisk_worker.physical_risk.runner import compute_physical_risk_models

    from climaterisk.physical_risk.excel_export import frames_from_output, write_workbook

    output = compute_physical_risk_models(
        {
            "assessment_target": "MUNICIPALITY",
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
    print(
        "This result represents hazard conditions at each municipality's representative point. "
        "It is not a municipality-wide spatial aggregation."
        if not n_office
        else "These results represent hazard conditions at each selected point (representative "
        "point and/or government-published official office point). They are not "
        "municipality-wide spatial aggregations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
