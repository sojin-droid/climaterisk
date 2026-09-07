"""The physical-run request must carry run-config options through to the worker.

Options (e.g. the opt-in Knutson TC future method) are set in the UI on
``RunConfig.options`` and forwarded verbatim so the worker can branch on them.
"""

from __future__ import annotations

from climaterisk.core.entities import Portfolio, RunConfig, Scenario
from climaterisk.engines.base import (
    OBSERVED_SOURCES,
    CalibrationRequest,
    PhysicalRunRequest,
)


def test_options_forwarded_to_request() -> None:
    portfolio = Portfolio(
        run_config=RunConfig(options={"tc_future_method": "knutson"}),
        scenario=Scenario(),
    )
    req = PhysicalRunRequest.from_portfolio(portfolio)
    assert req.options == {"tc_future_method": "knutson"}


def test_options_default_empty() -> None:
    req = PhysicalRunRequest.from_portfolio(Portfolio())
    assert req.options == {}


def test_calibration_observed_source_defaults_to_emdat() -> None:
    """A caller that does not pass the new field keeps the pre-existing behaviour."""
    req = CalibrationRequest.from_portfolio(Portfolio())
    assert req.observed_source == "emdat"


def test_calibration_observed_source_is_carried_to_the_worker_request() -> None:
    req = CalibrationRequest.from_portfolio(Portfolio(), "disaster_yearbook")
    assert req.observed_source == "disaster_yearbook"
    assert "disaster_yearbook" in OBSERVED_SOURCES
    # The worker reads the serialized request, so the field has to survive the dump.
    assert '"observed_source": "disaster_yearbook"' in req.model_dump_json(indent=2)
