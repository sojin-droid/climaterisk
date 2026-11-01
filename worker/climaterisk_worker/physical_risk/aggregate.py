"""Portfolio (and 시도) return-period losses from event-level losses — non-additive, done right.

A per-asset 100-year loss is read from that asset's own frequency curve, so two assets'
100-year losses usually come from different events and do not add. The portfolio's
100-year loss is read from the frequency curve of the **portfolio's** event losses: every
asset priced against the same hazard set shares its event axis, so the per-event losses add
exactly, and CLIMADA's ``Impact.calc_freq_curve`` is applied to that sum. Nothing about the
curve is re-implemented here; this module only adds event vectors and asks CLIMADA.

Algorithm:
    $$L_e^{P} = \\sum_{a \\in P} L_{a,e}, \\qquad
      \\mathrm{EAL}^{P} = \\sum_e f_e L_e^{P} = \\sum_{a \\in P} \\mathrm{EAL}_a, \\qquad
      \\mathrm{RP}_k = 1 \\Big/ \\sum_{j \\le k} f_{(j)}$$
    ASCII: L_P[e] = sum_a L[a, e]; EAL_P = sum_e f[e] * L_P[e] (= sum of asset EALs);
    sort events by L_P descending, RP of the k-th = 1 / cumulative frequency.

    ``L_{a,e}``: loss of asset ``a`` in event ``e`` (asset currency); ``f_e``: annual event
    frequency (1/year); ``f_{(j)}``: frequency of the j-th largest portfolio event; ``RP``:
    return period (years). EAL adds (expectation is linear); the RP loss does not, and
    ``sum_a RP-loss_a - RP-loss_P`` is the diversification the per-asset figures hide.

Refusals, each of which would otherwise print a plausible number:

* **Beyond the curve.** ``calc_freq_curve`` interpolates with ``np.interp``, which clamps:
  above the rarest point of the curve it repeats the largest event's loss, below the most
  frequent point it repeats the smallest. Both are reported as not resolvable (null), not
  as a loss. The ceiling is the curve's own longest return period (``1 / f`` of the
  largest-loss event), which for a portfolio need not equal ``1 / min(f)``.
* **Mixed currencies.** Event losses in different currencies are not added; the group is
  reported as ``MIXED_CURRENCY`` with no money.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Status of one return-period cell.
RESOLVED = "RESOLVED"
ABOVE_CURVE = "RETURN_PERIOD_NOT_RESOLVABLE"
BELOW_CURVE = "SHORTER_THAN_CURVE"
#: Status of a whole group.
GROUP_OK = "OK"
GROUP_MIXED_CURRENCY = "MIXED_CURRENCY"
GROUP_NO_PRICED_POINTS = "NO_PRICED_POINTS"


def _impact(at_event: Any, frequency: Any, unit: str) -> Any:
    """A CLIMADA ``Impact`` carrying only what ``calc_freq_curve`` and ``aai_agg`` need."""
    import numpy as np
    from climada.engine import Impact

    at_event = np.asarray(at_event, dtype=float)
    frequency = np.asarray(frequency, dtype=float)
    return Impact(
        event_id=np.arange(1, at_event.size + 1),
        event_name=[str(i) for i in range(1, at_event.size + 1)],
        date=np.zeros(at_event.size, dtype=int),
        frequency=frequency,
        frequency_unit="1/year",
        at_event=at_event,
        aai_agg=float(np.sum(at_event * frequency)),
        unit=unit,
    )


def curve_losses(
    at_event: Any, frequency: Any, return_periods: Sequence[float], unit: str = ""
) -> tuple[list[dict[str, Any]], float | None, float | None]:
    """Loss at each return period from one event-loss vector, refusing outside the curve.

    Args:
        at_event: Loss per event, aligned with ``frequency``.
        frequency: Annual frequency per event (1/year).
        return_periods: Requested return periods, years.
        unit: Currency label carried on the CLIMADA object.

    Returns:
        ``(cells, curve_min_rp, curve_max_rp)``; one cell per requested return period with
        ``return_period_years``, ``loss`` (None unless ``RESOLVED``), ``status`` and
        ``detail``. The bounds are None when no event has a positive frequency.
    """
    import numpy as np

    imp = _impact(at_event, frequency, unit)
    if imp.frequency.size == 0 or float(np.sum(imp.frequency)) <= 0:
        return (
            [
                {
                    "return_period_years": float(rp),
                    "loss": None,
                    "status": ABOVE_CURVE,
                    "detail": "event set carries no positive frequency",
                }
                for rp in return_periods
            ],
            None,
            None,
        )
    full = imp.calc_freq_curve()
    rp_axis = np.asarray(full.return_per, dtype=float)
    lo, hi = float(rp_axis.min()), float(rp_axis.max())
    inside = [float(rp) for rp in return_periods if lo <= float(rp) <= hi]
    values = (
        dict(zip(inside, np.asarray(imp.calc_freq_curve(inside).impact, dtype=float), strict=True))
        if inside
        else {}
    )
    cells: list[dict[str, Any]] = []
    for rp in (float(r) for r in return_periods):
        if rp in values:
            cells.append(
                {
                    "return_period_years": rp,
                    "loss": float(values[rp]),
                    "status": RESOLVED,
                    "detail": None,
                }
            )
        elif rp > hi:
            cells.append(
                {
                    "return_period_years": rp,
                    "loss": None,
                    "status": ABOVE_CURVE,
                    "detail": (
                        f"{rp:g}-year loss is beyond the {hi:.1f}-year end of this curve; "
                        "calc_freq_curve would repeat the largest event's loss"
                    ),
                }
            )
        else:
            cells.append(
                {
                    "return_period_years": rp,
                    "loss": None,
                    "status": BELOW_CURVE,
                    "detail": (
                        f"{rp:g}-year loss is shorter than the {lo:.2f}-year start of this "
                        "curve; calc_freq_curve would repeat the smallest event's loss"
                    ),
                }
            )
    return cells, lo, hi


def group_loss_curve(
    event_losses: Mapping[str, Any],
    frequency: Any,
    return_periods: Sequence[float],
    *,
    currencies: Mapping[str, str | None],
    asset_values: Mapping[str, float | None],
) -> dict[str, Any]:
    """The loss curve of one group of points priced against one shared hazard set.

    Args:
        event_losses: ``point id -> at_event`` (same event order as ``frequency``).
        frequency: The hazard set's annual event frequencies.
        return_periods: Return periods to report, years.
        currencies: ``point id -> asset_value_currency`` (None reads as USD).
        asset_values: ``point id -> asset value`` for the priced-value column.

    Returns:
        ``status``, ``currency``, ``points_priced``, ``priced_asset_value``, ``eal``
        (portfolio ``aai_agg`` — equals the sum of the points' EAL), the curve bounds, and
        ``losses``: per return period the portfolio loss, the sum of the points' standalone
        losses (only when every point resolves it) and the diversification benefit
        ``1 - portfolio / standalone sum`` in percent.
    """
    import numpy as np

    ids = sorted(event_losses)
    if not ids:
        return {"status": GROUP_NO_PRICED_POINTS, "points_priced": 0, "losses": []}
    units = {currencies.get(i) or "USD" for i in ids}
    base: dict[str, Any] = {
        "points_priced": len(ids),
        "priced_asset_value": float(sum(float(asset_values.get(i) or 0.0) for i in ids)),
    }
    if len(units) > 1:
        return {
            **base,
            "status": GROUP_MIXED_CURRENCY,
            "currency": None,
            "detail": (
                f"asset values in {sorted(units)} — event losses are not added across currencies"
            ),
            "losses": [],
        }
    unit = units.pop()
    stack = np.vstack([np.asarray(event_losses[i], dtype=float) for i in ids])
    portfolio = stack.sum(axis=0)
    cells, lo, hi = curve_losses(portfolio, frequency, return_periods, unit)
    standalone = [curve_losses(row, frequency, return_periods, unit)[0] for row in stack]
    for k, cell in enumerate(cells):
        point_losses = [s[k]["loss"] for s in standalone]
        total = None if any(v is None for v in point_losses) else float(sum(point_losses))
        cell["sum_of_standalone_losses"] = total
        cell["diversification_pct"] = (
            (1.0 - cell["loss"] / total) * 100.0
            if cell["loss"] is not None and total is not None and total > 0
            else None
        )
    return {
        **base,
        "status": GROUP_OK,
        "currency": unit,
        "eal": float(np.sum(portfolio * np.asarray(frequency, dtype=float))),
        "curve_return_period_min": lo,
        "curve_return_period_max": hi,
        "losses": cells,
    }
