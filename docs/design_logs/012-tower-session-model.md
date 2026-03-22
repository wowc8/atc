# 012 — Tower Session Model: Rule-Based Controller, Not Persistent Claude Session

**Date**: 2026-03-22
**Status**: accepted

## Decision

The Tower is a rule-based Python controller (`TowerController`) that calls Claude on-demand for specific reasoning tasks (e.g. goal decomposition). It is not a persistent Claude Code session that runs continuously in a terminal.

## Context

Two models were considered:

1. **Persistent Claude Code session** — Tower runs as a long-lived Claude Code terminal (like a Leader or Ace), receives events via hooks, and reasons continuously. Rejected for two reasons: (a) idle token burn — a persistent session waiting for the next goal consumes tokens even when doing nothing, which compounds across all projects; (b) restart complexity — if the Tower session crashes or times out, the entire orchestration layer must be rebuilt, and session state recovery is fragile.

2. **Rule-based Python controller with on-demand Claude calls** — Tower logic is deterministic Python code. Claude is invoked only when a goal arrives that requires decomposition or planning, then the connection closes. Accepted: zero idle cost, straightforward restartability (the controller is a plain Python object recreated on startup from DB state), and full observability through normal logging.

The Leader and Ace tiers do use persistent Claude Code sessions because they need sustained context across a multi-step task. The Tower's role is coordination and enforcement, not sustained reasoning, so the trade-off is different.

## Consequences

- `TowerController` is a Python class, not a session type in the state machine
- Tower's "own session" (if started) is an optional convenience terminal for human-in-the-loop interaction, not required for orchestration
- Budget enforcement, goal routing, and Leader lifecycle are all handled by synchronous Python logic on the event bus
- Adding AI-powered Tower reasoning in future means adding a targeted `await claude_client.call(...)` in the relevant method, not maintaining a persistent session
