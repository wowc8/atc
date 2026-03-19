"""Agent deployment SOT — single source of truth for all agent configuration files.

Writes CLAUDE.md, .claude/settings.json, and hook scripts into a staging
directory (typically /tmp/{session_id}/) before launching an Ace or Manager.
Hook scripts report status back to the ATC API via the ``atc`` CLI.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default staging root for deployed agent files
_DEFAULT_STAGING_ROOT = Path("/tmp/atc-agents")


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
    repo_path: str | None = None
    github_repo: str | None = None
    api_base_url: str = "http://127.0.0.1:8420"
    model: str = "opus"
    allowed_commands: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    extra_context: str = ""


@dataclass
class ManagerDeploySpec:
    """Everything needed to deploy a Manager/Leader session's config files."""

    leader_id: str
    project_name: str
    goal: str
    session_id: str | None = None  # actual session_id for hooks
    repo_path: str | None = None
    github_repo: str | None = None
    api_base_url: str = "http://127.0.0.1:8420"
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
    api_base_url: str = "http://127.0.0.1:8420"
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

    # CLAUDE.md
    claude_md = _build_ace_claude_md(spec)
    written.append(_write_file(root / "CLAUDE.md", claude_md))

    # .claude/settings.json
    settings = _build_settings(
        model=spec.model,
        allowed_commands=_ace_allowed_commands(spec),
        hooks=_ace_hooks(spec),
    )
    written.append(_write_file(root / ".claude" / "settings.json", json.dumps(settings, indent=2)))

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

    # CLAUDE.md
    claude_md = _build_manager_claude_md(spec)
    written.append(_write_file(root / "CLAUDE.md", claude_md))

    # .claude/settings.json
    settings = _build_settings(
        model=spec.model,
        allowed_commands=_manager_allowed_commands(spec),
        hooks=_manager_hooks(spec),
    )
    written.append(_write_file(root / ".claude" / "settings.json", json.dumps(settings, indent=2)))

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

    # CLAUDE.md
    claude_md = _build_tower_claude_md(spec)
    written.append(_write_file(root / "CLAUDE.md", claude_md))

    # .claude/settings.json
    settings = _build_settings(
        model=spec.model,
        allowed_commands=_tower_allowed_commands(spec),
        hooks=_tower_hooks(spec),
    )
    written.append(_write_file(root / ".claude" / "settings.json", json.dumps(settings, indent=2)))

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

    if spec.extra_context:
        lines.extend(["## Context", "", spec.extra_context, ""])

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
        "",
        "## Role",
        "",
        "You are a **Leader** — a project manager in the ATC agent hierarchy.",
        "You receive a goal from Tower and are responsible for delivering it:",
        "",
        "- **Plan** — decompose the goal into well-scoped tasks with acceptance criteria.",
        "- **Create Aces** — spin up Ace sessions and assign one task each.",
        "- **Monitor** — track Ace progress, unblock them, reassign if needed.",
        "- **Review** — inspect Ace output to ensure quality before marking done.",
        "- **Coordinate** — manage dependencies between tasks so Aces don't conflict.",
        "- **Report** — keep Tower informed of milestone progress and blockers.",
        "",
        "You never write code directly — always delegate implementation to Aces.",
        "You own the task graph: create it, maintain it, and drive it to completion.",
        "When all tasks are done and verified, report completion to Tower.",
        "",
        "## Goal",
        "",
        spec.goal,
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
        "You talk directly to the user, understand their vision, and turn it into action:",
        "",
        "- **Listen** — understand the user's goal and ask clarifying questions when needed.",
        "- **Plan** — break the goal into projects and high-level milestones.",
        "- **Delegate** — spin up Leaders and provide them with goals and context.",
        "- **Monitor** — track Leader progress and intervene when off track.",
        "- **Communicate** — keep the user informed and ask for decisions.",
        "",
        "You never write code directly — always delegate through Leaders.",
        "You never manage individual tasks — that is the Leader's job.",
        "You are the single point of contact between the user and the agent hierarchy.",
        "",
    ]

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
) -> dict[str, Any]:
    """Build the .claude/settings.json content."""
    return {
        "model": model,
        "autoMemoryEnabled": False,
        "spinnerTipsEnabled": False,
        "permissions": {
            "allow": allowed_commands,
            "deny": [],
        },
        "hooks": hooks,
    }


# ---------------------------------------------------------------------------
# Hook configuration
# ---------------------------------------------------------------------------

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
    commands.extend(spec.allowed_commands)
    return commands


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


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
