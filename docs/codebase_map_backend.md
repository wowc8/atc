# Executive summary
ATC’s backend is a single-process FastAPI app that keeps durable state in SQLite, coordinates live agent terminals through tmux, and uses an in-process async EventBus plus WebSocket hub to glue the UI, session lifecycle, and background monitors together. The most central paths are `api/app.py` startup, `state/db.py` CRUD and schema, `session/ace.py` for tmux-backed session creation, `tower/controller.py` for top-level goal orchestration, `leader/leader.py` plus `leader/orchestrator.py` for manager/Ace delegation, and `terminal/pty_stream.py` for terminal streaming. The architecture is pragmatic and fairly cohesive, but several seams show strain: DB access is a huge god-module, orchestration logic spans routers/controllers/session helpers, provider abstraction is only partially adopted, and some lifecycle state is split between SQLite and in-memory controller fields.

# ATC backend codebase map

## 1. High-level shape
- Backend root: `src/atc`
- Main runtime style: async FastAPI + one shared `aiosqlite` connection + background tasks/monitors.
- Orchestration substrate:
  - SQLite persists projects, leaders, sessions, task graphs, assignments, heartbeats, context, usage, QA runs, backup log, etc.
  - tmux provides the actual long-lived interactive agent terminals.
  - `PtyStreamPool` pipes tmux pane output into the EventBus and WebSockets.
  - `EventBus` coordinates internal side effects.
  - `WsHub` is the frontend pub/sub transport.

## 2. Directory and module map

### `api/`
- `api/app.py`: app factory and lifespan. Most important composition root.
- `api/routers/`
  - `aces.py`: CRUD/start/stop/message/status for Ace sessions.
  - `tower.py`: Tower session start/stop, goal submission, progress, memory, token usage reporting.
  - `leader.py`: Leader decomposition, Ace spawning, task completion/failure, cleanup.
  - `context.py`: context hub CRUD and broadcast.
  - plus support routers for projects, memory, backup, usage, failure logs, heartbeat, settings, QA, system, etc.
  - `tasks.py` is currently just a stub router.
- `api/ws/`
  - `hub.py`: channel-based websocket pub/sub.
  - `terminal.py`, `browser.py`, `state.py`: ancillary websocket/state support.

### `state/`
- `db.py`: schema, migrations, connection helpers, and most CRUD. This is the persistence center of gravity.
- `models.py`: dataclass row models.
- `migrations/`: migration support, but most active schema creation still lives inline in `db.py`.

### `session/`
- `ace.py`: tmux spawning, instruction delivery, lifecycle ops, verification/reliability helpers.
- `reconnect.py`: startup reconnection/respawn logic for existing sessions.
- `state_machine.py`: allowed session transitions and event emission.
- `tunnel.py`: remote/session plumbing.

### `tower/`
- `controller.py`: singleton orchestration controller for top-level goals and Tower/Leader coordination.
- `session.py`: Tower session start/stop/message helpers.
- `allocator.py`: capacity logic.
- `memory.py`: currently stubbed.

### `leader/`
- `leader.py`: Leader session lifecycle, config deploy, tmux spawn, messaging.
- `orchestrator.py`: runtime loop for ready-task discovery, Ace spawn/assignment, cleanup, retry.
- `decomposer.py`: task graph decomposition helpers.
- `context_package.py`: context assembly for Leader/Ace views.

### `agents/`
- `base.py`: provider protocol and metadata types.
- `factory.py`: provider registry, launch command lookup, plugin loading.
- `claude_provider.py`: tmux-backed provider wrapping current Claude Code flow.
- `opencode_provider.py`: alternate provider implementation.
- `deploy.py`: stages CLAUDE.md, hooks, settings for Tower/Leader/Ace.
- `auth.py`: auth mode and API key resolution.

### `terminal/`
- `control.py`: lower-level tmux control-mode/send-keys helpers.
- `pty_stream.py`: tmux `pipe-pane` to FIFO to asyncio streaming.
- `monitor.py`, `output_parser.py`: terminal/session monitoring utilities.

### Background/support subsystems
- `core/events.py`: simple in-process async pub/sub.
- `core/heartbeat.py`: stale-session monitor.
- `core/cleanup.py`: startup cleanup of orphaned state.
- `tracking/`: resource monitor, token usage tracker, GitHub tracker, budget enforcer.
- `memory/cron.py`, `memory/consolidation.py`, `memory/ace_stm.py`, `memory/ltm.py`: memory services.
- `qa/loop.py`: QA feedback loop driven off GitHub PR rows.
- `backup/`: local/cloud backup services.

## 3. Startup and composition flow
Central file: `src/atc/api/app.py`.

`lifespan()` does almost all runtime wiring:
1. Runs DB migrations via `run_migrations()`.
2. Starts `EventBus`.
3. Opens one persistent `aiosqlite` app connection and enables WAL/foreign keys.
4. Creates `WsHub`.
5. Creates `TowerController(db, event_bus, ws_hub)`.
6. Starts `PtyStreamPool` and wires:
   - EventBus `pty_output` -> `WsHub.broadcast("terminal:{session_id}")`
   - websocket terminal input -> PTY send keys
   - websocket resize -> tmux resize
   - terminal subscribe -> capture current pane content
7. Subscribes session lifecycle events to PTY reader attach/detach and UI state broadcast.
8. Starts `HeartbeatMonitor` and wires websocket heartbeats.
9. Runs startup cleanup and smoke test.
10. Reconnects persisted sessions and restarts PTY readers.
11. Restores TowerController state from DB if tower/leader sessions survived restart.
12. Starts resource/token/github/token-limit/memory/backup background services.
13. Optionally auto-starts Tower if there are user projects and no restored tower session.

This file is effectively the service container and operational playbook.

## 4. Main request/control flows

### A. Ace creation and work flow
Primary files: `api/routers/aces.py`, `session/ace.py`, `session/state_machine.py`, `state/db.py`.

Flow:
1. `POST /api/projects/{project_id}/aces` in `aces.py` validates project and chooses provider launch command.
2. `session.ace.create_ace()`:
   - creates DB session row first with status `connecting`
   - optionally deploys staged config files using the real session id
   - publishes `session_created`
   - prepares workspace via provider hook
   - ensures tmux session exists, spawns pane, auto-accepts trust dialog
   - stores `tmux_session`/`tmux_pane`
   - transitions `connecting -> idle`
3. `api/app.py` subscriber sees idle session and starts PTY streaming for its pane.
4. `POST /api/aces/{session_id}/start` calls `start_ace()`:
   - validates state transition
   - sets status `working`
   - sends initial instruction with readiness/delivery verification.
5. Hooks or APIs later update session status through `PATCH /api/aces/{id}/status`.
6. `DELETE /api/aces/{id}` kills pane and removes DB row.

Important implementation trait: DB-first entity creation means the UI sees the session even if tmux spawn later fails.

### B. Tower goal orchestration flow
Primary files: `tower/controller.py`, `tower/session.py`, `leader/leader.py`, `leader/orchestrator.py`, `leader/context_package.py`.

Flow:
1. `POST /api/tower/start` optionally starts Tower’s own session.
2. `POST /api/tower/goal` calls `TowerController.submit_goal(project_id, goal)`.
3. `submit_goal()`:
   - enforces tower state/budget/capacity guards
   - moves Tower into `planning -> managing`
   - builds context package from project metadata + context hub
   - writes leader context/goal to DB
   - seeds some project context entries
   - starts or reuses a Leader session via `leader.start_leader()`
   - broadcasts leader status to UI
   - sends kickoff prompt to Leader
   - starts a background verification/nudge loop
4. Leader then uses the leader API to decompose tasks and spawn Aces.
5. `TowerController.get_progress()` derives task status aggregates from `task_graphs`.
6. When done, `mark_complete()` moves Tower to `complete`; cleanup/reset paths move it back to idle.

### C. Leader decomposition and Ace delegation
Primary files: `api/routers/leader.py`, `leader/orchestrator.py`, `leader/decomposer.py`.

Flow:
1. `POST /api/projects/{project_id}/leader/decompose`
   - reads leader row/context
   - turns incoming task specs into `task_graphs`
   - broadcasts task graph updates.
2. `POST /leader/spawn-aces`
   - gets/creates in-memory `LeaderOrchestrator` for that project
   - asks it to spawn Aces for dependency-ready tasks.
3. `LeaderOrchestrator.spawn_aces_for_ready_tasks()`:
   - lists task graphs
   - finds ready tasks
   - enforces global concurrency/resource governor
   - spawns Ace sessions
   - writes idempotent `task_assignments`
   - advances task status to `assigned` then `in_progress`.
4. `POST /leader/instruct` sends the actual prompt to the assigned Ace.
5. `task-done` / `task-failed` update task graph and assignment state, destroy Ace sessions, decrement global counters, and enable retries.

## 5. Session lifecycle ownership

### Ownership layers
- Persistence truth: `sessions` table in SQLite.
- Legal transitions: `session/state_machine.py`.
- tmux process ownership: `session/ace.py` and `leader/leader.py` plus `tower/session.py`.
- Live stream ownership: `terminal/pty_stream.py`.
- Cross-restart restoration: `session/reconnect.py` and `api/app.py` startup restore logic.
- High-level orchestration ownership:
  - Tower singleton holds current goal/session pointers in memory.
  - Per-project `LeaderOrchestrator` holds active task-to-Ace assignments in memory.

### Session types
- `ace`: worker agents.
- `manager`: Leader session for a project.
- `tower`: top-level Tower session.

### Notable lifecycle design choices
- Session rows are created before panes spawn.
- Status changes publish `session_status_changed` events but DB persistence is done separately by callers.
- Reconnect logic skips tower/manager sessions for normal reconnection because Tower restore logic handles them specially.
- PTY readers are attached lazily when session status reaches `idle` and a live pane exists.

## 6. Data and persistence model
Primary file: `state/db.py`.

### Core relational entities
- `projects`: repo path, github repo, selected provider, status, ordering.
- `leaders`: one per project, stores linked session id, goal, serialized context package, status.
- `sessions`: all tower/leader/ace terminals plus tmux identifiers and runtime flags.
- `task_graphs`: decomposed tasks and dependency list.
- `task_assignments`: idempotent Ace-to-task assignment records.
- `context_entries`: scoped context hub entries (`global`, `project`, `tower`, `leader`, `ace`).
- `session_heartbeats`: liveness tracking.
- `usage_events`, `project_budgets`, `github_prs`, `qa_loop_runs`, `tower_memory`, `ace_stm`, `failure_logs`, `app_events`, `backup_log`, etc.

### Persistence patterns
- Very wide CRUD surface centralized in one module.
- JSON blobs are stored in TEXT columns for context, dependencies, metadata-like fields.
- WAL mode and busy timeout are enabled for runtime use.
- Many tables are created through a monolithic `_SCHEMA_SQL` string, with small additive migration checks afterwards.
- Some business invariants live in Python, not DB constraints, for example task graph status transitions.

### Context visibility model
`get_context_for_agent()` encodes inheritance:
- Ace sees global + project + parent leader + own session entries.
- Leader sees global + project + own leader entries.
- Tower sees global + own tower entries.

## 7. Provider/runtime abstractions
Primary files: `agents/base.py`, `agents/factory.py`, `agents/claude_provider.py`.

### Intended abstraction
`AgentProvider` protocol abstracts:
- session spawn
- workspace prep
- readiness checks
- prompt send
- status lookup
- output streaming
- stop/list

### Actual runtime reality today
- The abstraction exists, but the system is still tmux-first.
- `factory.get_launch_command()` is widely used to choose the CLI command.
- `ClaudeCodeProvider` wraps the existing tmux approach and is tested for workspace prep/readiness.
- Core lifecycle code still directly calls low-level tmux helpers in `session/ace.py` and `leader/leader.py` rather than delegating fully through a provider instance.
- Provider hooks are used opportunistically for workspace prep, not as the exclusive execution path.

So the provider layer is a partial seam, not yet the dominant architecture.

## 8. Orchestration boundaries

### API boundary
- FastAPI routers validate input and translate domain errors to HTTP.
- Routers are mostly thin, but `leader.py` also owns an in-process orchestrator cache, which is a notable leak of orchestration state into the API layer.

### Orchestration boundary
- `TowerController` owns top-level goal lifecycle and Tower/Leader coordination.
- `LeaderOrchestrator` owns task readiness, Ace spawning, task completion/failure handling.

### Runtime/process boundary
- tmux is the process/session boundary for interactive agents.
- PTY FIFO piping is the streaming boundary.
- WebSocket channels are the frontend live-state boundary.

### Persistence boundary
- SQLite is the durable source of truth for nearly everything except some controller-local runtime state.

## 9. Recurring code patterns
- DB-first creation, then external side effect, then status transition.
- Event-driven side effects via `EventBus.publish()` after session/tower changes.
- Best-effort broadcasting to `WsHub` after important state changes.
- tmux reliability helpers: trust-dialog acceptance, prompt readiness checks, capture-pane verification, pane-alive checks.
- Startup reconciliation: cleanup, smoke test, reconnect, restore, then background monitors.
- In-memory caches for orchestration (`TowerController` singleton fields, `_orchestrators` dict, global Ace counter).
- Repeated state duplication across DB status + in-memory fields + websocket messages.

## 10. Central files and functions

### Most central files
- `src/atc/api/app.py`
- `src/atc/state/db.py`
- `src/atc/session/ace.py`
- `src/atc/tower/controller.py`
- `src/atc/leader/leader.py`
- `src/atc/leader/orchestrator.py`
- `src/atc/terminal/pty_stream.py`
- `src/atc/api/ws/hub.py`

### Central functions/methods
- `api.app.lifespan()`
- `state.db.run_migrations()`
- `state.db.create_session()`, `update_session_status()`, `assign_task()`, `get_context_for_agent()`
- `session.ace.create_ace()`, `start_ace()`, `send_instruction()`, `verify_session()`
- `session.reconnect.reconnect_session()` / `reconnect_all()`
- `tower.controller.TowerController.submit_goal()`, `start_session()`, `get_progress()`
- `leader.leader.start_leader()`, `send_leader_message()`
- `leader.orchestrator.LeaderOrchestrator.spawn_aces_for_ready_tasks()`, `_spawn_ace_for_task()`, `mark_task_done()`, `mark_task_failed()`
- `terminal.pty_stream.PtyStreamPool.add_session()`
- `core.heartbeat.HeartbeatMonitor.handle_heartbeat()` / `_check_heartbeats()`

## 11. Tests and what they cover

### Strongly represented areas
- `tests/e2e/test_ace_lifecycle.py`: end-to-end REST lifecycle for Ace sessions with tmux mocked.
- `tests/unit/test_db.py`: connection factory, migrations, project/leader/session CRUD.
- `tests/unit/test_tower_controller.py`: Tower state transitions, goal submission, monitoring behavior.
- `tests/unit/test_provider_abstraction.py`: Claude provider workspace prep/readiness.
- `tests/integration/test_websocket_hub.py`: websocket hub behavior.
- Several session/tmux reliability tests: `test_terminal_control.py`, `test_pty_stream.py`, `test_state_machine.py`, `test_tower_session_reuse.py`, `test_tower_respawn.py`, `test_creation_reliability.py`.

### Architectural signal from tests
- The backend is tested more at behavioral boundaries than at pure domain boundaries.
- tmux flows are heavily mocked, which is practical but means some orchestration complexity is only partially integration-tested.

## 12. Concrete tech debt and refactor seams

### A. `state/db.py` is a god-module
Symptoms:
- schema definition, migrations, and almost all CRUD live together
- spans unrelated domains: projects, sessions, context, budgets, QA, backups, GitHub, heartbeats.

Refactor seam:
- split by aggregate/domain (`projects_repo.py`, `sessions_repo.py`, `context_repo.py`, etc.) while keeping `models.py` and shared connection helpers.

### B. Provider abstraction is incomplete
Symptoms:
- lifecycle code still calls tmux primitives directly
- provider is used for workspace prep and launch command lookup, not as the canonical runtime path.

Refactor seam:
- move spawn/send/status/stream responsibilities fully behind provider adapters; make tmux-backed Claude provider the first concrete implementation.

### C. Runtime state is split between DB and process memory
Symptoms:
- Tower current goal/session ids live in controller fields
- Leader orchestrators live in module-global cache
- global active Ace count is process-local
- restart recovery needs special-case restore logic.

Risk:
- stale in-memory state, restart edge cases, hard horizontal scaling.

Refactor seam:
- persist more orchestration state explicitly, or create a dedicated runtime-state service with clearer ownership.

### D. Orchestration concerns bleed into routers
Symptoms:
- `api/routers/leader.py` owns orchestrator cache creation and event subscription.

Refactor seam:
- introduce an application service/facade layer so routers stay thin transport adapters.

### E. Session state transitions are not transactionally coupled to DB writes
Symptoms:
- `transition()` only publishes events; callers separately persist DB changes.

Risk:
- event says one thing, DB write fails, or vice versa.

Refactor seam:
- add a transaction-aware session service that validates, persists, and emits in one place.

### F. Startup flow is doing too much in one function
Symptoms:
- `lifespan()` owns migrations, bus wiring, PTY wiring, reconnection, tower restore, monitors, smoke tests, auth checks, backup scheduling.

Refactor seam:
- extract startup coordinators/modules for infra init, session recovery, tower recovery, and background services.

### G. Multiple overlapping terminal control paths
Symptoms:
- direct `_tmux_run` helpers, `terminal/control.py`, and `ClaudeCodeProvider` all encapsulate parts of tmux interaction.

Refactor seam:
- consolidate to one tmux service layer used by session lifecycle and provider implementations.

### H. Stubbed or half-finished areas
- `api/routers/tasks.py` is empty.
- `tower/memory.py` is a stub despite tower memory table existing.
- Some orchestration features appear to exist mainly through side effects and tests rather than cohesive domain modules.

## 13. Overall read on the architecture
The backend is a practical orchestration server built around a strong core idea: SQLite for durable coordination state, tmux for durable interactive agent processes, and a simple in-process event/websocket layer for liveliness. That core is clear and mostly consistent. The main limitations are not conceptual but organizational: too much logic is concentrated in a few large modules, several abstractions are only half-complete, and runtime ownership is split across DB rows, tmux state, and in-memory controller caches.