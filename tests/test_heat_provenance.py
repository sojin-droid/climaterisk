"""The heat-mortality model must state what it is: custom, uncited, uncalibrated.

These tests police *metadata and wording*, never numbers. They exist because the model was
described in docs and UI as literature-informed and epidemiologically calibrated when no
external source is recorded for any vulnerability parameter — see
``docs/HEAT_MORTALITY_PROVENANCE.md``.

CLIMADA-free.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import heat_mortality as hm  # noqa: E402

PROVENANCE_DOC = REPO / "docs" / "HEAT_MORTALITY_PROVENANCE.md"


# --------------------------------------------------------------------------- #
# The records themselves                                                      #
# --------------------------------------------------------------------------- #
def test_every_record_is_classified_and_carries_a_location() -> None:
    assert hm.PARAMETER_PROVENANCE, "the inventory must not be empty"
    for r in hm.PARAMETER_PROVENANCE:
        assert r.provenance in hm.PROVENANCE_CLASSES, f"{r.parameter}: {r.provenance}"
        assert "::" in r.location or r.location.endswith(".py"), r.parameter
        assert r.value.strip() and r.unit.strip() and r.rationale.strip(), r.parameter


def test_a_record_without_a_source_says_so_rather_than_implying_one() -> None:
    """An unsupported value must be visibly unsupported, not silently plausible."""
    for r in hm.PARAMETER_PROVENANCE:
        if r.provenance in ("indicative", "unknown"):
            assert r.citation == "source unavailable", (
                f"{r.parameter} is {r.provenance} but cites {r.citation!r}"
            )
        if r.provenance == "external":
            assert r.citation != "source unavailable", r.parameter


def test_the_vulnerability_parameters_actually_in_use_are_inventoried() -> None:
    """Guard against a constant being added to the model without a provenance record."""
    names = " ".join(r.parameter.lower() for r in hm.PARAMETER_PROVENANCE)
    for needed in ("beta, under 65", "beta, 65 and over", "baseline daily mortality"):
        assert needed in names
    for band in hm.AGE_BANDS:
        rendered = " ".join(r.value for r in hm.PARAMETER_PROVENANCE)
        assert f"{band.beta:g}" in rendered, f"beta {band.beta} of {band.key} not inventoried"
    # the band-width coefficients and the reference table must both be accounted for
    assert any("band-width" in r.parameter for r in hm.PARAMETER_PROVENANCE)
    assert any("reference city" in r.parameter for r in hm.PARAMETER_PROVENANCE)


def test_the_recorded_values_match_the_constants_they_document() -> None:
    """The inventory documents; it must not drift from the code it documents."""
    by_name = {r.parameter: r for r in hm.PARAMETER_PROVENANCE}
    assert by_name["beta, under 65"].value == f"{hm.band_by_key('u65').beta:.3f}"
    assert by_name["beta, 65 and over"].value == f"{hm.band_by_key('o65').beta:.3f}"
    assert by_name["share_over65 for Korea"].value == f"{hm.COUNTRY_SHARE_OVER65['KOR']:.3f}"
    kor = hm.BASELINE_MORTALITY_PER_100K["KOR"]
    assert by_name["baseline mortality, under 65, Korea"].value == f"{kor['u65']:.1f}"
    assert by_name["baseline mortality, 65 and over, Korea"].value == f"{kor['o65']:.1f}"
    legacy = hm.LEGACY_BASELINE_PER_100K
    assert by_name[
        "baseline daily mortality, under 65 (legacy fallback outside Korea)"
    ].value.startswith(f"{legacy['u65']:.1f}")
    assert by_name[
        "baseline daily mortality, 65 and over (legacy fallback outside Korea)"
    ].value.startswith(f"{legacy['o65']:.1f}")
    assert by_name["age-share ceiling"].value == f"{hm.MAX_SHARE_OVER65:.2f}"
    assert by_name["mmt_high (comfort-band upper edge) per reference city"].value.startswith(
        f"{len(hm.REF_CITIES)} values"
    )


# --------------------------------------------------------------------------- #
# The summary the runner and the UI consume                                   #
# --------------------------------------------------------------------------- #
def test_summary_names_climada_as_container_only_and_denies_calibration() -> None:
    s = hm.provenance_summary()
    assert s["calibrated_on_observed_mortality"] is False
    assert "custom" in s["model"].lower()
    assert "impactcalc" in s["model"].lower().replace(" ", "")
    assert "not calibrated" in s["label"].lower()
    assert s["n_parameters"] == len(hm.PARAMETER_PROVENANCE)
    assert sum(s["counts"].values()) == s["n_parameters"]


def test_most_parameters_are_reported_as_unsupported() -> None:
    """Not a target — a statement of the current state that must stay visible."""
    c = hm.provenance_summary()["counts"]
    assert c["indicative"] + c["unknown"] > c["external"] + c["internal_fit"]


# --------------------------------------------------------------------------- #
# Wording: no claim the code cannot back                                      #
# --------------------------------------------------------------------------- #
OVERCLAIMS = (
    r"literature-informed",
    r"literature-based",
    r"epidemiologically calibrated",
    r"empirically derived",
    r"CLIMADA standard",
    r"CLIMADA heat[- ]mortality (function|impact function)",
)
CHECKED_FILES = (
    "docs/HEATWAVE_EUROPE.md",
    "docs/METHODOLOGY.md",
    "docs/HEAT_MORTALITY_PROVENANCE.md",
    "worker/climaterisk_worker/heat_mortality.py",
    "scripts/heatwave_europe.py",
    "frontend/climaterisk/src/views/ResultsView.tsx",
)


@pytest.mark.parametrize("rel", CHECKED_FILES)
def test_heat_documents_and_code_make_no_unsupported_claim(rel: str) -> None:
    text = (REPO / rel).read_text(encoding="utf-8")
    for pattern in OVERCLAIMS:
        hits = [m.group(0) for m in re.finditer(pattern, text, re.IGNORECASE)]
        assert not hits, f"{rel}: unsupported claim {hits}"


def test_the_provenance_document_exists_and_lists_every_parameter() -> None:
    doc = PROVENANCE_DOC.read_text(encoding="utf-8")
    assert "source unavailable" in doc
    for r in hm.PARAMETER_PROVENANCE:
        assert r.parameter in doc, f"{r.parameter} missing from {PROVENANCE_DOC.name}"


def test_the_ui_shows_the_model_status() -> None:
    view = (REPO / "frontend" / "climaterisk" / "src" / "views" / "ResultsView.tsx").read_text()
    assert "parameter_status" in view and "Model status" in view
    types = (REPO / "frontend" / "climaterisk" / "src" / "types.ts").read_text()
    assert "parameter_status" in types


# --------------------------------------------------------------------------- #
# KOSIS-anchored baseline mortality (2026-09-11) — baseline only, nothing else  #
# --------------------------------------------------------------------------- #
def test_korean_baseline_mortality_is_the_kosis_2024_value() -> None:
    assert hm.baseline_mortality_per_100k("KOR", "u65") == 163.2
    assert hm.baseline_mortality_per_100k("KOR", "o65") == 2928.4
    bands = {b.key: b for b in hm.age_bands_for("KOR")}
    # unit: deaths per 100,000 person-years -> per person per day
    assert bands["u65"].baseline_daily_mortality == pytest.approx(163.2 / 1e5 / 365.0)
    assert bands["o65"].baseline_daily_mortality == pytest.approx(2928.4 / 1e5 / 365.0)
    assert hm.baseline_mortality_per_100k("kor", "o65") == 2928.4  # case-insensitive ISO3


def test_kosis_provenance_metadata_is_complete_and_names_the_derivation() -> None:
    by_name = {r.parameter: r for r in hm.PARAMETER_PROVENANCE}
    cases = (
        ("baseline mortality, under 65, Korea", "under 65", "true"),
        ("baseline mortality, 65 and over, Korea", "65 and over (objL3=63)", "false"),
    )
    for name, age, derived in cases:
        r = by_name[name]
        assert r.provenance == "external" and "DT_1B34E01" in r.citation, name
        assert r.unit == "deaths per 100,000 person-years"
        md = dict(r.metadata)
        for key in ("source", "table", "year", "sex", "cause", "age", "unit", "derivation"):
            assert md.get(key), f"{name}: metadata {key} missing"
        assert md["source"] == "KOSIS" and md["table"] == "DT_1B34E01" and md["year"] == "2024"
        assert md["cause"].startswith("all causes") and md["sex"].startswith("total")
        assert md["age"] == age
        assert md["derived_from_published_totals"] == derived
    # the derivation of the under-65 rate is arithmetic on published figures, stated as such
    u65 = dict(by_name["baseline mortality, under 65, Korea"].metadata)
    assert "358,569" in u65["derivation"] and "291,539" in u65["derivation"]


def test_countries_without_a_cited_table_keep_the_legacy_baseline() -> None:
    """Anchoring Korea must not move Spain — or any run with no country."""
    for country in (None, "", "ESP", "global"):
        bands = {b.key: b for b in hm.age_bands_for(country)}
        assert bands["u65"].baseline_daily_mortality == pytest.approx(130.0 / 1e5 / 365.0)
        assert bands["o65"].baseline_daily_mortality == pytest.approx(4500.0 / 1e5 / 365.0)
        assert hm.baseline_mortality_version(country) == hm.LEGACY_BASELINE_VERSION
    assert hm.age_bands_for(None) == hm.AGE_BANDS
    assert hm.baseline_mortality_version("KOR") == "KOSIS-2024 mortality baseline"
    # the legacy pair is still inventoried — and still says it has no source
    legacy = [r for r in hm.PARAMETER_PROVENANCE if "legacy fallback" in r.parameter]
    assert len(legacy) == 2 and all(r.provenance == "indicative" for r in legacy)


def test_anchoring_the_baseline_changed_nothing_else() -> None:
    """The scope of the change is the two baseline rates. Everything else is pinned here."""
    assert hm.band_by_key("u65").beta == 0.010 and hm.band_by_key("o65").beta == 0.034
    assert {b.key: b.beta for b in hm.age_bands_for("KOR")} == {"u65": 0.010, "o65": 0.034}
    assert hm.COUNTRY_SHARE_OVER65["KOR"] == 0.203  # resident-registration share, not unified
    assert hm.MMT_ANNUAL_MEAN_SLOPE == 0.8 and hm.MMT_SD_SLOPE == 1.0
    assert hm.HEAT_ONSET_PERCENTILE["KOR"] == 93.0
    by_name = {r.parameter: r for r in hm.PARAMETER_PROVENANCE}
    assert by_name["beta, under 65"].provenance == "indicative"
    assert by_name["beta, 65 and over"].provenance == "indicative"


def test_summary_reports_the_baseline_version_per_country_without_claiming_calibration() -> None:
    kor, esp, none = (
        hm.provenance_summary("KOR"),
        hm.provenance_summary("ESP"),
        hm.provenance_summary(),
    )
    assert kor["baseline_mortality"]["anchored"] is True
    assert "KOSIS" in kor["baseline_mortality"]["version"]
    assert kor["baseline_mortality"]["o65_per_100k"] == 2928.4
    assert (
        esp["baseline_mortality"]["anchored"] is False
        and none["baseline_mortality"]["anchored"] is False
    )
    assert esp["baseline_mortality"]["o65_per_100k"] == 4500.0
    # a cited baseline is not a calibration, and the label must keep saying so
    assert kor["calibrated_on_observed_mortality"] is False
    assert "not calibrated" in kor["label"].lower()
    assert "not validated" in kor["detail"]
    # the pre-existing contract fields are all still there
    for key in ("model", "counts", "n_parameters", "label", "detail"):
        assert key in kor and key in none
    assert kor["counts"] == none["counts"]  # the inventory does not depend on the run's country
