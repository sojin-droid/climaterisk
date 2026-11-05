"""No-damage threshold report: the threshold mechanics and its identity with the JRC curve.

The engine's flood curve (CLIMADA ``ImpfRiverFlood``, JRC Huizinga 2017) already has
``MDD = 0`` at 0 m and 0.05 m, so the user's 5 cm no-damage rule (0 at or below T, the
unshifted curve above) must reproduce the existing impact exactly; a higher T removes only
the events at or below it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")

import numpy as np  # noqa: E402


def _report():
    spec = importlib.util.spec_from_file_location(
        "no_damage_threshold_report", REPO / "scripts" / "no_damage_threshold_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _hazard(depths: np.ndarray):
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    n_ev = depths.size
    return Hazard(
        haz_type="RF",
        units="m",
        centroids=Centroids(lat=np.array([37.5]), lon=np.array([127.0])),
        event_id=np.arange(1, n_ev + 1),
        event_name=[f"e{k}" for k in range(n_ev)],
        date=np.arange(1, n_ev + 1),
        frequency=np.full(n_ev, 0.1),
        intensity=sparse.csr_matrix(depths.reshape(n_ev, 1)),
        fraction=sparse.csr_matrix(np.ones((n_ev, 1))),
    )


def _eal(hazard) -> float:
    from climada.engine import ImpactCalc
    from climada.entity import Exposures, ImpactFuncSet
    from climaterisk_worker.physical_risk import registry

    f = registry.flood_impact_function("Asia", "commercial")
    exp = Exposures({"latitude": [37.5], "longitude": [127.0], "value": [1.0e6], "impf_RF": [f.id]})
    exp.assign_centroids(hazard)
    return float(ImpactCalc(exp, ImpactFuncSet([f]), hazard).impact().aai_agg)


def test_config_has_the_threshold_block() -> None:
    cfg = _report().threshold_config()
    assert cfg["depth_m"] == pytest.approx(0.05)
    assert cfg["enabled"] is False
    assert cfg["depth_m"] in cfg["report_sensitivity_m"]


def test_threshold_zeroes_at_or_below_and_keeps_full_depth_above() -> None:
    rep = _report()
    depths = np.array([0.0001, 0.05, 0.08, 0.3, 2.0])
    out = rep.apply_threshold(_hazard(depths), 0.10)
    np.testing.assert_allclose(out.intensity.toarray().ravel(), [0, 0, 0, 0.3, 2.0])
    np.testing.assert_allclose(out.fraction.toarray().ravel(), [0, 0, 0, 1, 1])


def test_five_cm_rule_equals_the_existing_jrc_curve() -> None:
    from climaterisk_worker.physical_risk import registry

    f = registry.flood_impact_function("Asia", "commercial")
    assert f.calc_mdr(np.array([0.0, 0.05]))[1] == 0.0  # the identity rests on this point
    rep = _report()
    haz = _hazard(np.array([0.0001, 0.03, 0.05, 0.2, 1.5]))
    np.testing.assert_allclose(_eal(rep.apply_threshold(haz, 0.05)), _eal(haz), rtol=1e-12)
    expect = 1.0e6 * 0.1 * float(f.calc_mdr(np.array([1.5]))[0])  # only the 1.5 m event
    np.testing.assert_allclose(_eal(rep.apply_threshold(haz, 0.5)), expect, rtol=1e-12)
