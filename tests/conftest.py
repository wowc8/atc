"""Shared test fixtures: in-memory DB, mock tmux, test client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings
from atc.state.db import ConnectionFactory
from atc.state.migrations import run_migrations


@pytest.fixture
def settings() -> Settings:
    """Test settings with in-memory database."""
    return Settings(database={"path": ":memory:"})  # type: ignore[arg-type]


@pytest.fixture
def db_factory() -> ConnectionFactory:
    """In-memory ConnectionFactory with migrations applied."""
    factory = ConnectionFactory(":memory:")
    run_migrations(factory)
    return factory


@pytest.fixture
def app(settings: Settings) -> TestClient:
    """FastAPI test client."""
    return TestClient(create_app(settings))
