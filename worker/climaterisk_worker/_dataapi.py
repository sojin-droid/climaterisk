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


_CACHE_HINT = (
    "fetch it once while online (run the same analysis, or scripts/build_hazard.py "
    "cache / ingest, or the Data tab's Fetch & ingest); offline runs read CLIMADA's "
    "download cache and the local hazard catalog only"
)


def make_client() -> Any:
    """The CLIMADA Data API client every worker module uses.

    Online it is CLIMADA's own ``Client``. With ``CLIMATERISK_OFFLINE`` set it is a client
    that never touches the network yet still serves what CLIMADA cached on earlier online
    runs: dataset listings and infos come from CLIMADA's request cache (``Client`` already
    does this when it believes it is offline), and hazard files from its download cache
    (``_tracked_download`` returns an existing file without downloading). Two methods are
    overridden to make "never touches the network" hold rather than hope for it:
    ``_online`` (the stock constructor sends a HEAD request to decide) and ``_download``
    (the only place a file transfer starts).
    """
    from climada.util.api_client import Client

    from climaterisk_worker._params import offline

    if not offline():
        return Client()

    class _OfflineClient(Client):  # type: ignore[misc]
        _climaterisk_offline = True

        def _online(self) -> bool:
            return False

        def _download(self, url: str, path: Path, replace: bool = False) -> None:
            raise OfflineError(
                f"offline mode (CLIMATERISK_OFFLINE): {Path(path).name} is not in CLIMADA's "
                f"download cache — {_CACHE_HINT}"
            )

    return _OfflineClient()


def resilient_get_hazard(client: Any, *args: Any, **kwargs: Any) -> Any:
    """``client.get_hazard`` with one self-healing retry past a stuck/interrupted download.

    In offline mode the client must come from :func:`make_client` (it reads CLIMADA's caches
    and cannot reach the network); anything else is refused rather than trusted. A request
    the caches cannot answer raises :class:`OfflineError` naming what to fetch.
    """
    from climaterisk_worker._params import offline

    if offline():
        if not getattr(client, "_climaterisk_offline", False):
            raise OfflineError(
                "offline mode (CLIMATERISK_OFFLINE): this Data API client could reach the "
                "network; build it with climaterisk_worker._dataapi.make_client()"
            )
        from climada.util.api_client import Client

        try:
            return client.get_hazard(*args, **kwargs)
        except Client.NoConnection as exc:
            wanted = args[0] if args else kwargs.get("data_type", "hazard")
            props = kwargs.get("properties") or kwargs.get("name") or {}
            raise OfflineError(
                f"offline mode (CLIMATERISK_OFFLINE): {wanted} {props} was never fetched "
                f"on this machine — {_CACHE_HINT}"
            ) from exc
    from climada.util.api_client import Download

    try:
        return client.get_hazard(*args, **kwargs)
    except Download.Failed:
        purge_incomplete_downloads()
        return client.get_hazard(*args, **kwargs)
