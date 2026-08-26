"""Guardrail checks — docs/02-tsd.md §6, MAP-027.

Two call sites:

- `check_guardrails()` — called from `orchestrator.schedule()` *before* a `Run` row is
  created. Covers `max_concurrent_runs` (per workspace), `max_cost_per_ticket`,
  `max_handoff_depth`, and ticket-not-in-active-sprint. Raises `GuardrailBlocked`
  (never creates the Run); the caller
  transitions the ticket to `blocked` with a system comment naming the guardrail — same
  shape as the existing `RuntimeError("workspace paused")` -> `AppError(409, ...)` path in
  `app/api/runs.py`.
- `over_run_timeout()` / `over_cost_per_run()` — polled from `orchestrator.execute()`'s
  streaming loop while a run is in flight, to enforce `run_timeout_sec` and
  `max_cost_per_run`. These don't raise; they return a reason string (or None) that the
  caller uses both to set `cancel_event` and to word the eventual system comment.

Every trip must be traceable to a specific guardrail name in its comment — see
CLAUDE.md "Guardrails are the only brakes left".
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import Run, Sprint, Ticket
from app.schemas.workspace import DEFAULT_GUARDRAILS


class GuardrailBlocked(Exception):
    """Raised by `check_guardrails()` when a schedule-time guardrail fires.

    `guardrail` is the exact key from `workspace.guardrails` (e.g.
    "max_cost_per_ticket") so callers/tests can assert on it without parsing prose.
    """

    def __init__(self, guardrail: str, message: str):
        self.guardrail = guardrail
        super().__init__(message)


def guardrail_limit(guardrails: dict, key: str):
    """Effective value for `key`: workspace override if set, else the documented default."""
    value = guardrails.get(key)
    return value if value is not None else DEFAULT_GUARDRAILS[key]


_limit = guardrail_limit


async def check_guardrails(
    session,
    ticket: Ticket,
    guardrails: dict,
    *,
    agent_role: str | None = None,
    sprint_creator_roles: list | None = None,
    exclude_run_id: str | None = None,
    trigger: str | None = None,
) -> None:
    """Schedule-time checks. Raises `GuardrailBlocked` on the first failing check.

    `exclude_run_id`: the run whose own report-processing (handoff / tickets[] /
    updates:) is *calling* this (still DB-status "running" until its `_finish_run`
    fully wraps up) shouldn't count against `max_concurrent_runs` for follow-ups it
    is itself scheduling — it's practically done, just finishing bookkeeping.

    `agent_role`/`sprint_creator_roles`: the ticket-not-in-active-sprint gate below
    exempts whichever roles the workspace already trusts to plan sprints (default
    PM-only, `workspace.sprint_creator_roles`) — those roles must always be able to
    respond (including to a brand-new backlog ticket) so they can actually do the
    triage/handoff-into-a-sprint this gate is forcing everyone else to wait for.

    `trigger`: `max_handoff_depth` bounds runaway agent-to-agent handoff chains
    (docs/03-agent-design.md §6) — it is not meant to cap how many times an owner
    can nudge/chat an agent (`trigger="mention"`, the only human-initiated trigger;
    see app/api/comments.py). A long-running epic ticket can legitimately rack up
    a deep handoff chain and still be `done`; without this exemption, any further
    owner chat message on that same ticket would guardrail-block forever, since
    `handoff_depth` never decreases on its own. Real agent-to-agent handoffs
    triggered by a reply to such a chat message (trigger="handoff") are still fully
    bounded. `app/api/tickets.py`'s `update_ticket()` resets `handoff_depth` to 0
    whenever an owner unblocks a ticket, so this guardrail is recoverable too
    (mirrors `loop_reset_at` for the loop detector) instead of permanently jamming
    agent-to-agent progress once a ticket ever hits the limit.
    """

    if agent_role not in (sprint_creator_roles or []):
        sprint = await session.get(Sprint, ticket.sprint_id) if ticket.sprint_id else None
        if sprint is None or sprint.status != "active":
            state = (
                f'sprint "{sprint.name}" (status {sprint.status})'
                if sprint
                else "not in any sprint (backlog)"
            )
            raise GuardrailBlocked(
                "ticket_not_in_active_sprint",
                f"This ticket is {state} — agents can only work on tickets "
                f"in the active sprint",
            )

    max_concurrent = _limit(guardrails, "max_concurrent_runs")
    query = (
        select(func.count())
        .select_from(Run)
        .join(Ticket, Run.ticket_id == Ticket.id)
        .where(Ticket.workspace_id == ticket.workspace_id, Run.status == "running")
    )
    if exclude_run_id is not None:
        query = query.where(Run.id != exclude_run_id)
    running = await session.scalar(query)
    if running >= max_concurrent:
        raise GuardrailBlocked(
            "max_concurrent_runs",
            f"Guardrail max_concurrent_runs terlampaui: {running} run sedang berjalan "
            f"(batas {max_concurrent})",
        )

    max_cost_per_ticket = _limit(guardrails, "max_cost_per_ticket")
    cost_used = ticket.cost_used or 0.0
    if cost_used >= max_cost_per_ticket:
        raise GuardrailBlocked(
            "max_cost_per_ticket",
            f"Guardrail max_cost_per_ticket terlampaui: ${cost_used:.2f} >= "
            f"${max_cost_per_ticket:.2f}",
        )

    if trigger != "mention":
        max_handoff_depth = _limit(guardrails, "max_handoff_depth")
        depth = ticket.handoff_depth or 0
        if depth >= max_handoff_depth:
            raise GuardrailBlocked(
                "max_handoff_depth",
                f"Guardrail max_handoff_depth terlampaui: kedalaman {depth} >= {max_handoff_depth}",
            )


async def check_guardrails_routine(
    session,
    workspace_id: str,
    guardrails: dict,
) -> None:
    """Schedule-time checks for no-ticket runs (routine + chat): only
    `max_concurrent_runs` applies — cost-per-ticket and handoff-depth are ticket-scoped
    and meaningless here. Counts running ticket runs AND running no-ticket runs
    (routine/chat) for the workspace, so a chat run can't sneak past the concurrency
    cap by not being on a ticket. Raises `GuardrailBlocked` on the first failing check.
    """
    max_concurrent = _limit(guardrails, "max_concurrent_runs")
    query = (
        select(func.count())
        .select_from(Run)
        .join(Ticket, Run.ticket_id == Ticket.id)
        .where(Ticket.workspace_id == workspace_id, Run.status == "running")
    )
    running = await session.scalar(query)
    # No-ticket runs (routine/chat) share the same concurrency budget.
    from app.db.models import Conversation

    no_ticket_running = await session.scalar(
        select(func.count())
        .select_from(Run)
        .join(Conversation, Run.conversation_id == Conversation.id)
        .where(Conversation.workspace_id == workspace_id, Run.status == "running")
    )
    running += no_ticket_running or 0
    if running >= max_concurrent:
        raise GuardrailBlocked(
            "max_concurrent_runs",
            f"Guardrail max_concurrent_runs terlampaui: {running} run sedang berjalan "
            f"(batas {max_concurrent})",
        )


def over_run_timeout(guardrails: dict, elapsed_sec: float) -> str | None:
    """Returns a system-comment message if `elapsed_sec` exceeds `run_timeout_sec`, else None."""
    limit = _limit(guardrails, "run_timeout_sec")
    if elapsed_sec >= limit:
        return f"Guardrail run_timeout_sec ({limit}s) terlampaui: run dihentikan setelah {elapsed_sec:.0f}s"
    return None


def over_cost_per_run(guardrails: dict, running_cost: float) -> str | None:
    """Returns a system-comment message if `running_cost` exceeds `max_cost_per_run`, else None."""
    limit = _limit(guardrails, "max_cost_per_run")
    if running_cost >= limit:
        return (
            f"Guardrail max_cost_per_run terlampaui: ${running_cost:.2f} >= ${limit:.2f}"
        )
    return None
