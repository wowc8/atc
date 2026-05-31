# Runtime and Provider Development Guardrails

These are hard rules for ATC runtime/provider development.

If future work violates these rules, the codebase will drift back into the mixed abstraction state this refactor is trying to fix.

## 1. One runtime boundary only
All provider-specific runtime behavior must enter the backend through the central runtime service.

Hard rule:
- Tower, Leader, and Ace lifecycle/instruction flows must call the runtime service, not provider-specific modules directly.

Do not:
- launch provider CLIs directly from routers/controllers/orchestrators
- call provider-specific readiness logic from shared workflow code
- bypass the runtime service for “just one special case”

## 2. Shared orchestration code must be provider-neutral
Shared orchestration code is allowed to know:
- role kind
- session id
- project id
- task id
- provider name as metadata

Shared orchestration code is not allowed to know:
- provider-specific sleeps
- provider-specific prompt formatting
- provider-specific CLI flags
- provider-specific trust/login wording
- provider-specific readiness heuristics

If a provider needs special handling, implement it in the provider module or wrapper.

## 3. tmux is shared infrastructure, not provider logic
tmux is a core ATC runtime dependency and may be treated as stable shared transport.

Hard rule:
- generic tmux operations belong in shared runtime infrastructure
- provider modules may use that infrastructure but must not duplicate it

Generic tmux concerns include:
- spawn pane/session/window
- capture output
- send input
- resize
- attach streams
- check pane/session health

## 4. Provider behavior belongs in provider-owned folders
All provider-specific behavior must live under provider-owned implementation paths.

Preferred structure:
```text
src/atc/providers/<provider>/
```

This includes:
- launch command construction
- workspace/bootstrap setup
- readiness detection
- prompt/instruction delivery behavior
- output interpretation
- provider-specific recovery behavior

## 5. Wrapper commands are procedural, not orchestration-authoritative
`atc-provider` is allowed to execute operations.
It is not allowed to decide workflow policy.

Python decides:
- which task to assign
- when to assign it
- why to retry or stop
- how state transitions happen

The wrapper decides:
- how to deliver the operation cleanly to that provider session

## 6. No new provider-specific hacks in shared code
Forbidden patterns outside provider-owned code include:
- `if provider == "codex": sleep(...)`
- `if provider == "claude_code": paste differently`
- `if provider == "opencode": use these flags here`
- raw provider CLI command strings in routers/controllers/orchestrators

If you think you need one of these, you are probably putting the logic in the wrong layer.

## 7. New provider support is not complete until the full contract exists
A provider is not first-class just because ATC can launch it.

A provider is only first-class when it supports the shared runtime contract, including at minimum:
- start-role
- send-instruction
- check-readiness
- inspect-session
- restore-session

Prefer also:
- stop-role
- assign-task

## 8. Role nouns must stay canonical
Canonical role names are:
- `tower`
- `leader`
- `ace`

Do not introduce or preserve parallel product/runtime names for the same role in new code.

Migration compatibility is allowed temporarily, but new code should not deepen `manager` drift.

## 9. Session-targeted operations should prefer session identity over role guesses
After startup, most runtime operations should target a concrete session id.

Hard rule:
- startup may be role-based
- post-start operations should usually be session-based

This reduces branching and makes runtime behavior easier to reason about.

## 10. Machine-readable markers are part of the contract
If a wrapper command changes emitted event names or payload shapes, that is a contract change.

Hard rule:
- wrapper markers must stay stable and documented
- event parsing must not depend on ad hoc human-readable terminal text when a documented marker exists

## 11. Docs are part of the implementation
Any runtime/provider boundary change must update the corresponding docs in the same workstream.

At minimum, review/update:
- `docs/provider_runtime_refactor_plan.md`
- `docs/provider_cli_wrapper_spec.md`
- this file
- architecture/API docs if public/shared behavior changed

## 12. No silent fallback to old abstractions
If a new flow is meant to use the runtime service or wrapper contract, do not silently fall back to older direct-launch paths.

Fail clearly instead of preserving hidden bypasses.

Silent mixed-mode behavior is how architecture drift returns.

## 13. Refactor toward deletion, not layering forever
Temporary compatibility shims are acceptable during migration.
Permanent duplicate abstraction layers are not.

When a new runtime/provider path is proven, plan the deletion of:
- old direct provider launch paths
- stale role terminology
- duplicate readiness logic
- duplicate task models where possible

## 14. Tests must defend the boundary
New runtime/provider work should add tests at the boundary being introduced.

At minimum, prefer tests for:
- runtime service to provider runtime integration
- wrapper event/exit code behavior
- provider readiness interpretation
- restore/reconnect behavior
- session lifecycle through the runtime service

## 15. If unsure, push logic downward
When deciding where code belongs:
- if it is workflow/policy, keep it in Python orchestration
- if it is provider-specific terminal behavior, push it into provider runtime/wrapper code
- if it is generic terminal transport, keep it in shared tmux infrastructure

That is the central organizing rule for keeping ATC clean going forward.
