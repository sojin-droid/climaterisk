"""Step R5 gate (written by the reviewer before the step; agents may not edit it).

Effect of the no-damage threshold on the loss of the real Gangnam buildings, using ONLY
buildings whose use is known from OSM (no assumed use) and ONLY the ground storey (basement
storeys are assumptions). Module ``worker/climaterisk_worker/twin/threshold_compare.py``;
every number from ``spec["threshold_compare"]``:

- ``known_use(buildings, spec)`` -> list (one per building) of the JRC property type
  (``"residential"`` / ``"commercial"`` / ``"industrial"``) when the row is a wall
  (``is_wall``, not ``underground``) whose OSM ``building`` value is listed under that type
  in ``spec["threshold_compare"]["known_uses"]``; ``None`` otherwise (``"yes"``, schools,
  churches, unlisted values, canopies, underground features). Never falls back to a default.
- ``pure_jrc(func)`` -> a copy of a CLIMADA ``ImpfRiverFlood`` without the zero-damage
  points CLIMADA inserts between 0 m and the first damaging depth (JRC Asia: the 0.05 m
  point), i.e. the published JRC table, linear from (0, 0) to the first damage.
- ``price_thresholds(buildings, depth_m, value_usd, rps, thresholds, spec, curve)`` ->
  dict keyed by each entry of ``thresholds`` (``None`` = no threshold, else T in m) holding
  ``eal_usd`` (n,) and ``event_loss_usd`` (K, n). ``depth_m`` is (K, n) as in R3b
  ``rain_eal.price_ladder``. Priced through ``engine.calculate`` (one RF hazard, K events,
  ``rain_eal.layered_frequencies(rps)``); a threshold is passed as the engine's own facility
  input ``flood_zero_damage_threshold_m``. ``curve`` is ``"climada"`` (the engine's curve)
  or ``"pure"`` (``pure_jrc`` of it). With ``curve="climada"`` and ``None`` the result equals
  ``rain_eal.price_ladder``.

Algorithm under test:
    EAL_i(T) = sum_k f_k V_i MDR_c(d_ki) [d_ki > T]
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
pytest.importorskip("climada")

import numpy as np  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

tc = pytest.importorskip("climaterisk_worker.twin.threshold_compare")

M = 111320.0
LON_M = M * math.cos(math.radians(37.0))


def _square(x0: float, y0: float, s: float):
    from shapely.geometry import box

    return box(127.0 + x0 / LON_M, 37.0 + y0 / M, 127.0 + (x0 + s) / LON_M, 37.0 + (y0 + s) / M)


def _buildings():
    import geopandas as gpd

    return gpd.GeoDataFrame(
        {
            "building": ["apartments", "office", "yes", "school", "warehouse", "commercial"],
            "height_m": [30.0, 45.0, 9.9, 12.0, 6.6, 3.3],
            "height_source": ["height"] * 6,
            "underground": [False, False, False, False, False, True],
            "is_wall": [True, True, True, True, True, False],
        },
        geometry=[_square(60 * i, 0, 20) for i in range(6)],
        crs="EPSG:4326",
    )


def test_known_use_never_assumes() -> None:
    spec = load_spec()
    uses = spec["threshold_compare"]["known_uses"]
    assert all("yes" not in v for v in uses.values())
    assert tc.known_use(_buildings(), spec) == [
        "residential",
        "commercial",
        None,
        None,
        "industrial",
        None,
    ]


def test_pure_jrc_is_the_published_table() -> None:
    from climaterisk_worker.physical_risk import registry

    f = registry.flood_impact_function("Asia", "commercial")
    p = tc.pure_jrc(f)
    assert 0.05 in list(f.intensity) and 0.05 not in list(p.intensity)
    m05 = float(f.calc_mdr(np.array([0.5]))[0])
    np.testing.assert_allclose(p.calc_mdr(np.array([0.02, 0.1])), [m05 * 0.04, m05 * 0.2])
    deep = np.array([0.5, 1.0, 2.0, 6.0])
    np.testing.assert_allclose(p.calc_mdr(deep), f.calc_mdr(deep), rtol=1e-12)


def test_thresholds_priced_through_the_engine() -> None:
    from climaterisk_worker.physical_risk import registry
    from climaterisk_worker.twin import rain_eal, site_loss

    from climaterisk.physical_risk.metrics import jrc_sector_for

    spec = load_spec()
    b = _buildings().iloc[[0, 1, 4]].reset_index(drop=True)
    rps = [5, 30, 200]
    depth = np.array([[0.03, 0.1, 0.0], [0.12, 0.25, 0.4], [0.45, 0.8, 1.2]])
    value = np.array([1e6, 2e6, 5e5])
    f = rain_eal.layered_frequencies(rps)
    for curve in ("climada", "pure"):
        out = tc.price_thresholds(b, depth, value, rps, [None, 0.05, 0.15, 0.5], spec, curve)
        assert set(out) == {None, 0.05, 0.15, 0.5}
        for t, res in out.items():
            assert res["event_loss_usd"].shape == (3, 3)
            for i in range(3):
                kind = site_loss.property_type(b["building"].iloc[i], spec)
                fn = registry.flood_impact_function("Asia", jrc_sector_for(kind))
                if curve == "pure":
                    fn = tc.pure_jrc(fn)
                mdr = fn.calc_mdr(depth[:, i])
                if t is not None:
                    mdr = np.where(depth[:, i] > t, mdr, 0.0)
                expect = float(np.sum(f * value[i] * mdr))
                assert res["eal_usd"][i] == pytest.approx(expect, rel=1e-6, abs=1e-6)
        # thresholds only ever remove loss
        assert np.all(out[0.5]["eal_usd"] <= out[0.15]["eal_usd"] + 1e-9)
        assert np.all(out[0.15]["eal_usd"] <= out[None]["eal_usd"] + 1e-9)
    base = rain_eal.price_ladder(b, depth, value, rps, spec)
    climada = tc.price_thresholds(b, depth, value, rps, [None], spec, "climada")[None]
    np.testing.assert_allclose(climada["eal_usd"], base["eal_usd"], rtol=1e-9)
    # on the CLIMADA curve the 5 cm default changes nothing; on the pure table it removes
    # exactly the 3 cm storm of building 0
    c = tc.price_thresholds(b, depth, value, rps, [None, 0.05], spec, "climada")
    np.testing.assert_allclose(c[0.05]["eal_usd"], c[None]["eal_usd"], rtol=1e-12)
    p = tc.price_thresholds(b, depth, value, rps, [None, 0.05], spec, "pure")
    assert p[0.05]["eal_usd"][0] < p[None]["eal_usd"][0]
    np.testing.assert_allclose(p[0.05]["eal_usd"][1:], p[None]["eal_usd"][1:], rtol=1e-9)


def test_spec_section() -> None:
    t = load_spec()["threshold_compare"]
    assert t["thresholds_m"] == sorted(t["thresholds_m"]) and t["thresholds_m"][0] == 0.05
    assert set(t["known_uses"]) <= {"residential", "commercial", "industrial"}
