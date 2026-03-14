"""Migration runner — applies versioned SQL files in order."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atc.state.db import ConnectionFactory

logger = logging.getLogger(__name__)

VERSIONS_DIR = Path(__file__).parent / "versions"

# Matches files like 001_initial_schema.sql
_MIGRATION_RE = re.compile(r"^(\d{3})_.*\.sql$")


def _ensure_migrations_table(factory: ConnectionFactory) -> None:
    """Create the ``_migrations`` tracking table if it doesn't exist."""
    with factory.connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                version  INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def _applied_versions(factory: ConnectionFactory) -> set[int]:
    """Return the set of already-applied migration version numbers."""
    with factory.connection() as conn:
        rows = conn.execute("SELECT version FROM _migrations").fetchall()
    return {row[0] for row in rows}


def _discover_migrations(versions_dir: Path | None = None) -> list[tuple[int, Path]]:
    """Discover and sort migration files by version number."""
    search_dir = versions_dir or VERSIONS_DIR
    migrations: list[tuple[int, Path]] = []
    if not search_dir.exists():
        return migrations
    for path in sorted(search_dir.iterdir()):
        match = _MIGRATION_RE.match(path.name)
        if match:
            version = int(match.group(1))
            migrations.append((version, path))
    return migrations


def run_migrations(
    factory: ConnectionFactory,
    *,
    versions_dir: Path | None = None,
) -> list[str]:
    """Apply all pending migrations in order.

    Returns a list of filenames that were applied.
    """
    _ensure_migrations_table(factory)
    applied = _applied_versions(factory)
    migrations = _discover_migrations(versions_dir)

    newly_applied: list[str] = []
    for version, path in migrations:
        if version in applied:
            continue

        sql = path.read_text()
        logger.info("Applying migration %s", path.name)
        with factory.connection() as conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO _migrations (version, filename) VALUES (?, ?)",
                (version, path.name),
            )
            conn.commit()
        newly_applied.append(path.name)
        logger.info("Applied migration %s", path.name)

    if not newly_applied:
        logger.info("Database is up to date — no migrations to apply")
    else:
        logger.info("Applied %d migration(s)", len(newly_applied))

    return newly_applied
