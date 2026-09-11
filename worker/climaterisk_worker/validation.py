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

# --- units and currencies ------------------------------------------------------------
# A comparison is only arithmetic if both sides are in the same currency AND the same
# multiple of it. "KRW thousand" vs "KRW" is a 1000x error that no metric would reveal, so
# the unit strings are parsed and compared instead of trusted. An unrecognised unit is
# reported as UNKNOWN, never as compatible (docs/OBSERVED_LOSSES_KR_SPEC.md §5).

_CURRENCY_ALIASES: dict[str, str] = {
    "krw": "KRW",
    "won": "KRW",
    "원": "KRW",
    "₩": "KRW",
    "usd": "USD",
    "us$": "USD",
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "eur": "EUR",
    "€": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "jpy": "JPY",
    "yen": "JPY",
    "¥": "JPY",
}
# Multiples, largest token first so "백만원" is not read as "만원". Searched only AFTER the
# currency token has been removed — otherwise "KRW" matches a bare "k" and reads as thousands,
# which made "KRW thousand" and "KRW" compare equal.
_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("조", 1e12),
    ("trillion", 1e12),
    ("억", 1e8),
    ("백만", 1e6),
    ("million", 1e6),
    ("mn", 1e6),
    ("만", 1e4),
    ("천", 1e3),
    ("thousand", 1e3),
    ("'000", 1e3),
)

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class UnitSpec:
    """A unit string resolved into ``(currency, multiplier)``; ``None`` means *not recognised*."""

    raw: str
    currency: str | None
    multiplier: float | None

    def known(self) -> bool:
        return self.currency is not None and self.multiplier is not None


def _normalize(text: str) -> str:
    return "".join(str(text).lower().split())


def parse_unit(unit: str) -> UnitSpec:
    """Resolve a unit string into a currency and a multiplier.

    Recognises the forms these sources actually publish — "KRW", "천원", "억원",
    "KRW thousand", "USD", "million USD". Anything else yields ``None`` parts, which the
    checks below treat as *unknown* (blocking), not as agreement.
    """
    raw = str(unit)
    text = _normalize(raw)
    currency = next(
        (code for alias, code in _CURRENCY_ALIASES.items() if alias in text),
        None,
    )
    # Remove every currency token before looking for a multiple, so letters inside a currency
    # code ("k" in "KRW") cannot be read as one.
    rest = text
    for alias in _CURRENCY_ALIASES:
        rest = rest.replace(alias, " ")
    multiplier = next((mult for token, mult in _MULTIPLIERS if token in rest), None)
    if currency is not None and multiplier is None:
        multiplier = 1.0  # a bare currency means units of that currency
    return UnitSpec(raw=raw, currency=currency, multiplier=multiplier)


def check_units(observed_unit: str, modelled_unit: str) -> dict[str, Any]:
    """Compare two unit strings. Returns ``status`` ``ok`` / ``mismatch`` / ``unknown``.

    ``basis`` records *how* the verdict was reached: ``parsed`` (currency and multiplier both
    recognised) or ``identical-string`` (unrecognised units that are textually identical — the
    honest verdict for non-monetary units such as "persons/yr", recorded rather than assumed).
    """
    obs, mod = parse_unit(observed_unit), parse_unit(modelled_unit)
    reasons: list[str] = []
    if obs.known() and mod.known():
        if obs.currency != mod.currency:
            reasons.append(f"currency {obs.currency} vs {mod.currency}")
        if obs.multiplier != mod.multiplier:
            reasons.append(f"multiplier {obs.multiplier:g} vs {mod.multiplier:g}")
        status = STATUS_MISMATCH if reasons else STATUS_OK
        basis = "parsed"
    elif _normalize(observed_unit) and _normalize(observed_unit) == _normalize(modelled_unit):
        status, basis = STATUS_OK, "identical-string"
    else:
        status, basis = STATUS_UNKNOWN, "unparsed"
        reasons.append(
            f"unit not recognised: observed {observed_unit!r} / modelled {modelled_unit!r}"
        )
    return {
        "status": status,
        "basis": basis,
        "observed_unit": str(observed_unit),
        "modelled_unit": str(modelled_unit),
        "observed_currency": obs.currency,
        "modelled_currency": mod.currency,
        "observed_multiplier": obs.multiplier,
        "modelled_multiplier": mod.multiplier,
        "reasons": reasons,
    }


@dataclass(frozen=True)
class ObservedSeries:
    """An observed annual-loss series with its provenance (required for any validation claim).

    The first five fields are the original contract, unchanged. The rest were added 2026-09-07
    **with defaults**, so every existing construction keeps working; they carry what a
    comparability gate needs (:func:`comparability_report`) instead of leaving it implicit:

    ``currency``          ISO code when known ("KRW"); empty = derive it from ``unit``.
    ``covers_subperils``  which sub-perils this number contains. 재해연보 and EM-DAT book a
                          typhoon's **wind + surge + rain** together while the platform's TC
                          runner models wind only — docs/OBSERVED_LOSSES_KR_SPEC.md §6-1.
    ``scope``             the spatial aggregate the number refers to ("national:KOR",
                          "admin2:28710"), compared against the modelled exposure's scope so a
                          national total is never fitted to a handful of point assets (§8).
    ``price_basis``       "nominal" / "real:2020" — 재해연보 재산피해 is 당해연도 가격 (§5).
    """

    source: str  # e.g. "행정안전부 재해연보 API (data.go.kr 15107316), 태풍, 시군구 합계"
    unit: str  # currency / unit of ``losses``
    losses: dict[int, float] = field(default_factory=dict)  # {calendar year: loss}
    peril: str = "tropical_cyclone"
    notes: str = ""
    currency: str = ""
    covers_subperils: tuple[str, ...] = ()
    scope: str = ""
    price_basis: str = ""

    def years(self) -> list[int]:
        return sorted(self.losses)

    @property
    def n_years(self) -> int:
        """Years the series carries — including years whose loss is zero."""
        return len(self.losses)

    @property
    def n_nonzero_years(self) -> int:
        """Years with a loss above zero. A zero year is data, not a gap."""
        return sum(1 for value in self.losses.values() if value > 0.0)

    @property
    def observed_period(self) -> tuple[int, int] | None:
        """``(first, last)`` year kept, or None for an empty series.

        Derived from ``losses`` rather than stored, so it always describes what the series
        actually contains: a loader that drops a year (a source defect, say) reports the
        narrower period here and the reason in ``notes``.
        """
        if not self.losses:
            return None
        years = self.years()
        return years[0], years[-1]

    def unit_spec(self) -> UnitSpec:
        """Parsed ``unit``; an explicit ``currency`` overrides what the parse inferred."""
        spec = parse_unit(self.unit)
        if self.currency:
            return UnitSpec(spec.raw, self.currency.strip().upper(), spec.multiplier)
        return spec

    def mean_annual_loss(self) -> float:
        """Mean over the years present — the target a one-scalar AAI fit matches."""
        return float(np.mean(list(self.losses.values()))) if self.losses else float("nan")


def annual_comparison(
    observed: Mapping[int, float],
    modelled: Mapping[int, float],
    *,
    observed_series: ObservedSeries | None = None,
    modelled_unit: str = "",
    modelled_covers: Iterable[str] = (),
    modelled_scope: str = "",
    is_original_subset: bool | None = None,
) -> dict[str, Any]:
    """Compare two ``{year: loss}`` series on their overlapping years.

    Returns a dict with ``n`` (overlap years), ``years``, ``bias``, ``mae``, ``rmse``,
    ``ratio``, ``coverage`` and the paired values. All-NaN metrics when there is no overlap.
    Only the intersection of the two year sets is compared — a modelled year with no observed
    counterpart (and the reverse) is excluded and shows up in ``coverage``.

    The keyword arguments are optional metadata (added 2026-09-07; the metrics above are
    unchanged). When they are given, the result also carries ``years_compared``,
    ``observed_unit``, ``modelled_unit``, ``peril_coverage`` and ``comparison_status`` from
    :func:`comparability_report`. When they are omitted, ``comparison_status`` is
    ``"unknown"`` — never ``"comparable"``: metrics computed without stated units, coverage and
    scope are arithmetic, not evidence.
    """
    years = sorted(set(observed) & set(modelled))
    n_obs = len(observed)
    if not years:
        out: dict[str, Any] = {
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
    else:
        obs = np.array([float(observed[y]) for y in years])
        mod = np.array([float(modelled[y]) for y in years])
        diff = mod - obs
        total_obs = float(obs.sum())
        out = {
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

    out["years_compared"] = list(years)
    if observed_series is None:
        out.update(
            {
                "observed_unit": None,
                "modelled_unit": modelled_unit or None,
                "peril_coverage": None,
                "comparison_status": COMPARISON_UNKNOWN,
                "comparability": None,
            }
        )
        return out
    report = comparability_report(
        observed=observed_series,
        modelled_unit=modelled_unit,
        modelled_covers=modelled_covers,
        modelled_scope=modelled_scope,
        is_original_subset=is_original_subset,
    )
    out.update(
        {
            "observed_unit": observed_series.unit,
            "modelled_unit": modelled_unit,
            "peril_coverage": report["peril_coverage"],
            "comparison_status": report["comparison_status"],
            "comparability": report,
        }
    )
    return out


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


def modelled_annual_losses(
    at_event: Iterable[float],
    dates: Iterable[float],
    orig: Iterable[bool] | None = None,
) -> dict[int, float]:
    """Sum per-event losses into calendar years from proleptic-Gregorian ordinal dates.

    ``at_event`` and ``date`` are the CLIMADA ``Impact`` attributes; events with a
    non-positive date (undated synthetic events) are skipped.

    ``orig`` is the caller-supplied original/observed-track mask (CLIMADA ``Hazard.orig``).
    **Pass it whenever the impact came from a probabilistic set**: synthetic ensemble members
    inherit their parent track's date, so summing them by calendar year multiplies that year's
    loss by the ensemble size. This helper only filters arrays the caller already has — it does
    not select or rebuild hazards (see :func:`check_annual_basis` and
    docs/OBSERVED_LOSSES_KR_SPEC.md §7).
    """
    out: dict[int, float] = {}
    mask = list(orig) if orig is not None else None
    for i, (loss, d) in enumerate(zip(at_event, dates, strict=True)):
        if mask is not None and not bool(mask[i]):
            continue
        if d is None or float(d) <= 0:
            continue
        year = _dt.date.fromordinal(int(d)).year
        out[year] = out.get(year, 0.0) + float(loss)
    return out


# --- comparability gates -------------------------------------------------------------
# Everything below answers one question: *may these two numbers be compared at all, and may a
# calibration be fitted between them?* Optimisation convergence is not scientific validity, so
# a fit that converges on an incomparable pair must still be reported as not comparable.

#: What an aggregate tropical-cyclone loss statistic (재해연보 태풍, EM-DAT TC) contains.
TC_AGGREGATE_SUBPERILS: tuple[str, ...] = ("wind", "surge", "rain")

COMPARISON_COMPARABLE = "comparable"
COMPARISON_NOT_COMPARABLE = "not_comparable"
COMPARISON_UNKNOWN = "unknown"

#: Blockers that mean "could not be checked" rather than "checked and wrong".
_UNVERIFIABLE_PREFIXES = (
    "unit not verifiable",
    "sub-peril coverage not declared",
    "spatial scope undeclared",
)

#: Accepted spatial-scope shapes for :func:`check_scope` (C in the spec = any explicit match).
SCOPE_UNDECLARED = "undeclared"


def check_peril_coverage(
    observed_covers: Iterable[str], modelled_covers: Iterable[str]
) -> dict[str, Any]:
    """Compare which sub-perils each side contains.

    ``calibration_ok`` is True **only** when the two sets are equal. The case this exists for is
    ``observed = {wind, surge, rain}`` against ``modelled = {wind}``: fitting there would make the
    wind vulnerability curve absorb the missing rain and surge damage, producing a curve that
    matches the total and misattributes the physics (spec §6-1; measured capture 1.5–20 %).
    """
    obs = {str(s).strip().lower() for s in observed_covers if str(s).strip()}
    mod = {str(s).strip().lower() for s in modelled_covers if str(s).strip()}
    if not obs or not mod:
        status, calibration_ok = STATUS_UNKNOWN, False
    elif obs == mod:
        status, calibration_ok = STATUS_OK, True
    elif obs > mod:
        status, calibration_ok = "observed_wider", False
    elif mod > obs:
        status, calibration_ok = "modelled_wider", False
    else:
        status, calibration_ok = STATUS_MISMATCH, False
    return {
        "status": status,
        "calibration_ok": calibration_ok,
        "observed": sorted(obs),
        "modelled": sorted(mod),
        "missing_in_model": sorted(obs - mod),
        "extra_in_model": sorted(mod - obs),
    }


def check_scope(observed_scope: str, modelled_scope: str) -> dict[str, Any]:
    """Compare the spatial aggregates the two numbers refer to.

    An undeclared scope is **not** treated as a match: the runner cannot verify that a national
    observed total belongs with the exposure in the request, so it must refuse rather than assume
    (spec §8 — the defect where national losses were fitted to whatever assets were placed).
    """
    obs, mod = _normalize(observed_scope), _normalize(modelled_scope)
    if not obs or not mod:
        status = SCOPE_UNDECLARED
    elif obs == mod:
        status = STATUS_OK
    else:
        status = STATUS_MISMATCH
    return {
        "status": status,
        "observed_scope": str(observed_scope),
        "modelled_scope": str(modelled_scope),
    }


def check_annual_basis(
    is_original_subset: bool | None, *, hazard_label: str = ""
) -> dict[str, Any]:
    """Whether a *year-by-year* modelled series is defensible for this hazard set.

    ``is_original_subset`` — True when the modelled losses come from observed/original tracks
    (or an explicit ``orig == True`` subset), False for a full probabilistic set, None when the
    caller did not say. Only True permits per-year comparison; the other two leave AAI-vs-mean
    as the honest option. This reports; it does not filter hazards (spec §7).
    """
    if is_original_subset is True:
        status, annual_ok = "observed_tracks", True
        note = (
            "modelled years come from original/observed tracks — per-year comparison is defensible"
        )
    elif is_original_subset is False:
        status, annual_ok = "synthetic_unfiltered", False
        note = (
            "probabilistic set: synthetic members inherit parent-track dates, so per-year sums are "
            "inflated by the ensemble size — compare aai_agg with the observed mean instead"
        )
    else:
        status, annual_ok = SCOPE_UNDECLARED, False
        note = "caller did not state whether the modelled events are an original-track subset"
    return {
        "status": status,
        "annual_comparison_ok": annual_ok,
        "hazard": str(hazard_label),
        "note": note,
    }


def comparability_report(
    *,
    observed: ObservedSeries,
    modelled_unit: str,
    modelled_covers: Iterable[str] = (),
    modelled_scope: str = "",
    is_original_subset: bool | None = None,
    hazard_label: str = "",
) -> dict[str, Any]:
    """One gate for units, sub-peril coverage, scope and annual basis.

    Returns ``comparison_status`` (``comparable`` / ``not_comparable`` / ``unknown``),
    ``calibration_allowed`` and the list of ``blockers``. Shared by the validation path and the
    calibration runner so both refuse the same pairs for the same stated reasons.
    """
    units = check_units(observed.unit, modelled_unit)
    perils = check_peril_coverage(observed.covers_subperils, modelled_covers)
    scope = check_scope(observed.scope, modelled_scope)
    basis = check_annual_basis(is_original_subset, hazard_label=hazard_label)

    blockers: list[str] = []
    if units["status"] == STATUS_MISMATCH:
        blockers.append("unit mismatch: " + "; ".join(units["reasons"]))
    elif units["status"] == STATUS_UNKNOWN:
        blockers.append("unit not verifiable: " + "; ".join(units["reasons"]))
    if not perils["calibration_ok"]:
        if perils["status"] == "observed_wider":
            blockers.append(
                "observed aggregates sub-perils the model does not carry: missing "
                + ", ".join(perils["missing_in_model"])
            )
        elif perils["status"] == STATUS_UNKNOWN:
            blockers.append("sub-peril coverage not declared on one or both sides")
        else:
            blockers.append(
                f"sub-peril coverage {perils['status']}: observed {perils['observed']} "
                f"vs modelled {perils['modelled']}"
            )
    if scope["status"] == STATUS_MISMATCH:
        blockers.append(
            f"spatial scope mismatch: observed {scope['observed_scope']!r} "
            f"vs modelled {scope['modelled_scope']!r}"
        )
    elif scope["status"] == SCOPE_UNDECLARED:
        blockers.append("spatial scope undeclared on one or both sides")

    if blockers:
        status = (
            COMPARISON_UNKNOWN
            if all(b.startswith(_UNVERIFIABLE_PREFIXES) for b in blockers)
            else COMPARISON_NOT_COMPARABLE
        )
    else:
        status = COMPARISON_COMPARABLE
    return {
        "comparison_status": status,
        "calibration_allowed": status == COMPARISON_COMPARABLE,
        "blockers": blockers,
        "units": units,
        "peril_coverage": perils,
        "scope": scope,
        "annual_basis": basis,
        "observed_source": observed.source,
        "observed_price_basis": observed.price_basis,
    }
