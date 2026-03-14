# Linux Setup

## Prerequisites

- Ubuntu 20.04+ / Debian 11+ / Fedora 36+
- Python 3.12+
- Node.js 20+
- tmux (`apt install tmux` or `dnf install tmux`)
- `gh` CLI

## Install

```bash
git clone <repo-url>
cd atc

# Backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Frontend
cd frontend && npm install && cd ..
```

## Run

```bash
./scripts/dev.sh
```

Open http://127.0.0.1:5173 in your browser.
