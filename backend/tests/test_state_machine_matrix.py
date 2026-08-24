"""MAP-016 exhaustive transition matrix — docs/03-agent-design.md §5.

Full cross product of status x status x (role | owner) checked against an expected
table written independently of app/core/state_machine.py's internals, so a
deliberate regression in the implementation has to satisfy this table too, not
just mirror whatever the code does. Any known role/owner may move a ticket
between any two *distinct* known statuses — the only illegal combos are a
same-status no-op, an unknown status, or an unknown role.
"""

import pytest

from app.core.state_machine import ALL_ROLES, STATUSES, can_transition

ROLES = sorted(ALL_ROLES)
ALL_STATUSES = sorted(STATUSES)


def _expected(frm: str, to: str, role: str | None) -> bool:
    if frm == to:
        return False
    if role is None:
        return True  # owner bypasses everything
    return role in ALL_ROLES


_CASES = [
    (frm, to, role)
    for frm in ALL_STATUSES
    for to in ALL_STATUSES
    for role in [*ROLES, None]
]


@pytest.mark.parametrize("frm,to,role", _CASES, ids=[f"{f}->{t}/{r}" for f, t, r in _CASES])
def test_full_transition_matrix(frm, to, role):
    allowed, reason = can_transition(frm, to, role)
    expected = _expected(frm, to, role)
    assert allowed == expected, (
        f"can_transition({frm!r}, {to!r}, {role!r}) -> {allowed} ({reason}), "
        f"expected {expected}"
    )


def test_matrix_covers_all_statuses_and_roles():
    assert ALL_STATUSES == sorted(STATUSES)
    assert set(ROLES) == ALL_ROLES
    assert len(_CASES) == len(ALL_STATUSES) * len(ALL_STATUSES) * (len(ROLES) + 1)
