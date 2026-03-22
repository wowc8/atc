"""Tests for end-to-end agent wiring — deploy.py integration with session lifecycle.

Verifies that:
  - Tower → Leader flow deploys manager config before spawning
  - Leader → Ace flow deploys ace config before spawning
  - Config files contain correct task/goal information
  - tmux panes receive the correct launch command and working directory
  - Deployed files are cleaned up on task completion/failure
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from atc.agents.deploy import AceDeploySpec, ManagerDeploySpec, deploy_ace_files
from atc.agents.factory import get_launch_command
from atc.core.events import EventBus
from atc.leader.leader import _build_manager_deploy_spec
from atc.leader.orchestrator import LeaderOrchestrator
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    create_task_graph,
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


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


# ---------------------------------------------------------------------------
# Manager deploy spec builder
# ---------------------------------------------------------------------------


class TestBuildManagerDeploySpec:
    def test_basic_spec(self) -> None:
        spec = _build_manager_deploy_spec(
            leader_id="leader-1",
            project_name="Phoenix",
            goal="Ship the MVP",
            repo_path="/home/user/phoenix",
            github_repo="acme/phoenix",
        )
        assert isinstance(spec, ManagerDeploySpec)
        assert spec.leader_id == "leader-1"
        assert spec.project_name == "Phoenix"
        assert spec.goal == "Ship the MVP"
        assert spec.repo_path == "/home/user/phoenix"
        assert spec.github_repo == "acme/phoenix"

    def test_with_context_entries(self) -> None:
        entries = [{"key": "stack", "value": "Python"}]
        spec = _build_manager_deploy_spec(
            leader_id="l-1",
            project_name="Test",
            goal="Test goal",
            context_entries=entries,
        )
        assert spec.context_entries == entries

    def test_defaults_to_empty_context(self) -> None:
        spec = _build_manager_deploy_spec(
            leader_id="l-1",
            project_name="Test",
            goal="Test goal",
        )
        assert spec.context_entries == []


# ---------------------------------------------------------------------------
# Tower → Leader: deploy + launch wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTowerToLeaderWiring:
    """Verify that Tower → Leader flow deploys config and launches claude."""

    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    @patch("atc.leader.leader.deploy_manager_files")
    async def test_start_leader_deploys_config(
        self,
        mock_deploy: AsyncMock,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.agents.deploy import DeployedFiles

        mock_deploy.return_value = DeployedFiles(root=Path("/tmp/atc-agents/leader-1"), files=[])

        project = await create_project(db, "test-proj", repo_path="/tmp/repo")
        await create_leader(db, project.id)

        from atc.leader.leader import start_leader

        context_package = {
            "goal": "Build feature X",
            "project_name": "test-proj",
            "repo_path": "/tmp/repo",
            "github_repo": None,
            "context_entries": [{"key": "stack", "value": "Python"}],
        }

        await start_leader(
            db,
            project.id,
            goal="Build feature X",
            event_bus=event_bus,
            context_package=context_package,
        )

        # Verify deploy_manager_files was called
        mock_deploy.assert_called_once()
        spec = mock_deploy.call_args[0][0]
        assert isinstance(spec, ManagerDeploySpec)
        assert spec.goal == "Build feature X"
        assert spec.project_name == "test-proj"

    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    @patch("atc.leader.leader.deploy_manager_files")
    async def test_start_leader_launches_claude(
        self,
        mock_deploy: AsyncMock,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.agents.deploy import DeployedFiles

        mock_deploy.return_value = DeployedFiles(root=Path("/tmp/atc-agents/leader-1"), files=[])

        project = await create_project(db, "test-proj", repo_path="/tmp/repo")
        await create_leader(db, project.id)

        from atc.leader.leader import start_leader

        await start_leader(db, project.id, goal="Build it", event_bus=event_bus)

        # Verify tmux pane was spawned with claude command and working_dir
        mock_spawn.assert_called_once()
        args, kwargs = mock_spawn.call_args
        assert args[0] == "atc"  # tmux session name
        assert args[1] == get_launch_command("claude_code")
        # Leader now always uses the deploy staging directory so Claude Code
        # finds the deployed CLAUDE.md (Leader role/goal instructions).
        assert kwargs.get("working_dir") == "/tmp/atc-agents/leader-1"

    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    @patch("atc.leader.leader.deploy_manager_files")
    async def test_start_leader_uses_deploy_root_when_no_repo(
        self,
        mock_deploy: AsyncMock,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.agents.deploy import DeployedFiles

        mock_deploy.return_value = DeployedFiles(root=Path("/tmp/atc-agents/leader-1"), files=[])

        project = await create_project(db, "test-proj")  # no repo_path
        await create_leader(db, project.id)

        from atc.leader.leader import start_leader

        await start_leader(db, project.id, goal="Test", event_bus=event_bus)

        # Without repo_path, should fall back to deployed root
        _, kwargs = mock_spawn.call_args
        assert kwargs.get("working_dir") == "/tmp/atc-agents/leader-1"

    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    @patch("atc.leader.leader.deploy_manager_files")
    async def test_start_leader_uses_project_agent_provider(
        self,
        mock_deploy: AsyncMock,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        """Leader must use project.agent_provider to pick the launch command."""
        from atc.agents.deploy import DeployedFiles

        mock_deploy.return_value = DeployedFiles(root=Path("/tmp/atc-agents/leader-1"), files=[])

        project = await create_project(db, "test-proj", repo_path="/tmp/repo")
        # Set project to use opencode
        await db.execute(
            "UPDATE projects SET agent_provider = ? WHERE id = ?",
            ("opencode", project.id),
        )
        await db.commit()
        await create_leader(db, project.id)

        from atc.leader.leader import start_leader

        await start_leader(db, project.id, goal="Build it", event_bus=event_bus)

        mock_spawn.assert_called_once()
        args, _ = mock_spawn.call_args
        assert args[1] == get_launch_command("opencode")
        assert args[1] == "opencode"


# ---------------------------------------------------------------------------
# Leader → Ace: deploy + launch wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLeaderToAceWiring:
    """Verify that Leader → Ace flow deploys config and launches claude."""

    @patch("atc.leader.orchestrator.create_ace", new_callable=AsyncMock, return_value="ace-1")
    @patch("atc.leader.orchestrator.deploy_ace_files")
    async def test_spawn_ace_deploys_config(
        self,
        mock_deploy: AsyncMock,
        mock_create: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.agents.deploy import DeployedFiles

        mock_deploy.return_value = DeployedFiles(root=Path("/tmp/atc-agents/ace-preview"), files=[])

        project = await create_project(db, "test-proj", repo_path="/tmp/repo")
        leader = await create_leader(db, project.id, goal="Build auth")

        orchestrator = LeaderOrchestrator(
            project_id=project.id,
            leader_id=leader.id,
            conn=db,
            event_bus=event_bus,
        )

        await create_task_graph(
            db,
            project.id,
            "Login page",
            description="Build OAuth login flow",
        )

        assignments = await orchestrator.spawn_aces_for_ready_tasks()

        assert len(assignments) == 1
        # Verify deploy was called
        mock_deploy.assert_called_once()
        spec = mock_deploy.call_args[0][0]
        assert isinstance(spec, AceDeploySpec)
        assert spec.task_title == "Login page"
        assert spec.task_description == "Build OAuth login flow"
        assert spec.project_name == "test-proj"
        assert spec.repo_path == "/tmp/repo"

    @patch("atc.leader.orchestrator.create_ace", new_callable=AsyncMock, return_value="ace-1")
    @patch("atc.leader.orchestrator.deploy_ace_files")
    async def test_spawn_ace_passes_working_dir_and_command(
        self,
        mock_deploy: AsyncMock,
        mock_create: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.agents.deploy import DeployedFiles

        mock_deploy.return_value = DeployedFiles(root=Path("/tmp/atc-agents/ace-preview"), files=[])

        project = await create_project(db, "test-proj", repo_path="/tmp/repo")
        leader = await create_leader(db, project.id, goal="Test")

        orchestrator = LeaderOrchestrator(
            project_id=project.id,
            leader_id=leader.id,
            conn=db,
            event_bus=event_bus,
        )

        await create_task_graph(db, project.id, "Task A")
        await orchestrator.spawn_aces_for_ready_tasks()

        # Verify create_ace was called with working_dir and launch_command
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["working_dir"] == "/tmp/repo"
        assert kwargs["launch_command"] == "claude --dangerously-skip-permissions"

    @patch("atc.leader.orchestrator.create_ace", new_callable=AsyncMock, return_value="ace-1")
    @patch("atc.leader.orchestrator.deploy_ace_files")
    async def test_assignment_stores_deployed_root(
        self,
        mock_deploy: AsyncMock,
        mock_create: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.agents.deploy import DeployedFiles

        deploy_root = Path("/tmp/atc-agents/ace-preview")
        mock_deploy.return_value = DeployedFiles(root=deploy_root, files=[])

        project = await create_project(db, "test-proj")
        leader = await create_leader(db, project.id, goal="Test")

        orchestrator = LeaderOrchestrator(
            project_id=project.id,
            leader_id=leader.id,
            conn=db,
            event_bus=event_bus,
        )

        tg = await create_task_graph(db, project.id, "Task A")
        await orchestrator.spawn_aces_for_ready_tasks()

        assignment = orchestrator.assignments[tg.id]
        assert assignment.deployed_root == deploy_root


# ---------------------------------------------------------------------------
# Cleanup of deployed files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDeployedFileCleanup:
    @patch("atc.leader.orchestrator.cleanup_deployed_files")
    @patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
    async def test_mark_done_cleans_up_deployed_files(
        self,
        mock_destroy: AsyncMock,
        mock_cleanup: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.leader.orchestrator import AceAssignment

        project = await create_project(db, "test-proj")
        leader = await create_leader(db, project.id, goal="Test")

        orchestrator = LeaderOrchestrator(
            project_id=project.id,
            leader_id=leader.id,
            conn=db,
            event_bus=event_bus,
        )

        tg = await create_task_graph(db, project.id, "Task A")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        deploy_root = Path("/tmp/atc-agents/ace-123")
        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
            deployed_root=deploy_root,
        )

        await orchestrator.mark_task_done(tg.id)

        mock_cleanup.assert_called_once_with(deploy_root)

    @patch("atc.leader.orchestrator.cleanup_deployed_files")
    @patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
    async def test_mark_failed_cleans_up_deployed_files(
        self,
        mock_destroy: AsyncMock,
        mock_cleanup: AsyncMock,
        db,
        event_bus: EventBus,
    ) -> None:
        from atc.leader.orchestrator import AceAssignment

        project = await create_project(db, "test-proj")
        leader = await create_leader(db, project.id, goal="Test")

        orchestrator = LeaderOrchestrator(
            project_id=project.id,
            leader_id=leader.id,
            conn=db,
            event_bus=event_bus,
        )

        tg = await create_task_graph(db, project.id, "Task A")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        deploy_root = Path("/tmp/atc-agents/ace-456")
        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
            deployed_root=deploy_root,
        )

        await orchestrator.mark_task_failed(tg.id, reason="test failure")

        mock_cleanup.assert_called_once_with(deploy_root)


# ---------------------------------------------------------------------------
# _spawn_pane working_dir parameter
# ---------------------------------------------------------------------------


class TestSpawnPaneWorkingDir:
    """Verify that _spawn_pane passes -c working_dir to tmux."""

    @pytest.mark.asyncio
    @patch("atc.session.ace._tmux_run", new_callable=AsyncMock, return_value="%5")
    async def test_spawn_pane_with_working_dir(self, mock_tmux: AsyncMock) -> None:
        from atc.session.ace import _spawn_pane

        pane_id = await _spawn_pane("atc", "claude", working_dir="/tmp/repo")
        assert pane_id == "%5"

        args = mock_tmux.call_args[0]
        # Should include -c /tmp/repo before the command
        assert "-c" in args
        idx = args.index("-c")
        assert args[idx + 1] == "/tmp/repo"

    @pytest.mark.asyncio
    @patch("atc.session.ace._tmux_run", new_callable=AsyncMock, return_value="%6")
    async def test_spawn_pane_without_working_dir(self, mock_tmux: AsyncMock) -> None:
        from atc.session.ace import _spawn_pane

        pane_id = await _spawn_pane("atc", "claude")
        assert pane_id == "%6"

        args = mock_tmux.call_args[0]
        assert "-c" not in args


# ---------------------------------------------------------------------------
# Full deploy.py content verification
# ---------------------------------------------------------------------------


class TestDeployedContentIntegrity:
    """Verify that deployed config files contain the right information."""

    def test_ace_deploy_has_hooks_pointing_to_api(self, tmp_path: Path) -> None:
        spec = AceDeploySpec(
            session_id="ace-test",
            project_name="TestProj",
            task_title="Build login",
            task_description="Implement OAuth flow",
        )
        deployed = deploy_ace_files(spec, staging_root=tmp_path)

        # CLAUDE.md should mention the task
        claude_md = deployed.claude_md_path.read_text()
        assert "Build login" in claude_md
        assert "OAuth flow" in claude_md
        assert "atc ace status" in claude_md

        # Settings should have hooks
        settings = json.loads(deployed.settings_path.read_text())
        assert "PostToolUse" in settings["hooks"]
        assert "Stop" in settings["hooks"]

        # Hook scripts should report to the API
        post_hook = (deployed.root / ".claude" / "hooks" / "PostToolUse.sh").read_text()
        assert "ace-test" in post_hook
        assert "working" in post_hook

        stop_hook = (deployed.root / ".claude" / "hooks" / "Stop.sh").read_text()
        assert "waiting" in stop_hook
