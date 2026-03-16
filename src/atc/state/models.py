"""Dataclass models for ATC entities — maps to SQLite tables."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Project:
    id: str
    name: str
    status: str  # active|paused|archived
    description: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Leader:
    id: str
    project_id: str
    status: str  # idle|planning|managing|paused|error
    session_id: str | None = None
    context: dict | None = None  # type: ignore[type-arg]
    goal: str | None = None
    created_at: str = ""
    updated_at: str = ""


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
    result: dict | None = None  # type: ignore[type-arg]
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ProjectBudget:
    project_id: str
    daily_token_limit: int | None = None
    monthly_cost_limit: float | None = None
    warn_threshold: float = 0.8
    current_status: str = "ok"
    updated_at: str = ""


@dataclass
class UsageEvent:
    id: str
    event_type: str
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
class FailureLog:
    id: str
    level: str  # error|warning|info
    category: str
    message: str
    context: str  # JSON blob
    created_at: str
    project_id: str | None = None
    entity_type: str | None = None  # tower|leader|ace|system
    entity_id: str | None = None
    stack_trace: str | None = None
    resolved: bool = False


@dataclass
class Notification:
    id: str
    level: str  # info|warning|error|budget
    message: str
    created_at: str
    project_id: str | None = None
    read: bool = False


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
