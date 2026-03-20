"""``atc projects`` subcommands — project management from Tower sessions.

Tower uses these commands to create and inspect projects via the ATC REST API.
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
    """Register the ``projects`` command group."""
    proj_parser = subparsers.add_parser("projects", help="Project management commands")
    proj_sub = proj_parser.add_subparsers(dest="projects_command")

    # atc projects list
    list_parser = proj_sub.add_parser("list", help="List all projects")
    list_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    list_parser.set_defaults(handler=_handle_list)

    # atc projects create --name '...' --description '...'
    create_parser = proj_sub.add_parser("create", help="Create a new project")
    create_parser.add_argument("--name", required=True, help="Project name")
    create_parser.add_argument("--description", default=None, help="Project description")
    create_parser.add_argument("--repo-path", default=None, help="Local repository path")
    create_parser.add_argument("--github-repo", default=None, help="GitHub repo (owner/repo)")
    create_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    create_parser.set_defaults(handler=_handle_create)

    # atc projects show <id>
    show_parser = proj_sub.add_parser("show", help="Show project details")
    show_parser.add_argument("project_id", help="Project UUID")
    show_parser.add_argument("--api", default=_DEFAULT_API, help="ATC API base URL")
    show_parser.set_defaults(handler=_handle_show)

    proj_parser.set_defaults(handler=lambda _: proj_parser.print_help() or 1)


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


def _handle_list(args: argparse.Namespace) -> int:
    return _get_json(f"{args.api}/api/projects")


def _handle_create(args: argparse.Namespace) -> int:
    payload: dict = {"name": args.name}
    if args.description:
        payload["description"] = args.description
    if args.repo_path:
        payload["repo_path"] = args.repo_path
    if args.github_repo:
        payload["github_repo"] = args.github_repo
    return _post_json(f"{args.api}/api/projects", payload)


def _handle_show(args: argparse.Namespace) -> int:
    return _get_json(f"{args.api}/api/projects/{args.project_id}")
