# 011 — Tower UI: Dockable/Draggable Overlay Panel

**Date**: 2026-03-22
**Status**: accepted

## Decision

The Tower UI is implemented as a dockable, draggable overlay panel with a persistent edge tab that the user can expand or collapse. It is not a modal dialog and not a fixed toolbar row.

## Context

Three UI patterns were considered:

1. **Modal dialog** — Tower status and controls in a modal. Rejected: modals interrupt workflow, cannot be kept open while working in other project views, and conflict with the "calm interface" principle in design log 007.

2. **Fixed toolbar bar** — A permanent top or bottom bar always showing Tower status. Rejected: consumes vertical space on every screen even when Tower is idle, and cannot be repositioned for different monitor layouts or personal preference.

3. **Dockable overlay panel with edge tab** — A collapsible panel that lives at the screen edge (defaulting to right). An always-visible tab lets users open/close it without navigating away. When expanded it overlays content rather than pushing layout. Accepted: non-intrusive when idle, accessible when needed, repositionable.

The edge-tab approach follows established patterns in IDEs (VS Code sidebar, browser DevTools) and is consistent with ATC's terminal-first, low-friction philosophy.

## Consequences

- The Tower panel component uses absolute positioning + drag handles rather than a flex layout slot
- Panel state (open/closed, edge, position) is persisted in localStorage so it survives page reload
- The panel does not unmount when closed — it follows the keep-alive pattern from design log 007 to preserve terminal state
- Other panels (Leader console, Ace terminals) are unaffected by Tower panel position
