# 014 — Provider Runtime Consolidation Audit

## Status

Accepted as the Phase 1 audit for the provider runtime + helper subagent track.

## Context

ATC already has a Settings/provider selector that is expected to make Codex or Claude Code a first-class runtime for Tower, Leader, and Ace sessions. The next track adds provider-native helper subagents, so the provider boundary must be clean before any helper implementation lands.

The current repository has two provider abstractions:

1. The current role-runtime abstraction under `src/atc/providers/` + `src/atc/runtime/`.
2. An older AgentProvider abstraction under `src/atc/agents/`.

The product direction is that provider-specific behavior belongs under provider modules and shared ATC code should depend on provider-neutral runtime/session/token contracts.

## Audit scope

Audited paths:

```text
src/atc/agents/*provider*.py
src/atc/agents/base.py
src/atc/agents/factory.py
src/atc/agents/claude_runtime.py
src/atc/providers/
src/atc/runtime/
src/atc/session/
src/atc/tower/
src/atc/leader/
src/atc/api/routers/settings.py
src/atc/api/routers/projects.py
frontend provider settings surfaces
```

Searches focused on:

```text
AgentProvider
ProviderSpawnRequest
create_provider
list_providers
get_launch_command
CodexProvider
ClaudeCodeProvider
ProviderRuntime
RuntimeService
create_provider_runtime
list_provider_runtimes
project.agent_provider
settings.agent_provider.default
```

## Canonical provider runtime path

The canonical path for Tower/Leader/Ace runtime work is:

```text
settings/project provider selection
  -> session row stamped with provider
  -> RuntimeService
  -> atc.providers.registry
  -> ProviderRuntime implementation
  -> provider module under src/atc/providers/<provider>/
```

Current files:

```text
src/atc/runtime/service.py
src/atc/runtime/models.py
src/atc/providers/base.py
src/atc/providers/registry.py
src/atc/providers/codex/runtime.py
src/atc/providers/codex/classifier.py
src/atc/providers/claude_code/runtime.py
src/atc/providers/claude_code/__init__.py
src/atc/providers/codex/__init__.py
```

Runtime registry state:

```python
register_provider_runtime("claude_code", ClaudeCodeRuntime)
register_provider_runtime("codex", CodexRuntime)
```

Settings/project validation already uses the runtime registry in these routes:

```text
src/atc/api/routers/settings.py
src/atc/api/routers/projects.py
```

## Tower/Leader/Ace runtime flow

### Tower

`src/atc/tower/session.py` resolves the current provider from settings, stamps the Tower session row with that provider, deploys role files, then calls `_spawn_provider_session(...)`.

`_spawn_provider_session(...)` is defined in `src/atc/session/ace.py` and delegates to `RuntimeService.spawn_existing_session(...)` with role `RoleKind.TOWER` for `session_type="tower"`.

### Leader

`src/atc/leader/leader.py` resolves the current provider from settings, stamps the Leader session row with that provider, deploys role files, prepares workspace through `RuntimeService.prepare_workspace(...)`, then calls `_spawn_provider_session(...)`.

`_spawn_provider_session(...)` maps `session_type="manager"` to `RoleKind.LEADER` and delegates to `RuntimeService.spawn_existing_session(...)`.

### Ace

`src/atc/session/ace.py` resolves provider from project `agent_provider`, then live settings, then config. The Ace session row is stamped with that provider before the pane is spawned. It then calls `RuntimeService.prepare_workspace(...)` and `_spawn_provider_session(...)`, which maps `session_type="ace"` to `RoleKind.ACE`.

### Send/stop/inspect/recover

The send/stop/inspect paths are mostly through `RuntimeService`:

```text
src/atc/session/ace.py::_send_session_instruction
src/atc/session/ace.py::destroy_ace
src/atc/session/ace.py health/readiness helpers
src/atc/leader/leader.py::send_leader_message
src/atc/leader/leader.py::stop_leader
src/atc/session/reconcile.py
src/atc/session/reconnect.py
src/atc/runtime/health.py
```

This confirms the selector is already intended to mean first-class provider-backed role runtime.

## Legacy/parallel provider path

The older abstraction lives under:

```text
src/atc/agents/base.py
src/atc/agents/factory.py
src/atc/agents/codex_provider.py
src/atc/agents/claude_provider.py
src/atc/agents/opencode_provider.py
src/atc/agents/plugins/_example_provider.py
```

It defines:

```text
AgentProvider
ProviderSpawnRequest
SessionInfo
PromptResult
OutputChunk
SessionStatus
create_provider
list_providers
get_launch_command
```

### Active references found

The old `create_provider(...)` and `list_providers(...)` factory functions are not used by active Tower/Leader/Ace runtime code. They appear only in the old factory and package exports.

The old provider classes are imported by `src/atc/agents/factory.py` for registry construction:

```text
CodexProvider
ClaudeCodeProvider
OpenCodeProvider
```

The only active non-test import of `atc.agents.factory` found in product code is:

```text
src/atc/leader/orchestrator.py -> get_launch_command
```

`get_launch_command(...)` is used when Leader creates Aces:

```text
create_ace(..., launch_command=get_launch_command(provider_name), ...)
```

However, `launch_command` is passed as metadata through `_spawn_provider_session(...)` and `RuntimeService.spawn_existing_session(...)`. The current `CodexRuntime.start_role(...)` and `ClaudeCodeRuntime.start_role(...)` resolve command from their provider runtime settings rather than from this old factory command. That makes `get_launch_command(...)` a transitional compatibility seam and a candidate for removal/replacement in Phase 2.

### Provider-specific runtime code outside `src/atc/providers/`

Provider-specific behavior still exists outside `src/atc/providers/`:

```text
src/atc/agents/codex_provider.py
src/atc/agents/claude_provider.py
src/atc/agents/claude_runtime.py
src/atc/agents/auth.py
src/atc/agents/codex_usage.py
src/atc/leader/orchestrator.py -> get_launch_command(provider_name)
src/atc/session/ace.py -> imports atc.agents.claude_runtime compatibility helpers
```

Some of this is acceptable temporarily because `agents/deploy.py` is still the role-file deployment SOT and `codex_usage.py` was intentionally placed behind a Codex-owned boundary during the token-tracking phases. Phase 2 should either move provider runtime pieces to `src/atc/providers/<provider>/` or explicitly document why a non-runtime provider boundary remains under `src/atc/agents/`.

## Findings

### Finding 1 — The current provider selector already drives role runtime

Codex/Claude selection is not just a UI preference. Session creation stamps provider on Tower/Leader/Ace sessions, and `_spawn_provider_session(...)` routes to `RuntimeService`, which resolves `ProviderRuntime` implementations from `src/atc/providers/registry.py`.

### Finding 2 — Old AgentProvider path is parallel drift

The old `AgentProvider` abstraction remains in source, exports, plugins, and old provider classes. It duplicates responsibilities now owned by `ProviderRuntime`.

This is exactly the cleanup class called out in `CODE_STRUCTURE.md`: a new provider boundary plus an almost-equivalent old path.

### Finding 3 — `get_launch_command(...)` is the only active product-code dependency on the old provider factory

`src/atc/leader/orchestrator.py` imports only `get_launch_command(...)` from `atc.agents.factory`. This should be removed or replaced by a provider-runtime-owned command/config resolution path in Phase 2.

Likely replacement options:

1. Stop passing `launch_command` into `create_ace(...)`; allow the selected `ProviderRuntime` to resolve launch command from live settings.
2. If a command preview is still needed, expose it from `atc.providers.registry.runtime_kwargs_for_provider(...)` or a new provider-neutral capability model.

Option 1 is preferred unless tests prove a caller depends on per-Ace launch command overrides.

### Finding 4 — Claude-specific helpers remain outside provider module

`src/atc/agents/claude_runtime.py` contains Claude prompt/dialog/readiness helpers. Some legacy session code imports compatibility helpers from `session.ace`, and `src/atc/agents/claude_provider.py` uses `claude_runtime.py`.

The newer Claude runtime under `src/atc/providers/claude_code/runtime.py` should own or import its provider-specific helper code from `src/atc/providers/claude_code/`, not from a generic `agents` package.

### Finding 5 — Codex token usage boundary is provider-owned in spirit but not path-aligned

`src/atc/agents/codex_usage.py` intentionally owns Codex JSONL discovery/parsing/mapping/delta conversion and emits normalized `TokenUsageIncrement` values. This satisfies the boundary rule semantically, but the path is not aligned with the newer provider module structure.

Phase 2 should consider moving it to:

```text
src/atc/providers/codex/usage.py
```

with import updates and boundary tests.

### Finding 6 — OpenCode remains only in the legacy AgentProvider registry

`src/atc/agents/opencode_provider.py` exists, but no `src/atc/providers/opencode/` runtime is registered in the new provider runtime registry. Unless OpenCode support is active and intentionally legacy-only, Phase 2 should either migrate it to `ProviderRuntime` or remove/park it from active provider onboarding docs.

### Finding 7 — Some comments/docs still say Claude where they mean provider

Several docstrings/comments still describe Tower/Leader/Ace sessions as Claude Code sessions even though the code stamps a selected provider and uses `RuntimeService`. These should be renamed during cleanup to avoid future agents assuming Claude-only behavior.

Examples:

```text
src/atc/tower/session.py docstring/comments
src/atc/leader/leader.py docstring/comments
src/atc/session/ace.py comments around deployed CLAUDE.md compatibility files
```

The `CLAUDE.md` compatibility file itself may remain because providers may read compatibility instruction filenames; comments should distinguish provider-neutral role instructions from Claude compatibility filenames.

## Phase 2 migration/deletion plan

1. Remove `get_launch_command(...)` from active Leader/Ace creation flow.
   - Preferred: do not pass `launch_command` from `leader/orchestrator.py` to `create_ace(...)`.
   - Let `ProviderRuntime` resolve commands from `runtime_kwargs_for_provider(...)` and settings.

2. Move Codex token usage module into the Codex provider package.
   - From: `src/atc/agents/codex_usage.py`
   - To: `src/atc/providers/codex/usage.py`
   - Update app lifecycle/API/tests imports.
   - Add boundary scan proving shared token tracking does not import Codex paths/events.

3. Move Claude-specific runtime helpers into the Claude provider package.
   - From: `src/atc/agents/claude_runtime.py`
   - To: `src/atc/providers/claude_code/runtime_helpers.py` or equivalent.
   - Update `ClaudeCodeRuntime` imports.
   - Remove old AgentProvider-only usage.

4. Delete or quarantine old AgentProvider classes and factory.
   - Candidate deletions after import removal:
     ```text
     src/atc/agents/base.py
     src/atc/agents/factory.py
     src/atc/agents/codex_provider.py
     src/atc/agents/claude_provider.py
     src/atc/agents/opencode_provider.py
     src/atc/agents/plugins/_example_provider.py
     ```
   - Keep `src/atc/agents/deploy.py` for role-file deployment unless/until a separate deploy-boundary refactor moves it.
   - Keep `src/atc/agents/auth.py` only if Claude/Codex provider modules still depend on it; otherwise move provider-specific auth helpers under provider packages.

5. Add regression tests/scans.
   - No active product code imports `atc.agents.factory`.
   - No active product code imports `atc.agents.codex_provider` or `atc.agents.claude_provider`.
   - Settings/project provider APIs use `list_provider_runtimes()`.
   - Tower/Leader/Ace session spawn tests prove provider selection reaches `ProviderRuntime` for Codex and Claude.
   - Provider-specific token/readiness prompt terms do not appear in shared runtime/session/token modules, except documented compatibility filenames.

6. Update docs and comments.
   - Replace Claude-only comments where behavior is provider-neutral.
   - Update `CODE_STRUCTURE.md`, `docs/ARCHITECTURE.md`, and provider onboarding docs to name `src/atc/providers/<provider>/` as the runtime provider module path.

## Required Phase 2 validation

Backend targeted tests:

```bash
PYTHONPATH=src pytest \
  tests/unit/test_agent_provider.py \
  tests/unit/test_runtime_service.py \
  tests/unit/test_codex_runtime.py \
  tests/unit/test_leader.py \
  tests/unit/test_section15.py \
  tests/e2e/test_phase8_scenarios.py \
  -q
```

The exact set may change after the audit-derived edits, but it must cover provider selection, runtime registry resolution, Tower/Leader/Ace startup, reconnect/restore, and provider-specific Codex/Claude readiness classification.

Required scans:

```text
No imports of atc.agents.factory from active product code.
No imports of atc.agents.codex_provider or atc.agents.claude_provider.
No Codex/Claude provider-specific prompt/event/file parsing in shared runtime/session/token modules.
No cost/dollar usage semantics reintroduced.
```

## Decision

Proceed to Phase 2 with provider runtime consolidation cleanup before implementing provider-native helper subagents.

Phase 2 should remove the old AgentProvider runtime path or reduce it to zero active product-code usage, move provider-specific runtime/token helpers under `src/atc/providers/<provider>/`, and add boundary tests/scans so future provider work uses the canonical `ProviderRuntime` path.
