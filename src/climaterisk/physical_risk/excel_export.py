"""Excel export of a three-model physical-risk run — openpyxl, one sheet per frame.

The workbook is a *rendering* of the frames that :mod:`results_table` already produces;
nothing is computed here. Sheet order (titles as the reader sees them)::

    Portfolio Summary      non-expert first sheet: one line per facility, plain words
    Asset Risk Matrix      facility x hazard risk levels (or a status label)
    Hazard Results         every (facility x hazard x model) row, full provenance
    Global vs Country      GLOBAL_BASELINE vs DATA_API_COUNTRY, same impact function
    Global vs Korea Local  GLOBAL_BASELINE vs KOREA_LOCAL (not available today, said so)
    Climate Change         only when a baseline scenario was run: same-model multipliers
    Methodology            definitions, formulas, thresholds attribution, limitations
    Run Info               what was run: scenario, target year, country, readiness

Rules the writer enforces on its own: a ``None`` becomes an **empty cell**, never a ``0``
(a computed zero is written as ``0``), so an unpriced field cannot be summed as a loss;
``model_id`` and ``calculation_status`` are present on every result row (the writer refuses
a hazard_results frame without them); risk-level cells are colour-coded by conditional
formatting — a visual aid, not a rule.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from climaterisk.physical_risk.display_copy import LIMITATIONS, STATUS_COPY, risk_level_copy
from climaterisk.physical_risk.metrics import MODEL_DEFINITIONS, ModelId
from climaterisk.physical_risk.results_table import HAZARD_RESULTS_COLUMNS, export_frames

#: Frame key -> sheet title, in workbook order.
SHEET_TITLES: dict[str, str] = {
    "portfolio_summary": "Portfolio Summary",
    "asset_risk_matrix": "Asset Risk Matrix",
    "hazard_results": "Hazard Results",
    "global_vs_country": "Global vs Country",
    "global_vs_korea_local": "Global vs Korea Local",
    "climate_change": "Climate Change",
    "methodology": "Methodology",
    "run_info": "Run Info",
}
SHEET_ORDER: tuple[str, ...] = tuple(SHEET_TITLES)

#: Sheets the brief requires (by frame key).
REQUIRED_SHEETS: tuple[str, ...] = (
    "portfolio_summary",
    "asset_risk_matrix",
    "hazard_results",
    "global_vs_country",
    "methodology",
)

#: Excel number formats by column-name pattern (technical sheets).
_FORMATS: tuple[tuple[str, str], ...] = (
    ("_usd", "#,##0.00"),
    ("eal_as_pct_of_assets", "0.0000"),
    ("_change_pp", "0.0000"),
    ("_change_pct", "0.00"),
    ("probability", "0.000000"),
    ("hazard_intensity", "0.00"),
    ("climate_change_multiplier", "0.0000"),
)
#: Formats for the two non-expert sheets, by column title.
_PLAIN_FORMATS: tuple[tuple[str, str], ...] = (  # first match wins: ratios before money
    ("EAL / Assets", '0.000"%"'),
    ("Asset Value", '"$"#,##0'),
    (" EAL", '"$"#,##0'),
    ("Tmax", '0.0" °C"'),
)
#: Columns whose cells get the risk-level colour scale.
_RISK_COLUMNS: frozenset[str] = frozenset({"Flood", "Typhoon", "Heatwave", "risk_level"})

_HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_RISK_FILL = {
    "Low": PatternFill("solid", fgColor="D9EAD3"),
    "Medium": PatternFill("solid", fgColor="FFE599"),
    "High": PatternFill("solid", fgColor="F4C7C3"),
    "Hazard only": PatternFill("solid", fgColor="D0E0E3"),
    "Unavailable": PatternFill("solid", fgColor="EEEEEE"),
    "N/A": PatternFill("solid", fgColor="EEEEEE"),
    "N/A (scenario)": PatternFill("solid", fgColor="EEEEEE"),
}


def run_info_frame(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Key/value lines describing the run, read from the worker output."""
    readiness = output.get("readiness") or {}
    summary = readiness.get("summary") or {}
    lines: list[tuple[str, Any]] = [
        ("generated_at_utc", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")),
        ("status", output.get("status")),
        ("requested_scenario", output.get("climate_scenario")),
        ("target_year", output.get("target_year")),
        ("baseline_scenario", output.get("baseline_scenario")),
        ("country", output.get("country")),
        ("n_rows", len(output.get("rows") or [])),
        ("n_facilities", len({r["facility_id"] for r in output.get("rows") or []})),
        ("models", ", ".join(m.value for m in ModelId)),
    ]
    for model, definition in MODEL_DEFINITIONS.items():
        lines.append((f"definition.{model}", definition))
    for hazard, cells in summary.items():
        for model, status in cells.items():
            lines.append((f"readiness.{hazard}.{model}", status))
    for hazard, fixed in (output.get("impact_function_fixed") or {}).items():
        lines.append((f"impact_function_fixed.{hazard}", fixed))
    if output.get("detail"):
        lines.append(("detail", output["detail"]))
    return [{"key": k, "value": v} for k, v in lines]


def _plain_methodology(frame: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Methodology sheet a general reader can follow, in fixed sections.

    Sections: What was assessed · Hazards · Data sources · CLIMADA method · Impact Functions ·
    EAL · Potential Loss · Risk Level · Climate Change Multiplier · Limitations · Calculation
    statuses. The technical key/value lines from ``results_table.methodology_frame`` follow
    under "Technical definitions" so nothing is lost.
    """
    from climaterisk.physical_risk.display_copy import (
        HAZARD_COPY,
        HAZARD_LABEL,
        SCOPE_COPY,
        SCOPE_LABEL,
    )

    lines: list[tuple[str, str]] = [
        (
            "What was assessed",
            "Each selected asset (a point with an asset value) against each selected hazard, "
            "under each data scope that could run. Every result row names the data scope, the "
            "impact function and its calculation status.",
        ),
    ]
    for key in ("RF", "TC", "HEAT"):
        lines.append((f"Hazards — {HAZARD_LABEL[key]}", HAZARD_COPY[key]))
    for model, label in SCOPE_LABEL.items():
        lines.append((f"Data sources — {label} ({model})", SCOPE_COPY[model]))
    lines += [
        (
            "CLIMADA method",
            "Hazard intensity at the asset location → published CLIMADA impact function → "
            "ImpactCalc (event losses × event frequencies) → Expected Annual Loss and "
            "return-period loss. Nothing is re-implemented; no local calibration.",
        ),
        (
            "Impact Functions",
            "Flood: ImpfRiverFlood.from_jrc_region_sector (Huizinga et al. 2017), sector from the "
            "asset's property type. Tropical cyclone: the regional function CLIMADA assigns to the "
            "country (Eberenz et al. 2021; Korea → North West Pacific). Heatwave: none exists, so "
            "no financial loss is derived. The same function is used under every data scope.",
        ),
        (
            "EAL",
            "Expected Annual Loss = sum over events of (event loss × annual frequency); CLIMADA "
            "Impact.aai_agg. A computed zero is written as 0; an unpriced hazard is blank.",
        ),
        (
            "Potential Loss",
            "Loss at the configured return period (100 years) from CLIMADA's frequency curve. "
            "Left blank with status RETURN_PERIOD_NOT_RESOLVABLE when the hazard data does not "
            "reach that return period — CLIMADA alone would repeat the largest event's loss.",
        ),
    ]
    for level, text in risk_level_copy().items():
        lines.append((f"Risk Level — {level}", text))
    lines += [
        (
            "Risk Level — attribution",
            "EAL as a share of asset value, banded by this project's configuration "
            "(GRESB-informed; NOT an official GRESB threshold). No overall portfolio score.",
        ),
        (
            "Climate Change Multiplier",
            "future EAL / baseline EAL within one data scope, computed only when a baseline "
            "scenario was actually run. A Global-vs-Country difference is a data-source "
            "comparison, never a climate-change multiplier.",
        ),
    ]
    for i, line in enumerate(LIMITATIONS, start=1):
        lines.append((f"Limitations — {i}", line))
    for status, text in STATUS_COPY.items():
        lines.append((f"Calculation status — {status}", text))
    lines += [(f"Technical definitions — {m['key']}", str(m["value"])) for m in frame]
    return [{"key": k, "value": v} for k, v in lines]


def frames_from_output(output: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every sheet's frame for a worker output dict, in :data:`SHEET_ORDER`."""
    frames: dict[str, list[dict[str, Any]]] = dict(export_frames(output.get("rows") or []))
    frames["methodology"] = _plain_methodology(frames.get("methodology", []))
    frames["run_info"] = run_info_frame(output)
    multipliers = output.get("climate_change_multipliers")
    if multipliers:
        frames["climate_change"] = list(multipliers)
    return {name: frames[name] for name in SHEET_ORDER if name in frames}


def _columns(frame: Iterable[dict[str, Any]]) -> list[str]:
    """Union of keys in first-seen order (comparison frames have ragged rows)."""
    cols: list[str] = []
    seen: set[str] = set()
    for row in frame:
        for k in row:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return cols


def _number_format(column: str, plain: bool) -> str | None:
    table = _PLAIN_FORMATS if plain else _FORMATS
    for token, fmt in table:
        if token in column:
            return fmt
    return None


def _write_sheet(
    ws: Worksheet, frame: list[dict[str, Any]], columns: list[str], *, plain: bool
) -> None:
    ws.append(columns)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    formats = {c: _number_format(c, plain) for c in columns}
    for row in frame:
        values = []
        for c in columns:
            v = row.get(c)
            if isinstance(v, (dict, list, tuple)):
                v = str(v)
            values.append(v)  # None stays None -> empty cell, never 0
        ws.append(values)
    n_rows = ws.max_row
    for j, c in enumerate(columns, start=1):
        fmt = formats[c]
        if fmt:
            for i in range(2, n_rows + 1):
                cell = ws.cell(row=i, column=j)
                if cell.value is not None:
                    cell.number_format = fmt
        width = max([len(str(c))] + [len(str(r.get(c, ""))) for r in frame[:200]])
        ws.column_dimensions[get_column_letter(j)].width = min(max(10, width + 2), 60)
        if n_rows > 1 and (c.endswith(" Risk") or c.endswith(" Status") or c in _RISK_COLUMNS):
            ref = f"{get_column_letter(j)}2:{get_column_letter(j)}{n_rows}"
            for label, fill in _RISK_FILL.items():
                rule = CellIsRule(  # type: ignore[no-untyped-call]
                    operator="equal", formula=[f'"{label}"'], fill=fill
                )
                ws.conditional_formatting.add(ref, rule)
    ws.freeze_panes = "A2"
    if frame:
        ws.auto_filter.ref = ws.dimensions


def write_workbook(frames: dict[str, list[dict[str, Any]]], target: str | Path | BinaryIO) -> None:
    """Write the frames to ``target`` (a path or a binary buffer)."""
    if "hazard_results" not in frames:
        raise ValueError("frames lack 'hazard_results' — nothing to export")
    hr = frames["hazard_results"]
    if hr and not {"model_id", "calculation_status"} <= set(hr[0]):
        raise ValueError("hazard_results rows must carry model_id and calculation_status")
    wb = Workbook()
    default = wb.active
    if default is not None:
        wb.remove(default)
    for name in SHEET_ORDER:
        if name not in frames:
            continue
        frame = frames[name]
        ws = wb.create_sheet(SHEET_TITLES[name])
        columns = list(HAZARD_RESULTS_COLUMNS) if name == "hazard_results" else _columns(frame)
        if name in ("run_info", "methodology") and not columns:
            columns = ["key", "value"]
        _write_sheet(ws, frame, columns, plain=name in ("portfolio_summary", "asset_risk_matrix"))
    wb.save(target)


def workbook_bytes(output: dict[str, Any]) -> bytes:
    """The whole workbook for a worker output dict, as bytes (for an HTTP response)."""
    buf = io.BytesIO()
    write_workbook(frames_from_output(output), buf)
    return buf.getvalue()
