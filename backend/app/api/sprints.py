from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.core.auth import WorkspaceRole, require_sprint_role, require_workspace_role
from app.core import orchestrator
from app.core.guardrails import GuardrailBlocked
from app.core.settings_store import SettingsLoadError
from app.db import session as db_session
from app.db.models import Agent, Comment, Sprint, Ticket
from app.db.session import get_session
from app.schemas.sprint import SprintCreate, SprintOut, SprintUpdate

workspace_sprints_router = APIRouter(prefix="/workspaces/{workspace_id}/sprints", tags=["sprints"])
sprints_router = APIRouter(prefix="/sprints", tags=["sprints"])

_TERMINAL_TICKET_STATUSES = {"done"}

# Statuses that still need work — a ticket in any of these gets a run triggered
# when its sprint is activated.
_ACTIONABLE_TICKET_STATUSES = {
    "backlog",
    "todo",
    "in_progress",
    "review",
    "qa",
    "security",
    "blocked",
}


async def _get_sprint_or_404(session: AsyncSession, sprint_id: str) -> Sprint:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None:
        raise AppError(404, "not_found", f"sprint {sprint_id} not found")
    return sprint


@workspace_sprints_router.get("", response_model=list[SprintOut])
async def list_sprints(
    workspace_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.viewer)),
):
    await _get_workspace_or_404(session, workspace_id)
    result = await session.scalars(
        select(Sprint).where(Sprint.workspace_id == workspace_id).order_by(Sprint.index)
    )
    return result.all()


@workspace_sprints_router.post("", response_model=SprintOut, status_code=201)
async def create_sprint(
    workspace_id: str,
    body: SprintCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.editor)),
):
    await _get_workspace_or_404(session, workspace_id)
    existing = (
        await session.scalars(select(Sprint).where(Sprint.workspace_id == workspace_id))
    ).all()
    next_index = max((s.index for s in existing), default=-1) + 1
    has_active = any(s.status == "active" for s in existing)
    sprint = Sprint(
        workspace_id=workspace_id,
        name=body.name,
        goal=body.goal,
        index=next_index,
        status="planned" if has_active else "active",
        duration_estimate=body.duration_estimate,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    session.add(sprint)
    await session.commit()
    await session.refresh(sprint)
    return sprint


async def _select_carry_over_sprint(
    session: AsyncSession, workspace_id: str, completed_sprint_id: str
) -> Sprint | None:
    """Next eligible sprint for tickets carried over from a just-completed sprint:
    the workspace's other active sprint, else the lowest-index planned sprint,
    else None (caller falls back to sprint_id=NULL, i.e. the backlog)."""
    siblings = (
        await session.scalars(
            select(Sprint).where(
                Sprint.workspace_id == workspace_id,
                Sprint.id != completed_sprint_id,
            )
        )
    ).all()
    active = next((s for s in siblings if s.status == "active"), None)
    if active:
        return active
    planned = sorted((s for s in siblings if s.status == "planned"), key=lambda s: s.index)
    return planned[0] if planned else None


async def _carry_over_unfinished_tickets(session: AsyncSession, sprint: Sprint) -> None:
    tickets = (
        await session.scalars(
            select(Ticket).where(
                Ticket.sprint_id == sprint.id,
                Ticket.status.notin_(_TERMINAL_TICKET_STATUSES),
            )
        )
    ).all()
    if not tickets:
        return
    target = await _select_carry_over_sprint(session, sprint.workspace_id, sprint.id)
    for ticket in tickets:
        ticket.sprint_id = target.id if target else None
        destination = f'sprint "{target.name}"' if target else "backlog"
        session.add(
            Comment(
                ticket_id=ticket.id,
                author_agent_id=None,
                is_system=True,
                body=(
                    f'Sprint "{sprint.name}" ditutup, ticket ini masih status '
                    f'"{ticket.status}" dan dipindahkan ke {destination}.'
                ),
            )
        )


async def _kick_off_sprint_tickets(session: AsyncSession, sprint: Sprint) -> int:
    """Trigger a run for every ticket in the sprint that still needs work and has
    an assignee (owner request: activating a sprint starts the team on its tickets).

    Tickets already `done` are skipped — if every ticket is done, nothing
    is triggered. Tickets without an assignee can't run (no agent to execute them),
    so they're skipped too. Guardrail trips (e.g. `max_concurrent_runs`) are
    swallowed: `schedule()` already wrote its own system comment naming the
    guardrail, and the sprint activation itself must still succeed. Returns the
    number of runs scheduled.
    """
    tickets = (
        await session.scalars(
            select(Ticket).where(
                Ticket.sprint_id == sprint.id,
                Ticket.status.in_(_ACTIONABLE_TICKET_STATUSES),
                Ticket.assignee_id.is_not(None),
            )
        )
    ).all()
    if not tickets:
        return 0

    agents = (
        await session.scalars(
            select(Agent).where(Agent.workspace_id == sprint.workspace_id)
        )
    ).all()
    by_id = {a.id: a for a in agents}

    scheduled = 0
    for ticket in tickets:
        agent = by_id.get(ticket.assignee_id)
        if agent is None or not agent.enabled or agent.status == "disabled":
            continue
        try:
            await orchestrator.schedule(
                session,
                db_session.async_session,
                ticket=ticket,
                agent=agent,
                trigger="manual",
            )
            scheduled += 1
        except (GuardrailBlocked, RuntimeError, SettingsLoadError):
            # schedule() already recorded the reason (blocked ticket / paused
            # workspace / guardrail) as a system comment; keep going with the rest.
            # A malformed .cempala/settings.yaml is treated the same way — best
            # effort, don't let one workspace's bad file block the rest.
            continue
    return scheduled


async def _demote_other_active_sprints(session: AsyncSession, sprint: Sprint) -> None:
    others = await session.scalars(
        select(Sprint).where(
            Sprint.workspace_id == sprint.workspace_id,
            Sprint.id != sprint.id,
            Sprint.status == "active",
        )
    )
    for other in others:
        other.status = "planned"


async def activate_sprint(session: AsyncSession, sprint: Sprint) -> int:
    """Activate `sprint`: demote any other active sprint in the workspace,
    flip this one to active, and kick off its actionable tickets.

    Shared by the owner's PATCH endpoint below and by the chat sprint-proposal
    approval flow (`orchestrator.py`) — "activating a sprint starts the team
    on its tickets" applies the same way regardless of who triggered it.
    Returns the number of runs scheduled by kickoff.
    """
    await _demote_other_active_sprints(session, sprint)
    sprint.status = "active"
    await session.commit()
    await session.refresh(sprint)
    scheduled = await _kick_off_sprint_tickets(session, sprint)
    await session.commit()
    return scheduled


async def complete_sprint(session: AsyncSession, sprint: Sprint) -> None:
    """Complete `sprint`: carry its unfinished tickets over to the next eligible
    sprint (or the backlog), then flip it to completed.

    Shared by the owner's PATCH endpoint below and the ```map `sprints:` `status:
    completed` path (`orchestrator.py`) — same "the PM gets the same levers the
    owner has" reasoning as `activate_sprint`.
    """
    if sprint.status != "completed":
        await _carry_over_unfinished_tickets(session, sprint)
    sprint.status = "completed"
    await session.commit()
    await session.refresh(sprint)


@sprints_router.patch("/{sprint_id}", response_model=SprintOut)
async def update_sprint(
    sprint_id: str,
    body: SprintUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_sprint_role(WorkspaceRole.editor)),
):
    sprint = await _get_sprint_or_404(session, sprint_id)

    new_start = body.start_date if body.start_date is not None else sprint.start_date
    new_end = body.end_date if body.end_date is not None else sprint.end_date
    if new_start and new_end and new_end < new_start:
        raise AppError(422, "invalid_dates", "end_date must be on or after start_date")

    if body.status == "active":
        await _demote_other_active_sprints(session, sprint)

    should_carry_over = body.status == "completed" and sprint.status != "completed"
    # Kick off the sprint's tickets only on the transition INTO active (not when
    # re-saving an already-active sprint with other fields).
    should_kick_off = body.status == "active" and sprint.status != "active"

    for field in ("name", "goal", "duration_estimate", "status", "start_date", "end_date"):
        value = getattr(body, field)
        if value is not None:
            setattr(sprint, field, value)

    if should_carry_over:
        await _carry_over_unfinished_tickets(session, sprint)

    await session.commit()
    await session.refresh(sprint)

    if should_kick_off:
        await _kick_off_sprint_tickets(session, sprint)
        await session.commit()

    return sprint
