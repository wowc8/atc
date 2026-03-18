"""Dataclass models for all core database entities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class Project:
    id: str
    name: str
    status: str  # active|paused|archived
    description: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None
    agent_provider: str = "claude_code"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Leader:
    id: str
    project_id: str
    status: str  # idle|planning|managing|paused|error
    session_id: str | None = None
    context: dict[str, Any] | None = None
    goal: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def context_json(self) -> str | None:
        """Serialize context dict to JSON for DB storage."""
        return json.dumps(self.context) if self.context is not None else None

    @staticmethod
    def context_from_json(raw: str | None) -> dict[str, Any] | None:
        """Deserialize context JSON from DB."""
        return json.loads(raw) if raw is not None else None


@dataclass
class Session:
    id: str
    project_id: str
    session_type: str  # ace|manager
    name: str
    status: str  # idle|connecting|working|paused|waiting|disconnected|error
    task_id: str | None = None
    host: str | None = None
    tmux_session: str | None = None
    tmux_pane: str | None = None
    alternate_on: bool = False
    auto_accept: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Task:
    id: str
    project_id: str
    leader_id: str
    title: str
    status: str  # pending|assigned|in_progress|blocked|done|cancelled
    parent_task_id: str | None = None
    description: str | None = None
    priority: int = 0
    assigned_to: str | None = None
    result: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def result_json(self) -> str | None:
        """Serialize result dict to JSON for DB storage."""
        return json.dumps(self.result) if self.result is not None else None

    @staticmethod
    def result_from_json(raw: str | None) -> dict[str, Any] | None:
        """Deserialize result JSON from DB."""
        return json.loads(raw) if raw is not None else None


@dataclass
class ProjectBudget:
    project_id: str
    daily_token_limit: int | None = None
    monthly_cost_limit: float | None = None
    warn_threshold: float = 0.8
    current_status: str = "ok"  # ok|warn|exceeded
    updated_at: str = ""


@dataclass
class UsageEvent:
    id: str
    event_type: str  # ai_tokens|ai_cost|cpu|ram|disk|github_api
    recorded_at: str
    project_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cpu_pct: float | None = None
    ram_mb: float | None = None
    disk_mb: float | None = None
    api_calls: int | None = None


@dataclass
class GitHubPR:
    id: str  # "{owner}/{repo}#{number}"
    project_id: str | None
    number: int
    title: str | None = None
    status: str | None = None  # open|merged|closed
    ci_status: str | None = None  # pending|running|success|failure
    url: str | None = None
    updated_at: str = ""


@dataclass
class FailureLog:
    id: str
    level: str  # info|warning|error|critical
    category: str  # creation_failure|runtime_error|budget_exceeded|etc
    message: str
    context: str  # JSON
    created_at: str = ""
    project_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    stack_trace: str | None = None
    resolved: bool = False


@dataclass
class Notification:
    id: str
    level: str  # info|warning|error|budget
    message: str
    created_at: str = ""
    project_id: str | None = None
    read: bool = False


@dataclass
class Config:
    key: str
    value: str
    updated_at: str = ""


@dataclass
class TowerMemory:
    id: str
    key: str
    value: str  # JSON
    project_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TaskGraph:
    id: str
    project_id: str
    title: str
    status: str  # todo|in_progress|done
    description: str | None = None
    assigned_ace_id: str | None = None
    dependencies: list[str] | None = None
    created_at: str = ""
    updated_at: str = ""

    def dependencies_json(self) -> str | None:
        """Serialize dependencies list to JSON for DB storage."""
        return json.dumps(self.dependencies) if self.dependencies is not None else None

    @staticmethod
    def dependencies_from_json(raw: str | None) -> list[str] | None:
        """Deserialize dependencies JSON from DB."""
        return json.loads(raw) if raw is not None else None


@dataclass
class SessionHeartbeat:
    session_id: str
    health: str  # alive|stale|stopped
    last_heartbeat_at: str
    registered_at: str
    updated_at: str


@dataclass
class AppEvent:
    id: str
    level: str  # debug|info|warning|error|critical
    category: str  # session|task|error|cost|system
    message: str
    created_at: str = ""
    detail: str | None = None  # JSON
    project_id: str | None = None
    session_id: str | None = None


@dataclass
class ContextEntry:
    id: str
    project_id: str
    key: str
    entry_type: str  # text|status|list|link|json
    value: str  # JSON-encoded
    position: int = 0
    updated_by: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class FeatureFlag:
    id: str
    key: str  # unique slug e.g. 'remote_aces', 'tower_memory'
    name: str  # human-readable display name
    description: str | None = None
    enabled: bool = False
    metadata: str | None = None  # optional JSON
    created_at: str = ""
    updated_at: str = ""
