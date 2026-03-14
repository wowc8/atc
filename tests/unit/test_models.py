"""Tests for dataclass models."""

from __future__ import annotations

from atc.state.models import (
    Config,
    FailureLog,
    GitHubPR,
    Leader,
    Notification,
    Project,
    ProjectBudget,
    Session,
    Task,
    TowerMemory,
    UsageEvent,
)


class TestProject:
    def test_required_fields(self) -> None:
        p = Project(id="p1", name="Test", status="active")
        assert p.id == "p1"
        assert p.name == "Test"
        assert p.status == "active"

    def test_optional_defaults(self) -> None:
        p = Project(id="p1", name="Test", status="active")
        assert p.description is None
        assert p.repo_path is None
        assert p.github_repo is None
        assert p.created_at == ""
        assert p.updated_at == ""

    def test_all_fields(self) -> None:
        p = Project(
            id="p1",
            name="Test",
            status="paused",
            description="A project",
            repo_path="/tmp/repo",
            github_repo="owner/repo",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert p.github_repo == "owner/repo"


class TestLeader:
    def test_context_json_serialization(self) -> None:
        leader = Leader(
            id="l1",
            project_id="p1",
            status="idle",
            context={"key": "value", "num": 42},
        )
        json_str = leader.context_json()
        assert json_str is not None
        assert '"key"' in json_str
        assert '"num": 42' in json_str

    def test_context_json_none(self) -> None:
        leader = Leader(id="l1", project_id="p1", status="idle")
        assert leader.context_json() is None

    def test_context_from_json(self) -> None:
        result = Leader.context_from_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_context_from_json_none(self) -> None:
        assert Leader.context_from_json(None) is None

    def test_defaults(self) -> None:
        leader = Leader(id="l1", project_id="p1", status="idle")
        assert leader.session_id is None
        assert leader.context is None
        assert leader.goal is None


class TestSession:
    def test_required_fields(self) -> None:
        s = Session(
            id="s1",
            project_id="p1",
            session_type="ace",
            name="ace-1",
            status="idle",
        )
        assert s.session_type == "ace"
        assert s.alternate_on is False
        assert s.auto_accept is False

    def test_optional_fields(self) -> None:
        s = Session(
            id="s1",
            project_id="p1",
            session_type="manager",
            name="leader-1",
            status="working",
            task_id="t1",
            host="remote",
            tmux_session="sess",
            tmux_pane="%1",
            alternate_on=True,
            auto_accept=True,
        )
        assert s.host == "remote"
        assert s.alternate_on is True


class TestTask:
    def test_result_json_serialization(self) -> None:
        task = Task(
            id="t1",
            project_id="p1",
            leader_id="l1",
            title="Do stuff",
            status="done",
            result={"summary": "done", "files": 3},
        )
        json_str = task.result_json()
        assert json_str is not None
        assert '"summary"' in json_str

    def test_result_json_none(self) -> None:
        task = Task(
            id="t1", project_id="p1", leader_id="l1", title="X", status="pending"
        )
        assert task.result_json() is None

    def test_result_from_json(self) -> None:
        result = Task.result_from_json('{"files": 3}')
        assert result == {"files": 3}

    def test_result_from_json_none(self) -> None:
        assert Task.result_from_json(None) is None

    def test_defaults(self) -> None:
        task = Task(
            id="t1", project_id="p1", leader_id="l1", title="X", status="pending"
        )
        assert task.parent_task_id is None
        assert task.description is None
        assert task.priority == 0
        assert task.assigned_to is None


class TestProjectBudget:
    def test_defaults(self) -> None:
        b = ProjectBudget(project_id="p1")
        assert b.daily_token_limit is None
        assert b.monthly_cost_limit is None
        assert b.warn_threshold == 0.8
        assert b.current_status == "ok"


class TestUsageEvent:
    def test_required_and_defaults(self) -> None:
        e = UsageEvent(id="u1", event_type="ai_tokens", recorded_at="2026-01-01")
        assert e.project_id is None
        assert e.model is None
        assert e.cost_usd is None


class TestGitHubPR:
    def test_fields(self) -> None:
        pr = GitHubPR(id="owner/repo#1", project_id="p1", number=1, title="Fix bug")
        assert pr.number == 1
        assert pr.status is None
        assert pr.ci_status is None


class TestNotification:
    def test_defaults(self) -> None:
        n = Notification(id="n1", level="info", message="Hello")
        assert n.project_id is None
        assert n.read is False


class TestConfig:
    def test_fields(self) -> None:
        c = Config(key="theme", value="dark")
        assert c.key == "theme"
        assert c.updated_at == ""


class TestTowerMemory:
    def test_fields(self) -> None:
        tm = TowerMemory(id="tm1", key="pattern", value='{"x": 1}')
        assert tm.project_id is None
        assert tm.created_at == ""


class TestFailureLog:
    def test_defaults(self) -> None:
        fl = FailureLog(
            id="f1",
            level="error",
            category="creation_failure",
            message="Failed",
            context="{}",
        )
        assert fl.project_id is None
        assert fl.entity_type is None
        assert fl.stack_trace is None
        assert fl.resolved is False
