"""Ticket status transition rules — docs/02-tsd.md §5, docs/03-agent-design.md §5.

Pure Python, no FastAPI/HTTP here: MAP-018's ```map parser reuses `can_transition`
from a non-HTTP context, so this module must stay framework-independent. The API
layer (app/api/tickets.py) is responsible for turning a `False` result into a 422.
"""

STATUSES = {"backlog", "todo", "in_progress", "review", "qa", "security", "done", "blocked"}

# (from, to) -> set of agent roles allowed to make that move. Owner (actor_role=None)
# bypasses this table entirely (see can_transition below).
_TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    ("backlog", "todo"): frozenset({"pm"}),
    ("in_progress", "review"): frozenset({"engineer", "designer"}),
    ("in_progress", "done"): frozenset({"pm"}),  # epic: all children done
    ("review", "qa"): frozenset({"lead"}),
    ("review", "in_progress"): frozenset({"lead"}),
    ("qa", "security"): frozenset({"qa"}),
    ("qa", "in_progress"): frozenset({"qa"}),
    ("security", "done"): frozenset({"pentester"}),
    ("security", "in_progress"): frozenset({"pentester"}),
    # ("todo", "in_progress") and ("blocked", "todo") have no agent role listed in
    # docs/03-agent-design.md §5 ("otomatis saat run dimulai" / "hanya owner") —
    # intentionally absent here so only the owner path below allows them.
}

ALL_ROLES = frozenset({"pm", "lead", "engineer", "designer", "qa", "pentester"})


def can_transition(from_status: str, to_status: str, actor_role: str | None) -> tuple[bool, str]:
    """Return (allowed, reason). `actor_role=None` means the owner (no agent)."""
    what = f"cannot transition from '{from_status}' to '{to_status}' for role '{actor_role}'"

    if to_status not in STATUSES or from_status not in STATUSES:
        return False, f"unknown status in transition '{from_status}' -> '{to_status}'"
    if from_status == to_status:
        return False, f"cannot transition from '{from_status}' to '{to_status}' (no-op)"

    if actor_role is None:
        return True, "owner may perform any transition"

    if actor_role not in ALL_ROLES:
        return False, f"unknown role '{actor_role}'"

    # any -> blocked: any agent role may do this (guardrail/error/agent request).
    if to_status == "blocked":
        return True, f"role '{actor_role}' may move any ticket to blocked"

    allowed_roles = _TRANSITIONS.get((from_status, to_status), frozenset())
    if actor_role in allowed_roles:
        return True, f"role '{actor_role}' may transition '{from_status}' -> '{to_status}'"

    return False, what
