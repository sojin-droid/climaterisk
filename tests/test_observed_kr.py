"""Contract tests for Korean observed-loss ingestion — **no loader exists yet**.

``worker/climaterisk_worker/observed_kr.py`` (spec F1) is deliberately NOT implemented: it is
behind a data gate (a data.go.kr service key and the portal's 컬럼정의서 are needed to fix the
field names, unit multiple and price basis — docs/OBSERVED_LOSSES_KR_SPEC.md §9, §11).

What this file does instead:

1. freezes the **parser contract** F1 must satisfy, against a tiny synthetic XML fixture whose
   own ``source`` says it is a fixture. ``_reference_extract`` below is a *contract witness*
   living in the test file — it reads the fixture with the standard library so the expectations
   are executable. It is not the production loader and must not be copied as one: F1 additionally
   owns authentication, response caching, unit resolution and deflation;
2. tests the guards that stop an incomparable observed/modelled pair from being calibrated —
   units, sub-peril coverage, spatial scope and year alignment.

The fixture numbers are invented for shape only. They are never used for analysis, and nothing
here is an empirical validation of the model.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import calibration as cal  # noqa: E402
from climaterisk_worker import validation as val  # noqa: E402

FIXTURE_SOURCE = "synthetic unit-test fixture (not 재해연보 data)"

# A response-shaped fixture: two well-formed rows, one with a missing amount field and one with a
# non-numeric amount, because those two cases are what the parser has to decide about.
FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE</resultMsg></header>
  <body>
    <items>
      <item><year>2019</year><disasterType>태풍</disasterType><propertyDamage>1000</propertyDamage></item>
      <item><year>2020</year><disasterType>태풍</disasterType><propertyDamage>2500</propertyDamage></item>
      <item><year>2021</year><disasterType>태풍</disasterType></item>
      <item><year>2022</year><disasterType>태풍</disasterType><propertyDamage>N/A</propertyDamage></item>
      <item><year>2020</year><disasterType>호우</disasterType><propertyDamage>4000</propertyDamage></item>
    </items>
  </body>
</response>
"""

# The 재해연보 disaster types this platform can map onto a peril. 호우 has no platform peril
# (urban pluvial is structurally absent — GAP G7), so it must not silently become river_flood.
PERIL_BY_DISASTER_TYPE = {"태풍": "tropical_cyclone"}


def _reference_extract(xml_text: str, disaster_type: str) -> tuple[dict[int, float], list[str]]:
    """Contract witness (test-only): rows of one disaster type -> ``{year: amount}`` + problems.

    Encodes the four decisions F1 must make identically: parse ``year`` as int, parse the amount
    as float, **skip** a row whose amount field is absent, and **skip** a row whose amount is not
    numeric — recording both as problems rather than coercing them to zero.
    """
    losses: dict[int, float] = {}
    problems: list[str] = []
    for item in ET.fromstring(xml_text).iter("item"):
        if (item.findtext("disasterType") or "") != disaster_type:
            continue
        year_text = item.findtext("year")
        raw = item.findtext("propertyDamage")
        if raw is None:
            problems.append(f"{year_text}: not_in_source (propertyDamage missing)")
            continue
        try:
            amount = float(raw)
        except ValueError:
            problems.append(f"{year_text}: unknown (propertyDamage {raw!r} not numeric)")
            continue
        losses[int(str(year_text))] = amount
    return losses, problems


# --------------------------------------------------------------- parser contract


def test_parser_contract_extracts_year_and_amount() -> None:
    losses, _ = _reference_extract(FIXTURE_XML, "태풍")
    assert losses == {2019: 1000.0, 2020: 2500.0}
    assert all(isinstance(y, int) for y in losses)
    assert all(isinstance(v, float) for v in losses.values())


def test_parser_contract_maps_disaster_type_to_peril_and_refuses_unmapped() -> None:
    assert PERIL_BY_DISASTER_TYPE["태풍"] == "tropical_cyclone"
    # 호우 rows exist in the fixture but there is no platform peril to map them onto.
    assert "호우" not in PERIL_BY_DISASTER_TYPE
    rain, _ = _reference_extract(FIXTURE_XML, "호우")
    assert rain == {2020: 4000.0}  # readable, but unmapped — never folded into river_flood


def test_parser_contract_missing_and_invalid_amounts_are_skipped_not_zeroed() -> None:
    """A missing or non-numeric amount must not become a 0-loss year: that is a real number."""
    losses, problems = _reference_extract(FIXTURE_XML, "태풍")
    assert 2021 not in losses and 2022 not in losses
    assert any("not_in_source" in p for p in problems)
    assert any("unknown" in p for p in problems)
    assert len(problems) == 2


def test_parser_contract_series_declares_its_provenance_and_basis() -> None:
    """What F1 must return: an ObservedSeries with source, unit, price basis, coverage, scope."""
    losses, _ = _reference_extract(FIXTURE_XML, "태풍")
    series = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="KRW thousand",
        losses=losses,
        peril="tropical_cyclone",
        currency="KRW",
        covers_subperils=val.TC_AGGREGATE_SUBPERILS,
        scope="national:KOR",
        price_basis="nominal",
        notes="fixture; 재해연보 재산피해 is 당해연도 가격 — deflate before comparing",
    )
    assert "synthetic unit-test fixture" in series.source
    assert series.years() == [2019, 2020]
    assert series.unit_spec().currency == "KRW" and series.unit_spec().multiplier == 1e3
    assert series.price_basis == "nominal"
    assert series.mean_annual_loss() == pytest.approx(1750.0)


# --------------------------------------------------------------- unit guards


@pytest.mark.parametrize(
    ("observed_unit", "modelled_unit"),
    [("KRW", "USD"), ("KRW thousand", "KRW"), ("억원", "KRW"), ("million USD", "USD")],
)
def test_unit_mismatch_is_reported(observed_unit: str, modelled_unit: str) -> None:
    assert val.check_units(observed_unit, modelled_unit)["status"] == val.STATUS_MISMATCH


def test_unrecognised_unit_is_unknown_never_ok() -> None:
    out = val.check_units("명-등가/yr", "")
    assert out["status"] == val.STATUS_UNKNOWN


def test_identical_unparsed_units_are_ok_but_say_why() -> None:
    out = val.check_units("persons/yr", "persons/yr")
    assert out["status"] == val.STATUS_OK and out["basis"] == "identical-string"


def test_unit_mismatch_blocks_calibration() -> None:
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="KRW",
        losses={2020: 1.0},
        covers_subperils=("wind",),
        scope="national:KOR",
    )
    gate = cal.calibration_gate(
        observed=observed, modelled_unit="USD", modelled_scope="national:KOR"
    )
    assert gate["proceed"] is False
    assert gate["comparison_status"] == val.COMPARISON_NOT_COMPARABLE
    assert any("unit mismatch" in b for b in gate["blockers"])


# --------------------------------------------------------------- peril coverage guards


def test_aggregate_tc_observed_vs_wind_only_model_is_not_calibration_compatible() -> None:
    cover = val.check_peril_coverage(val.TC_AGGREGATE_SUBPERILS, cal.MODEL_COVERS_SUBPERILS)
    assert cover["calibration_ok"] is False
    assert cover["status"] == "observed_wider"
    assert cover["missing_in_model"] == ["rain", "surge"]


def test_aggregate_tc_observed_blocks_calibration_but_validation_still_reports_ratio() -> None:
    """Blocked for fitting, still comparable for diagnosis: the ratio is the capture rate."""
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="USD",
        losses={2019: 100.0, 2020: 200.0},
        covers_subperils=val.TC_AGGREGATE_SUBPERILS,
        scope="national:KOR",
    )
    gate = cal.calibration_gate(
        observed=observed, modelled_unit="USD", modelled_scope="national:KOR"
    )
    assert gate["proceed"] is False
    assert any("missing rain, surge" in b for b in gate["blockers"])

    out = val.annual_comparison(
        observed.losses,
        {2019: 10.0, 2020: 20.0},
        observed_series=observed,
        modelled_unit="USD",
        modelled_covers=cal.MODEL_COVERS_SUBPERILS,
        modelled_scope="national:KOR",
    )
    assert out["ratio"] == pytest.approx(0.1)  # capture rate — the diagnostic that survives
    assert out["comparison_status"] == val.COMPARISON_NOT_COMPARABLE
    assert out["peril_coverage"]["missing_in_model"] == ["rain", "surge"]


def test_matching_coverage_and_scope_and_units_is_allowed() -> None:
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="USD",
        losses={2020: 1.0},
        covers_subperils=("wind",),
        scope="national:KOR",
    )
    gate = cal.calibration_gate(
        observed=observed, modelled_unit="USD", modelled_scope="national:KOR"
    )
    assert gate["proceed"] is True
    assert gate["comparison_status"] == val.COMPARISON_COMPARABLE
    assert gate["blockers"] == []


# --------------------------------------------------------------- scope guards


def test_national_observed_against_point_portfolio_is_blocked() -> None:
    """The §8 defect: a country total fitted to a few placed assets."""
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="USD",
        losses={2020: 1.0},
        covers_subperils=("wind",),
        scope="national:KOR",
    )
    gate = cal.calibration_gate(
        observed=observed, modelled_unit="USD", modelled_scope="point_portfolio:3_assets"
    )
    assert gate["proceed"] is False
    assert any("spatial scope mismatch" in b for b in gate["blockers"])


def test_undeclared_scope_is_blocked_not_assumed() -> None:
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="USD",
        losses={2020: 1.0},
        covers_subperils=("wind",),
        scope="national:KOR",
    )
    gate = cal.calibration_gate(observed=observed, modelled_unit="USD", modelled_scope="")
    assert gate["proceed"] is False
    assert gate["comparison_status"] == val.COMPARISON_UNKNOWN
    assert any("scope undeclared" in b for b in gate["blockers"])


def test_admin_level_observed_matches_the_same_admin_exposure() -> None:
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="USD",
        losses={2020: 1.0},
        covers_subperils=("wind",),
        scope="admin2:28710",
    )
    assert (
        cal.calibration_gate(observed=observed, modelled_unit="USD", modelled_scope="admin2:28710")[
            "proceed"
        ]
        is True
    )


def test_explicit_opt_in_proceeds_but_keeps_the_not_comparable_verdict() -> None:
    """Optimisation success is not scientific validity: fitted + not_comparable is a valid state."""
    observed = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        unit="USD",
        losses={2020: 1.0},
        covers_subperils=val.TC_AGGREGATE_SUBPERILS,
        scope="national:KOR",
    )
    gate = cal.calibration_gate(
        observed=observed,
        modelled_unit="USD",
        modelled_scope="national:KOR",
        allow_incomparable=True,
    )
    assert gate["proceed"] is True
    assert gate["comparison_status"] == val.COMPARISON_NOT_COMPARABLE
    record = cal.calibration_record(
        country="KOR",
        initial=190.5,
        calibrated=120.0,
        observed_annual_loss=1.0,
        modelled_annual_loss=1.0,
        observed_source=observed.source,
        observed_period=(2019, 2020),
        n_observed_events=2,
        hazard="synthetic",
        n_assets=1,
        target_covers_subperils=val.TC_AGGREGATE_SUBPERILS,
        model_covers_subperils=cal.MODEL_COVERS_SUBPERILS,
        scope=gate["scope"],
        comparison_status=gate["comparison_status"],
        comparability=gate,
    )
    assert record["fit_status"] == "fitted"
    assert record["comparison_status"] == val.COMPARISON_NOT_COMPARABLE
    assert record["target_covers_subperils"] == ["wind", "surge", "rain"]
    assert record["model_covers_subperils"] == ["wind"]


# --------------------------------------------------------------- year alignment


def test_only_overlapping_years_are_compared() -> None:
    out = val.annual_comparison({2018: 1.0, 2019: 2.0, 2020: 3.0}, {2019: 2.0, 2021: 9.0})
    assert out["years_compared"] == [2019]
    assert out["n"] == 1 and out["coverage"] == pytest.approx(1 / 3)
    assert out["comparison_status"] == val.COMPARISON_UNKNOWN  # no metadata given → not "ok"


def test_synthetic_ensemble_years_are_not_a_year_by_year_validation() -> None:
    """§7: synthetic members inherit the parent track's date, so per-year sums are inflated.

    The same three events dated in one year sum to 3x that year's loss unless the caller filters
    to the original tracks. ``check_annual_basis`` must refuse to call the unfiltered case an
    annual comparison.
    """
    import datetime as dt

    d = float(dt.date(2019, 8, 1).toordinal())
    at_event = [10.0, 10.0, 10.0]  # one historical event + two synthetic copies
    dates = [d, d, d]
    orig = [True, False, False]

    unfiltered = val.modelled_annual_losses(at_event, dates)
    filtered = val.modelled_annual_losses(at_event, dates, orig=orig)
    assert unfiltered == {2019: 30.0}  # ensemble-inflated
    assert filtered == {2019: 10.0}  # the honest per-year number

    assert val.check_annual_basis(False)["annual_comparison_ok"] is False
    assert val.check_annual_basis(None)["annual_comparison_ok"] is False
    assert val.check_annual_basis(True)["annual_comparison_ok"] is True


def test_undated_events_are_skipped() -> None:
    assert val.modelled_annual_losses([1.0, 2.0], [0.0, -1.0]) == {}


# --------------------------------------------------------------- source selection (F4)


def test_disaster_yearbook_source_is_refused_with_the_data_gate_reason() -> None:
    out = cal.compute_calibration(
        {
            "assets": [{"id": "a", "lat": 37.5, "lon": 127.0, "value": 1.0, "currency": "KRW"}],
            "climate_scenario": "rcp45",
            "anchor_years": [2040],
            "observed_source": "disaster_yearbook",
        }
    )
    assert out["status"] == "error"
    assert "15107318" in out["detail"] and "컬럼정의서" in out["detail"]
    assert "no loader yet" in out["detail"]


def test_unknown_observed_source_is_refused() -> None:
    out = cal.compute_calibration(
        {
            "assets": [{"id": "a", "lat": 37.5, "lon": 127.0, "value": 1.0}],
            "climate_scenario": "rcp45",
            "anchor_years": [2040],
            "observed_source": "made_up",
        }
    )
    assert out["status"] == "error" and "unknown observed_source" in out["detail"]


def test_portfolio_currency_is_unknown_when_assets_disagree() -> None:
    assert cal.portfolio_currency([{"currency": "KRW"}, {"currency": "USD"}]) == ""
    assert cal.portfolio_currency([{"currency": "krw"}, {"currency": "KRW"}]) == "KRW"
    assert cal.portfolio_currency([{}]) == ""
