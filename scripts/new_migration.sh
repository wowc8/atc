#!/usr/bin/env bash
# Scaffold a new SQL migration file.
# Usage: ./scripts/new_migration.sh <description>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="$(dirname "$SCRIPT_DIR")/src/atc/state/migrations/versions"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <description>"
    echo "Example: $0 add_notifications_table"
    exit 1
fi

DESCRIPTION="$1"

# Find next migration number
LAST_NUM=$(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | sort -V | tail -1 | grep -oP '\d{3}' | head -1 || echo "000")
NEXT_NUM=$(printf "%03d" $((10#$LAST_NUM + 1)))

FILENAME="${NEXT_NUM}_${DESCRIPTION}.sql"
FILEPATH="${MIGRATIONS_DIR}/${FILENAME}"

cat > "$FILEPATH" << SQL
-- Migration: ${NEXT_NUM} ${DESCRIPTION}
-- Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
--
-- WARNING: Migrations are append-only. Never edit existing migration files.

SQL

echo "Created: $FILEPATH"
