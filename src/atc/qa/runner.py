"""Test runner for the QA loop — tsc, pytest, and optional Playwright."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TestFailure:
    """A single failure from any test category."""

    category: str  # "typescript" | "pytest" | "playwright"
    file: str
    line: int | None
    message: str
    raw: str


@dataclass
class TestRunResult:
    """Aggregated result of a full test run."""

    failures: list[TestFailure] = field(default_factory=list)
    passed: bool = False
    tsc_output: str | None = None
    pytest_output: str | None = None
    playwright_output: str | None = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# tsc --noEmit output lines look like:
#   src/foo.ts(10,5): error TS2345: Argument of type ...
_TSC_LINE_RE = re.compile(
    r"^(?P<file>[^(]+)\((?P<line>\d+),\d+\): (?:error|warning) TS\d+: (?P<msg>.+)$"
)


def _parse_tsc_failures(output: str) -> list[TestFailure]:
    """Extract TypeScript compiler errors from ``tsc --noEmit`` output."""
    failures: list[TestFailure] = []
    for raw_line in output.splitlines():
        m = _TSC_LINE_RE.match(raw_line.strip())
        if m:
            failures.append(
                TestFailure(
                    category="typescript",
                    file=m.group("file").strip(),
                    line=int(m.group("line")),
                    message=m.group("msg").strip(),
                    raw=raw_line,
                )
            )
    return failures


# pytest --tb=short -q output: FAILED tests/foo.py::test_bar - AssertionError
_PYTEST_FAILED_RE = re.compile(r"^FAILED (?P<node_id>\S+?)(?:\s+-\s+(?P<msg>.+))?$")
# file + line from short traceback: "tests/foo.py:42:"
_PYTEST_LOCATION_RE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):")


def _parse_pytest_failures(output: str) -> list[TestFailure]:
    """Extract pytest failures from ``--tb=short -q`` output."""
    failures: list[TestFailure] = []
    lines = output.splitlines()
    for i, raw_line in enumerate(lines):
        m = _PYTEST_FAILED_RE.match(raw_line.strip())
        if not m:
            continue
        node_id = m.group("node_id")
        msg = (m.group("msg") or "").strip()
        # Try to extract file and line from the node_id
        file_part = node_id.split("::")[0] if "::" in node_id else node_id
        line_num: int | None = None
        # Scan preceding lines for a location hint
        for prev in reversed(lines[max(0, i - 10) : i]):
            loc = _PYTEST_LOCATION_RE.match(prev.strip())
            if loc:
                line_num = int(loc.group("line"))
                break
        failures.append(
            TestFailure(
                category="pytest",
                file=file_part,
                line=line_num,
                message=msg or node_id,
                raw=raw_line,
            )
        )
    return failures


def _parse_playwright_failures(output: str) -> list[TestFailure]:
    """Extract Playwright test failures from CLI output."""
    failures: list[TestFailure] = []
    # Playwright CLI reports failures like:
    #   ✘ [chromium] tests/foo.spec.ts:12:5 - Error: ...
    pw_re = re.compile(
        r"✘\s+\[[\w]+\]\s+(?P<file>[^\s:]+):(?P<line>\d+):\d+\s+-\s+(?P<msg>.+)"
    )
    for raw_line in output.splitlines():
        m = pw_re.search(raw_line)
        if m:
            failures.append(
                TestFailure(
                    category="playwright",
                    file=m.group("file"),
                    line=int(m.group("line")),
                    message=m.group("msg").strip(),
                    raw=raw_line,
                )
            )
    return failures


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TestRunner:
    """Async runner that executes tsc, pytest, and optionally Playwright."""

    def __init__(
        self,
        repo_path: str,
        *,
        timeout: float = 300.0,
        run_playwright: bool = False,
        playwright_base_url: str = "http://localhost:3000",
    ) -> None:
        self._repo_path = repo_path
        self._timeout = timeout
        self._run_playwright = run_playwright
        self._playwright_base_url = playwright_base_url

    async def run(self) -> TestRunResult:
        """Execute all configured test suites and return an aggregated result."""
        result = TestRunResult()
        all_failures: list[TestFailure] = []

        # TypeScript type-check (non-fatal if tsc not found)
        tsc_failures, tsc_out = await self._run_tsc()
        result.tsc_output = tsc_out
        all_failures.extend(tsc_failures)

        # pytest — unit + integration
        pytest_failures, pytest_out = await self._run_pytest()
        result.pytest_output = pytest_out
        all_failures.extend(pytest_failures)

        # Playwright (optional)
        if self._run_playwright:
            pw_failures, pw_out = await self._run_playwright_tests()
            result.playwright_output = pw_out
            all_failures.extend(pw_failures)

        result.failures = all_failures
        result.passed = len(all_failures) == 0
        return result

    # ------------------------------------------------------------------
    # Sub-runners
    # ------------------------------------------------------------------

    async def _run_tsc(self) -> tuple[list[TestFailure], str]:
        """Run ``tsc --noEmit`` and return (failures, raw_output)."""
        output = await self._exec(
            ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"],
            description="tsc --noEmit",
        )
        if output is None:
            return [], ""
        return _parse_tsc_failures(output), output

    async def _run_pytest(self) -> tuple[list[TestFailure], str]:
        """Run pytest on unit and integration suites."""
        output = await self._exec(
            [
                "python3",
                "-m",
                "pytest",
                "tests/unit",
                "tests/integration",
                "-q",
                "--tb=short",
                "--no-header",
            ],
            description="pytest",
        )
        if output is None:
            return [], ""
        return _parse_pytest_failures(output), output

    async def _run_playwright_tests(self) -> tuple[list[TestFailure], str]:
        """Run Playwright E2E tests if available."""
        output = await self._exec(
            ["npx", "--no-install", "playwright", "test"],
            description="playwright",
        )
        if output is None:
            return [], ""
        return _parse_playwright_failures(output), output

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

    async def _exec(
        self, cmd: list[str], *, description: str
    ) -> str | None:
        """Run *cmd* in the repo directory. Returns stdout+stderr, or None on spawn failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._repo_path,
            )
        except OSError as exc:
            logger.warning("TestRunner: failed to spawn %s: %s", description, exc)
            return None

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError:
            proc.kill()
            logger.warning(
                "TestRunner: %s timed out after %.0fs", description, self._timeout
            )
            return f"{description} timed out after {self._timeout:.0f}s"

        output = stdout_bytes.decode("utf-8", errors="replace")
        rc = proc.returncode
        logger.debug("TestRunner: %s exit=%s len=%d", description, rc, len(output))
        return output
