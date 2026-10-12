"""The backend serves the built UI itself (Windows bundle), and the bundle launcher agrees
with the settings it relies on. CLIMADA-free."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from climaterisk.api.deps import get_session_store
from climaterisk.api.main import create_app
from climaterisk.config import Settings, get_settings

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "scripts" / "windows_bundle"


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>climaterisk</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def test_built_ui_is_served_under_the_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIMATERISK_UI_DIR", str(_dist(tmp_path)))
    get_settings.cache_clear()
    get_session_store.cache_clear()
    try:
        client = TestClient(create_app())
        assert client.get("/api/health").status_code == 200  # the API keeps precedence
        root = client.get("/")
        assert root.status_code == 200 and "climaterisk" in root.text
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/api/does-not-exist").status_code == 404
    finally:
        get_settings.cache_clear()
        get_session_store.cache_clear()


def test_ui_dir_defaults_only_to_an_existing_build_and_refuses_a_typo(tmp_path: Path) -> None:
    default = Settings(ui_dir=None).resolve_ui_dir()
    dist = REPO / "frontend" / "climaterisk" / "dist"
    assert (default is not None) == (dist / "index.html").is_file()
    with pytest.raises(FileNotFoundError, match=r"index\.html"):
        Settings(ui_dir=tmp_path / "nope").resolve_ui_dir()
    assert Settings(ui_dir=_dist(tmp_path)).resolve_ui_dir() == tmp_path / "dist"


def test_bundle_launcher_only_sets_settings_the_backend_knows() -> None:
    """Every CLIMATERISK_* the launcher sets must be a Settings field or a paths env var."""
    from climaterisk import paths

    text = (BUNDLE / "start.ps1").read_text(encoding="utf-8")
    used = set(re.findall(r"CLIMATERISK_([A-Z0-9_]+)", text))
    known = (
        {name.upper() for name in Settings.model_fields}
        | {v.removeprefix("CLIMATERISK_") for v in paths.WORKER_ENV_VARS}
        | {"NO_BROWSER"}  # launcher-only: skip opening the browser (smoke tests)
    )
    unknown = sorted(used - known)
    assert not unknown, f"start.ps1 sets settings the backend does not read: {unknown}"
    # the launcher relies on these exact mechanisms
    assert "CLIMATERISK_WORKER_PYTHON" in text and "CLIMATERISK_UI_DIR" in text
    assert 'PYTHONPATH = (Join-Path $App "src")' in text  # repo root = app\ (paths.REPO_ROOT)
    assert "conda-unpack" in text
    for f in ("Start climaterisk.bat", "README-WINDOWS.md"):
        assert (BUNDLE / f).is_file(), f
    packager = (REPO / "scripts" / "make_windows_bundle.ps1").read_text(encoding="utf-8")
    for name in ("start.ps1", "Start climaterisk.bat", "README-WINDOWS.md"):
        assert name in packager, f"packager does not ship {name}"
