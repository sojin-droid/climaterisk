"""Gate tests for the KDCA heat-illness check (design 2026-11-16 §4.2-§4.3, §6).

Written before the implementation (Opus gate). Pure functions live in
``worker/climaterisk_worker/heat_validation.py``; the station daily series in
``worker/climaterisk_worker/heat_utci.py``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "worker", ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from climaterisk_worker import heat_validation as hv  # noqa: E402

RULES = {"v1_min_rho": 0.5, "v1_margin": 0.02, "v2_margin": 0.02, "v3_margin": 0.10}


def _config() -> dict:
    path = ROOT / "assets" / "libraries" / "physical_risk_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 시도 names                                                                     #
# --------------------------------------------------------------------------- #
def test_sixteen_regions_sejong_merged() -> None:
    assert len(hv.SIDO16) == 16 and len(set(hv.SIDO16)) == 16
    assert "세종" not in hv.SIDO16


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("서울특별시", "서울"),
        ("경기도", "경기"),
        ("강원도", "강원"),
        ("강원특별자치도", "강원"),
        ("전라북도", "전북"),
        ("전북특별자치도", "전북"),
        ("제주특별자치도", "제주"),
        ("제주도", "제주"),
        ("세종특별자치시", "충남"),  # merged: 세종 split from 충남 in 2012
        ("충청남도", "충남"),
        ("경상남도", "경남"),
        (" 대구광역시 ", "대구"),
    ],
)
def test_normalise_sido(raw: str, want: str) -> None:
    assert hv.normalise_sido(raw) == want


@pytest.mark.parametrize("raw", ["", "   ", "알수없음", None])
def test_normalise_sido_unknown_is_none(raw: str | None) -> None:
    assert hv.normalise_sido(raw) is None


def test_station_sido_from_address() -> None:
    assert hv.station_sido("강원도 강릉시 용강동 123") == "강원"
    assert hv.station_sido("세종특별자치시 연기면") == "충남"
    assert hv.station_sido("") is None


def test_station_sido_merged_gwangju_jeonnam_by_district() -> None:
    # KMA station table writes the 2026 merged name; 2011-2019 KDCA rows keep 광주 / 전남 apart
    assert hv.station_sido("전남광주통합특별시 북구 운암동") == "광주"
    assert hv.station_sido("전남광주통합특별시 광산구 신촌동") == "광주"
    assert hv.station_sido("전남광주통합특별시 목포시 연산동") == "전남"
    assert hv.station_sido("전남광주통합특별시") is None
    assert hv.normalise_sido("전남광주통합특별시") is None  # ambiguous without the district


def test_station_sido_drops_mountain_tag() -> None:
    assert hv.station_sido("(산지)강원특별자치도 평창군 대관령면 횡계리") == "강원"


# --------------------------------------------------------------------------- #
# Loading the KDCA file                                                        #
# --------------------------------------------------------------------------- #
def _write_kdca(tmp_path: Path) -> Path:
    p = tmp_path / "kdca.csv"
    p.write_text(
        "﻿발생일자,성별,나이,발생시도,발생시군구,실내외구분,발생장소\n"
        "2011-07-01,남자,70,경기도,,실외,실외 작업장\n"
        "2011-07-01,여자,45,강원특별자치도,,실외,논밭\n"
        ",,,,,,\n"
        "2013-08-02,남자,60,세종특별자치시,,실외,논밭\n"
        "2019-08-03,여자,80,서울특별시,종로구,실내,집\n"
        "2019-08-03,남자,30,,,실외,길가\n",
        encoding="utf-8",
    )
    return p


def test_load_kdca_drops_blank_rows_and_counts_them(tmp_path: Path) -> None:
    cases, meta = hv.load_kdca(_write_kdca(tmp_path))
    assert meta["n_rows"] == 6
    assert meta["n_blank"] == 1
    assert meta["n_unknown_sido"] == 1  # date present, region missing: kept out, counted
    assert len(cases) == 4
    assert list(cases.columns) == ["date", "sido"]
    assert cases["sido"].tolist() == ["경기", "강원", "충남", "서울"]
    assert str(cases["date"].dtype).startswith("datetime64")


def test_daily_counts_has_zero_days_and_all_regions(tmp_path: Path) -> None:
    cases, _ = hv.load_kdca(_write_kdca(tmp_path))
    c = hv.daily_counts(cases, (2011, 2011), [7])
    assert c.shape == (31, 16)
    assert list(c.columns) == list(hv.SIDO16)
    assert c.loc[pd.Timestamp("2011-07-01"), "경기"] == 1
    assert c.loc[pd.Timestamp("2011-07-02"), "경기"] == 0
    assert int(c.to_numpy().sum()) == 2  # 2013 and 2019 rows outside the window


# --------------------------------------------------------------------------- #
# Statistics                                                                   #
# --------------------------------------------------------------------------- #
def test_spearman_ignores_nan_pairs_and_constant_is_none() -> None:
    assert hv.spearman([1, 2, 3, np.nan], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert hv.spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None
    assert hv.spearman([1, 2], [1, 2]) is None  # fewer than 3 pairs


def test_weighted_national_renormalises_over_missing_regions() -> None:
    idx = pd.to_datetime(["2015-07-01", "2015-07-02"])
    ind = pd.DataFrame({"서울": [30.0, np.nan], "부산": [20.0, 24.0]}, index=idx)
    pop = pd.DataFrame({"서울": [300.0], "부산": [100.0]}, index=[2015])
    out = hv.weighted_national(ind, pop)
    assert out.iloc[0] == pytest.approx((30 * 300 + 20 * 100) / 400)
    assert out.iloc[1] == pytest.approx(24.0)  # 서울 missing: weight goes to 부산 alone


def test_rate_per_100k_is_mean_of_yearly_rates() -> None:
    counts = pd.DataFrame({"서울": [10, 30]}, index=[2011, 2012])
    pop = pd.DataFrame({"서울": [1_000_000, 2_000_000]}, index=[2011, 2012])
    r = hv.rate_per_100k(counts, pop)
    assert r["서울"] == pytest.approx((1.0 + 1.5) / 2)


def test_annual_from_daily() -> None:
    days = pd.date_range("2011-05-01", "2011-09-30", freq="D")
    u = np.full(days.size, 30.0)
    u[:10] = 39.0  # 10 days >= 38
    t = np.full(days.size, 25.0)
    t[:5] = 34.0  # 5 days >= 33
    df = pd.DataFrame({"utci_max": u, "tmax_grid": t}, index=days)
    a = hv.annual_from_daily(df, (2011, 2011), min_day_fraction=0.95)
    assert a.loc[2011, "d38"] == 10
    assert a.loc[2011, "heat_days"] == 5
    assert a.loc[2011, "t95"] == pytest.approx(np.percentile(t, 95))


def test_annual_from_daily_incomplete_year_is_nan_not_zero() -> None:
    days = pd.date_range("2012-05-01", "2012-09-30", freq="D")
    u = np.full(days.size, 30.0)
    u[: days.size // 2] = np.nan
    df = pd.DataFrame({"utci_max": u, "tmax_grid": np.full(days.size, 25.0)}, index=days)
    a = hv.annual_from_daily(df, (2012, 2012), min_day_fraction=0.95)
    assert np.isnan(a.loc[2012, "d38"])


# --------------------------------------------------------------------------- #
# Decision (pre-registered, design §4.2 table)                                 #
# --------------------------------------------------------------------------- #
def _v(u1: float | None, t1: float | None, u2: float | None, t2: float | None, u3, t95, h) -> tuple:
    return (
        {"rho_u": u1, "rho_t": t1},
        {"median_u": u2, "median_t": t2},
        {"rho_u": u3, "rho_t95": t95, "rho_h": h},
    )


def test_decide_switches_when_all_pass() -> None:
    d = hv.decide(*_v(0.70, 0.70, 0.40, 0.41, 0.30, 0.35, 0.40), RULES)
    assert d["switch"] is True
    assert all(d["items"][k]["pass"] for k in ("V1", "V2", "V3"))


def test_decide_v1_floor_is_inclusive() -> None:
    assert hv.decide(*_v(0.50, 0.50, 0.5, 0.5, 0.5, 0.5, 0.5), RULES)["items"]["V1"]["pass"] is True
    assert (
        hv.decide(*_v(0.49, 0.40, 0.5, 0.5, 0.5, 0.5, 0.5), RULES)["items"]["V1"]["pass"] is False
    )


def test_decide_v1_margin_against_tmax() -> None:
    # rho_u >= rho_t - 0.02
    assert hv.decide(*_v(0.68, 0.70, 0.5, 0.5, 0.5, 0.5, 0.5), RULES)["items"]["V1"]["pass"] is True
    assert (
        hv.decide(*_v(0.67, 0.70, 0.5, 0.5, 0.5, 0.5, 0.5), RULES)["items"]["V1"]["pass"] is False
    )


def test_decide_v2_margin() -> None:
    assert hv.decide(*_v(0.7, 0.7, 0.30, 0.32, 0.5, 0.5, 0.5), RULES)["items"]["V2"]["pass"] is True
    assert (
        hv.decide(*_v(0.7, 0.7, 0.29, 0.32, 0.5, 0.5, 0.5), RULES)["items"]["V2"]["pass"] is False
    )


def test_decide_v3_against_best_of_tmax95_and_heat_days() -> None:
    # rho_u >= max(rho_t95, rho_h) - 0.10
    assert (
        hv.decide(*_v(0.7, 0.7, 0.5, 0.5, 0.30, 0.20, 0.40), RULES)["items"]["V3"]["pass"] is True
    )
    d = hv.decide(*_v(0.7, 0.7, 0.5, 0.5, 0.29, 0.20, 0.40), RULES)
    assert d["items"]["V3"]["pass"] is False and d["switch"] is False
    assert any("V3" in r for r in d["reasons"])


def test_decide_missing_metric_fails_never_passes() -> None:
    d = hv.decide(*_v(None, 0.7, 0.5, 0.5, 0.5, 0.5, 0.5), RULES)
    assert d["items"]["V1"]["pass"] is False and d["switch"] is False
    d = hv.decide(*_v(0.7, 0.7, 0.5, 0.5, 0.5, None, None), RULES)
    assert d["items"]["V3"]["pass"] is False


def test_config_validation_rules_match_the_design() -> None:
    v = _config()["heat_utci"]["validation"]
    assert {k: v[k] for k in RULES} == RULES
    assert v["years"] == [2011, 2019]
    assert v["months"] == [5, 6, 7, 8, 9]


def test_recorded_result_reproduces_the_switch() -> None:
    """The flip of heat_utci.enabled must follow from the recorded numbers and the rules."""
    c = _config()["heat_utci"]
    res = c["validation"]["result"]
    d = hv.decide(res["V1"], res["V2"], res["V3"], c["validation"])
    assert d["switch"] is res["switch"] is c["enabled"]


def test_adapter_detail_carries_the_validation() -> None:
    pytest.importorskip("climada")
    from climaterisk_worker.physical_risk import adapters

    detail = adapters.KoreaLocalUtciAdapter("rcp85", 2050).describe().detail or ""
    assert "KDCA" in detail and "Supplementary result (2026-11-16, not a validation)" in detail


# --------------------------------------------------------------------------- #
# Station daily series (heat_utci) — real data opt-in                          #
# --------------------------------------------------------------------------- #
REAL_ROOT = Path(
    os.environ.get("CLIMATERISK_REAL_DATA_ROOT", str(Path.home() / "Data/climaterisk"))
)
_HAVE = (REAL_ROOT / "external" / "kma" / "asos_hourly" / "108").is_dir() and (
    REAL_ROOT / "cache" / "heat_days" / "obs_2000_2019_m5-9_b0.05.npz"
).is_file()


@pytest.mark.skipif(not _HAVE, reason="KMA data not on disk")
def test_station_daily_series_seoul(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pythermalcomfort")
    from climaterisk_worker import heat_utci as hu

    monkeypatch.setenv("CLIMATERISK_DATA_ROOT", str(REAL_ROOT))
    monkeypatch.delenv("CLIMATERISK_KMA_DIR", raising=False)
    out = hu.station_daily_series(_config()["heat_utci"], stations=[108])
    df = out[108]
    assert list(df.columns) == ["utci_max", "tmax_grid"]
    assert df.index.min() == pd.Timestamp("2000-05-01") and df.index.max() == pd.Timestamp(
        "2019-09-30"
    )
    a = hv.annual_from_daily(df, (2000, 2019), min_day_fraction=0.95)
    # the yearly D38 from the daily series agrees with the asset run at the station
    want = json.loads(
        (ROOT / "tests" / "baselines" / "heat_utci_seoul108.json").read_text(encoding="utf-8")
    )
    got = float(np.nanmean(a["d38"].to_numpy(dtype=float)))
    assert got == pytest.approx(want["utci_days_ge38"], abs=1.0)
