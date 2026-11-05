"""Korean observed-loss ingestion — the F1 loader and the guards around it.

``worker/climaterisk_worker/observed_kr.py`` (spec F1) **is now implemented**; the data gate
that held it back (a data.go.kr service key) was cleared on 2026-09-11 and the live response
settled the two schema questions the spec left open (§4, §12).

What this file covers:

1. the **loader** — parsing, the ObservedSeries contract it must produce, and the four source
   defects it has to defend against (``tot``, ``typhoon_heavy_rain``, duplicate ``seq``, the
   thousandfold year). Every test drives a tiny synthetic XML fixture, never the network;
2. the **parser contract** the loader must satisfy. ``_reference_extract`` below is a *contract
   witness* living in the test file — it reads the fixture with the standard library so the
   expectations are executable independently of the loader's own code path. It is not the
   production loader and must not be copied as one;
3. the guards that stop an incomparable observed/modelled pair from being calibrated — units,
   sub-peril coverage, spatial scope and year alignment. **Having the data does not lift them.**

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
from climaterisk_worker import observed_kr as okr  # noqa: E402
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
        # Unit confirmed 2026-09-07 from the source table (행정안전 통계연보 7-3-2-2:
        # "(단위: 백만원) (Unit: KRW million)", identical across the 2024/2025/2026 editions)
        # — spec §5-A. The API itself publishes no unit element.
        unit="KRW million",
        losses=losses,
        peril="tropical_cyclone",
        currency="KRW",
        covers_subperils=val.TC_AGGREGATE_SUBPERILS,
        scope="national:KOR",
        price_basis="nominal",
        notes=(
            "fixture; source table is 당해연도 가격 (nominal) in KRW million — the edition notes\n"
            "claiming 환산가격 contradict their own unchanged values (spec §5-A)"
        ),
    )
    assert "synthetic unit-test fixture" in series.source
    assert series.years() == [2019, 2020]
    assert series.unit_spec().currency == "KRW" and series.unit_spec().multiplier == 1e6
    assert series.price_basis == "nominal"
    assert series.mean_annual_loss() == pytest.approx(1750.0)


# ------------------------------------------------- source defects the loader must survive
# Found 2026-09-07 by comparing three 행정안전 통계연보 editions against the 2024 재해연보 —
# docs/OBSERVED_LOSSES_KR_SPEC.md §5-B. These are properties of the SOURCE, not of our code, so
# they are frozen here as requirements on F1 rather than as behaviour of anything already built.

#: The source table's own confirmed unit (all three editions, English gloss included).
SOURCE_UNIT = "KRW million"
#: Causes the source table carries that the API does not expose (spec §4, §5-B item 3).
CAUSES_MISSING_FROM_API = ("우박", "폭풍·해일", "냉해·동해")
#: 2023 row of table 7-3-2-2, verbatim (백만원): the published total and the three dropped causes.
ROW_2023_TOTAL = 958_221
ROW_2023_MISSING = {"우박": 2_293, "폭풍·해일": 16, "냉해·동해": 110_354}
#: 2024 row as published in the 2026 edition (천원 despite a 백만원 header) vs 재해연보 (백만원).
ROW_2024_AS_PUBLISHED_THOUSANDS = {"합계": 910_713_075, "태풍": 106_342, "호우": 423_947_425}
ROW_2024_YEARBOOK_MILLIONS = {"합계": 910_713, "태풍": 106, "호우": 423_947}


def test_source_unit_parses_to_krw_millions() -> None:
    """The unit string the loader must record resolves to KRW x 1e6, not thousands."""
    spec = val.parse_unit(SOURCE_UNIT)
    assert spec.currency == "KRW" and spec.multiplier == 1e6
    # And it must NOT compare equal to the thousands form that the detail tables use.
    assert val.check_units(SOURCE_UNIT, "KRW thousand")["status"] == val.STATUS_MISMATCH


def test_api_columns_do_not_sum_to_the_published_total() -> None:
    """`tot` is not the sum of the causes the API exposes — 11.8 % short in 2023."""
    dropped = sum(ROW_2023_MISSING.values())
    assert set(ROW_2023_MISSING) == set(CAUSES_MISSING_FROM_API)
    assert dropped == 112_663
    available = ROW_2023_TOTAL - dropped
    assert available == 845_558
    assert dropped / ROW_2023_TOTAL == pytest.approx(0.1176, abs=5e-4)


def test_latest_year_row_is_published_a_thousand_times_too_large() -> None:
    """2026 edition, 2024 row: 천원 under a 백만원 header — a x1000 trap on the newest year."""
    for cause, thousands in ROW_2024_AS_PUBLISHED_THOUSANDS.items():
        millions = ROW_2024_YEARBOOK_MILLIONS[cause]
        assert round(thousands / 1000) == millions, cause
    # A loader that took the header at face value would report 106,342 KRW million of typhoon
    # damage for 2024 where the yearbook reports 106.
    assert ROW_2024_AS_PUBLISHED_THOUSANDS["태풍"] / ROW_2024_YEARBOOK_MILLIONS["태풍"] > 900


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
    assert "15107318" in out["detail"]
    # The reason must name what is actually blocking now. The key and the schema questions were
    # resolved on 2026-09-11 and the loader exists; what remains is physics and scope.
    assert "has a loader" in out["detail"] or "HAS a loader" in out["detail"]
    assert "wind + surge + rain" in out["detail"]
    assert "point portfolio" in out["detail"]
    assert "no data.go.kr service key" not in out["detail"]


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


# --- F1 loader ------------------------------------------------------------------------
# Every test below drives a synthetic fixture, never the live API: a unit test must not
# depend on data.go.kr being up, and the real response must not be frozen into the repo.
# Fixture ELEMENT NAMES are the real ones; the numbers are the real 2026-09-11 values only
# where a test is about a real source defect, and are never used as evidence about Korea.

LOADER_FIXTURE_SOURCE = "synthetic unit-test fixture (not 재해연보 data)"


def _envelope(rows: str, total: int | None = None, result: str = "INFO-0") -> bytes:
    n = total if total is not None else rows.count("<row>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NaturalDisasterDamageByYear>
  <head><totalCount>{n}</totalCount><numOfRows>100</numOfRows><pageNo>1</pageNo>
    <RESULT><resultCode>{result}</resultCode><resultMsg>NOMAL SERVICE</resultMsg></RESULT>
  </head>{rows}
</NaturalDisasterDamageByYear>""".encode()


def _wide_row(year: int, seq: str = "1", **cols: int) -> str:
    values = {"tot": 0, **dict.fromkeys(okr.CAUSE_COLUMNS, 0), **cols}
    body = "".join(f"<{k}>{v}</{k}>" for k, v in values.items())
    return f"<row><wrttimeid>{year}</wrttimeid><seq>{seq}</seq>{body}</row>"


#: Four years shaped like the live response, including the two real oddities: 2018 carries a
#: non-zero ``typhoon_heavy_rain``, and 2017 reports zero typhoon damage.
LOADER_FIXTURE = _envelope(
    _wide_row(2018, tot=141284, typhoon=64200, heavy_rain=53800, typhoon_heavy_rain=6416)
    + _wide_row(2019, tot=216226, typhoon=212778, heavy_rain=1651)
    + _wide_row(2020, tot=1318177, typhoon=222541, heavy_rain=1095172)
    + _wide_row(2017, tot=187302, typhoon=0, heavy_rain=101592, earthquak=85022)
)


def _stub(payload: bytes):  # type: ignore[no-untyped-def]
    """Stand-in for :func:`observed_kr.fetch_raw` — page 1 is the payload, page 2 is empty."""

    def fetch(page: int = 1, rows: int = 100, key: str | None = None, timeout: int = 60) -> bytes:
        return payload if page == 1 else _envelope("", total=0)

    return fetch


def test_loader_parses_rows_by_element_name_and_sorts_by_year() -> None:
    rows = okr.fetch_rows(key="fixture", fetch=_stub(LOADER_FIXTURE))
    assert [r["wrttimeid"] for r in rows] == [2017, 2018, 2019, 2020]
    assert rows[3]["typhoon"] == 222541
    assert rows[1]["typhoon_heavy_rain"] == 6416
    assert all(r["seq"] == "1" for r in rows)


def test_loader_series_declares_every_field_the_gate_reads() -> None:
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(LOADER_FIXTURE))
    assert series.unit == "KRW million" and series.currency == "KRW"
    assert series.unit_spec().multiplier == 1e6
    assert series.price_basis == "nominal"
    assert series.scope == "national:KOR"
    assert series.peril == "tropical_cyclone"
    # A 태풍 loss statistic books wind + surge + rain together (spec §6-1).
    assert series.covers_subperils == val.TC_AGGREGATE_SUBPERILS
    assert "15107318" in series.source


def test_loader_series_tracks_period_and_year_counts() -> None:
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(LOADER_FIXTURE))
    assert series.observed_period == (2017, 2020)
    assert series.n_years == 4
    assert series.n_nonzero_years == 3  # 2017 reports zero — data, not a gap
    assert series.losses[2017] == 0.0


def test_loader_reads_the_named_cause_column_never_the_total() -> None:
    """``tot`` omits 우박·폭풍해일·냉해동해 — 11.8 % of the 2023 total (spec §4)."""
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(LOADER_FIXTURE))
    assert series.losses[2020] == 222541.0, "the typhoon column, not tot=1318177"
    assert all(v != 1318177.0 for v in series.losses.values())


def test_loader_does_not_reconstruct_a_total_from_the_cause_columns() -> None:
    """Summing the published causes is not the published total and must not stand in for it."""
    rows = okr.fetch_rows(key="fixture", fetch=_stub(LOADER_FIXTURE))
    row_2020 = next(r for r in rows if r["wrttimeid"] == 2020)
    assert sum(row_2020[c] for c in okr.CAUSE_COLUMNS) != row_2020["tot"]


def test_loader_does_not_add_typhoon_heavy_rain_into_typhoon() -> None:
    """The source's "could not separate" column makes the typhoon figure a lower bound."""
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(LOADER_FIXTURE))
    assert series.losses[2018] == 64200.0, "6416 must not be summed in"
    assert "typhoon_heavy_rain" in series.notes and "[2018]" in series.notes
    assert "lower bound" in series.notes


def test_loader_drops_a_thousandfold_year_and_never_rescales_it() -> None:
    """The 2026 통계연보 vintage publishes its newest year in 천원 under a 백만원 header (§5-B-1).

    The correct multiplier is a guess until it is checked against 재해연보, so the year is
    excluded and the exclusion is recorded — not silently divided by 1000.
    """
    payload = _envelope(
        _wide_row(2018, tot=141284, typhoon=64200)
        + _wide_row(2019, tot=216226, typhoon=212778)
        + _wide_row(2020, tot=1318177, typhoon=222541)
        + _wide_row(2024, tot=910713075, typhoon=106342)
    )
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(payload))
    assert 2024 not in series.losses
    assert series.observed_period == (2018, 2020)
    assert "[2024] dropped" in series.notes and "천원" in series.notes
    assert "106342" not in series.notes, "the anomalous value must not be carried forward"


def test_loader_refuses_two_rows_for_one_year_rather_than_choosing() -> None:
    """Live responses carry one row per year at ``seq=1``; a second row's meaning is unknown."""
    payload = _envelope(
        _wide_row(2020, seq="1", tot=1318177, typhoon=222541)
        + _wide_row(2020, seq="2", tot=999, typhoon=999)
    )
    with pytest.raises(okr.YearbookUnavailable, match="two rows for 2020"):
        okr.fetch_rows(key="fixture", fetch=_stub(payload))


def test_loader_rejects_a_gateway_error_envelope() -> None:
    """Omitting ``type=xml`` returns this under HTTP 200 — it must not parse as an empty page."""
    envelope = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>HTTP_ERROR</errMsg>"
        b"<returnReasonCode>04</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    with pytest.raises(okr.YearbookUnavailable, match="error envelope"):
        okr.parse_rows(envelope)


def test_loader_rejects_malformed_and_non_numeric_responses() -> None:
    with pytest.raises(okr.YearbookUnavailable, match="not XML"):
        okr.parse_rows(b"<not xml")
    with pytest.raises(okr.YearbookUnavailable, match="not an integer"):
        okr.parse_rows(
            _envelope("<row><wrttimeid>2020</wrttimeid><seq>1</seq><tot>n/a</tot></row>")
        )
    with pytest.raises(okr.YearbookUnavailable, match="wrttimeid"):
        okr.parse_rows(_envelope("<row><wrttimeid>x</wrttimeid><seq>1</seq></row>"))


def test_loader_refuses_a_peril_with_no_cause_column() -> None:
    with pytest.raises(okr.YearbookUnavailable, match="no cause column"):
        okr.load_year_series("river_flood", key="fixture", fetch=_stub(LOADER_FIXTURE))


def test_loader_names_the_key_variable_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(okr.KEY_ENV, raising=False)
    with pytest.raises(okr.YearbookUnavailable, match=okr.KEY_ENV):
        okr.fetch_rows()


def test_loader_output_still_fails_the_calibration_gate() -> None:
    """Having the observed data is not permission to calibrate on it.

    A national aggregate covering wind+surge+rain, in KRW millions, against a wind-only
    point-asset model in USD fails on units, coverage and scope — all three reported.
    """
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(LOADER_FIXTURE))
    report = val.comparability_report(
        observed=series,
        modelled_unit="USD",
        modelled_covers=("wind",),
        modelled_scope="",
        is_original_subset=False,
        hazard_label="TC IBTrACS",
    )
    assert report["comparison_status"] == val.COMPARISON_NOT_COMPARABLE
    assert report["calibration_allowed"] is False
    joined = " | ".join(report["blockers"])
    assert "unit mismatch" in joined
    assert "missing rain, surge" in joined
    assert "scope undeclared" in joined


def test_only_the_subperil_blocker_survives_fixing_units_and_scope() -> None:
    """Units and scope are plumbing and can be fixed; the coverage gap is physics and cannot."""
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub(LOADER_FIXTURE))
    report = val.comparability_report(
        observed=series,
        modelled_unit="KRW million",
        modelled_covers=("wind",),
        modelled_scope="national:KOR",
        is_original_subset=True,
    )
    assert report["calibration_allowed"] is False
    assert report["blockers"] == [
        "observed aggregates sub-perils the model does not carry: missing rain, surge"
    ]
