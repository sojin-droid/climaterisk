"""Korea readiness status — what is on this machine, read from disk, never inferred.

Backs ``GET /api/status/korea`` and the "현황 체크" card on the Data tab. Everything here is a
*reading* of local state: which KMA archives sit in the drop folder, which KOR hazard layers
the catalog manifest lists and what their labels say, which credentials are present (by name
and length only — never the value), and whether the CLIMADA worker environment exists.

It deliberately does **not** import the worker package or CLIMADA (the GPL boundary:
``docs/ARCHITECTURE.md``), does not open HDF5 files, and does not parse the KMA archives —
file discovery here is by name only, so it can run in the backend process with the standard
library. The loader that actually reads those files is ``worker/climaterisk_worker/kma_scenario``.

The grades follow ``docs/KOREA_1KM_IMPLEMENTATION_GAP.md``: ``5km-current`` for a layer built
from KMA and stored at 0.05°, ``missing`` for perils with no KOR layer, ``1km-data-only`` for
perils whose KMA input exists but has no consumer. ``1km-ready`` is never emitted here: its
fifth condition (metadata recording the actual resolution) is not met by any layer today, and
this module has no way to verify the first four without opening the HDF5.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from climaterisk.config import REPO_ROOT, get_settings
from climaterisk.data.hazard_catalog import read_catalog

#: Default drop folder of the KMA loader (mirrors ``kma_scenario._HOME_KMA``).
_HOME_KMA = Path.home() / "climada" / "data" / "kma"

#: Discovery-only grammar for KMA archive/member names. Looser than the loader's on purpose:
#: this lists what is present; it does not decide what can be read.
_KMA_NAME = re.compile(
    r"^(?P<prefix>AR6|MKPRISM)_(?P<scen>[A-Za-z0-9]+)_(?:(?P<model>(?!skorea_)[A-Za-z0-9]+)_)?"
    r"(?:skorea_)?(?P<var>[A-Z]+)_gridraw_(?P<step>daily|monthly|yearly)_"
    r"(?P<y0>\d{4})(?:_(?P<y1>\d{4}))?(?:_(?P<fmt>nc|asc))?\.(?P<ext>nc|txt|tar\.gz)$"
)

#: The seven 기후요소 the 남한상세 grid offers; only ``TA`` has a consumer in the code.
KMA_VARIABLES: tuple[str, ...] = ("TA", "TAMAX", "TAMIN", "RN", "RHM", "WS", "SI")
CONSUMED_VARIABLES: tuple[str, ...] = ("TA",)

#: Credentials the platform can use, checked by presence only.
CREDENTIAL_ENV: tuple[tuple[str, str], ...] = (
    ("CLIMATERISK_DATAGOKR_KEY", "data.go.kr (재해연보 15107318 · 15107316)"),
    ("CLIMATERISK_KOSIS_KEY", "KOSIS 사망원인통계 (기저사망률 재조회용)"),
    ("CLIMATERISK_EMDAT_PATH", "EM-DAT CSV (보정 관측 계열)"),
)

#: Perils whose KMA input exists on the portal but has no consumer in the code — graded
#: ``1km-data-only`` (docs/KOREA_1KM_PHYSICAL_RISK_COVERAGE.md §12).
_KMA_DATA_ONLY: tuple[str, ...] = (
    "heat_stress",
    "extreme_precipitation",
    "tc_rain",
    "drought",
    "cold_wave",
)


def kma_dir() -> Path:
    """The KMA drop folder: ``CLIMATERISK_KMA_DIR`` when set, else ``~/climada/data/kma``."""
    env = os.environ.get("CLIMATERISK_KMA_DIR")
    return Path(env) if env else _HOME_KMA


def _kma_files(directory: Path) -> list[dict[str, Any]]:
    """Archives and NetCDFs present in the drop folder, parsed by name only."""
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        m = _KMA_NAME.match(path.name)
        if not m:
            continue
        y0 = int(m["y0"])
        out.append(
            {
                "file": path.name,
                "variable": m["var"],
                "scenario_token": m["scen"],
                "model": m["model"],
                "years": [y0, int(m["y1"]) if m["y1"] else y0],
                "format": m["fmt"] or ("nc" if m["ext"] == "nc" else "txt"),
                "kind": "archive" if m["ext"] == "tar.gz" else "member",
                "size_mb": round(path.stat().st_size / 1e6, 1),
            }
        )
    return out


def _scenario_tokens(source: str) -> list[str]:
    return re.findall(r"rcp\d\d|SSP\d{3}|MKPRISMv\d+|historical|observed", source)


def _kor_layers(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """KOR catalog entries with the two audited label checks made explicit."""
    out: list[dict[str, Any]] = []
    for e in entries:
        if e.get("region") != "KOR":
            continue
        source = str(e.get("source", ""))
        requested = str(e.get("climate_scenario", ""))
        served_tokens = _scenario_tokens(source)
        served = served_tokens[0] if served_tokens else None
        # rcp45 vs SSP245 is a naming-system difference (SCENARIO_TOKENS), not a mismatch;
        # rcp45 vs rcp60 is.
        mismatch = bool(served and served.startswith("rcp") and served != requested)
        from_kma = "KMA" in source
        label_claims_tmax = "Tmax" in source
        variable_is_ta = bool(re.search(r"\bTA\b", source))
        out.append(
            {
                "peril": e.get("peril"),
                "file": e.get("file"),
                "requested_scenario": requested,
                "served_scenario": served,
                "scenario_mismatch": mismatch,
                "year": e.get("year"),
                "n_events": e.get("n_events"),
                "n_centroids": e.get("n_centroids"),
                "source": source,
                "from_kma": from_kma,
                "stored_resolution_note": (
                    "0.05 deg block mean (from source label)" if "0.05 deg" in source else None
                ),
                "label_variable_conflict": label_claims_tmax and variable_is_ta,
                "grade": "5km-current" if from_kma and "0.05 deg" in source else "not-kma",
            }
        )
    return out


def env_file() -> Path:
    """The project ``.env`` the launcher sources; a backend started bare will not have it."""
    return REPO_ROOT / ".env"


def _env_file_values() -> dict[str, str]:
    """Names → values from ``.env`` (only ever reduced to presence/length before leaving)."""
    path = env_file()
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        return {k: (v or "") for k, v in dotenv_values(path).items()}
    except ImportError:  # pragma: no cover - dotenv ships with pydantic-settings
        return {}


def _credentials() -> list[dict[str, Any]]:
    """Presence and length of each credential — from the process env, else from ``.env``.

    The macOS launcher and ``run.command`` export ``.env``; a bare ``uvicorn`` does not, and
    a status card that then said "없음" for a key that is in ``.env`` would be wrong.
    """
    file_values = _env_file_values()
    out = []
    for name, purpose in CREDENTIAL_ENV:
        value = os.environ.get(name, "") or file_values.get(name, "")
        present = bool(value.strip())
        if name.endswith("_PATH"):
            present = present and Path(value).is_file()
        out.append(
            {
                "env": name,
                "purpose": purpose,
                "present": present,
                "length": len(value.strip()),
                "source": "environment"
                if os.environ.get(name, "").strip()
                else ("dotenv" if file_values.get(name, "").strip() else None),
            }
        )
    return out


def _git_branch(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def korea_status() -> dict[str, Any]:
    """Everything the 현황 체크 card shows, as one JSON-serialisable dict."""
    settings = get_settings()
    directory = kma_dir()
    files = _kma_files(directory)
    variables_present = sorted({f["variable"] for f in files})
    catalog = read_catalog()
    layers = _kor_layers(catalog["entries"])
    perils_with_layer = sorted({str(layer["peril"]) for layer in layers})

    grades: dict[str, str] = {}
    for layer in layers:
        peril = str(layer["peril"])
        grades[peril] = layer["grade"] if layer["grade"] != "not-kma" else "missing"
    for peril in _KMA_DATA_ONLY:
        grades.setdefault(peril, "1km-data-only")

    return {
        "kma": {
            "dir": str(directory),
            "dir_exists": directory.is_dir(),
            "files": files,
            "variables_present": variables_present,
            "variables_offered": list(KMA_VARIABLES),
            "variables_consumed_by_code": list(CONSUMED_VARIABLES),
            "note": "discovery by file name only; reading is done by the CLIMADA worker",
        },
        "catalog": {
            "dir": catalog["dir"],
            "kor_layers": layers,
            "perils_with_kor_layer": perils_with_layer,
            "scenario_mismatches": [
                layer["file"] for layer in layers if layer["scenario_mismatch"]
            ],
            "label_variable_conflicts": [
                layer["file"] for layer in layers if layer["label_variable_conflict"]
            ],
        },
        "one_km": {
            "ready": [],
            "grades": grades,
            "rule": (
                "1km-ready requires source, stored and calculation grids ≈ 1 km, exposure "
                "matched at ≈ 1 km, and metadata recording the resolution — no layer meets the "
                "metadata condition today (docs/KOREA_1KM_IMPLEMENTATION_GAP.md)"
            ),
        },
        "credentials": _credentials(),
        "environment": {
            "worker_env_present": (settings.worker_env_dir / "bin" / "python").is_file()
            or (Path(settings._abspath(settings.worker_env_dir)) / "bin" / "python").is_file(),
            "git_branch": _git_branch(Path(__file__).resolve().parents[3]),
        },
    }
