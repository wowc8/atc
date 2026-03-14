# 005 — Failure Logging

**Date**: 2026-03-14
**Status**: accepted

## Decision

Every unexpected event is written to a structured failure log with full context. The primary use case is "Copy for Claude" — paste into Claude Code to diagnose.

## Context

Debugging multi-agent systems requires capturing the full state at the time of failure, not just error messages. The log must be machine-readable for AI analysis and human-readable for quick scanning.

## Consequences

- `failure_log()` is fire-and-forget async — never blocks
- Context blob captures all relevant state as JSON
- UI shows a log viewer with expandable entries and "Copy for Claude" button
- Logs publish on WebSocket for real-time badge updates
