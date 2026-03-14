# 004 — Creation Reliability

**Date**: 2026-03-14
**Status**: accepted

## Decision

All entity creation follows DB-first protocol with three-phase verification (t+10s alive, t+60s working, t+120s progressing). Enter keystrokes are sent atomically with no await gap after instruction text.

## Context

Two observed failure modes: ghost sessions (process exists but no DB row) and swallowed instructions (text sent but Enter never pressed). Both are silent failures that waste time and resources.

## Consequences

- Session rows must be written to DB before tmux pane spawn
- UI always shows every entity, even failed ones
- Verification loop runs in background for every creation
- Instruction sending requires TUI readiness check
