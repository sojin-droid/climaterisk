"""Cache the CLIMADA Data API dataset listing as a local JSON index.

EIOPA's CLIMADA-App stores the API's ``DatasetInfo`` list in a feather file so its
dropdowns work offline. The equivalent here: one JSON file in the data root's cache
(``climaterisk.paths.cache_dir``) listing, per data type, every dataset's name, version,
status and properties. It is written by the ``dataapi_index`` worker job (Data tab button,
or ``scripts/build_hazard.py index``) while online, and read by the backend's offline
status so an analyst can see what the Data API offers without reaching it.

Only metadata is cached — no hazard file is downloaded here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Mirrors ``climaterisk.data.offline_status.DATAAPI_INDEX_TYPES`` (the backend reader);
#: kept here as well so the worker never imports backend code (GPL boundary).
DEFAULT_TYPES: tuple[str, ...] = (
    "tropical_cyclone",
    "river_flood",
    "wildfire",
    "earthquake",
    "storm_europe",
)


def _record(info: Any) -> dict[str, Any]:
    """A JSON-safe record of one ``DatasetInfo``."""
    props = getattr(info, "properties", None) or {}
    files = getattr(info, "files", None) or []
    return {
        "data_type": str(getattr(info, "data_type", "")),
        "name": str(getattr(info, "name", "")),
        "version": str(getattr(info, "version", "")),
        "status": str(getattr(info, "status", "")),
        "properties": {str(k): str(v) for k, v in dict(props).items()},
        "n_files": len(files),
        "uuid": str(getattr(info, "uuid", "")),
    }


def build_index(
    data_types: tuple[str, ...] | list[str] = DEFAULT_TYPES,
    client: Any = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """List every active dataset of ``data_types`` and write the index JSON.

    ``client`` defaults to a CLIMADA ``Client`` (network); tests pass a stand-in. The index
    carries ``fetched_at`` (UTC ISO) so a reader can show its age.
    """
    if client is None:
        from climada.util.api_client import Client

        client = Client()
    datasets: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for dt in data_types:
        try:
            infos = client.list_dataset_infos(data_type=dt)
        except Exception as exc:  # one failing type must not lose the others
            errors[dt] = f"{type(exc).__name__}: {exc}"
            continue
        datasets.extend(_record(i) for i in infos)
    index = {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_types": list(data_types),
        "n_datasets": len(datasets),
        "datasets": datasets,
        "errors": errors,
        "source": "CLIMADA Data API, Client.list_dataset_infos (metadata only)",
    }
    if out_path is None:
        from climaterisk_worker._paths import paths

        out_path = paths.cache_dir("dataapi_index.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    index["path"] = str(out_path)
    return index


def compute_dataapi_index(request: dict[str, Any]) -> dict[str, Any]:
    """Worker-job entry: ``{"mode": "dataapi_index", "data_types": [...]}`` → status dict."""
    from climaterisk_worker._params import offline

    if offline():
        return {
            "status": "error",
            "detail": "offline mode (CLIMATERISK_OFFLINE): the Data API index can only be "
            "refreshed while online",
        }
    types = tuple(request.get("data_types") or DEFAULT_TYPES)
    index = build_index(types)
    return {
        "status": "ok",
        "path": index["path"],
        "fetched_at": index["fetched_at"],
        "n_datasets": index["n_datasets"],
        "errors": index["errors"],
        "detail": f"Data API index: {index['n_datasets']} datasets over {len(types)} types "
        f"cached at {index['path']}",
    }
