#!/usr/bin/env bash
# Full production build: PyInstaller → Vite → Tauri
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Building ATC production app..."

# 1. Build Python sidecar with PyInstaller
echo "→ Building Python sidecar..."
mkdir -p "$ROOT_DIR/src-tauri/binaries"
(cd "$ROOT_DIR" && pyinstaller src-tauri/atc-server.spec)
# Detect current Rust target triple and rename binary accordingly
RUST_TARGET=$(rustc -vV | grep host | awk '{print $2}')
mv "$ROOT_DIR/src-tauri/binaries/atc-server" "$ROOT_DIR/src-tauri/binaries/atc-server-${RUST_TARGET}"
chmod +x "$ROOT_DIR/src-tauri/binaries/atc-server-${RUST_TARGET}"

# 2. Build frontend
echo "→ Building frontend..."
(cd "$ROOT_DIR/frontend" && npm run build)

# 3. Build Tauri app
echo "→ Building Tauri app..."
(cd "$ROOT_DIR" && cargo tauri build)

echo "Build complete."
