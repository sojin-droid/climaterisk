#!/usr/bin/env python
"""KDCA heat-illness check of the open-field UTCI (heat phase 2, design 2026-11-16 §4.2-§4.3).

Compares daily and yearly notified heat-illness cases (질병관리청, 2011-2019, May-Sep) with the
daily maximum UTCI (candidate), the daily maximum air temperature and the heat days (current
default) at the 시도 level, runs the tests V1-V4 and the pre-registered switch rule
(``worker/climaterisk_worker/heat_validation.py``; criteria in ``heat_utci.validation`` of
``assets/libraries/physical_risk_config.json``, fixed before the result was seen).

Run (needs the CLIMADA environment and the KMA / KDCA data on disk)::

    export CLIMATERISK_DATA_ROOT=~/Data/climaterisk
    ./.climada-env/bin/python scripts/heat_utci_validate_kdca.py

The 시도 population (KOSIS DT_1B040A3) is fetched once with ``CLIMATERISK_KOSIS_KEY`` (process
environment or ``.env``, read inside this process only, masked in every message) and cached as
public aggregates under ``<DATA_ROOT>/external/kosis_population``. Output under
``<DATA_ROOT>/derived/heat_validation``: ``kdca_check_<YYYYMMDD>.json`` (all numbers, per-시도
tables, station map, counts, criteria, caveats, sources) and a PNG of grouped bars. Only aggregate
numbers are printed; individual KDCA rows and keys are never printed or written.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

log = logging.getLogger("heat_utci_validate_kdca")

KOSIS_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
KEY_NAME = "CLIMATERISK_KOSIS_KEY"

REGION_CAVEAT = (
    "KDCA region field: the reporting hospital's location up to 2018, the place of occurrence "
    "from 2019. The check is therefore made at 시도 level only; 세종 is merged into 충남."
)
SOURCES = [
    "질병관리청 온열질환 감시 데이터, 공공데이터포털 15149889 (collected 2026-11-16)",
    "통계청 KOSIS DT_1B040A3 시도 주민등록인구 (orgId 101)",
    "기상청 ASOS 시간자료 and MK-PRISM v3.1 0.05 deg daily maximum (as heat_utci)",
]


def mask(text: str, secret: str | None) -> str:
    """``text`` with the secret (and its URL-encoded form) replaced by ``***``."""
    if not secret:
        return text
    return text.replace(secret, "***").replace(urllib.parse.quote(secret, safe=""), "***")


def fetch_population(
    spec: dict[str, str], years: tuple[int, int], key: str, age_code: str
) -> dict[str, dict[str, float]]:
    """Resident population per 시도 and year from KOSIS (public aggregates).

    Args:
        spec: ``orgId``, ``tblId`` and ``itmId`` of the table (config).
        years: First and last year.
        key: KOSIS API key; masked in any error text.
        age_code: Code of the all-ages class on the second classification axis, if the table
            has one (``objL2``); empty string to send none.

    Returns:
        ``{sido code (2 digits): {year: persons}}`` for the codes the table returns.

    Raises:
        RuntimeError: If the API answers with an error or no usable rows (key masked).
    """
    params = {
        "method": "getList",
        "apiKey": key,
        "itmId": spec["itmId"] + "+",
        "objL1": "ALL",
        "format": "json",
        "jsonVD": "Y",
        "prdSe": "Y",
        "startPrdDe": str(years[0]),
        "endPrdDe": str(years[1]),
        "orgId": spec["orgId"],
        "tblId": spec["tblId"],
    }
    if age_code:
        params["objL2"] = age_code
    url = KOSIS_URL + "?" + urllib.parse.urlencode(params, safe="+")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(mask(f"KOSIS request failed: {exc}", key)) from None
    if isinstance(payload, dict):
        raise RuntimeError(mask(f"KOSIS error: {payload.get('errMsg', payload)}", key))
    out: dict[str, dict[str, float]] = {}
    for row in payload:
        code = str(row.get("C1", ""))
        if len(code) != 2 or code == "00":  # 시도 codes only; skip national and 시군구
            continue
        try:
            out.setdefault(code, {})[str(row["PRD_DE"])] = float(str(row["DT"]).replace(",", ""))
        except (KeyError, ValueError):
            continue
    if not out:
        raise RuntimeError("KOSIS returned no 시도 rows (check table and --age-code)")
    names = {str(r.get("C1")): str(r.get("C1_NM", "")) for r in payload}
    return {c: {"name": names.get(c, ""), **v} for c, v in out.items()}  # type: ignore[dict-item]


def population_frame(raw: dict[str, dict[str, Any]], years: tuple[int, int]) -> Any:
    """Frame (rows = years, columns = SIDO16) from the cached KOSIS answer; 세종 into 충남."""
    import numpy as np
    import pandas as pd
    from climaterisk_worker import heat_validation as hv

    idx = list(range(years[0], years[1] + 1))
    pop = pd.DataFrame(np.nan, index=idx, columns=list(hv.SIDO16))
    for entry in raw.values():
        sido = hv.normalise_sido(entry.get("name"))
        if sido is None:
            continue
        for y in idx:
            v = entry.get(str(y))
            if v is not None:
                pop.loc[y, sido] = (0.0 if np.isnan(pop.loc[y, sido]) else pop.loc[y, sido]) + v
    return pop


def load_population(cfg_val: dict[str, Any], years: tuple[int, int], age_code: str) -> Any:
    """Population frame, from the cache or from KOSIS (key read here, never printed)."""
    from climaterisk_worker._paths import paths

    cache = paths.external_dir("kosis_population") / "sido_2011_2019.json"
    if cache.is_file():
        raw = json.loads(cache.read_text(encoding="utf-8"))
        log.info("population from cache (%d regions)", len(raw))
    else:
        key = paths.setting(KEY_NAME)
        if not key:
            raise SystemExit(f"{KEY_NAME} is not set (process environment or .env)")
        raw = fetch_population(cfg_val["kosis_population"], years, key, age_code)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("population fetched and cached (%d regions)", len(raw))
    return population_frame(raw, years)


def station_map(series: dict[int, Any], cfg: dict[str, Any]) -> tuple[dict[int, str], list[int]]:
    """Station -> 시도 from the table address; stations with an unknown region are listed."""
    from climaterisk_worker import heat_utci as hu
    from climaterisk_worker import heat_validation as hv
    from climaterisk_worker._paths import paths

    table = hu.read_station_table(paths.kma_dir() / str(cfg["station_table"]))
    loc = hu.station_locations(table, f"{cfg['obs_years'][1]}-09-30").set_index("stnId")
    mapping: dict[int, str] = {}
    unknown: list[int] = []
    for stn in series:
        sido = hv.station_sido(str(loc.loc[stn, "addr"])) if stn in loc.index else None
        if sido is None:
            unknown.append(stn)
        else:
            mapping[stn] = sido
    return mapping, unknown


def plot_bars(path: Path, v1: dict[str, Any], v2: dict[str, Any], v3: dict[str, Any]) -> None:
    """Grouped bars of rho for V1-V3: dark blue = UTCI, light blue = T / T95, grey = heat days."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = ["V1 daily national", "V2 daily by region (median)", "V3 spatial"]
    data = [
        [("UTCI", v1["rho_u"], "#16325c"), ("Tmax", v1["rho_t"], "#8cb8e0")],
        [("UTCI", v2["median_u"], "#16325c"), ("Tmax", v2["median_t"], "#8cb8e0")],
        [
            ("UTCI D38", v3["rho_u"], "#16325c"),
            ("T95", v3["rho_t95"], "#8cb8e0"),
            ("Heat days", v3["rho_h"], "#9a9a9a"),
        ],
    ]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    width = 0.25
    seen: set[str] = set()
    for gi, bars in enumerate(data):
        for bi, (label, val, colour) in enumerate(bars):
            x = gi + (bi - (len(bars) - 1) / 2) * width
            if val is None:
                ax.text(x, 0.02, "n/a", ha="center", va="bottom", fontsize=9)
                continue
            ax.bar(x, val, width * 0.92, color=colour, label=None if label in seen else label)
            seen.add(label)
            ax.text(x, val + 0.01, f"{val:.2f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(groups)), groups)
    ax.set_ylabel("Spearman rho (-)")
    ax.set_ylim(0, 1)
    ax.set_title("KDCA heat-illness cases vs UTCI, 2011-2019 May-Sep")
    ax.legend(frameon=False)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(age_code: str) -> dict[str, Any]:
    """Compute V1-V4 and the decision on the real data and write the JSON and PNG."""
    import numpy as np
    import pandas as pd
    from climaterisk_worker import heat_utci as hu
    from climaterisk_worker import heat_validation as hv
    from climaterisk_worker._paths import paths

    from climaterisk.physical_risk.metrics import load_config

    cfg = load_config()["heat_utci"]
    val = cfg["validation"]
    years = (int(val["years"][0]), int(val["years"][1]))
    months = [int(m) for m in val["months"]]
    rules = {k: val[k] for k in ("v1_min_rho", "v1_margin", "v2_margin", "v3_margin")}

    cases, meta = hv.load_kdca(paths.data_root() / str(val["kdca_file"]))
    counts = hv.daily_counts(cases, years, months)
    pop = load_population(val, years, age_code)

    series = hu.station_daily_series(cfg)
    mapping, unknown = station_map(series, cfg)
    days = hv.season_days(years, months)
    cols = list(hv.SIDO16)
    u_day = pd.DataFrame(np.nan, index=days, columns=cols)
    t_day = pd.DataFrame(np.nan, index=days, columns=cols)
    annual: dict[str, pd.DataFrame] = {}
    stations_of: dict[str, list[int]] = {}
    for sido in cols:
        members = [s for s, r in mapping.items() if r == sido]
        stations_of[sido] = members
        if not members:
            continue
        frames = [series[s].reindex(days) for s in members]
        u_day[sido] = pd.concat([f["utci_max"] for f in frames], axis=1).mean(axis=1, skipna=True)
        t_day[sido] = pd.concat([f["tmax_grid"] for f in frames], axis=1).mean(axis=1, skipna=True)
        per_station = [
            hv.annual_from_daily(f, years, float(val["min_day_fraction"])) for f in frames
        ]
        # mean over stations with a valid year (NaN-aware)
        stack = np.stack([p.to_numpy(dtype=float) for p in per_station])
        with np.errstate(invalid="ignore"):
            annual[sido] = pd.DataFrame(
                np.nanmean(stack, axis=0),
                index=per_station[0].index,
                columns=per_station[0].columns,
            )

    counts_year = counts.groupby(counts.index.year).sum()
    v1 = hv.v1_daily_national(counts, u_day, t_day, pop)
    v2 = hv.v2_daily_by_sido(counts, u_day, t_day)
    v3 = hv.v3_spatial(counts_year, pop, annual)
    v4 = hv.v4_yearly(counts_year, pop, annual)
    decision = hv.decide(v1, v2, v3, rules)

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    out_dir = paths.derived_dir("heat_validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "generated": stamp,
        "years": list(years),
        "months": months,
        "criteria": {**rules, "min_day_fraction": val["min_day_fraction"]},
        "decision": decision,
        "V1": v1,
        "V2": v2,
        "V3": v3,
        "V4_record_only": v4,
        "kdca_meta": meta,
        "n_cases_in_window": int(counts.to_numpy().sum()),
        "stations": {
            "n_series": len(series),
            "n_mapped": len(mapping),
            "unknown_sido": sorted(unknown),
            "station_to_sido": {str(s): r for s, r in sorted(mapping.items())},
            "n_per_sido": {s: len(m) for s, m in stations_of.items()},
        },
        "method": (
            "V1/V2 daily: station daily series averaged over the stations of a 시도; V3/V4 "
            "yearly: per-station D38 / T95 / heat days averaged over the stations with a valid "
            "year; heat days = tmax_grid >= 33; national values population-weighted"
        ),
        "region_caveat": REGION_CAVEAT,
        "sources": SOURCES,
    }
    json_path = out_dir / f"kdca_check_{stamp}.json"
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, default=float), encoding="utf-8"
    )
    plot_bars(out_dir / f"kdca_check_{stamp}.png", v1, v2, v3)
    log.info("wrote %s", json_path.name)
    return record


def main(argv: list[str] | None = None) -> int:
    """Entry point; prints only aggregate numbers."""
    ap = argparse.ArgumentParser(
        description="KDCA heat-illness check of the open-field UTCI (V1-V4 and the switch rule).",
        epilog="Reads CLIMATERISK_KOSIS_KEY from the environment or .env only when the "
        "population cache is missing; the key is never printed.",
    )
    ap.add_argument(
        "--age-code",
        default="0",
        help="KOSIS objL2 code of the all-ages class of DT_1B040A3 (empty: send none)",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rec = run(args.age_code)
    d = rec["decision"]
    print(f"switch: {d['switch']}")
    for name, item in d["items"].items():
        nums = {
            k: (None if v is None else round(float(v), 3)) for k, v in item.items() if k != "pass"
        }
        print(f"{name}: pass={item['pass']} {nums}")
    print(f"V4 (record only): {rec['V4_record_only']}")
    print(f"n: V1 days={rec['V1']['n']} V2 regions={rec['V2']['n']} V3 regions={rec['V3']['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
