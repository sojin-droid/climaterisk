"""Heat-mortality scenario resolution + Korean heatwave hazard-type consistency (no CLIMADA).

``_run_heat_mortality`` used to query the ``historical`` catalog key unconditionally, so KMA
SSP layers registered under ``rcp45``/``rcp85`` were never used. The resolver now tries the
requested scenario first and falls back to ``historical`` (reported in ``detail``). The
end-to-end KMA test (request → SSP hazard → ImpactCalc) lives in ``test_kma_scenario.py``
and needs CLIMADA.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import catalog, physical  # noqa: E402
from climaterisk_worker._params import HEATWAVE_HAZ_TYPE  # noqa: E402


def _fake_catalog(available: dict[tuple[str, str, str], str]):  # type: ignore[no-untyped-def]
    """load_hazard stand-in returning a token for (peril, scenario, region) keys present."""

    def load_hazard(peril: str, scenario: str, region: str, year):  # type: ignore[no-untyped-def]
        return available.get((peril, scenario, region))

    return load_hazard


def test_requested_scenario_is_used_when_the_layer_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        catalog,
        "load_hazard",
        _fake_catalog(
            {
                ("heat_mortality", "rcp45", "KOR"): "SSP245",
                ("heat_mortality", "historical", "KOR"): "HIST",
            }
        ),
    )
    haz, scen = physical._resolve_heat_hazard("KOR", "rcp45", 2050)
    assert (haz, scen) == ("SSP245", "rcp45")


def test_falls_back_to_historical_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        catalog, "load_hazard", _fake_catalog({("heat_mortality", "historical", "KOR"): "HIST"})
    )
    haz, scen = physical._resolve_heat_hazard("KOR", "rcp85", 2050)
    assert (haz, scen) == ("HIST", "historical")


def test_no_layer_at_all_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "load_hazard", _fake_catalog({}))
    assert physical._resolve_heat_hazard("KOR", "rcp45", 2050) == (None, None)


def test_historical_request_does_not_double_query(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def load_hazard(peril, scenario, region, year):  # type: ignore[no-untyped-def]
        calls.append(scenario)
        return None

    monkeypatch.setattr(catalog, "load_hazard", load_hazard)
    physical._resolve_heat_hazard("KOR", "historical", 2020)
    assert calls == ["historical"]


# --- Korean heatwave layer haz_type must match the runner ------------------------------


def _load_heat_korea():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("heat_korea", REPO / "scripts" / "heat_korea.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_korean_heatwave_layer_uses_the_runners_hazard_type() -> None:
    hk = _load_heat_korea()
    cells = [SimpleNamespace(lat=37.5, lon=127.0), SimpleNamespace(lat=35.1, lon=129.0)]
    years = np.array([2021, 2022, 2023])
    obs = SimpleNamespace(tmax=np.full((2, 3, 122), 30.0), source="synthetic test stack")
    grid = hk._heatwave_grid(obs, cells, years, "rcp45", 2030)
    runner_haz_type = physical._CATALOG_PERILS["heatwave"][0]
    assert grid["haz_type"] == runner_haz_type == HEATWAVE_HAZ_TYPE == "HW"
    assert grid["peril"] == "heatwave" and grid["climate_scenario"] == "rcp45"
    assert len(grid["observations"]) == 2 * 3
    # the mortality layer keeps its own tag — the two layers must not be confused
    from climaterisk_worker import heat_mortality as hm

    assert hm.HAZ_TYPE == "HM" != grid["haz_type"]
