"""Tests for context entries CRUD REST API router."""

from __future__ import annotations

import json

import pytest

from atc.state.db import (
    _SCHEMA_SQL,
    create_context_entry,
    create_project,
    create_session,
    get_connection,
    get_context_entry,
    run_migrations,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
async def project(db):
    return await create_project(db, "test-project")


@pytest.fixture
async def tower_session(db, project):
    return await create_session(db, project.id, "tower", "tower-1")


@pytest.fixture
async def leader_session(db, project):
    return await create_session(db, project.id, "manager", "leader-1")


@pytest.fixture
async def ace_session(db, project):
    return await create_session(db, project.id, "ace", "ace-1")


# ---------------------------------------------------------------------------
# Helper — we test the DB layer directly since the router is thin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGlobalContextEndpoints:
    async def test_create_global_entry(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "coding-standards", "text", json.dumps("PEP8"),
        )
        assert entry.scope == "global"
        assert entry.key == "coding-standards"
        assert entry.project_id is None
        assert entry.session_id is None
        assert entry.restricted is False

    async def test_create_global_entry_restricted(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "internal", "text", json.dumps("secret"),
            restricted=True,
        )
        assert entry.restricted is True

    async def test_create_duplicate_global_key_fails(self, db) -> None:
        await create_context_entry(
            db, "global", "k1", "text", json.dumps("v1"),
        )
        with pytest.raises(Exception):
            await create_context_entry(
                db, "global", "k1", "text", json.dumps("v2"),
            )


@pytest.mark.asyncio
class TestProjectContextEndpoints:
    async def test_create_project_entry(self, db, project) -> None:
        entry = await create_context_entry(
            db, "project", "architecture", "text", json.dumps("Clean arch"),
            project_id=project.id,
        )
        assert entry.scope == "project"
        assert entry.project_id == project.id

    async def test_create_duplicate_project_key_fails(self, db, project) -> None:
        await create_context_entry(
            db, "project", "k1", "text", json.dumps("v1"),
            project_id=project.id,
        )
        with pytest.raises(Exception):
            await create_context_entry(
                db, "project", "k1", "text", json.dumps("v2"),
                project_id=project.id,
            )

    async def test_same_key_different_projects(self, db) -> None:
        p1 = await create_project(db, "proj-1")
        p2 = await create_project(db, "proj-2")
        e1 = await create_context_entry(
            db, "project", "arch", "text", json.dumps("v1"),
            project_id=p1.id,
        )
        e2 = await create_context_entry(
            db, "project", "arch", "text", json.dumps("v2"),
            project_id=p2.id,
        )
        assert e1.id != e2.id


@pytest.mark.asyncio
class TestSessionContextEndpoints:
    async def test_create_tower_entry(self, db, tower_session) -> None:
        entry = await create_context_entry(
            db, "tower", "strategy", "text", json.dumps("Scale first"),
            session_id=tower_session.id,
        )
        assert entry.scope == "tower"
        assert entry.session_id == tower_session.id

    async def test_create_leader_entry(self, db, leader_session) -> None:
        entry = await create_context_entry(
            db, "leader", "plan", "text", json.dumps("Split into 3"),
            session_id=leader_session.id,
        )
        assert entry.scope == "leader"
        assert entry.session_id == leader_session.id

    async def test_create_ace_entry(self, db, ace_session) -> None:
        entry = await create_context_entry(
            db, "ace", "wip-notes", "text", json.dumps("Implementing auth"),
            session_id=ace_session.id,
        )
        assert entry.scope == "ace"
        assert entry.session_id == ace_session.id

    async def test_invalid_scope_rejected(self, db) -> None:
        with pytest.raises(ValueError, match="Invalid scope"):
            await create_context_entry(
                db, "invalid_scope", "k", "text", json.dumps("v"),
            )


@pytest.mark.asyncio
class TestGetUpdateDeleteEntry:
    async def test_get_entry(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        fetched = await get_context_entry(db, entry.id)
        assert fetched is not None
        assert fetched.id == entry.id
        assert fetched.key == "k"

    async def test_get_nonexistent_returns_none(self, db) -> None:
        result = await get_context_entry(db, "nonexistent-id")
        assert result is None

    async def test_update_value(self, db) -> None:
        from atc.state.db import update_context_entry

        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("old"),
        )
        updated = await update_context_entry(db, entry.id, value=json.dumps("new"))
        assert updated is not None
        assert json.loads(updated.value) == "new"
        assert updated.updated_at != entry.updated_at

    async def test_update_multiple_fields(self, db) -> None:
        from atc.state.db import update_context_entry

        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        updated = await update_context_entry(
            db, entry.id,
            value=json.dumps("v2"),
            entry_type="json",
            position=10,
            restricted=True,
            updated_by="admin",
        )
        assert updated is not None
        assert updated.entry_type == "json"
        assert updated.position == 10
        assert updated.restricted is True
        assert updated.updated_by == "admin"

    async def test_update_nonexistent_returns_none(self, db) -> None:
        from atc.state.db import update_context_entry

        result = await update_context_entry(db, "nope", value="x")
        assert result is None

    async def test_update_no_changes(self, db) -> None:
        from atc.state.db import update_context_entry

        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        result = await update_context_entry(db, entry.id)
        assert result is not None
        assert result.id == entry.id

    async def test_delete_entry(self, db) -> None:
        from atc.state.db import delete_context_entry

        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        assert await delete_context_entry(db, entry.id) is True
        assert await get_context_entry(db, entry.id) is None

    async def test_delete_nonexistent_returns_false(self, db) -> None:
        from atc.state.db import delete_context_entry

        assert await delete_context_entry(db, "nope") is False


@pytest.mark.asyncio
class TestListFiltering:
    async def test_filter_by_restricted(self, db) -> None:
        from atc.state.db import list_context_entries_by_scope

        await create_context_entry(
            db, "global", "public", "text", json.dumps("v"), restricted=False,
        )
        await create_context_entry(
            db, "global", "internal", "text", json.dumps("v"), restricted=True,
        )

        all_entries = await list_context_entries_by_scope(db, "global")
        assert len(all_entries) == 2

        public = [e for e in all_entries if not e.restricted]
        assert len(public) == 1
        assert public[0].key == "public"

        restricted = [e for e in all_entries if e.restricted]
        assert len(restricted) == 1
        assert restricted[0].key == "internal"

    async def test_filter_by_key(self, db) -> None:
        from atc.state.db import list_context_entries_by_scope

        await create_context_entry(
            db, "global", "alpha", "text", json.dumps("v1"),
        )
        await create_context_entry(
            db, "global", "beta", "text", json.dumps("v2"),
        )

        entries = await list_context_entries_by_scope(db, "global")
        filtered = [e for e in entries if e.key == "alpha"]
        assert len(filtered) == 1
        assert filtered[0].key == "alpha"

    async def test_filter_by_scope_on_project(self, db, project) -> None:
        from atc.state.db import list_context_entries_by_scope

        await create_context_entry(
            db, "project", "p1", "text", json.dumps("v"),
            project_id=project.id,
        )
        await create_context_entry(
            db, "global", "g1", "text", json.dumps("v"),
        )

        project_entries = await list_context_entries_by_scope(
            db, "project", project_id=project.id,
        )
        assert len(project_entries) == 1
        assert project_entries[0].key == "p1"

    async def test_list_session_entries(self, db, project, ace_session) -> None:
        from atc.state.db import list_context_entries_by_scope

        await create_context_entry(
            db, "ace", "notes", "text", json.dumps("v"),
            session_id=ace_session.id,
        )

        entries = await list_context_entries_by_scope(
            db, "ace", session_id=ace_session.id,
        )
        assert len(entries) == 1
        assert entries[0].key == "notes"

    async def test_position_ordering(self, db) -> None:
        from atc.state.db import list_context_entries_by_scope

        await create_context_entry(
            db, "global", "z-last", "text", json.dumps("v"), position=10,
        )
        await create_context_entry(
            db, "global", "a-first", "text", json.dumps("v"), position=0,
        )
        await create_context_entry(
            db, "global", "m-mid", "text", json.dumps("v"), position=5,
        )

        entries = await list_context_entries_by_scope(db, "global")
        assert [e.key for e in entries] == ["a-first", "m-mid", "z-last"]


@pytest.mark.asyncio
class TestEntryTypes:
    async def test_text_type(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("plain text"),
        )
        assert entry.entry_type == "text"

    async def test_json_type(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "json", json.dumps({"key": "value"}),
        )
        assert entry.entry_type == "json"
        assert json.loads(entry.value) == {"key": "value"}

    async def test_list_type(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "list", json.dumps(["a", "b", "c"]),
        )
        assert entry.entry_type == "list"
        assert json.loads(entry.value) == ["a", "b", "c"]

    async def test_link_type(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "link", json.dumps("https://example.com"),
        )
        assert entry.entry_type == "link"

    async def test_status_type(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "status", json.dumps("in_progress"),
        )
        assert entry.entry_type == "status"


@pytest.mark.asyncio
class TestCrossScope:
    async def test_same_key_global_and_project(self, db, project) -> None:
        e1 = await create_context_entry(
            db, "global", "standards", "text", json.dumps("global"),
        )
        e2 = await create_context_entry(
            db, "project", "standards", "text", json.dumps("project"),
            project_id=project.id,
        )
        assert e1.id != e2.id
        assert e1.scope == "global"
        assert e2.scope == "project"

    async def test_same_key_different_sessions(self, db, project) -> None:
        s1 = await create_session(db, project.id, "ace", "ace-1")
        s2 = await create_session(db, project.id, "ace", "ace-2")

        e1 = await create_context_entry(
            db, "ace", "notes", "text", json.dumps("v1"),
            session_id=s1.id,
        )
        e2 = await create_context_entry(
            db, "ace", "notes", "text", json.dumps("v2"),
            session_id=s2.id,
        )
        assert e1.id != e2.id

    async def test_all_five_scopes(self, db, project, tower_session, leader_session, ace_session) -> None:
        entries = []
        entries.append(await create_context_entry(
            db, "global", "g", "text", json.dumps("global"),
        ))
        entries.append(await create_context_entry(
            db, "project", "p", "text", json.dumps("project"),
            project_id=project.id,
        ))
        entries.append(await create_context_entry(
            db, "tower", "t", "text", json.dumps("tower"),
            session_id=tower_session.id,
        ))
        entries.append(await create_context_entry(
            db, "leader", "l", "text", json.dumps("leader"),
            session_id=leader_session.id,
        ))
        entries.append(await create_context_entry(
            db, "ace", "a", "text", json.dumps("ace"),
            session_id=ace_session.id,
        ))

        assert len(entries) == 5
        scopes = {e.scope for e in entries}
        assert scopes == {"global", "project", "tower", "leader", "ace"}


# ---------------------------------------------------------------------------
# HTTP-level endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def http_db():
    """In-memory DB for HTTP tests, accessible via app.state.db."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def http_app(http_db):
    """FastAPI app with db wired into state."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from atc.api.routers.context import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.db = http_db
    return app


@pytest.fixture
def client(http_app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=http_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
class TestHTTPGlobalEndpoints:
    async def test_list_global_empty(self, client) -> None:
        resp = await client.get("/api/context")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_and_list_global(self, client) -> None:
        resp = await client.post("/api/context", json={
            "key": "standards",
            "entry_type": "text",
            "value": json.dumps("PEP8"),
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["scope"] == "global"
        assert data["key"] == "standards"
        assert data["id"]

        resp = await client.get("/api/context")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_create_global_duplicate_key(self, client) -> None:
        await client.post("/api/context", json={
            "key": "k1", "value": json.dumps("v1"),
        })
        resp = await client.post("/api/context", json={
            "key": "k1", "value": json.dumps("v2"),
        })
        assert resp.status_code == 409

    async def test_create_global_restricted(self, client) -> None:
        resp = await client.post("/api/context", json={
            "key": "internal", "value": json.dumps("secret"), "restricted": True,
        })
        assert resp.status_code == 201
        assert resp.json()["restricted"] is True

    async def test_filter_by_restricted(self, client) -> None:
        await client.post("/api/context", json={
            "key": "public", "value": json.dumps("v"), "restricted": False,
        })
        await client.post("/api/context", json={
            "key": "private", "value": json.dumps("v"), "restricted": True,
        })
        resp = await client.get("/api/context", params={"restricted": "true"})
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "private"

        resp = await client.get("/api/context", params={"restricted": "false"})
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "public"

    async def test_filter_by_key(self, client) -> None:
        await client.post("/api/context", json={
            "key": "alpha", "value": json.dumps("v"),
        })
        await client.post("/api/context", json={
            "key": "beta", "value": json.dumps("v"),
        })
        resp = await client.get("/api/context", params={"key": "alpha"})
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "alpha"


@pytest.mark.asyncio
class TestHTTPEntryEndpoints:
    async def test_get_entry(self, client) -> None:
        create_resp = await client.post("/api/context", json={
            "key": "k", "value": json.dumps("v"),
        })
        entry_id = create_resp.json()["id"]

        resp = await client.get(f"/api/context/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["key"] == "k"

    async def test_get_nonexistent(self, client) -> None:
        resp = await client.get("/api/context/nonexistent")
        assert resp.status_code == 404

    async def test_update_entry(self, client) -> None:
        create_resp = await client.post("/api/context", json={
            "key": "k", "value": json.dumps("old"),
        })
        entry_id = create_resp.json()["id"]

        resp = await client.put(f"/api/context/{entry_id}", json={
            "value": json.dumps("new"),
        })
        assert resp.status_code == 200
        assert json.loads(resp.json()["value"]) == "new"

    async def test_update_multiple_fields(self, client) -> None:
        create_resp = await client.post("/api/context", json={
            "key": "k", "value": json.dumps("v"),
        })
        entry_id = create_resp.json()["id"]

        resp = await client.put(f"/api/context/{entry_id}", json={
            "value": json.dumps("v2"),
            "entry_type": "json",
            "position": 10,
            "restricted": True,
            "updated_by": "admin",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry_type"] == "json"
        assert data["position"] == 10
        assert data["restricted"] is True
        assert data["updated_by"] == "admin"

    async def test_update_nonexistent(self, client) -> None:
        resp = await client.put("/api/context/nonexistent", json={
            "value": json.dumps("v"),
        })
        assert resp.status_code == 404

    async def test_delete_entry(self, client) -> None:
        create_resp = await client.post("/api/context", json={
            "key": "k", "value": json.dumps("v"),
        })
        entry_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/context/{entry_id}")
        assert resp.status_code == 204

        resp = await client.get(f"/api/context/{entry_id}")
        assert resp.status_code == 404

    async def test_delete_nonexistent(self, client) -> None:
        resp = await client.delete("/api/context/nonexistent")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestHTTPProjectEndpoints:
    async def test_project_not_found(self, client) -> None:
        resp = await client.get("/api/projects/nonexistent/context")
        assert resp.status_code == 404

    async def test_create_project_entry(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        resp = await client.post(f"/api/projects/{project.id}/context", json={
            "key": "arch", "value": json.dumps("Clean"),
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["scope"] == "project"
        assert data["project_id"] == project.id

    async def test_list_project_entries(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "k1", "value": json.dumps("v1"),
        })
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "k2", "value": json.dumps("v2"),
        })

        resp = await client.get(f"/api/projects/{project.id}/context")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_create_project_not_found(self, client) -> None:
        resp = await client.post("/api/projects/nonexistent/context", json={
            "key": "k", "value": json.dumps("v"),
        })
        assert resp.status_code == 404

    async def test_invalid_scope_filter(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        resp = await client.get(
            f"/api/projects/{project.id}/context",
            params={"scope": "invalid"},
        )
        assert resp.status_code == 422

    async def test_create_project_duplicate_key(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "k1", "value": json.dumps("v1"),
        })
        resp = await client.post(f"/api/projects/{project.id}/context", json={
            "key": "k1", "value": json.dumps("v2"),
        })
        assert resp.status_code == 409


@pytest.mark.asyncio
class TestHTTPSessionEndpoints:
    async def test_session_not_found(self, client) -> None:
        resp = await client.get("/api/sessions/nonexistent/context")
        assert resp.status_code == 404

    async def test_create_and_list_session_entry(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "ace", "ace-1")

        resp = await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace",
            "key": "wip",
            "value": json.dumps("notes"),
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["scope"] == "ace"
        assert data["session_id"] == session.id

        resp = await client.get(f"/api/sessions/{session.id}/context")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_create_session_invalid_scope(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "ace", "ace-1")

        resp = await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "global",
            "key": "k",
            "value": json.dumps("v"),
        })
        assert resp.status_code == 422

    async def test_create_session_not_found(self, client) -> None:
        resp = await client.post("/api/sessions/nonexistent/context", json={
            "scope": "ace",
            "key": "k",
            "value": json.dumps("v"),
        })
        assert resp.status_code == 404

    async def test_tower_session_scope_mapping(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "tower", "tower-1")

        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "tower",
            "key": "strategy",
            "value": json.dumps("v"),
        })

        resp = await client.get(f"/api/sessions/{session.id}/context")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["scope"] == "tower"

    async def test_leader_session_scope_mapping(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "manager", "leader-1")

        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "leader",
            "key": "plan",
            "value": json.dumps("v"),
        })

        resp = await client.get(f"/api/sessions/{session.id}/context")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["scope"] == "leader"

    async def test_filter_session_by_restricted(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "ace", "ace-1")

        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "pub", "value": json.dumps("v"), "restricted": False,
        })
        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "priv", "value": json.dumps("v"), "restricted": True,
        })

        resp = await client.get(
            f"/api/sessions/{session.id}/context",
            params={"restricted": "true"},
        )
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "priv"

    async def test_filter_session_by_key(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "ace", "ace-1")

        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "alpha", "value": json.dumps("v"),
        })
        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "beta", "value": json.dumps("v"),
        })

        resp = await client.get(
            f"/api/sessions/{session.id}/context",
            params={"key": "alpha"},
        )
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "alpha"

    async def test_session_scope_override(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "ace", "ace-1")

        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "k", "value": json.dumps("v"),
        })

        # Override scope to leader — should return empty since entry is ace-scoped
        resp = await client.get(
            f"/api/sessions/{session.id}/context",
            params={"scope": "leader"},
        )
        assert resp.json() == []

    async def test_project_filter_by_scope(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "k", "value": json.dumps("v"),
        })

        resp = await client.get(
            f"/api/projects/{project.id}/context",
            params={"scope": "project"},
        )
        assert len(resp.json()) == 1

    async def test_project_filter_by_restricted(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "pub", "value": json.dumps("v"), "restricted": False,
        })
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "priv", "value": json.dumps("v"), "restricted": True,
        })

        resp = await client.get(
            f"/api/projects/{project.id}/context",
            params={"restricted": "true"},
        )
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "priv"

    async def test_project_filter_by_key(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "alpha", "value": json.dumps("v"),
        })
        await client.post(f"/api/projects/{project.id}/context", json={
            "key": "beta", "value": json.dumps("v"),
        })

        resp = await client.get(
            f"/api/projects/{project.id}/context",
            params={"key": "alpha"},
        )
        assert len(resp.json()) == 1
        assert resp.json()[0]["key"] == "alpha"

    async def test_create_session_duplicate_key(self, client, http_db) -> None:
        project = await create_project(http_db, "test-proj")
        session = await create_session(http_db, project.id, "ace", "ace-1")

        await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "k1", "value": json.dumps("v1"),
        })
        resp = await client.post(f"/api/sessions/{session.id}/context", json={
            "scope": "ace", "key": "k1", "value": json.dumps("v2"),
        })
        assert resp.status_code == 409


@pytest.mark.asyncio
class TestRouterImport:
    def test_router_importable(self) -> None:
        from atc.api.routers.context import router
        assert router is not None

    def test_router_has_expected_routes(self) -> None:
        from atc.api.routers.context import router
        paths = {r.path for r in router.routes}
        assert "/context" in paths
        assert "/projects/{project_id}/context" in paths
        assert "/sessions/{session_id}/context" in paths
        assert "/context/{entry_id}" in paths

    def test_pydantic_models_importable(self) -> None:
        from atc.api.routers.context import (
            ContextEntryResponse,
            CreateContextEntryRequest,
            CreateSessionContextEntryRequest,
            UpdateContextEntryRequest,
        )
        assert ContextEntryResponse is not None
        assert CreateContextEntryRequest is not None
        assert CreateSessionContextEntryRequest is not None
        assert UpdateContextEntryRequest is not None


@pytest.mark.asyncio
class TestRouterRegistration:
    def test_context_router_registered_in_app(self) -> None:
        from atc.api.app import create_app
        from atc.config import Settings

        settings = Settings(database={"path": ":memory:"})  # type: ignore[arg-type]
        app = create_app(settings)

        paths = set()
        for route in app.routes:
            if hasattr(route, "path"):
                paths.add(route.path)

        assert "/api/context" in paths
        assert "/api/context/{entry_id}" in paths
        assert "/api/projects/{project_id}/context" in paths
        assert "/api/sessions/{session_id}/context" in paths
