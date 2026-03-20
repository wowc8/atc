"""Leader context package — assembles project context for a Leader session.

A context package is a dict containing everything a Leader needs to start
working on a goal: the goal itself, project metadata, repo path, and any
relevant context entries from the project's context hub.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from atc.state.db import get_context_for_agent

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


async def build_context_package(
    db: aiosqlite.Connection,
    project_id: str,
    goal: str,
    *,
    session_id: str | None = None,
    parent_session_id: str | None = None,
    scope: str = "leader",
) -> dict[str, Any]:
    """Assemble a context package for a Leader or Ace session.

    Returns a dict with:
      - goal: the user's goal string
      - project_id: project identifier
      - project_name: human-readable project name
      - repo_path: filesystem path to the project repo (if set)
      - github_repo: GitHub owner/repo (if set)
      - context_entries: list of context hub entries visible to the agent
    """
    # Fetch project metadata
    cursor = await db.execute(
        "SELECT id, name, repo_path, github_repo, description FROM projects WHERE id = ?",
        (project_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Project {project_id} not found")

    project = dict(row)

    # Fetch context entries using scope-aware inheritance
    entries = await get_context_for_agent(
        db,
        scope,
        project_id=project_id,
        session_id=session_id,
        parent_session_id=parent_session_id,
    )

    context_entries = []
    for entry in entries:
        entry_dict = {
            "key": entry.key,
            "entry_type": entry.entry_type,
            "value": entry.value,
        }
        # Parse JSON values
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            entry_dict["value"] = json.loads(entry_dict["value"])
        context_entries.append(entry_dict)

    return {
        "goal": goal,
        "project_id": project_id,
        "project_name": project["name"],
        "repo_path": project.get("repo_path"),
        "github_repo": project.get("github_repo"),
        "description": project.get("description"),
        "context_entries": context_entries,
    }
