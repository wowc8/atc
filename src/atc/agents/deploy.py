"""Agent deployment SOT — single source of truth for all agent configuration files.

Writes CLAUDE.md, .claude/settings.json, and hook scripts into a staging
directory (typically /tmp/{session_id}/) before launching an Ace or Manager.
Hook scripts report status back to the ATC API via the ``atc`` CLI.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default staging root for deployed agent files
_DEFAULT_STAGING_ROOT = Path("/tmp/atc-agents")


def _resolve_api_base_url(api_base_url: str) -> str:
    """Return api_base_url, resolved dynamically.

    Resolution order:
    1. Explicitly provided ``api_base_url`` argument
    2. ``ATC_API_URL`` environment variable (set by the server at startup)
    3. ``load_settings()`` — reads atc.toml / env vars for host+port
    4. Hardcoded fallback (last resort, same default as ServerConfig)
    """
    if api_base_url:
        return api_base_url
    env_url = os.environ.get("ATC_API_URL", "")
    if env_url:
        return env_url.rstrip("/")
    try:
        from atc.config import load_settings as _load_settings
        _s = _load_settings()
        return f"http://{_s.server.host}:{_s.server.port}"
    except Exception:
        return "http://127.0.0.1:8420"


@dataclass(frozen=True)
class DeployedFiles:
    """Result of a deployment: root directory and manifest of written files."""

    root: Path
    files: list[str]

    @property
    def claude_md_path(self) -> Path:
        return self.root / "CLAUDE.md"

    @property
    def settings_path(self) -> Path:
        return self.root / ".claude" / "settings.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / ".manifest.json"


@dataclass(frozen=True)
class HookConfig:
    """Configuration for a single Claude Code hook."""

    event: str
    command: str


@dataclass
class AceDeploySpec:
    """Everything needed to deploy an Ace session's config files."""

    session_id: str
    project_name: str
    task_title: str
    task_description: str | None = None
    project_id: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None
    api_base_url: str = ""
    model: str = "opus"
    allowed_commands: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    context_entries: list[dict[str, Any]] = field(default_factory=list)
    extra_context: str = ""


@dataclass
class ManagerDeploySpec:
    """Everything needed to deploy a Manager/Leader session's config files."""

    leader_id: str
    project_name: str
    goal: str
    project_id: str | None = None  # project_id for API calls
    session_id: str | None = None  # actual session_id for hooks
    repo_path: str | None = None
    github_repo: str | None = None
    api_base_url: str = ""
    model: str = "opus"
    allowed_commands: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    context_entries: list[dict[str, Any]] = field(default_factory=list)
    initial_tasks: list[str] = field(default_factory=list)
    budget_ceiling_usd: float | None = None


@dataclass
class TowerDeploySpec:
    """Everything needed to deploy a Tower session's config files."""

    session_id: str
    project_name: str
    project_id: str
    repo_path: str | None = None
    github_repo: str | None = None
    api_base_url: str = ""
    model: str = "opus"
    allowed_commands: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def deploy_ace_files(
    spec: AceDeploySpec,
    *,
    staging_root: Path | None = None,
) -> DeployedFiles:
    """Write CLAUDE.md, .claude/settings.json, and hooks for an Ace session.

    Args:
        spec: Deployment specification for the Ace.
        staging_root: Override the staging root directory (default: /tmp/atc-agents).

    Returns:
        DeployedFiles with the root directory and list of all written file paths.
    """
    root = (staging_root or _DEFAULT_STAGING_ROOT) / spec.session_id
    written: list[str] = []

    # Resolve api_base_url from settings if not explicitly provided
    if not spec.api_base_url:
        spec = dataclasses.replace(spec, api_base_url=_resolve_api_base_url(""))

    # Ensure the staging directory is a git repo so Claude Code finds settings
    _ensure_git_repo(root)

    # Write trust acceptance to the user-level per-project settings so Claude
    # Code skips the "Do you trust this project?" dialog.
    _write_user_trust_settings(root)

    # CLAUDE.md
    claude_md = _build_ace_claude_md(spec)
    written.append(_write_file(root / "CLAUDE.md", claude_md))

    # .claude/settings.json
    settings = _build_settings(
        model=spec.model,
        allowed_commands=_ace_allowed_commands(spec),
        hooks=_ace_hooks(spec),
    )
    settings_json = json.dumps(settings, indent=2)
    written.append(_write_file(root / ".claude" / "settings.json", settings_json))
    # Also write settings.local.json — Claude Code reads trust/personal prefs from here
    written.append(_write_file(root / ".claude" / "settings.local.json", settings_json))

    # Hook scripts
    for hook in _ace_hook_scripts(spec):
        path = root / ".claude" / "hooks" / f"{hook.event}.sh"
        written.append(_write_executable(path, hook.command))

    # Manifest
    manifest = {"session_id": spec.session_id, "session_type": "ace", "files": written}
    written.append(_write_file(root / ".manifest.json", json.dumps(manifest, indent=2)))

    logger.info("Deployed ace files for %s → %s (%d files)", spec.session_id, root, len(written))
    return DeployedFiles(root=root, files=written)


def deploy_manager_files(
    spec: ManagerDeploySpec,
    *,
    staging_root: Path | None = None,
) -> DeployedFiles:
    """Write CLAUDE.md, .claude/settings.json, and hooks for a Manager/Leader session.

    Args:
        spec: Deployment specification for the Manager.
        staging_root: Override the staging root directory (default: /tmp/atc-agents).

    Returns:
        DeployedFiles with the root directory and list of all written file paths.
    """
    root = (staging_root or _DEFAULT_STAGING_ROOT) / spec.leader_id
    written: list[str] = []

    # Resolve api_base_url from settings if not explicitly provided
    if not spec.api_base_url:
        spec = dataclasses.replace(spec, api_base_url=_resolve_api_base_url(""))

    # Ensure the staging directory is a git repo so Claude Code finds settings
    _ensure_git_repo(root)

    # Write trust acceptance to the user-level per-project settings so Claude
    # Code skips the "Do you trust this project?" dialog.
    _write_user_trust_settings(root)

    # CLAUDE.md
    claude_md = _build_manager_claude_md(spec)
    written.append(_write_file(root / "CLAUDE.md", claude_md))

    # .claude/settings.json
    # Leaders must NOT create or edit files — that's the Ace's job.
    manager_denied = [
        "Edit",
        "Write",
        "NotebookEdit",
    ]
    settings = _build_settings(
        model=spec.model,
        allowed_commands=_manager_allowed_commands(spec),
        hooks=_manager_hooks(spec),
        denied_commands=manager_denied,
    )
    settings_json = json.dumps(settings, indent=2)
    written.append(_write_file(root / ".claude" / "settings.json", settings_json))
    # Also write settings.local.json — Claude Code reads trust/personal prefs from here
    written.append(_write_file(root / ".claude" / "settings.local.json", settings_json))

    # Hook scripts
    for hook in _manager_hook_scripts(spec):
        path = root / ".claude" / "hooks" / f"{hook.event}.sh"
        written.append(_write_executable(path, hook.command))

    # Manifest
    manifest = {"session_id": spec.leader_id, "session_type": "manager", "files": written}
    written.append(_write_file(root / ".manifest.json", json.dumps(manifest, indent=2)))

    logger.info("Deployed manager files for %s → %s (%d files)", spec.leader_id, root, len(written))
    return DeployedFiles(root=root, files=written)


def deploy_tower_files(
    spec: TowerDeploySpec,
    *,
    staging_root: Path | None = None,
) -> DeployedFiles:
    """Write CLAUDE.md, .claude/settings.json, and hooks for a Tower session.

    Args:
        spec: Deployment specification for the Tower.
        staging_root: Override the staging root directory (default: /tmp/atc-agents).

    Returns:
        DeployedFiles with the root directory and list of all written file paths.
    """
    root = (staging_root or _DEFAULT_STAGING_ROOT) / spec.session_id
    written: list[str] = []

    # Resolve api_base_url from settings if not explicitly provided
    if not spec.api_base_url:
        spec = dataclasses.replace(spec, api_base_url=_resolve_api_base_url(""))

    # Ensure the staging directory is a git repo so Claude Code finds settings
    _ensure_git_repo(root)

    # Write trust acceptance to the user-level per-project settings so Claude
    # Code skips the "Do you trust this project?" dialog.
    _write_user_trust_settings(root)

    # CLAUDE.md
    claude_md = _build_tower_claude_md(spec)
    written.append(_write_file(root / "CLAUDE.md", claude_md))

    # .claude/settings.json
    settings = _build_settings(
        model=spec.model,
        allowed_commands=_tower_allowed_commands(spec),
        hooks=_tower_hooks(spec),
    )
    settings_json = json.dumps(settings, indent=2)
    written.append(_write_file(root / ".claude" / "settings.json", settings_json))
    # Also write settings.local.json — Claude Code reads trust/personal prefs from here
    written.append(_write_file(root / ".claude" / "settings.local.json", settings_json))

    # Hook scripts
    for hook in _tower_hook_scripts(spec):
        path = root / ".claude" / "hooks" / f"{hook.event}.sh"
        written.append(_write_executable(path, hook.command))

    # Manifest
    manifest = {"session_id": spec.session_id, "session_type": "tower", "files": written}
    written.append(_write_file(root / ".manifest.json", json.dumps(manifest, indent=2)))

    logger.info("Deployed tower files for %s → %s (%d files)", spec.session_id, root, len(written))
    return DeployedFiles(root=root, files=written)


def cleanup_deployed_files(root: Path) -> None:
    """Remove all files listed in a deployment manifest, then the directory tree.

    Args:
        root: The deployment root directory containing .manifest.json.
    """
    manifest_path = root / ".manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for filepath in manifest.get("files", []):
            p = Path(filepath)
            if p.exists():
                p.unlink()
        manifest_path.unlink()

    # Remove .git directory created by _ensure_git_repo
    git_dir = root / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)

    # Remove empty directories bottom-up
    if root.exists():
        for dirpath, _dirnames, _filenames in os.walk(str(root), topdown=False):
            dp = Path(dirpath)
            if not any(dp.iterdir()):
                dp.rmdir()


# ---------------------------------------------------------------------------
# CLAUDE.md builders
# ---------------------------------------------------------------------------


def _build_ace_claude_md(spec: AceDeploySpec) -> str:
    """Build the CLAUDE.md content for an Ace session."""
    lines = [
        f"# {spec.project_name} — Ace Session",
        "",
        f"Session ID: `{spec.session_id}`",
        "",
        "## Role",
        "",
        "You are an **Ace** — an expert developer in the ATC agent hierarchy.",
        "You receive a single task from your Leader and own it end-to-end:",
        "",
        "- **Design** the solution before writing code.",
        "- **Implement** clean, production-quality code that follows project conventions.",
        "- **Write tests** that cover the critical paths and edge cases.",
        "- **Run tests** and fix failures — do not move on until all tests pass.",
        "- **Self-review** your diff for bugs, security issues, and style.",
        "- **Iterate** until the code is solid and CI-ready.",
        "- **Create a PR** with a clear description: what, why, how tested.",
        "",
        "Never move on to the next step until the current one is done right.",
        "Never ask the Leader for help you can figure out yourself.",
        "When blocked on something outside your control, report it immediately.",
        "",
        "## Task",
        "",
        f"**{spec.task_title}**",
        "",
    ]

    if spec.task_description:
        lines.extend([spec.task_description, ""])

    lines.extend(
        [
            "## Reporting",
            "",
            "Use the `atc` CLI to report status back to the ATC tower:",
            "",
            "```bash",
            f'atc ace status "{spec.session_id}" working   # when actively working',
            f'atc ace status "{spec.session_id}" waiting   # when idle/waiting',
            f'atc ace done "{spec.session_id}"              # when task is complete',
            f'atc ace blocked "{spec.session_id}" --reason "description"  # when blocked',
            "```",
            "",
        ]
    )

    if spec.constraints:
        lines.extend(["## Constraints", ""])
        for c in spec.constraints:
            lines.append(f"- {c}")
        lines.append("")

    if spec.context_entries:
        lines.extend(["## Project Context", ""])
        for entry in spec.context_entries:
            key = entry.get("key", "")
            value = entry.get("value", "")
            if isinstance(value, dict):
                value = json.dumps(value, indent=2)
            lines.extend([f"### {key}", "", str(value), ""])

    if spec.extra_context:
        lines.extend(["## Context", "", spec.extra_context, ""])

    # Context read/write CLI instructions
    hooks_dir = f"/tmp/atc-agents/{spec.session_id}/.claude/hooks"
    lines.extend(_context_cli_instructions(hooks_dir))

    if spec.github_repo:
        lines.extend(
            [
                "## Repository",
                "",
                f"GitHub: `{spec.github_repo}`",
                "",
            ]
        )

    return "\n".join(lines)


def _build_manager_claude_md(spec: ManagerDeploySpec) -> str:
    """Build the CLAUDE.md content for a Manager/Leader session."""
    lines = [
        f"# {spec.project_name} — Leader Session",
        "",
        f"Leader ID: `{spec.leader_id}`",
        f"Project ID: `{spec.project_id or 'unknown'}`",
        "",
        "## Role",
        "",
        "You are a **Leader** — a project manager in the ATC agent hierarchy.",
        "You receive a goal from Tower and are responsible for delivering it.",
        "",
        "### CRITICAL RULE — NEVER DO WORK DIRECTLY",
        "",
        "**You MUST NOT write code, create files, edit files, or implement anything yourself.**",
        "**You MUST NOT use the Edit, Write, or NotebookEdit tools.**",
        "Your ONLY job is to manage — break the goal into tasks, spawn Aces to do the work,",
        "and monitor their progress. If you catch yourself about to create or modify a file,",
        "STOP — that is an Ace's job.",
        "",
        "### Your Workflow",
        "",
        "1. **Decompose** — break the goal into well-scoped tasks with acceptance criteria.",
        f'   `curl -X POST {spec.api_base_url}/api/projects/{spec.project_id}/leader/decompose`',
        "2. **Spawn Aces** — create Ace sessions for each ready task.",
        f'   `curl -X POST {spec.api_base_url}/api/projects/{spec.project_id}/leader/spawn-aces`',
        "3. **Instruct** — send work instructions to each Ace.",
        f'   `curl -X POST {spec.api_base_url}/api/projects/{spec.project_id}/leader/instruct`',
        "4. **Monitor** — track Ace progress, unblock them, reassign if needed.",
        f'   `curl {spec.api_base_url}/api/projects/{spec.project_id}/leader/progress`',
        "5. **Review** — inspect Ace output to ensure quality before marking done.",
        f'   `curl -X POST {spec.api_base_url}/api/projects/{spec.project_id}/leader/task-done`',
        "6. **Report** — keep Tower informed of milestone progress and blockers.",
        "",
        "You own the task graph: create it, maintain it, and drive it to completion.",
        "When all tasks are done and verified, report completion to Tower.",
        "",
        "## Goal",
        "",
        spec.goal or "(No specific goal set — await instructions from Tower.)",
        "",
    ]

    if spec.budget_ceiling_usd is not None:
        lines.extend(
            [
                "## Budget",
                "",
                f"Ceiling: **${spec.budget_ceiling_usd:.2f} USD**",
                "",
            ]
        )

    if spec.constraints:
        lines.extend(["## Constraints", ""])
        for c in spec.constraints:
            lines.append(f"- {c}")
        lines.append("")

    if spec.initial_tasks:
        lines.extend(["## Initial Task Breakdown", ""])
        for i, task in enumerate(spec.initial_tasks, 1):
            lines.append(f"{i}. {task}")
        lines.append("")

    if spec.context_entries:
        lines.extend(["## Project Context", ""])
        for entry in spec.context_entries:
            key = entry.get("key", "")
            value = entry.get("value", "")
            if isinstance(value, dict):
                value = json.dumps(value, indent=2)
            lines.extend([f"### {key}", "", str(value), ""])

    # Context read/write CLI instructions
    hooks_dir = f"/tmp/atc-agents/{spec.leader_id}/.claude/hooks"
    lines.extend(_context_cli_instructions(hooks_dir))

    lines.extend(
        [
            "## Reporting",
            "",
            "Use the `atc` CLI to report status:",
            "",
            "```bash",
            f'atc ace status "{spec.leader_id}" working',
            f'atc ace status "{spec.leader_id}" waiting',
            "```",
            "",
        ]
    )

    if spec.github_repo:
        lines.extend(
            [
                "## Repository",
                "",
                f"GitHub: `{spec.github_repo}`",
                "",
            ]
        )

    return "\n".join(lines)


def _build_tower_claude_md(spec: TowerDeploySpec) -> str:
    """Build the CLAUDE.md content for a Tower session."""
    lines = [
        f"# {spec.project_name} — Tower Session",
        "",
        f"Session ID: `{spec.session_id}`",
        f"Project ID: `{spec.project_id}`",
        "",
        "## Role",
        "",
        "You are **Tower** — the top-level orchestrator in the ATC agent hierarchy.",
        "You talk directly to the user, understand their vision, and turn it into action.",
        "",
        "## CRITICAL: You Are Fully Autonomous",
        "",
        "**You NEVER ask permission to do your job.** You do not ask:",
        "- 'Should I check on the leader?'",
        "- 'Would you like me to monitor progress?'",
        "- 'Do you want me to continue?'",
        "",
        "You JUST DO IT. Your job is to run autonomously in the background.",
        "Only interrupt the user when you need a decision that only they can make",
        "(e.g. ambiguous requirements, budget approval, conflicting priorities).",
        "",
        "## Autonomous Monitoring Loop",
        "",
        "After starting a Leader, you IMMEDIATELY begin a monitoring loop:",
        "",
        "1. Wait 30 seconds",
        "2. Check progress API: `curl -s http://127.0.0.1:8420/api/projects/<id>/leader/progress`",
        "3. If tasks are being created and aces are working: wait 60s and repeat from step 2",
        "4. If leader appears stuck (no progress for >2 minutes): send a SHORT nudge ONLY:",
        "   `atc leader message --project-id <id> --message 'Please continue with your goal.'`",
        "5. If leader fails 3 times: report to user with a summary and ask how to proceed",
        "",
        "**CRITICAL — NEVER paste context, goals, or project details into the Leader terminal.**",
        "Leader already has everything it needs in its CLAUDE.md. Only send short nudges.",
        "Wrong: `atc leader message ... --message 'Build a web app that does X. Requirements: ...'`",
        "Right: `atc leader message ... --message 'Please continue with your goal.'`",
        "",
        "**Do not wait for the user to tell you to check. Just check.**",
        "",
        "## Your Responsibilities",
        "",
        "- **Listen** — understand the user's goal. Ask ONE clarifying question if truly ambiguous.",
        "- **Plan** — break the goal into projects and high-level milestones.",
        "- **Delegate** — spin up Leaders and provide them with goals and context.",
        "- **Monitor** — autonomously track Leader progress on a schedule. No asking.",
        "- **Intervene** — unblock Leaders when they are stuck. No asking.",
        "- **Report** — proactively surface blockers and completions to the user.",
        "",
        "You never write code directly — always delegate through Leaders.",
        "You never manage individual tasks — that is the Leader's job.",
        "You are the always-on orchestration layer. Act like it.",
        "",
    ]

    # CLI commands documentation
    lines.extend([
        "## ATC CLI Commands",
        "",
        "Use these commands to manage projects, leaders, and aces.",
        "",
        "### Project Management",
        "```bash",
        "atc projects list                                    # List all projects",
        "atc projects create --name 'Name' --description '...' # Create a new project",
        "atc projects show <project-id>                       # Show project details",
        "```",
        "",
        "### Leader Lifecycle",
        "```bash",
        "atc leader start --project-id <id>                   # Start leader for a project",
        "atc leader start --project-id <id> --goal '...'      # Start leader with a goal",
        "atc leader stop --project-id <id>                    # Stop leader for a project",
        "atc leader message --project-id <id> --message '...' # Send a message to the leader's terminal",
        "```",
        "",
        "### Ace Management",
        "```bash",
        "atc ace create --project-id <id> --name 'ace-name'   # Create an ace session",
        "atc ace list --project-id <id>                       # List aces for a project",
        "```",
        "",
        "### Workflow",
        "",
        "When the user asks to create a project:",
        "1. `atc projects create --name '...' --description '...'` — note the project ID from the response",
        "2. `atc leader start --project-id <id> --goal '...'` — start the leader with the goal",
        "   The leader receives its full goal and context automatically. Do NOT message it again with the same info.",
        "3. Monitor progress with: `curl -s http://127.0.0.1:8420/api/projects/<id>/leader/progress`",
        "",
        "All commands output JSON. Parse the `id` field from create responses to use in subsequent commands.",
        "",
        "Use `atc leader message` ONLY for short nudges when leader is stuck, never to repeat context.",
        "",
    ])

    # Context read/write CLI instructions
    hooks_dir = f"/tmp/atc-agents/{spec.session_id}/.claude/hooks"
    lines.extend(_context_cli_instructions(hooks_dir))

    if spec.repo_path or spec.github_repo:
        lines.append("## Repository")
        lines.append("")
        if spec.repo_path:
            lines.append(f"Local path: `{spec.repo_path}`")
        if spec.github_repo:
            lines.append(f"GitHub: `{spec.github_repo}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# settings.json builder
# ---------------------------------------------------------------------------


def _build_settings(
    *,
    model: str,
    allowed_commands: list[str],
    hooks: dict[str, list[dict[str, Any]]],
    denied_commands: list[str] | None = None,
) -> dict[str, Any]:
    """Build the .claude/settings.json content."""
    return {
        "model": model,
        "hasTrustDialogAccepted": True,
        "enableAllProjectMcpServers": True,
        "autoMemoryEnabled": False,
        "spinnerTipsEnabled": False,
        "permissions": {
            "allow": allowed_commands,
            "deny": denied_commands or [],
        },
        "hooks": hooks,
    }


# ---------------------------------------------------------------------------
# Hook configuration
# ---------------------------------------------------------------------------

def _context_cli_instructions(hooks_dir: str) -> list[str]:
    """Return CLAUDE.md lines explaining the context read/write scripts."""
    return [
        "## Context Read/Write",
        "",
        "You can read and write context entries that persist across sessions.",
        "Use the deployed helper scripts:",
        "",
        "```bash",
        "# List all context entries visible to you",
        f"bash {hooks_dir}/context_read.sh",
        "",
        "# Read a specific entry by key",
        f'bash {hooks_dir}/context_read.sh --key "my-key"',
        "",
        "# Write a context entry (creates or updates by key)",
        f'bash {hooks_dir}/context_write.sh --key "my-key" --value "my value"',
        "",
        "# Write with explicit type (text, json, list, status, link)",
        f'bash {hooks_dir}/context_write.sh --key "config"'
        ' --value \'{"a":1}\' --type json',
        "```",
        "",
        "Context entries are scoped to your session and visible to your scope's",
        "inheritance rules. Use them to record findings, decisions, and notes",
        "that should persist beyond the current conversation.",
        "",
    ]


_STATUS_HOOK_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
ATC_API="{api_base_url}"
SESSION_ID="{session_id}"
"""

_POST_TOOL_USE_BODY = """
# Report working status after each tool use
curl -sf -X PATCH "$ATC_API/api/aces/$SESSION_ID/status" \
  -H "Content-Type: application/json" \
  -d '{{"status": "working"}}' >/dev/null 2>&1 || true

# Send heartbeat
curl -sf -X POST "$ATC_API/api/heartbeat/$SESSION_ID" >/dev/null 2>&1 || true

# Memory checkpoint: track tool call count and snapshot progress every 5 calls
TOOL_COUNT_DIR="/tmp/$SESSION_ID"
mkdir -p "$TOOL_COUNT_DIR"
TOOL_COUNT_FILE="$TOOL_COUNT_DIR/.tool_count"
LAST_OUTPUT_FILE="$TOOL_COUNT_DIR/.last_output"

if [[ -f "$TOOL_COUNT_FILE" ]]; then
  _COUNT=$(cat "$TOOL_COUNT_FILE" 2>/dev/null || echo "0")
  _COUNT=$(( _COUNT + 1 ))
else
  _COUNT=1
fi
echo "$_COUNT" > "$TOOL_COUNT_FILE"

# Every 5 tool calls, write a progress snapshot to Ace STM
if (( _COUNT % 5 == 0 )); then
  if [[ -f "$LAST_OUTPUT_FILE" ]]; then
    _CONTENT=$(head -c 500 "$LAST_OUTPUT_FILE" 2>/dev/null || echo "checkpoint at tool call $_COUNT")
  else
    _CONTENT="Tool call checkpoint: $_COUNT calls completed"
  fi
  curl -sf -X POST "$ATC_API/api/memory/ace/$SESSION_ID/write" \
    -H "Content-Type: application/json" \
    -d "{{\\"content\\": \\"$_CONTENT\\", \\"tool_call_count\\": $_COUNT}}" \
    >/dev/null 2>&1 || true
fi
"""

_STOP_HOOK_BODY = """
# Report waiting status when agent stops
curl -sf -X PATCH "$ATC_API/api/aces/$SESSION_ID/status" \
  -H "Content-Type: application/json" \
  -d '{{"status": "waiting"}}' >/dev/null 2>&1 || true
"""

_NOTIFICATION_HOOK_BODY = """
# Forward notifications to ATC
NOTIFICATION="${{1:-}}"
curl -sf -X POST "$ATC_API/api/aces/$SESSION_ID/notify" \
  -H "Content-Type: application/json" \
  -d "{{\\"message\\": \\"$NOTIFICATION\\"}}" >/dev/null 2>&1 || true
"""


_CONTEXT_READ_TEMPLATE = r"""#!/usr/bin/env bash
set -euo pipefail
ATC_API="{api_base_url}"
SESSION_ID="{session_id}"

# Usage: context_read.sh [--key KEY]
# Lists context entries visible to this session, or fetches a specific entry by key.

KEY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$KEY" ]]; then
  curl -sf "$ATC_API/api/sessions/$SESSION_ID/context?key=$KEY" \
    -H "Accept: application/json" 2>/dev/null
else
  curl -sf "$ATC_API/api/sessions/$SESSION_ID/context" \
    -H "Accept: application/json" 2>/dev/null
fi
"""

_CONTEXT_WRITE_TEMPLATE = r"""#!/usr/bin/env bash
set -euo pipefail
ATC_API="{api_base_url}"
SESSION_ID="{session_id}"
SCOPE="{scope}"

# Usage: context_write.sh --key KEY --value VALUE [--type TYPE]
# Creates or updates a context entry for this session.
# TYPE defaults to "text". Valid types: text, json, list, status, link.

KEY=""
VALUE=""
ENTRY_TYPE="text"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="$2"; shift 2 ;;
    --value) VALUE="$2"; shift 2 ;;
    --type) ENTRY_TYPE="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$KEY" || -z "$VALUE" ]]; then
  echo "Usage: context_write.sh --key KEY --value VALUE [--type TYPE]" >&2
  exit 1
fi

# Check if entry with this key already exists
EXISTING=$(curl -sf "$ATC_API/api/sessions/$SESSION_ID/context?key=$KEY" \
  -H "Accept: application/json" 2>/dev/null || echo "[]")

ENTRY_ID=$(echo "$EXISTING" | python3 -c "
import sys, json
entries = json.load(sys.stdin)
if entries:
    print(entries[0]['id'])
" 2>/dev/null || true)

if [[ -n "$ENTRY_ID" ]]; then
  # Update existing entry
  curl -sf -X PUT "$ATC_API/api/context/$ENTRY_ID" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json, sys
print(json.dumps({{'value': sys.argv[1], 'entry_type': sys.argv[2], 'updated_by': '$SCOPE'}}))
" "$VALUE" "$ENTRY_TYPE")" 2>/dev/null
else
  # Create new entry
  curl -sf -X POST "$ATC_API/api/sessions/$SESSION_ID/context" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json, sys
d = {{'scope': '$SCOPE', 'key': sys.argv[1], 'value': sys.argv[2]}}
d.update({{'entry_type': sys.argv[3], 'updated_by': '$SCOPE'}})
print(json.dumps(d))
" "$KEY" "$VALUE" "$ENTRY_TYPE")" 2>/dev/null
fi
"""


def _ace_hook_scripts(spec: AceDeploySpec) -> list[HookConfig]:
    """Build the hook shell scripts for an Ace session."""
    header = _STATUS_HOOK_TEMPLATE.format(
        api_base_url=spec.api_base_url,
        session_id=spec.session_id,
    )
    return [
        HookConfig(event="PostToolUse", command=header + _POST_TOOL_USE_BODY),
        HookConfig(event="Stop", command=header + _STOP_HOOK_BODY),
        HookConfig(event="Notification", command=header + _NOTIFICATION_HOOK_BODY),
        HookConfig(
            event="context_read",
            command=_CONTEXT_READ_TEMPLATE.format(
                api_base_url=spec.api_base_url, session_id=spec.session_id,
            ),
        ),
        HookConfig(
            event="context_write",
            command=_CONTEXT_WRITE_TEMPLATE.format(
                api_base_url=spec.api_base_url, session_id=spec.session_id, scope="ace",
            ),
        ),
    ]


def _manager_hook_scripts(spec: ManagerDeploySpec) -> list[HookConfig]:
    """Build the hook shell scripts for a Manager session."""
    # Use the actual session_id for hooks (not leader_id) so that
    # /api/aces/{session_id}/status and /api/heartbeat/{session_id} resolve correctly.
    hook_session_id = spec.session_id or spec.leader_id
    header = _STATUS_HOOK_TEMPLATE.format(
        api_base_url=spec.api_base_url,
        session_id=hook_session_id,
    )
    return [
        HookConfig(event="PostToolUse", command=header + _POST_TOOL_USE_BODY),
        HookConfig(event="Stop", command=header + _STOP_HOOK_BODY),
        HookConfig(event="Notification", command=header + _NOTIFICATION_HOOK_BODY),
        HookConfig(
            event="context_read",
            command=_CONTEXT_READ_TEMPLATE.format(
                api_base_url=spec.api_base_url, session_id=hook_session_id,
            ),
        ),
        HookConfig(
            event="context_write",
            command=_CONTEXT_WRITE_TEMPLATE.format(
                api_base_url=spec.api_base_url, session_id=hook_session_id, scope="leader",
            ),
        ),
    ]


def _ace_hooks(spec: AceDeploySpec) -> dict[str, list[dict[str, Any]]]:
    """Build the hooks section for .claude/settings.json (Ace)."""
    hooks_dir = f"/tmp/atc-agents/{spec.session_id}/.claude/hooks"
    return _hooks_dict(hooks_dir)


def _manager_hooks(spec: ManagerDeploySpec) -> dict[str, list[dict[str, Any]]]:
    """Build the hooks section for .claude/settings.json (Manager)."""
    # The deploy root uses leader_id as the directory name (see deploy_manager_files)
    hooks_dir = f"/tmp/atc-agents/{spec.leader_id}/.claude/hooks"
    return _hooks_dict(hooks_dir)


def _tower_hook_scripts(spec: TowerDeploySpec) -> list[HookConfig]:
    """Build the hook shell scripts for a Tower session."""
    header = _STATUS_HOOK_TEMPLATE.format(
        api_base_url=spec.api_base_url,
        session_id=spec.session_id,
    )
    return [
        HookConfig(event="PostToolUse", command=header + _POST_TOOL_USE_BODY),
        HookConfig(event="Stop", command=header + _STOP_HOOK_BODY),
        HookConfig(event="Notification", command=header + _NOTIFICATION_HOOK_BODY),
        HookConfig(
            event="context_read",
            command=_CONTEXT_READ_TEMPLATE.format(
                api_base_url=spec.api_base_url, session_id=spec.session_id,
            ),
        ),
        HookConfig(
            event="context_write",
            command=_CONTEXT_WRITE_TEMPLATE.format(
                api_base_url=spec.api_base_url, session_id=spec.session_id, scope="tower",
            ),
        ),
    ]


def _tower_hooks(spec: TowerDeploySpec) -> dict[str, list[dict[str, Any]]]:
    """Build the hooks section for .claude/settings.json (Tower)."""
    hooks_dir = f"/tmp/atc-agents/{spec.session_id}/.claude/hooks"
    return _hooks_dict(hooks_dir)


def _hooks_dict(hooks_dir: str) -> dict[str, list[dict[str, Any]]]:
    """Build a hooks dict pointing to shell scripts in the given directory.

    Claude Code settings.json hooks format requires each entry to have a
    ``matcher`` string and a ``hooks`` array of command objects.
    """
    return {
        "PostToolUse": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": f"bash {hooks_dir}/PostToolUse.sh"}],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": f"bash {hooks_dir}/Stop.sh"}],
            }
        ],
        "Notification": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": f"bash {hooks_dir}/Notification.sh"}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Allowed commands
# ---------------------------------------------------------------------------

_BASE_ALLOWED_COMMANDS = [
    "Bash(atc ace *)",
    "Bash(git *)",
    "Bash(gh *)",
    "Bash(npm *)",
    "Bash(npx *)",
    "Bash(python3 *)",
    "Bash(pip *)",
    "Bash(pytest *)",
    "Bash(ruff *)",
    "Bash(mypy *)",
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(mkdir *)",
    "Bash(cp *)",
    "Bash(mv *)",
    "Bash(curl *)",
    "Bash(bash *)",
    "Bash(echo *)",
    "Bash(find *)",
    "Bash(grep *)",
    "Bash(sed *)",
    "Bash(awk *)",
    "Bash(chmod *)",
    "Bash(touch *)",
    "Bash(rm *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(wc *)",
    "Bash(sort *)",
    "Bash(uniq *)",
    "Bash(tee *)",
    "Bash(cargo *)",
    "Bash(rustc *)",
    "Bash(make *)",
    "Bash(yarn *)",
    "Bash(pnpm *)",
    "Bash(uv *)",
    "Bash(uvicorn *)",
]


def _ace_allowed_commands(spec: AceDeploySpec) -> list[str]:
    """Build the allowed commands list for an Ace."""
    commands = list(_BASE_ALLOWED_COMMANDS)
    commands.extend(spec.allowed_commands)
    return commands


def _manager_allowed_commands(spec: ManagerDeploySpec) -> list[str]:
    """Build the allowed commands list for a Manager."""
    commands = list(_BASE_ALLOWED_COMMANDS)
    commands.append("Bash(atc tower *)")
    commands.extend(spec.allowed_commands)
    return commands


def _tower_allowed_commands(spec: TowerDeploySpec) -> list[str]:
    """Build the allowed commands list for a Tower."""
    commands = list(_BASE_ALLOWED_COMMANDS)
    commands.append("Bash(atc tower *)")
    commands.append("Bash(atc projects *)")
    commands.append("Bash(atc leader *)")
    commands.extend(spec.allowed_commands)
    return commands


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _ensure_git_repo(root: Path) -> None:
    """Initialize a bare git repo at *root* so Claude Code recognizes it as a project.

    Claude Code looks for ``.claude/settings.json`` relative to the nearest
    git root.  Without a ``.git`` marker the staging directory is not seen as a
    project, and the ``hasTrustDialogAccepted`` setting is ignored.
    """
    git_dir = root / ".git"
    if not git_dir.exists():
        subprocess.run(
            ["git", "init", "--quiet", str(root)],
            check=True,
            capture_output=True,
        )
        logger.debug("Initialized git repo at %s", root)


def _write_user_trust_settings(root: Path) -> None:
    """Write trust acceptance to the user-level per-project settings.

    Claude Code stores per-project trust decisions at::

        ~/.claude/projects/<encoded-path>/settings.local.json

    where ``<encoded-path>`` is the resolved project path with ``/`` replaced
    by ``-``.  Writing ``hasTrustDialogAccepted`` at the project level
    (``.claude/settings.local.json``) is **not** sufficient — Claude Code only
    reads trust acceptance from the user-level location.

    This function creates the user-level settings file so Claude Code skips
    the "Do you trust this project?" dialog entirely.
    """
    # Resolve symlinks (e.g. /tmp → /private/tmp on macOS) to match
    # the path Claude Code uses internally.
    resolved = root.resolve()
    encoded_path = str(resolved).replace("/", "-")

    claude_home = Path.home() / ".claude"
    user_project_dir = claude_home / "projects" / encoded_path
    user_settings_path = user_project_dir / "settings.local.json"

    trust_settings = {
        "hasTrustDialogAccepted": True,
        "enableAllProjectMcpServers": True,
    }

    # Merge with existing settings if the file already exists
    if user_settings_path.exists():
        try:
            existing = json.loads(user_settings_path.read_text())
            existing.update(trust_settings)
            trust_settings = existing
        except (json.JSONDecodeError, OSError):
            pass  # overwrite if corrupt

    user_project_dir.mkdir(parents=True, exist_ok=True)
    user_settings_path.write_text(json.dumps(trust_settings, indent=2))
    logger.debug(
        "Wrote user-level trust settings for %s → %s", resolved, user_settings_path
    )


def _write_file(path: Path, content: str) -> str:
    """Write a text file, creating parent directories as needed. Returns the path string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)


def _write_executable(path: Path, content: str) -> str:
    """Write a file and make it executable. Returns the path string."""
    filepath = _write_file(path, content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return filepath
