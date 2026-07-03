# Features

> Feature guide: what exists, how it works, how to use it.

## Current Status: Scaffold

The project is in the initial scaffold phase. Features below describe the planned
architecture. Implementation status is tracked per feature.

## Tower Controller

**Status**: Stub

The Tower is the singleton top-level controller that:
- Receives high-level goals from the user
- Creates Leader sessions with context packages
- Monitors aggregate resource usage
- Enforces per-project budgets
- Maintains cross-project memory

## Leader (Project Manager)

**Status**: Stub

One per project. The Leader:
- Receives a context package from Tower
- Decomposes goals into task graphs
- Spawns and assigns Ace sessions
- Monitors ace output and re-assigns on failure
- Reports status and token usage deltas back to Tower

## Ace Sessions

**Status**: Stub

Task-scoped Claude Code sessions that:
- Execute assigned tasks
- Report status via Claude Code hooks
- Run locally or remotely via SSH tunnels

## Real-Time Dashboard

**Status**: Stub

Web UI showing:
- TowerBar with live status, token usage, notifications
- Project/Ace list with status indicators
- Leader console with terminal + task board
- Ace terminal tabs with keep-alive off-screen pattern
- Token usage, resource, and GitHub analytics charts

## Budget Enforcement

**Status**: Stub

Per-project budget limits with:
- Daily token limits
- Warning threshold notifications
- Automatic session pause on budget exceeded

## Token Usage Telemetry

**Status**: Foundation + Codex provider runtime sync implemented

Provider-neutral token usage infrastructure with:
- Append-only `usage_events` token increment rows
- Provider/model/source metadata for usage attribution
- Separate cached-input and reasoning-output token fields for providers that expose them
- Durable `usage_source_offsets` high-water state for provider collectors to avoid double-counting
- Shared `TokenUsageRecorder` interface used by provider-specific collectors
- Boundary rule: provider-specific parsing, filesystem paths, and event formats stay in provider modules; shared tracking only records normalized token increments

Codex-specific collection lives in `src/atc/agents/codex_usage.py`:
- Discovers/parses Codex session JSONL files under the Codex provider boundary
- Extracts Codex `token_count` cumulative token snapshots
- Maps Codex session metadata/cwd back to ATC sessions without creating orphan usage rows
- Converts cumulative Codex totals into provider-neutral token increments
- Reuses shared high-water state so restart/re-read sync passes do not double-count
- Starts with the backend when `token_tracker.codex_enabled` is true
- Supports manual deterministic sync through `POST /api/usage/tokens/sync-codex` or `atc usage sync-codex`

## GitHub Integration

**Status**: Stub

PR and CI tracking via `gh` CLI:
- PR status monitoring
- CI check results
- Rate limit awareness

## Failure Logging

**Status**: Implemented

Structured failure log with:
- `failure_logs` DB table (created in initial migration)
- `failure_log()` async helper for fire-and-forget logging
- REST API: `GET /api/failure-logs`, `GET /api/failure-logs/{id}`, `PATCH /api/failure-logs/{id}/resolve`, `GET /api/failure-logs/unresolved-count`
- LogViewer slide-out panel accessible from TowerBar
- Level/category filtering and resolved toggle
- "Copy for Claude" button on each entry (formats as Markdown)
- Real-time WebSocket notifications via `failure_logs` channel
- Unresolved failure badge in TowerBar
