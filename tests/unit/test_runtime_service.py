from __future__ import annotations

import asyncio
from types import SimpleNamespace

import atc.runtime.service as runtime_service_module
from atc.providers.registry import register_provider_runtime
from atc.runtime.models import (
    InstructionRequest,
    ReadinessResult,
    ReadinessState,
    RoleKind,
    RuntimeBlockReason,
    RuntimeInspection,
    RuntimeSessionHandle,
    RuntimeTransport,
    StartRoleRequest,
    StopRoleRequest,
    TaskAssignmentRequest,
)
from atc.runtime.service import RuntimeService
from atc.state.db import clear_connection_app_state, set_connection_app_state

runtime_service_module._STARTUP_INITIAL_DELAY_SECONDS = 0.0
runtime_service_module._STARTUP_RESOLVE_DELAY_SECONDS = 0.0
runtime_service_module._STARTUP_FINAL_DELAY_SECONDS = 0.0


class DummyProviderRuntime:
    provider_name = "dummy_runtime"

    def __init__(self) -> None:
        self.prepared: list[StartRoleRequest] = []
        self.started: list[StartRoleRequest] = []
        self.spawned_existing: list[StartRoleRequest] = []
        self.instructions: list[InstructionRequest] = []
        self.assignments: list[TaskAssignmentRequest] = []
        self.stopped: list[tuple[RuntimeSessionHandle, StopRoleRequest | None]] = []

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
        self.stopped.append((handle, request))
        return None

    async def send_instruction(
        self, handle: RuntimeSessionHandle, request: InstructionRequest
    ) -> None:
        self.instructions.append(request)

    async def assign_task(
        self, handle: RuntimeSessionHandle, request: TaskAssignmentRequest
    ) -> None:
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
    first_settings = SimpleNamespace(
        agent_provider=SimpleNamespace(
            tmux_session="atc", claude_command="claude", codex_command="codex"
        )
    )
    second_settings = SimpleNamespace(
        agent_provider=SimpleNamespace(
            tmux_session="atc", claude_command="claude", codex_command="codex --profile prod"
        )
    )
    first_conn = SimpleNamespace(
        _connection=SimpleNamespace(app_state=SimpleNamespace(settings=first_settings))
    )
    second_conn = SimpleNamespace(
        _connection=SimpleNamespace(app_state=SimpleNamespace(settings=second_settings))
    )

    first = service._get_provider_runtime("codex", first_conn)
    second = service._get_provider_runtime("codex", second_conn)

    assert first is not second
    assert second.codex_command == "codex --profile prod"


def test_service_refreshes_cached_provider_when_live_settings_change_via_sqlite_connection() -> (
    None
):
    service = RuntimeService()
    first_settings = SimpleNamespace(
        agent_provider=SimpleNamespace(
            tmux_session="atc", claude_command="claude", codex_command="codex"
        )
    )
    second_settings = SimpleNamespace(
        agent_provider=SimpleNamespace(
            tmux_session="atc", claude_command="claude", codex_command="codex --profile prod"
        )
    )
    sqlite_conn = SimpleNamespace()
    first_conn = SimpleNamespace(_connection=sqlite_conn)
    sqlite_conn.app_state = SimpleNamespace(settings=first_settings)

    first = service._get_provider_runtime("codex", first_conn)

    sqlite_conn.app_state = SimpleNamespace(settings=second_settings)
    second = service._get_provider_runtime("codex", first_conn)

    assert first is not second
    assert second.codex_command == "codex --profile prod"


def test_runtime_service_spawn_existing_session_adds_trace_events() -> None:
    register_provider_runtime("dummy_runtime_trace_spawn", DummyProviderRuntime)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-trace-spawn-1",
        provider_name="dummy_runtime_trace_spawn",
        role=RoleKind.LEADER,
        project_id="proj-trace",
    )

    asyncio.run(service.spawn_existing_session(request))

    events = request.metadata["delivery_trace_events"]
    assert [event["stage"] for event in events[:3]] == ["queued", "spawn_started", "spawned"]
    assert events[-1]["action"] == "spawn"
    assert events[-1]["verdict"] == "accepted"
    assert events[-1]["reason_code"] == "session_running"
    assert events[-1]["details"]["startup_phase"] == "final"


def test_runtime_service_startup_handshake_traces_blocked_auth_without_auto_answer() -> None:
    class AuthBlockedProvider(DummyProviderRuntime):
        async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=handle.provider_name,
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.AUTH,
                summary="Blocked on authentication",
            )

    register_provider_runtime("dummy_runtime_auth_blocked", AuthBlockedProvider)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-auth-blocked-1",
        provider_name="dummy_runtime_auth_blocked",
        role=RoleKind.LEADER,
    )

    asyncio.run(service.spawn_existing_session(request))

    events = request.metadata["delivery_trace_events"]
    assert events[-1]["stage"] == "blocked"
    assert events[-1]["verdict"] == "blocked"
    assert events[-1]["reason_code"] == "auth_required"
    assert events[-1]["details"]["startup_phase"] == "final"


def test_runtime_service_startup_handshake_auto_resolves_provider_declared_safe_trust() -> None:
    resolved: list[str] = []

    class TrustThenReadyProvider(DummyProviderRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.inspect_count = 0

        async def spawn_existing_session(self, request: StartRoleRequest) -> RuntimeSessionHandle:
            self.spawned_existing.append(request)
            return RuntimeSessionHandle(
                session_id=request.session_id,
                provider_name=request.provider_name,
                role=request.role,
                transport=RuntimeTransport.TMUX,
                tmux_pane="%42",
            )

        async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
            self.inspect_count += 1
            if self.inspect_count == 1:
                return RuntimeInspection(
                    session_id=handle.session_id,
                    provider_name=handle.provider_name,
                    alive=True,
                    readiness=ReadinessState.BLOCKED,
                    block_reason=RuntimeBlockReason.TRUST,
                    summary="Blocked on trust prompt",
                    details={
                        "provider_diagnostics": {"safe_to_auto_resolve": True},
                        "recovery_capabilities": {
                            "can_auto_accept_managed_workspace_trust_prompt": True
                        },
                    },
                )
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=handle.provider_name,
                alive=True,
                readiness=ReadinessState.READY,
                summary="Ready",
            )

        async def resolve_startup_prompt(
            self,
            handle: RuntimeSessionHandle,
            inspection: RuntimeInspection,
        ) -> bool:
            resolved.append(handle.session_id)
            return True

    register_provider_runtime("dummy_runtime_trust_then_ready", TrustThenReadyProvider)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-trust-1",
        provider_name="dummy_runtime_trust_then_ready",
        role=RoleKind.ACE,
    )

    asyncio.run(service.spawn_existing_session(request))
    assert resolved == ["sess-trust-1"]
    phases = [
        event["details"].get("startup_phase")
        for event in request.metadata["delivery_trace_events"]
    ]
    assert "initial" in phases
    assert "after_auto_resolve" in phases
    assert "final" in phases


def test_runtime_service_startup_handshake_refuses_unsafe_trust() -> None:
    resolved: list[str] = []

    class UnsafeTrustProvider(DummyProviderRuntime):
        async def spawn_existing_session(self, request: StartRoleRequest) -> RuntimeSessionHandle:
            self.spawned_existing.append(request)
            return RuntimeSessionHandle(
                session_id=request.session_id,
                provider_name=request.provider_name,
                role=request.role,
                transport=RuntimeTransport.TMUX,
                tmux_pane="%43",
            )

        async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=handle.provider_name,
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.TRUST,
                summary="Blocked on unsafe trust prompt",
                details={
                    "provider_diagnostics": {"safe_to_auto_resolve": False},
                    "recovery_capabilities": {
                        "can_auto_accept_managed_workspace_trust_prompt": True
                    },
                },
            )

        async def resolve_startup_prompt(
            self,
            handle: RuntimeSessionHandle,
            inspection: RuntimeInspection,
        ) -> bool:
            resolved.append(handle.session_id)
            return True

    register_provider_runtime("dummy_runtime_unsafe_trust", UnsafeTrustProvider)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-unsafe-trust-1",
        provider_name="dummy_runtime_unsafe_trust",
        role=RoleKind.ACE,
    )

    asyncio.run(service.spawn_existing_session(request))

    assert resolved == []
    events = request.metadata["delivery_trace_events"]
    assert events[-1]["stage"] == "blocked"
    assert events[-1]["reason_code"] == "trust_required"
    phases = [event["details"].get("startup_phase") for event in events]
    assert "after_auto_resolve" not in phases


def test_runtime_service_startup_handshake_requires_provider_capability_for_trust() -> None:
    resolved: list[str] = []

    class NoCapabilityTrustProvider(DummyProviderRuntime):
        async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=handle.provider_name,
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.TRUST,
                summary="Blocked on trust prompt",
                details={"provider_diagnostics": {"safe_to_auto_resolve": True}},
            )

        async def resolve_startup_prompt(
            self,
            handle: RuntimeSessionHandle,
            inspection: RuntimeInspection,
        ) -> bool:
            resolved.append(handle.session_id)
            return True

    register_provider_runtime("dummy_runtime_no_capability_trust", NoCapabilityTrustProvider)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-no-capability-trust-1",
        provider_name="dummy_runtime_no_capability_trust",
        role=RoleKind.LEADER,
    )

    asyncio.run(service.spawn_existing_session(request))

    assert resolved == []
    assert request.metadata["delivery_trace_events"][-1]["stage"] == "blocked"


def test_runtime_service_startup_handshake_traces_ready_no_prompt_branch() -> None:
    register_provider_runtime("dummy_runtime_ready_no_prompt", DummyProviderRuntime)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-ready-no-prompt-1",
        provider_name="dummy_runtime_ready_no_prompt",
        role=RoleKind.LEADER,
    )

    asyncio.run(service.spawn_existing_session(request))

    events = request.metadata["delivery_trace_events"]
    assert events[-1]["details"]["startup_phase"] == "final"
    assert events[-1]["reason_code"] == "session_running"


def test_runtime_service_startup_handshake_never_auto_resolves_permission() -> None:
    resolved: list[str] = []

    class PermissionBlockedProvider(DummyProviderRuntime):
        async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=handle.provider_name,
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.PERMISSION,
                summary="Blocked on permission prompt",
                details={
                    "provider_diagnostics": {"safe_to_auto_resolve": True},
                    "recovery_capabilities": {
                        "can_auto_accept_managed_workspace_trust_prompt": True
                    },
                },
            )

        async def resolve_startup_prompt(
            self,
            handle: RuntimeSessionHandle,
            inspection: RuntimeInspection,
        ) -> bool:
            resolved.append(handle.session_id)
            return True

    register_provider_runtime("dummy_runtime_permission_blocked", PermissionBlockedProvider)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-permission-blocked-1",
        provider_name="dummy_runtime_permission_blocked",
        role=RoleKind.LEADER,
    )

    asyncio.run(service.spawn_existing_session(request))

    assert resolved == []
    event = request.metadata["delivery_trace_events"][-1]
    assert event["stage"] == "blocked"
    assert event["reason_code"] == "permission_required"


def test_runtime_service_send_instruction_adds_queued_trace_event() -> None:
    register_provider_runtime("dummy_runtime_trace_instruction", DummyProviderRuntime)
    service = RuntimeService()
    handle = asyncio.run(
        service.start_ace(
            StartRoleRequest(
                session_id="sess-trace-instruction-1",
                provider_name="dummy_runtime_trace_instruction",
                role=RoleKind.ACE,
            )
        )
    )
    request = InstructionRequest(
        session_id="sess-trace-instruction-1",
        message="trace this",
        instruction_id="instruction-1",
    )

    asyncio.run(service.send_instruction(handle, request))

    events = request.metadata["delivery_trace_events"]
    assert events[0]["stage"] == "queued"
    assert events[0]["action"] == "instruction"
    assert events[0]["details"] == {"instruction_id": "instruction-1"}


def test_runtime_service_assign_task_to_ace_preserves_task_assignment_trace() -> None:
    register_provider_runtime("dummy_runtime_trace_assignment", DummyProviderRuntime)
    service = RuntimeService()
    handle = asyncio.run(
        service.start_ace(
            StartRoleRequest(
                session_id="sess-trace-assignment-1",
                provider_name="dummy_runtime_trace_assignment",
                role=RoleKind.ACE,
            )
        )
    )
    request = TaskAssignmentRequest(
        session_id="sess-trace-assignment-1",
        task_id="task-1",
        assignment_id="assignment-1",
        message="do the task",
    )

    asyncio.run(service.assign_task_to_ace(handle, request))

    events = request.metadata["delivery_trace_events"]
    assert events[0]["action"] == "task_assignment"
    assert events[0]["stage"] == "queued"
    assert events[0]["details"] == {
        "task_id": "task-1",
        "assignment_id": "assignment-1",
    }
    provider = service.get_provider("dummy_runtime_trace_assignment")
    assert provider.assignments[-1].metadata is request.metadata


def test_runtime_service_provider_settings_use_connection_app_state_side_map() -> None:
    service = RuntimeService()
    conn = SimpleNamespace()
    settings = SimpleNamespace(
        agent_provider=SimpleNamespace(
            codex_command="codex-custom",
            claude_command="claude",
            tmux_session="atc-side-map",
        )
    )
    app_state = SimpleNamespace(settings=settings)
    set_connection_app_state(conn, app_state)
    try:
        first = service._get_provider_runtime("codex", conn)
        settings.agent_provider.codex_command = "codex-updated"
        second = service._get_provider_runtime("codex", conn)
    finally:
        clear_connection_app_state(conn)

    assert first is not second
    assert first.codex_command == "codex-custom"
    assert second.codex_command == "codex-updated"


def test_delivery_exception_permission_maps_to_permission_required() -> None:
    from atc.runtime.service import RuntimeService
    from atc.runtime.tracing import DeliveryReasonCode

    assert (
        RuntimeService._reason_code_for_delivery_exception(
            Exception("Instruction blocked by runtime interrupt: permission_prompt")
        )
        is DeliveryReasonCode.PERMISSION_REQUIRED
    )


def test_runtime_service_send_instruction_returns_delivery_result() -> None:
    register_provider_runtime("dummy_runtime_result_instruction", DummyProviderRuntime)
    service = RuntimeService()
    handle = asyncio.run(
        service.start_leader(
            StartRoleRequest(
                session_id="sess-result-leader-1",
                provider_name="dummy_runtime_result_instruction",
                role=RoleKind.LEADER,
            )
        )
    )
    request = InstructionRequest(session_id=handle.session_id, message="hello")

    result = asyncio.run(service.assign_project_to_leader(handle, request))

    assert result.session_id == "sess-result-leader-1"
    assert result.provider_name == "dummy_runtime_result_instruction"
    assert result.role is RoleKind.LEADER
    assert result.status == "delivered"
    assert result.stage == "confirmed_running"
    assert result.verdict == "accepted"
    assert result.reason_code == "delivery_unverified"
    assert result.as_dict()["status"] == "delivered"


def test_runtime_service_handle_from_session_record_maps_manager_and_stops_provider() -> None:
    register_provider_runtime("dummy_runtime_record_stop", DummyProviderRuntime)
    service = RuntimeService()
    session = SimpleNamespace(
        id="sess-record-manager-1",
        provider="dummy_runtime_record_stop",
        session_type="manager",
        project_id="proj-record",
        tmux_session="atc",
        tmux_pane="%42",
    )

    handle = service.handle_from_session_record(session)
    asyncio.run(
        service.stop_session_record(session, StopRoleRequest(reason="test-stop", graceful=True))
    )

    provider = service.get_provider("dummy_runtime_record_stop")
    stopped_handle, stop_request = provider.stopped[-1]
    assert handle.role is RoleKind.LEADER
    assert stopped_handle.session_id == "sess-record-manager-1"
    assert stopped_handle.tmux_pane == "%42"
    assert stop_request.reason == "test-stop"


def test_runtime_service_inspect_session_record_uses_provider_boundary() -> None:
    register_provider_runtime("dummy_runtime_record_inspect", DummyProviderRuntime)
    service = RuntimeService()
    session = SimpleNamespace(
        id="sess-record-ace-1",
        provider="dummy_runtime_record_inspect",
        session_type="worker",
        project_id="proj-record",
        tmux_session="atc",
        tmux_pane="%99",
    )

    inspection = asyncio.run(service.inspect_session_record(session))

    assert inspection.session_id == "sess-record-ace-1"
    assert inspection.provider_name == "dummy_runtime_record_inspect"
    assert inspection.alive is True
    assert service.get_handle("sess-record-ace-1").role is RoleKind.ACE


def test_runtime_service_assign_task_blocks_until_ace_input_ready() -> None:
    class TrustBlockedProvider(DummyProviderRuntime):
        async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=handle.provider_name,
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.TRUST,
                summary="Do you trust this directory?",
            )

    register_provider_runtime("dummy_runtime_ace_startup_blocked", TrustBlockedProvider)
    service = RuntimeService()
    request = StartRoleRequest(
        session_id="sess-ace-startup-blocked-1",
        provider_name="dummy_runtime_ace_startup_blocked",
        role=RoleKind.ACE,
    )
    handle = asyncio.run(service.start_ace(request))
    assignment = TaskAssignmentRequest(
        session_id="sess-ace-startup-blocked-1",
        task_id="task-1",
        message="Do the work",
        assignment_id="assign-startup-blocked-1",
    )
    result = asyncio.run(service.assign_task_to_ace(handle, assignment))

    provider = service.get_provider("dummy_runtime_ace_startup_blocked")
    assert provider.assignments == []
    assert result.status == "blocked"
    assert result.blocker_reason.value == "runtime_trust_required"
    assert result.details["startup_readiness_state"] == "awaiting_startup_confirmation"
