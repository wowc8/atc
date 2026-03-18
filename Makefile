.PHONY: setup dev test lint format clean help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv, install all deps, set up pre-commit
	python3.12 -m venv $(VENV)
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
