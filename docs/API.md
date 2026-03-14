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

## Tasks

```
GET    /api/projects/{id}/tasks               → task list (filterable by status)
POST   /api/projects/{id}/tasks               → create task
PUT    /api/tasks/{id}                        → update task
DELETE /api/tasks/{id}                        → cancel task
POST   /api/tasks/{id}/assign                 → assign to ace session
```

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
