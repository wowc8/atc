# ATC Start Here

If you are a human or AI agent making changes in this repository, start here.

This file is the entrypoint to ATC's durable developer/agent documentation.

## Read this before making changes

Minimum reading order for any non-trivial change:
1. `docs/START_HERE.md`
2. `docs/CODEBASE_MAP.md`
3. `docs/HARD_RULES.md`
4. `docs/DEVELOPMENT_RULES.md`
5. `docs/TESTING_RULES.md`
6. Any area-specific design/refactor doc relevant to the work

Examples:
- provider/runtime work:
  - `docs/runtime_orchestration_refactor_phases.md`
  - `docs/provider_runtime_refactor_plan.md`
  - `docs/provider_cli_wrapper_spec.md`
  - `docs/RUNTIME_PROVIDER_GUARDRAILS.md`
- API or architecture work:
  - `docs/ARCHITECTURE.md`
  - `docs/API.md`
- agent role/boundary work:
  - `docs/agents/README.md`
  - `docs/agents/TOWER.md`
  - `docs/agents/LEADER.md`
  - `docs/agents/ACE.md`
- historical design intent:
  - `docs/design_logs/`

## Hard documentation rule
If you make a code change, you must review the relevant `.md` docs and update them in the same workstream if they are now stale.

This is a hard rule, not a suggestion.

At minimum, after making a code change, the agent/developer must ask:
- did the codebase map change?
- did any hard rules or development rules change?
- did testing expectations change?
- did runtime/provider behavior change?
- did architecture/API/docs become stale because of this change?

If yes, update the docs before considering the work complete.

## Purpose of this doc system
ATC is large enough that future work should not require every agent to rediscover the architecture from scratch.

These docs exist to:
- reduce drift between code and mental model
- reduce repeated deep dives
- keep architecture rules durable
- make refactors safer
- make future agents faster and less likely to damage the codebase

## Document index
- `docs/CODEBASE_MAP.md`
  - high-level current-state code organization and ownership map
- `docs/HARD_RULES.md`
  - non-negotiable rules for future development
- `docs/DEVELOPMENT_RULES.md`
  - coding and refactor guidelines
- `docs/TESTING_RULES.md`
  - what test coverage is expected for changes
- `docs/provider_runtime_refactor_plan.md`
  - phased runtime/provider refactor plan
- `docs/provider_cli_wrapper_spec.md`
  - `atc-provider` wrapper contract
- `docs/RUNTIME_PROVIDER_GUARDRAILS.md`
  - runtime/provider boundary rules
- `docs/runtime_orchestration_refactor_phases.md`
  - phased implementation plan for the runtime/orchestration hardening refactor
- `docs/runtime_orchestration_phase0_baseline.md`
  - Phase 0 validation evidence, current delivery/session path map, test inventory, and Playwright baseline gate
- `docs/agents/README.md`
  - role contracts and behavior boundaries for Tower, Leader, and Ace
- `docs/ARCHITECTURE.md`
  - older and broader architecture overview
- `docs/API.md`
  - API surface reference, update when API behavior changes
- `docs/design_logs/`
  - important historical decisions and accepted design changes

## Completion rule
A change is not fully complete until both are true:
1. code/tests are in acceptable shape
2. relevant docs have been reviewed and updated if needed
