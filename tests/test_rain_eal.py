"""Step R3b gate (written by the reviewer before the step; agents may not edit it).

Expected annual loss of the real site over a return-period ladder of observed-statistics
storms. Two modules; every number comes from ``spec["rain_frequency"]`` (and the sections
the earlier steps use):

``worker/climaterisk_worker/twin/rain_frequency.py`` (no I/O):

- ``annual_maxima(rain_mm, window_h)``: ``rain_mm`` is a ``pandas.Series`` of hourly depths
  (mm) on a ``DatetimeIndex``; hours absent from the index or NaN count as 0 (reindex to the
  full hourly range first). Rolling sum over ``window_h`` consecutive hours, assigned to the
  calendar year of the window's LAST hour; returns the maximum per year as a Series indexed
  by int year.
- ``lmoments(x)`` -> ``(l1, l2, t3)``: sample L-moments from the unbiased probability-weighted
  moments (Hosking 1990): ``b_r = n^-1 sum_i C(i-1, r) / C(n-1, r) x_(i)``,
  ``l1 = b0``, ``l2 = 2 b1 - b0``, ``l3 = 6 b2 - 6 b1 + b0``, ``t3 = l3 / l2``.
- ``fit_gumbel(x)`` -> ``(u, alpha)``: ``alpha = l2 / ln 2``, ``u = l1 - 0.5772156649 alpha``.
- ``fit_gev(x)`` -> ``(xi, alpha, k)``: Hosking, Wallis & Wood (1985) in Hosking's sign
  convention (``k > 0`` bounded upper tail): ``c = 2/(3+t3) - ln2/ln3``,
  ``k = 7.8590 c + 2.9554 c^2``, ``alpha = l2 k / ((1 - 2^-k) Gamma(1+k))``,
  ``xi = l1 - alpha (1 - Gamma(1+k)) / k``.
- ``quantile(dist, params, T)`` (``dist`` ``"gumbel"`` / ``"gev"``, ``T`` years, scalar or
  array): non-exceedance ``F = 1 - 1/T``; Gumbel ``u - alpha ln(-ln F)``; GEV
  ``xi + alpha / k (1 - (-ln F)^k)``.
- ``bootstrap_quantiles(x, T, dist, n, seed, q)`` -> array ``(len(q), len(T))``: resample
  ``x`` with replacement ``n`` times (``numpy.random.default_rng(seed)``), refit, take the
  ``q`` quantiles of the return levels.

``worker/climaterisk_worker/twin/rain_eal.py``:

- ``layered_frequencies(rps)`` -> float array: for ascending return periods
  ``T_1 < ... < T_K`` the annual frequency of each layer, ``f_k = 1/T_k - 1/T_{k+1}`` and
  ``f_K = 1/T_K`` (so ``sum f = 1/T_1``; CLIMADA's step convention: storms rarer than
  ``T_k`` but more frequent than ``T_{k+1}`` count as the ``T_k`` storm, storms more frequent
  than ``T_1`` are taken to cause no loss).
- ``spec_for_rain(spec, total_mm)`` -> a deep copy of ``spec`` with ``rain.total_mm``
  replaced; ``spec`` itself is left unchanged.
- ``price_ladder(buildings, depth_m, value_usd, rps, spec)``: ``depth_m`` is ``(K, n)``
  (row k = the entry depth of every building in the ``T_k`` storm). ONE river-flood (RF)
  hazard with K events of ``layered_frequencies(rps)``, centroids at each building's
  ``representative_point()``, priced per building with ``engine.calculate(...)`` like
  ``site_loss.price_buildings``. Returns ``rows``, ``eal_usd`` (n, each row's ``eal_usd``;
  None counts as 0), ``event_loss_usd`` (K, n) — each row equal to
  ``site_loss.price_buildings`` of that storm's depths, rtol 1e-9 — and
  ``portfolio_eal_usd`` (ImpactCalc ``aai_agg``; equals ``eal_usd.sum()``).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("geopandas")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

rf = pytest.importorskip("climaterisk_worker.twin.rain_frequency")
real = pytest.importorskip("climaterisk_worker.twin.rain_eal")

M = 111320.0
LON_M = M * math.cos(math.radians(37.0))


def test_annual_maxima_window_gaps_and_year_of_last_hour() -> None:
    idx = pd.to_datetime(
        [
            "2020-06-01 10:00",
            "2020-06-01 11:00",
            "2020-06-01 13:00",
            "2020-12-31 23:00",
            "2021-01-01 00:00",
            "2021-07-01 05:00",
        ]
    )
    s = pd.Series([10.0, 20.0, 50.0, 30.0, 40.0, np.nan], index=idx)
    am = rf.annual_maxima(s, 2)
    assert list(am.index) == [2020, 2021]
    # 2020: 10+20 = 30 and 0+50 (12:00 is missing -> 0) = 50
    assert am.loc[2020] == pytest.approx(50.0)
    # 2020-12-31 23:00 + 2021-01-01 00:00 = 70, the window ends in 2021
    assert am.loc[2021] == pytest.approx(70.0)
    assert rf.annual_maxima(s, 1).loc[2020] == pytest.approx(50.0)


def test_lmoments_of_a_known_sample() -> None:
    n = 50
    x = np.arange(1.0, n + 1)
    l1, l2, t3 = rf.lmoments(x[::-1])  # order of input must not matter
    assert l1 == pytest.approx((n + 1) / 2)
    assert l2 == pytest.approx((n + 1) / 6)
    assert t3 == pytest.approx(0.0, abs=1e-12)


def test_gumbel_and_gev_recover_their_parameters() -> None:
    rng = np.random.default_rng(11)
    p = rng.uniform(size=40_000)
    u, a = 50.0, 15.0
    u_hat, a_hat = rf.fit_gumbel(u - a * np.log(-np.log(p)))
    assert u_hat == pytest.approx(u, rel=0.01) and a_hat == pytest.approx(a, rel=0.02)
    xi, al, k = 60.0, 18.0, 0.1
    xs = xi + al / k * (1 - (-np.log(p)) ** k)
    xi_h, al_h, k_h = rf.fit_gev(xs)
    assert xi_h == pytest.approx(xi, rel=0.02)
    assert al_h == pytest.approx(al, rel=0.03)
    assert k_h == pytest.approx(k, abs=0.02)


def test_quantiles_invert_the_distribution() -> None:
    T = np.array([2.0, 10.0, 100.0, 500.0])
    F = 1 - 1 / T
    xg = rf.quantile("gumbel", (50.0, 15.0), T)
    np.testing.assert_allclose(np.exp(-np.exp(-(xg - 50.0) / 15.0)), F, rtol=1e-12)
    xi, al, k = 60.0, 18.0, 0.1
    xv = rf.quantile("gev", (xi, al, k), T)
    np.testing.assert_allclose(np.exp(-((1 - k * (xv - xi) / al) ** (1 / k))), F, rtol=1e-12)
    # a GEV with a tiny shape is the Gumbel
    np.testing.assert_allclose(
        rf.quantile("gev", (50.0, 15.0, 1e-7), T),
        rf.quantile("gumbel", (50.0, 15.0), T),
        rtol=1e-5,
    )
    assert float(rf.quantile("gumbel", (50.0, 15.0), 100.0)) == pytest.approx(xg[2])


def test_bootstrap_band_brackets_the_fit() -> None:
    rng = np.random.default_rng(5)
    x = 50.0 - 15.0 * np.log(-np.log(rng.uniform(size=66)))
    T = np.array([2.0, 10.0, 100.0])
    band = rf.bootstrap_quantiles(x, T, "gumbel", 400, 3, (0.05, 0.95))
    assert band.shape == (2, 3)
    mid = rf.quantile("gumbel", rf.fit_gumbel(x), T)
    assert np.all(band[0] < mid) and np.all(mid < band[1])
    np.testing.assert_array_equal(
        band, rf.bootstrap_quantiles(x, T, "gumbel", 400, 3, (0.05, 0.95))
    )


def test_layered_frequencies_and_spec_copy() -> None:
    np.testing.assert_allclose(real.layered_frequencies([2, 10, 100]), [0.4, 0.09, 0.01])
    assert real.layered_frequencies([5, 50]).sum() == pytest.approx(0.2)
    spec = load_spec()
    before = spec["rain"]["total_mm"]
    s2 = real.spec_for_rain(spec, 77.0)
    assert s2["rain"]["total_mm"] == 77.0 and spec["rain"]["total_mm"] == before
    rfq = spec["rain_frequency"]
    assert rfq["return_periods"] == sorted(rfq["return_periods"])
    assert rfq["distribution"] in ("gev", "gumbel") and rfq["window_h"] >= 1


def _square(x0: float, y0: float, s: float):
    from shapely.geometry import box

    return box(127.0 + x0 / LON_M, 37.0 + y0 / M, 127.0 + (x0 + s) / LON_M, 37.0 + (y0 + s) / M)


def test_ladder_is_priced_through_the_engine() -> None:
    pytest.importorskip("climada")
    import geopandas as gpd
    from climaterisk_worker.physical_risk import registry
    from climaterisk_worker.twin import site_loss

    from climaterisk.physical_risk.metrics import jrc_sector_for

    spec = load_spec()
    b = gpd.GeoDataFrame(
        {
            "building": ["office", "apartments", "warehouse"],
            "height_m": [45.0, 9.9, 6.6],
            "height_source": ["height"] * 3,
            "underground": [False, False, False],
            "is_wall": [True, True, True],
        },
        geometry=[_square(0, 0, 30), _square(60, 0, 20), _square(0, 60, 25)],
        crs="EPSG:4326",
    )
    rps = [2, 10, 50, 200]
    depth = np.array([[0.0, 0.0, 0.0], [0.2, 0.0, 0.08], [0.6, 0.3, 0.4], [1.2, 0.9, 1.5]])
    value = np.array([2e6, 5e5, 1e6])
    out = real.price_ladder(b, depth, value, rps, spec)
    f = real.layered_frequencies(rps)
    assert out["event_loss_usd"].shape == (4, 3)
    for k in range(4):
        one = site_loss.price_buildings(b, depth[k], value, spec)["loss_usd"]
        np.testing.assert_allclose(out["event_loss_usd"][k], one, rtol=1e-9, atol=1e-9)
    for i in range(3):
        kind = site_loss.property_type(b["building"].iloc[i], spec)
        fn = registry.flood_impact_function("Asia", jrc_sector_for(kind))
        expect = float(np.sum(f * value[i] * fn.calc_mdr(depth[:, i])))
        assert out["eal_usd"][i] == pytest.approx(expect, rel=1e-9)
        assert out["rows"][i].eal_usd == pytest.approx(out["eal_usd"][i], rel=1e-12)
    assert out["portfolio_eal_usd"] == pytest.approx(out["eal_usd"].sum(), rel=1e-9)


def test_seoul_series_gives_plausible_return_levels() -> None:
    from climaterisk import paths

    spec = load_spec()["rain_frequency"]
    folder = paths.kma_dir() / "asos_hourly" / str(spec["station"])
    files = sorted(folder.glob("*.csv"))
    if not files:
        pytest.skip(f"ASOS hourly files not on this machine: {folder}")
    df = pd.concat([pd.read_csv(f, usecols=["tm", "rn"]) for f in files])
    s = pd.Series(
        pd.to_numeric(df["rn"], errors="coerce").to_numpy(), index=pd.to_datetime(df["tm"])
    )
    am = rf.annual_maxima(s, spec["window_h"])
    assert am.index.min() <= 1961 and am.size >= 60
    params = (
        rf.fit_gev(am.to_numpy()) if spec["distribution"] == "gev" else rf.fit_gumbel(am.to_numpy())
    )
    x = rf.quantile(spec["distribution"], params, np.array(spec["return_periods"], dtype=float))
    assert np.all(np.diff(x) > 0)
    x100 = float(rf.quantile(spec["distribution"], params, 100.0))
    assert 100.0 < x100 < 200.0  # mm in the window; Seoul 2 h, sanity bounds only
