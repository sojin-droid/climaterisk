"""Observed-vs-modelled validation framework (arithmetic only; no CLIMADA import).

**Status (2026-09-07): framework only.** This module defines the comparison metrics and the
observed-loss input contract. It has **not** been run against observed Korean losses —
no 재해연보 / EM-DAT Korea series is wired into the repository yet — and the unit tests
exercise the arithmetic on synthetic numbers. Do not describe anything computed here as an
empirical validation until an observed series (with its source recorded) is supplied.

Inputs
------
``observed``  — ``{year: loss}`` from an observed series (EM-DAT, 재해연보 API, insurer
statistics). Record the ``source`` string alongside; see :class:`ObservedSeries`.
``modelled``  — ``{year: loss}`` for the same asset set. For a CLIMADA ``Impact`` with dated
events use :func:`modelled_annual_losses` (sums ``at_event`` per calendar year of ``date``);
for a probabilistic set without calendar years compare ``aai_agg`` to the observed mean
instead (``annual_comparison`` on a one-value series is not meaningful).

Metrics (all in the currency of the inputs)
-------------------------------------------
``bias``     mean(modelled − observed) over overlapping years (sign: + = model too high)
``mae``      mean |modelled − observed|
``rmse``     sqrt(mean (modelled − observed)²)
``ratio``    Σ modelled / Σ observed over overlapping years
``coverage`` share of observed years that have a modelled counterpart
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ObservedSeries:
    """An observed annual-loss series with its provenance (required for any validation claim)."""

    source: str  # e.g. "행정안전부 재해연보 API (data.go.kr 15107316), 태풍, 시군구 합계"
    unit: str  # currency / unit of ``losses``
    losses: dict[int, float] = field(default_factory=dict)  # {calendar year: loss}
    peril: str = "tropical_cyclone"
    notes: str = ""

    def years(self) -> list[int]:
        return sorted(self.losses)


def annual_comparison(
    observed: Mapping[int, float], modelled: Mapping[int, float]
) -> dict[str, Any]:
    """Compare two ``{year: loss}`` series on their overlapping years.

    Returns a dict with ``n`` (overlap years), ``years``, ``bias``, ``mae``, ``rmse``,
    ``ratio``, ``coverage`` and the paired values. All-NaN metrics when there is no overlap.
    """
    years = sorted(set(observed) & set(modelled))
    n_obs = len(observed)
    if not years:
        return {
            "n": 0,
            "years": [],
            "bias": float("nan"),
            "mae": float("nan"),
            "rmse": float("nan"),
            "ratio": float("nan"),
            "coverage": 0.0,
            "observed": [],
            "modelled": [],
        }
    obs = np.array([float(observed[y]) for y in years])
    mod = np.array([float(modelled[y]) for y in years])
    diff = mod - obs
    total_obs = float(obs.sum())
    return {
        "n": len(years),
        "years": years,
        "bias": float(diff.mean()),
        "mae": float(np.abs(diff).mean()),
        "rmse": float(np.sqrt((diff**2).mean())),
        "ratio": float(mod.sum() / total_obs) if total_obs > 0 else float("nan"),
        "coverage": len(years) / n_obs if n_obs else 0.0,
        "observed": [float(x) for x in obs],
        "modelled": [float(x) for x in mod],
    }


def event_comparison(pairs: Iterable[tuple[str, float, float]]) -> dict[str, Any]:
    """Compare matched events ``(label, observed_loss, modelled_loss)``.

    Returns per-event ratios plus ``bias``/``mae``/``rmse`` and the ratio of totals —
    the CLIMADA papers' TDR-style aggregate (Σ modelled / Σ observed) and EDR-style
    per-event ratios (Eberenz et al. 2021 terminology).
    """
    rows = [(str(k), float(o), float(m)) for k, o, m in pairs]
    if not rows:
        return {
            "n": 0,
            "events": [],
            "bias": float("nan"),
            "mae": float("nan"),
            "rmse": float("nan"),
            "total_ratio": float("nan"),
        }
    obs = np.array([o for _, o, _ in rows])
    mod = np.array([m for _, _, m in rows])
    diff = mod - obs
    total_obs = float(obs.sum())
    return {
        "n": len(rows),
        "events": [
            {"label": k, "observed": o, "modelled": m, "ratio": (m / o) if o > 0 else float("nan")}
            for k, o, m in rows
        ],
        "bias": float(diff.mean()),
        "mae": float(np.abs(diff).mean()),
        "rmse": float(np.sqrt((diff**2).mean())),
        "total_ratio": float(mod.sum() / total_obs) if total_obs > 0 else float("nan"),
    }


def modelled_annual_losses(at_event: Iterable[float], dates: Iterable[float]) -> dict[int, float]:
    """Sum per-event losses into calendar years from proleptic-Gregorian ordinal dates.

    ``at_event`` and ``date`` are the CLIMADA ``Impact`` attributes; events with a
    non-positive date (undated synthetic events) are skipped.
    """
    out: dict[int, float] = {}
    for loss, d in zip(at_event, dates, strict=True):
        if d is None or float(d) <= 0:
            continue
        year = _dt.date.fromordinal(int(d)).year
        out[year] = out.get(year, 0.0) + float(loss)
    return out
