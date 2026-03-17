"""Tests for Leader context package builder."""

from __future__ import annotations

import json

import pytest

from atc.leader.context_package import build_context_package
from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    get_connection,
    run_migrations,
)


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestBuildContextPackage:
    async def test_basic_package(self, db) -> None:
        project = await create_project(
            db, "my-project", description="A test", repo_path="/tmp/repo"
        )

        pkg = await build_context_package(db, project.id, "Build feature X")

        assert pkg["goal"] == "Build feature X"
        assert pkg["project_id"] == project.id
        assert pkg["project_name"] == "my-project"
        assert pkg["repo_path"] == "/tmp/repo"
        assert pkg["description"] == "A test"
        assert pkg["context_entries"] == []

    async def test_package_with_github_repo(self, db) -> None:
        project = await create_project(db, "gh-proj", github_repo="org/repo")

        pkg = await build_context_package(db, project.id, "Deploy")

        assert pkg["github_repo"] == "org/repo"

    async def test_package_with_context_entries(self, db) -> None:
        project = await create_project(db, "ctx-proj")

        # Insert context entries
        cols = (
            "id, project_id, key, entry_type, value,"
            " position, updated_by, created_at, updated_at"
        )
        vals = "?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')"
        sql = f"INSERT INTO context_entries ({cols}) VALUES ({vals})"
        await db.execute(
            sql,
            ("e1", project.id, "tech-stack", "text", json.dumps("Python + FastAPI"), 0, "test"),
        )
        await db.execute(
            sql,
            ("e2", project.id, "conventions", "list", json.dumps(["ruff", "mypy"]), 1, "test"),
        )
        await db.commit()

        pkg = await build_context_package(db, project.id, "Follow conventions")

        assert len(pkg["context_entries"]) == 2
        assert pkg["context_entries"][0]["key"] == "tech-stack"
        assert pkg["context_entries"][0]["value"] == "Python + FastAPI"
        assert pkg["context_entries"][1]["key"] == "conventions"
        assert pkg["context_entries"][1]["value"] == ["ruff", "mypy"]

    async def test_package_project_not_found(self, db) -> None:
        with pytest.raises(ValueError, match="not found"):
            await build_context_package(db, "nonexistent-id", "Some goal")

    async def test_package_minimal_project(self, db) -> None:
        """Project with only required fields — optional fields are None."""
        project = await create_project(db, "minimal")

        pkg = await build_context_package(db, project.id, "Minimal goal")

        assert pkg["repo_path"] is None
        assert pkg["github_repo"] is None
        assert pkg["description"] is None
        assert pkg["context_entries"] == []
