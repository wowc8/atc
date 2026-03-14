# 006 — Key Stability

**Date**: 2026-03-14
**Status**: accepted

## Decision

Context entries are always accessed by key, never by position. The position field is a UI-only display hint for block ordering.

## Context

Users drag blocks to reorder them visually. If agents accessed entries by position, reordering would break agent behavior silently.

## Consequences

- Agents use keyed maps for context, never ordered lists
- Drag-and-drop only updates position values, not keys or content
- New entries from agents get position = max + 1
