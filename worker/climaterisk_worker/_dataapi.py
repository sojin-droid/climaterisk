"""Resilient CLIMADA Data API access — self-heal interrupted-download cache locks.

CLIMADA's ``api_client`` records every download in a local cache db. If a download is
interrupted (the worker is killed, or two runs race the same large file), the record is
left without an ``enddownload`` timestamp. Every later ``get_hazard`` for that file then
raises ``Download.Failed`` ("…requested before. Either it is still in progress or the
process got interrupted…") and never recovers until the cache db is purged — turning one
interrupted 2 GB download into a permanently failing peril.

``resilient_get_hazard`` catches that specific failure, purges the stale (incomplete)
download records, and retries once, so a poisoned cache self-heals instead of failing
every subsequent run. Worker (CLIMADA) env only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def purge_incomplete_downloads() -> int:
    """Delete cache-db records for downloads that never completed. Returns count purged."""
    from climada.util.api_client import Client, Download

    purged = 0
    for d in Download.select().where(Download.enddownload.is_null(True)):
        try:
            Client.purge_cache_db(Path(d.path))
        except Exception:
            try:
                d.delete_instance()
            except Exception:
                continue
        purged += 1
    return purged


class OfflineError(RuntimeError):
    """Raised instead of a network call when ``CLIMATERISK_OFFLINE`` is set."""


def resilient_get_hazard(client: Any, *args: Any, **kwargs: Any) -> Any:
    """``client.get_hazard`` with one self-healing retry past a stuck/interrupted download.

    In offline mode the Data API is never consulted: the caller's catalog lookup has
    already failed, so this raises :class:`OfflineError` naming what to cache instead of
    waiting for a network timeout.
    """
    from climaterisk_worker._params import offline

    if offline():
        wanted = args[0] if args else kwargs.get("data_type", "hazard")
        props = kwargs.get("properties") or (args[1] if len(args) > 1 else {})
        raise OfflineError(
            f"offline mode (CLIMATERISK_OFFLINE): no local catalog layer for {wanted} "
            f"{props} and the Data API is not consulted — cache it while online with "
            "scripts/build_hazard.py cache / ingest or the Data tab (Fetch & ingest)"
        )
    from climada.util.api_client import Download

    try:
        return client.get_hazard(*args, **kwargs)
    except Download.Failed:
        purge_incomplete_downloads()
        return client.get_hazard(*args, **kwargs)
