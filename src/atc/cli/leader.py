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

    leader_parser.set_defaults(handler=lambda _: leader_parser.print_help() or 1)


def _post_json(url: str, payload: dict) -> int:
    """POST JSON to a URL and print the response."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
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


def _handle_start(args: argparse.Namespace) -> int:
    payload: dict = {}
    if args.goal:
        payload["goal"] = args.goal
    return _post_json(f"{args.api}/api/projects/{args.project_id}/leader/start", payload)


def _handle_stop(args: argparse.Namespace) -> int:
    return _post_json(f"{args.api}/api/projects/{args.project_id}/leader/stop", {})
