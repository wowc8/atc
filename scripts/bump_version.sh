#!/usr/bin/env bash
# Sync version across pyproject.toml / Cargo.toml / tauri.conf.json / package.json
# Usage: ./scripts/bump_version.sh <new-version>
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <new-version>"
    echo "Example: $0 0.2.0"
    exit 1
fi

VERSION="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Bumping version to ${VERSION}..."

# pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"${VERSION}\"/" "$ROOT_DIR/pyproject.toml"

# src/atc/__init__.py
sed -i '' "s/__version__ = \".*\"/__version__ = \"${VERSION}\"/" "$ROOT_DIR/src/atc/__init__.py"

# Cargo.toml
sed -i '' "s/^version = \".*\"/version = \"${VERSION}\"/" "$ROOT_DIR/src-tauri/Cargo.toml"

# tauri.conf.json
sed -i '' "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$ROOT_DIR/src-tauri/tauri.conf.json"

# frontend/package.json
sed -i '' "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$ROOT_DIR/frontend/package.json"

echo "Version updated to ${VERSION} in all files."
