"""Ticket status transition rules — docs/02-tsd.md §5, docs/03-agent-design.md §5.

Pure Python, no FastAPI/HTTP here: MAP-018's ```map parser reuses `can_transition`
from a non-HTTP context, so this module must stay framework-independent. The API
layer (app/api/tickets.py) is responsible for turning a `False` result into a 422.

Any known role (or the owner) may move a ticket between any two distinct known
statuses — the from/to matrix that used to restrict this (e.g. only Lead could do
review -> qa) was removed by owner request: it kept producing false blocks on the
kanban board for perfectly reasonable manual moves. What a role may *declare* in
its own ```map block is a separate, narrower gate (report.py's `status == "release"`
check) — this function only governs "is this move structurally legal at all".
"""

STATUSES = {
    "backlog",
    "todo",
    "in_progress",
    "review",
    "qa",
    "security",
    "done",
    "release",
    "blocked",
}

ALL_ROLES = frozenset({"pm", "lead", "engineer", "designer", "qa", "pentester"})


def can_transition(from_status: str, to_status: str, actor_role: str | None) -> tuple[bool, str]:
    """Return (allowed, reason). `actor_role=None` means the owner (no agent)."""
    if to_status not in STATUSES or from_status not in STATUSES:
        return False, f"unknown status in transition '{from_status}' -> '{to_status}'"
    if from_status == to_status:
        return False, f"cannot transition from '{from_status}' to '{to_status}' (no-op)"

    if actor_role is None:
        return True, "owner may perform any transition"

    if actor_role not in ALL_ROLES:
        return False, f"unknown role '{actor_role}'"

    return True, f"role '{actor_role}' may transition '{from_status}' -> '{to_status}' (unrestricted)"
