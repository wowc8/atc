# Provider Implementation Map

Use this map to locate the ATC subsystems a new CLI provider may need to integrate with. Provider onboarding should start from the docs in this directory, then use this map to find the implementation areas.

## Main docs

- [`provider_onboarding.md`](provider_onboarding.md) — main onboarding guide.
- [`provider_interface_contract.md`](provider_interface_contract.md) — shared/provider boundary contract.
- [`provider_acceptance_checklist.md`](provider_acceptance_checklist.md) — PR acceptance checklist.
- [`templates/provider_onboarding_template.md`](templates/provider_onboarding_template.md) — provider-specific design log template.

## Provider modules

Provider-specific logic belongs under provider-owned modules, for example:

```text
src/atc/agents/<provider>/
src/atc/agents/<provider>_usage.py
src/atc/agents/providers/<provider>/
```

Current examples:

- `src/atc/agents/codex_usage.py` — Codex-owned token JSONL parser/sync service.

Provider modules should adapt provider-specific facts into provider-neutral ATC primitives before calling shared code.

## Session lifecycle

Relevant areas:

```text
src/atc/session/
src/atc/state/
src/atc/core/
```

Use these areas for DB-backed session creation, lifecycle state, reconnect/recovery, and event handling. Preserve the DB-first invariant: session rows must exist before terminal panes or provider sessions are spawned.

## Terminal, PTY, tmux, and output

Relevant areas:

```text
src/atc/terminal/
frontend/src/components/terminal/
```

Shared terminal code manages PTY/tmux streaming, resizing, output delivery, and operator-console behavior. Provider prompt text, trust prompt text, token event parsing, and CLI quirks do not belong in shared terminal modules.

## Tower, Leader, Ace orchestration

Relevant areas:

```text
src/atc/tower/
src/atc/leader/
src/atc/session/
src/atc/core/
```

The normal execution chain is:

```text
Operator → Tower → Leader → Ace
```

Provider integrations must not add shortcuts around this chain for normal orchestration. Tests may use direct lower-level calls for debugging, but acceptance must prove Tower-driven Leader/Ace work.

## Token usage

Relevant shared area:

```text
src/atc/tracking/tokens.py
```

Provider-specific token collectors/parsers belong under provider modules, not here.

Shared token tracking handles:

- normalized `TokenUsageIncrement` validation;
- append-only usage event storage;
- summaries/aggregation;
- event/WebSocket fanout;
- token limit enforcement.

Provider modules handle:

- token telemetry source discovery;
- token event/file parsing;
- cumulative total to increment conversion;
- external provider session mapping;
- provider-specific de-dupe/source-key semantics.

## API surfaces

Relevant areas:

```text
src/atc/api/app.py
src/atc/api/routers/
```

Provider APIs should expose normalized status and actions. Provider-specific details may be surfaced deliberately through provider-specific endpoints/cards, but parsing and interpretation should remain provider-owned.

## Frontend operator visibility

Relevant areas:

```text
frontend/src/pages/
frontend/src/components/
frontend/src/context/
```

Provider changes need UI only when operator action or visibility changes. Examples:

- provider unavailable/misconfigured;
- trust/auth blocked;
- token sync status;
- manual sync/backfill trigger;
- recovery guidance.

When UI changes, add unit tests and Playwright smoke evidence.

## Config

Relevant files:

```text
config.yaml
src/atc/config.py
```

Provider-specific config should be explicit, local-overridable, and read by provider-owned code whenever possible. Shared modules should not learn provider-specific settings unless the setting is part of a provider-neutral contract.

## Tests

Common locations:

```text
tests/unit/test_<provider>.py
tests/unit/test_<provider>_usage.py
tests/unit/test_<provider>_runtime.py
frontend/src/**/__tests__/
scripts/playwright/
```

Required classes of tests are listed in [`provider_acceptance_checklist.md`](provider_acceptance_checklist.md).

## Do not put this here

- Do not put provider file/event parsing in `src/atc/tracking/tokens.py`.
- Do not put provider CLI output branching in shared Tower/Leader/Ace orchestration.
- Do not put provider auth/trust prompt text in shared terminal code.
- Do not put provider secrets in docs, tests, fixtures, screenshots, or logs.
- Do not add cost/dollar/billing concepts to token usage surfaces.
- Do not modify shared interfaces in a provider PR unless a separate design log justifies the generic need.
