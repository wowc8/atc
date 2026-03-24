"""Tests for atc-agent script and the get_launch_command factory integration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from atc.agents.factory import get_launch_command

# Absolute path to the atc-agent script in the project root.
_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "atc-agent"


# ---------------------------------------------------------------------------
# get_launch_command — atc-agent integration
# ---------------------------------------------------------------------------


class TestGetLaunchCommandAtcAgent:
    def test_returns_script_when_exists(self) -> None:
        """When atc-agent exists on disk, claude_code should use it."""
        assert _SCRIPT.exists(), f"atc-agent not found at {_SCRIPT}"
        cmd = get_launch_command("claude_code")
        assert cmd == str(_SCRIPT)

    def test_falls_back_when_script_missing(self, tmp_path: Path) -> None:
        """When atc-agent is absent, fall back to bare claude command."""
        # Patch Path.exists on the computed script path inside get_launch_command.
        with patch.object(Path, "exists", return_value=False):
            cmd = get_launch_command("claude_code")
        assert cmd == "claude --dangerously-skip-permissions"

    def test_opencode_unaffected(self) -> None:
        """opencode provider is not affected by atc-agent presence."""
        cmd = get_launch_command("opencode")
        assert cmd == "opencode"

    def test_unknown_falls_back_to_claude(self) -> None:
        """Unknown providers still fall back to the claude_code default."""
        with patch.object(Path, "exists", return_value=False):
            cmd = get_launch_command("unknown_provider")
        assert cmd == "claude --dangerously-skip-permissions"


# ---------------------------------------------------------------------------
# atc-agent script behaviour (subprocess tests)
# ---------------------------------------------------------------------------


def _run_agent(
    env: dict[str, str], extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run atc-agent with a minimal env, replacing exec'd claude with 'true'."""
    # We cannot actually exec claude, so override PATH so that 'claude' resolves
    # to a no-op script we create inline via a wrapper.
    cmd = [str(_SCRIPT)] + (extra_args or [])
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def base_env() -> dict[str, str]:
    """Minimal env with PATH pointing at a fake 'claude' that just exits 0."""
    # Build a minimal PATH that includes a directory with a stub 'claude'.
    # We use a shell trick: inject a function override via a wrapper script
    # stored in a temp dir.
    return {}


class TestAtcAgentScript:
    """Run the atc-agent script in a subprocess with a stub 'claude' binary."""

    @pytest.fixture(autouse=True)
    def stub_claude(self, tmp_path: Path) -> Path:
        """Create a stub 'claude' script that records its invocation and exits 0."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "claude"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n")
        stub.chmod(0o755)
        self._bin_dir = bin_dir
        return bin_dir

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            "PATH": f"{self._bin_dir}:{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        if extra:
            env.update(extra)
        return env

    def test_script_is_executable(self) -> None:
        assert os.access(_SCRIPT, os.X_OK), "atc-agent must be executable"

    def test_no_env_vars_exits_zero(self) -> None:
        result = subprocess.run(
            [str(_SCRIPT)],
            env=self._env(),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_creates_repo_path(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo" / "subdir"
        assert not repo.exists()
        result = subprocess.run(
            [str(_SCRIPT)],
            env=self._env({"ATC_REPO_PATH": str(repo)}),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert repo.is_dir(), "ATC_REPO_PATH should have been created"

    def test_copies_claude_md_from_staging(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        repo = tmp_path / "repo"
        # Write a CLAUDE.md in staging; repo does not exist yet.
        (staging / "CLAUDE.md").write_text("# project instructions\n")

        result = subprocess.run(
            [str(_SCRIPT)],
            env=self._env({
                "ATC_REPO_PATH": str(repo),
                "ATC_STAGING_DIR": str(staging),
            }),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (repo / "CLAUDE.md").exists(), "CLAUDE.md should be copied from staging"
        assert (repo / "CLAUDE.md").read_text() == "# project instructions\n"

    def test_does_not_overwrite_existing_claude_md(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        (staging / "CLAUDE.md").write_text("staging version\n")
        (repo / "CLAUDE.md").write_text("existing version\n")

        result = subprocess.run(
            [str(_SCRIPT)],
            env=self._env({
                "ATC_REPO_PATH": str(repo),
                "ATC_STAGING_DIR": str(staging),
            }),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (repo / "CLAUDE.md").read_text() == "existing version\n"

    def test_staging_only_no_crash(self, tmp_path: Path) -> None:
        """ATC_STAGING_DIR set but no ATC_REPO_PATH — should cd into staging."""
        staging = tmp_path / "staging"
        staging.mkdir()
        result = subprocess.run(
            [str(_SCRIPT)],
            env=self._env({"ATC_STAGING_DIR": str(staging)}),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
