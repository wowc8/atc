"""ATC CLI entry point — dispatches to ``ace``, ``leader``, ``projects``, and ``tower`` subcommands.

Usage::

    atc ace status <session_id> working
    atc ace done <session_id>
    atc ace create --project-id <id> --name '...'
    atc ace list --project-id <id>
    atc leader start --project-id <id>
    atc leader stop --project-id <id>
    atc projects list
    atc projects create --name '...' --description '...'
    atc projects show <id>
    atc tower status
"""

from __future__ import annotations

import argparse
import sys

from atc.cli import ace, leader, projects, tower


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="atc",
        description="ATC CLI — communicate with the ATC backend from agent sessions.",
    )
    subparsers = parser.add_subparsers(dest="command")

    ace.register(subparsers)
    leader.register(subparsers)
    projects.register(subparsers)
    tower.register(subparsers)

    return parser


def cli(argv: list[str] | None = None) -> int:
    """Run the ATC CLI. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


def main() -> None:
    """Console-script entry point."""
    sys.exit(cli())
