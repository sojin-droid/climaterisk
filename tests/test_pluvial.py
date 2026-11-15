"""Step R2 gate (written by the reviewer before the step; agents may not edit it).

Rain on the real Gangnam site: a design storm, NRCS curve-number losses, sewer drainage as a
capacity-limited sink, water leaving at the site edges, and buildings as walls whose roof
rain runs off onto the streets. Repo-specific method (label it so in outputs).

Additions to ``twin/hydraulics.py`` ``run(...)``:

- ``rain_fn``: callable ``t_s -> rate`` (m/s, scalar or ``(ny, nx)`` array) used instead of the
  fixed ``rain`` array; the rate is held over each step from its start time.
- ``open_edges``: ``None`` (closed walls, default) or a bed slope ``S0`` (-). Then every outer
  face of an edge cell lets water out at Manning normal flow
  ``q = h^(5/3) sqrt(S0) / n`` per unit width (limited, with the other outflows, so no cell
  drains below zero).
- result keys ``rain_volume_m3`` (rain added), ``sink_volume_m3`` (water actually removed by
  ``infiltration``, which acts as a capacity-limited sink) and ``outflow_volume_m3`` (water
  that left through open edges); always present, zero when unused.

Module ``twin/pluvial.py``:

- ``hyetograph(spec)`` -> ``(t_s, cum_mm)``: cumulative rain at the breakpoints of
  ``spec["rain"]["cumulative_fraction"]`` (fractions of total at equally spaced fractions of
  the storm, from 0 to 1), times ``total_mm``, over ``duration_min``. ``rain_rate(t_s, spec)``
  -> m/s, piecewise constant between breakpoints (0 outside the storm).
- ``runoff_mm(p_mm, cn)``: NRCS curve number, ``S = 25400/CN - 254`` (mm), ``Ia = 0.2 S``,
  ``Q = (P - Ia)^2 / (P - Ia + S)`` for ``P > Ia`` else 0.
- ``excess_rate(t_s, spec, cn)`` -> m/s: ``[Q(P(t + dt)) - Q(P(t))] / dt`` evaluated piecewise
  per hyetograph interval (so its integral over the storm equals ``Q(total)`` exactly).
- ``run_pluvial(ground, walls, cell_m, spec, snapshot_every_s=None)`` -> dict with ``h_max``,
  ``h``, ``rain_volume_m3``, ``sink_volume_m3``, ``outflow_volume_m3``, ``max_froude`` and
  optional ``snapshots``. Rain excess for ``spec["rain"]["cn_paved"]`` falls on every cell;
  rain on wall cells is moved onto open cells by scaling the open-cell rate by
  ``n_cells / n_open`` (roof runoff to the streets, spread uniformly — a simplification);
  ``hydraulics.run(blocked=walls, infiltration=drain, open_edges=spec["rain"]["edge_slope"])``
  with ``drain = spec["rain"]["sewer_capacity_mm_h"] / 3.6e6 * n_cells / n_open`` (m/s) on open
  cells — sewer capacity per unit catchment area, roofs included (changed in review: per
  open area it shrank the total sewer capacity whenever buildings were added); Manning n
  and alpha from ``spec["hydraulic"]``; runs ``duration_min + recession_min``.
"""

from __future__ import annotations

import copy
import itertools
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402
from climaterisk_worker.twin import hydraulics  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

pluvial = pytest.importorskip("climaterisk_worker.twin.pluvial")


def test_hyetograph_integrates_to_the_total() -> None:
    spec = load_spec()
    r = spec["rain"]
    t, cum = pluvial.hyetograph(spec)
    frac = np.asarray(r["cumulative_fraction"], dtype=float)
    assert frac[0] == 0.0 and frac[-1] == 1.0 and np.all(np.diff(frac) >= 0)
    np.testing.assert_allclose(t, np.linspace(0, r["duration_min"] * 60.0, frac.size))
    np.testing.assert_allclose(cum, frac * r["total_mm"])
    fine = np.linspace(0.0, r["duration_min"] * 60.0 + 600.0, 200_001)
    total = np.trapezoid([pluvial.rain_rate(x, spec) for x in fine], fine) * 1000.0
    np.testing.assert_allclose(total, r["total_mm"], rtol=2e-3)
    assert pluvial.rain_rate(-1.0, spec) == 0.0
    assert pluvial.rain_rate(r["duration_min"] * 60.0 + 1.0, spec) == 0.0


def test_nrcs_curve_number() -> None:
    s = 25400.0 / 90.0 - 254.0
    ia = 0.2 * s
    p = np.array([0.0, ia, ia + 10.0, 150.0])
    want = np.where(p > ia, (p - ia) ** 2 / np.maximum(p - ia + s, 1e-12), 0.0)
    np.testing.assert_allclose(pluvial.runoff_mm(p, 90.0), want, rtol=1e-12, atol=0)
    assert pluvial.runoff_mm(100.0, 100.0) == pytest.approx(100.0)  # fully impervious


def test_excess_rate_integrates_to_the_runoff() -> None:
    spec = load_spec()
    r = spec["rain"]
    t_end = r["duration_min"] * 60.0
    _, cum = pluvial.hyetograph(spec)
    edges = np.linspace(0.0, t_end, cum.size)
    total = (
        sum(
            pluvial.excess_rate(a, spec, r["cn_paved"]) * (b - a)
            for a, b in itertools.pairwise(edges)
        )
        * 1000.0
    )
    np.testing.assert_allclose(total, pluvial.runoff_mm(r["total_mm"], r["cn_paved"]), rtol=1e-9)


def test_mass_balance_with_rain_sink_and_open_edges() -> None:
    rng = np.random.default_rng(7)
    ny, nx, dx = 25, 30, 10.0
    bed = rng.normal(0.0, 0.2, (ny, nx)) + 0.01 * np.arange(ny)[:, None] * dx
    walls = np.zeros((ny, nx), dtype=bool)
    walls[8:14, 10:16] = True
    res = hydraulics.run(
        np.zeros((ny, nx)),
        bed,
        dx,
        dx,
        0.03,
        3600.0,
        rain_fn=lambda t: 80e-3 / 3600.0 if t < 1800.0 else 0.0,
        infiltration=np.full((ny, nx), 20e-3 / 3600.0),
        open_edges=0.01,
        blocked=walls,
    )
    stored = float(res["h"].sum() * dx * dx)
    balance = res["rain_volume_m3"] - res["sink_volume_m3"] - res["outflow_volume_m3"] - stored
    assert res["rain_volume_m3"] == pytest.approx(80e-3 / 2.0 * dx * dx * (ny * nx - 36), rel=1e-3)
    assert res["outflow_volume_m3"] > 0 and res["sink_volume_m3"] > 0
    assert abs(balance) < 1e-9 * res["rain_volume_m3"]
    assert np.all(res["h"][walls] == 0.0)


def test_open_edges_reach_steady_state_on_a_tilted_plane() -> None:
    """Uniform rain on a plane draining over its edges: outflow -> rain once steady."""
    ny, nx, dx = 6, 40, 5.0
    bed = np.tile(0.005 * dx * (nx - np.arange(nx)), (ny, 1))
    rate = 50e-3 / 3600.0
    t1, t2 = 4 * 3600.0, 5 * 3600.0
    a = hydraulics.run(
        np.zeros((ny, nx)), bed, dx, dx, 0.03, t1, rain_fn=lambda t: rate, open_edges=0.005
    )
    b = hydraulics.run(
        np.zeros((ny, nx)), bed, dx, dx, 0.03, t2, rain_fn=lambda t: rate, open_edges=0.005
    )
    out_rate = (b["outflow_volume_m3"] - a["outflow_volume_m3"]) / (t2 - t1)
    np.testing.assert_allclose(out_rate, rate * ny * nx * dx * dx, rtol=0.02)


def _toy_site(spec: dict) -> tuple[np.ndarray, np.ndarray]:
    ny, nx = 30, 40
    ground = 20.0 - 0.02 * np.arange(nx)[None, :] * 10.0 + np.zeros((ny, 1))
    ground[10:20, 15:25] -= 1.5  # a hollow in the middle of the street grid
    walls = np.zeros((ny, nx), dtype=bool)
    for i0 in range(2, ny - 4, 8):
        for j0 in range(2, nx - 4, 8):
            walls[i0 : i0 + 4, j0 : j0 + 4] = True
    walls[10:20, 15:25] = False  # keep the hollow open
    return ground, walls


@pytest.fixture()
def fast_spec() -> dict:
    spec = copy.deepcopy(load_spec())
    spec["rain"]["duration_min"] = 30.0
    spec["rain"]["recession_min"] = 15.0
    return spec


def test_roof_rain_reaches_the_streets(fast_spec: dict) -> None:
    ground, walls = _toy_site(fast_spec)
    no_sink = copy.deepcopy(fast_spec)
    no_sink["rain"]["sewer_capacity_mm_h"] = 0.0
    res = pluvial.run_pluvial(ground, walls, 10.0, no_sink)
    q_mm = pluvial.runoff_mm(no_sink["rain"]["total_mm"], no_sink["rain"]["cn_paved"])
    np.testing.assert_allclose(
        res["rain_volume_m3"], q_mm / 1000.0 * ground.size * 100.0, rtol=1e-3
    )
    assert np.all(res["h_max"][walls] == 0.0)


def test_buildings_change_where_the_water_goes_not_how_much(fast_spec: dict) -> None:
    """Same rain and the same total sewer capacity with or without buildings; the walls only
    change where the water goes. Changed in review: the first version asserted that buildings
    make the water deeper, which held only because it had also shrunk the sewer; with a fair
    sewer the toy site's street depths came out slightly LOWER with buildings (0.050 vs
    0.053 m). Which way buildings push the depths is a result to look at, not a law to gate.
    """
    ground, walls = _toy_site(fast_spec)
    with_walls = pluvial.run_pluvial(ground, walls, 10.0, fast_spec)
    without = pluvial.run_pluvial(ground, np.zeros_like(walls), 10.0, fast_spec)
    np.testing.assert_allclose(with_walls["rain_volume_m3"], without["rain_volume_m3"], rtol=1e-3)
    assert np.all(with_walls["h_max"][walls] == 0.0)
    assert np.abs(with_walls["h_max"][~walls] - without["h_max"][~walls]).max() > 0.01


def test_more_sewer_capacity_floods_less(fast_spec: dict) -> None:
    ground, walls = _toy_site(fast_spec)
    big = copy.deepcopy(fast_spec)
    big["rain"]["sewer_capacity_mm_h"] = 3.0 * fast_spec["rain"]["sewer_capacity_mm_h"]
    a = pluvial.run_pluvial(ground, walls, 10.0, fast_spec)
    b = pluvial.run_pluvial(ground, walls, 10.0, big)
    assert b["h_max"].sum() < a["h_max"].sum()


def test_gangnam_rain_gathers_in_the_seocho_basin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real site, terrain routing only (skipped when the OSM / DEM files are not here).

    Changed in review: the sewer is switched off so the check isolates what it is about —
    the terrain sending water west into the Seocho basin. With the uniform 95 mm/h sink the
    two means were 0.067 vs 0.068 m (the sink, not the terrain, sets the residual). Reads
    the real data root (read-only), which tests/conftest.py otherwise hides.
    """
    pytest.importorskip("pyogrio")
    from climaterisk_worker.twin import site

    from climaterisk import paths

    monkeypatch.setenv("CLIMATERISK_DATA_ROOT", str(paths.default_data_root()))
    paths.clear_cache()
    spec = load_spec()
    spec["rain"]["sewer_capacity_mm_h"] = 0.0
    cfg = spec["site"]
    if not (
        paths.osm_dir(cfg["osm_file"]).exists()
        and paths.external_dir("copernicus_dem", cfg["dem_file"]).exists()
    ):
        pytest.skip("public site data not on this machine")
    s = site.build_site(spec)
    res = pluvial.run_pluvial(s["ground"], s["mask"], float(cfg["cell_m"]), spec)
    _, lon = s["grid"].centres()
    west = (lon < 127.0255) & ~s["mask"]  # Seocho basin, west of Gangnam-daero
    east = (lon > 127.0300) & ~s["mask"]  # Yeoksam slope
    assert res["h_max"][west].mean() > res["h_max"][east].mean()
    assert res["max_froude"] >= 0.0
