# ATC Provider Runtime Refactor: Phase 0 Checklist

## Goal
Freeze the core contract and prepare the first implementation-safe foundation for the runtime/provider refactor.

Phase 0 is about defining the boundary cleanly before broad code motion starts.

## Required inputs
Read first:
- `docs/START_HERE.md`
- `docs/CODEBASE_MAP.md`
- `docs/HARD_RULES.md`
- `docs/DEVELOPMENT_RULES.md`
- `docs/TESTING_RULES.md`
- `docs/provider_runtime_refactor_plan.md`
- `docs/provider_cli_wrapper_spec.md`
- `docs/provider_runtime_contract_spec.md`
- `docs/RUNTIME_PROVIDER_GUARDRAILS.md`

## Deliverables
Phase 0 should produce:
- canonical runtime/provider models
- canonical provider runtime interface
- wrapper event and exit code definitions
- compatibility note for `manager -> leader`
- initial provider registry direction
- tests for the new shared contract objects where practical

## Checklist

### A. Canonical shared models
- [ ] Create `src/atc/runtime/models.py`
- [ ] Add `RoleKind`
- [ ] Add `RuntimeTransport`
- [ ] Add `ReadinessState`
- [ ] Add `RuntimeBlockReason`
- [ ] Add `RuntimeSessionHandle`
- [ ] Add `StartRoleRequest`
- [ ] Add `StopRoleRequest`
- [ ] Add `InstructionRequest`
- [ ] Add `TaskAssignmentRequest`
- [ ] Add `ReadinessResult`
- [ ] Add `RuntimeInspection`

### B. Provider interface
- [ ] Create `src/atc/providers/base.py`
- [ ] Define `ProviderRuntime` protocol
- [ ] Ensure interface matches `docs/provider_runtime_contract_spec.md`

### C. Wrapper event contract
- [ ] Create parser/schema home, for example `src/atc/runtime/wrapper_events.py`
- [ ] Define supported event names
- [ ] Define shared payload expectations
- [ ] Define parsing/validation helpers
- [ ] Define wrapper exit code enum

### D. Registry direction
- [ ] Create or reshape provider registry module, likely `src/atc/providers/registry.py`
- [ ] Document how provider name resolves to runtime implementation
- [ ] Record any temporary compatibility with existing `src/atc/agents/` modules

### E. Compatibility and migration notes
- [ ] Write explicit `manager -> leader` compatibility note in code comments/docs where needed
- [ ] Record whether old session types remain temporarily supported in persistence
- [ ] Record temporary rules for old direct launch paths while migration is incomplete

### F. Testing baseline
- [ ] Add unit tests for shared models if validation/serialization helpers exist
- [ ] Add unit tests for wrapper event parsing
- [ ] Add unit tests for wrapper exit code mapping
- [ ] Add protocol/registry tests if useful

### G. Documentation sync
- [ ] Re-read all relevant `.md` docs after Phase 0 code changes
- [ ] Update any docs that became stale from the implementation
- [ ] Do not close Phase 0 with stale docs

## Out of scope for Phase 0
Do not yet:
- migrate all Tower/Leader/Ace flows
- implement provider-specific startup behavior in full
- move all tmux helpers
- rewrite reconnect/restore
- clean up every old abstraction immediately

Those belong in later phases.

## Phase 0 completion criteria
Phase 0 is complete only when:
- the shared contract is represented in code
- the docs still match the contract
- the first implementation phase can begin without ambiguity about interfaces, enums, event shapes, or exit codes
