# Contributing

> How to make changes: branch naming, PR process, review.

## Development Setup

```bash
# Clone and install
git clone <repo-url>
cd atc

# Python backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Frontend
cd frontend
npm install
cd ..

# Run in dev mode
./scripts/dev.sh
```

## Branch Naming

```
feature/<short-description>    # New feature
fix/<short-description>        # Bug fix
refactor/<short-description>   # Code refactoring
docs/<short-description>       # Documentation only
```

## Making a Change

1. Create a branch from `main`
2. Write a failing test first
3. Implement the change
4. Run all checks:
   ```bash
   ruff check src/ tests/
   ruff format src/ tests/
   mypy src/
   pytest
   ```
5. If you made a design decision, run `./scripts/new_design_log.sh <slug>`
6. Update docs if behavior changed (`ARCHITECTURE.md`, `FEATURES.md`)
7. Commit and push
8. Open a PR with a clear description

## PR Process

- PRs require at least one review
- All CI checks must pass
- Squash merge to `main`
- Delete branch after merge

## Code Quality

- Pre-commit hooks enforce `ruff`, `mypy`, `prettier`, `eslint`
- CI runs full test suite
- No `any` types in TypeScript
- No bare `except:` in Python
