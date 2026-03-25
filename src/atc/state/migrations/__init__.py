"""Migration runner — applies versioned SQL files in order."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atc.state.db import ConnectionFactory

logger = logging.getLogger(__name__)

VERSIONS_DIR = Path(__file__).parent / "versions"

# Matches files like 001_initial_schema.sql or 010a_tower_memory_ensure.sql
_MIGRATION_RE = re.compile(r"^(\d{3}[a-z]?)_.*\.sql$")


def _ensure_migrations_table(factory: ConnectionFactory) -> None:
    """Create the ``_migrations`` tracking table if it doesn't exist."""
    with factory.connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                version  TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def _applied_filenames(factory: ConnectionFactory) -> set[str]:
    """Return the set of already-applied migration filenames.

    Using filename as the effective unique key is backward-compatible with both
    old INTEGER-PK schemas and new TEXT-PK schemas.
    """
    with factory.connection() as conn:
        rows = conn.execute("SELECT filename FROM _migrations").fetchall()
    return {row[0] for row in rows}


def _applied_versions(factory: ConnectionFactory) -> set[str]:
    """Return the set of already-applied migration version strings.

    Backward-compatible replacement for the old ``set[int]`` variant —
    now returns zero-padded strings (e.g. ``{"001", "010", "010a"}``).
    """
    with factory.connection() as conn:
        rows = conn.execute("SELECT version FROM _migrations").fetchall()
    result: set[str] = set()
    for row in rows:
        v = str(row[0])
        # Normalise bare integers (legacy INTEGER PK) to zero-padded strings.
        try:
            result.add(f"{int(v):03d}")
        except (ValueError, TypeError):
            result.add(v)
    return result


def _discover_migrations(versions_dir: Path | None = None) -> list[tuple[str, Path]]:
    """Discover and sort migration files by version string."""
    search_dir = versions_dir or VERSIONS_DIR
    migrations: list[tuple[str, Path]] = []
    if not search_dir.exists():
        return migrations
    for path in sorted(search_dir.iterdir()):
        match = _MIGRATION_RE.match(path.name)
        if match:
            version = match.group(1)  # e.g. "010", "010a"
            migrations.append((version, path))
    return migrations


def _insert_migration(conn: sqlite3.Connection, version: str, filename: str) -> None:
    """Insert a migration record, handling legacy INTEGER PRIMARY KEY schemas.

    New databases use ``version TEXT PRIMARY KEY``.  Older databases (or those
    created by tests that manually set up the schema) may use
    ``version INTEGER PRIMARY KEY``.  When a letter-suffix version (e.g.
    ``"010a"``) cannot be stored as an integer, we fall back to storing just
    the numeric prefix with ``INSERT OR IGNORE`` (the migration SQL is
    idempotent, so occasional re-runs are safe).
    """
    try:
        conn.execute(
            "INSERT INTO _migrations (version, filename) VALUES (?, ?)",
            (version, filename),
        )
    except sqlite3.IntegrityError as exc:
        if "datatype mismatch" in str(exc).lower():
            # Legacy INTEGER PRIMARY KEY schema; letter-suffix won't coerce.
            numeric = int(re.match(r"(\d+)", version).group(1))  # type: ignore[union-attr]
            conn.execute(
                "INSERT OR IGNORE INTO _migrations (version, filename) VALUES (?, ?)",
                (numeric, filename),
            )
        else:
            raise


def run_migrations(
    factory: ConnectionFactory,
    *,
    versions_dir: Path | None = None,
) -> list[str]:
    """Apply all pending migrations in order.

    Returns a list of filenames that were applied.
    """
    _ensure_migrations_table(factory)
    applied = _applied_filenames(factory)
    migrations = _discover_migrations(versions_dir)

    newly_applied: list[str] = []
    for version, path in migrations:
        if path.name in applied:
            continue

        sql = path.read_text()
        logger.info("Applying migration %s", path.name)
        with factory.connection() as conn:
            conn.executescript(sql)
            _insert_migration(conn, version, path.name)
            conn.commit()
        newly_applied.append(path.name)
        logger.info("Applied migration %s", path.name)

    if not newly_applied:
        logger.info("Database is up to date — no migrations to apply")
    else:
        logger.info("Applied %d migration(s)", len(newly_applied))

    return newly_applied
