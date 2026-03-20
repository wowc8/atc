"""Unit tests for the ATC CLI entry points (src/atc/cli/)."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import pytest

from atc.cli.main import cli

# ---------------------------------------------------------------------------
# Lightweight HTTP stub for the ATC API
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """Captures requests and returns canned responses."""

    requests: list[dict[str, Any]] = []
    response_code: int = 200
    response_body: dict[str, Any] = {"status": "ok"}

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        _StubHandler.requests.append({
            "method": self.command,
            "path": self.path,
            "body": json.loads(body) if body else None,
        })
        self.send_response(_StubHandler.response_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(_StubHandler.response_body).encode())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # Suppress logging during tests


@pytest.fixture
def api_stub():
    """Start a stub HTTP server and yield the base URL."""
    _StubHandler.requests = []
    _StubHandler.response_code = 200
    _StubHandler.response_body = {"status": "ok"}

    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Top-level CLI
# ---------------------------------------------------------------------------


class TestCLIDispatch:
    def test_no_args_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli([]) == 1

    def test_ace_no_subcommand_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli(["ace"]) == 1

    def test_tower_no_subcommand_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli(["tower"]) == 1


# ---------------------------------------------------------------------------
# atc ace status
# ---------------------------------------------------------------------------


class TestAceStatus:
    def test_reports_working(self, api_stub: str) -> None:
        rc = cli(["ace", "status", "sess-123", "working", "--api", api_stub])
        assert rc == 0
        assert len(_StubHandler.requests) == 1
        req = _StubHandler.requests[0]
        assert req["method"] == "PATCH"
        assert req["path"] == "/api/aces/sess-123/status"
        assert req["body"] == {"status": "working"}

    def test_reports_waiting(self, api_stub: str) -> None:
        rc = cli(["ace", "status", "sess-456", "waiting", "--api", api_stub])
        assert rc == 0
        assert _StubHandler.requests[0]["body"] == {"status": "waiting"}

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli(["ace", "status", "sess-789", "invalid_status"])

    def test_api_error_returns_1(self, api_stub: str) -> None:
        _StubHandler.response_code = 404
        _StubHandler.response_body = {"detail": "Not found"}
        rc = cli(["ace", "status", "sess-bad", "working", "--api", api_stub])
        assert rc == 1

    def test_unreachable_api_returns_1(self) -> None:
        rc = cli(["ace", "status", "sess-x", "working", "--api", "http://127.0.0.1:1"])
        assert rc == 1


# ---------------------------------------------------------------------------
# atc ace done
# ---------------------------------------------------------------------------


class TestAceDone:
    def test_sends_idle_status(self, api_stub: str) -> None:
        rc = cli(["ace", "done", "sess-done-1", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "PATCH"
        assert req["path"] == "/api/aces/sess-done-1/status"
        assert req["body"] == {"status": "idle"}


# ---------------------------------------------------------------------------
# atc ace blocked
# ---------------------------------------------------------------------------


class TestAceBlocked:
    def test_sends_waiting_status(self, api_stub: str) -> None:
        rc = cli(["ace", "blocked", "sess-blk-1", "--api", api_stub])
        assert rc == 0
        # First request: status update to waiting
        req = _StubHandler.requests[0]
        assert req["body"] == {"status": "waiting"}

    def test_sends_notification_with_reason(self, api_stub: str) -> None:
        rc = cli([
            "ace", "blocked", "sess-blk-2",
            "--reason", "PR review needed",
            "--api", api_stub,
        ])
        assert rc == 0
        assert len(_StubHandler.requests) == 2
        notify_req = _StubHandler.requests[1]
        assert notify_req["method"] == "POST"
        assert notify_req["path"] == "/api/aces/sess-blk-2/notify"
        assert "PR review needed" in notify_req["body"]["message"]

    def test_no_reason_skips_notification(self, api_stub: str) -> None:
        rc = cli(["ace", "blocked", "sess-blk-3", "--api", api_stub])
        assert rc == 0
        assert len(_StubHandler.requests) == 1  # Only status, no notify


# ---------------------------------------------------------------------------
# atc ace notify
# ---------------------------------------------------------------------------


class TestAceNotify:
    def test_sends_notification(self, api_stub: str) -> None:
        rc = cli(["ace", "notify", "sess-n-1", "Build complete", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/api/aces/sess-n-1/notify"
        assert req["body"] == {"message": "Build complete"}


# ---------------------------------------------------------------------------
# atc tower status
# ---------------------------------------------------------------------------


class TestTowerStatus:
    def test_gets_status(self, api_stub: str) -> None:
        _StubHandler.response_body = {
            "status": "running",
            "state": "idle",
            "current_goal": None,
        }
        rc = cli(["tower", "status", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "GET"
        assert req["path"] == "/api/tower/status"


# ---------------------------------------------------------------------------
# atc tower cancel
# ---------------------------------------------------------------------------


class TestTowerCancel:
    def test_cancels_goal(self, api_stub: str) -> None:
        rc = cli(["tower", "cancel", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/api/tower/cancel"


# ---------------------------------------------------------------------------
# atc tower memory
# ---------------------------------------------------------------------------


class TestTowerMemory:
    def test_lists_memory(self, api_stub: str) -> None:
        _StubHandler.response_body = []  # type: ignore[assignment]
        rc = cli(["tower", "memory", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "GET"
        assert req["path"] == "/api/tower/memory"


# ---------------------------------------------------------------------------
# atc projects list
# ---------------------------------------------------------------------------


class TestProjectsList:
    def test_lists_projects(self, api_stub: str) -> None:
        _StubHandler.response_body = []  # type: ignore[assignment]
        rc = cli(["projects", "list", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "GET"
        assert req["path"] == "/api/projects"


# ---------------------------------------------------------------------------
# atc projects create
# ---------------------------------------------------------------------------


class TestProjectsCreate:
    def test_creates_project(self, api_stub: str) -> None:
        _StubHandler.response_body = {"id": "proj-1", "name": "Test", "status": "active"}
        rc = cli([
            "projects", "create",
            "--name", "Test Project",
            "--description", "A test project",
            "--api", api_stub,
        ])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/api/projects"
        assert req["body"]["name"] == "Test Project"
        assert req["body"]["description"] == "A test project"

    def test_creates_project_name_only(self, api_stub: str) -> None:
        _StubHandler.response_body = {"id": "proj-2", "name": "Minimal", "status": "active"}
        rc = cli(["projects", "create", "--name", "Minimal", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["body"]["name"] == "Minimal"
        assert "description" not in req["body"]

    def test_name_required(self) -> None:
        with pytest.raises(SystemExit):
            cli(["projects", "create"])


# ---------------------------------------------------------------------------
# atc projects show
# ---------------------------------------------------------------------------


class TestProjectsShow:
    def test_shows_project(self, api_stub: str) -> None:
        _StubHandler.response_body = {"id": "proj-1", "name": "Test", "status": "active"}
        rc = cli(["projects", "show", "proj-1", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "GET"
        assert req["path"] == "/api/projects/proj-1"


# ---------------------------------------------------------------------------
# atc leader start
# ---------------------------------------------------------------------------


class TestLeaderStart:
    def test_starts_leader(self, api_stub: str) -> None:
        _StubHandler.response_body = {"status": "started", "session_id": "sess-1"}
        rc = cli(["leader", "start", "--project-id", "proj-1", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/api/projects/proj-1/leader/start"

    def test_starts_leader_with_goal(self, api_stub: str) -> None:
        _StubHandler.response_body = {"status": "started", "session_id": "sess-2"}
        rc = cli([
            "leader", "start",
            "--project-id", "proj-1",
            "--goal", "Build a calculator",
            "--api", api_stub,
        ])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["body"]["goal"] == "Build a calculator"

    def test_project_id_required(self) -> None:
        with pytest.raises(SystemExit):
            cli(["leader", "start"])


# ---------------------------------------------------------------------------
# atc leader stop
# ---------------------------------------------------------------------------


class TestLeaderStop:
    def test_stops_leader(self, api_stub: str) -> None:
        rc = cli(["leader", "stop", "--project-id", "proj-1", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/api/projects/proj-1/leader/stop"


# ---------------------------------------------------------------------------
# atc ace create
# ---------------------------------------------------------------------------


class TestAceCreate:
    def test_creates_ace(self, api_stub: str) -> None:
        _StubHandler.response_body = {"id": "sess-1", "name": "frontend-ace", "status": "idle"}
        rc = cli([
            "ace", "create",
            "--project-id", "proj-1",
            "--name", "frontend-ace",
            "--api", api_stub,
        ])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/api/projects/proj-1/aces"
        assert req["body"]["name"] == "frontend-ace"

    def test_project_id_and_name_required(self) -> None:
        with pytest.raises(SystemExit):
            cli(["ace", "create"])


# ---------------------------------------------------------------------------
# atc ace list
# ---------------------------------------------------------------------------


class TestAceList:
    def test_lists_aces(self, api_stub: str) -> None:
        _StubHandler.response_body = []  # type: ignore[assignment]
        rc = cli(["ace", "list", "--project-id", "proj-1", "--api", api_stub])
        assert rc == 0
        req = _StubHandler.requests[0]
        assert req["method"] == "GET"
        assert req["path"] == "/api/projects/proj-1/aces"
