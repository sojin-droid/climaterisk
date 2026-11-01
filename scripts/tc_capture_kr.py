#!/usr/bin/env python3
"""TC capture diagnostic for Korea — how the wind-only model's year-to-year loss compares
with the observed aggregate 재해연보 series.

**Diagnostic, not calibration.** Nothing here fits ``v_half``, builds a vulnerability curve
or authorises a calibration; ``calibration_gate`` still refuses this pair and this script does
not touch it. The question is narrower and answerable:

    How much of the observed year-to-year variation in Korean typhoon damage does the current
    CLIMADA wind-only TC model reproduce, and where does its structure differ?

What makes the comparison possible at all is the **observed-track** hazard: its events carry
real calendar dates, so a per-year comparison is defensible (``validation.check_annual_basis``).
A synthetic ensemble would not do — its members inherit their parent track's date, so summing
by calendar year multiplies that year by the ensemble size.

What the comparison cannot be
-----------------------------
* The observed figure books **wind + surge + rain** together; the runner models **wind only**
  (spec §6-1). A gap between them is therefore not a parameter error — it contains a model
  **coverage** gap, and no re-parameterisation would close it.
* There is no citable Korean asset-stock figure in this repository, so exposure is normalised
  to 1.0 and every modelled loss is a **fraction of normalised exposure**, never a KRW amount.
  ``ABSOLUTE CAPTURE RATE = NOT ESTIMATED`` is reported for exactly that reason.
* The window is five years. Correlation coefficients are printed with ``n`` and ``p`` and
  explicitly marked as not interpretable as significant.

Layers: data loading → model calculation → annual aggregation → diagnostic metrics → report.
The model layer calls the existing runners (``exposures.build_exposure``, ``physical._impact``,
``vulnerability.tc_regional_preset``, ``validation.modelled_annual_losses``); no second TC
model is defined here.

Run::

    ./.climada-env/bin/python scripts/tc_capture_kr.py --out outputs/tc_capture_kr
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "worker") not in sys.path:
    sys.path.insert(0, str(REPO / "worker"))

COUNTRY = "KOR"
PERIL = "tropical_cyclone"

#: Observed losses aggregate these; the runner carries only ``wind``.
OBSERVED_COVERS: tuple[str, ...] = ("wind", "surge", "rain")
MODEL_COVERS: tuple[str, ...] = ("wind",)

#: Below this many paired years, a correlation coefficient is reported but not interpreted.
MIN_YEARS_FOR_SIGNIFICANCE = 8

#: Printed wherever a reader might expect a capture percentage.
ABSOLUTE_CAPTURE = "NOT ESTIMATED"
ABSOLUTE_CAPTURE_REASON = (
    "no citable Korean asset-stock figure is available in this repository, so a modelled "
    "fraction of normalised exposure cannot be turned into a share of real national losses"
)


# --- 1. data loading -------------------------------------------------------------------


def load_observed(fetch: Any = None) -> Any:
    """The F1 national 재해연보 series (2016–2023), rebuilt from the API.

    Args:
        fetch: Injection seam passed through to the loader — tests supply a fixture.
    """
    from climaterisk_worker import observed_kr

    return observed_kr.load_year_series(PERIL, fetch=fetch)


def load_observed_track_hazard(country: str = COUNTRY) -> Any:
    """The IBTrACS **observed-track** TC hazard for ``country`` from the CLIMADA Data API.

    ``event_type='observed'`` is what makes per-year comparison defensible; a synthetic set
    is deliberately not accepted here.

    Raises:
        RuntimeError: when the returned set is not all original tracks.
    """
    from climada.util.api_client import Client
    from climaterisk_worker._dataapi import resilient_get_hazard

    hazard = resilient_get_hazard(
        Client(),
        "tropical_cyclone",
        properties={"country_iso3alpha": country, "event_type": "observed"},
    )
    orig = getattr(hazard, "orig", None)
    if orig is None or not bool(orig.all()):
        raise RuntimeError(
            "the resolved TC hazard is not an all-original-track set; a synthetic ensemble "
            "cannot be compared year by year (docs/OBSERVED_LOSSES_KR_SPEC.md §7)"
        )
    return hazard


def build_normalized_exposure(country: str = COUNTRY, res_arcsec: int = 300) -> tuple[Any, dict]:  # type: ignore[type-arg]
    """Gridded exposure whose values sum to 1.0, plus its provenance.

    The spatial pattern is a **proxy**: WorldPop population, not asset value. Emanuel ``mdd``
    is a pure fraction, so the modelled AAI scales linearly with the total — normalising to 1
    removes the absolute scale (which we do not have) while keeping the distribution (which
    we do). Every modelled number downstream is therefore a fraction of normalised exposure.
    """
    from climaterisk_worker import exposures

    exposure = exposures.build_exposure("raster", country, res_arcsec=res_arcsec)
    total = float(exposure.gdf["value"].sum())
    if total <= 0:
        raise RuntimeError(f"exposure for {country} sums to {total}; cannot normalise")
    exposure.gdf["value"] = exposure.gdf["value"] / total
    return exposure, {
        "exposure_source": "WorldPop 1 km (<iso3>_ppp_2020_1km_Aggregated.tif)",
        "exposure_proxy": "WorldPop 1 km — POPULATION, not Korean asset stock",
        "res_arcsec": res_arcsec,
        "n_cells": len(exposure.gdf),
        "raw_total": total,
        "normalised_total": 1.0,
    }


# --- 2. model calculation --------------------------------------------------------------


def model_impact(exposure: Any, hazard: Any, country: str = COUNTRY) -> tuple[Any, dict]:  # type: ignore[type-arg]
    """Run the existing TC impact path over ``exposure`` and ``hazard``.

    Uses the platform's own regional ``v_half`` default and its own ``ImpactCalc`` wrapper —
    this function selects nothing and fits nothing.
    """
    from climada.entity import ImpactFuncSet
    from climada.entity.impact_funcs.trop_cyclone import ImpfTropCyclone
    from climaterisk_worker import physical, vulnerability

    preset = vulnerability.tc_regional_preset(country)
    if preset is None:
        raise RuntimeError(f"no regional TC preset for {country}; refusing to invent a v_half")
    v_half = float(preset["tc_v_half"])
    impf = ImpfTropCyclone.from_emanuel_usa(impf_id=1, v_half=v_half)
    exposure.gdf["impf_TC"] = 1
    impact = physical._impact(exposure, ImpactFuncSet([impf]), hazard)
    return impact, {
        "impact_function": "climada ImpfTropCyclone.from_emanuel_usa",
        "v_half": v_half,
        "v_half_source": f"{preset['provenance']} [{preset['region_code']}]",
        "v_half_fitted_here": False,
    }


# --- 3. annual aggregation -------------------------------------------------------------


def annual_model_losses(impact: Any, hazard: Any) -> dict[int, float]:
    """``{calendar year: normalised loss}`` from the impact, via the shared aggregator."""
    from climaterisk_worker.validation import modelled_annual_losses

    return modelled_annual_losses(impact.at_event, hazard.date, orig=hazard.orig)


# --- 4. diagnostic metrics (pure — no CLIMADA, no network) -------------------------------


def ranks(values: list[float]) -> list[int]:
    """Dense ranks, 1 = largest. Ties share the smaller rank."""
    order = sorted(set(values), reverse=True)
    return [order.index(v) + 1 for v in values]


def distribution_stats(values: list[float]) -> dict[str, Any]:
    """Shape of a loss series, independent of its scale.

    ``max_year_share`` is the fraction of the window's total carried by its worst year, and
    ``nonzero_max_min`` the spread across years that had any loss — both dimensionless, so an
    observed KRW series and a normalised modelled series can be compared directly.
    """
    n = len(values)
    total = sum(values)
    nonzero = [v for v in values if v > 0]
    mean = total / n if n else float("nan")
    if n > 1:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        cv = (var**0.5) / mean if mean > 0 else float("nan")
    else:
        cv = float("nan")
    return {
        "n": n,
        "n_zero_years": n - len(nonzero),
        "total": total,
        "mean": mean,
        "max": max(values) if values else float("nan"),
        "cv": cv,
        "max_year_share": (max(values) / total) if total > 0 else float("nan"),
        "nonzero_max_min": (max(nonzero) / min(nonzero)) if len(nonzero) >= 2 else float("nan"),
    }


def _average_ranks(values: list[float]) -> list[float]:
    """Ascending ranks with ties averaged — the convention Spearman is defined on."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny, eps = 1e-30, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for num in (
            m * (b - m) * x / ((qam + m2) * (a + m2)),
            -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)),
        ):
            d = 1.0 + num * d
            d = tiny if abs(d) < tiny else d
            c = 1.0 + num / c
            c = tiny if abs(c) < tiny else c
            d = 1.0 / d
            h *= d * c
        if abs(d * c - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta ``I_x(a, b)``."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _two_sided_t_p(r: float, n: int) -> float:
    """Two-sided p for a correlation ``r`` on ``n`` pairs, via the t approximation.

    Implemented here rather than taken from SciPy so the diagnostic layer runs in the
    backend environment too; ``test_pure_correlations_match_scipy`` checks it against SciPy
    wherever SciPy is installed.
    """
    df = n - 2
    if df <= 0 or not -1.0 < r < 1.0:
        return 0.0 if abs(r) >= 1.0 else float("nan")
    t2 = r * r * df / (1.0 - r * r)
    return float(_betai(0.5 * df, 0.5, df / (df + t2)))


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den > 0 else float("nan")


def correlations(observed: list[float], modelled: list[float]) -> dict[str, Any]:
    """Pearson and Spearman with ``n``, ``p`` and an explicit interpretability verdict.

    The verdict is the point: on a short window a coefficient is still computed, but it is
    labelled so a reader cannot take it for evidence.
    """
    n = len(observed)
    out: dict[str, Any] = {"n": n}
    if n >= 3:
        pearson = _pearson(observed, modelled)
        spearman = _pearson(_average_ranks(observed), _average_ranks(modelled))
        out["pearson"] = float(pearson)
        out["pearson_p"] = _two_sided_t_p(pearson, n)
        out["spearman"] = float(spearman)
        out["spearman_p"] = _two_sided_t_p(spearman, n)
    else:  # pragma: no cover - a window this short never reaches the report
        out.update(dict.fromkeys(("pearson", "pearson_p", "spearman", "spearman_p"), float("nan")))
    out["interpretable"] = n >= MIN_YEARS_FOR_SIGNIFICANCE
    out["note"] = (
        f"n={n}: Do not interpret as statistically significant"
        if not out["interpretable"]
        else f"n={n}"
    )
    return out


def discrepancies(years: list[int], observed: list[float], modelled: list[float]) -> dict:  # type: ignore[type-arg]
    """The years the two series disagree about most, found from the data.

    Nothing here is keyed to a known year: the report names whatever the run produces.
    """
    r_obs, r_mod = ranks(observed), ranks(modelled)
    rank_gap = [abs(a - b) for a, b in zip(r_obs, r_mod, strict=True)]
    tot_o, tot_m = sum(observed), sum(modelled)
    share_gap = [
        (m / tot_m if tot_m > 0 else 0.0) - (o / tot_o if tot_o > 0 else 0.0)
        for o, m in zip(observed, modelled, strict=True)
    ]
    i_rank = max(range(len(years)), key=lambda i: rank_gap[i])
    i_over = max(range(len(years)), key=lambda i: share_gap[i])
    i_under = min(range(len(years)), key=lambda i: share_gap[i])
    return {
        "largest_rank_discrepancy": {
            "year": years[i_rank],
            "observed_rank": r_obs[i_rank],
            "model_rank": r_mod[i_rank],
            "rank_gap": rank_gap[i_rank],
        },
        "model_most_over_concentrated": {
            "year": years[i_over],
            "observed_share": observed[i_over] / tot_o if tot_o > 0 else float("nan"),
            "model_share": modelled[i_over] / tot_m if tot_m > 0 else float("nan"),
        },
        "model_most_under_weighted": {
            "year": years[i_under],
            "observed_share": observed[i_under] / tot_o if tot_o > 0 else float("nan"),
            "model_share": modelled[i_under] / tot_m if tot_m > 0 else float("nan"),
        },
        "agreed_zero_years": [
            y for y, o, m in zip(years, observed, modelled, strict=True) if o == 0 and m == 0
        ],
    }


@dataclass(frozen=True)
class Diagnostic:
    """Everything the report prints, and nothing that had to be assumed to get it."""

    years: list[int]
    observed: list[float]
    modelled: list[float]
    observed_stats: dict[str, Any]
    model_stats: dict[str, Any]
    variability_inflation: float
    correlations: dict[str, Any]
    discrepancies: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def absolute_capture_rate(self) -> str:
        """Always :data:`ABSOLUTE_CAPTURE` — see :data:`ABSOLUTE_CAPTURE_REASON`."""
        return ABSOLUTE_CAPTURE


def diagnose(
    observed_losses: dict[int, float],
    model_losses: dict[int, float],
    provenance: dict[str, Any] | None = None,
) -> Diagnostic:
    """Compare the two series over the years they share.

    Years present in only one series are excluded and the exclusion is recorded — the
    observed series runs to 2023 while the observed-track hazard stops in 2020.
    """
    years = sorted(set(observed_losses) & set(model_losses))
    if not years:
        raise ValueError("observed and modelled series share no year")
    obs = [float(observed_losses[y]) for y in years]
    mod = [float(model_losses[y]) for y in years]
    o_stats, m_stats = distribution_stats(obs), distribution_stats(mod)
    o_cv, m_cv = o_stats["cv"], m_stats["cv"]
    inflation = (m_cv / o_cv) if (o_cv and o_cv == o_cv and o_cv > 0) else float("nan")
    prov = dict(provenance or {})
    prov.setdefault("observed_years_excluded", sorted(set(observed_losses) - set(years)))
    prov.setdefault("model_years_excluded", sorted(set(model_losses) - set(years)))
    return Diagnostic(
        years=years,
        observed=obs,
        modelled=mod,
        observed_stats=o_stats,
        model_stats=m_stats,
        variability_inflation=inflation,
        correlations=correlations(obs, mod),
        discrepancies=discrepancies(years, obs, mod),
        provenance=prov,
    )


# --- 5. report / export ----------------------------------------------------------------

CAVEATS = (
    "Observed TC loss is AGGREGATE: 재해연보 books wind + surge + rain under 태풍.",
    "Model TC loss is WIND-ONLY: the runner derives damage from wind speed alone.",
    "A difference between them therefore contains a model COVERAGE gap, not only parameter "
    "error — no re-parameterisation of the wind curve would close it.",
    "This diagnostic does not calibrate v_half, does not validate Korean TC vulnerability, "
    "does not reproduce national damage, and reports no absolute capture percentage.",
)


def annual_rows(diag: Diagnostic) -> list[dict[str, Any]]:
    """The annual table, one row per shared year."""
    r_obs, r_mod = ranks(diag.observed), ranks(diag.modelled)
    return [
        {
            "year": y,
            "observed_loss_KRW_million": o,
            "model_loss_normalized": m,
            "observed_rank": ro,
            "model_rank": rm,
        }
        for y, o, m, ro, rm in zip(
            diag.years, diag.observed, diag.modelled, r_obs, r_mod, strict=True
        )
    ]


def summary_markdown(diag: Diagnostic) -> str:
    """Human-readable diagnostic summary — the wording ceiling is set by :data:`CAVEATS`."""
    o, m = diag.observed_stats, diag.model_stats
    lines = [
        "# TC capture diagnostic — Korea",
        "",
        "**Diagnostic, not calibration.** Nothing here fits `v_half` or validates a "
        "vulnerability curve; the calibration gate remains BLOCKED.",
        "",
        f"- Observation window: {diag.years[0]}–{diag.years[-1]} (n={len(diag.years)})",
        "- Observed: aggregate TC (wind + surge + rain), 재해연보 15107318, KRW million",
        "- Model: wind-only TC, observed IBTrACS tracks, loss as a fraction of normalised exposure",
        f"- **ABSOLUTE CAPTURE RATE = {ABSOLUTE_CAPTURE}** — {ABSOLUTE_CAPTURE_REASON}",
        "",
        "## Annual comparison",
        "",
        "| year | observed (KRW m) | model (normalised) | obs rank | model rank |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {r['year']} | {r['observed_loss_KRW_million']:,.0f} | "
        f"{r['model_loss_normalized']:.3e} | {r['observed_rank']} | {r['model_rank']} |"
        for r in annual_rows(diag)
    ]
    lines += [
        "",
        "## Distribution",
        "",
        "| metric | observed | model |",
        "|---|---:|---:|",
        f"| coefficient of variation | {o['cv']:.2f} | {m['cv']:.2f} |",
        f"| max/min (non-zero years) | {o['nonzero_max_min']:.1f}x | {m['nonzero_max_min']:.1f}x |",
        f"| share carried by worst year | {o['max_year_share'] * 100:.1f}% | "
        f"{m['max_year_share'] * 100:.1f}% |",
        f"| zero years | {o['n_zero_years']} | {m['n_zero_years']} |",
        "",
        f"**Variability inflation factor** (model CV / observed CV): "
        f"**{diag.variability_inflation:.1f}x**. This describes these two series over this "
        "window; it is not a general model-error coefficient.",
        "",
        "## Correlation",
        "",
        f"- Pearson {diag.correlations['pearson']:+.3f} (p={diag.correlations['pearson_p']:.3f})",
        f"- Spearman {diag.correlations['spearman']:+.3f} "
        f"(p={diag.correlations['spearman_p']:.3f})",
        f"- {diag.correlations['note']}",
        "",
        "## Where the series disagree most",
        "",
    ]
    d = diag.discrepancies
    lines += [
        f"- Largest rank discrepancy: **{d['largest_rank_discrepancy']['year']}** "
        f"(observed #{d['largest_rank_discrepancy']['observed_rank']}, "
        f"model #{d['largest_rank_discrepancy']['model_rank']})",
        f"- Model most over-concentrated: **{d['model_most_over_concentrated']['year']}** "
        f"({d['model_most_over_concentrated']['model_share'] * 100:.1f}% of the modelled "
        f"window vs {d['model_most_over_concentrated']['observed_share'] * 100:.1f}% observed)",
        f"- Model most under-weighted: **{d['model_most_under_weighted']['year']}** "
        f"({d['model_most_under_weighted']['model_share'] * 100:.1f}% vs "
        f"{d['model_most_under_weighted']['observed_share'] * 100:.1f}% observed)",
        f"- Years both series report as zero: {d['agreed_zero_years'] or 'none'}",
        "",
        "## Caveats",
        "",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(CAVEATS, 1)]
    lines += ["", "## Reproducibility", "", "```json", json.dumps(diag.provenance, indent=2), "```"]
    return "\n".join(lines) + "\n"


def write_report(diag: Diagnostic, out_dir: Path) -> list[Path]:
    """Write the three artefacts and return their paths."""
    import csv

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = annual_rows(diag)
    csv_path = out_dir / "annual_comparison.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    json_path = out_dir / "distribution_summary.json"
    json_path.write_text(
        json.dumps(
            {
                "window": {"years": diag.years, "n": len(diag.years)},
                "observed": diag.observed_stats,
                "model": diag.model_stats,
                "variability_inflation_factor": diag.variability_inflation,
                "correlations": diag.correlations,
                "discrepancies": diag.discrepancies,
                "observed_covers_subperils": list(OBSERVED_COVERS),
                "model_covers_subperils": list(MODEL_COVERS),
                "coverage_mismatch": sorted(set(OBSERVED_COVERS) - set(MODEL_COVERS)),
                "absolute_capture_rate": ABSOLUTE_CAPTURE,
                "absolute_capture_rate_reason": ABSOLUTE_CAPTURE_REASON,
                "calibration": "BLOCKED",
                "provenance": diag.provenance,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    md_path = out_dir / "diagnostic_summary.md"
    md_path.write_text(summary_markdown(diag), encoding="utf-8")
    return [csv_path, json_path, md_path]


# --- CLI --------------------------------------------------------------------------------


def _climada_version() -> str:
    """Installed CLIMADA version — the package exposes no ``__version__`` attribute."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("climada")
    except PackageNotFoundError:  # pragma: no cover - CLIMADA is required to reach here
        return "unknown"


def run(out_dir: Path | None = None) -> Diagnostic:
    """Load, model, aggregate and diagnose. Requires CLIMADA and a data.go.kr key."""
    series = load_observed()
    hazard = load_observed_track_hazard()
    exposure, exp_prov = build_normalized_exposure()
    impact, impf_prov = model_impact(exposure, hazard)
    model_losses = annual_model_losses(impact, hazard)

    hazard_years = sorted({int(y) for y in _event_years(hazard)})
    provenance: dict[str, Any] = {
        "observed_source": series.source,
        "observed_unit": series.unit,
        "observed_period": list(series.observed_period or ()),
        "observed_n_years": series.n_years,
        "observed_n_nonzero_years": series.n_nonzero_years,
        "observed_covers_subperils": list(series.covers_subperils),
        "observed_scope": series.scope,
        "observed_price_basis": series.price_basis,
        "hazard_source": "CLIMADA Data API tropical_cyclone, country_iso3alpha=KOR",
        "hazard_vintage": "event_type=observed (IBTrACS original tracks)",
        "hazard_years": [hazard_years[0], hazard_years[-1]] if hazard_years else [],
        "hazard_n_events": int(hazard.size),
        "hazard_all_original_tracks": True,
        "model_covers_subperils": list(MODEL_COVERS),
        "normalization": "exposure values scaled so the national total is 1.0; modelled loss "
        "is a fraction of normalised exposure, never a currency amount",
        "random_seed": None,
        "random_process": "none — this diagnostic draws no random numbers",
        "climada_version": _climada_version(),
        "python_version": sys.version.split()[0],
        **exp_prov,
        **impf_prov,
    }
    diag = diagnose(series.losses, model_losses, provenance=provenance)
    if out_dir is not None:
        for path in write_report(diag, out_dir):
            print(f"wrote {path}")
    return diag


def _event_years(hazard: Any) -> list[int]:
    import datetime as dt

    return [dt.date.fromordinal(int(d)).year for d in hazard.date if int(d) > 0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "outputs" / "tc_capture_kr",
        help="directory for annual_comparison.csv / distribution_summary.json / "
        "diagnostic_summary.md",
    )
    args = parser.parse_args(argv)
    diag = run(args.out)
    print()
    print(summary_markdown(diag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
