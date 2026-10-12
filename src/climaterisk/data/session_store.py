"""SQLite-backed session store — the backend owns the model.

Each session holds one :class:`~climaterisk.core.entities.Portfolio` document,
stored as JSON in a single ``app.db`` (WAL mode). The browser persists only the
session id; on every (debounced) edit it PUTs the whole model back here.

A new connection is opened per operation (cheap, and SQLite + WAL handles the
concurrent access from FastAPI's threadpool and the future CLIMADA worker).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from climaterisk.core.entities import Portfolio
from climaterisk.logger import get_logger

#: Serialises schema initialisation across the stores that share ``app.db`` (session and
#: run store) when first requests arrive concurrently in the threadpool.
_SCHEMA_LOCK = threading.Lock()

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    """Persist and retrieve per-session portfolio documents."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        # The journal mode is a property of the file, set once here under a process-wide
        # lock — never per connection: switching to WAL fails with "database is locked"
        # (the busy timeout does not apply) when another connection is open, which is
        # exactly what two first requests on a fresh database do.
        with _SCHEMA_LOCK, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    model_json  TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
                """
            )

    def create(self) -> Portfolio:
        """Create and persist a fresh, empty portfolio; return it."""
        portfolio = Portfolio.empty()
        ts = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, model_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (portfolio.id, portfolio.model_dump_json(), ts, ts),
            )
        logger.info("session created id=%s", portfolio.id)
        return portfolio

    def get(self, session_id: str) -> Portfolio | None:
        """Return the portfolio for ``session_id``, or ``None`` if unknown."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_json FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return Portfolio.model_validate_json(row["model_json"])

    def save(self, session_id: str, portfolio: Portfolio) -> Portfolio | None:
        """Replace the stored model for ``session_id``; return it (or ``None`` if unknown)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET model_json = ?, updated_at = ? WHERE id = ?",
                (portfolio.model_dump_json(), _now_iso(), session_id),
            )
            if cur.rowcount == 0:
                return None
        return portfolio

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns ``True`` if a row was removed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0
