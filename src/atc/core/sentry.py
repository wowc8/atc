"""Sentry SDK initialisation and PII stripping for ATC backend."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from atc.config import SentryConfig

logger = logging.getLogger(__name__)

# Patterns that look like PII — matched case-insensitively in event data.
_PII_KEY_PATTERNS = re.compile(
    r"(password|secret|token|api.?key|auth|credential|cookie|session.?id|"
    r"email|phone|ssn|credit.?card|card.?number)",
    re.IGNORECASE,
)

_REDACTED = "[Filtered]"


def _strip_pii(obj: Any) -> Any:
    """Recursively redact values whose keys look like PII."""
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _PII_KEY_PATTERNS.search(k) else _strip_pii(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_pii(item) for item in obj]
    return obj


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip PII from Sentry events before they leave the process."""
    # Scrub request data
    if "request" in event:
        req = event["request"]
        if "headers" in req:
            req["headers"] = _strip_pii(req["headers"])
        if "data" in req:
            req["data"] = _strip_pii(req["data"])
        if "cookies" in req:
            req["cookies"] = _REDACTED
        # Strip query strings that may contain tokens
        if "query_string" in req:
            req["query_string"] = _REDACTED

    # Scrub breadcrumb data
    if "breadcrumbs" in event:
        for bc in event.get("breadcrumbs", {}).get("values", []):
            if "data" in bc:
                bc["data"] = _strip_pii(bc["data"])

    # Scrub extra context
    if "extra" in event:
        event["extra"] = _strip_pii(event["extra"])

    # Scrub user info — keep id for correlation but drop everything else
    if "user" in event:
        event["user"] = {"id": event["user"].get("id", "anon")}

    return event


def _before_send_transaction(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any] | None:
    """Strip PII from performance transactions."""
    return _before_send(event, hint)


def init_sentry(config: SentryConfig) -> bool:
    """Initialise Sentry SDK if enabled and DSN is configured.

    Returns True if Sentry was successfully initialised.
    """
    if not config.enabled or not config.dsn:
        logger.debug("Sentry disabled or no DSN configured — skipping init")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=config.dsn,
            environment=config.environment,
            traces_sample_rate=config.traces_sample_rate,
            send_default_pii=False,
            before_send=_before_send,
            before_send_transaction=_before_send_transaction,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
            release=_get_release(),
        )
        logger.info("Sentry initialised (env=%s)", config.environment)
        return True
    except Exception:
        logger.exception("Failed to initialise Sentry SDK")
        return False


def capture_exception(exc: Exception, **extra: Any) -> str | None:
    """Capture an exception to Sentry with optional extra context.

    Returns the Sentry event ID, or None if Sentry is not active.
    """
    try:
        import sentry_sdk

        if sentry_sdk.is_initialized():
            with sentry_sdk.new_scope() as scope:
                for k, v in extra.items():
                    scope.set_extra(k, v)
                return sentry_sdk.capture_exception(exc)
    except Exception:
        logger.debug("Failed to send exception to Sentry", exc_info=True)
    return None


def capture_message(message: str, level: str = "error", **extra: Any) -> str | None:
    """Capture a message to Sentry with optional extra context."""
    try:
        import sentry_sdk

        if sentry_sdk.is_initialized():
            with sentry_sdk.new_scope() as scope:
                for k, v in extra.items():
                    scope.set_extra(k, v)
                return sentry_sdk.capture_message(message, level=level)
    except Exception:
        logger.debug("Failed to send message to Sentry", exc_info=True)
    return None


def _get_release() -> str:
    """Return the ATC version string for Sentry release tracking."""
    try:
        from atc import __version__

        return f"atc@{__version__}"
    except Exception:
        return "atc@unknown"
