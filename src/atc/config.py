"""Pydantic settings loader — reads config.yaml + config.local.yaml overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8420
    reload: bool = False


class DatabaseConfig(BaseModel):
    path: str = "atc.db"
    wal_mode: bool = True


class TowerConfig(BaseModel):
    enabled: bool = True
    auto_start: bool = False


class ResourceMonitorConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = 5


class GitHubConfig(BaseModel):
    poll_interval_seconds: int = 60


class BudgetConfig(BaseModel):
    check_interval_seconds: int = 30


class CostTrackerConfig(BaseModel):
    poll_interval_seconds: int = 30


class LoggingConfig(BaseModel):
    level: str = "INFO"


class AgentProviderConfig(BaseModel):
    """Configuration for the agent provider abstraction layer."""

    default: str = "claude_code"
    opencode_url: str = "http://localhost:4096"
    opencode_username: str | None = None
    opencode_password: str | None = None
    tmux_session: str = "atc"
    claude_command: str = "claude"


class Settings(BaseSettings):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    tower: TowerConfig = TowerConfig()
    resource_monitor: ResourceMonitorConfig = ResourceMonitorConfig()
    github: GitHubConfig = GitHubConfig()
    budget: BudgetConfig = BudgetConfig()
    cost_tracker: CostTrackerConfig = CostTrackerConfig()
    logging: LoggingConfig = LoggingConfig()
    agent_provider: AgentProviderConfig = AgentProviderConfig()


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def load_settings(config_dir: Path | None = None) -> Settings:
    """Load settings from config.yaml with config.local.yaml overrides."""
    base_dir = config_dir or Path.cwd()
    base = _load_yaml(base_dir / "config.yaml")
    local = _load_yaml(base_dir / "config.local.yaml")

    # Merge local overrides into base (shallow per top-level key)
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value

    return Settings(**base)
