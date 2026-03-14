# Windows (WSL2) Setup

WSL2 (Windows Subsystem for Linux) is the only supported Windows path. The desktop
Tauri app is not available on WSL2 — use the web UI in your Windows browser.

## What is WSL2

WSL2 lets you run a real Linux kernel inside Windows. ATC runs inside WSL2 and you
access the UI from any Windows browser.

## Install WSL2

Open PowerShell as Administrator:
```powershell
wsl --install
```
Restart your computer. Pick Ubuntu when prompted.

## Open a WSL2 Terminal

- Open Windows Terminal → click the dropdown → select Ubuntu
- Or find "Ubuntu" in the Start menu

## Install Prerequisites

Inside WSL2:
```bash
# Python 3.12+
sudo apt update && sudo apt install -y python3.12 python3.12-venv

# Node.js 20+
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# tmux (usually pre-installed)
sudo apt install -y tmux

# gh CLI
sudo apt install -y gh
```

## Clone and Install

Clone inside WSL2 home directory (NOT under /mnt/c/ for performance):
```bash
cd ~
git clone <repo-url>
cd atc

# Backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend
cd frontend && npm install && cd ..
```

## Run

```bash
./scripts/dev.sh
```

Open http://localhost:5173 in any Windows browser (Edge, Chrome, Firefox).
WSL2 automatically forwards ports.

## Known Limitations

- No Tauri desktop app — web UI only
- File watchers may be slower than native Linux
- `psutil` reports Linux process info (expected behavior)
