"""GitHub tracker — polls PR and run status via the ``gh`` CLI.

Every ``poll_interval`` seconds, for each project that has a ``github_repo``
set, the tracker runs ``gh pr list`` and ``gh run list``, upserts the
``github_prs`` table, and broadcasts updates on the
``github:{project_id}`` WebSocket channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

# Map gh run conclusion/status → our ci_status values
_CI_STATUS_MAP: dict[str, str] = {
    "success": "success",
    "failure": "failure",
    "cancelled": "failure",
    "timed_out": "failure",
    "action_required": "failure",
    "in_progress": "running",
    "queued": "pending",
    "waiting": "pending",
    "requested": "pending",
    "pending": "pending",
    "neutral": "success",
    "skipped": "success",
}


async def _run_gh(
    *args: str,
    timeout: float = 30.0,
) -> tuple[str | None, str | None]:
    """Run a ``gh`` subcommand. Returns (stdout, error_message)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return None, f"gh command timed out after {timeout}s"

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return None, f"gh exited {proc.returncode}: {err}"

        return stdout.decode("utf-8", errors="replace"), None
    except OSError as exc:
        return None, f"gh not available: {exc}"


def _map_ci_status(run: dict[str, Any]) -> str:
    """Convert a gh run object to our ci_status string."""
    status = str(run.get("status", "")).lower()
    conclusion = str(run.get("conclusion", "")).lower()
    if conclusion and conclusion not in ("", "null", "none"):
        return _CI_STATUS_MAP.get(conclusion, "pending")
    return _CI_STATUS_MAP.get(status, "pending")


class GitHubTracker:
    """Polls GitHub PR and CI status for all projects with a github_repo."""

    poll_interval = 60.0

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        poll_interval: float | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        if poll_interval is not None:
            self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._rate_limits: dict[str, dict[str, int]] = {}

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("GitHubTracker started (interval=%.0fs)", self.poll_interval)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("GitHubTracker stopped")

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_all()
            except Exception:
                logger.exception("GitHubTracker poll failed")
            await asyncio.sleep(self.poll_interval)

    async def _poll_all(self) -> None:
        """Poll all projects that have github_repo set."""
        cursor = await self._db.execute(
            "SELECT id, github_repo FROM projects WHERE github_repo IS NOT NULL",
        )
        rows = await cursor.fetchall()
        for row in rows:
            project_id = str(row[0])
            github_repo = str(row[1])
            try:
                await self._poll_project(project_id, github_repo)
            except Exception:
                logger.exception(
                    "GitHubTracker failed for project %s (%s)",
                    project_id,
                    github_repo,
                )

    async def poll_project(self, project_id: str, github_repo: str) -> None:
        """Force a sync for a single project (called from the API)."""
        await self._poll_project(project_id, github_repo)

    async def _poll_project(self, project_id: str, github_repo: str) -> None:
        """Fetch PRs and CI runs for a single project/repo."""
        pr_json, err = await _run_gh(
            "pr",
            "list",
            "--repo",
            github_repo,
            "--state",
            "open",
            "--json",
            "number,title,state,url",
            "--limit",
            "100",
        )
        if err:
            logger.warning("gh pr list failed for %s: %s", github_repo, err)
        else:
            await self._process_prs(project_id, github_repo, pr_json or "[]")

        # Fetch recent CI runs to derive ci_status
        runs_json, err = await _run_gh(
            "run",
            "list",
            "--repo",
            github_repo,
            "--json",
            "status,conclusion,headBranch,name",
            "--limit",
            "20",
        )
        run_list: list[dict[str, Any]] = []
        if not err:
            with contextlib.suppress(json.JSONDecodeError):
                run_list = json.loads(runs_json or "[]")

        # Build branch → ci_status map
        branch_ci: dict[str, str] = {}
        for run in run_list:
            branch = str(run.get("headBranch", ""))
            if branch and branch not in branch_ci:
                branch_ci[branch] = _map_ci_status(run)

        if branch_ci:
            await self._update_ci_status(project_id, branch_ci)

        # Track rate limits
        rl_json, rl_err = await _run_gh("api", "rate_limit")
        if not rl_err and rl_json:
            try:
                rl_data = json.loads(rl_json)
                core = rl_data.get("resources", {}).get("core", {})
                self._rate_limits[github_repo] = {
                    "limit": int(core.get("limit", 0)),
                    "remaining": int(core.get("remaining", 0)),
                    "reset": int(core.get("reset", 0)),
                }
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

        if self._ws_hub:
            await self._ws_hub.broadcast(
                f"github:{project_id}",
                {"type": "sync_complete", "project_id": project_id, "repo": github_repo},
            )

        logger.debug("GitHubTracker synced %s (%s)", project_id, github_repo)

    async def _process_prs(
        self,
        project_id: str,
        github_repo: str,
        pr_json: str,
    ) -> None:
        """Parse PR JSON and upsert github_prs rows."""
        try:
            prs: list[dict[str, Any]] = json.loads(pr_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse gh pr list output for %s", github_repo)
            return

        now = datetime.now(UTC).isoformat()

        for pr in prs:
            number = int(pr.get("number", 0))
            if number == 0:
                continue
            pr_id = f"{github_repo}#{number}"
            title = str(pr.get("title", ""))
            state = str(pr.get("state", "open")).lower()
            url = str(pr.get("url", ""))
            status = "open" if state == "open" else state

            await self._db.execute(
                """INSERT INTO github_prs (id, project_id, number, title, status, url, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     title = excluded.title,
                     status = excluded.status,
                     url = excluded.url,
                     updated_at = excluded.updated_at""",
                (pr_id, project_id, number, title, status, url, now),
            )

        await self._db.commit()

    async def _update_ci_status(
        self,
        project_id: str,
        branch_ci: dict[str, str],
    ) -> None:
        """Update ci_status on open PRs for this project using branch run data."""
        if not branch_ci:
            return

        priority = {"failure": 0, "running": 1, "pending": 2, "success": 3}
        overall = min(
            branch_ci.values(),
            key=lambda s: priority.get(s, 99),
        )

        now = datetime.now(UTC).isoformat()
        await self._db.execute(
            """UPDATE github_prs
               SET ci_status = ?, updated_at = ?
               WHERE project_id = ? AND status = 'open'""",
            (overall, now, project_id),
        )
        await self._db.commit()

    def get_rate_limit(self, github_repo: str) -> dict[str, int] | None:
        """Return the last known rate limit for a repo, or None."""
        return self._rate_limits.get(github_repo)
