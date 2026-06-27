# API Reference

> Full REST API + WebSocket reference for ATC.

## Base URL

```
http://127.0.0.1:8420/api
```

## Health & Version

```
GET /api/health    → {"ok": true, "version": "0.1.0"}
GET /api/version   → version info
```

## Projects

```
GET    /api/projects                          → list all projects
POST   /api/projects                          → create project
GET    /api/projects/{id}                     → project detail + Leader status
PUT    /api/projects/{id}                     → update project metadata
DELETE /api/projects/{id}                     → archive project

GET    /api/projects/{id}/manager             → Leader detail + task graph
POST   /api/projects/{id}/leader/start        → start Leader session
POST   /api/projects/{id}/leader/stop         → stop Leader session
POST   /api/projects/{id}/leader/message      → send message to Leader
POST   /api/projects/{id}/leader/report-active → Leader reports kickoff goal acceptance
GET    /api/projects/{id}/leader/health       → provider-neutral Leader runtime/kickoff health
POST   /api/projects/{id}/leader/recover      → inspect-first Leader recovery dry-run/apply
```

### Leader start kickoff truth

`POST /api/projects/{id}/leader/start` returns additive kickoff truth fields when a goal is provided. Consumers must not treat `session_id`, `leader_session_id`, `status`, `delivery_state`, or `202`-style queued/submitted wording as proof that the Leader accepted the goal.

Important fields:

- `kickoff_state`: staged delivery/kickoff state such as `not_requested`, `queued_unverified`, `runtime_created`, `payload_written`, `submitted_pending_acceptance`, `accepted_active`, `blocked`, or `failed`.
- `kickoff_verified`: `true` only when provider-neutral evidence shows the Leader accepted the kickoff and began work.
- `startup_handshake_state`: provider-neutral startup readiness, e.g. `not_started`, `runtime_created`, `ready`, `blocked`, or `failed`.
- `goal_acceptance_state`: whether the goal is `not_submitted`, `submitted_pending_acceptance`, `accepted_active`, `blocked`, or `failed`.
- `delivery_trace_id`: trace id correlating startup, payload persistence, prompt delivery, submit, and later recovery events.
- `kickoff_payload_persisted`: whether the original Leader kickoff payload was saved for recovery planning.
- `kickoff_blocker_reason` / `kickoff_recovery_recommendation`: structured blocker and inspect-first recovery guidance when kickoff is blocked or unverified.

`GET /api/projects/{id}/leader/health` exposes the same kickoff dimensions under `kickoff_state` alongside runtime, task graph, and recovery summaries.

### Leader health and recovery

`GET /api/projects/{id}/leader/health` is the canonical operator/Tower view for a Leader's runtime truth. Important top-level fields include:

- `leader_state`: normalized health state for the Leader, such as healthy, unverified, blocked, or missing.
- `runtime_state`: provider-neutral runtime state; product layers branch on this, not raw terminal text.
- `delivery_state`: provider-neutral kickoff delivery state.
- `blocker_reason`: stable blocker reason such as `pane_missing`, `runtime_trust_required`, `runtime_permission_required`, or `prompt_not_submitted`.
- `recovery_recommendation`: concise safe next action for tooling.
- `operator_guidance`: user-facing summary with severity, recommended action, command, and details.
- `provider_diagnostics`: redacted nested provider details for debugging only; orchestration must not branch on provider-specific prompt strings.

`POST /api/projects/{id}/leader/recover` plans or applies inspect-first recovery. Use dry-run before mutation. A typical operator flow is:

```bash
atc leader health --project-id <project-id> --summary
atc leader recover --project-id <project-id> --dry-run
```

Recovery apply policies must remain explicit; pressing Enter or resending a persisted kickoff is not success until health/kickoff verification changes.

## Tasks / Task Graphs

```
GET    /api/projects/{id}/task-graphs         → task graph list with task/runtime/delivery truth
POST   /api/projects/{id}/task-graphs         → create task graph entry
GET    /api/task-graphs/{id}                  → task graph detail with runtime truth
PATCH  /api/task-graphs/{id}                  → update task graph metadata
PATCH  /api/task-graphs/{id}/status           → transition product task state
POST   /api/task-graphs/{id}/assign           → assign to Ace session
GET    /api/task-graphs/{id}/assignments      → assignment delivery/runtime records
PATCH  /api/task-assignments/{assignment_id}/status → transition assignment state
```

Task graph responses keep `status` as a legacy product task-state field, but also expose explicit truth fields:

- `task_state`: product task lifecycle (`todo`, `assigned`, `in_progress`, `done`, ...).
- `runtime_state`: provider-neutral runtime truth for the assigned Ace (`idle`, `starting`, `active`, `blocked`, `complete`, `failed`, ...).
- `delivery_state`: provider-neutral instruction/dispatch truth (`not_started`, `queued_unverified`, `submitted_pending_acceptance`, `accepted_active`, `blocked`, ...).
- `dispatch_verified`: whether ATC has evidence that the Ace accepted/started the dispatch.
- `blocker_reason`: structured blocker code when present.
- `runtime_truth`: nested summary containing the same task/runtime/delivery split plus evidence identifiers.

`GET /api/tower/progress` keeps the legacy `todo`/`in_progress`/`done` task counters and adds separate `task_states`, `runtime_states`, `delivery_states`, `blocked`, and `dispatch_unverified` summaries. The progress endpoint therefore reports product progress and runtime truth as separate dimensions instead of collapsing assignment intent into "working".

A task in `assigned` state is ownership intent only. UI/API consumers must not treat it as active Ace work unless `delivery_state`/`runtime_state` provide that evidence.

First-class CLI helpers are thin wrappers over these REST contracts and are preferred for managed agents:

```bash
atc tasks create --project-id <project-id> --title "..." --description "..."
atc tasks assign --project-id <project-id> --task-id <task-id>
atc leader bootstrap-tasks --project-id <project-id> --goal "..." --task "..."
```

These commands return stable task/session identifiers plus task/runtime/delivery truth so Leaders do not need to inspect OpenAPI before creating or assigning normal task graphs.

## Aces

```
GET    /api/projects/{id}/aces                → list ace sessions
POST   /api/projects/{id}/aces                → spawn new ace
GET    /api/projects/{id}/aces/{ace_id}/health → provider-neutral Ace runtime/assignment health
POST   /api/projects/{id}/aces/{ace_id}/report-active → Ace reports assignment acceptance
POST   /api/projects/{id}/aces/{ace_id}/report-artifact → Ace reports canonical artifact/worktree path
POST   /api/projects/{id}/aces/{ace_id}/recover → inspect-first Ace recovery dry-run/apply
POST   /api/aces/{id}/start                   → start session
POST   /api/aces/{id}/stop                    → stop session
POST   /api/aces/{id}/message                 → send message to ace
DELETE /api/aces/{id}                         → delete session
```

Leader → Ace handoff uses a separate startup-readiness, assignment-acceptance, and artifact-routing truth contract so Leaders do not confuse session/assignment existence with active Ace work. `ace_dispatch.startup_readiness_state` distinguishes `startup_handshake_pending`, `awaiting_startup_confirmation`, `blocked_on_provider_startup_prompt`, `input_ready`, `runtime_missing`, and `startup_handshake_failed`. `ace_dispatch.assignment_acceptance_state` distinguishes `queued_unverified`, `session_created`, `payload_written`, `submitted_pending_acceptance`, `awaiting_ace_active_report`, `assignment_accepted`, `accepted_active`, `blocked`, and `failed`.

`POST /api/projects/{id}/aces/{ace_id}/report-active` records Ace-side acceptance evidence:

```json
{
  "assignment_id": "optional-assignment-id",
  "assignment_accepted": true,
  "message": "accepted and working"
}
```

The response includes `ace_reported_active`, `assignment_accepted`, `assignment_accepted_at`, `acceptance_message`, and the resulting `ace_dispatch` block. Leaders should treat `dispatch_verified=true` plus `assignment_accepted=true` as stronger evidence than delivery alone; delivery alone may remain `awaiting_ace_active_report`.

`POST /api/projects/{id}/aces/{ace_id}/report-artifact` records canonical artifact routing evidence so verification Aces do not search isolated sibling worktrees:

```json
{
  "assignment_id": "optional-assignment-id",
  "artifact_path": "/absolute/worktree-or-build-output",
  "artifact_kind": "worktree",
  "ready": true
}
```

Leader progress and Ace health expose `artifact_ready`, `artifact_path`, `artifact_kind`, `artifact_reported_at`, `last_provider_activity_at`, and `last_ace_report_at` separately from task lifecycle status.

CLI helpers:

```bash
atc ace health --project-id <project-id> --ace-id <ace-id>
atc ace report-active --project-id <project-id> --ace-id <ace-id> --message "accepted task"
atc ace report-artifact --project-id <project-id> --ace-id <ace-id> --path /absolute/output --kind worktree
atc ace recover --project-id <project-id> --ace-id <ace-id> --dry-run
```

## Tower

```
GET    /api/tower/status                      → Tower session status + summary
POST   /api/tower/goal                        → submit new goal to Tower
GET    /api/tower/memory                      → list tower memory entries
DELETE /api/tower/memory/{key}                → forget a memory entry
```

## Orchestration

```
GET    /api/orchestration/sessions                     → list normalized sessions
GET    /api/orchestration/sessions/{id}                → normalized single session view
POST   /api/orchestration/leaders                      → spawn leader via Tower goal path
POST   /api/orchestration/sessions/{id}/instruction    → send provider-owned instruction to a session
POST   /api/orchestration/sessions/{id}/wait           → wait until session reaches target normalized status
```

Notes:
- This surface exposes normalized orchestration roles (`tower`, `leader`, `ace`) and statuses (`starting`, `ready`, `running`, etc.) rather than raw internal DB strings.
- `send_instruction` deliberately reuses the existing provider-owned delivery path instead of creating a second prompt-delivery implementation.
- `wait` is currently a polling contract over session state, which keeps the API stable while backend event plumbing evolves.

## MCP

ATC also exposes a thin MCP-oriented stdio interface through `atc-mcp`.

Current request methods:

```
tools/list
tools/call
```

Current tool names:

```
list_sessions
get_session
list_operations
get_operation
list_session_events
spawn_leader
spawn_ace
send_instruction
wait_for_session
cancel_session
```

See `docs/mcp.md` for example request/response envelopes and usage notes.

## Usage & Budget

```
GET    /api/usage/tokens                      → token usage history (query: project_id, period)
GET    /api/usage/tokens                      → token history
GET    /api/usage/resources                   → resource samples
GET    /api/usage/github                      → GitHub API usage
GET    /api/usage/summary                     → aggregate summary

GET    /api/projects/{id}/budget              → budget config + current status
PUT    /api/projects/{id}/budget              → update budget limits
POST   /api/projects/{id}/budget/reset        → reset monthly accumulator
```

## GitHub

```
GET    /api/projects/{id}/github/prs          → PR list with CI status
GET    /api/projects/{id}/github/rate-limit   → current API rate limit
POST   /api/projects/{id}/github/sync         → force immediate sync
```

## Settings

```
GET    /api/settings                          → all config key/values
PUT    /api/settings                          → bulk update
```

## Failure Logs

```
GET    /api/logs                              → paginated log list (filters: level, category, project_id)
GET    /api/logs/{id}                         → single log entry with full context
PATCH  /api/logs/{id}/resolve                 → mark resolved
DELETE /api/logs/{id}                         → delete entry
GET    /api/logs/export?ids=a,b,c             → export as formatted text
```

## WebSocket

Connect to `ws://127.0.0.1:8420/ws` and subscribe to channels:

```json
{"channel": "subscribe", "data": ["state", "usage", "manager:proj-123"]}
```

### Channels

| Channel | Direction | Payload |
|---|---|---|
| `state` | Server → Client | Full state snapshot + delta events |
| `tower` | Server → Client | Tower status, goal updates |
| `manager:{project_id}` | Server → Client | Leader status, task graph |
| `usage` | Server → Client | AI token usage events |
| `budget:{project_id}` | Server → Client | Budget status changes |
| `resources` | Server → Client | CPU/RAM/disk samples |
| `github:{project_id}` | Server → Client | PR status, CI results |
| `terminal:{session_id}` | Server → Client | Binary PTY frames |
| `logs` | Server → Client | Failure log entries |
| `input:{session_id}` | Client → Server | Keystroke strings |

### Wire Format

Text frames are JSON: `{"channel": "...", "event": "...", "data": {...}}`

Binary frames for terminal: first 36 bytes = session UUID (ASCII), remaining = PTY output.
