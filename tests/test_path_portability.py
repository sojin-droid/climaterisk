"""Path portability — the checkout and the data root are relocatable; nothing depends on the cwd.

CLIMADA-free. Pins the rules of ``climaterisk.paths``:

* the repository root is found from the code, not from the working directory;
* ``CLIMATERISK_DATA_ROOT`` resolves env → repo ``.env`` → ``~/Data/climaterisk``, with ``~``
  expanded and relative values anchored at the repo root;
* KMA and municipality data resolve under the data root, never under CLIMADA's directory;
* legacy locations are reported, not read (the hazard catalogue is the one transitional read);
* missing optional data yields a status, not a traceback;
* scripts run from a foreign working directory;
* no runtime file names an obsolete Windows path or the old ``~/climada`` layout.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402

VARS = (
    paths.ENV_DATA_ROOT,
    paths.ENV_KMA_DIR,
    paths.ENV_CLIMADA_DATA_DIR,
    paths.ENV_HAZARD_DB,
)


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No path variables in the environment and an empty .env; returns a fresh data root dir."""
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(paths, "env_file", lambda: tmp_path / "absent.env")
    paths.clear_cache()
    root = tmp_path / "dataroot"
    root.mkdir()
    return root


def test_the_suite_never_resolves_the_real_data_root() -> None:
    """conftest isolates CLIMATERISK_DATA_ROOT — no test may write into ~/Data/climaterisk."""
    assert os.environ.get(paths.ENV_DATA_ROOT)
    assert paths.data_root() != paths.default_data_root()


def test_repo_root_is_found_from_the_code_not_the_cwd(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert paths.REPO_ROOT == REPO
    assert (paths.REPO_ROOT / "pyproject.toml").is_file()
    monkeypatch.chdir(tmp_path)
    assert paths.REPO_ROOT == REPO
    assert paths.env_file() == tmp_path / "absent.env"  # patched; real one is REPO/.env


def test_data_root_default_env_tilde_and_relative(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert paths.data_root() == Path.home() / "Data" / "climaterisk"
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    assert paths.data_root() == isolated
    monkeypatch.setenv(paths.ENV_DATA_ROOT, "~/somewhere/cr")
    assert paths.data_root() == Path.home() / "somewhere" / "cr"
    monkeypatch.setenv(paths.ENV_DATA_ROOT, "rel/data")
    assert paths.data_root() == REPO / "rel" / "data"  # repo-anchored, not cwd-anchored
    monkeypatch.setenv(paths.ENV_DATA_ROOT, "   ")
    assert paths.data_root() == Path.home() / "Data" / "climaterisk"  # blank = unset


def test_dotenv_value_is_used_when_the_environment_is_silent(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / "repo.env"
    env.write_text(
        f'# comment\nexport {paths.ENV_DATA_ROOT}="{isolated}"\nOTHER=1\n', encoding="utf-8"
    )
    monkeypatch.setattr(paths, "env_file", lambda: env)
    paths.clear_cache()
    assert paths.data_root() == isolated
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path / "from-env"))
    assert paths.data_root() == tmp_path / "from-env"  # process env wins


def test_changing_cwd_does_not_move_anything(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    before = (
        paths.data_root(),
        paths.kma_dir(),
        paths.municipality_sgis_dir(),
        paths.hazard_db_dir(),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    after = (
        paths.data_root(),
        paths.kma_dir(),
        paths.municipality_sgis_dir(),
        paths.hazard_db_dir(),
    )
    assert before == after


def test_named_locations_live_under_the_data_root(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    assert paths.kma_dir() == isolated / "external" / "kma"
    assert paths.municipality_sgis_dir() == isolated / "external" / "municipality" / "sgis"
    assert paths.municipality_source_dir() == isolated / "external" / "municipality" / "source"
    assert paths.municipality_office_dir() == isolated / "external" / "municipality" / "office"
    assert paths.derived_municipality_dir() == isolated / "derived" / "municipality"
    assert paths.floodmap_dir() == isolated / "external" / "other" / "floodmap"
    assert paths.cache_dir() == isolated / "cache" and paths.evidence_dir() == isolated / "evidence"
    # none of the project's own data defaults to CLIMADA's directory
    climada = paths.climada_data_dir()
    for p in (paths.kma_dir(), paths.municipality_sgis_dir(), paths.canonical_hazard_db()):
        assert climada not in p.parents
    monkeypatch.setenv(paths.ENV_KMA_DIR, str(isolated / "custom_kma"))
    assert paths.kma_dir() == isolated / "custom_kma"


def test_hazard_catalogue_transitional_rule(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    legacy = tmp_path / "legacy_repo_data" / "hazard_db"
    monkeypatch.setattr(paths, "legacy_repo_hazard_db", lambda: legacy)
    assert paths.hazard_db_dir() == isolated / "derived" / "hazard_db"  # fresh machine
    legacy.mkdir(parents=True)
    (legacy / "catalog.json").write_text("{}", encoding="utf-8")
    assert paths.hazard_db_dir() == legacy  # un-migrated checkout keeps serving
    assert paths.status()["hazard_db"]["status"] == "LEGACY_REPO_LOCATION"
    canonical = isolated / "derived" / "hazard_db"
    canonical.mkdir(parents=True)
    (canonical / "catalog.json").write_text("{}", encoding="utf-8")
    assert paths.hazard_db_dir() == canonical  # migrated: canonical wins
    monkeypatch.setenv(paths.ENV_HAZARD_DB, str(tmp_path / "explicit"))
    assert paths.hazard_db_dir() == tmp_path / "explicit"


def test_status_reports_missing_and_legacy_without_raising(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    legacy_kma = tmp_path / "old_climada" / "kma"
    legacy_kma.mkdir(parents=True)
    (legacy_kma / "AR6_SSP585_5ENSMN_skorea_TA_gridraw_daily_2021_2030_asc.tar.gz").write_bytes(
        b"x"
    )
    monkeypatch.setattr(
        paths,
        "legacy_locations",
        lambda: {
            "kma": legacy_kma,
            "municipality_sgis": tmp_path / "nope1",
            "floodmap": tmp_path / "nope2",
            "hazard_db": tmp_path / "nope3",
        },
    )
    st = paths.status(with_sizes=True)
    assert st["data_root"] == str(isolated) and st["data_root_source"] == "env/.env"
    kma = st["locations"]["kma"]
    assert kma["status"] == "LEGACY_LOCATION" and kma["legacy_path"] == str(legacy_kma)
    assert "migrate_data_root.py" in kma["hint"] and kma["present"] is False
    assert st["locations"]["municipality_source"]["status"] == "missing"
    assert st["locations"]["evidence"]["bytes"] is None


def test_korea_status_points_at_the_canonical_kma_dir_and_flags_legacy(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("pydantic_settings")
    from climaterisk.data import korea_status as ks

    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    legacy_kma = tmp_path / "old" / "kma"
    legacy_kma.mkdir(parents=True)
    (legacy_kma / "MKPRISM_MKPRISMv31_TA_gridraw_daily_2000_2019_nc.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(paths, "legacy_locations", lambda: {"kma": legacy_kma})
    assert ks.kma_dir() == isolated / "external" / "kma"
    info = ks._kma_legacy(ks.kma_dir(), [])
    assert info["legacy_file_count"] == 1 and "no longer read" in info["migration_hint"]
    assert ks._kma_files(ks.kma_dir()) == []  # the legacy file is not read


def test_worker_receives_the_resolved_roots(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    env = paths.worker_env()
    assert set(env) == set(paths.WORKER_ENV_VARS)
    assert env[paths.ENV_DATA_ROOT] == str(isolated)
    assert env[paths.ENV_KMA_DIR] == str(isolated / "external" / "kma")


def test_settings_read_the_repo_dotenv_whatever_the_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("pydantic_settings")
    from climaterisk.config import Settings

    env_file = Settings.model_config.get("env_file")
    assert env_file is not None and Path(str(env_file)).is_absolute()
    assert Path(str(env_file)) == REPO / ".env"


def test_scripts_run_from_a_foreign_working_directory(tmp_path: Path) -> None:
    env = {**os.environ, paths.ENV_DATA_ROOT: str(tmp_path / "dr")}
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "migrate_data_root.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert f"repo root : {REPO}" in out.stdout and "No files were changed." in out.stdout
    assert not (tmp_path / "dr").exists()  # the plan creates nothing
    help_ = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "physical_risk_batch.py"), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert help_.returncode == 0, help_.stderr


#: Runtime / configuration files that must not name obsolete locations.
_SCAN_ROOTS = ("src", "worker", "scripts", "frontend/climaterisk/src", "assets/libraries", "tests")
_SCAN_FILES = (".env.example", "pyproject.toml", ".claude/launch.json")
_SCAN_EXT = {".py", ".ts", ".tsx", ".js", ".json", ".toml", ".yml", ".yaml", ".sh", ".cfg", ".ini"}
_FORBIDDEN = (
    re.compile(r"C:\\\\Users\\\\|C:\\Users\\"),
    re.compile("새 폴더"),
    re.compile(r"~/climada"),
)


def _runtime_files() -> list[Path]:
    out: list[Path] = [REPO / f for f in _SCAN_FILES if (REPO / f).is_file()]
    for root in _SCAN_ROOTS:
        for p in (REPO / root).rglob("*"):
            if (
                p.is_file()
                and p.suffix in _SCAN_EXT
                and "node_modules" not in p.parts
                and "__pycache__" not in p.parts
            ):
                out.append(p)
    return [p for p in out if p.resolve() != Path(__file__).resolve()]


def test_no_runtime_file_names_an_obsolete_path() -> None:
    offenders = []
    for p in _runtime_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in _FORBIDDEN:
            if pat.search(text):
                offenders.append(f"{p.relative_to(REPO)}: {pat.pattern}")
    assert not offenders, offenders


def test_only_the_paths_module_spells_the_climada_default() -> None:
    pat = re.compile(r'Path\.home\(\)\s*/\s*"climada"')
    hits = [
        str(p.relative_to(REPO))
        for p in _runtime_files()
        if pat.search(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert hits == ["src/climaterisk/paths.py"], hits


def _load_migrator():  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "migrate_data_root", REPO / "scripts" / "migrate_data_root.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_migrator_links_and_copies_without_touching_the_legacy_tree(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(isolated))
    old = tmp_path / "old"
    kma = old / "kma"
    kma.mkdir(parents=True)
    (kma / "MKPRISM_MKPRISMv31_TA_gridraw_daily_2000_2019_nc.tar.gz").write_bytes(b"k")
    sgis = old / "municipality_src"
    (sgis / "extracted").mkdir(parents=True)
    (sgis / "extracted" / "bnd_sido_00_2025_2Q.shp").write_bytes(b"s")
    (sgis / "sgis_admin_2025.zip").write_bytes(b"z")
    (sgis / "page.html").write_text("debris", encoding="utf-8")
    flood = old / "floodmap"
    flood.mkdir()
    (flood / "RFM.zip").write_bytes(b"f")
    hz = tmp_path / "repo_data" / "hazard_db"
    hz.mkdir(parents=True)
    (hz / "catalog.json").write_text("{}", encoding="utf-8")
    legacy = {"kma": kma, "municipality_sgis": sgis, "floodmap": flood, "hazard_db": hz}
    monkeypatch.setattr(paths, "legacy_locations", lambda: legacy)
    before = sorted(str(p.relative_to(old)) for p in old.rglob("*"))

    mig = _load_migrator()
    assert mig.main(["--apply", "--method", "symlink", "--only", "kma,municipality_sgis"]) == 0
    assert mig.main(["--apply", "--method", "copy", "--only", "hazard_db"]) == 0

    canon_kma = isolated / "external" / "kma"
    assert canon_kma.is_symlink() and canon_kma.resolve() == kma.resolve()
    canon_sgis = isolated / "external" / "municipality" / "sgis"
    assert canon_sgis.is_dir() and not canon_sgis.is_symlink()  # a real folder of links
    assert (canon_sgis / "bnd_sido_00_2025_2Q.shp").is_symlink()
    assert (canon_sgis / "sgis_admin_2025.zip").is_symlink()
    assert not (canon_sgis / "page.html").exists()  # debris outside "extracted" is not linked
    canon_hz = isolated / "derived" / "hazard_db"
    assert (canon_hz / "catalog.json").is_file() and not canon_hz.is_symlink()
    assert paths.hazard_db_dir() == canon_hz
    assert not (isolated / "external" / "other" / "floodmap").exists()  # never migrated
    # the legacy tree is byte-for-byte unchanged, nothing written into it
    assert sorted(str(p.relative_to(old)) for p in old.rglob("*")) == before
    # idempotent: a second run changes nothing
    assert mig.main(["--apply", "--method", "symlink", "--only", "kma,municipality_sgis"]) == 0
    assert sorted(str(p.relative_to(old)) for p in old.rglob("*")) == before
