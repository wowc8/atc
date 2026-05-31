# ATC Testing Rules

These rules define the minimum testing expectations for changes.

## 1. Boundary changes require boundary tests
If you change an architectural boundary, add tests at that boundary.

Examples:
- runtime service to provider runtime integration
- wrapper marker parsing and exit code handling
- restore/reconnect behavior
- session lifecycle transitions

## 2. New provider support is not complete without runtime contract coverage
A provider is not first-class just because it launches.

It should have coverage for at least:
- startup path
- readiness path
- instruction delivery path
- inspection path
- restore path where applicable

## 3. Do not rely only on unit tests for orchestration changes
Changes to Tower/Leader/Ace flows should usually include integration coverage as well.

## 4. If docs specify a contract, tests should defend it where practical
This especially applies to:
- wrapper markers
- exit codes
- role/runtime contract semantics
- API behavior when changed

## 5. If a high-risk module changes, increase testing scrutiny
High-risk modules include:
- `src/atc/state/db.py`
- `src/atc/tower/controller.py`
- `src/atc/api/routers/projects.py`
- `frontend/src/context/AppContext.tsx`

## 6. Completion rule
A change is not done when it merely compiles. It is done when the relevant level of tests and docs are both in acceptable shape.
