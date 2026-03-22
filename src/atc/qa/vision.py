"""Vision reviewer — Playwright screenshots + Claude visual QA.

Takes screenshots of key views using the ``playwright`` CLI, then asks
Claude claude-sonnet-4-6 to identify visual regressions or UI issues.
Skips gracefully when:
  - ``ANTHROPIC_API_KEY`` is not set in the environment.
  - ``playwright`` CLI is not available on PATH.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class VisualIssue:
    """A single visual problem identified by the Claude reviewer."""

    view: str
    description: str
    severity: str  # "blocker" | "minor"


# ---------------------------------------------------------------------------
# VisionReviewer
# ---------------------------------------------------------------------------

_VIEWS: list[tuple[str, str]] = [
    ("/dashboard", "dashboard"),
    ("/usage", "usage"),
]

_PROMPT = """\
You are a QA engineer reviewing screenshots of a web application.
Examine the screenshot and list any visual issues: broken layouts, missing
content, overlapping elements, console errors visible on screen, or anything
that looks unintentionally wrong.

Respond with a JSON array of objects, each with:
  "description": short description of the issue
  "severity": "blocker" or "minor"

Return an empty array [] if no issues are found.
Do not include markdown fences — only the raw JSON array.
"""


class VisionReviewer:
    """Screenshot-based visual regression reviewer using Claude."""

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        *,
        timeout: float = 30.0,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._model = model

    async def review(self) -> list[VisualIssue]:
        """Take screenshots and ask Claude to identify issues.

        Returns an empty list (not an error) if the API key is missing or
        playwright is not installed.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.info("VisionReviewer: ANTHROPIC_API_KEY not set — skipping")
            return []

        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError:
            logger.info("VisionReviewer: anthropic package not installed — skipping")
            return []

        if not await self._playwright_available():
            logger.info("VisionReviewer: playwright not available — skipping")
            return []

        issues: list[VisualIssue] = []
        client: Any = anthropic.AsyncAnthropic(api_key=api_key)

        with tempfile.TemporaryDirectory() as tmpdir:
            for path, view_name in _VIEWS:
                url = f"{self._base_url}{path}"
                screenshot_path = Path(tmpdir) / f"{view_name}.png"

                captured = await self._capture_screenshot(url, screenshot_path)
                if not captured:
                    logger.warning(
                        "VisionReviewer: screenshot failed for %s", view_name
                    )
                    continue

                view_issues = await self._review_screenshot(
                    client, view_name, screenshot_path
                )
                issues.extend(view_issues)

        return issues

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _playwright_available(self) -> bool:
        """Return True if the ``playwright`` CLI can be invoked."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "--no-install",
                "playwright",
                "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            return proc.returncode == 0
        except (OSError, TimeoutError):
            return False

    async def _capture_screenshot(self, url: str, dest: Path) -> bool:
        """Capture a screenshot of *url* to *dest* via the playwright CLI."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "--no-install",
                "playwright",
                "screenshot",
                "--browser",
                "chromium",
                "--wait-for-timeout",
                "3000",
                url,
                str(dest),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except TimeoutError:
                proc.kill()
                logger.warning("VisionReviewer: screenshot timed out for %s", url)
                return False
        except OSError as exc:
            logger.warning("VisionReviewer: failed to spawn playwright: %s", exc)
            return False

        return dest.exists() and dest.stat().st_size > 0

    async def _review_screenshot(
        self,
        client: Any,
        view_name: str,
        screenshot_path: Path,
    ) -> list[VisualIssue]:
        """Send *screenshot_path* to Claude and parse the returned issues."""
        import anthropic

        raw = screenshot_path.read_bytes()
        image_b64 = base64.standard_b64encode(raw).decode()

        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": _PROMPT},
                        ],
                    }
                ],
            )
        except anthropic.APIError as exc:
            logger.warning(
                "VisionReviewer: Claude API error for view %s: %s", view_name, exc
            )
            return []

        text = response.content[0].text.strip()
        try:
            raw_list: list[dict[str, str]] = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "VisionReviewer: could not parse Claude response for %s: %r",
                view_name,
                text[:200],
            )
            return []

        issues: list[VisualIssue] = []
        for item in raw_list:
            severity = item.get("severity", "minor")
            if severity not in ("blocker", "minor"):
                severity = "minor"
            issues.append(
                VisualIssue(
                    view=view_name,
                    description=str(item.get("description", "")),
                    severity=severity,
                )
            )
        return issues
