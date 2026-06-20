"""``atc leader`` subcommands — Leader lifecycle management from Tower sessions.

Tower uses these commands to start and stop Leaders via the ATC REST API.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

_DEFAULT_API = "http://127.0.0.1:8420"


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``leader`` command group."""
    leader_parser = subparsers.add_parser("leader", help="Leader lifecycle commands")
    leader_sub = leader_parser.add_subparsers(dest="leader_command")

    # atc leader start --project-id <id>
    start_parser = leader_sub.add_parser("start", help="Start leader for a project")
    start_parser.add_argument("--project-id", required=True, help="Project UUID")
    start_parser.add_argument("--goal", default=None, help="Goal for the leader")
    start_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    start_parser.set_defaults(handler=_handle_start)

    # atc leader stop --project-id <id>
    stop_parser = leader_sub.add_parser("stop", help="Stop leader for a project")
    stop_parser.add_argument("--project-id", required=True, help="Project UUID")
    stop_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    stop_parser.set_defaults(handler=_handle_stop)

    # atc leader message --project-id <id> --message '...'
    msg_parser = leader_sub.add_parser("message", help="Send a message to the leader's terminal")
    msg_parser.add_argument("--project-id", required=True, help="Project UUID")
    msg_parser.add_argument("--message", required=True, help="Message text to send")
    msg_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    msg_parser.set_defaults(handler=_handle_message)

    # atc leader health --project-id <id>
    health_parser = leader_sub.add_parser("health", help="Inspect leader runtime health")
    health_parser.add_argument("--project-id", required=True, help="Project UUID")
    health_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print concise operator guidance instead of raw JSON",
    )
    health_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    health_parser.set_defaults(handler=_handle_health)

    # atc leader recover --project-id <id> [--dry-run|--apply]
    recover_parser = leader_sub.add_parser("recover", help="Plan inspect-first leader recovery")
    recover_parser.add_argument("--project-id", required=True, help="Project UUID")
    recover_parser.add_argument("--policy", default="inspect_first", help="Recovery policy")
    recover_mode = recover_parser.add_mutually_exclusive_group()
    recover_mode.add_argument(
        "--dry-run", action="store_true", default=True, help="Inspect and plan only"
    )
    recover_mode.add_argument("--apply", action="store_true", help="Apply only if policy allows")
    recover_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    recover_parser.set_defaults(handler=_handle_recover)

    # atc leader bootstrap-tasks --project-id <id> --goal '...' [--task '...']
    bootstrap_parser = leader_sub.add_parser(
        "bootstrap-tasks",
        help="Create an initial task graph without inspecting OpenAPI",
    )
    bootstrap_parser.add_argument("--project-id", required=True, help="Project UUID")
    bootstrap_parser.add_argument("--goal", default=None, help="Goal to bootstrap")
    bootstrap_parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Task title to create; repeat for multiple tasks",
    )
    bootstrap_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    bootstrap_parser.set_defaults(handler=_handle_bootstrap_tasks)

    leader_parser.set_defaults(handler=lambda _: leader_parser.print_help() or 1)


def _post_json(url: str, payload: dict) -> int:
    """POST JSON to a URL and print the response."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
            print(json.dumps(body, indent=2))
            return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        print(f"Error: {exc.code} — {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Error: cannot reach ATC API — {exc.reason}", file=sys.stderr)
        return 1


def _get_json(url: str) -> tuple[int, dict | None]:
    """GET JSON from a URL and print the response."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
            return 0, body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        print(f"Error: {exc.code} — {detail}", file=sys.stderr)
        return 1, None
    except urllib.error.URLError as exc:
        print(f"Error: cannot reach ATC API — {exc.reason}", file=sys.stderr)
        return 1, None


def _print_health_summary(body: dict) -> None:
    guidance = body.get("operator_guidance") or {}
    kickoff = body.get("kickoff_state") or {}
    tasks = body.get("task_graph_state") or {}
    dispatch = body.get("ace_dispatch") or {}
    print(f"Leader health: {guidance.get('severity', 'unknown')}")
    print(f"Summary: {guidance.get('summary', 'No guidance available.')}")
    print(f"Runtime: {body.get('runtime_state')} / delivery: {body.get('delivery_state')}")
    print(
        "Leader state: "
        f"{kickoff.get('kickoff_state')} / acceptance: {kickoff.get('goal_acceptance_state')}"
    )
    print(
        "Tasks: "
        f"total={tasks.get('total', 0)} todo={tasks.get('todo', 0)} "
        f"active={tasks.get('assigned', 0) + tasks.get('in_progress', 0)} "
        f"done={tasks.get('done', 0)}"
    )
    print(
        "Ace dispatch: "
        f"verified={dispatch.get('verified', 0)} "
        f"blocked={dispatch.get('blocked', 0)} "
        f"unverified={dispatch.get('unverified', 0)}"
    )
    if body.get("current_blocker"):
        print(f"Blocker: {body['current_blocker']}")
    if guidance.get("recommended_action"):
        print(f"Recommended action: {guidance['recommended_action']}")
    if guidance.get("command"):
        print(f"Command: {guidance['command']}")
    if guidance.get("details"):
        print(f"Details: {guidance['details']}")


def _handle_start(args: argparse.Namespace) -> int:
    payload: dict = {}
    if args.goal:
        payload["goal"] = args.goal
    return _post_json(f"{args.api}/api/projects/{args.project_id}/leader/start", payload)


def _handle_stop(args: argparse.Namespace) -> int:
    return _post_json(f"{args.api}/api/projects/{args.project_id}/leader/stop", {})


def _handle_message(args: argparse.Namespace) -> int:
    return _post_json(
        f"{args.api}/api/projects/{args.project_id}/leader/message",
        {"message": args.message},
    )


def _handle_health(args: argparse.Namespace) -> int:
    rc, body = _get_json(f"{args.api}/api/projects/{args.project_id}/leader/health")
    if rc != 0 or body is None:
        return rc
    if args.summary:
        _print_health_summary(body)
    else:
        print(json.dumps(body, indent=2))
    return 0


def _handle_recover(args: argparse.Namespace) -> int:
    return _post_json(
        f"{args.api}/api/projects/{args.project_id}/leader/recover",
        {"dry_run": not args.apply, "policy": args.policy},
    )


def _handle_bootstrap_tasks(args: argparse.Namespace) -> int:
    goal = args.goal or "Deliver the Tower goal"
    task_titles = args.task or [
        "Plan delivery and acceptance criteria",
        "Execute the implementation work",
        "Validate and report completion evidence",
    ]
    task_specs = [
        {
            "title": title,
            "description": f"Bootstrap task for goal: {goal}",
        }
        for title in task_titles
    ]
    return _post_json(
        f"{args.api}/api/projects/{args.project_id}/leader/decompose",
        {"task_specs": task_specs},
    )
