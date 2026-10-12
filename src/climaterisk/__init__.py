"""climaterisk — map-first climate physical- and transition-risk analysis platform.

The orchestration backend owns the model (per-session SQLite), serves the bundled
methodology libraries, runs the lightweight transition-risk math, and submits
physical-risk runs to a separate CLIMADA worker process. It deliberately imports
no geospatial / CLIMADA code (that lives in the conda-based ``worker/`` package).
"""

from importlib.metadata import PackageNotFoundError, version


def _version_from_pyproject() -> str:
    """The version in ``pyproject.toml`` when the package is not installed.

    The Windows bundle runs the package un-installed (``PYTHONPATH=app\\src``), so no
    distribution metadata exists; the file next to the sources is the authority then.
    """
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    except OSError:
        return "0.0.0+unknown"
    return f"{match.group(1)}+source" if match else "0.0.0+unknown"


try:
    __version__ = version("climaterisk")
except PackageNotFoundError:  # not installed (a source checkout, the Windows bundle)
    __version__ = _version_from_pyproject()
