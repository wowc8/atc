"""OpenCodeProvider — agent provider using OpenCode REST API.

Communicates with an OpenCode server (``opencode serve`` on localhost:4096)
via its REST API. Sessions are spawned in tmux panes running
``opencode --session-id <id>``, but all control flows through HTTP —
no terminal scraping needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

from atc.agents.base import (
    OutputChunk,
    PromptResult,
    ProviderCapabilities,
    ProviderError,
    SessionInfo,
    SessionStatus,
)

logger = logging.getLogger(__name__)

_TMUX_CMD = "tmux"

# Mapping from OpenCode API status strings to our canonical enum
_STATUS_MAP: dict[str, SessionStatus] = {
    "idle": SessionStatus.IDLE,
    "busy": SessionStatus.BUSY,
    "running": SessionStatus.BUSY,
    "error": SessionStatus.ERROR,
    "stopped": SessionStatus.STOPPED,
}


class OpenCodeProvider:
    """Agent provider backed by the OpenCode REST API.

    Requires an OpenCode server running on ``base_url`` (default localhost:4096).
    Sessions are spawned as ``opencode --session-id <id>`` in tmux panes for
    human visibility, but all commands flow through HTTP endpoints.

    Implements :class:`AgentProvider` protocol.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:4096",
        tmux_session: str = "atc",
        auth_username: str | None = None,
        auth_password: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._tmux_session = tmux_session
        self._auth_username = auth_username
        self._auth_password = auth_password
        self._pane_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "opencode"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_tool_use=True,
            context_window=200_000,
            model="opencode",
        )

    async def ensure_server_running(self) -> None:
        """Start ``opencode serve`` in a detached tmux session if not already running.

        Checks if the server is reachable first. If not, starts it in a
        dedicated tmux session named ``opencode-server``.
        """
        # Check if server is already responding
        try:
            await self._api_request("GET", "/session")
            logger.debug("OpenCode server already running at %s", self._base_url)
            return
        except ProviderError:
            pass

        logger.info("Starting opencode serve at %s", self._base_url)

        if shutil.which(_TMUX_CMD) is None:
            raise ProviderError(self.name, "tmux is not installed or not on PATH")

        server_session = f"{self._tmux_session}-opencode-server"

        # Check if the tmux session already exists
        try:
            proc = await asyncio.create_subprocess_exec(
                _TMUX_CMD,
                "has-session",
                "-t",
                server_session,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                logger.info("tmux session %s already exists, waiting for server", server_session)
            else:
                # Start a new detached tmux session running opencode serve
                serve_cmd = f"opencode serve --port {self._base_url.split(':')[-1]}"
                start_proc = await asyncio.create_subprocess_exec(
                    _TMUX_CMD,
                    "new-session",
                    "-d",
                    "-s",
                    server_session,
                    serve_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await start_proc.communicate()
                if start_proc.returncode != 0:
                    err = stderr.decode().strip()
                    raise ProviderError(self.name, f"Failed to start opencode serve: {err}")
                logger.info("Started opencode serve in tmux session %s", server_session)
        except OSError as exc:
            raise ProviderError(self.name, f"tmux command failed: {exc}") from exc

        # Wait for the server to become responsive (up to 15 seconds)
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                await self._api_request("GET", "/session")
                logger.info("OpenCode server is ready at %s", self._base_url)
                return
            except ProviderError:
                continue

        raise ProviderError(self.name, "OpenCode server did not start within 15 seconds")

    async def spawn_session(
        self,
        session_id: str,
        *,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SessionInfo:
        if session_id in self._pane_ids:
            raise ProviderError(self.name, f"Session {session_id} already tracked")

        # Ensure the OpenCode server is running before creating sessions
        await self.ensure_server_running()

        # Create session via REST API
        body: dict[str, Any] = {"id": session_id}
        if working_dir:
            body["working_dir"] = working_dir
        await self._api_request("POST", "/session", body=body)

        # Also spawn a tmux pane for human observability
        pane_id = await self._spawn_tmux_pane(session_id, working_dir, env)
        self._pane_ids[session_id] = pane_id

        logger.info(
            "Spawned OpenCode session %s (pane %s, server %s)",
            session_id,
            pane_id,
            self._base_url,
        )
        return SessionInfo(
            session_id=session_id,
            status=SessionStatus.IDLE,
            metadata={"pane_id": pane_id, "base_url": self._base_url},
        )

    async def send_prompt(self, session_id: str, prompt: str) -> PromptResult:
        self._require_tracked(session_id)

        try:
            resp = await self._api_request(
                "POST",
                "/prompt_async",
                body={"session_id": session_id, "prompt": prompt},
            )
        except ProviderError:
            return PromptResult(
                session_id=session_id,
                accepted=False,
                message="API request failed",
            )

        logger.info("Sent prompt to OpenCode session %s", session_id)
        return PromptResult(
            session_id=session_id,
            accepted=True,
            metadata=resp,
        )

    async def get_status(self, session_id: str) -> SessionInfo:
        self._require_tracked(session_id)

        try:
            resp = await self._api_request("GET", f"/session/{session_id}")
        except ProviderError:
            return SessionInfo(
                session_id=session_id,
                status=SessionStatus.ERROR,
                metadata={"error": "Failed to reach OpenCode API"},
            )

        api_status = resp.get("status", "idle")
        status = _STATUS_MAP.get(api_status, SessionStatus.IDLE)
        return SessionInfo(
            session_id=session_id,
            status=status,
            metadata=resp,
        )

    async def stream_output(self, session_id: str) -> AsyncIterator[OutputChunk]:
        """Stream output via Server-Sent Events from the OpenCode API."""
        self._require_tracked(session_id)

        url = f"{self._base_url}/session/{session_id}/events"
        headers = self._auth_headers()
        headers["Accept"] = "text/event-stream"

        try:
            proc = await asyncio.create_subprocess_exec(
                "curl",
                "-sN",
                *self._curl_auth_args(),
                "-H",
                "Accept: text/event-stream",
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ProviderError(self.name, f"Failed to start SSE stream: {exc}") from exc

        assert proc.stdout is not None  # noqa: S101
        try:
            async for line_bytes in proc.stdout:
                line = line_bytes.decode().strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = {"raw": data_str}

                    content = data.get("content", data.get("text", data_str))
                    is_final = data.get("done", False) is True

                    yield OutputChunk(
                        session_id=session_id,
                        content=str(content),
                        is_final=is_final,
                        metadata=data,
                    )
                    if is_final:
                        break
        finally:
            proc.terminate()
            await proc.wait()

    async def stop_session(self, session_id: str) -> None:
        self._require_tracked(session_id)

        # Stop via API
        try:
            await self._api_request("DELETE", f"/session/{session_id}")
        except ProviderError:
            logger.warning("API delete for session %s failed, cleaning up locally", session_id)

        # Kill the tmux pane
        pane_id = self._pane_ids.get(session_id)
        if pane_id:
            try:
                proc = await asyncio.create_subprocess_exec(
                    _TMUX_CMD,
                    "kill-pane",
                    "-t",
                    pane_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except OSError:
                pass

        del self._pane_ids[session_id]
        logger.info("Stopped OpenCode session %s", session_id)

    async def list_sessions(self) -> list[SessionInfo]:
        try:
            resp = await self._api_request("GET", "/session")
        except ProviderError:
            logger.warning("Failed to list sessions from OpenCode API")
            return []

        sessions: list[SessionInfo] = []
        items = resp if isinstance(resp, list) else resp.get("sessions", [])
        for item in items:
            sid = item.get("id", "")
            api_status = item.get("status", "idle")
            status = _STATUS_MAP.get(api_status, SessionStatus.IDLE)
            sessions.append(SessionInfo(session_id=sid, status=status, metadata=item))
        return sessions

    async def prepare_workspace(
        self,
        session_id: str,
        *,
        working_dir: str,
        context_file: Path | None = None,
    ) -> None:
        """No-op for OpenCode — workspace prep is handled by the server."""

    async def is_ready(self, session_id: str) -> bool:
        """Return True when the session is tracked and not in error state."""
        try:
            info = await self.get_status(session_id)
            return info.status not in (SessionStatus.ERROR, SessionStatus.STOPPED)
        except ProviderError:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_tracked(self, session_id: str) -> None:
        if session_id not in self._pane_ids:
            raise ProviderError(self.name, f"Session {session_id} not tracked")

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._auth_username and self._auth_password:
            import base64

            creds = base64.b64encode(
                f"{self._auth_username}:{self._auth_password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    def _curl_auth_args(self) -> list[str]:
        if self._auth_username and self._auth_password:
            return ["-u", f"{self._auth_username}:{self._auth_password}"]
        return []

    async def _api_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request to the OpenCode API using curl.

        Uses curl subprocess to avoid adding httpx as a runtime dependency.
        """
        url = f"{self._base_url}{path}"
        cmd: list[str] = ["curl", "-s", "-X", method]
        cmd.extend(self._curl_auth_args())
        cmd.extend(["-H", "Content-Type: application/json"])

        if body is not None:
            cmd.extend(["-d", json.dumps(body)])
        cmd.append(url)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"HTTP {method} {path} failed: {exc}") from exc

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise ProviderError(self.name, f"HTTP {method} {path} error: {err}")

        raw = stdout.decode().strip()
        if not raw:
            return {}
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                self.name, f"Invalid JSON from {method} {path}: {raw[:200]}"
            ) from exc

        if isinstance(result, dict):
            return result
        return {"data": result}

    async def _spawn_tmux_pane(
        self,
        session_id: str,
        working_dir: str | None,
        env: dict[str, str] | None,
    ) -> str:
        """Spawn an ``opencode --session-id`` process in a tmux pane."""
        if shutil.which(_TMUX_CMD) is None:
            raise ProviderError(self.name, "tmux is not installed or not on PATH")

        env_prefix = ""
        if env:
            env_parts = [f"{k}={v}" for k, v in env.items()]
            env_prefix = " ".join(env_parts) + " "

        shell_cmd = f"{env_prefix}opencode --session-id {session_id}"

        tmux_args = [
            _TMUX_CMD,
            "new-window",
            "-t",
            self._tmux_session,
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
        ]
        if working_dir:
            tmux_args.extend(["-c", working_dir])
        tmux_args.append(shell_cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *tmux_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"tmux spawn failed: {exc}") from exc

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise ProviderError(self.name, f"tmux new-window failed: {err}")

        return stdout.decode().strip()
