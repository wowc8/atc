"""ATC CLI entry point — dispatches to ``ace`` and ``tower`` subcommands.

Usage::

    atc ace status <session_id> working
    atc ace done <session_id>
    atc tower status
"""

from __future__ import annotations

import argparse
import sys

from atc.cli import ace, tower


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="atc",
        description="ATC CLI — communicate with the ATC backend from agent sessions.",
    )
    subparsers = parser.add_subparsers(dest="command")

    ace.register(subparsers)
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
