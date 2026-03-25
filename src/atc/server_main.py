"""Sidecar entry point — starts uvicorn server."""
import multiprocessing
import os
import sys

# When running as a macOS .app bundle, the PATH is minimal (/usr/bin:/bin).
# Expand it to include common locations for claude CLI, tmux, git, etc.
_EXTRA_PATHS = [
    # Homebrew (Apple Silicon)
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    # Homebrew (Intel)
    "/usr/local/bin",
    "/usr/local/sbin",
    # nvm default (common Claude Code install location)
    os.path.expanduser("~/.nvm/versions/node/*/bin"),
    # npm global
    os.path.expanduser("~/.npm-global/bin"),
    os.path.expanduser("~/Library/Application Support/Claude/bin"),
    os.path.expanduser("~/.claude/bin"),
    # Standard unix
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]

# Resolve glob patterns (e.g. nvm node versions)
import glob
expanded = []
for p in _EXTRA_PATHS:
    if "*" in p:
        expanded.extend(sorted(glob.glob(p), reverse=True))  # newest version first
    else:
        expanded.append(p)

current_path = os.environ.get("PATH", "")
extra = ":".join(p for p in expanded if p not in current_path)
os.environ["PATH"] = extra + (":" + current_path if current_path else "")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run("atc.api.app:create_app", factory=True, host="127.0.0.1", port=8420, log_level="info")
