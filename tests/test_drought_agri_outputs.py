"""Agricultural drought v0: configuration, distribution choice and output schema.

Not the reviewer's gate (``tests/test_drought_agri.py``). The output-schema test reads the
built files named by ``DROUGHT_AGRI_CHECK_DIR`` and skips when it is unset.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
da = pytest.importorskip("climaterisk_worker.drought_agri")


def _write(tmp_path: Path, cfg: dict) -> Path:
    path = tmp_path / "drought_agri.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_config_loads_and_has_documented_values() -> None:
    cfg = da.load_config()
    assert cfg["spei"]["scale_months"] == 3
    assert cfg["spei"]["reference_years"] == [1991, 2020]
    assert cfg["spei"]["season_end_months"] == [5, 6, 7, 8, 9]
    assert [s["spei_le"] for s in cfg["stages"]] == [-1.0, -1.5, -2.0]
    assert cfg["farmland_classes"]["01"] == "rice"
    assert "06" in cfg["excluded_classes"]
    assert "저수율" in cfg["unevaluated_classes"]["rice"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["spei"].update(scale_months=0),
        lambda c: c["spei"].update(reference_years=[2020, 1991]),
        lambda c: c["spei"].update(season_end_months=[0, 13]),
        lambda c: c["stages"][1].update(spei_le=-0.5),
        lambda c: c["stages"][0].update(spei_le=0.5),
        lambda c: c["farmland_classes"].pop("01"),
    ],
)
def test_config_rejects_invalid(tmp_path: Path, mutate) -> None:
    cfg = copy.deepcopy(json.loads(da.config_path().read_text(encoding="utf-8")))
    mutate(cfg)
    with pytest.raises(ValueError):
        da.load_config(_write(tmp_path, cfg))


def test_stage_follows_config_thresholds() -> None:
    s = np.array([-0.99, -1.0, -1.49, -1.5, -2.0, np.nan])
    np.testing.assert_array_equal(da.stage(s), [0, 1, 1, 2, 3, -1])
    np.testing.assert_array_equal(da.stage(s, thresholds=[-0.5]), [1, 1, 1, 1, 1, -1])


def test_dry_tail_loglogistic_undefined_gev_fitted() -> None:
    """A long dry tail (negative skew) breaks the log-logistic; the GEV fits it."""
    rng = np.random.default_rng(11)
    years = 200
    months = np.tile(np.arange(1, 13), years)
    u = rng.uniform(1e-6, 1 - 1e-6, months.size)
    d = 50.0 - 30.0 * (u / (1 - u)) ** (1 / 5.0)  # mirror image of a log-logistic
    ref = np.ones(months.size, bool)
    ll: dict[str, int] = {}
    s_ll = da.spei(d, months, 1, ref, distribution="loglogistic", diagnostics=ll)
    assert ll["undefined"] == 12 and np.isnan(s_ll).all()
    gv: dict[str, int] = {}
    s = da.spei(d, months, 1, ref, distribution="gev", diagnostics=gv)
    assert gv["fitted"] == 12 and gv["undefined"] == 0 and np.isfinite(s).all()
    assert abs(float(np.mean(s))) < 0.1 and abs(float(np.std(s)) - 1.0) < 0.15
    order = np.argsort(d[months == 3])
    assert np.all(np.diff(s[months == 3][order]) >= -1e-9)


def test_simplify_regions_is_identity_at_zero() -> None:
    gpd = pytest.importorskip("geopandas")
    from climaterisk_worker import board
    from shapely.geometry import box

    regions = gpd.GeoDataFrame({"a": [1]}, geometry=[box(127, 37, 127.1, 37.1)], crs=4326)
    assert board.simplify_regions(regions, 0.0) is regions
    out = board.simplify_regions(regions, 50.0)
    assert out.crs.to_epsg() == 4326 and len(out) == 1


def _drought_dir() -> Path:
    """Built outputs: the conftest isolates the data root, so point at them explicitly with
    ``DROUGHT_AGRI_CHECK_DIR=~/Data/climaterisk/derived/drought``."""
    return Path(os.environ.get("DROUGHT_AGRI_CHECK_DIR", "/nonexistent")).expanduser()


def test_output_schema_if_built() -> None:
    out = _drought_dir()
    meta_path = out / "drought_agri.meta.json"
    if not meta_path.is_file():
        pytest.skip("drought outputs not built (scripts/drought_agri.py)")
    pd = pytest.importorskip("pandas")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for key in ("sources", "resolution", "reference_period", "assumptions", "farmmap"):
        assert key in meta
    for key in ("parcels_read", "parcels_assigned", "parcels_excluded_by_code"):
        assert key in meta["farmmap"]
    assert any("representative point" in a for a in meta["assumptions"])
    pieces = pd.read_csv(out / "pieces.csv")
    expected = [
        "id", "code", "name", "rice_m2", "upland_m2", "orchard_m2", "greenhouse_m2",
        "freq_stage_ge1", "freq_stage_ge2", "freq_stage_ge3", "has_hazard_data",
    ]  # fmt: skip
    for col in expected:
        assert col in pieces.columns
    freq_cols = ["freq_stage_ge1", "freq_stage_ge2", "freq_stage_ge3"]
    freq = pieces.loc[pieces["has_hazard_data"], freq_cols]
    assert ((freq >= 0) & (freq <= 1)).all().all()
    assert (freq["freq_stage_ge1"] >= freq["freq_stage_ge2"]).all()
    assert (freq["freq_stage_ge2"] >= freq["freq_stage_ge3"]).all()
    py = pd.read_csv(out / "piece_year.csv")
    assert list(py.columns) == ["piece_id", "year", "min_spei3_season", "stage"]
    assert py["year"].min() == 1991 and py["year"].max() == 2025
    summ = pd.read_csv(out / "sigungu_summary.csv")
    assert "rice_ha" in summ.columns and summ["rice_note"].str.contains("미평가").all()
    assert "expected_affected_ha_stage_ge2" in summ.columns
    assert not any("spei" in c.lower() and "mean" in c.lower() for c in summ.columns)
    yr = pd.read_csv(out / "sigungu_year_affected.csv")
    assert "upland_orchard_stage_ge2_ha" in yr.columns
    # sum over pieces: 시군구 farmland equals the sum of its pieces
    by = pieces.groupby("code")["upland_m2"].sum() / 1e4
    got = summ.set_index("code")["upland_ha"]
    np.testing.assert_allclose(got.reindex(by.index).to_numpy(), by.to_numpy(), rtol=1e-9)
