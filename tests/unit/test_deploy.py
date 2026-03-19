"""Unit tests for agent deployment SOT (src/atc/agents/deploy.py)."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from atc.agents.deploy import (
    AceDeploySpec,
    DeployedFiles,
    ManagerDeploySpec,
    TowerDeploySpec,
    cleanup_deployed_files,
    deploy_ace_files,
    deploy_manager_files,
    deploy_tower_files,
)


@pytest.fixture
def staging_root(tmp_path: Path) -> Path:
    """Provide a temporary staging root for each test."""
    return tmp_path / "staging"


@pytest.fixture
def ace_spec() -> AceDeploySpec:
    return AceDeploySpec(
        session_id="ace-001",
        project_name="Phoenix",
        task_title="Implement login page",
        task_description="Build OAuth login with GitHub provider.",
        repo_path="/home/user/phoenix",
        github_repo="acme/phoenix",
        api_base_url="http://127.0.0.1:8420",
        model="opus",
        constraints=["No direct SQL queries", "Must use TypeScript"],
        extra_context="The auth module lives in src/auth/.",
    )


@pytest.fixture
def manager_spec() -> ManagerDeploySpec:
    return ManagerDeploySpec(
        leader_id="leader-bravo",
        project_name="Phoenix",
        goal="Ship the MVP by end of sprint",
        repo_path="/home/user/phoenix",
        github_repo="acme/phoenix",
        api_base_url="http://127.0.0.1:8420",
        model="opus",
        constraints=["Stay under budget", "No force-pushes"],
        context_entries=[
            {"key": "tech-stack", "value": "React + FastAPI"},
            {"key": "deploy-target", "value": {"cloud": "aws", "region": "us-east-1"}},
        ],
        initial_tasks=["Set up CI", "Scaffold frontend", "Design DB schema"],
        budget_ceiling_usd=50.0,
    )


# ---------------------------------------------------------------------------
# Ace deployment
# ---------------------------------------------------------------------------


class TestDeployAceFiles:
    def test_returns_deployed_files(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        assert isinstance(result, DeployedFiles)
        assert result.root == staging_root / "ace-001"

    def test_creates_claude_md(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        assert result.claude_md_path.exists()
        content = result.claude_md_path.read_text()
        assert "Phoenix — Ace Session" in content
        assert "ace-001" in content
        assert "Implement login page" in content
        assert "OAuth login" in content

    def test_claude_md_includes_role_identity(
        self, ace_spec: AceDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "## Role" in content
        assert "Ace" in content
        assert "expert developer" in content
        assert "Create a PR" in content
        assert "Run tests" in content

    def test_claude_md_includes_constraints(
        self, ace_spec: AceDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "No direct SQL queries" in content
        assert "Must use TypeScript" in content

    def test_claude_md_includes_github_repo(
        self, ace_spec: AceDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "acme/phoenix" in content

    def test_claude_md_includes_reporting_instructions(
        self, ace_spec: AceDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "atc ace status" in content
        assert "atc ace done" in content
        assert "atc ace blocked" in content

    def test_claude_md_includes_extra_context(
        self, ace_spec: AceDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "auth module" in content

    def test_creates_settings_json(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        assert result.settings_path.exists()
        settings = json.loads(result.settings_path.read_text())
        assert settings["model"] == "opus"
        assert settings["autoMemoryEnabled"] is False

    def test_settings_has_permissions(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        settings = json.loads(result.settings_path.read_text())
        allowed = settings["permissions"]["allow"]
        assert "Bash(git *)" in allowed
        assert "Bash(atc ace *)" in allowed

    def test_settings_has_hooks(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        settings = json.loads(result.settings_path.read_text())
        assert "PostToolUse" in settings["hooks"]
        assert "Stop" in settings["hooks"]
        assert "Notification" in settings["hooks"]

    def test_hooks_use_correct_format(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        """Hooks must use matcher + hooks array format, not bare command objects."""
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        settings = json.loads(result.settings_path.read_text())
        for event_name, entries in settings["hooks"].items():
            assert isinstance(entries, list), f"{event_name} hooks must be a list"
            for entry in entries:
                assert "matcher" in entry, f"{event_name} entry missing 'matcher'"
                assert "hooks" in entry, f"{event_name} entry missing 'hooks'"
                assert isinstance(entry["hooks"], list), f"{event_name} 'hooks' must be a list"
                for hook in entry["hooks"]:
                    assert "type" in hook
                    assert "command" in hook

    def test_creates_hook_scripts(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        hooks_dir = result.root / ".claude" / "hooks"
        assert (hooks_dir / "PostToolUse.sh").exists()
        assert (hooks_dir / "Stop.sh").exists()
        assert (hooks_dir / "Notification.sh").exists()

    def test_hook_scripts_are_executable(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        hook = result.root / ".claude" / "hooks" / "PostToolUse.sh"
        assert hook.stat().st_mode & stat.S_IEXEC

    def test_hook_scripts_report_to_api(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        post_hook = (result.root / ".claude" / "hooks" / "PostToolUse.sh").read_text()
        assert "127.0.0.1:8420" in post_hook
        assert "ace-001" in post_hook
        assert '"working"' in post_hook

        stop_hook = (result.root / ".claude" / "hooks" / "Stop.sh").read_text()
        assert '"waiting"' in stop_hook

    def test_hook_scripts_have_shebang(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        hook = (result.root / ".claude" / "hooks" / "PostToolUse.sh").read_text()
        assert hook.startswith("#!/usr/bin/env bash")

    def test_creates_manifest(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        assert result.manifest_path.exists()
        manifest = json.loads(result.manifest_path.read_text())
        assert manifest["session_id"] == "ace-001"
        assert manifest["session_type"] == "ace"
        assert len(manifest["files"]) > 0

    def test_manifest_lists_all_files(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        manifest = json.loads(result.manifest_path.read_text())
        paths = manifest["files"]
        # CLAUDE.md, settings.json, 3 hooks, manifest itself is NOT in the list
        # (manifest is added last, so it IS in the list)
        assert any("CLAUDE.md" in p for p in paths)
        assert any("settings.json" in p for p in paths)
        assert any("PostToolUse.sh" in p for p in paths)

    def test_custom_allowed_commands(self, staging_root: Path) -> None:
        spec = AceDeploySpec(
            session_id="ace-002",
            project_name="Test",
            task_title="Test task",
            allowed_commands=["Bash(cargo *)"],
        )
        result = deploy_ace_files(spec, staging_root=staging_root)
        settings = json.loads(result.settings_path.read_text())
        assert "Bash(cargo *)" in settings["permissions"]["allow"]

    def test_no_github_repo_omits_section(self, staging_root: Path) -> None:
        spec = AceDeploySpec(
            session_id="ace-003",
            project_name="Test",
            task_title="Test task",
        )
        result = deploy_ace_files(spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Repository" not in content

    def test_no_constraints_omits_section(self, staging_root: Path) -> None:
        spec = AceDeploySpec(
            session_id="ace-004",
            project_name="Test",
            task_title="Test task",
        )
        result = deploy_ace_files(spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Constraints" not in content


# ---------------------------------------------------------------------------
# Manager deployment
# ---------------------------------------------------------------------------


class TestDeployManagerFiles:
    def test_returns_deployed_files(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        assert isinstance(result, DeployedFiles)
        assert result.root == staging_root / "leader-bravo"

    def test_creates_claude_md(self, manager_spec: ManagerDeploySpec, staging_root: Path) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Phoenix — Leader Session" in content
        assert "leader-bravo" in content
        assert "Ship the MVP" in content

    def test_claude_md_includes_role_identity(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "## Role" in content
        assert "Leader" in content
        assert "project manager" in content
        assert "never write code directly" in content
        assert "Create Aces" in content

    def test_claude_md_includes_budget(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "$50.00" in content

    def test_claude_md_includes_initial_tasks(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Set up CI" in content
        assert "Scaffold frontend" in content
        assert "Design DB schema" in content

    def test_claude_md_includes_context_entries(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "tech-stack" in content
        assert "React + FastAPI" in content
        assert "deploy-target" in content
        assert "us-east-1" in content

    def test_claude_md_includes_constraints(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Stay under budget" in content
        assert "No force-pushes" in content

    def test_settings_includes_tower_command(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        settings = json.loads(result.settings_path.read_text())
        allowed = settings["permissions"]["allow"]
        assert "Bash(atc tower *)" in allowed

    def test_creates_hook_scripts(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        hooks_dir = result.root / ".claude" / "hooks"
        assert (hooks_dir / "PostToolUse.sh").exists()
        assert (hooks_dir / "Stop.sh").exists()

    def test_manifest_session_type_is_manager(
        self, manager_spec: ManagerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_manager_files(manager_spec, staging_root=staging_root)
        manifest = json.loads(result.manifest_path.read_text())
        assert manifest["session_type"] == "manager"

    def test_no_budget_omits_section(self, staging_root: Path) -> None:
        spec = ManagerDeploySpec(
            leader_id="leader-x",
            project_name="Test",
            goal="Do something",
        )
        result = deploy_manager_files(spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Budget" not in content


# ---------------------------------------------------------------------------
# Tower deployment
# ---------------------------------------------------------------------------


class TestDeployTowerFiles:
    @pytest.fixture
    def tower_spec(self) -> TowerDeploySpec:
        return TowerDeploySpec(
            session_id="tower-001",
            project_name="Phoenix",
            project_id="proj-abc",
            repo_path="/home/user/phoenix",
            github_repo="acme/phoenix",
        )

    def test_returns_deployed_files(self, tower_spec: TowerDeploySpec, staging_root: Path) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        assert isinstance(result, DeployedFiles)
        assert result.root == staging_root / "tower-001"

    def test_creates_claude_md(self, tower_spec: TowerDeploySpec, staging_root: Path) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        assert result.claude_md_path.exists()
        content = result.claude_md_path.read_text()
        assert "Phoenix — Tower Session" in content
        assert "tower-001" in content

    def test_claude_md_includes_role_identity(
        self, tower_spec: TowerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "## Role" in content
        assert "Tower" in content
        assert "top-level orchestrator" in content
        assert "never write code directly" in content
        assert "Delegate" in content

    def test_claude_md_includes_github_repo(
        self, tower_spec: TowerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "acme/phoenix" in content

    def test_claude_md_includes_repo_path(
        self, tower_spec: TowerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "/home/user/phoenix" in content

    def test_no_repo_info_omits_section(self, staging_root: Path) -> None:
        spec = TowerDeploySpec(
            session_id="tower-002",
            project_name="Test",
            project_id="proj-x",
        )
        result = deploy_tower_files(spec, staging_root=staging_root)
        content = result.claude_md_path.read_text()
        assert "Repository" not in content

    def test_creates_settings_json(
        self, tower_spec: TowerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        assert result.settings_path.exists()
        settings = json.loads(result.settings_path.read_text())
        assert settings["model"] == "opus"
        assert "Bash(atc tower *)" in settings["permissions"]["allow"]

    def test_creates_hook_scripts(
        self, tower_spec: TowerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        hooks_dir = result.root / ".claude" / "hooks"
        assert (hooks_dir / "PostToolUse.sh").exists()
        assert (hooks_dir / "Stop.sh").exists()
        assert (hooks_dir / "Notification.sh").exists()

    def test_manifest_session_type_is_tower(
        self, tower_spec: TowerDeploySpec, staging_root: Path
    ) -> None:
        result = deploy_tower_files(tower_spec, staging_root=staging_root)
        manifest = json.loads(result.manifest_path.read_text())
        assert manifest["session_type"] == "tower"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_removes_files(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        assert result.claude_md_path.exists()

        cleanup_deployed_files(result.root)
        assert not result.claude_md_path.exists()
        assert not result.settings_path.exists()

    def test_cleanup_removes_empty_dirs(self, ace_spec: AceDeploySpec, staging_root: Path) -> None:
        result = deploy_ace_files(ace_spec, staging_root=staging_root)
        cleanup_deployed_files(result.root)
        assert not result.root.exists()

    def test_cleanup_nonexistent_root_is_noop(self, staging_root: Path) -> None:
        cleanup_deployed_files(staging_root / "nonexistent")
        # Should not raise


# ---------------------------------------------------------------------------
# DeployedFiles dataclass
# ---------------------------------------------------------------------------


class TestDeployedFiles:
    def test_paths(self, tmp_path: Path) -> None:
        df = DeployedFiles(root=tmp_path / "test", files=[])
        assert df.claude_md_path == tmp_path / "test" / "CLAUDE.md"
        assert df.settings_path == tmp_path / "test" / ".claude" / "settings.json"
        assert df.manifest_path == tmp_path / "test" / ".manifest.json"

    def test_frozen(self, tmp_path: Path) -> None:
        df = DeployedFiles(root=tmp_path, files=["a.txt"])
        with pytest.raises(AttributeError):
            df.root = tmp_path / "other"  # type: ignore[misc]
