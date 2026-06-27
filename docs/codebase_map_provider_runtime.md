# Executive summary

ATC has a provider abstraction on paper, but the live runtime is still overwhelmingly tmux and Claude-centric. The abstraction lives in `src/atc/agents/base.py` and `src/atc/agents/factory.py`, yet most real lifecycle code for Tower, Leader, and Ace still launches tmux panes directly through `_spawn_pane()` and `get_launch_command()` instead of driving `AgentProvider.spawn_session()` / `send_prompt()`. That split is the main reason provider switching still feels unstable: the UI, settings, and project metadata say providers are selectable, but the operational control plane still assumes a shared tmux-backed terminal model, Claude startup dialogs, Claude-specific working-dir/config deployment, and PTY-based restore/reconnect semantics.

The biggest root causes are: (1) two session-control architectures coexisting, provider protocol vs direct tmux control, (2) inconsistent provider use across roles, especially Ace creation hardcoding Claude workspace prep, (3) multiple overlapping sources of truth for status and restore state, and (4) several semantic mismatches, such as Leader being stored as session type `manager` while some logic looks for `leader`. These issues make selectable-provider behavior look supported in the product surface while remaining only partially real in the runtime.

## 1. Main code map

### Provider abstraction
- `src/atc/agents/base.py`
  - Defines `AgentProvider`, `SessionInfo`, `PromptResult`, `OutputChunk`, `ProviderCapabilities`.
  - Canonical provider API expects provider-owned `spawn_session()`, `send_prompt()`, `get_status()`, `stream_output()`, `stop_session()`, `list_sessions()`.
- `src/atc/agents/factory.py`
  - Registry plus builtin providers.
  - Builtins: `claude_code`, `opencode`.
  - `get_launch_command()` is especially important because much of the app bypasses provider objects and only asks for a shell command.

### Concrete providers
- `src/atc/agents/claude_provider.py`
  - Provider implementation is tmux-native.
  - Tracks sessions only in an in-memory `_sessions` dict.
  - Uses tmux `new-window`, `capture-pane`, `kill-pane`, and `send_instruction_async()`.
- `src/atc/agents/opencode_provider.py`
  - Talks to an OpenCode HTTP API, but still spawns a tmux pane for visibility.
  - Tracks sessions only in an in-memory `_pane_ids` dict.
  - Has no DB integration and does not participate in ATC restore logic.

### Direct session runtime, still dominant
- `src/atc/session/ace.py`
  - Shared tmux helpers: `_ensure_tmux_session()`, `_spawn_pane()`, `_accept_trust_dialog()`.
  - Ace lifecycle: `create_ace()`, `start_ace()`, `stop_ace()`, `destroy_ace()`.
- `src/atc/leader/leader.py`
  - Leader lifecycle, but persisted session type is `manager`.
- `src/atc/tower/session.py`
  - Tower session lifecycle.
- `src/atc/session/reconnect.py`
  - Reconnect/respawn logic after restart.
- `src/atc/session/state_machine.py`
  - DB/event-bus session status machine.

### Terminal control and streaming
- `src/atc/terminal/control.py`
  - Persistent tmux control-mode connection for reliable send-keys.
- `src/atc/terminal/pty_stream.py`
  - `tmux pipe-pane` to FIFO, then event bus `pty_output`.
- `src/atc/terminal/monitor.py`
  - Parses PTY output into session status/token-usage/error events.

### Startup and restore orchestration
- `src/atc/api/app.py`
  - Lifespan startup wires TowerController, PTY streaming, websocket forwarding, reconnect, Tower restore, and Tower auto-start.

## 2. What the architecture says vs what the runtime does

### Intended model
The provider protocol suggests this shape:
1. choose provider from config/project,
2. provider spawns and owns sessions,
3. provider reports status/output,
4. runtime stays provider-agnostic.

### Actual model
The live runtime mostly does this instead:
1. read project `agent_provider`,
2. convert it to a launch command via `get_launch_command()`,
3. call shared tmux helpers in `session/ace.py`,
4. rely on tmux pane IDs in the DB,
5. stream/capture output via tmux/PTY,
6. special-case Claude startup dialogs and CLAUDE.md deployment.

That means provider selection is often only changing the command string, not the control plane.

## 3. Provider semantics by role

### Tower
- Tower is treated as its own session type, `tower`, in `src/atc/tower/session.py`.
- Startup path:
  - resolve or create anchor project,
  - deploy Tower identity files with `deploy_tower_files()`,
  - choose command with `get_launch_command(project.agent_provider)`,
  - `_spawn_pane()` in tmux,
  - `_accept_trust_dialog()`.
- Tower always uses tmux session state, DB session rows, PTY readers, and websocket terminal channels.
- Even for non-Claude providers, Tower still depends on a terminal pane and tmux restore behavior.

### Leader
- Leader is conceptually “Leader”, but persisted as `session_type="manager"` in `src/atc/leader/leader.py` and `src/atc/state/models.py`.
- Start path is very similar to Tower:
  - create DB session row,
  - deploy manager files and copy `CLAUDE.md` / `.claude` into repo path,
  - choose command from project provider,
  - spawn tmux pane,
  - accept Claude trust dialogs.
- Leader messaging uses `send_instruction()` to the tmux pane, not provider `send_prompt()`.

### Ace
- Ace creation in `src/atc/session/ace.py:create_ace()` is the most obviously split-brain path.
- It accepts a provider-specific `launch_command`, but workspace prep is hardcoded to `create_provider("claude_code")` before spawn.
- After that, it still spawns a tmux pane directly and uses the shared Claude trust-dialog logic.
- So an Ace in an `opencode` project still inherits Claude-specific prep assumptions in part of the path.

## 4. Runtime startup, restart, and restore flows

### Normal startup path
In `src/atc/api/app.py` lifespan:
1. create `TowerController`,
2. start `PtyStreamPool`,
3. wire websocket input/resize/subscribe to PTY operations,
4. run startup smoke test,
5. call `reconnect_all()` for active sessions,
6. start PTY readers for active sessions with live panes,
7. separately restore TowerController state from DB,
8. optionally auto-start Tower.

### Reconnect flow
`src/atc/session/reconnect.py`:
- reconnects active sessions from DB,
- skips `tower` and `manager` because Tower restore logic owns them,
- for Ace-like sessions, if pane is alive, status may be pushed back to `idle`,
- if pane is dead, respawns using `get_launch_command(provider)` and shared tmux helpers.

Important detail: reconnect logic is not provider-owned. It assumes every restorable session can be recreated from a tmux pane plus a launch command.

### Tower restore flow
`src/atc/api/app.py` has a second, separate restore path for Tower:
- scans projects and tower sessions,
- validates pane aliveness,
- restores TowerController private fields directly (`_current_project_id`, `_current_session_id`, `_leader_session_id`, `_current_goal`, `_state`),
- starts PTY reader for restored tower pane.

This is a manual controller-state reconstruction, not a provider-level restore.

## 5. Session metadata and sources of truth

### DB session model
`src/atc/state/models.py:Session` stores:
- `session_type`,
- `status`,
- `tmux_session`,
- `tmux_pane`,
- `alternate_on`,
- no provider-specific runtime metadata blob.

### Provider-side metadata
Provider protocol supports `SessionInfo.metadata`, but runtime persistence does not meaningfully use it.
- `ClaudeCodeProvider` keeps pane IDs in memory only.
- `OpenCodeProvider` keeps pane IDs in memory only.
- restore/reconnect rely on DB tmux fields, not provider metadata.

### Implication
The DB schema is tuned for tmux-backed sessions, not generic providers. A provider can expose metadata in theory, but ATC restore and UI lifecycles need tmux pane IDs and session rows.

## 6. tmux and terminal control model

### Control plane
- `_spawn_pane()` in `src/atc/session/ace.py` is the de facto session launcher.
- `_accept_trust_dialog()` is Claude-specific and embedded in the shared path.
- `src/atc/terminal/control.py` maintains persistent tmux control-mode connections for send-keys.
- `src/atc/terminal/pty_stream.py` uses `tmux pipe-pane` and FIFOs for output.
- websocket terminal UX subscribes to `terminal:{session_id}` channels in `src/atc/api/app.py`.

### Why that matters
This control plane assumes every provider can be meaningfully represented as:
- a tmux pane,
- interactive terminal input via send-keys,
- output parsable from PTY text.

That works naturally for Claude Code. It is a poorer fit for OpenCode, whose provider abstraction is HTTP-first and only uses tmux “for human observability”.

## 7. Concrete architectural inconsistencies

### A. Provider abstraction is mostly bypassed
- Intended provider methods like `spawn_session()` and `send_prompt()` are rarely used in live session flows.
- Main runtime calls `get_launch_command()` and shared tmux helpers instead.
- `create_provider()` usage is limited mostly to metadata/capabilities and workspace prep.

### B. Ace path hardcodes Claude provider prep
- `src/atc/session/ace.py:create_ace()` calls `create_provider("claude_code")` for workspace prep, regardless of project provider.
- That is a direct bug-shaped inconsistency for selectable providers.

### C. Global/default provider and project provider are separate surfaces
- Runtime settings expose `settings.agent_provider.default` in `src/atc/config.py` and `src/atc/api/routers/settings.py`.
- Projects separately store `projects.agent_provider` and update through `PATCH /projects/{id}/agent-provider`.
- The real lifecycle code usually reads project provider, but not consistently, and not through one central resolution layer.
- This likely makes “switch provider” feel incomplete because there is no single authoritative resolver for Tower/Leader/Ace creation, restore, and UI assumptions.

### D. Leader vs manager naming mismatch
- DB/session model uses `manager` as the session type.
- UI and controller semantics call it Leader.
- `TowerController._on_session_created()` and `_on_session_status_changed()` look for `session_type in ("leader", "ace")`, not `manager`.
- That means leader sessions are excluded from active-count accounting and terminal-status bookkeeping in those paths.

### E. Status ownership is split
There are multiple ways session status changes happen:
- direct DB writes in session lifecycle code,
- `transition()` plus event bus publishes in `session/state_machine.py`,
- PTY-derived status changes in `terminal/monitor.py`,
- manual websocket broadcasts in `api/app.py`.

Also, `StateLeader` exists in `src/atc/core/state_leader.py` as a queue-backed DB writer, but I could not find it started anywhere in the app. That suggests an unfinished or abandoned alternate status-persistence design.

### F. Restore logic is split across layers
- generic reconnect in `session/reconnect.py`,
- special Tower restore in `api/app.py`,
- provider classes have their own in-memory tracking but are not used to restore,
- frontend auto-start logic for Claude adds another behavioral layer.

### G. Frontend is provider-aware, but mainly in Claude-specific ways
- `frontend/src/components/tower/TowerConsole.tsx` and `LeaderConsole.tsx` auto-start when `project.agent_provider === "claude_code"`.
- For “other providers”, UI often falls back to forms/goals rather than an equivalently first-class terminal experience.
- This reinforces that Claude is the product’s native runtime, while other providers are partial adapters.

## 8. Likely root causes of provider-switch/selectable-provider instability

1. **Provider selection mostly swaps a launch command, not the lifecycle implementation.**
   - Core logic remains tmux + pane + trust-dialog + PTY based.

2. **The persistence model is tmux-centric, not provider-centric.**
   - Sessions persist pane ids and statuses, not provider runtime handles needed for API-native providers.

3. **Provider instances are ephemeral and in-memory.**
   - `ClaudeCodeProvider` and `OpenCodeProvider` track sessions only in local dicts, which are useless after restart.

4. **Restore/reconnect are written around tmux resurrection, not provider restoration.**
   - That particularly weakens non-Claude providers.

5. **Tower/Leader/Ace do not share one central provider-driven session launcher.**
   - They share tmux helpers instead.

6. **Naming drift (`leader` vs `manager`) leaks into control logic.**
   - This can create missing accounting, special-case bugs, and confusing mental models.

7. **UI semantics and backend semantics do not fully match.**
   - UI implies provider selection is meaningful now; backend still treats Claude as the default worldview.

## 9. Tech debt to call out explicitly

- Provider protocol exists but is not the true runtime boundary.
- `get_launch_command()` is effectively more important than `create_provider()` for real session control.
- Shared helpers in `session/ace.py` have become the hidden session runtime for all roles.
- Claude-specific deployment and trust-dialog logic is embedded in shared lifecycle code.
- Tower restore mutates controller internals directly in app startup.
- `StateLeader` looks orphaned.
- `manager`/`leader` terminology mismatch is active debt, not just naming style.
- OpenCode provider is architecturally half-integrated: HTTP-native in class design, tmux-native in surrounding runtime.

## 10. Bottom line

ATC currently has a **tmux/Claude runtime with provider-shaped seams**, not a truly provider-native runtime. Provider selection feels broken because the selectable surface area exceeds the amount of runtime that is actually abstracted. Until session creation, messaging, streaming, persistence, and restore all route through one provider-owned lifecycle, switching providers will continue to hit edge cases, partial behavior, and role-specific inconsistencies.