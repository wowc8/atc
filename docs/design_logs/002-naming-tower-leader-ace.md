# 002 — Naming: Tower, Leader, Ace

**Date**: 2026-03-14
**Status**: accepted

## Decision

The four-tier hierarchy uses aviation-themed naming: Tower (top-level controller), Leader (project manager), and Ace (task executor).

## Context

Clear naming is critical for a system where AI agents must understand their role. Aviation terminology maps naturally to the chain of command and is distinct enough to avoid confusion with other systems.

## Consequences

- All code, docs, and UI use these terms consistently
- Database tables and API routes reflect this naming
- The Tower is always singular; Leaders and Aces are plural per project
