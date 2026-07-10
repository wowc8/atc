# CLI Provider Onboarding Guide

This guide is the canonical checklist for adding a new CLI-backed platform or provider to ATC. Use it before implementing support for a provider such as Claude Code, Codex, Gemini CLI, OpenCode, or any other terminal-driven agent runtime.

A provider is not considered supported until it satisfies the provider boundary lock, Tower/Leader/Ace orchestration contract, terminal contract, token usage contract, runtime-truth contract, and acceptance checklist in this guide.

## Start here

1. Read [`provider_interface_contract.md`](provider_interface_contract.md).
2. Read [`provider_implementation_map.md`](provider_implementation_map.md).
3. Copy [`templates/provider_onboarding_template.md`](templates/provider_onboarding_template.md) into a provider-specific design log under `docs/design_logs/`.
4. Use [`provider_acceptance_checklist.md`](provider_acceptance_checklist.md) as the PR checklist.

## Provider boundary lock

New CLI providers must adapt to ATC's existing provider-neutral contracts. A provider onboarding PR must not modify shared orchestration, session, terminal, token, or API interfaces just to fit one provider.

Hard rules:

- Provider-specific logic stays under the provider module.
- Shared ATC layers receive only normalized ATC facts, events, statuses, and token increments.
- Shared interface changes require a separate architecture decision/design log and must prove generic benefit beyond one provider.
- Provider onboarding PRs must include boundary regression tests or scans that prove provider-specific terms did not leak into shared modules.

Provider-owned behavior includes:

- CLI command discovery and provider-specific launch flags.
- Auth, trust, first-run, and blocked-prompt detection.
- Provider-specific terminal output parsing.
- Provider-specific session metadata and external session ID mapping.
- Token telemetry file/event discovery and parsing.
- Cumulative token total to increment conversion.
- Provider-specific health interpretation and error classification.

Shared layers must not know about provider-specific filesystem paths, JSON payload shapes, event names, prompt wording, or CLI quirks.

## Required provider module

Add provider logic under a provider-owned location, for example:

```text
src/atc/providers/<provider>/
```

Runtime adapters implement `ProviderRuntime` and are registered through `src/atc/providers/registry.py`. Provider-specific classifiers, prompt recovery, token usage parsers, session metadata mapping, and helper-subagent integrations should live under the same provider package.

The provider module owns adaptation from provider-specific behavior into ATC-normalized primitives.

Minimum responsibilities:

- Resolve whether the provider CLI is installed and executable.
- Build the command/env needed to launch the CLI.
- Start or attach to provider-backed ATC sessions through existing session/terminal infrastructure.
- Detect readiness, trust/auth prompts, blocked states, and completion states.
- Map external provider session identifiers to ATC session IDs.
- Emit normalized token usage increments when the provider has token telemetry.
- Expose provider-specific status through provider-owned code, then normalize it for API/UI use.

## Session lifecycle contract

Every provider must respect ATC's session lifecycle invariants:

- DB session rows are created before terminal/tmux panes are spawned.
- Session IDs are stable and used for all downstream events.
- Provider external session IDs are mapped to ATC session IDs before usage/events are recorded.
- Start, stop, reconnect, health, and recovery paths are deterministic and testable.
- Unknown or unmapped provider sessions are skipped or surfaced as blocked; they must not create orphan ATC usage rows or task events.

## Tower / Leader / Ace orchestration contract

Normal provider validation must prove the full chain:

```text
Operator → Tower → Leader → Ace
```

Required behavior:

- Tower receives operator instructions and governs project/goal execution.
- Tower starts, monitors, and recovers Leaders.
- Leaders plan work, manage context, spawn Aces, assign tasks, and keep Aces on task.
- Aces execute assigned work in isolated sessions and report active/accepted/completed states.
- Artifact and report paths flow Ace → Leader → Tower/operator-visible surfaces.
- Direct Ace execution may be used for low-level debugging, but it does not satisfy orchestration acceptance.

Provider support must not bypass Tower/Leader/Ace boundaries by adding provider-specific shortcuts in shared orchestration code.

## Terminal / PTY / tmux contract

CLI providers are terminal runtimes and must preserve operator-console behavior:

- launch through the existing terminal/session infrastructure unless a separate architecture decision approves otherwise;
- stream output to the UI without unmounting terminals;
- preserve resize behavior across panel/column splits;
- preserve manual scrollback while output streams;
- resume live-follow only after the operator scrolls back to the bottom;
- submit prompts atomically, with instruction text and Enter sent without an await gap;
- avoid provider-specific prompt parsing in shared terminal code.

## Prompt submission contract

Provider prompt submission must be deterministic:

- Build prompts in provider-owned or orchestration-owned code, not ad hoc UI handlers.
- Submit text and Enter atomically.
- Record dispatch/delivery events where the orchestration contract requires runtime truth.
- Detect blocked trust/auth prompts before sending task prompts when possible.

## Trust, auth, and first-run prompts

Providers often block on first-run trust, login, permissions, model selection, or workspace prompts. Provider modules must detect and surface these states without leaking secrets.

Requirements:

- Never auto-fill secrets, API keys, credentials, or passwords.
- Never persist secrets in docs, fixtures, logs, screenshots, or tests.
- Surface blocked states with operator-readable reason codes.
- Provide a recovery path or manual instruction for the operator.
- Tests should use sanitized fixtures with `[REDACTED]` for any secret-like examples.

## Runtime truth and health contract

Provider state must be reconcilable across API, DB, terminal/tmux, frontend, and provider output.

Required surfaces:

- provider available/unavailable state;
- session running/blocked/ready/completed state;
- trust/auth blocked state;
- last health check or sync state, when applicable;
- recovery guidance;
- structured failure logs and reason codes.

If the provider is blocked, ATC should report `blocked` or equivalent guidance rather than presenting the session as healthy/running.

## Token usage contract

Token accounting is token-only. Do not add or reintroduce cost, dollar, pricing, or billing semantics.

Forbidden concepts include legacy dollar-denominated accounting fields, billing-limit
settings, pricing registries, per-token price tables, cost-tracker classes, and
cost-specific usage/Tower API endpoints.

Provider-specific token discovery/parsing belongs inside the provider module. Shared token tracking receives normalized increments only.

Provider modules must emit:

```python
TokenUsageIncrement(
    session_id=...,
    project_id=...,
    provider=...,
    model=...,
    input_tokens=...,
    cached_input_tokens=...,
    output_tokens=...,
    reasoning_output_tokens=...,
    total_tokens=...,
    source=...,
    external_session_id=...,
    source_event_id=...,
)
```

Requirements:

- Preserve token classes when the provider exposes them.
- Convert cumulative provider totals to increments behind the provider boundary.
- Use durable high-water/de-dupe state where provider sources can be re-read.
- Restart, re-read, and backfill must not double-count.
- Shared token tracking must not parse provider files, event names, paths, or JSON payloads.

## Context, artifact, and report contract

Provider sessions must participate in ATC's context and artifact flows:

- Leaders own project context packages.
- Aces receive task-scoped context through Leader-managed assignment paths.
- Aces report progress and artifacts through ATC event/report paths.
- Artifact locations must be explicit and operator-visible.
- Provider modules should not create alternate hidden context/report channels.

## Frontend / operator visibility contract

Provider support must include operator visibility for any provider-specific state that affects orchestration:

- CLI unavailable/misconfigured;
- trust/auth blocked;
- session starting/running/ready/failed;
- token usage sync enabled/running/stale/failed;
- last sync/check timestamps;
- recoverable next action.

When UI changes are made, include frontend unit tests and Playwright evidence.

## Config contract

Provider config should be explicit and local-overridable:

- global defaults in `config.yaml`;
- local operator overrides in `config.local.yaml`;
- provider enable/disable switch;
- CLI command/path override if needed;
- polling/sync intervals if needed;
- provider-owned glob/path settings for telemetry sources.

Do not require shared modules to know provider-specific config keys unless those keys are part of a provider-neutral interface.

## Required tests

At minimum, provider onboarding requires tests for:

- CLI command discovery and missing-command errors;
- session lifecycle start/stop/recovery behavior;
- Tower → Leader → Ace orchestration path;
- trust/auth blocked detection and recovery guidance;
- prompt submission atomicity when provider-specific logic is involved;
- terminal output streaming/resize behavior when UI or terminal code changes;
- token parser fixtures, if provider exposes token telemetry;
- no double-counting after restart/re-read/backfill;
- unknown/unmapped external sessions are skipped safely;
- API/UI status surfaces;
- provider-boundary scans preventing provider-specific terms in shared modules;
- stale cost/dollar term scans.

## Required documentation updates

Every new provider PR must update:

- provider-specific design log created from [`templates/provider_onboarding_template.md`](templates/provider_onboarding_template.md);
- [`provider_implementation_map.md`](provider_implementation_map.md) if new files or patterns are introduced;
- [`FEATURES.md`](FEATURES.md) if operator-visible behavior changes;
- [`ARCHITECTURE.md`](ARCHITECTURE.md) if subsystem responsibilities change;
- README links only if a new top-level provider entrypoint is added.

## Acceptance

A provider is accepted only when:

- provider-specific logic is contained in the provider module;
- shared interfaces remain unchanged, or a separate design log justifies generic shared changes;
- Tower/Leader/Ace orchestration is proven through the normal chain;
- terminal behavior remains operator-console compatible;
- token telemetry records normalized token increments without cost semantics;
- runtime truth is visible and recoverable;
- tests, scans, and UI evidence are attached to the PR.
