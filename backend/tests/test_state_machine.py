"""Unit tests for core/state_machine.py — docs/03-agent-design.md §5 transition matrix."""

import pytest

from app.core.state_machine import ALL_ROLES, can_transition

LEGAL_AGENT_TRANSITIONS = {
    ("backlog", "todo", "pm"),
    ("in_progress", "review", "engineer"),
    ("in_progress", "review", "designer"),
    ("in_progress", "done", "pm"),
    ("review", "qa", "lead"),
    ("review", "in_progress", "lead"),
    ("qa", "security", "qa"),
    ("qa", "in_progress", "qa"),
    ("security", "done", "pentester"),
    ("security", "in_progress", "pentester"),
}


@pytest.mark.parametrize("from_status,to_status,role", sorted(LEGAL_AGENT_TRANSITIONS))
def test_legal_agent_transitions(from_status, to_status, role):
    allowed, reason = can_transition(from_status, to_status, role)
    assert allowed, reason


@pytest.mark.parametrize(
    "from_status", sorted({"backlog", "todo", "in_progress", "review", "qa", "security", "done"})
)
def test_any_role_may_block(from_status):
    for role in ALL_ROLES:
        allowed, _ = can_transition(from_status, "blocked", role)
        assert allowed


def test_wrong_role_for_transition_rejected():
    allowed, reason = can_transition("review", "qa", "engineer")
    assert not allowed
    assert "review" in reason and "qa" in reason and "engineer" in reason


@pytest.mark.parametrize(
    "from_status,to_status,role",
    [
        ("backlog", "todo", "engineer"),
        ("backlog", "todo", "lead"),
        ("in_progress", "review", "pm"),
        ("in_progress", "review", "qa"),
        ("review", "qa", "engineer"),
        ("review", "qa", "pm"),
        ("review", "in_progress", "engineer"),
        ("qa", "security", "lead"),
        ("qa", "security", "engineer"),
        ("qa", "in_progress", "lead"),
        ("security", "done", "qa"),
        ("security", "done", "lead"),
        ("security", "in_progress", "qa"),
        ("todo", "in_progress", "engineer"),  # only owner ("otomatis") may
        ("todo", "in_progress", "pm"),
        ("blocked", "todo", "pm"),  # hanya owner
        ("blocked", "todo", "lead"),
        ("in_progress", "done", "engineer"),  # epic->done is PM-only
    ],
)
def test_illegal_agent_transitions(from_status, to_status, role):
    allowed, reason = can_transition(from_status, to_status, role)
    assert not allowed
    assert from_status in reason and to_status in reason


def test_owner_may_do_any_transition():
    allowed, _ = can_transition("backlog", "in_progress", None)
    assert allowed


def test_owner_may_unblock():
    allowed, _ = can_transition("blocked", "todo", None)
    assert allowed


def test_owner_may_do_todo_to_in_progress():
    allowed, _ = can_transition("todo", "in_progress", None)
    assert allowed


def test_same_status_is_illegal_for_everyone():
    allowed, _ = can_transition("todo", "todo", None)
    assert not allowed
    allowed, _ = can_transition("todo", "todo", "pm")
    assert not allowed


def test_unknown_role_rejected():
    allowed, reason = can_transition("backlog", "todo", "banana")
    assert not allowed
    assert "banana" in reason


def test_unknown_status_rejected():
    allowed, reason = can_transition("backlog", "nope", None)
    assert not allowed
