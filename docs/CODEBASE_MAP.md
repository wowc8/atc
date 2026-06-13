# ATC Codebase Map

This is the durable high-level code organization guide for ATC.

It should stay shorter and more stable than deep-dive mapping docs. When the architecture changes materially, update this file.

## Product model
ATC is an orchestration platform with three main runtime roles:
- `tower` , top-level orchestration and goal management
- `leader` , project-scoped decomposition and supervision
- `ace` , task-scoped execution

## Main layers

### 1. API and app composition
Primary path:
- `src/atc/api/`

Important files:
- `src/atc/api/app.py`
  - application composition root and runtime startup
- `src/atc/api/routers/`
  - HTTP routes for projects, tower, leader, aces, context, settings, etc.
- `src/atc/api/ws/`
  - websocket hub/state integration

### 2. State and persistence
Primary path:
- `src/atc/state/`

Important files:
- `src/atc/state/db.py`
  - current persistence center of gravity, large and high-risk
- `src/atc/state/models.py`
  - persistent data models

### 3. Runtime and session control
Primary paths today:
- `src/atc/session/`
- `src/atc/terminal/`
- `src/atc/tower/`
- `src/atc/leader/`

Important files today:
- `src/atc/session/ace.py`
- `src/atc/session/reconnect.py`
- `src/atc/terminal/control.py`
- `src/atc/terminal/pty_stream.py`
- `src/atc/tower/controller.py`
- `src/atc/leader/leader.py`
- `src/atc/leader/orchestrator.py`

### 4. Provider/runtime abstraction work
Current/future paths:
- current partial abstraction: `src/atc/agents/`
- target refactor direction: `src/atc/runtime/` and `src/atc/providers/`

See:
- `docs/runtime_truth_recovery_plan.md` — provider-neutral runtime truth, delivery verification, health, and recovery model
- `docs/leader_kickoff_recovery_plan.md` — follow-up phased plan for proving Leader goal acceptance, startup prompt recovery, task graph ergonomics, and managed-agent local API capability setup
- `docs/provider_runtime_refactor_plan.md`
- `docs/provider_cli_wrapper_spec.md`
- `docs/RUNTIME_PROVIDER_GUARDRAILS.md`

### 5. Frontend and desktop shell
Primary paths:
- `frontend/src/`
- `src-tauri/`

Important files:
- `frontend/src/context/AppContext.tsx`
  - current frontend state center of gravity, large and high-risk
- `frontend/src/pages/ProjectView.tsx`
- `frontend/src/components/tower/*`
- `frontend/src/components/leader/*`
- `frontend/src/components/ace/*`
- `src-tauri/src/lib.rs`
  - thin shell/sidecar layer

## High-risk modules
Be extra careful when touching these:
- `src/atc/state/db.py`
- `src/atc/tower/controller.py`
- `src/atc/api/routers/projects.py`
- `frontend/src/context/AppContext.tsx`

These files currently carry broad ownership and can create architecture drift quickly.

## Known drift areas
- `leader` vs `manager` terminology
- legacy `tasks` vs active `task_graphs`
- docs/API/runtime drift in some areas
- partial provider abstraction that is not yet the real runtime boundary

## Rule for changes
If a change materially alters ownership, flow, folder structure, runtime boundaries, or architectural expectations, update this file.

## Control stack layering
For current and future refactor work, use this conceptual stack:
- MCP / external control surface
- orchestration/application services
- runtime service
- provider runtimes
- provider wrapper + hooks
- tmux substrate

The runtime/provider refactor is establishing the middle layers so MCP and hooks have a cleaner, more stable foundation.
