"""Unit tests for core/state_machine.py — docs/03-agent-design.md §5.

Any known role (or the owner) may move a ticket between any two distinct known
statuses; see test_state_machine_matrix.py for the exhaustive cross-product.
"""

import pytest

from app.core.state_machine import ALL_ROLES, STATUSES, can_transition


@pytest.mark.parametrize("role", sorted(ALL_ROLES))
def test_any_role_may_transition_between_any_two_distinct_statuses(role):
    for frm in STATUSES:
        for to in STATUSES:
            if frm == to:
                continue
            allowed, reason = can_transition(frm, to, role)
            assert allowed, f"{role} should be able to move {frm} -> {to}: {reason}"


@pytest.mark.parametrize("from_status", sorted(STATUSES - {"blocked"}))
def test_any_role_may_block(from_status):
    for role in ALL_ROLES:
        allowed, _ = can_transition(from_status, "blocked", role)
        assert allowed


def test_owner_may_do_any_transition():
    allowed, _ = can_transition("backlog", "in_progress", None)
    assert allowed


def test_owner_may_unblock():
    allowed, _ = can_transition("blocked", "todo", None)
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
