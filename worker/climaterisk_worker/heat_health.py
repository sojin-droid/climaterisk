"""Heat health (열 관련 초과사망률) on the visual board (A4): rates per 100 000 people per piece.

The science is the repository's own heat-mortality model (``heat_mortality.py``): the hazard is
the season exceedance degree-days $D$ above a location's comfort band (20 seasons 2000-2019 on
the 0.05 deg KMA grid, ``HM_historical_KOR_2020.hdf5``), and the CLIMADA impact function of an
age band $b$ (<65, 65+; ``heat_mortality.build_impact_functions`` with the KOSIS 2024 baseline
mortality) turns it into attributable deaths:

.. math::

    \\mathrm{deaths}_{b,e,p} = P_{b,p}\\cdot \\mathrm{mdd}_b(D_{e,c(p)}),\\qquad
    r_{e,p} = 10^{5}\\,\\frac{\\sum_b \\mathrm{deaths}_{b,e,p}}{\\sum_b P_{b,p}}

with $P_{b,p}$ the persons of band $b$ in piece $p$ (WorldPop 2024, summed over the 1 km pixels
whose centre lies in the piece; persons add up, no hazard value is averaged), $e$ a season,
$c(p)$ the hazard cell of the piece, $\\mathrm{mdd}_b$ deaths per person at $D$ degC-days (the
band's baseline daily mortality times the dose-response) and $r$ in deaths per 100 000 people.
The annual-mean rate weights the 20 seasons by their frequencies (0.05 each). ASCII:
deaths = persons_band * mdd_band(D); rate = 1e5 * deaths / persons.

This module holds only the pure rate math (no I/O at import); the CLIMADA run and the data
reading are in ``scripts/heat_health_board.py``. Exposure is the 2024 population and age
structure applied to every 2000-2019 season (stated in the layer notes).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


def share_over65(bands: dict[str, Any], from_age: int = 65) -> Any:
    """Share of persons aged ``from_age`` and above, per cell.

    Algorithm:
        $$s = \\frac{\\sum_{a \\ge 65} P_a}{\\sum_a P_a}$$ with $P_a$ the persons of the
        WorldPop age band whose lower bound is $a$ years (labels ``"0"``, ``"1"``, ``"5"``
        ... ``"80"``; 80 is the open ``80+`` band). NaN where the total is 0.

    Args:
        bands: Age-band label -> persons array (all arrays one shape).
        from_age: Lowest lower bound counted as old.

    Returns:
        Array of the common shape, a fraction in 0..1 or NaN.
    """
    arrays = {int(k): np.asarray(v, dtype=float) for k, v in bands.items()}
    total = sum(arrays.values())
    old = sum(v for a, v in arrays.items() if a >= from_age)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(total > 0, old / total, np.nan)


def rate_per_100k(deaths: Any, population: Any) -> Any:
    """Deaths per 100 000 people: ``1e5 * deaths / population``, NaN where population is 0.

    Args:
        deaths: Deaths (persons) per cell.
        population: Persons per cell.

    Returns:
        Float array; a cell without people has no rate (NaN), not 0.
    """
    d = np.asarray(deaths, dtype=float)
    p = np.asarray(population, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(p > 0, 1e5 * d / p, np.nan)


def assign_pixels(pieces: Any, lon: Any, lat: Any) -> Any:
    """Index of the piece containing each point (``-1`` if none).

    Pieces are tiles of the country (cell x 시군구) and may share an edge or overlap by the
    simplification tolerance; a point in several pieces takes the lowest piece index, so every
    person is counted once.

    Args:
        pieces: GeoDataFrame from ``board.clip_pieces`` (index = row position).
        lon: Point longitudes (degrees east).
        lat: Point latitudes (degrees north).

    Returns:
        Integer array of piece row positions, ``-1`` outside every piece.
    """
    import shapely

    pts = shapely.points(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    pt_ix, piece_ix = pieces.sindex.query(pts, predicate="within")
    out = np.full(len(pts), -1, dtype=int)
    order = np.lexsort((piece_ix, pt_ix))  # by point, then lowest piece
    pt_ix, piece_ix = pt_ix[order], piece_ix[order]
    first = np.r_[True, pt_ix[1:] != pt_ix[:-1]]
    out[pt_ix[first]] = piece_ix[first]
    log.debug("assign_pixels points=%d assigned=%d", len(pts), int(first.sum()))
    return out


def sum_by_piece(piece_index: Any, values: Any, n_pieces: int) -> Any:
    """Sum ``values`` over the points of each piece (points with index < 0 are skipped)."""
    ix = np.asarray(piece_index)
    keep = ix >= 0
    return np.bincount(ix[keep], weights=np.asarray(values, dtype=float)[keep], minlength=n_pieces)
