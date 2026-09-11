"""``scripts/tc_capture_kr.py`` — the TC capture diagnostic.

Everything here drives the pure metrics layer: no CLIMADA, no network, no live API. The
observed series is rebuilt through the real F1 loader but against a synthetic XML fixture, so
the loading contract is exercised without depending on data.go.kr being up.

The point of these tests is not that the numbers are right — it is that the diagnostic cannot
quietly become a calibration: the coverage mismatch stays visible, the correlation carries its
own "do not interpret" verdict, and no absolute capture percentage can be produced.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "worker") not in sys.path:
    sys.path.insert(0, str(REPO / "worker"))

from climaterisk_worker import observed_kr as okr  # noqa: E402


def _load_script():  # type: ignore[no-untyped-def]
    """Import the analysis script by path — it must import without CLIMADA installed."""
    spec = importlib.util.spec_from_file_location(
        "tc_capture_kr", REPO / "scripts" / "tc_capture_kr.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A @dataclass resolves its annotations through sys.modules, so the module must be
    # registered before exec_module or the decorator raises on the first field.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tcc = _load_script()

FIXTURE_SOURCE = "synthetic unit-test fixture (not 재해연보 data)"


def _row(year: int, seq: str = "1", **cols: int) -> str:
    values = {"tot": 0, **dict.fromkeys(okr.CAUSE_COLUMNS, 0), **cols}
    body = "".join(f"<{k}>{v}</{k}>" for k, v in values.items())
    return f"<row><wrttimeid>{year}</wrttimeid><seq>{seq}</seq>{body}</row>"


#: Eight years shaped like the live response — one per year, one of them zero.
FIXTURE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<NaturalDisasterDamageByYear>
  <head><totalCount>8</totalCount><numOfRows>100</numOfRows><pageNo>1</pageNo>
    <RESULT><resultCode>INFO-0</resultCode><resultMsg>NOMAL SERVICE</resultMsg></RESULT>
  </head>
  {_row(2016, tot=288862, typhoon=200000)}
  {_row(2017, tot=187302, typhoon=0)}
  {_row(2018, tot=141284, typhoon=60000)}
  {_row(2019, tot=216226, typhoon=200000)}
  {_row(2020, tot=1318177, typhoon=220000)}
  {_row(2021, tot=66054, typhoon=20000)}
  {_row(2022, tot=592656, typhoon=240000)}
  {_row(2023, tot=958221, typhoon=50000)}
</NaturalDisasterDamageByYear>""".encode()


def _stub(payload: bytes = FIXTURE_XML):  # type: ignore[no-untyped-def]
    def fetch(page: int = 1, rows: int = 100, key: str | None = None, timeout: int = 60) -> bytes:
        if page == 1:
            return payload
        return (
            b'<?xml version="1.0"?><NaturalDisasterDamageByYear>'
            b"<head><totalCount>0</totalCount></head></NaturalDisasterDamageByYear>"
        )

    return fetch


#: A modelled series that stops in 2020, like the observed-track hazard does.
MODEL_LOSSES = {
    2016: 4.0e-05,
    2017: 0.0,
    2018: 1.7e-04,
    2019: 1.3e-05,
    2020: 9.4e-04,
}


def _diag():  # type: ignore[no-untyped-def]
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub())
    return series, tcc.diagnose(series.losses, MODEL_LOSSES)


# --- loading and window ------------------------------------------------------------------


def test_observed_series_loads_eight_years_through_the_f1_loader() -> None:
    series = okr.load_year_series("tropical_cyclone", key="fixture", fetch=_stub())
    assert series.observed_period == (2016, 2023)
    assert series.n_years == 8 and series.n_nonzero_years == 7
    assert series.unit == "KRW million" and series.scope == "national:KOR"


def test_comparison_window_is_the_year_intersection_and_records_what_it_dropped() -> None:
    """The observed series runs to 2023; the observed-track hazard stops in 2020."""
    _series, diag = _diag()
    assert diag.years == [2016, 2017, 2018, 2019, 2020]
    assert diag.correlations["n"] == 5
    assert diag.provenance["observed_years_excluded"] == [2021, 2022, 2023]
    assert diag.provenance["model_years_excluded"] == []


def test_a_year_both_series_report_as_zero_is_kept_not_dropped() -> None:
    _series, diag = _diag()
    i = diag.years.index(2017)
    assert diag.observed[i] == 0.0 and diag.modelled[i] == 0.0
    assert diag.observed_stats["n_zero_years"] == 1
    assert diag.model_stats["n_zero_years"] == 1
    assert 2017 in diag.discrepancies["agreed_zero_years"]


# --- metrics -----------------------------------------------------------------------------


def test_ranks_are_dense_with_one_as_largest_and_ties_share_a_rank() -> None:
    assert tcc.ranks([5.0, 1.0, 3.0]) == [1, 3, 2]
    assert tcc.ranks([2.0, 2.0, 1.0]) == [1, 1, 2]


def test_distribution_stats_are_scale_free_where_they_claim_to_be() -> None:
    """CV, max-year share and the non-zero spread must not move when the series is rescaled."""
    base = [1.0, 0.0, 4.0, 2.0]
    a = tcc.distribution_stats(base)
    b = tcc.distribution_stats([v * 1e6 for v in base])
    for key in ("cv", "max_year_share", "nonzero_max_min"):
        assert math.isclose(a[key], b[key], rel_tol=1e-9), key
    assert a["n_zero_years"] == 1
    assert math.isclose(a["max_year_share"], 4.0 / 7.0)
    assert math.isclose(a["nonzero_max_min"], 4.0)


def test_max_year_share_and_cv_capture_concentration() -> None:
    flat = tcc.distribution_stats([1.0, 1.0, 1.0, 1.0])
    spiky = tcc.distribution_stats([1.0, 1.0, 1.0, 100.0])
    assert math.isclose(flat["cv"], 0.0, abs_tol=1e-12)
    assert spiky["cv"] > flat["cv"]
    assert spiky["max_year_share"] > flat["max_year_share"]


def test_variability_inflation_is_the_ratio_of_the_two_cvs() -> None:
    _series, diag = _diag()
    expected = diag.model_stats["cv"] / diag.observed_stats["cv"]
    assert math.isclose(diag.variability_inflation, expected, rel_tol=1e-12)
    assert diag.variability_inflation > 1.0, "the modelled series is the more concentrated one"


def test_a_five_year_window_is_reported_as_not_interpretable() -> None:
    _series, diag = _diag()
    corr = diag.correlations
    assert corr["n"] == 5 and corr["interpretable"] is False
    assert "Do not interpret as statistically significant" in corr["note"]
    for key in ("pearson", "pearson_p", "spearman", "spearman_p"):
        assert isinstance(corr[key], float)


def test_a_long_enough_window_is_not_flagged() -> None:
    years = list(range(2000, 2010))
    obs = {y: float(i + 1) for i, y in enumerate(years)}
    mod = {y: float(i + 1) * 2 for i, y in enumerate(years)}
    diag = tcc.diagnose(obs, mod)
    assert diag.correlations["n"] == 10 and diag.correlations["interpretable"] is True
    assert "Do not interpret" not in diag.correlations["note"]


def test_discrepancy_years_are_found_from_the_data_not_hardcoded() -> None:
    """Reversing which year dominates must move the reported year with it."""
    years = [2001, 2002, 2003]
    obs = dict.fromkeys(years, 10.0) | {2001: 100.0}
    mod = dict.fromkeys(years, 10.0) | {2003: 100.0}
    d = tcc.diagnose(obs, mod).discrepancies
    assert d["model_most_over_concentrated"]["year"] == 2003
    assert d["model_most_under_weighted"]["year"] == 2001
    flipped = tcc.diagnose(mod, obs).discrepancies
    assert flipped["model_most_over_concentrated"]["year"] == 2001


def test_normalized_model_losses_stay_fractions_never_currency() -> None:
    _series, diag = _diag()
    assert all(0.0 <= v < 1.0 for v in diag.modelled)
    rows = tcc.annual_rows(diag)
    assert set(rows[0]) == {
        "year",
        "observed_loss_KRW_million",
        "model_loss_normalized",
        "observed_rank",
        "model_rank",
    }


# --- the guards that keep this a diagnostic ------------------------------------------------


def test_coverage_mismatch_is_declared_in_the_exported_summary(tmp_path: Path) -> None:
    _series, diag = _diag()
    tcc.write_report(diag, tmp_path)
    payload = json.loads((tmp_path / "distribution_summary.json").read_text(encoding="utf-8"))
    assert payload["observed_covers_subperils"] == ["wind", "surge", "rain"]
    assert payload["model_covers_subperils"] == ["wind"]
    assert payload["coverage_mismatch"] == ["rain", "surge"]
    assert payload["calibration"] == "BLOCKED"


def test_absolute_capture_rate_is_never_reported(tmp_path: Path) -> None:
    """There is no citable Korean asset stock, so the percentage must not exist anywhere."""
    _series, diag = _diag()
    assert diag.absolute_capture_rate == "NOT ESTIMATED"
    paths = tcc.write_report(diag, tmp_path)
    payload = json.loads((tmp_path / "distribution_summary.json").read_text(encoding="utf-8"))
    assert payload["absolute_capture_rate"] == "NOT ESTIMATED"
    assert payload["absolute_capture_rate_reason"]
    markdown = (tmp_path / "diagnostic_summary.md").read_text(encoding="utf-8")
    assert "ABSOLUTE CAPTURE RATE = NOT ESTIMATED" in markdown
    assert {p.name for p in paths} == {
        "annual_comparison.csv",
        "distribution_summary.json",
        "diagnostic_summary.md",
    }


def test_the_summary_states_the_coverage_caveats_and_claims_no_more() -> None:
    _series, diag = _diag()
    markdown = tcc.summary_markdown(diag)
    assert "Diagnostic, not calibration" in markdown
    assert "wind + surge + rain" in markdown and "WIND-ONLY" in markdown
    assert "COVERAGE gap" in markdown
    for banned in (
        "accurately predicts",
        "is validated",
        "capture rate is",
        "reproduces Korean typhoon damage",
    ):
        assert banned.lower() not in markdown.lower(), banned


def test_an_empty_overlap_is_refused_rather_than_reported() -> None:
    with pytest.raises(ValueError, match="share no year"):
        tcc.diagnose({2016: 1.0}, {2030: 1.0})


def test_pure_correlations_match_scipy() -> None:
    """The hand-rolled Pearson/Spearman and their p-values must equal SciPy's.

    They are hand-rolled so the diagnostic layer runs without SciPy (the backend environment
    has none). That is only acceptable if it agrees with the reference implementation, so this
    test pins it wherever SciPy is installed.
    """
    stats = pytest.importorskip("scipy.stats")
    cases = [
        ([214965.0, 0.0, 64200.0, 212778.0, 222541.0], [3.9e-05, 0.0, 1.7e-04, 1.3e-05, 9.4e-04]),
        ([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]),
        ([5.0, 5.0, 1.0, 9.0, 2.0, 2.0, 7.0], [1.0, 3.0, 3.0, 8.0, 2.0, 5.0, 6.0]),
    ]
    for observed, modelled in cases:
        got = tcc.correlations(observed, modelled)
        pear = stats.pearsonr(observed, modelled)
        spear = stats.spearmanr(observed, modelled)
        assert math.isclose(got["pearson"], float(pear.statistic), rel_tol=1e-10)
        assert math.isclose(got["pearson_p"], float(pear.pvalue), rel_tol=1e-8)
        assert math.isclose(got["spearman"], float(spear.statistic), rel_tol=1e-10)
        assert math.isclose(got["spearman_p"], float(spear.pvalue), rel_tol=1e-8)
