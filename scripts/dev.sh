#!/usr/bin/env bash
# Start backend + frontend in dev mode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Starting ATC in development mode..."

# Check for port conflicts before starting
if lsof -i :8420 -sTCP:LISTEN -t >/dev/null 2>&1; then
  STALE_PID=$(lsof -i :8420 -sTCP:LISTEN -t 2>/dev/null | head -1)
  echo "⚠ Port 8420 is already in use by PID $STALE_PID."
  echo "  Killing stale process..."
  kill "$STALE_PID" 2>/dev/null
  sleep 1
  if lsof -i :8420 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "✗ Could not free port 8420. Please kill PID $STALE_PID manually."
    exit 1
  fi
  echo "  Port 8420 freed."
fi

# Start backend
echo "→ Starting backend (uvicorn with reload)..."
# PYTHONPATH=src ensures uvicorn always loads from source, not stale site-packages
(cd "$ROOT_DIR" && PYTHONPATH="$ROOT_DIR/src" python3 -m uvicorn atc.api.app:create_app --factory --reload --host 127.0.0.1 --port 8420) &
BACKEND_PID=$!

# Start frontend
echo "→ Starting frontend (vite dev server)..."
(cd "$ROOT_DIR/frontend" && npm run dev) &
FRONTEND_PID=$!

# Trap to kill both on exit
trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit' INT TERM

echo ""
echo "Backend:  http://127.0.0.1:8420"
echo "Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop."

wait
