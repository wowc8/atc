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
```

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

## Aces

```
GET    /api/projects/{id}/aces                → list ace sessions
POST   /api/projects/{id}/aces                → spawn new ace
POST   /api/aces/{id}/start                   → start session
POST   /api/aces/{id}/stop                    → stop session
POST   /api/aces/{id}/message                 → send message to ace
DELETE /api/aces/{id}                         → delete session
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
GET    /api/usage/cost                        → cost history (query: project_id, period)
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
{"channel": "subscribe", "data": ["state", "costs", "manager:proj-123"]}
```

### Channels

| Channel | Direction | Payload |
|---|---|---|
| `state` | Server → Client | Full state snapshot + delta events |
| `tower` | Server → Client | Tower status, goal updates |
| `manager:{project_id}` | Server → Client | Leader status, task graph |
| `costs` | Server → Client | AI usage events |
| `budget:{project_id}` | Server → Client | Budget status changes |
| `resources` | Server → Client | CPU/RAM/disk samples |
| `github:{project_id}` | Server → Client | PR status, CI results |
| `terminal:{session_id}` | Server → Client | Binary PTY frames |
| `logs` | Server → Client | Failure log entries |
| `input:{session_id}` | Client → Server | Keystroke strings |

### Wire Format

Text frames are JSON: `{"channel": "...", "event": "...", "data": {...}}`

Binary frames for terminal: first 36 bytes = session UUID (ASCII), remaining = PTY output.
