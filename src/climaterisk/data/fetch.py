"""Open-data fetcher — download curated open-source datasets and place them where the
platform / CLIMADA expects them.

Safety: only fetches sources from the bundled ``data_sources.json`` registry (no arbitrary
client-supplied URLs → no SSRF), HTTPS only, into a fixed set of destination directories:

  - ``downloads`` → ``<data_dir>/downloads`` (generic staging)
  - ``climada``   → CLIMADA's own data dir   (LitPop / exposure drop-ins CLIMADA reads;
                    ``climaterisk.paths.climada_data_dir``)
  - ``catalog``   → the local hazard catalog dir

Per-country files use a ``{iso3}`` / ``{iso3_lower}`` template filled from the request.
``.zip`` payloads are extracted in place. Login-gated / portal-only sources carry no
``download_url`` and report a clear, actionable error.
"""

from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from climaterisk import paths
from climaterisk.config import get_settings
from climaterisk.data.libraries import load_libraries
from climaterisk.logger import get_logger

logger = get_logger(__name__)


def _dest_dir(dest: str) -> Path:
    settings = get_settings()
    if dest == "climada":
        path = paths.climada_data_dir()
    elif dest == "catalog":
        path = settings.hazard_db_path
    else:
        path = settings.data_path / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_url(source: dict[str, Any], country: str | None) -> str:
    """Resolve a source's download URL, filling the per-country template if present.

    Raises ValueError if the source has no direct URL, or needs a country it wasn't given.
    """
    url = source.get("download_url")
    if not url:
        raise ValueError(
            f"source '{source.get('id')}' has no direct download URL "
            f"(portal or login-gated) — see {source.get('url')}"
        )
    if "{" in url:
        if not country:
            raise ValueError(
                f"source '{source.get('id')}' is per-country — needs a single-country portfolio"
            )
        url = url.format(iso3=country.upper(), iso3_lower=country.lower())
    if not url.lower().startswith("https://"):
        raise ValueError("only https downloads are allowed")
    return str(url)


def _download(url: str, dest_dir: Path, unzip: bool) -> dict[str, Any]:
    filename = url.rsplit("/", 1)[-1].split("?")[0] or "download.dat"
    out = dest_dir / Path(filename).name  # sanitize: basename only
    timeout = get_settings().download_timeout_seconds
    req = urllib.request.Request(url, headers={"User-Agent": "climaterisk/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, out.open("wb") as fh:
        shutil.copyfileobj(resp, fh, length=256 * 1024)
    size = out.stat().st_size
    extracted: list[str] = []
    if unzip and out.suffix == ".zip":
        with zipfile.ZipFile(out) as zf:
            zf.extractall(dest_dir)
            extracted = zf.namelist()[:20]
        out.unlink()
        return {"path": str(dest_dir), "bytes": size, "extracted": extracted}
    return {"path": str(out), "bytes": size, "extracted": extracted}


def fetch_source(source_id: str, country: str | None = None) -> dict[str, Any]:
    """Download a curated open-data source by id, placing it at its declared destination."""
    sources = load_libraries()["data_sources"]["sources"]
    src = next((s for s in sources if s.get("id") == source_id), None)
    if src is None:
        return {"status": "error", "detail": f"unknown data source '{source_id}'"}
    try:
        url = resolve_url(src, country)
    except ValueError as exc:
        return {"status": "error", "source": source_id, "detail": str(exc)}
    dest = str(src.get("dest", "downloads"))
    logger.info("fetching open data %s -> %s", source_id, dest)
    try:
        res = _download(url, _dest_dir(dest), bool(src.get("unzip")))
    except Exception as exc:
        return {
            "status": "error",
            "source": source_id,
            "detail": f"download failed: {type(exc).__name__}: {str(exc)[:160]}",
        }
    mb = res["bytes"] / 1e6
    return {
        "status": "ok",
        "source": source_id,
        "dest": dest,
        **res,
        "detail": f"Downloaded {source_id} ({mb:.1f} MB) → {res['path']}",
    }
