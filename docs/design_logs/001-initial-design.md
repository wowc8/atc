# 001 — Initial Design

**Date**: 2026-03-14
**Status**: accepted

## Decision

ATC will be a hierarchical AI orchestration platform with a four-tier chain of command: User → Tower → Leader → Ace. It combines terminal-first session management with cost tracking, GitHub integration, and dashboard analytics.

## Context

Two existing reference architectures (Orchestrator and Coral) each solve part of the multi-agent management problem. ATC unifies both approaches with a new Leader tier that owns project-level context and planning.

## Consequences

- The Tower is a singleton that governs all resources and goals
- Every project gets exactly one Leader session
- Aces are task-scoped and have no awareness of siblings
- The architecture requires a robust event bus and state management system
