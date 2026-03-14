# Anti-Patterns

> Prohibited patterns with explanations. Violating these is wrong even if the code appears to work.

## Never spawn a tmux pane before writing the DB row

**Why**: Causes ghost sessions — processes exist but are invisible to the UI and API.
The DB row must exist first so the session appears immediately in the app.

## Never send instruction text without immediately sending Enter

**Why**: Causes swallowed instructions — text sits in the prompt buffer, unsubmitted.
The creating entity believes the instruction was delivered when it was not.

## Never send keystrokes when `alternate_on` is True

**Why**: Corrupts the Claude TUI state. Wait for the TUI to exit alternate screen
before sending any keystrokes.

## Never edit an existing migration file

**Why**: Breaks all existing installs. Migrations are append-only — write a new
migration to alter existing tables.

## Never access context entries by position

**Why**: Positions are UI-only display hints. Users can drag to reorder at any time.
Always access context entries by key.

## Never use `window.confirm()` or `window.alert()`

**Why**: Violates the no-modal-walls design principle. Use `<ConfirmPopover>` for
destructive actions with undo-able alternatives.

## Never unmount a terminal component

**Why**: Destroys the xterm.js WebGL context. Use the keep-alive off-screen pattern
instead: position inactive terminals at `top: -200vh`.

## Never write a DB connection outside `ConnectionFactory`

**Why**: Breaks WAL concurrency guarantees. All DB access must go through the
connection factory with retry logic.

## Never use bare `except:`

**Why**: Catches `KeyboardInterrupt`, `SystemExit`, and other critical exceptions.
Always catch specific exception types.

## Never use `print()` in production code

**Why**: Output goes nowhere useful. Use `logging` for operational info or
`failure_log()` for error tracking.

## Never use `useEffect` for data fetching in React

**Why**: TanStack Query handles caching, deduplication, and background refetching.
`useEffect` fetches cause waterfall requests and stale data bugs.

## Never add a feature without updating docs

**Why**: Docs rot causes AI agents to make wrong assumptions. Update
`ARCHITECTURE.md` or `FEATURES.md` in the same PR as any behavior change.
