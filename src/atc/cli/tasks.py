"""``atc tasks`` subcommands — first-class task graph helpers for Leaders.

These commands intentionally wrap the project task-graph and Leader assignment
REST APIs so managed Leader agents do not need to inspect OpenAPI or hand-write
curl payloads for common task graph operations.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse

_DEFAULT_API = "http://127.0.0.1:8420"


def _add_boundary_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--caller-role",
        default=os.environ.get("ATC_AGENT_ROLE"),
        choices=["tower", "leader", "ace", "operator"],
        help="Calling ATC role for hierarchy boundary enforcement",
    )
    parser.add_argument(
        "--break-glass-approved",
        action="store_true",
        help="Explicit operator-approved override for direct Tower→Ace assignment",
    )
    parser.add_argument(
        "--break-glass-reason",
        default=None,
        help="Required reason when using --break-glass-approved",
    )


def _boundary_headers(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    caller_role = getattr(args, "caller_role", None)
    if caller_role:
        headers["X-ATC-Caller-Role"] = caller_role
    if getattr(args, "break_glass_approved", False):
        headers["X-ATC-Break-Glass-Approved"] = "true"
    reason = getattr(args, "break_glass_reason", None)
    if reason:
        headers["X-ATC-Break-Glass-Reason"] = reason
    return headers


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``tasks`` command group."""
    tasks_parser = subparsers.add_parser("tasks", help="Task graph commands")
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_command")

    list_parser = tasks_sub.add_parser("list", help="List project task graph entries")
    list_parser.add_argument("--project-id", required=True, help="Project UUID")
    list_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    list_parser.set_defaults(handler=_handle_list)

    create_parser = tasks_sub.add_parser("create", help="Create a task graph entry")
    create_parser.add_argument("--project-id", required=True, help="Project UUID")
    create_parser.add_argument("--title", required=True, help="Task title")
    create_parser.add_argument("--description", default=None, help="Task description")
    create_parser.add_argument(
        "--depends-on",
        action="append",
        default=[],
        help="Dependency task_graph_id; repeat for multiple dependencies",
    )
    create_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    create_parser.set_defaults(handler=_handle_create)

    assign_parser = tasks_sub.add_parser("assign", help="Assign/spawn an Ace for one ready task")
    assign_parser.add_argument("--project-id", required=True, help="Project UUID")
    assign_parser.add_argument("--task-id", required=True, help="Task graph UUID")
    assign_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    _add_boundary_args(assign_parser)
    assign_parser.set_defaults(handler=_handle_assign)

    tasks_parser.set_defaults(handler=lambda _: tasks_parser.print_help() or 1)


def _print_json(body: Any) -> None:
    print(json.dumps(body, indent=2))


def _read_response(resp: Any) -> Any:
    raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> int:
    data = json.dumps(payload).encode() if payload is not None else None
    request_headers = {"Content-Type": "application/json"} if payload is not None else {}
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            _print_json(_read_response(resp))
            return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        print(f"Error: {exc.code} — {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Error: cannot reach ATC API — {exc.reason}", file=sys.stderr)
        return 1


def _handle_list(args: argparse.Namespace) -> int:
    return _request_json(f"{args.api}/api/projects/{args.project_id}/task-graphs")


def _handle_create(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"title": args.title}
    if args.description:
        payload["description"] = args.description
    if args.depends_on:
        payload["dependencies"] = args.depends_on
    return _request_json(
        f"{args.api}/api/projects/{args.project_id}/task-graphs",
        method="POST",
        payload=payload,
    )


def _handle_assign(args: argparse.Namespace) -> int:
    return _request_json(
        f"{args.api}/api/projects/{args.project_id}/leader/assign-task",
        method="POST",
        payload={"task_graph_id": args.task_id},
        headers=_boundary_headers(args),
    )
