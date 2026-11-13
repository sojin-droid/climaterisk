"""Guard against committing API keys.

The repository is public. GitHub secret scanning only knows provider formats (AWS, GitHub, ...);
Korean public-data keys (data.go.kr, KOSIS, 도로명주소, V-World, KEPCO, 팜맵) have no registered
pattern, so a leaked one would not be flagged. This test scans every tracked text file instead.

Two checks:
1. Every ``*_KEY`` / ``*_TOKEN`` / ``*_SECRET`` variable in a tracked env template is blank.
2. No tracked text assigns a long literal to a key-like name, and no URL carries a key parameter
   with a literal value (``serviceKey=…``, ``apiKey=…``, ``confmKey=…``, ``crtfc_key=…``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# A literal secret: 20+ characters of the alphabets keys use. Placeholders such as
# "발급받은 키", "<your-key>" or "${VAR}" never match.
_LITERAL = r"[A-Za-z0-9%+/=_-]{20,}"
_ASSIGN = re.compile(
    r"""(?ix)\b([a-z_]*(?:api_?key|service_?key|secret|token|_key))\b\s*[:=]\s*["']"""
    + _LITERAL
    + r"""["']"""
)
_URL_PARAM = re.compile(
    r"(?i)[?&](?:service_?key|api_?key|confm_?key|crtfc_key|auth_?key)=" + _LITERAL
)
_ENV_LINE = re.compile(r"^\s*([A-Z0-9_]*(?:_KEY|_TOKEN|_SECRET))\s*=\s*(\S+)")

# Files whose literals are deliberately fake keys; add a path here only with a reason.
_ALLOWED: set[str] = {
    "tests/test_korea_status.py",  # asserts the status card never echoes a made-up secret
}
# snake_case identifiers ("allow_incomparable_regions") are names, not keys.
_IDENTIFIER = re.compile(r"""[:=]\s*["'][a-z][a-z0-9_]*["']""")
_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".gz",
    ".nc",
    ".tif",
    ".tiff",
    ".npz",
    ".npy",
    ".pkl",
    ".xlsx",
    ".xls",
    ".hdf5",
    ".h5",
    ".parquet",
    ".woff",
    ".woff2",
    ".ico",
}


def _tracked_text_files() -> list[Path]:
    """Return tracked files that are plausibly text."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    paths = [ROOT / p for p in out.decode().split("\0") if p]
    return [
        p
        for p in paths
        if p.is_file() and p.suffix.lower() not in _BINARY_SUFFIXES and p.stat().st_size < 5_000_000
    ]


def test_env_templates_have_blank_keys() -> None:
    """Every key-like variable in a tracked env template is empty or commented out."""
    offenders = []
    for path in _tracked_text_files():
        if not path.name.startswith(".env") and not path.name.endswith(".env.example"):
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            m = _ENV_LINE.match(line)
            if m:
                offenders.append(f"{path.relative_to(ROOT)}:{n} {m.group(1)}")
    assert not offenders, f"key variables with a value in a tracked env file: {offenders}"


def test_no_literal_keys_in_tracked_files() -> None:
    """No tracked file assigns a literal key or puts one in a URL query."""
    offenders = []
    for path in _tracked_text_files():
        rel = str(path.relative_to(ROOT))
        if rel in _ALLOWED or rel == "tests/test_no_secrets.py":
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if (_ASSIGN.search(line) and not _IDENTIFIER.search(line)) or _URL_PARAM.search(line):
                offenders.append(f"{rel}:{n}")  # location only — never echo the value
    assert not offenders, f"possible literal API keys: {offenders[:20]}"


@pytest.mark.parametrize(
    ("line", "hit"),
    [
        ('VWORLD_KEY = "12345678-ABCD-ABCD-ABCD-123456789ABC"', True),
        ("url = f'https://x/api?serviceKey=abcdefghijklmnopqrstuvwxyz0123'", True),
        ('API_KEY = os.environ["VWORLD_API_KEY"]', False),
        ('export VWORLD_API_KEY="발급받은 키"', False),
        ("params = {'serviceKey': key}", False),
        ('ALLOW_INCOMPARABLE_KEY = "allow_incomparable_regions"', False),
    ],
)
def test_patterns(line: str, hit: bool) -> None:
    """The detectors catch literal keys and ignore env lookups and placeholders."""
    found = (_ASSIGN.search(line) and not _IDENTIFIER.search(line)) or _URL_PARAM.search(line)
    assert bool(found) is hit
