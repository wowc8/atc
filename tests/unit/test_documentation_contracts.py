from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def read_doc(relative_path: str) -> str:
    return (DOCS / relative_path).read_text(encoding="utf-8")


def assert_contains_all(text: str, required: list[str]) -> None:
    missing = [item for item in required if item not in text]
    assert missing == []


def test_api_docs_cover_leader_health_recovery_and_task_cli_contracts() -> None:
    api = read_doc("API.md")

    assert_contains_all(
        api,
        [
            "GET    /api/projects/{id}/leader/health",
            "POST   /api/projects/{id}/leader/recover",
            "POST   /api/projects/{id}/leader/report-active",
            "operator_guidance",
            "leader_state",
            "recovery_recommendation",
            "recommended_command",
            "provider_diagnostics",
            "atc leader health --project-id",
            "atc leader recover --project-id",
            "atc tasks create --project-id",
            "atc tasks assign --project-id",
            "atc leader bootstrap-tasks --project-id",
            "GET    /api/projects/{id}/aces/{ace_id}/health",
            "POST   /api/projects/{id}/aces/{ace_id}/report-active",
            "POST   /api/projects/{id}/aces/{ace_id}/report-artifact",
            "startup_readiness_state",
            "assignment_acceptance_state",
            "artifact_ready",
            "atc ace report-active --project-id",
            "atc ace report-artifact --project-id",
        ],
    )


def test_role_docs_encode_runtime_truth_and_provider_boundary() -> None:
    tower = read_doc("agents/TOWER.md")
    leader = read_doc("agents/LEADER.md")
    ace = read_doc("agents/ACE.md")

    assert_contains_all(
        tower,
        [
            "kickoff_verified",
            "operator_guidance",
            "Leader session row is not proof",
            "provider-neutral",
            "Tower must not paste provider-specific key sequences",
        ],
    )
    assert_contains_all(
        leader,
        [
            "atc tasks create --project-id",
            "atc tasks assign --project-id",
            "atc leader bootstrap-tasks --project-id",
            "report-active",
            "local ATC API helper",
            "do not inspect OpenAPI as the first move",
            "assignment_acceptance_state",
            "startup_readiness_state",
            "input_ready",
            "atc ace report-active --project-id",
            "atc ace report-artifact --project-id",
            "session row or assignment row exists",
        ],
    )
    assert_contains_all(
        ace,
        [
            "dispatch_verified",
            "runtime_state",
            "delivery_state",
            "assignment_acceptance_state",
            "ace_reported_active",
            "assignment_accepted",
            "startup_readiness_state",
            "artifact_ready",
            "artifact_path",
            "awaiting_ace_active_report",
            "Leader owns recovery decisions",
            "provider-neutral blocker",
        ],
    )


def test_leader_recovery_plan_has_phase9_doc_contract_status() -> None:
    plan = read_doc("leader_kickoff_recovery_plan.md")

    assert_contains_all(
        plan,
        [
            "## Phase 9 — Contract/documentation convergence",
            "Docs and role contracts encode the same runtime truth rules",
            "Documentation contract tests pass",
            "normal monitoring still requires kickoff/task-graph truth",
        ],
    )


def test_provider_subagent_contract_keeps_tower_busywork_hidden() -> None:
    contract = read_doc("provider_subagent_contract.md")
    tower = read_doc("agents/TOWER.md")

    assert_contains_all(
        contract,
        [
            "Hidden Tower busywork",
            "Tower kickoff/recovery helper requests must use hidden visibility",
            "Codex `/subagent`",
            "tower_kickoff_supervision",
            "tower_recovery_supervision",
            "Provider-specific mechanics",
        ],
    )
    assert_contains_all(
        tower,
        [
            "Tower-owned hidden provider helper subagents",
            "busywork",
            "aggregate status/escalations",
        ],
    )
