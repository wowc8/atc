"""``atc ace`` subcommands — status reporting from Ace and Manager sessions.

These commands are called by agents (via CLAUDE.md instructions) and by hook
scripts (PostToolUse, Stop) to report session status back to the ATC API.
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

_DEFAULT_API = "http://127.0.0.1:8420"


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``ace`` command group."""
    ace_parser = subparsers.add_parser("ace", help="Ace session commands")
    ace_sub = ace_parser.add_subparsers(dest="ace_command")

    # atc ace status <session_id> <status>
    status_parser = ace_sub.add_parser("status", help="Report session status")
    status_parser.add_argument("session_id", help="Session UUID")
    status_parser.add_argument(
        "status",
        choices=["working", "waiting", "idle", "paused", "error"],
        help="New status",
    )
    status_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    status_parser.set_defaults(handler=_handle_status)

    # atc ace done <session_id>
    done_parser = ace_sub.add_parser("done", help="Mark session as done (idle)")
    done_parser.add_argument("session_id", help="Session UUID")
    done_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    done_parser.set_defaults(handler=_handle_done)

    # atc ace blocked <session_id> --reason "..."
    blocked_parser = ace_sub.add_parser("blocked", help="Report session is blocked")
    blocked_parser.add_argument("session_id", help="Session UUID")
    blocked_parser.add_argument(
        "--reason", default="", help="Reason for being blocked",
    )
    blocked_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    blocked_parser.set_defaults(handler=_handle_blocked)

    # atc ace notify <session_id> <message>
    notify_parser = ace_sub.add_parser("notify", help="Send notification to ATC")
    notify_parser.add_argument("session_id", help="Session UUID")
    notify_parser.add_argument("message", help="Notification message")
    notify_parser.add_argument(
        "--api", default=_DEFAULT_API, help="ATC API base URL",
    )
    notify_parser.set_defaults(handler=_handle_notify)

    ace_parser.set_defaults(handler=lambda _: ace_parser.print_help() or 1)


def _api_patch_status(api_base: str, session_id: str, status: str) -> int:
    """PATCH /api/aces/{session_id}/status with the given status."""
    url = f"{api_base}/api/aces/{session_id}/status"
    payload = json.dumps({"status": status}).encode()
    req = urllib.request.Request(
        url, data=payload, method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            print(json.dumps(body))
            return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        print(f"Error: {exc.code} — {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Error: cannot reach ATC API at {api_base} — {exc.reason}", file=sys.stderr)
        return 1


def _api_post_notify(api_base: str, session_id: str, message: str) -> int:
    """POST /api/aces/{session_id}/notify with the given message."""
    url = f"{api_base}/api/aces/{session_id}/notify"
    payload = json.dumps({"message": message}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            print(json.dumps(body))
            return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        print(f"Error: {exc.code} — {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Error: cannot reach ATC API at {api_base} — {exc.reason}", file=sys.stderr)
        return 1


def _handle_status(args: argparse.Namespace) -> int:
    return _api_patch_status(args.api, args.session_id, args.status)


def _handle_done(args: argparse.Namespace) -> int:
    return _api_patch_status(args.api, args.session_id, "idle")


def _handle_blocked(args: argparse.Namespace) -> int:
    # Report waiting status (blocked is semantically "waiting for help")
    # and send a notification with the reason if provided
    rc = _api_patch_status(args.api, args.session_id, "waiting")
    if rc != 0:
        return rc
    if args.reason:
        return _api_post_notify(args.api, args.session_id, f"BLOCKED: {args.reason}")
    return 0


def _handle_notify(args: argparse.Namespace) -> int:
    return _api_post_notify(args.api, args.session_id, args.message)
