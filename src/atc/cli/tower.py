"""``atc tower`` subcommands — Tower interaction from Leader sessions.

Leaders use these commands to query Tower status, report progress, and
interact with the Tower controller via the ATC REST API.
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
    """Register the ``tower`` command group."""
    tower_parser = subparsers.add_parser("tower", help="Tower commands (for Leaders)")
    tower_sub = tower_parser.add_subparsers(dest="tower_command")

    # atc tower status
    status_parser = tower_sub.add_parser("status", help="Get Tower status")
    status_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    status_parser.set_defaults(handler=_handle_status)

    # atc tower goal <project_id> <goal>
    goal_parser = tower_sub.add_parser("goal", help="Submit a goal to the Tower")
    goal_parser.add_argument("project_id", help="Project UUID")
    goal_parser.add_argument("goal", help="Goal description")
    goal_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    goal_parser.set_defaults(handler=_handle_goal)

    # atc tower cancel
    cancel_parser = tower_sub.add_parser("cancel", help="Cancel the current goal")
    cancel_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    cancel_parser.set_defaults(handler=_handle_cancel)

    # atc tower memory
    memory_parser = tower_sub.add_parser("memory", help="List Tower memory entries")
    memory_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    memory_parser.set_defaults(handler=_handle_memory)

    tower_parser.set_defaults(handler=lambda _: tower_parser.print_help() or 1)


def _get_json(url: str) -> int:
    """GET a URL and print the JSON response."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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


def _post_json(url: str, payload: dict) -> int:
    """POST JSON to a URL and print the response."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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


def _handle_status(args: argparse.Namespace) -> int:
    return _get_json(f"{args.api}/api/tower/status")


def _handle_goal(args: argparse.Namespace) -> int:
    return _post_json(
        f"{args.api}/api/tower/goal",
        {"project_id": args.project_id, "goal": args.goal},
    )


def _handle_cancel(args: argparse.Namespace) -> int:
    return _post_json(f"{args.api}/api/tower/cancel", {})


def _handle_memory(args: argparse.Namespace) -> int:
    return _get_json(f"{args.api}/api/tower/memory")
