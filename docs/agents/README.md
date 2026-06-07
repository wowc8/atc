# ATC Agent Role Docs

This directory defines the intended behavior boundaries for ATC's runtime agent hierarchy:

```text
User / operator → Tower → Leader → Ace
```

These docs are product/behavior contracts, not implementation guides. They should be read alongside:

- `docs/ARCHITECTURE.md`
- `docs/CODEBASE_MAP.md`
- `docs/design_logs/002-naming-tower-leader-ace.md`

## Core chain-of-command rule

External operators and assistant integrations should communicate with **Tower**. Tower is responsible for coordinating Leaders. Leaders are responsible for coordinating Aces. Aces execute assigned work and report upward.

Do not flatten the hierarchy by bypassing Tower to directly instruct Leaders or Aces except for explicit debugging, recovery, or implementation-level tests.

## Role docs

- `TOWER.md` — top-level orchestration, project planning, resource governance, Leader supervision
- `LEADER.md` — project management, decomposition, task graph ownership, Ace supervision
- `ACE.md` — task execution, concrete implementation, evidence capture, upward reporting
