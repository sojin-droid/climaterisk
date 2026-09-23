"""Excel export of a three-model physical-risk run — openpyxl, one sheet per frame.

The workbook is a *rendering* of the frames that :mod:`results_table` already produces;
nothing is computed here. Sheet order::

    run_info               what was run: scenario, target year, country, models, readiness
    hazard_results         every (facility x hazard x model) row, full provenance
    asset_summary          one line per (facility x model), priced vs unpriced hazards
    global_vs_country      GLOBAL_BASELINE vs DATA_API_COUNTRY, same impact function
    global_vs_korea_local  GLOBAL_BASELINE vs KOREA_LOCAL (not available today, said so)
    climate_change         only when a baseline scenario was run: same-model multipliers
    methodology            definitions, formulas, thresholds attribution

Two rules the writer enforces on its own: a ``None`` becomes an **empty cell**, never a
``0``, so an unpriced field cannot be summed as a loss; and ``model_id`` and
``calculation_status`` are present on every result row (they are in the frames — the
writer refuses a hazard_results frame without them).
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from climaterisk.physical_risk.metrics import MODEL_DEFINITIONS, ModelId
from climaterisk.physical_risk.results_table import HAZARD_RESULTS_COLUMNS, export_frames

#: Sheets the brief requires, in order. ``run_info`` and ``climate_change`` are additions.
REQUIRED_SHEETS: tuple[str, ...] = (
    "hazard_results",
    "asset_summary",
    "global_vs_country",
    "methodology",
)
SHEET_ORDER: tuple[str, ...] = (
    "run_info",
    "hazard_results",
    "asset_summary",
    "global_vs_country",
    "global_vs_korea_local",
    "climate_change",
    "methodology",
)

#: Excel number formats by column-name pattern. Anything else is written as-is.
_FORMATS: tuple[tuple[str, str], ...] = (
    ("_usd", "#,##0.00"),
    ("eal_as_pct_of_assets", "0.0000"),
    ("_change_pp", "0.0000"),
    ("_change_pct", "0.00"),
    ("probability", "0.000000"),
    ("hazard_intensity", "0.00"),
    ("climate_change_multiplier", "0.0000"),
)

_HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF")


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


def frames_from_output(output: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every sheet's frame for a worker output dict, in :data:`SHEET_ORDER`."""
    frames: dict[str, list[dict[str, Any]]] = {"run_info": run_info_frame(output)}
    frames.update(export_frames(output.get("rows") or []))
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


def _number_format(column: str) -> str | None:
    for token, fmt in _FORMATS:
        if token in column:
            return fmt
    return None


def _write_sheet(ws: Worksheet, frame: list[dict[str, Any]], columns: list[str]) -> None:
    ws.append(columns)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    formats = {c: _number_format(c) for c in columns}
    for row in frame:
        values = []
        for c in columns:
            v = row.get(c)
            if isinstance(v, (dict, list, tuple)):
                v = str(v)
            values.append(v)  # None stays None -> empty cell, never 0
        ws.append(values)
    for j, c in enumerate(columns, start=1):
        fmt = formats[c]
        if fmt:
            for i in range(2, ws.max_row + 1):
                cell = ws.cell(row=i, column=j)
                if cell.value is not None:
                    cell.number_format = fmt
        width = max([len(str(c))] + [len(str(r.get(c, ""))) for r in frame[:200]])
        ws.column_dimensions[get_column_letter(j)].width = min(max(10, width + 2), 60)
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
        ws = wb.create_sheet(name)
        columns = list(HAZARD_RESULTS_COLUMNS) if name == "hazard_results" else _columns(frame)
        if name in ("run_info", "methodology") and not columns:
            columns = ["key", "value"]
        _write_sheet(ws, frame, columns)
    wb.save(target)


def workbook_bytes(output: dict[str, Any]) -> bytes:
    """The whole workbook for a worker output dict, as bytes (for an HTTP response)."""
    buf = io.BytesIO()
    write_workbook(frames_from_output(output), buf)
    return buf.getvalue()
