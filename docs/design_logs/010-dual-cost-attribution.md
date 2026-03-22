# 010 — Dual Cost Attribution

**Date**: 2026-03-22
**Status**: accepted

## Decision

`CostTracker` supports two cost reporting paths: explicit reporting via `atc tower cost <session_id> <input_tokens> <output_tokens> <model>` (primary) and stats-cache polling (fallback). Sessions that have reported at least one explicit cost are tracked in `_has_explicit_reporting`; the polling loop skips attribution for those sessions entirely.

## Context

Stats-cache polling (`~/.claude/stats-cache.json`) is the only historical source of cost data but has two weaknesses: it attributes cost to whichever session was most recently active (imprecise with concurrent sessions), and it is cumulative rather than per-session (deltas can be misattributed across session boundaries).

The `atc tower cost` CLI gives Ace sessions a way to report their own token usage exactly, which Claude Code's hooks can call at task completion. When a session self-reports, the polling delta for that session's window is redundant and risks double-counting.

The alternative — replacing polling entirely — would break attribution for sessions that do not use the CLI (e.g. older Aces, manual sessions). Keeping both paths with a session-level suppression flag preserves compatibility.

## Consequences

- `CostTracker.record_explicit()` writes a `usage_events` row with the provided values and adds the session to `_has_explicit_reporting`
- `CostTracker._on_cost_reported()` is subscribed to the `cost_reported` event bus event, which the Tower API endpoint fires when it receives a POST to `/api/tower/cost`
- The polling loop checks the active session against `_has_explicit_reporting` and returns early if matched
- `_has_explicit_reporting` is in-memory only; a process restart clears it, after which polling resumes for all sessions (acceptable — any double-count is bounded by one poll interval)
