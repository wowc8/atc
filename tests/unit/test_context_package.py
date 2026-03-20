"""Tests for Leader context package builder."""

from __future__ import annotations

import json

import pytest

from atc.leader.context_package import build_context_package
from atc.state.db import (
    _SCHEMA_SQL,
    create_context_entry,
    create_project,
    create_session,
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

        # Create context entries using the new CRUD helper
        await create_context_entry(
            db,
            "project",
            "tech-stack",
            "text",
            json.dumps("Python + FastAPI"),
            project_id=project.id,
            position=0,
            updated_by="test",
        )
        await create_context_entry(
            db,
            "project",
            "conventions",
            "list",
            json.dumps(["ruff", "mypy"]),
            project_id=project.id,
            position=1,
            updated_by="test",
        )

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

    async def test_leader_sees_global_and_project_entries(self, db) -> None:
        """Leader scope should see global + project entries."""
        project = await create_project(db, "proj")

        await create_context_entry(
            db, "global", "coding-standards", "text", json.dumps("Use ruff"),
        )
        await create_context_entry(
            db, "project", "arch-notes", "text", json.dumps("Monorepo"),
            project_id=project.id,
        )

        pkg = await build_context_package(db, project.id, "A goal", scope="leader")

        keys = [e["key"] for e in pkg["context_entries"]]
        assert "coding-standards" in keys
        assert "arch-notes" in keys

    async def test_ace_sees_leader_parent_entries(self, db) -> None:
        """Ace scope should see global + project + leader parent + own entries."""
        project = await create_project(db, "proj")
        leader_session = await create_session(db, project.id, "manager", "leader-1")
        ace_session = await create_session(db, project.id, "ace", "ace-1")

        await create_context_entry(
            db, "global", "standards", "text", json.dumps("PEP8"),
        )
        await create_context_entry(
            db, "project", "design", "text", json.dumps("Clean arch"),
            project_id=project.id,
        )
        await create_context_entry(
            db, "leader", "decomposition", "text", json.dumps("Split into 3"),
            session_id=leader_session.id,
        )
        await create_context_entry(
            db, "ace", "wip-notes", "text", json.dumps("Working on X"),
            session_id=ace_session.id,
        )

        pkg = await build_context_package(
            db, project.id, "Do task",
            scope="ace",
            session_id=ace_session.id,
            parent_session_id=leader_session.id,
        )

        keys = [e["key"] for e in pkg["context_entries"]]
        assert "standards" in keys
        assert "design" in keys
        assert "decomposition" in keys
        assert "wip-notes" in keys
