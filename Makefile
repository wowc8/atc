.PHONY: setup dev test lint format clean help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv, install all deps, set up pre-commit
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"
	cd frontend && npm install
	$(VENV)/bin/pre-commit install

dev: ## Run backend + frontend dev servers
	./scripts/dev.sh

test: ## Run backend and frontend tests
	$(PYTHON) -m pytest tests/
	cd frontend && npm run test

lint: ## Run all linters (ruff, mypy, eslint)
	$(VENV)/bin/ruff check src/ tests/
	$(VENV)/bin/mypy src/
	cd frontend && npm run lint

format: ## Auto-format Python and frontend code
	$(VENV)/bin/ruff format src/ tests/
	cd frontend && npm run format

clean: ## Remove build artifacts and venv
	rm -rf $(VENV) frontend/node_modules dist/ build/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

cleardb: ## Full reset: kill ALL dev processes, nuke venv + DB, rebuild from scratch, restart
	@echo "→ Killing all ATC dev processes (uvicorn, vite, node)..."
	@pkill -f "uvicorn" 2>/dev/null || true
	@pkill -f "vite" 2>/dev/null || true
	@pkill -f "atc.api.app" 2>/dev/null || true
	@lsof -ti:8420 | xargs kill -9 2>/dev/null || true
	@lsof -ti:5173,5174,5175,5176,5177 | xargs kill -9 2>/dev/null || true
	@sleep 1
	@echo "→ Ensuring git remote uses HTTPS (not SSH)..."
	@git remote set-url origin https://github.com/wowc8/atc.git
	@echo "→ Pulling latest code..."
	@git fetch origin && git reset --hard origin/main || echo "⚠ git pull failed. Continuing with local code."
	@if [ -f atc.db ]; then \
		rm atc.db && echo "✓ atc.db deleted"; \
	else \
		echo "  (no atc.db found)"; \
	fi
	@echo "→ Cleaning up stale agent staging dirs and tmux sessions..."
	@rm -rf /tmp/atc-agents && echo "✓ /tmp/atc-agents cleared" || true
	@/usr/local/bin/tmux kill-session -t atc 2>/dev/null && echo "✓ tmux atc session killed" || true
	@echo "→ Nuking .venv to ensure a clean Python environment..."
	@rm -rf $(VENV)
	@echo "→ Rebuilding venv and installing all deps..."
	@python3 -m venv $(VENV)
	@$(VENV)/bin/pip install -e ".[dev]" --quiet
	@echo "✓ venv rebuilt and package installed fresh"
	@echo "→ Starting dev servers..."
	@$(MAKE) dev

clearcache: ## Remove Python __pycache__, .pytest_cache, and frontend .vite cache
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache frontend/.vite frontend/node_modules/.vite
	@echo "✓ Caches cleared"
