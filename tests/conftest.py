"""Shared test fixtures: in-memory DB, mock tmux, test client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Test settings with in-memory database."""
    return Settings(database={"path": ":memory:"})  # type: ignore[arg-type]


@pytest.fixture
def app(settings: Settings) -> TestClient:
    """FastAPI test client."""
    return TestClient(create_app(settings))
