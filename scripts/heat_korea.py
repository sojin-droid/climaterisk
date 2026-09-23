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
* ``heatwave`` — season 95th-percentile daily Tmax (degC), from **TAMAX** (S7 Option C,
  2026-09-23; never from TA), for the platform's built-in
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


def expected_files(variable: str | None = None) -> list[str]:
    """Archive names the on-ramp needs, as 기후변화 상황지도 serves them.

    The variable token comes from the loader (:data:`kma_scenario.DEFAULT_VARIABLE`) so this
    list cannot drift from what ``register`` actually reads — it did once, listing ``TAMAX``
    after the model moved to daily mean temperature.

    Args:
        variable: One file-name variable token, or None for every variable the two heat
            layers read (``TA`` for heat mortality, ``TAMAX`` for the heatwave layer).

    Returns:
        Archive file names, historical window first.
    """
    if variable is None:
        return [n for v in (kma.DEFAULT_VARIABLE, kma.HEATWAVE_VARIABLE) for n in expected_files(v)]
    names = [f"MKPRISM_MKPRISMv31_{variable}_gridraw_daily_2000_2019_nc.tar.gz"]
    for ssp in ("SSP245", "SSP585"):
        for y0 in range(2021, 2100, 10):
            names.append(
                f"AR6_{ssp}_5ENSMN_skorea_{variable}_gridraw_daily_{y0}_{y0 + 9}_asc.tar.gz"
            )
    return names


def cmd_files(_: argparse.Namespace) -> int:
    d = kma.kma_dir()
    print(f"drop folder: {d}  (exists: {d.is_dir()})")
    files = kma.list_files(d) if d.is_dir() else []
    present = {f.path.name for f in files}
    for name in expected_files():
        have = name in present or (d / name).is_file()
        print(f"  [{'x' if have else ' '}] {name}")
    seasons = ", ".join(
        f"{s}:{min(y)}-{max(y)}"
        for s in sorted({f.platform_scenario for f in files if f.platform_scenario})
        if (y := [f.year_start for f in files if f.platform_scenario == s])
    )
    print(f"present daily {kma.DEFAULT_VARIABLE} seasons: {len(files)}  [{seasons}]")
    return 0


def _register(grid: dict, catalog_dir: Path) -> dict:  # type: ignore[type-arg]
    from climaterisk_worker import catalog
    from climaterisk_worker.hazard_convert import convert_grid_to_catalog

    entry = convert_grid_to_catalog(grid, catalog_dir)
    catalog.register(entry)
    return entry


def _heatwave_grid(obs_max, cells, years, scenario: str, year: int) -> dict:  # type: ignore[no-untyped-def,type-arg]
    """Season p95 of daily **maximum** temperature per cell-season. ``obs_max`` is TAMAX."""
    assert kma.HEATWAVE_VARIABLE in obs_max.source, "heatwave layer must be built from TAMAX"
    p95 = np.percentile(obs_max.tmax, 95, axis=2)  # (cells, years) degC
    return {
        "peril": "heatwave",
        # Must match the runner's heatwave tag ("HW"); hm.HAZ_TYPE ("HM") is the
        # degree-day *mortality* layer and would leave ImpactCalc without an impf column.
        "haz_type": HEATWAVE_HAZ_TYPE,
        "units": "degC",
        "climate_scenario": scenario,
        "region": COUNTRY,
        "year": int(year),
        "source": f"season p95 daily Tmax — {obs_max.source}",
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


def _check_aligned(obs, obs_max) -> None:  # type: ignore[no-untyped-def]
    """Both variables must describe the same cells and seasons, or the layers cannot pair."""
    if obs.lat.shape != obs_max.lat.shape or not (
        np.allclose(obs.lat, obs_max.lat) and np.allclose(obs.lon, obs_max.lon)
    ):
        raise ValueError(
            f"TA and {kma.HEATWAVE_VARIABLE} footprints differ ({obs.lat.size} vs "
            f"{obs_max.lat.size} cells) — refusing to pair the heat layers"
        )
    if list(obs.years) != list(obs_max.years):
        raise ValueError(
            f"TA seasons {obs.years[0]}-{obs.years[-1]} vs {kma.HEATWAVE_VARIABLE} seasons "
            f"{obs_max.years[0]}-{obs_max.years[-1]} — refusing to pair the heat layers"
        )


def _intersect_footprints(a, b):  # type: ignore[no-untyped-def]
    """Cells present in both ``(lat, lon)`` footprints, in ``a``'s order."""
    keep = set(kma._keys(*b))
    idx = [i for i, k in enumerate(kma._keys(*a)) if k in keep]
    return a[0][idx], a[1][idx]


def register_window(
    scenario: str,
    year_key: int,
    year_start: int | None,
    year_end: int | None,
    coarsen: int,
    catalog_dir: Path,
    reference_cells: tuple[Any, ...] | None = None,
    band_shift_c: float = 0.0,
    footprint: tuple[Any, Any] | None = None,
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
        footprint: ``(lat, lon)`` every window is pinned to, from
            ``kma_scenario.common_footprint``. MK-PRISM and the AR6 ensemble carry
            different land masks, so without this an observed and an SSP window cover
            different cells and their difference is not a scenario signal.

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
        scenario=scenario,
        year_start=year_start,
        year_end=year_end,
        coarsen=coarsen,
        restrict_to=footprint,
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
    # The heatwave layer reads TAMAX on exactly the mortality layer's cells AND seasons: the
    # season bounds are the TA seasons actually loaded (a window may hold more TAMAX decades
    # than TA decades — 2021-2060 vs 2021-2030 today — and the two layers must stay paired).
    # No TAMAX file -> no heatwave layer; it is never approximated from TA (S7 Option C).
    try:
        obs_max = kma.load_summer_tmax(
            scenario=scenario,
            year_start=int(obs.years[0]),
            year_end=int(obs.years[-1]),
            coarsen=coarsen,
            variable=kma.HEATWAVE_VARIABLE,
            restrict_to=(obs.lat, obs.lon),
        )
    except kma.KmaUnavailable as exc:
        print(
            f"  {scenario:>10} {year_key}: heatwave layer skipped — no "
            f"{kma.HEATWAVE_VARIABLE} daily file ({exc}); not approximated from TA"
        )
        return entries, cells
    _check_aligned(obs, obs_max)
    entries.append(
        _register(_heatwave_grid(obs_max, cells, years, scenario, year_key), catalog_dir)
    )
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
    have = [s for s in scenarios if kma.available(s)]
    have_max = [s for s in have if kma.available(s, variable=kma.HEATWAVE_VARIABLE)]
    # One footprint for every window and both variables: the products' land masks differ,
    # and a scenario delta computed over two different cell sets is not a scenario delta.
    footprint: tuple[Any, Any] | None = None
    if len(have) > 1 or have_max:
        lat, lon = kma.common_footprint(have, coarsen=args.coarsen)
        if have_max:
            lat2, lon2 = kma.common_footprint(
                have_max, coarsen=args.coarsen, variable=kma.HEATWAVE_VARIABLE
            )
            lat, lon = _intersect_footprints((lat, lon), (lat2, lon2))
        footprint = (lat, lon)
        print(
            f"common footprint across {', '.join(have)} (TA"
            f"{' + ' + kma.HEATWAVE_VARIABLE if have_max else ''}): {lat.size} cells"
        )
    for scen in scenarios:
        if not kma.available(scen):
            print(f"  {scen:>10}: no {kma.DEFAULT_VARIABLE} daily file present — skipped")
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
                    footprint=footprint,
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
