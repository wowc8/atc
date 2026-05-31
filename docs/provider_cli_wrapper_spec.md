# ATC provider CLI wrapper spec

## Status
Draft design spec for the provider-runtime refactor.

## Purpose
`atc-provider` is the terminal-facing adapter layer between ATC's Python runtime/orchestration code and provider-specific AI CLIs such as Claude Code, Codex, and OpenCode.

It is not a second orchestration system.
It is not the source of truth for workflow decisions.
It is a thin but stable command contract that lets tmux launch and interact with providers in a uniform way while preserving provider-specific behavior where it belongs.

## Architectural position

### Python orchestration/runtime owns
- which project, goal, or task should run
- when a role session starts or stops
- which task is assigned to which Ace
- persistence, workflow state transitions, retries, and policy
- provider selection from config/session metadata

### `atc-provider` owns
- provider-specific terminal launch behavior
- provider-specific flags/env/bootstrap
- readiness checks and startup waits
- provider-specific delivery mechanics into the running session
- normalized machine-readable terminal markers

## Hard boundary rule
ATC Python code must not encode provider-specific sleeps, trust/login handling, prompt-shaping rules, or CLI command strings once the wrapper exists for that operation.

If a provider needs special startup/delivery handling, that behavior belongs in:
- `src/atc/providers/<provider>/...`
- or the `atc-provider` command implementation for that provider

Never add scattered logic like:
- `if provider == "codex": sleep(...)`
- `if provider == "claude_code": use different paste flow`
- `if provider == "opencode": special-case readiness here`

outside the provider runtime/wrapper boundary.

## Binary shape
Preferred invocation shape:
```bash
atc-provider <provider> <command> [options]
```

Examples:
```bash
atc-provider claude_code start-role --role leader --session-id sess_1 --project-id proj_1 --working-dir /repo
atc-provider codex assign-task --session-id sess_ace_1 --task-id task_1 --context-ref ctx:task:1 --message-file /tmp/task.txt
atc-provider codex check-readiness --session-id sess_ace_1
```

## Command set
Required command family:
- `start-role`
- `stop-role`
- `send-instruction`
- `assign-task`
- `check-readiness`
- `inspect-session`
- `restore-session`

### Command design rule
- startup commands are role-based
- post-start commands are session-based

That means:
- `start-role` and `stop-role` accept `--role tower|leader|ace`
- `send-instruction`, `assign-task`, `check-readiness`, `inspect-session`, and `restore-session` primarily target `--session-id`

## Command specs

## 1. `start-role`

### Required args
- `--role tower|leader|ace`
- `--session-id <id>`

### Optional args
- `--project-id <id>`
- `--working-dir <path>`
- `--context-ref <ref>`
- `--display-name <label>`
- `--provider-config-ref <ref>`
- `--bootstrap-file <path>`
- `--metadata-json <json>`
- `--metadata-file <path>`

### Responsibilities
- emit `runtime_starting`
- apply provider-specific bootstrap/env/flags
- launch the provider CLI
- wait for provider-specific readiness condition
- emit one terminal result marker: `runtime_ready`, `runtime_blocked`, or `runtime_error`

### Exit codes
- `0` role started and reached a valid ready/running state
- `10` blocked by trust/login/auth
- `11` transient startup not ready
- `12` provider executable/process failure
- `13` invalid args or unsupported role/provider combination
- `30` wrapper internal failure

## 2. `stop-role`

### Required args
- `--role tower|leader|ace`
- `--session-id <id>`

### Optional args
- `--reason <text>`
- `--graceful true|false`

### Responsibilities
- emit `runtime_stopping`
- perform provider-appropriate shutdown if required
- emit `runtime_stopped` or `runtime_error`

## 3. `send-instruction`

### Required args
- `--session-id <id>`
- one of:
  - `--message <text>`
  - `--message-file <path>`

### Optional args
- `--context-ref <ref>`
- `--instruction-id <id>`
- `--expects-readiness-check true|false`
- `--metadata-json <json>`

### Responsibilities
- optionally verify readiness
- deliver a general instruction using provider-specific paste/submit/timing behavior
- emit delivery lifecycle markers

### Exit codes
- `0` delivery confirmed
- `10` blocked by trust/login/auth
- `11` session busy/not ready
- `20` delivery failed
- `30` wrapper internal failure

## 4. `assign-task`

### Required args
- `--session-id <id>`
- `--task-id <id>`
- one of:
  - `--message <text>`
  - `--message-file <path>`

### Optional args
- `--context-ref <ref>`
- `--task-title <text>`
- `--assignment-id <id>`
- `--metadata-json <json>`

### Responsibilities
- provider-specialized task delivery path for Ace-like work assignment
- may differ from generic `send-instruction` in formatting, pacing, and readiness behavior
- emits task assignment markers

### Exit codes
- `0` assignment delivery confirmed
- `10` blocked by trust/login/auth
- `11` session busy/not ready
- `20` assignment delivery failed
- `30` wrapper internal failure

## 5. `check-readiness`

### Required args
- `--session-id <id>`

### Responsibilities
- evaluate provider-specific readiness state
- emit a normalized readiness result marker

### Exit codes
- `0` ready
- `10` blocked by auth/trust/login
- `11` not ready yet / busy
- `12` session missing or dead
- `30` wrapper internal failure

## 6. `inspect-session`

### Required args
- `--session-id <id>`

### Responsibilities
- return normalized health/inspection result
- optionally emit short status summary and output excerpt metadata

### Exit codes
- `0` inspect succeeded
- `12` session missing or dead
- `30` wrapper internal failure

## 7. `restore-session`

### Required args
- `--session-id <id>`

### Optional args
- `--tmux-session <name>`
- `--tmux-pane <pane>`

### Responsibilities
- validate and interpret a prior running provider session after restart
- emit normalized restore result

### Exit codes
- `0` restore succeeded / session still viable
- `12` runtime target missing
- `21` restore failed / unrecoverable
- `30` wrapper internal failure

## Marker protocol

### Format
Every machine-readable line emitted by the wrapper must use:
```text
ATC_EVENT <event_name> <json_payload>
```

### Event name rules
- lowercase snake_case only
- stable names, additive evolution preferred
- no provider-specific event names in shared orchestration handling

### Required event families
Runtime lifecycle:
- `runtime_starting`
- `runtime_ready`
- `runtime_blocked`
- `runtime_error`
- `runtime_stopping`
- `runtime_stopped`

Delivery lifecycle:
- `delivery_started`
- `delivery_confirmed`
- `delivery_blocked`
- `delivery_error`

Task lifecycle:
- `task_assignment_started`
- `task_assignment_confirmed`
- `task_assignment_blocked`
- `task_assignment_error`

Inspection/readiness:
- `readiness_result`
- `inspection_result`
- `restore_result`

### Required payload fields
All payloads must include:
- `session_id`
- `provider`
- `command`

When applicable, also include:
- `role`
- `project_id`
- `task_id`
- `instruction_id`
- `assignment_id`
- `reason`
- `message`
- `details`

### Example markers
```text
ATC_EVENT runtime_starting {"session_id":"sess_123","provider":"codex","command":"start-role","role":"ace"}
ATC_EVENT runtime_ready {"session_id":"sess_123","provider":"codex","command":"start-role"}
ATC_EVENT runtime_blocked {"session_id":"sess_123","provider":"claude_code","command":"start-role","reason":"login"}
ATC_EVENT task_assignment_confirmed {"session_id":"sess_123","provider":"codex","command":"assign-task","task_id":"task_789"}
```

## CLI implementation rules
- The wrapper must be deterministic for the same inputs.
- Provider-specific sleeps/waits are allowed here, not in shared orchestration code.
- All provider command strings and startup flags must live in provider-owned implementation files.
- Message delivery logic must support both inline text and file-based payloads.
- Large instructions/task briefs should prefer `--message-file`.
- The wrapper must never mutate ATC DB state directly.
- Human-readable output is fine, but machine-readable event lines must remain parseable and stable.

## Python integration rules
The runtime service should:
- resolve provider and session metadata
- build wrapper command invocations
- consume wrapper markers
- map exit codes and events into ATC runtime state

The runtime service must not:
- duplicate provider-specific sleeps or timing hacks
- hardcode provider CLI flags in orchestration code
- parse provider-specific terminal quirks outside provider runtime logic unless explicitly documented as shared marker parsing

## Future extensibility rules
- New provider support must implement the same command family before it is considered first-class.
- Provider-specific extra flags are allowed, but shared orchestration should not depend on them directly.
- Shared command semantics must remain stable across providers.
- If a command cannot be meaningfully supported for a provider, the wrapper must fail clearly rather than silently degrading behavior.

## Relationship to the broader refactor
This CLI spec is one layer inside the broader ATC runtime/provider refactor. It should be implemented alongside:
- `docs/codebase_map_current_state.md`
- `docs/provider_runtime_refactor_plan.md`
- provider runtime modules under `src/atc/providers/`
- shared tmux substrate under `src/atc/runtime/tmux/`

## Recommended first implementation slice
1. implement the shared `atc-provider` binary shell
2. implement common event emitter utilities
3. implement `start-role`, `check-readiness`, `inspect-session`
4. implement Claude Code provider wrapper path
5. implement Codex provider wrapper path
6. implement `send-instruction`
7. implement `assign-task`
8. implement `restore-session`
9. add `stop-role` provider nuances only where needed

## Final recommendation
Yes, ATC should own a provider CLI wrapper layer.

But it must stay:
- thin
- procedural
- provider-specific only in terminal behavior
- subordinate to Python orchestration
- aligned with the larger ATC runtime/provider refactor
