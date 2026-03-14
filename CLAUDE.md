# ATC — Claude Code Guide

ATC is a hierarchical AI orchestration platform that manages multiple Claude Code
sessions through a Tower → Leader → Ace chain of command. The Tower governs resources
and goals, Leaders own context and planning within a project, and Aces execute tasks
in isolated Claude Code sessions.

## Before You Write Any Code

1. Read `docs/ARCHITECTURE.md` to understand the subsystem you're touching
2. Read `docs/PATTERNS.md` — these are non-negotiable conventions
3. Read `docs/ANTI_PATTERNS.md` — these are things you must never do
4. Check `docs/design_logs/` for any decisions relevant to your change

## Build & Run Commands

```bash
# Dev mode (backend + frontend)
./scripts/dev.sh

# Backend only
python -m uvicorn atc.api.app:create_app --factory --reload --port 8420

# Frontend only
cd frontend && npm run dev

# Run tests
pytest                      # all tests
pytest tests/unit           # fast unit tests only
pytest -x -q                # stop on first failure

# Lint & format
ruff check src/ tests/
ruff format src/ tests/
mypy src/

# New migration
./scripts/new_migration.sh <description>

# New design log
./scripts/new_design_log.sh <slug>
```

## Key Invariants

- **DB-first creation**: session row written before tmux pane spawned
- **Keys are stable**: context entries accessed by key only, never position
- **Enter is atomic**: instruction text and Enter sent with no await gap
- **Migrations are append-only**: never edit existing .sql files
- **No bare except**: always catch specific exception types
- **No print()**: use `logging` or `failure_log()` in production code
- **No window.confirm()**: use `<ConfirmPopover>` component
- **No unmounting terminals**: keep-alive pattern for all xterm.js instances

## Where Things Live

```
src/atc/api/          → FastAPI routers + WebSocket hub
src/atc/tower/        → Tower controller, allocator, memory
src/atc/leader/       → Leader session type + context package
src/atc/core/         → Event bus, orchestrator, state manager, failure logger
src/atc/session/      → State machine, ace lifecycle, reconnect, tunnels
src/atc/terminal/     → PTY stream, output parser, monitor
src/atc/tracking/     → Cost, resources, GitHub, budget enforcer
src/atc/state/        → DB connection, models, migrations
src/atc/config.py     → Pydantic settings from config.yaml
frontend/src/         → React 19 + TypeScript UI
tests/                → Mirrors src/atc/ structure
docs/                 → Architecture, patterns, API reference
```

## How To Make a Change

1. Write a failing test first
2. Implement the change
3. Run `ruff check`, `mypy --strict`, `pytest` — all must pass
4. If you made a design decision, add a design log entry
5. Update `docs/ARCHITECTURE.md` or `docs/FEATURES.md` if behavior changed

## Code Conventions

### Python
- `ruff` for linting/formatting — configured in `pyproject.toml`
- `mypy` strict mode — all functions must have type annotations
- Async everywhere — no synchronous DB or subprocess calls on main thread
- All DB writes through `ConnectionFactory` with `with_retry`

### TypeScript / React
- `ESLint` + `Prettier` enforced
- All components typed with explicit `interface Props`
- TanStack Query for all server state — no `useEffect` fetches
- Keep-alive pattern for terminal components

### SQL / Migrations
- New migration file per schema change: `NNN_description.sql`
- Idempotent: `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`
- All tables have `created_at` and `updated_at` as ISO-8601 strings

## Architecture Quick Reference

- **Tower**: singleton controller, spawns Leaders, enforces budgets
- **Leader**: one per project, decomposes goals into task graphs, spawns Aces
- **Ace**: task-scoped Claude Code session, reports via hooks
- **Event Bus**: in-process pub/sub connecting all subsystems
- **WebSocket Hub**: channel-based pub/sub for frontend (`/ws`)
- **State Manager**: queued DB write handler for events
