# ATC

A hierarchical AI orchestration platform that manages multiple Claude Code sessions through a Tower → Leader → Ace chain of command. The Tower governs resources and goals, Leaders own context and planning within a project, and Aces execute tasks in isolated sessions — all visible from a single desktop application.

## Supported Platforms

- **macOS 12+** — full support including Tauri desktop app
- **Linux** (Ubuntu 20.04+, Debian 11+, Fedora 36+) — full support
- **Windows via WSL2** — web UI only; no desktop app

## Quick Start

### macOS / Linux

```bash
# Prerequisites: Python 3.12+, Node.js 20+, tmux, gh CLI

git clone <repo-url>
cd atc

# Backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Frontend
cd frontend && npm install && cd ..

# Run
./scripts/dev.sh
```

Open http://127.0.0.1:5173

### Windows (WSL2)

See [docs/setup/windows-wsl2.md](docs/setup/windows-wsl2.md) for detailed instructions.

```bash
# Inside WSL2 (Ubuntu)
cd ~ && git clone <repo-url> && cd atc
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm install && cd ..
./scripts/dev.sh
```

Open http://localhost:5173 in any Windows browser.

## Prerequisites

| Dependency | macOS | Linux | WSL2 |
|---|---|---|---|
| Python 3.12+ | `brew install python@3.12` | `apt install python3.12` | `apt install python3.12` |
| Node.js 20+ | `brew install node` | `apt install nodejs` | `apt install nodejs` |
| tmux | `brew install tmux` | `apt install tmux` | pre-installed |
| gh CLI | `brew install gh` | `apt install gh` | `apt install gh` |

## Development Setup

```bash
# Start backend + frontend in dev mode
./scripts/dev.sh

# Or run them separately:
# Backend (port 8420)
python3 -m uvicorn atc.api.app:create_app --factory --reload --port 8420

# Frontend (port 5173, proxies API to 8420)
cd frontend && npm run dev
```

## Configuration

Runtime config lives in `config.yaml`. Override locally with `config.local.yaml` (gitignored).

See `config.yaml` for all available options.

## Testing

```bash
pytest                      # all tests
pytest tests/unit           # fast unit tests
pytest tests/integration    # integration tests
pytest -x -q               # stop on first failure
```

## Project Structure

```
src/atc/        → Python backend (FastAPI + SQLite + tmux)
frontend/       → React 19 + TypeScript UI
src-tauri/      → Tauri 2 desktop shell
docs/           → Architecture, patterns, API reference
scripts/        → Dev + build utilities
tests/          → Unit, integration, e2e tests
```

## Documentation

- [CLAUDE.md](CLAUDE.md) — AI agent guide (read first)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — System architecture
- [docs/FEATURES.md](docs/FEATURES.md) — Feature guide
- [docs/API.md](docs/API.md) — REST API + WebSocket reference
- [docs/PATTERNS.md](docs/PATTERNS.md) — Code patterns
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — How to contribute

## Upgrading

Migrations run automatically on startup. After pulling new code, restart the server
and any new database migrations will be applied.
