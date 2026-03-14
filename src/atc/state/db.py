"""SQLite WAL connection factory with retry logic."""

from __future__ import annotations

import itertools
import logging
import sqlite3
from contextlib import contextmanager
from time import sleep
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

logger = logging.getLogger(__name__)

_memory_counter = itertools.count()

# Default retry settings for SQLITE_BUSY
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAY = 0.1  # seconds, doubles each retry


class ConnectionFactory:
    """Manages SQLite connections with WAL mode and retry logic.

    All database access should go through this factory — never use
    bare ``sqlite3.connect()`` elsewhere.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        wal_mode: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        raw = str(db_path)
        # Use shared-cache URI for in-memory databases so multiple connections
        # see the same data (each plain `:memory:` connection is isolated).
        if raw == ":memory:":
            n = next(_memory_counter)
            self._db_path = f"file:memdb{n}?mode=memory&cache=shared"
            self._use_uri = True
        else:
            self._db_path = raw
            self._use_uri = False
        self._wal_mode = wal_mode
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        # For shared-cache in-memory DBs, keep one connection open so the
        # database survives between connect()/close() cycles.
        self._keepalive: sqlite3.Connection | None = None
        if self._use_uri:
            self._keepalive = sqlite3.connect(self._db_path, uri=True)

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def is_memory(self) -> bool:
        """True when backed by an in-memory database."""
        return self._use_uri

    def close(self) -> None:
        """Close the keepalive connection (for in-memory databases)."""
        if self._keepalive is not None:
            self._keepalive.close()
            self._keepalive = None

    def _configure(self, conn: sqlite3.Connection) -> None:
        """Apply connection-level pragmas."""
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL is not meaningful for in-memory databases
        if self._wal_mode and not self._use_uri:
            conn.execute("PRAGMA journal_mode = WAL")

    def connect(self) -> sqlite3.Connection:
        """Open a new connection with WAL mode and foreign keys enabled."""
        conn = sqlite3.connect(self._db_path, uri=self._use_uri)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that opens, yields, and closes a connection."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that wraps work in a transaction with retry."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def with_retry(
        self,
        fn: Any,
        *args: Any,
        max_retries: int | None = None,
        retry_delay: float | None = None,
    ) -> Any:
        """Execute ``fn(conn, *args)`` with exponential-backoff retry on SQLITE_BUSY.

        Returns whatever ``fn`` returns. The connection is opened/closed per attempt.
        """
        retries = max_retries if max_retries is not None else self._max_retries
        delay = retry_delay if retry_delay is not None else self._retry_delay

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with self.transaction() as conn:
                    return fn(conn, *args)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_err = exc
                if attempt < retries:
                    wait = delay * (2**attempt)
                    logger.warning(
                        "DB busy (attempt %d/%d), retrying in %.2fs",
                        attempt + 1,
                        retries + 1,
                        wait,
                    )
                    sleep(wait)

        raise sqlite3.OperationalError(
            f"Database busy after {retries + 1} attempts"
        ) from last_err
