# Architecture

> System architecture reference — single source of truth for ATC's structure.

## Overview

ATC is a hierarchical AI orchestration platform with a four-tier chain of command:

```
User → Tower → Leader → Ace
```

- **Tower** (singleton): receives goals, spawns Leaders, enforces budgets, monitors aggregate resources
- **Leader** (one per project): owns context, decomposes goals into task graphs, spawns and manages Aces
- **Ace** (many per project): task-scoped Claude Code session, executes work, reports via hooks

## Backend Architecture

### Application Layer (`src/atc/api/`)

FastAPI application with:
- **App factory** (`app.py`): `create_app()` with lifespan management
- **REST routers** (`routers/`): tower, projects, tasks, aces, usage, settings
- **WebSocket hub** (`ws/`): channel-based pub/sub for real-time state

### Domain Layer

| Package | Responsibility |
|---|---|
| `tower/` | Tower controller loop, resource allocation, cross-project memory |
| `leader/` | Leader session lifecycle, context packages, task graph management |
| `core/` | Event bus, orchestrator, state manager, failure logger |
| `session/` | Session state machine, ace lifecycle, reconnect, SSH tunnels |
| `terminal/` | PTY streaming (tmux pipe-pane → FIFO → WS), output parser, monitor |
| `tracking/` | AI cost tracker, system resources (psutil), GitHub PR/CI, budget enforcer |
| `rws/` | Remote Ace Server daemon for remote hosts |
| `agents/` | Agent deployment SOT — writes config files to /tmp |

### Data Layer (`src/atc/state/`)

- **SQLite** with WAL mode for concurrent reads
- **Connection factory** with retry logic
- **Dataclass models** (no ORM)
- **Append-only migrations** in `migrations/versions/`

## Frontend Architecture

React 19 + TypeScript + Vite application.

### Provider Tree

```
QueryClientProvider → AppProvider (WebSocket state) → Router
```

### Key Routes

| Route | Component | Description |
|---|---|---|
| `/dashboard` | Dashboard | Cost charts, resource charts, project cards |
| `/projects/:id` | ProjectView | Leader console + ace terminals + task board |
| `/settings` | SettingsPage | Configuration management |
| `/usage` | UsagePage | Full analytics charts |

### Layout (Option A)

- **TowerBar**: always-visible top bar with status, costs, notifications
- **Left Panel**: project list + ace list
- **Right Panel**: Leader console with tabs (tasks, context, GitHub, budget)
- **Bottom Panel**: ace terminal tabs with keep-alive off-screen pattern

## Real-Time Communication

Single WebSocket endpoint at `/ws` with channel-based pub/sub.

### Channels

| Channel | Payload |
|---|---|
| `state` | Full state snapshot + delta events |
| `tower` | Tower status, goal updates |
| `manager:{project_id}` | Leader status, task graph updates |
| `costs` | AI usage events |
| `budget:{project_id}` | Budget status changes |
| `resources` | CPU/RAM/disk samples |
| `github:{project_id}` | PR status, CI results |
| `terminal:{session_id}` | Binary PTY frames |
| `logs` | Failure log entries |

## Database Schema

Core tables: `projects`, `leaders`, `sessions`, `tasks`, `project_budgets`,
`usage_events`, `github_prs`, `notifications`, `config`, `tower_memory`, `failure_logs`.

See `src/atc/state/migrations/versions/` for the canonical schema.

## Startup Sequence

1. Run DB migrations
2. Start event bus
3. Start state manager
4. Start PtyStreamPool
5. Start Tower controller
6. Start resource monitor
7. Start GitHub tracker
8. Start budget enforcer
9. Reconnect active sessions from last shutdown

Shutdown runs in reverse order, draining queues before closing DB.
