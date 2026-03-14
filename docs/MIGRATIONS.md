# Migrations

> How to write and run database migrations.

## Overview

ATC uses SQLite with WAL mode. Schema changes are managed through append-only SQL
migration files in `src/atc/state/migrations/versions/`.

## Creating a New Migration

```bash
./scripts/new_migration.sh <description>
# Example: ./scripts/new_migration.sh add_notifications_table
```

This creates `NNN_description.sql` with the next sequence number.

## Migration Rules

1. **Append-only**: never edit existing migration files
2. **Idempotent**: use `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`
3. **Timestamps**: all tables must have `created_at TEXT NOT NULL` and `updated_at TEXT NOT NULL`
4. **Comments**: include a header comment explaining what the migration does
5. **No destructive changes**: never `DROP TABLE` or `DROP COLUMN` in production

## Migration Format

```sql
-- Migration: NNN description
-- Created: YYYY-MM-DDTHH:MM:SSZ
--
-- What this migration does and why.

CREATE TABLE IF NOT EXISTS my_table (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

## How Migrations Run

Migrations run automatically on application startup, in order by filename.
The migration runner tracks which files have been applied in a `_migrations` table.

## Rollbacks

There is no automatic rollback mechanism. If a migration needs to be undone,
write a new migration that reverses the changes.
