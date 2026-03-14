#!/usr/bin/env bash
# Full production build: PyInstaller → Vite → Tauri
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Building ATC production app..."

# 1. Build Python sidecar with PyInstaller
echo "→ Building Python sidecar..."
# TODO: pyinstaller --onedir src/atc/api/app.py -n atc-server-sidecar --distpath binaries/

# 2. Build frontend
echo "→ Building frontend..."
(cd "$ROOT_DIR/frontend" && npm run build)

# 3. Build Tauri app
echo "→ Building Tauri app..."
# TODO: (cd "$ROOT_DIR/src-tauri" && cargo tauri build)

echo "Build complete."
