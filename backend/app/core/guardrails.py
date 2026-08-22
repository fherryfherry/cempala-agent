"""Guardrail checks — docs/02-tsd.md §6, MAP-027.

Two call sites:

- `check_guardrails()` — called from `orchestrator.schedule()` *before* a `Run` row is
  created. Covers `max_concurrent_runs` (per workspace), `max_cost_per_ticket`, and
  `max_handoff_depth`. Raises `GuardrailBlocked` (never creates the Run); the caller
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

from app.db.models import Run, Ticket
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
    session, ticket: Ticket, guardrails: dict, *, exclude_run_id: str | None = None
) -> None:
    """Schedule-time checks. Raises `GuardrailBlocked` on the first failing check.

    `exclude_run_id`: the run whose own report-processing (handoff / tickets[] /
    updates:) is *calling* this (still DB-status "running" until its `_finish_run`
    fully wraps up) shouldn't count against `max_concurrent_runs` for follow-ups it
    is itself scheduling — it's practically done, just finishing bookkeeping.
    """

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

    max_handoff_depth = _limit(guardrails, "max_handoff_depth")
    depth = ticket.handoff_depth or 0
    if depth >= max_handoff_depth:
        raise GuardrailBlocked(
            "max_handoff_depth",
            f"Guardrail max_handoff_depth terlampaui: kedalaman {depth} >= {max_handoff_depth}",
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
