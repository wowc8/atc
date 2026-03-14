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
- Reports status and cost deltas back to Tower

## Ace Sessions

**Status**: Stub

Task-scoped Claude Code sessions that:
- Execute assigned tasks
- Report status via Claude Code hooks
- Run locally or remotely via SSH tunnels

## Real-Time Dashboard

**Status**: Stub

Web UI showing:
- TowerBar with live status, costs, notifications
- Project/Ace list with status indicators
- Leader console with terminal + task board
- Ace terminal tabs with keep-alive off-screen pattern
- Cost, resource, and GitHub analytics charts

## Budget Enforcement

**Status**: Stub

Per-project budget limits with:
- Daily token limits and monthly cost limits
- Warning threshold notifications
- Automatic session pause on budget exceeded

## GitHub Integration

**Status**: Stub

PR and CI tracking via `gh` CLI:
- PR status monitoring
- CI check results
- Rate limit awareness

## Failure Logging

**Status**: Stub

Structured failure log with:
- Category-based classification
- Full context capture for debugging
- "Copy for Claude" export format
- Real-time WebSocket notifications
