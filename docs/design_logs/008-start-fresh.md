# 008 — Start Fresh

**Date**: 2026-03-14
**Status**: accepted

## Decision

ATC is a new repository, not a fork of yudongqiu/orchestrator. Proven subsystems are re-implemented from scratch using the full system spec as a behavioral reference.

## Context

The upstream Orchestrator is a different product with different design goals. Forking would cause constant merge conflicts with the new Leader tier and budget system. We own the original code and can reference it freely.

## Consequences

- No upstream merge conflicts
- Clean commit history
- Subsystems (PTY streaming, SSH tunnels, state machine) are re-implemented, not copied
- The original Orchestrator codebase serves as a behavioral reference only
