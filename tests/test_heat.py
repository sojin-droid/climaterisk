"""Step 8 gate (written by the reviewer before the step; agents may not edit it).

Heatwave: street-canyon heat on the synthetic city -> the repo's heat-productivity curve.
No UTCI library is installed, so the heat index is a documented **repo-specific proxy**
built from street-canyon geometry, not UTCI. Module under test:
``worker/climaterisk_worker/twin/heat.py``. Required API and formulas:

- ``sky_view_factor(h_over_w)`` — symmetric infinite canyon, centre of the floor (Oke 1987):
  ``SVF = cos(arctan(2 H/W))``.
- ``sunlit_fraction(h_over_w, solar_elevation_deg)`` — sun perpendicular to the canyon:
  ``f = clip(1 - (H/W) / tan(beta), 0, 1)``.
- ``mean_radiant_temperature(t_air_c, h_over_w, solar_elevation_deg, spec)`` —
  ``MRT = T + a_sun f + a_sky SVF`` with ``a_sun = spec["heat"]["mrt_sun_excess_c"]``,
  ``a_sky = spec["heat"]["mrt_sky_excess_c"]`` (degC).
- ``feels_like(t_air_c, mrt_c, spec)`` — ``T_feel = T + w (MRT - T)``,
  ``w = spec["heat"]["mrt_weight"]``.
- ``heat_events(spec)`` -> ``(return_periods, frequency, t_air_peak_c)``, ascending return
  periods ``spec["heat"]["return_periods"]``, layered frequency ``1/T_i - 1/T_{i+1}`` (the
  rarest keeps ``1/T_n``), and ``T_peak(T) = t2_c + slope_c ln(T/2)``
  (``spec["heat"]["t_air_2y_c"]``, ``spec["heat"]["t_air_slope_c"]``).
- ``street_canyons(assets, spec)`` -> ``H/W`` per asset: a lot uses its own building's
  height from ``viewer3d.generate_buildings``; a road uses the mean height of its two end
  lots; ``W = spec["heat"]["street_width_m"]``.
- ``build_heat_hazard(assets, spec)`` -> CLIMADA ``Hazard`` (``haz_type="HW"``, ``units="degC"``)
  whose intensity is ``T_feel`` per event and asset, with the local air temperature
  ``T = T_peak + spec["heat"]["uhi_c"][kind]`` and ``beta = spec["heat"]["solar_elevation_deg"]``.
- ``productivity_impact_function(max_mdd)`` -> a CLIMADA ``ImpactFunc`` (``HW``) built
  from the repo's heat-productivity curve ``physical._CATALOG_PERILS["heatwave"]``
  (intensity points and ramp), ``mdd = ramp * max_mdd``, ``paa = 1`` — the same
  construction as ``physical._run_catalog_peril``.
- ``price_productivity(assets, hazard, spec)`` -> list of dicts, one per asset, with
  ``exposure_usd = workers[kind] * daily_wage_usd * heatwave_days`` and ``eal_usd`` from CLIMADA
  ``ImpactCalc`` using ``productivity_impact_function(spec["heat"]["max_mdd"][kind])``.

Heat is hazard-only in damage terms: nothing here touches the asset replacement value.
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
from climaterisk_worker.twin.city import build_city  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

heat = pytest.importorskip("climaterisk_worker.twin.heat")


def test_sky_view_factor_of_a_canyon() -> None:
    hw = np.array([0.0, 0.5, 1.0, 2.0])
    np.testing.assert_allclose(
        heat.sky_view_factor(hw), np.cos(np.arctan(2.0 * hw)), rtol=1e-12, atol=0
    )
    np.testing.assert_allclose(heat.sky_view_factor(np.array([1.0])), [1 / math.sqrt(5)])


def test_sunlit_fraction_geometry() -> None:
    f = heat.sunlit_fraction(np.array([0.0, 0.5, 1.0, 2.0]), 45.0)
    np.testing.assert_allclose(f, [1.0, 0.5, 0.0, 0.0], rtol=0, atol=1e-12)
    high_sun = heat.sunlit_fraction(np.array([1.0]), 80.0)
    np.testing.assert_allclose(high_sun, [1.0 - 1.0 / math.tan(math.radians(80.0))])


def test_mrt_and_feels_like_formulas() -> None:
    spec = load_spec()
    hp = spec["heat"]
    hw = np.array([0.0, 1.0])
    mrt = heat.mean_radiant_temperature(33.0, hw, 60.0, spec)
    f = np.clip(1 - hw / math.tan(math.radians(60.0)), 0, 1)
    svf = np.cos(np.arctan(2 * hw))
    want = 33.0 + hp["mrt_sun_excess_c"] * f + hp["mrt_sky_excess_c"] * svf
    np.testing.assert_allclose(mrt, want, rtol=1e-12)
    feel = heat.feels_like(33.0, mrt, spec)
    np.testing.assert_allclose(feel, 33.0 + hp["mrt_weight"] * (want - 33.0), rtol=1e-12)
    assert feel[1] < feel[0]  # a deep canyon shades the street


def test_heat_events_follow_the_spec() -> None:
    spec = load_spec()
    hp = spec["heat"]
    rps, freq, t_peak = heat.heat_events(spec)
    t = np.array(sorted(hp["return_periods"]), dtype=float)
    np.testing.assert_array_equal(rps, t)
    want_f = 1.0 / t
    want_f[:-1] -= 1.0 / t[1:]
    np.testing.assert_allclose(freq, want_f, rtol=1e-12)
    np.testing.assert_allclose(
        t_peak, hp["t_air_2y_c"] + hp["t_air_slope_c"] * np.log(t / 2.0), rtol=1e-12
    )


def test_repo_curve_is_reused() -> None:
    from climaterisk_worker import physical

    _, intens, ramp, *_ = physical._CATALOG_PERILS["heatwave"]
    f = heat.productivity_impact_function(0.5)
    assert f.haz_type == "HW"
    np.testing.assert_allclose(f.intensity, intens)
    np.testing.assert_allclose(f.mdd, np.array(ramp) * 0.5)
    np.testing.assert_allclose(f.paa, 1.0)


def _hand_eal(assets: list[dict], hazard: object, spec: dict) -> np.ndarray:
    from climaterisk_worker import physical

    _, intens, ramp, *_ = physical._CATALOG_PERILS["heatwave"]
    hp = spec["heat"]
    feel = hazard.intensity.toarray()  # type: ignore[attr-defined]
    out = np.zeros(len(assets))
    for k, a in enumerate(assets):
        kind = a["kind"]
        exposure = hp["workers"][kind] * hp["daily_wage_usd"] * hp["heatwave_days"]
        mdr = np.interp(feel[:, k], intens, np.array(ramp) * hp["max_mdd"][kind])
        out[k] = hazard.frequency @ (exposure * mdr)  # type: ignore[attr-defined]
    return out


def test_hazard_and_productivity_loss_equal_the_hand_calculation() -> None:
    spec = load_spec()
    hp = spec["heat"]
    _, assets = build_city(spec)
    hazard = heat.build_heat_hazard(assets, spec)
    assert hazard.haz_type == "HW" and hazard.units == "degC"
    hw = heat.street_canyons(assets, spec)
    _, _, t_peak = heat.heat_events(spec)
    t_loc = t_peak[:, None] + np.array([hp["uhi_c"][a["kind"]] for a in assets])[None, :]
    mrt = heat.mean_radiant_temperature(t_loc, hw[None, :], hp["solar_elevation_deg"], spec)
    np.testing.assert_allclose(
        hazard.intensity.toarray(), heat.feels_like(t_loc, mrt, spec), rtol=1e-12
    )
    rows = heat.price_productivity(assets, hazard, spec)
    got = np.array([r["eal_usd"] for r in rows])
    np.testing.assert_allclose(got, _hand_eal(assets, hazard, spec), rtol=1e-9, atol=1e-6)
    assert got.sum() > 0.0


def test_street_canyon_uses_building_heights() -> None:
    from climaterisk_worker.twin.viewer3d import generate_buildings

    spec = load_spec()
    _, assets = build_city(spec)
    hw = heat.street_canyons(assets, spec)
    height = {b["node"]: b["height_m"] for b in generate_buildings(spec, assets)}
    w = spec["heat"]["street_width_m"]
    for a, x in zip(assets, hw, strict=True):
        if "node" in a:
            want = height[a["node"]] / w
        else:
            n1, n2 = a["edge"]
            want = 0.5 * (height[n1] + height[n2]) / w
        assert x == pytest.approx(want, rel=1e-12)


def test_hotter_summers_cost_more_and_shade_helps() -> None:
    spec = load_spec()
    _, assets = build_city(spec)
    base = sum(
        r["eal_usd"]
        for r in heat.price_productivity(assets, heat.build_heat_hazard(assets, spec), spec)
    )
    hot = copy.deepcopy(spec)
    hot["heat"]["t_air_2y_c"] += 2.0
    hotter = sum(
        r["eal_usd"]
        for r in heat.price_productivity(assets, heat.build_heat_hazard(assets, hot), hot)
    )
    assert hotter > base > 0.0
    wide = copy.deepcopy(spec)
    wide["heat"]["street_width_m"] *= 4.0  # less shade
    exposed = heat.build_heat_hazard(assets, wide).intensity.toarray()
    assert np.all(exposed >= heat.build_heat_hazard(assets, spec).intensity.toarray() - 1e-12)
