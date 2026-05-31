"""Wrapper event parsing and schema helpers for atc-provider output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_EVENT_PREFIX = "ATC_EVENT "


@dataclass(slots=True)
class WrapperEvent:
    """Normalized parsed wrapper event."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str | None:
        return self.payload.get("session_id")

    @property
    def provider(self) -> str | None:
        return self.payload.get("provider")

    @property
    def command(self) -> str | None:
        return self.payload.get("command")


class WrapperEventParseError(ValueError):
    """Raised when a wrapper event line is malformed."""


SUPPORTED_EVENT_NAMES = {
    "runtime_starting",
    "runtime_ready",
    "runtime_blocked",
    "runtime_error",
    "runtime_stopping",
    "runtime_stopped",
    "delivery_started",
    "delivery_confirmed",
    "delivery_blocked",
    "delivery_error",
    "task_assignment_started",
    "task_assignment_confirmed",
    "task_assignment_blocked",
    "task_assignment_error",
    "readiness_result",
    "inspection_result",
    "restore_result",
}

REQUIRED_COMMON_FIELDS = {"session_id", "provider", "command"}


def parse_wrapper_event(line: str) -> WrapperEvent:
    """Parse a machine-readable `ATC_EVENT` line.

    Expected format:
        ATC_EVENT <event_name> <json_payload>
    """

    if not line.startswith(_EVENT_PREFIX):
        raise WrapperEventParseError("Missing ATC_EVENT prefix")

    remainder = line[len(_EVENT_PREFIX) :].strip()
    if not remainder:
        raise WrapperEventParseError("Missing event body")

    try:
        name, payload_json = remainder.split(" ", 1)
    except ValueError as exc:
        raise WrapperEventParseError("Missing payload JSON") from exc

    if name not in SUPPORTED_EVENT_NAMES:
        raise WrapperEventParseError(f"Unsupported event name: {name}")

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise WrapperEventParseError("Invalid event JSON payload") from exc

    if not isinstance(payload, dict):
        raise WrapperEventParseError("Event payload must be an object")

    missing = REQUIRED_COMMON_FIELDS - payload.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise WrapperEventParseError(f"Missing required event fields: {missing_list}")

    return WrapperEvent(name=name, payload=payload)
