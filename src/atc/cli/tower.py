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

    # atc tower cost <session_id> <input_tokens> <output_tokens> <model>
    cost_parser = tower_sub.add_parser(
        "cost", help="Report explicit cost for a session (primary source)"
    )
    cost_parser.add_argument("session_id", help="Session ID to attribute cost to")
    cost_parser.add_argument("input_tokens", type=int, help="Input token count")
    cost_parser.add_argument("output_tokens", type=int, help="Output token count")
    cost_parser.add_argument("model", help="Model name (e.g. claude-sonnet-4-6)")
    cost_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    cost_parser.set_defaults(handler=_handle_cost)

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



def _handle_cancel(args: argparse.Namespace) -> int:
    return _post_json(f"{args.api}/api/tower/cancel", {})


def _handle_memory(args: argparse.Namespace) -> int:
    return _get_json(f"{args.api}/api/tower/memory")


def _handle_cost(args: argparse.Namespace) -> int:
    return _post_json(
        f"{args.api}/api/tower/cost",
        {
            "session_id": args.session_id,
            "input_tokens": args.input_tokens,
            "output_tokens": args.output_tokens,
            "model": args.model,
        },
    )
