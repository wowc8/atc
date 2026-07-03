"""``atc usage`` subcommands for deterministic usage sync/backfill."""

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
    """Register the ``usage`` command group."""
    usage_parser = subparsers.add_parser("usage", help="Usage telemetry commands")
    usage_sub = usage_parser.add_subparsers(dest="usage_command")

    sync_codex = usage_sub.add_parser(
        "sync-codex",
        help="Run one deterministic Codex token JSONL sync pass",
    )
    sync_codex.add_argument(
        "--api",
        default=_DEFAULT_API,
        help="ATC API base URL",
    )
    sync_codex.set_defaults(handler=_handle_sync_codex)

    usage_parser.set_defaults(handler=lambda _: usage_parser.print_help() or 1)


def _post_json(url: str, payload: dict) -> int:
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


def _handle_sync_codex(args: argparse.Namespace) -> int:
    return _post_json(f"{args.api}/api/usage/tokens/sync-codex", {})
