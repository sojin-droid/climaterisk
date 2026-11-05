"""Step 7 gate (written by the reviewer before the step; agents may not edit it).

Typhoon: synthetic tracks -> CLIMADA ``TropCyclone`` wind field -> repo engine (TC).
The module under test is ``worker/climaterisk_worker/twin/typhoon.py`` and must provide:

- ``make_track(lat0, lon0, *, heading_deg, speed_kmh, vmax_ms, pmin_hpa, rmax_km, hours,
  name)`` -> one hourly track as an ``xarray.Dataset`` in CLIMADA ``TCTracks`` format
  (heading 0 = due north; constant intensity along the track);
- ``synthetic_tracks(spec)`` -> ``(TCTracks, frequency)``: ``spec["typhoon"]["n_tracks"]``
  seeded tracks, ``frequency`` (1/year) per track summing to
  ``spec["typhoon"]["annual_rate_per_year"]``, each track's ``max_sustained_wind`` within
  ``spec["typhoon"]["vmax_ms"]`` ([min, max], m/s);
- ``wind_hazard(lat, lon, tracks, frequency)`` -> CLIMADA ``Hazard`` (``TC``, m/s) on those
  points, computed without any download (``ignore_distance_to_coast=True``);
- ``build_tc_hazard(assets, spec)`` -> the same on the city's asset points;
- ``price_tc(assets, hazard)`` -> one engine ``ResultRow`` per asset from
  ``engine.calculate(..., "TC", iso3="KOR")`` (Korea -> CLIMADA region WP4, impf 9).

The physical checks below were confirmed on CLIMADA 6.1 before this file was written.
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("networkx")

import numpy as np  # noqa: E402
from climada.hazard import TCTracks  # noqa: E402
from climaterisk_worker.twin.city import build_city  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

typhoon = pytest.importorskip("climaterisk_worker.twin.typhoon")

KM_PER_DEG = 111.32
VMAX, RMAX_KM, SPEED_KMH = 45.0, 30.0, 20.0


def _profile(offsets_km: np.ndarray) -> np.ndarray:
    """Peak wind (m/s) at points ``offsets_km`` east of a northbound track at 37 N."""
    track = typhoon.make_track(
        35.0,
        127.0,
        heading_deg=0.0,
        speed_kmh=SPEED_KMH,
        vmax_ms=VMAX,
        pmin_hpa=950.0,
        rmax_km=RMAX_KM,
        hours=48,
        name="gate",
    )
    lat = np.full(offsets_km.size, 37.0)
    lon = 127.0 + offsets_km / (KM_PER_DEG * math.cos(math.radians(37.0)))
    hazard = typhoon.wind_hazard(lat, lon, TCTracks(data=[track]), np.array([1.0]))
    assert hazard.haz_type == "TC"
    return hazard.intensity.toarray()[0]


def test_right_of_track_is_windier_in_the_northern_hemisphere() -> None:
    """Translation adds to the cyclonic wind on the right of the motion."""
    w = _profile(np.array([-60.0, 60.0]))
    assert w[1] > w[0] + 2.0, w


def test_wind_decays_outside_the_radius_of_maximum_wind() -> None:
    w = _profile(np.array([60.0, 90.0, 120.0, 150.0, 300.0]))
    assert np.all(np.diff(w) < 0), w


def test_peak_wind_is_bounded_by_vmax_plus_translation() -> None:
    w = _profile(np.linspace(-150.0, 150.0, 31))
    assert w.max() <= VMAX + SPEED_KMH / 3.6 + 1.0, w.max()
    assert w.max() >= 0.8 * VMAX, w.max()


def test_far_points_feel_nothing() -> None:
    assert np.all(_profile(np.array([1500.0, -1500.0])) == 0.0)


def test_synthetic_tracks_follow_the_spec() -> None:
    spec = load_spec()
    ty = spec["typhoon"]
    tracks, freq = typhoon.synthetic_tracks(spec)
    again, freq2 = typhoon.synthetic_tracks(spec)
    assert tracks.size == ty["n_tracks"]
    np.testing.assert_allclose(freq.sum(), ty["annual_rate_per_year"], rtol=1e-12)
    np.testing.assert_array_equal(freq, freq2)
    lo, hi = ty["vmax_ms"]
    for a, b in zip(tracks.data, again.data, strict=True):
        np.testing.assert_array_equal(a.lat.values, b.lat.values)
        v = float(a.max_sustained_wind.max())
        unit = a.attrs["max_sustained_wind_unit"]
        v_ms = v / 1.943844 if unit == "kn" else v
        assert lo - 1e-6 <= v_ms <= hi + 1e-6, (v_ms, unit)


def _hand_eal(assets: list[dict], hazard: object) -> np.ndarray:
    """EAL_k = sum_i f_i v_k MDR_WP4(w_ik), MDR by linear interpolation of impf 9."""
    from climaterisk_worker.physical_risk import registry

    func = registry.tc_impact_function(9)
    wind = hazard.intensity.toarray()  # type: ignore[attr-defined]
    mdr = np.interp(wind, func.intensity, func.mdd * func.paa)
    value = np.array([a["asset_value_usd"] for a in assets])
    return hazard.frequency @ (mdr * value)  # type: ignore[attr-defined]


def test_engine_tc_eal_equals_hand_integral() -> None:
    _, assets = build_city(load_spec())
    spec = load_spec()
    hazard = typhoon.build_tc_hazard(assets, spec)
    assert hazard.intensity.shape == (spec["typhoon"]["n_tracks"], len(assets))
    rows = typhoon.price_tc(assets, hazard)
    got = np.array([r.eal_usd or 0.0 for r in rows])
    np.testing.assert_allclose(got, _hand_eal(assets, hazard), rtol=1e-9, atol=1e-6)
    assert got.sum() > 0.0


def test_stronger_storms_cost_more() -> None:
    spec = load_spec()
    _, assets = build_city(spec)
    strong = copy.deepcopy(spec)
    strong["typhoon"]["vmax_ms"] = [1.2 * v for v in spec["typhoon"]["vmax_ms"]]
    base = _hand_eal(assets, typhoon.build_tc_hazard(assets, spec)).sum()
    more = _hand_eal(assets, typhoon.build_tc_hazard(assets, strong)).sum()
    assert more > base > 0.0
