"""Agricultural drought gate (written by the reviewer before the step; agents may not edit it).

Module ``worker/climaterisk_worker/drought_agri.py`` (worker env). Method:
``docs/DROUGHT_AGRI_KO.md``. Functions under test:

- ``accumulate(x, scale)`` -> rolling sum over ``scale`` months along axis 0 (time first);
  the first ``scale - 1`` steps are NaN.
- ``spei(d, months, scale, ref_mask)`` -> SPEI (Vicente-Serrano et al. 2010): ``d`` = monthly
  climatic water balance P - PET (mm, shape (T,) or (T, N)), ``months`` (T,) calendar month
  1..12 of each step, ``ref_mask`` (T,) bool selecting the reference period. For each
  calendar month separately: accumulate over ``scale`` months, fit a three-parameter
  log-logistic distribution by unbiased probability-weighted moments on the reference
  steps only, transform through its CDF to the standard normal quantile. Values are
  clipped to [-3, 3]; NaN where the accumulation is NaN.
- ``stage(s)`` -> int array: 0 if s > -1, 1 if -1.5 < s <= -1 (관심), 2 if -2 < s <= -1.5
  (주의), 3 if s <= -2 (경계 이상); NaN -> -1.
- ``farm_area_by_piece(parcels, pieces)`` -> DataFrame indexed by piece ``id`` with
  columns ``rice, upland, orchard, greenhouse`` (m^2): parcels (GeoDataFrame, any CRS,
  columns ``CLSF_CD`` and ``AREA`` m^2) are assigned to the piece containing their
  representative point; ``CLSF_CD`` 01 rice (논), 02 upland (밭), 03 orchard (과수),
  04 greenhouse (시설); 06 (비경지) and unknown codes are excluded. ``AREA`` is summed, not
  recomputed. Parcels outside every piece are dropped. Pieces with no farmland get 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("geopandas")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

da = pytest.importorskip("climaterisk_worker.drought_agri")


def test_accumulate() -> None:
    x = np.arange(1.0, 7.0)
    out = da.accumulate(x, 3)
    assert np.isnan(out[:2]).all()
    np.testing.assert_allclose(out[2:], [6.0, 9.0, 12.0, 15.0])
    two = da.accumulate(np.column_stack([x, 2 * x]), 2)
    np.testing.assert_allclose(two[1:, 1], 2 * (x[1:] + x[:-1]))


def _loglogistic(rng: np.random.Generator, n: int, alpha: float, beta: float, gamma: float):
    u = rng.uniform(1e-6, 1 - 1e-6, n)
    return gamma + alpha * (u / (1 - u)) ** (1 / beta)


def test_spei_is_standard_normal_on_loglogistic_data() -> None:
    rng = np.random.default_rng(20261110)
    years = 600
    months = np.tile(np.arange(1, 13), years)
    # scale 1: each calendar month's d is drawn from its own log-logistic
    d = np.empty(months.size)
    for m in range(1, 13):
        sel = months == m
        d[sel] = _loglogistic(rng, int(sel.sum()), alpha=30.0 + m, beta=4.0, gamma=-100.0 + 5 * m)
    s = da.spei(d, months, 1, np.ones(months.size, bool))
    assert s.shape == d.shape
    for m in (1, 7):
        sm = s[months == m]
        assert abs(float(np.mean(sm))) < 0.1
        assert abs(float(np.std(sm)) - 1.0) < 0.1
    # monotone in d within a calendar month
    sel = months == 4
    order = np.argsort(d[sel])
    assert np.all(np.diff(s[sel][order]) >= -1e-9)
    assert float(np.max(np.abs(s))) <= 3.0


def test_spei_reference_period_and_shape() -> None:
    rng = np.random.default_rng(7)
    months = np.tile(np.arange(1, 13), 60)
    base = rng.normal(0.0, 30.0, (months.size, 3))
    ref = np.zeros(months.size, bool)
    ref[: 30 * 12] = True
    s = da.spei(base, months, 3, ref)
    assert s.shape == base.shape
    assert np.isnan(s[:2]).all()
    # a uniformly wetter second half -> higher SPEI than the reference half on average
    wet = base.copy()
    wet[30 * 12 :] += 40.0
    sw = da.spei(wet, months, 3, ref)
    assert np.nanmean(sw[30 * 12 :]) > np.nanmean(sw[2 : 30 * 12]) + 0.5


def test_stage() -> None:
    s = np.array([0.5, -1.0, -1.2, -1.5, -1.9, -2.0, -2.8, np.nan])
    np.testing.assert_array_equal(da.stage(s), [0, 1, 1, 2, 2, 3, 3, -1])


def test_farm_area_by_piece() -> None:
    import geopandas as gpd
    from shapely.geometry import box

    pieces = gpd.GeoDataFrame(
        {"id": [10, 11, 12]},
        geometry=[
            box(127.0, 37.0, 127.1, 37.1),
            box(127.1, 37.0, 127.2, 37.1),
            box(127.2, 37.0, 127.3, 37.1),
        ],
        crs="EPSG:4326",
    )
    parcels = gpd.GeoDataFrame(
        {
            "CLSF_CD": ["01", "02", "03", "04", "06", "01", "99", "02"],
            "AREA": [100.0, 200.0, 300.0, 400.0, 500.0, 50.0, 70.0, 900.0],
        },
        geometry=[
            box(127.01, 37.01, 127.02, 37.02),
            box(127.03, 37.01, 127.04, 37.02),
            box(127.05, 37.01, 127.06, 37.02),
            box(127.07, 37.01, 127.08, 37.02),
            box(127.08, 37.01, 127.09, 37.02),  # 비경지: excluded
            box(127.11, 37.01, 127.12, 37.02),
            box(127.13, 37.01, 127.14, 37.02),  # unknown code: excluded
            box(130.0, 40.0, 130.01, 40.01),  # outside every piece: dropped
        ],
        crs="EPSG:4326",
    ).to_crs(5186)
    out = da.farm_area_by_piece(parcels, pieces)
    assert list(out.columns) == ["rice", "upland", "orchard", "greenhouse"]
    assert out.loc[10].tolist() == [100.0, 200.0, 300.0, 400.0]
    assert out.loc[11].tolist() == [50.0, 0.0, 0.0, 0.0]
    assert out.loc[12].tolist() == [0.0, 0.0, 0.0, 0.0]
