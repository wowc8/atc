#!/usr/bin/env bash
# Scaffold a new design log entry.
# Usage: ./scripts/new_design_log.sh <slug>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS_DIR="$(dirname "$SCRIPT_DIR")/docs/design_logs"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <slug>"
    echo "Example: $0 context-hub-ux"
    exit 1
fi

SLUG="$1"

# Find next log number
LAST_NUM=$(ls "$LOGS_DIR"/[0-9]*.md 2>/dev/null | sort -V | tail -1 | grep -oP '\d{3}' | head -1 || echo "000")
NEXT_NUM=$(printf "%03d" $((10#$LAST_NUM + 1)))

FILENAME="${NEXT_NUM}-${SLUG}.md"
FILEPATH="${LOGS_DIR}/${FILENAME}"

cat > "$FILEPATH" << MD
# ${NEXT_NUM} — ${SLUG}

**Date**: $(date -u +%Y-%m-%d)
**Status**: accepted

## Decision

[One paragraph: what was decided]

## Context

[Why this decision was needed: what problem it solves, what alternatives were considered]

## Consequences

[What this decision means going forward: what becomes easier, what becomes harder, what is now off-limits]
MD

echo "Created: $FILEPATH"
