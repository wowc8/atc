# 007 — UI Philosophy

**Date**: 2026-03-14
**Status**: accepted

## Decision

The UI follows "ease of use, smooth, and attractive" as its overarching principle. Frictionless by default, living interfaces, no modal walls, calm information density.

## Context

ATC manages complex multi-agent workflows. The UI's job is to make that complexity invisible. Every extra click, modal, or flash of content adds friction.

## Consequences

- No `window.confirm()` — use `<ConfirmPopover>` with undo
- Inline editing everywhere — no separate edit modes
- Subtle transitions (150-250ms) signal the app is alive
- Dark theme with aviation blue accent
- Terminal components use keep-alive off-screen pattern
