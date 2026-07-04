# Provider: <Name> Onboarding Plan

Copy this template into `docs/design_logs/NNN-provider-<name>-onboarding.md` before implementing a new CLI-backed provider/platform.

## Provider summary

- Provider name:
- CLI command:
- Provider module path:
- Token telemetry available: yes/no/unknown
- Auth/trust model:
- Supported roles: Tower / Leader / Ace / other

## Goals

State what ATC should be able to do with this provider.

## Non-goals

State what is intentionally out of scope for the first provider implementation.

## Provider boundary plan

Explain where provider-specific logic will live.

Provider-owned logic:

- CLI discovery and launch flags:
- Auth/trust prompt detection:
- Session metadata mapping:
- Token telemetry parsing:
- Health/error classification:

Shared interface changes:

- Expected shared interface changes: none / list proposed changes
- If any shared interface change is proposed, link the separate design log:

## CLI availability and config

- Command discovery:
- Version detection:
- PATH/command override:
- Provider enable/disable config:
- Local override expectations:

## Auth, trust, and blocked states

- First-run prompts:
- Login/auth requirements:
- Trust/workspace prompts:
- Blocked-state detection:
- Recovery guidance:
- Secret handling plan:

## Session model

- Provider external session ID source:
- ATC session mapping strategy:
- Working directory/project mapping:
- Start behavior:
- Stop behavior:
- Reconnect/recovery behavior:

## Tower / Leader / Ace orchestration

Describe how this provider supports the normal chain:

```text
Operator → Tower → Leader → Ace
```

- Tower support:
- Leader support:
- Ace support:
- Assignment/report flow:
- Artifact flow:
- Acceptance evidence path:

## Terminal and prompt behavior

- Launch mechanism:
- PTY/tmux integration:
- Readiness detection:
- Prompt submission:
- Resize/scrollback considerations:
- Provider-specific output parsing:

## Token telemetry

- Telemetry source:
- Parser module:
- Token classes available:
- Cumulative or incremental source:
- De-dupe/high-water strategy:
- External session mapping:
- Unknown/unmapped session handling:
- Manual sync/backfill support:

## Runtime truth and recovery

- Availability status:
- Readiness status:
- Blocked/failure status:
- Health check:
- Recovery path:
- API/UI visibility:

## Frontend / operator visibility

- Required status card/panel changes:
- Manual actions:
- Error/recovery display:
- Token usage display:
- Screenshots/Playwright evidence plan:

## Test plan

Backend:

- [ ] CLI discovery tests
- [ ] session lifecycle tests
- [ ] orchestration tests
- [ ] token parser tests
- [ ] token de-dupe tests
- [ ] runtime status tests

Frontend/UI:

- [ ] unit tests
- [ ] Playwright smoke

Scans:

- [ ] provider-boundary scan
- [ ] stale cost-term scan

## Acceptance criteria

- [ ] Provider-specific logic is contained in provider module.
- [ ] Shared interfaces are unchanged, or separate design log justifies generic shared change.
- [ ] Tower/Leader/Ace chain is proven.
- [ ] Terminal behavior remains operator-console compatible.
- [ ] Token telemetry emits normalized increments without double-counting.
- [ ] No cost/dollar semantics are introduced.
- [ ] Runtime truth is visible and recoverable.
- [ ] Tests/scans/PR evidence are complete.

## Open questions

- Question:
- Owner:
- Resolution needed before:
