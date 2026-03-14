# 003 — Context Hub UX

**Date**: 2026-03-14
**Status**: accepted

## Decision

The project context hub uses a Notion-style living document UX. Entries are blocks that can be clicked to edit, dragged to reorder, and automatically saved. No forms, no explicit save buttons.

## Context

AI agents and humans need to share context within a project. A living document model makes it natural for both to read and write simultaneously, with changes animating in for visual feedback.

## Consequences

- Entries are accessed by key (stable), never by position (UI-only)
- Leaders receive context as a keyed map, not an ordered list
- Real-time updates require WebSocket subscription
