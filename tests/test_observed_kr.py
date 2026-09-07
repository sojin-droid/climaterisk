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

The fixture's STRUCTURE and element names are the real ones (confirmed 2026-09-07 from the
portal's 컬럼정의서 and embedded Swagger — docs/OBSERVED_LOSSES_KR_SPEC.md §4, §12); only the
numbers are invented, for shape. They are never used for analysis, and nothing here is an
empirical validation of the model.
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

# Shaped after the REAL schema, confirmed 2026-09-07 from the portal's own
# 연도별 자연재해 피해_컬럼정의서.xlsx (FILE_000000002777343) and the Swagger embedded in the
# dataset page for apis.data.go.kr/1741000/NaturalDisasterDamageByYear — spec §4.
# An earlier version of this fixture invented a long shape (disasterType / propertyDamage rows).
# The response is actually WIDE — one element per disaster cause — plus an opaque `seq`.
# Element names below are verbatim from the source, including its `earthquak` typo.
FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <head>
    <totalCount>5</totalCount><numOfRows>10</numOfRows><pageNo>1</pageNo>
    <type>xml</type>
    <RESULT><resultCode>INFO-0</resultCode><resultMsg>NORMAL SERVICE</resultMsg></RESULT>
  </head>
  <row>
    <wrttimeid>2019</wrttimeid><seq>1</seq><tot>3000</tot>
    <typhoon>1000</typhoon><heavy_rain>2000</heavy_rain><heavy_snow>0</heavy_snow>
    <heavy_wind>0</heavy_wind><wind_wave_strong_wind>0</wind_wave_strong_wind>
    <typhoon_heavy_rain>0</typhoon_heavy_rain><lightning>0</lightning>
    <cold_wave>0</cold_wave><earthquak>0</earthquak><heatwave>0</heatwave>
  </row>
  <row>
    <wrttimeid>2020</wrttimeid><seq>1</seq><tot>6500</tot>
    <typhoon>2500</typhoon><heavy_rain>4000</heavy_rain><heavy_snow>0</heavy_snow>
    <heavy_wind>0</heavy_wind><wind_wave_strong_wind>0</wind_wave_strong_wind>
    <typhoon_heavy_rain>0</typhoon_heavy_rain><lightning>0</lightning>
    <cold_wave>0</cold_wave><earthquak>0</earthquak><heatwave>0</heatwave>
  </row>
  <row>
    <wrttimeid>2021</wrttimeid><seq>1</seq><tot>0</tot>
    <heavy_rain>0</heavy_rain><heavy_snow>0</heavy_snow>
    <heavy_wind>0</heavy_wind><wind_wave_strong_wind>0</wind_wave_strong_wind>
    <typhoon_heavy_rain>0</typhoon_heavy_rain><lightning>0</lightning>
    <cold_wave>0</cold_wave><earthquak>0</earthquak><heatwave>0</heatwave>
  </row>
  <row>
    <wrttimeid>2022</wrttimeid><seq>1</seq><tot>0</tot>
    <typhoon>N/A</typhoon><heavy_rain>0</heavy_rain><heavy_snow>0</heavy_snow>
    <heavy_wind>0</heavy_wind><wind_wave_strong_wind>0</wind_wave_strong_wind>
    <typhoon_heavy_rain>0</typhoon_heavy_rain><lightning>0</lightning>
    <cold_wave>0</cold_wave><earthquak>0</earthquak><heatwave>0</heatwave>
  </row>
  <row>
    <wrttimeid>2020</wrttimeid><seq>2</seq><tot>77</tot>
    <typhoon>11</typhoon><heavy_rain>66</heavy_rain><heavy_snow>0</heavy_snow>
    <heavy_wind>0</heavy_wind><wind_wave_strong_wind>0</wind_wave_strong_wind>
    <typhoon_heavy_rain>0</typhoon_heavy_rain><lightning>0</lightning>
    <cold_wave>0</cold_wave><earthquak>0</earthquak><heatwave>0</heatwave>
  </row>
</response>
"""

#: Confirmed response element per disaster cause (verbatim names, spec §4).
CAUSE_ELEMENTS = (
    "typhoon",
    "heavy_rain",
    "heavy_snow",
    "heavy_wind",
    "wind_wave_strong_wind",
    "typhoon_heavy_rain",
    "lightning",
    "cold_wave",
    "earthquak",
    "heatwave",
)

# Causes this platform can map onto a peril. heavy_rain (호우) has no platform peril — urban
# pluvial is structurally absent (GAP G7) — so it must not silently become river_flood.
PERIL_BY_CAUSE_ELEMENT = {"typhoon": "tropical_cyclone"}


class SeqMeaningUnknown(RuntimeError):
    """Raised when the caller did not say which ``seq`` classification to read.

    ``seq`` is documented only as "분류 일련번호" and the response carries **no label and no unit
    element**, so one year has several rows whose meaning (property damage in money vs casualties
    in persons) cannot be told apart from the API alone. A loader must refuse, not assume.
    """


def _reference_extract(
    xml_text: str, cause_element: str, seq: str | None = None
) -> tuple[dict[int, float], list[str]]:
    """Contract witness (test-only): one cause element -> ``{year: amount}`` + problems.

    Encodes the decisions F1 must make identically: read ``wrttimeid`` as the year, read the named
    cause element as a float, **skip** a row where that element is absent, **skip** a row whose
    value is not numeric — recording both rather than coercing them to zero — and **refuse** when
    the ``seq`` classification to read has not been established from the source publication.
    """
    if seq is None:
        raise SeqMeaningUnknown(
            "seq (분류 일련번호) has no label in the API; the classification to read must be "
            "established from the 행정안전 통계연보 table before any row is used"
        )
    if cause_element not in CAUSE_ELEMENTS:
        raise KeyError(f"{cause_element!r} is not a documented cause element")
    losses: dict[int, float] = {}
    problems: list[str] = []
    for row in ET.fromstring(xml_text).iter("row"):
        if (row.findtext("seq") or "") != seq:
            continue
        year_text = row.findtext("wrttimeid")
        raw = row.findtext(cause_element)
        if raw is None:
            problems.append(f"{year_text}: not_in_source ({cause_element} element missing)")
            continue
        try:
            amount = float(raw)
        except ValueError:
            problems.append(f"{year_text}: unknown ({cause_element} {raw!r} not numeric)")
            continue
        losses[int(str(year_text))] = amount
    return losses, problems


# --------------------------------------------------------------- parser contract


def test_parser_contract_extracts_year_and_amount() -> None:
    losses, _ = _reference_extract(FIXTURE_XML, "typhoon", seq="1")
    assert losses == {2019: 1000.0, 2020: 2500.0}
    assert all(isinstance(y, int) for y in losses)
    assert all(isinstance(v, float) for v in losses.values())


def test_parser_contract_refuses_when_the_seq_classification_is_unknown() -> None:
    """No label or unit element exists, so a row's meaning must not be inferred."""
    with pytest.raises(SeqMeaningUnknown):
        _reference_extract(FIXTURE_XML, "typhoon")


def test_parser_contract_rows_of_other_seq_are_not_mixed_in() -> None:
    """A year carries several seq rows (money vs persons) — never summed across them."""
    seq1, _ = _reference_extract(FIXTURE_XML, "typhoon", seq="1")
    seq2, _ = _reference_extract(FIXTURE_XML, "typhoon", seq="2")
    assert seq1[2020] == 2500.0
    assert seq2 == {2020: 11.0}


def test_parser_contract_maps_cause_element_to_peril_and_refuses_unmapped() -> None:
    assert PERIL_BY_CAUSE_ELEMENT["typhoon"] == "tropical_cyclone"
    assert "heavy_rain" not in PERIL_BY_CAUSE_ELEMENT
    rain, _ = _reference_extract(FIXTURE_XML, "heavy_rain", seq="1")
    assert rain == {2019: 2000.0, 2020: 4000.0, 2021: 0.0, 2022: 0.0}
    # typhoon_heavy_rain exists because the source itself could not split the two causes.
    assert "typhoon_heavy_rain" in CAUSE_ELEMENTS
    # The source's own typo is part of the contract.
    assert "earthquak" in CAUSE_ELEMENTS and "earthquake" not in CAUSE_ELEMENTS


def test_parser_contract_missing_and_invalid_amounts_are_skipped_not_zeroed() -> None:
    """A missing or non-numeric amount must not become a 0-loss year: that is a real number."""
    losses, problems = _reference_extract(FIXTURE_XML, "typhoon", seq="1")
    assert 2021 not in losses and 2022 not in losses
    assert any("not_in_source" in p for p in problems)
    assert any("unknown" in p for p in problems)
    assert len(problems) == 2


def test_parser_contract_series_declares_its_provenance_and_basis() -> None:
    """What F1 must return: an ObservedSeries with source, unit, price basis, coverage, scope."""
    losses, _ = _reference_extract(FIXTURE_XML, "typhoon", seq="1")
    series = val.ObservedSeries(
        source=FIXTURE_SOURCE,
        # The API publishes NO unit element; the fixture states one only to exercise the
        # unit checks. The real unit is still unconfirmed — spec §5.
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
    # the reason must name what is actually missing, not a resolved item
    assert "seq" in out["detail"] and "no unit element" in out["detail"]


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
