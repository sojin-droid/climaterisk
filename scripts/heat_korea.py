#!/usr/bin/env python
"""Korean heat hazards from KMA 남한상세 1 km scenarios → local CLIMADA catalog.

Runs in the worker (conda) environment::

    ./.climada-env/bin/python scripts/heat_korea.py files      # what is expected / present
    ./.climada-env/bin/python scripts/heat_korea.py register   # build + register hazards
    ./.climada-env/bin/python scripts/heat_korea.py check      # read back from the catalog
    ./.climada-env/bin/python scripts/heat_korea.py run        # demo portfolio through runner

What gets registered (region ``KOR``):

* ``heat_mortality`` — season exceedance degree-days above each cell's comfort band
  (degC-days, one event per summer with its calendar year).
* ``heatwave`` — season 95th-percentile daily Tmax (degC) for the platform's built-in
  productivity ramp.

Historical (MK-PRISM v2.1, 2000–2019) is filed under ``historical``; SSP files under the
platform's ``rcp45``/``rcp85`` keys in 20-year windows keyed by their centre year
(2030, 2050, 2070, 2090) so the runner's nearest-year lookup lands on the right one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import heat_mortality as hm  # noqa: E402
from climaterisk_worker import kma_scenario as kma  # noqa: E402
from climaterisk_worker._params import HEATWAVE_HAZ_TYPE  # noqa: E402

COUNTRY = "KOR"
FUTURE_WINDOWS: tuple[tuple[int, int, int], ...] = (
    # (centre year, first, last) — 20-year windows matching the platform anchor years.
    (2030, 2021, 2040),
    (2050, 2041, 2060),
    (2070, 2061, 2080),
    (2090, 2081, 2100),
)
DEMO_ASSETS = [
    {
        "id": "seoul",
        "name": "Seoul office",
        "lat": 37.5665,
        "lon": 126.9780,
        "value": 0.0,
        "currency": "KRW",
        "headcount": 1200,
    },
    {
        "id": "busan",
        "name": "Busan plant",
        "lat": 35.1796,
        "lon": 129.0756,
        "value": 0.0,
        "currency": "KRW",
        "headcount": 800,
    },
    {
        "id": "daegu",
        "name": "Daegu site",
        "lat": 35.8714,
        "lon": 128.6014,
        "value": 0.0,
        "currency": "KRW",
        "headcount": 600,
    },
]


def expected_files() -> list[str]:
    """File names the on-ramp needs (archive names as served by 기후변화 상황지도)."""
    names = ["MKPRISM_MKPRISMv21_skorea_TAMAX_gridraw_daily_2000_2019_nc.tar.gz"]
    for ssp in ("SSP245", "SSP585"):
        for y0 in range(2021, 2100, 10):
            names.append(f"AR6_{ssp}_5ENSMN_skorea_TAMAX_gridraw_daily_{y0}_{y0 + 9}_nc.tar.gz")
    return names


def cmd_files(_: argparse.Namespace) -> int:
    d = kma.kma_dir()
    print(f"drop folder: {d}  (exists: {d.is_dir()})")
    present = {f.path.name: f for f in kma.list_files(d)} if d.is_dir() else {}
    for name in expected_files():
        nc = name.replace("_nc.tar.gz", ".nc")
        have = nc in present or (d / name).is_file()
        print(f"  [{'x' if have else ' '}] {name}")
    print(f"present daily TAMAX files: {len(present)}")
    return 0


def _register(grid: dict, catalog_dir: Path) -> dict:  # type: ignore[type-arg]
    from climaterisk_worker import catalog
    from climaterisk_worker.hazard_convert import convert_grid_to_catalog

    entry = convert_grid_to_catalog(grid, catalog_dir)
    catalog.register(entry)
    return entry


def _heatwave_grid(obs, cells, years, scenario: str, year: int) -> dict:  # type: ignore[no-untyped-def,type-arg]
    p95 = np.percentile(obs.tmax, 95, axis=2)  # (cells, years) degC
    return {
        "peril": "heatwave",
        # Must match the runner's heatwave tag ("HW"); hm.HAZ_TYPE ("HM") is the
        # degree-day *mortality* layer and would leave ImpactCalc without an impf column.
        "haz_type": HEATWAVE_HAZ_TYPE,
        "units": "degC",
        "climate_scenario": scenario,
        "region": COUNTRY,
        "year": int(year),
        "source": f"season p95 daily Tmax — {obs.source}",
        "license": "KMA 국가 기후변화 표준 시나리오 (기후변화 상황지도) — cite KMA",
        "cells": [{"cell_id": f"c{i}", "lat": c.lat, "lon": c.lon} for i, c in enumerate(cells)],
        "observations": [
            {
                "cell_id": f"c{i}",
                "year": int(years[s]),
                "intensity": float(p95[i, s]),
                "valid": True,
            }
            for i in range(len(cells))
            for s in range(len(years))
        ],
    }


def register_window(
    scenario: str,
    year_key: int,
    year_start: int | None,
    year_end: int | None,
    coarsen: int,
    catalog_dir: Path,
    reference_cells: tuple[Any, ...] | None = None,
    band_shift_c: float = 0.0,
) -> tuple[list[dict], tuple[Any, ...]]:  # type: ignore[type-arg]
    """Load one scenario window, build both layers, write + register them.

    Args:
        scenario: Platform scenario key (``historical``, ``rcp45``, …).
        year_key: Catalog year the layer is filed under.
        year_start: First season of the window (None = all available).
        year_end: Last season of the window.
        coarsen: Block size in 0.01 deg cells passed to the KMA loader.
        catalog_dir: Local hazard catalog directory.
        reference_cells: Cells of the baseline (``historical``) window, whose comfort band
            this window reuses. **Required for every non-baseline scenario** — deriving the
            band from a warmer window's own climatology makes warming look protective
            (``docs/HEAT_ADAPTATION_KR.md``).
        band_shift_c: Absolute upward shift of the comfort band, degC — the adaptation axis
            of Lee et al. (2019). 0.0 is the no-adaptation reference case.

    Returns:
        ``(catalog entries, cells)``; pass the cells back as ``reference_cells`` for the
        future windows.

    Raises:
        ValueError: for a non-baseline scenario without ``reference_cells``.
    """
    if scenario != "historical" and reference_cells is None:
        raise ValueError(
            f"scenario {scenario!r} needs reference_cells from the historical window — "
            "register 'historical' first and pass its cells (see docs/HEAT_ADAPTATION_KR.md)"
        )
    obs = kma.load_summer_tmax(
        scenario=scenario, year_start=year_start, year_end=year_end, coarsen=coarsen
    )
    cells, dd, years = hm.grid_from_summer_tmax(
        obs,
        COUNTRY,
        tag="kma",
        land_mask=False,
        reference_band=reference_cells,
        band_shift_c=band_shift_c,
    )
    band = (
        "band: fitted from this window's own climatology (baseline)"
        if reference_cells is None
        else f"band: historical baseline + {band_shift_c:.1f} degC"
        + (" (no adaptation)" if band_shift_c == 0.0 else " (Lee et al. 2019 threshold shift)")
    )
    hm_grid = hm.standardized_grid(
        dd,
        cells,
        region=COUNTRY,
        ref_year=year_key,
        climate_scenario=scenario,
        years=years,
        source=f"exceedance degree-days — {obs.source}; {band}",
    )
    hm_grid["license"] = "KMA 국가 기후변화 표준 시나리오 (기후변화 상황지도) — cite KMA"
    entries = [_register(hm_grid, catalog_dir)]
    entries.append(_register(_heatwave_grid(obs, cells, years, scenario, year_key), catalog_dir))
    print(
        f"  {scenario:>10} {year_key}: {len(cells)} cells × {len(years)} seasons "
        f"({years[0]}-{years[-1]}); mean DD {dd.mean():.1f}, max cell-season DD {dd.max():.1f}"
        f"; {band}"
    )
    return entries, cells


def cmd_register(args: argparse.Namespace) -> int:
    from climaterisk_worker import catalog

    cdir = catalog.catalog_dir()
    written = 0
    baseline_cells: tuple[Any, ...] | None = None
    # historical first: every future window reuses its comfort band.
    scenarios = sorted(args.scenarios, key=lambda s: 0 if s == "historical" else 1)
    for scen in scenarios:
        if not kma.available(scen):
            print(f"  {scen:>10}: no TAMAX daily file present — skipped")
            continue
        if scen == "historical":
            windows = [(2020, args.year_start, args.year_end)]
        else:
            windows = [(c, a, b) for c, a, b in FUTURE_WINDOWS]
        for year_key, a, b in windows:
            try:
                entries, cells = register_window(
                    scen,
                    year_key,
                    a,
                    b,
                    args.coarsen,
                    cdir,
                    reference_cells=None if scen == "historical" else baseline_cells,
                    band_shift_c=0.0 if scen == "historical" else args.band_shift_c,
                )
                written += len(entries)
                if scen == "historical":
                    baseline_cells = cells
            except (kma.KmaUnavailable, ValueError) as exc:
                print(f"  {scen:>10} {year_key}: {exc}")
    print(f"registered {written} hazard layer(s) under {cdir}")
    return 0 if written else 1


def cmd_check(_: argparse.Namespace) -> int:
    from climaterisk_worker import catalog

    for e in catalog.load_manifest():
        if e.get("region") == COUNTRY and e.get("peril") in ("heat_mortality", "heatwave"):
            print(
                f"{e['peril']:>14} {e['climate_scenario']:>10} {e['year']}: "
                f"{e['n_events']} events × {e['n_centroids']} centroids — {e['source'][:70]}"
            )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from climaterisk_worker import physical

    res = physical._run_heat_mortality(DEMO_ASSETS, "rcp45", [args.year], {"country_iso3": COUNTRY})
    print(res["detail"])
    print(f"expected annual heat-attributable deaths (portfolio): {res['aai_agg']:.3f}")
    for row in res["per_asset"]:
        print(f"  {row['id']:>6}: {row['eai']:.3f} /yr")
    fc = res["freq_curve"]
    for rp, imp in zip(fc["return_periods"], fc["impact"], strict=True):
        print(f"  1-in-{rp:>4.0f} yr: {imp:.2f} deaths")
    print(f"  cap: {fc['max_resolvable_return_period']} yr (record {fc['record_years']} yr)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("files").set_defaults(fn=cmd_files)
    r = sub.add_parser("register")
    r.add_argument("--scenarios", nargs="+", default=["historical", "rcp45", "rcp85"])
    r.add_argument(
        "--coarsen", type=int, default=5, help="block size in 0.01 deg cells (5 → ~5 km)"
    )
    r.add_argument(
        "--band-shift-c",
        type=float,
        default=0.0,
        help="adaptation: absolute upward shift of the comfort band for FUTURE windows, degC "
        "(0 = no adaptation, the reference case; Lee et al. 2019 report +1/+2/+3)",
    )
    r.add_argument("--year-start", type=int, default=None)
    r.add_argument("--year-end", type=int, default=None)
    r.set_defaults(fn=cmd_register)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    ru = sub.add_parser("run")
    ru.add_argument("--year", type=int, default=2020)
    ru.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
