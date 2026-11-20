"""Dataset catalog: ids, required fields, links into code, docs and paths."""

from __future__ import annotations

import ast
import json
import warnings
from pathlib import Path

import pytest

from climaterisk import paths
from climaterisk.data import catalog

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "assets" / "libraries"
ROLES = {
    "hazard_main",
    "hazard_check",
    "hazard_reference",
    "supplementary",
    "exposure",
    "boundary",
    "vulnerability",
    "validation",
    "other",
}
STRING_FIELDS = (
    "label_ko",
    "label_en",
    "role",
    "path",
    "format",
    "spatial_resolution",
    "temporal",
    "coverage",
    "source",
    "licence",
    "storage_rule",
)
CODE_KEYS = {"hazard_dataset", "dataset", "dataset_family"}


def _raw_rows() -> list[dict]:
    return json.loads((LIB / "dataset_catalog.json").read_text(encoding="utf-8"))["datasets"]


def test_ids_unique_and_identifiers() -> None:
    rows = _raw_rows()
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    for i in ids:
        assert i.isidentifier(), i
        assert i == i.lower(), i
    assert catalog.ids() == ids


def test_required_fields_present_and_non_empty() -> None:
    for row in _raw_rows():
        for field in STRING_FIELDS:
            value = row.get(field)
            assert isinstance(value, str) and value.strip(), (row["id"], field)
        assert row["role"] in ROLES, row["id"]
        assert isinstance(row["perils"], list), row["id"]
        assert isinstance(row["used_by"], list), row["id"]
        assert "path_fn" in row and "data_sources_id" in row and "notes" in row, row["id"]
        assert row["path_fn"] is None or isinstance(row["path_fn"], str)
        assert isinstance(row["notes"], str)


def test_method_registry_datasets_resolve() -> None:
    registry = json.loads((LIB / "method_registry.json").read_text(encoding="utf-8"))
    found: list[str] = []
    for tag in registry["tags"].values():
        for main in tag["main"]:
            if main.get("dataset"):
                found.append(main["dataset"])
    assert found, "registry has no dataset strings"
    for name in found:
        assert catalog.dataset(name).id == name


def _literal_dataset_strings(source: str) -> list[tuple[str, int, str]]:
    """String constants assigned to hazard_dataset / dataset / dataset_family."""
    hits: list[tuple[str, int, str]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # other modules' escapes are not ours
        tree = ast.parse(source)
    for node in ast.walk(tree):
        pairs: list[tuple[str, ast.expr]] = []
        if isinstance(node, ast.keyword) and node.arg in CODE_KEYS:
            pairs.append((node.arg, node.value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                if name in CODE_KEYS:
                    pairs.append((name, node.value))
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if isinstance(k, ast.Constant) and k.value in CODE_KEYS:
                    pairs.append((str(k.value), v))
        for name, value in pairs:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                hits.append((name, node.lineno, value.value))
    return hits


def test_code_dataset_literals_are_catalog_ids() -> None:
    known = set(catalog.ids())
    checked = 0
    bad: list[str] = []
    for base in (REPO / "worker", REPO / "src"):
        for py in base.rglob("*.py"):
            for name, line, value in _literal_dataset_strings(py.read_text(encoding="utf-8")):
                checked += 1
                if value not in known:
                    bad.append(f"{py.relative_to(REPO)}:{line} {name}={value!r}")
    assert checked >= 10, "scanner found too few literals; it is probably broken"
    assert not bad, "dataset strings missing from the catalog:\n" + "\n".join(bad)


def test_path_fn_exists_in_paths_module() -> None:
    for row in _raw_rows():
        if row["path_fn"] is not None:
            assert callable(getattr(paths, row["path_fn"], None)), (row["id"], row["path_fn"])


def test_data_sources_ids_exist() -> None:
    sources = json.loads((LIB / "data_sources.json").read_text(encoding="utf-8"))["sources"]
    known = {s["id"] for s in sources}
    for row in _raw_rows():
        if row["data_sources_id"] is not None:
            assert row["data_sources_id"] in known, (row["id"], row["data_sources_id"])


def test_dataset_unknown_id_suggests_close_match() -> None:
    with pytest.raises(KeyError, match="floodmap_rfm_depth_class"):
        catalog.dataset("floodmap_rfm_depth_clas")


@pytest.fixture
def tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    monkeypatch.delenv(paths.ENV_CLIMADA_DATA_DIR, raising=False)
    paths.clear_cache()
    yield tmp_path
    paths.clear_cache()


def test_dataset_path_resolves_against_data_root(tmp_root: Path) -> None:
    assert catalog.dataset_path("khoa_ahhw_connected_bathtub") == (
        tmp_root / "external/khoa/tidebed_harmonics_20250812.csv"
    )
    # a glob resolves to the directory before it
    assert catalog.dataset_path("kma_mkprism_ta_daily") == tmp_root / "external" / "kma"
    assert catalog.dataset_path("floodmap_rfm_depth_class") == tmp_root / "external/other/floodmap"
    assert catalog.dataset_path("korea_municipalities") == (
        paths.REPO_ROOT / "assets/libraries/korea_municipalities.csv"
    )
    assert catalog.dataset_path("climada_rf_isimip_150arcsec") == (
        paths.climada_data_dir() / "hazard/river_flood"
    )


def test_on_disk_false_for_missing_and_true_for_present(tmp_root: Path) -> None:
    assert not catalog.on_disk("khoa_ahhw_connected_bathtub")
    assert not catalog.on_disk("kma_mkprism_ta_daily")
    target = tmp_root / "external" / "khoa"
    target.mkdir(parents=True)
    (target / "tidebed_harmonics_20250812.csv").write_text("x", encoding="utf-8")
    assert catalog.on_disk("khoa_ahhw_connected_bathtub")
    kma = tmp_root / "external" / "kma"
    kma.mkdir(parents=True)
    (kma / "MKPRISM_MKPRISMv31_TA_gridraw_daily_2000.nc").write_text("x", encoding="utf-8")
    assert catalog.on_disk("kma_mkprism_ta_daily")
    assert not catalog.on_disk("kma_mkprism_tamax_daily")  # TAMAX must not match the TA glob


def test_repo_files_are_on_disk() -> None:
    for i in catalog.ids():
        if catalog.dataset(i).path.startswith(catalog.REPO_PREFIX):
            assert catalog.on_disk(i), i
