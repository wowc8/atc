from __future__ import annotations

import asyncio
from types import SimpleNamespace

from atc.providers.registry import register_provider_runtime
from atc.runtime.models import (
    InstructionRequest,
    ReadinessResult,
    ReadinessState,
    RoleKind,
    RuntimeInspection,
    RuntimeSessionHandle,
    RuntimeTransport,
    StartRoleRequest,
    TaskAssignmentRequest,
)
from atc.runtime.service import RuntimeService


class DummyProviderRuntime:
    provider_name = "dummy_runtime"

    def __init__(self) -> None:
        self.prepared: list[StartRoleRequest] = []
        self.started: list[StartRoleRequest] = []
        self.spawned_existing: list[StartRoleRequest] = []
        self.instructions: list[InstructionRequest] = []
        self.assignments: list[TaskAssignmentRequest] = []

    async def prepare_workspace(self, request: StartRoleRequest) -> None:
        self.prepared.append(request)

    async def start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        self.started.append(request)
        return RuntimeSessionHandle(
            session_id=request.session_id,
            provider_name=request.provider_name,
            role=request.role,
            transport=RuntimeTransport.TMUX,
            project_id=request.project_id,
            working_dir=request.working_dir,
            context_ref=request.context_ref,
        )

    async def spawn_existing_session(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        self.spawned_existing.append(request)
        return RuntimeSessionHandle(
            session_id=request.session_id,
            provider_name=request.provider_name,
            role=request.role,
            transport=RuntimeTransport.TMUX,
            project_id=request.project_id,
            working_dir=request.working_dir,
            context_ref=request.context_ref,
        )

    async def stop_role(self, handle: RuntimeSessionHandle, request=None) -> None:
        return None

    async def send_instruction(self, handle: RuntimeSessionHandle, request: InstructionRequest) -> None:
        self.instructions.append(request)

    async def assign_task(self, handle: RuntimeSessionHandle, request: TaskAssignmentRequest) -> None:
        self.assignments.append(request)

    async def check_readiness(self, handle: RuntimeSessionHandle) -> ReadinessResult:
        return ReadinessResult(
            session_id=handle.session_id,
            provider_name=handle.provider_name,
            state=ReadinessState.READY,
        )

    async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
        return RuntimeInspection(
            session_id=handle.session_id,
            provider_name=handle.provider_name,
            alive=True,
            readiness=ReadinessState.READY,
        )

    async def restore_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
        return RuntimeInspection(
            session_id=handle.session_id,
            provider_name=handle.provider_name,
            alive=True,
            readiness=ReadinessState.READY,
        )


def test_runtime_service_prepare_workspace_uses_provider() -> None:
    register_provider_runtime("dummy_runtime_prepare", DummyProviderRuntime)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-prep-1",
        provider_name="dummy_runtime_prepare",
        role=RoleKind.LEADER,
        working_dir="/tmp/repo",
    )

    asyncio.run(service.prepare_workspace(request))

    provider = service.get_provider("dummy_runtime_prepare")
    assert provider.prepared[-1].session_id == "sess-prep-1"


def test_runtime_service_start_tower_remembers_handle() -> None:
    register_provider_runtime("dummy_runtime", DummyProviderRuntime)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-tower-1",
        provider_name="dummy_runtime",
        role=RoleKind.TOWER,
        project_id="proj-1",
    )

    handle = asyncio.run(service.start_tower(request))

    assert handle.session_id == "sess-tower-1"
    assert service.get_handle("sess-tower-1").role is RoleKind.TOWER


def test_runtime_service_spawn_existing_session_uses_provider() -> None:
    register_provider_runtime("dummy_runtime_existing", DummyProviderRuntime)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-existing-1",
        provider_name="dummy_runtime_existing",
        role=RoleKind.LEADER,
        project_id="proj-2",
    )

    handle = asyncio.run(service.spawn_existing_session(request))

    provider = service.get_provider("dummy_runtime_existing")
    assert provider.spawned_existing[-1].session_id == "sess-existing-1"
    assert service.get_handle("sess-existing-1").role is RoleKind.LEADER
    assert handle.session_id == "sess-existing-1"


def test_runtime_service_assign_task_to_ace_uses_provider() -> None:
    register_provider_runtime("dummy_runtime_ace", DummyProviderRuntime)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-ace-1",
        provider_name="dummy_runtime_ace",
        role=RoleKind.ACE,
    )

    handle = asyncio.run(service.start_ace(request))
    assignment = TaskAssignmentRequest(
        session_id="sess-ace-1",
        task_id="task-1",
        message="Do the work",
    )
    asyncio.run(service.assign_task_to_ace(handle, assignment))

    provider = service.get_provider("dummy_runtime_ace")
    assert provider.assignments[-1].task_id == "task-1"



def test_service_refreshes_cached_provider_when_live_settings_change() -> None:
    service = RuntimeService()
    first_settings = SimpleNamespace(agent_provider=SimpleNamespace(tmux_session="atc", claude_command="claude", codex_command="codex"))
    second_settings = SimpleNamespace(agent_provider=SimpleNamespace(tmux_session="atc", claude_command="claude", codex_command="codex --profile prod"))
    first_conn = SimpleNamespace(_connection=SimpleNamespace(app_state=SimpleNamespace(settings=first_settings)))
    second_conn = SimpleNamespace(_connection=SimpleNamespace(app_state=SimpleNamespace(settings=second_settings)))

    first = service._get_provider_runtime("codex", first_conn)
    second = service._get_provider_runtime("codex", second_conn)

    assert first is not second
    assert getattr(second, "codex_command") == "codex --profile prod"



def test_service_refreshes_cached_provider_when_live_settings_change_via_sqlite_connection() -> None:
    service = RuntimeService()
    first_settings = SimpleNamespace(agent_provider=SimpleNamespace(tmux_session="atc", claude_command="claude", codex_command="codex"))
    second_settings = SimpleNamespace(agent_provider=SimpleNamespace(tmux_session="atc", claude_command="claude", codex_command="codex --profile prod"))
    sqlite_conn = SimpleNamespace()
    first_conn = SimpleNamespace(_connection=sqlite_conn)
    sqlite_conn.app_state = SimpleNamespace(settings=first_settings)

    first = service._get_provider_runtime("codex", first_conn)

    sqlite_conn.app_state = SimpleNamespace(settings=second_settings)
    second = service._get_provider_runtime("codex", first_conn)

    assert first is not second
    assert getattr(second, "codex_command") == "codex --profile prod"
