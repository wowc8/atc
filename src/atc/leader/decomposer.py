"""Goal decomposition — breaks a goal into task graph entries.

The decomposer takes a context package (built by Tower) and produces a list
of task graph entries with titles, descriptions, dependencies, and priorities.
Each entry becomes a row in the ``task_graphs`` table and is later assigned
to an Ace session by the Leader orchestrator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite

    from atc.state.models import TaskGraph

logger = logging.getLogger(__name__)


@dataclass
class TaskSpec:
    """Specification for a single task to be created in the task graph."""

    title: str
    description: str | None = None
    priority: int = 0
    dependencies: list[str] | None = None  # titles of tasks this depends on


@dataclass
class DecompositionResult:
    """Result of decomposing a goal into task graph entries."""

    project_id: str
    goal: str
    task_graphs: list[TaskGraph] = field(default_factory=list)
    error: str | None = None


async def decompose_goal(
    conn: aiosqlite.Connection,
    context_package: dict[str, Any],
    task_specs: list[TaskSpec],
) -> DecompositionResult:
    """Decompose a goal into task graph entries in the database.

    Takes the context package from Tower and a list of TaskSpec objects
    (produced by the Leader's planning phase) and creates corresponding
    task_graph rows in the database.

    The decomposition flow:
      1. Validate context package has required fields
      2. Create task_graph entries for each TaskSpec
      3. Resolve dependency titles to task_graph IDs
      4. Return the created entries

    Parameters
    ----------
    conn:
        Active database connection.
    context_package:
        The context package from Tower containing goal, project_id, etc.
    task_specs:
        List of task specifications to create in the task graph.

    Returns
    -------
    DecompositionResult with created task_graph entries.
    """
    project_id = context_package.get("project_id")
    goal = context_package.get("goal", "")

    if not project_id:
        return DecompositionResult(
            project_id="",
            goal=goal,
            error="Context package missing project_id",
        )

    if not task_specs:
        return DecompositionResult(
            project_id=project_id,
            goal=goal,
            error="No task specifications provided",
        )

    # Verify project exists
    project = await db_ops.get_project(conn, project_id)
    if project is None:
        return DecompositionResult(
            project_id=project_id,
            goal=goal,
            error=f"Project {project_id} not found",
        )

    # Bug #164 follow-up: keep task IDs stable across repeat decompositions.
    # Deleting + recreating matching todo tasks introduces a race with spawn-aces:
    # a second decompose can replace a task row while an Ace is being created for
    # the first one, leaving orphaned sessions and stale task_graph_ids. Reuse
    # existing todo/unassigned tasks by title when possible, delete only stale
    # unmatched todo tasks, and create only genuinely new tasks.
    existing = await db_ops.list_task_graphs(conn, project_id=project_id)
    reusable_by_title = {
        tg.title: tg for tg in existing if tg.status == "todo" and tg.assigned_ace_id is None
    }
    requested_titles = {spec.title for spec in task_specs}

    for tg in existing:
        if tg.status == "todo" and tg.assigned_ace_id is None and tg.title not in requested_titles:
            await db_ops.delete_task_graph(conn, tg.id)
            logger.debug(
                "decompose_goal: deleted stale todo task_graph %s ('%s')",
                tg.id,
                tg.title,
            )

    created: list[TaskGraph] = []
    title_to_id: dict[str, str] = {}

    # Phase 1: Reuse matching task_graph entries when possible, otherwise create.
    for spec in task_specs:
        existing_tg = reusable_by_title.get(spec.title)
        if existing_tg is not None:
            tg = await db_ops.update_task_graph(
                conn,
                existing_tg.id,
                description=spec.description,
                dependencies=None,
            )
            if tg is None:
                logger.warning(
                    "decompose_goal: failed to reload reusable task_graph %s ('%s'); recreating",
                    existing_tg.id,
                    existing_tg.title,
                )
                tg = await db_ops.create_task_graph(
                    conn,
                    project_id,
                    spec.title,
                    description=spec.description,
                    status="todo",
                )
        else:
            tg = await db_ops.create_task_graph(
                conn,
                project_id,
                spec.title,
                description=spec.description,
                status="todo",
            )
        created.append(tg)
        title_to_id[spec.title] = tg.id

    # Phase 2: Resolve dependency titles to IDs and update
    for spec, tg in zip(task_specs, created, strict=True):
        if not spec.dependencies:
            continue

        dep_ids: list[str] = []
        for dep_title in spec.dependencies:
            dep_id = title_to_id.get(dep_title)
            if dep_id is not None:
                dep_ids.append(dep_id)
            else:
                logger.warning(
                    "Task '%s' depends on unknown task '%s' — skipping dependency",
                    spec.title,
                    dep_title,
                )

        if dep_ids:
            updated = await db_ops.update_task_graph(
                conn,
                tg.id,
                dependencies=dep_ids,
            )
            if updated is not None:
                # Replace the entry in created list with updated version
                idx = created.index(tg)
                created[idx] = updated

    logger.info(
        "Decomposed goal '%s' into %d task graph entries for project %s",
        goal,
        len(created),
        project_id,
    )

    return DecompositionResult(
        project_id=project_id,
        goal=goal,
        task_graphs=created,
    )


def get_ready_tasks(task_graphs: list[TaskGraph]) -> list[TaskGraph]:
    """Return task graphs that are ready to be assigned (no unfinished deps).

    A task is ready if:
      - Its status is ``todo``
      - It has no dependencies, OR all its dependencies are ``done``
    """
    done_ids = {tg.id for tg in task_graphs if tg.status == "done"}

    ready: list[TaskGraph] = []
    for tg in task_graphs:
        if tg.status != "todo":
            continue
        deps = tg.dependencies or []
        if all(dep_id in done_ids for dep_id in deps):
            ready.append(tg)

    return ready


def get_completion_status(task_graphs: list[TaskGraph]) -> dict[str, Any]:
    """Return a summary of task graph completion status.

    Returns a dict with counts and overall status:
      - total: total number of tasks
      - done: number of completed tasks
      - in_progress: number of in-progress tasks
      - todo: number of pending tasks
      - all_done: True if every task is done
      - progress_pct: percentage complete (0-100)
    """
    total = len(task_graphs)
    if total == 0:
        return {
            "total": 0,
            "done": 0,
            "in_progress": 0,
            "todo": 0,
            "all_done": True,
            "progress_pct": 100,
        }

    done = sum(1 for tg in task_graphs if tg.status == "done")
    in_progress = sum(1 for tg in task_graphs if tg.status in ("assigned", "in_progress", "review"))
    todo = sum(1 for tg in task_graphs if tg.status == "todo")
    error = sum(1 for tg in task_graphs if tg.status == "error")

    return {
        "total": total,
        "done": done,
        "in_progress": in_progress,
        "todo": todo,
        "error": error,
        "all_done": done == total,
        "progress_pct": round((done / total) * 100),
    }
