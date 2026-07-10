# Architecture

> System architecture reference — single source of truth for ATC's structure.

## Overview

For a faster visual map of the current services/classes and how they connect, start with [`docs/service_model.md`](service_model.md). It includes a rendered diagram image at [`docs/assets/atc-service-model.png`](assets/atc-service-model.png).

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
- **REST routers** (`routers/`): tower, projects, tasks, aces, orchestration, usage, settings
- **WebSocket hub** (`ws/`): channel-based pub/sub for real-time state

### Domain Layer

### Orchestration Boundary (`src/atc/orchestration/`)

The new orchestration package is an internal normalization boundary that sits between ATC's product-specific internals and future external control surfaces like MCP.

Current responsibilities:
- normalize raw session types/statuses into orchestration roles and statuses
- expose session-oriented service methods (`get_session`, `list_sessions`, `spawn_leader`, `send_instruction`, `wait_for_session`)
- translate Tower/provider/runtime failures into a stable orchestration error vocabulary
- back the first orchestration REST routes under `/api/orchestration/*`

Design rule:
- Orchestration should wrap existing high-level flows where they already exist, not duplicate runtime behavior. For example, `spawn_leader` goes through Tower's submit-goal path, and `send_instruction` goes through the existing provider-owned delivery path.
- Runtime truth and recovery work must follow the provider-neutral plan in [`docs/runtime_truth_recovery_plan.md`](runtime_truth_recovery_plan.md): product layers depend on `runtime_state`, `delivery_state`, `blocker_reason`, and recovery recommendations, while provider-specific prompt detection/recovery mechanics remain inside provider adapters/classifiers.
- Managed agent handoffs use a shared provider-neutral lifecycle contract in `atc.orchestration.handoff`. Tower→Leader kickoff and Leader→Ace assignment must distinguish `session_created`, `startup_inspected`, `input_ready`, `payload_written`, `payload_submitted`, `child_reported_active`, `first_actionable_step_observed`, and `handoff_verified`; session rows, `202 Accepted`, `queued`, or `submitted` transport states are not execution proof.
- Leader kickoff hardening must follow [`docs/leader_kickoff_recovery_plan.md`](leader_kickoff_recovery_plan.md): Tower may treat a Leader as running only after provider-neutral kickoff evidence proves goal acceptance and actionable progress or task graph creation. Session existence, visible prompt text, `queued`, `submitted`, `sent`, or `202 Accepted` responses are not execution proof.

| Package | Responsibility |
|---|---|
| `tower/` | Tower controller loop, resource allocation, cross-project memory |
| `leader/` | Leader session lifecycle, context packages, task graph management |
| `core/` | Event bus, orchestrator, state manager, failure logger |
| `session/` | Session state machine, ace lifecycle, reconnect, SSH tunnels |
| `terminal/` | PTY streaming (tmux pipe-pane → FIFO → WS), output parser, monitor |
| `tracking/` | Provider-neutral token usage recorder, provider usage trackers, system resources (psutil), GitHub PR/CI, budget enforcer |
| `rws/` | Remote Ace Server daemon for remote hosts |
| `agents/` | Shared agent deployment SOT — writes config files to /tmp |
| `providers/` | Provider-owned runtime adapters, provider-specific helpers, token collectors, and provider-native helper subagent mechanics |

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
| `/dashboard` | Dashboard | Token usage charts, resource charts, project cards |
| `/projects/:id` | ProjectView | Leader console + ace terminals + task board |
| `/settings` | SettingsPage | Configuration management, including provider helper visibility controls |
| `/usage` | UsagePage | Full analytics charts |

### Layout (Option A)

- **TowerBar**: always-visible top bar with status, token usage, notifications
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
| `usage` | AI token usage events |
| `budget:{project_id}` | Budget status changes |
| `resources` | CPU/RAM/disk samples |
| `github:{project_id}` | PR status, CI results |
| `terminal:{session_id}` | Binary PTY frames |
| `logs` | Failure log entries |

## Database Schema

Core tables: `projects`, `leaders`, `sessions`, `tasks`, `project_budgets`,
`usage_events`, `provider_helper_runs`, `provider_helper_events`, `github_prs`,
`notifications`, `config`, `tower_memory`, `failure_logs`.

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
9. Start provider usage sync services such as Codex JSONL token sync when enabled
10. Reconnect active sessions from last shutdown

Shutdown runs in reverse order, draining queues before closing DB.
