"""``docs/DATA_SOURCES.md`` is generated from the source registry — it must not drift.

The registry (``assets/libraries/data_sources.json``) is what the fetch/ingest code reads,
so the document is only useful if every registered source appears in it and no download
instruction contradicts the loaders.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "assets" / "libraries" / "data_sources.json"
DOC = REPO / "docs" / "DATA_SOURCES.md"


def _sources() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["sources"]


def test_every_registered_source_is_listed() -> None:
    doc = DOC.read_text(encoding="utf-8")
    missing = [s["id"] for s in _sources() if f"({s['url']})" not in doc]
    assert not missing, f"absent from DATA_SOURCES.md: {missing}"


def test_the_heat_hazard_input_is_registered_and_is_the_daily_mean() -> None:
    """E-OBS was the heat perils' main input while absent from the registry."""
    eobs = next((s for s in _sources() if s["id"] == "eobs"), None)
    assert eobs is not None, "E-OBS must be registered — the Data tab fetches from this list"
    assert "tg_ens_mean" in eobs["download_url"], "daily mean (tg), not daily max (tx)"
    assert eobs["dest"] == "climada"


def test_no_document_still_asks_for_the_daily_max_file() -> None:
    """The metric switch of 2026-09-09 left three documents telling users to fetch tx."""
    for rel in (
        "README.md",
        "docs/HEATWAVE_EUROPE.md",
        "docs/CLIMADA_METHODS.md",
        "docs/DATA_SOURCES.md",
        "docs/USER_GUIDE.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "tx_ens_mean" not in text, f"{rel} still points at the daily-max file"


def test_the_gated_sources_are_marked_as_needing_a_person() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "🔑" in doc
    for gated in ("climate.go.kr/atlas/ana/cdd", "kosis.kr/openapi", "data.go.kr/data/15107316"):
        assert gated in doc, f"{gated} must be listed as a gated source"
