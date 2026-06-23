from __future__ import annotations

from atc.orchestration.boundaries import evaluate_boundary


def test_tower_cannot_manage_ace_without_break_glass() -> None:
    decision = evaluate_boundary(
        caller_role="tower",
        action="ace.health",
        target_role="ace",
    )

    assert not decision.allowed
    assert decision.reason == "tower_must_delegate_ace_operations_to_leader"


def test_leader_can_manage_ace_by_default() -> None:
    decision = evaluate_boundary(
        caller_role="leader",
        action="ace.health",
        target_role="ace",
    )

    assert decision.allowed
    assert not decision.break_glass


def test_tower_break_glass_requires_reason() -> None:
    decision = evaluate_boundary(
        caller_role="tower",
        action="ace.recover",
        target_role="ace",
        break_glass_approved=True,
        break_glass_reason="",
    )

    assert not decision.allowed


def test_tower_break_glass_allows_audited_exception() -> None:
    decision = evaluate_boundary(
        caller_role="tower",
        action="tasks.assign",
        target_role="ace",
        break_glass_approved=True,
        break_glass_reason="operator requested direct inspection",
    )

    assert decision.allowed
    assert decision.break_glass
    assert decision.reason == "tower_break_glass_approved"
