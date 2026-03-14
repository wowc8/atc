#!/usr/bin/env bash
# Start backend + frontend in dev mode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Starting ATC in development mode..."

# Start backend
echo "→ Starting backend (uvicorn with reload)..."
(cd "$ROOT_DIR" && python -m uvicorn atc.api.app:create_app --factory --reload --host 127.0.0.1 --port 8420) &
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
