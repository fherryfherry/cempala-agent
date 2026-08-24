from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.db.models import Comment, Sprint, Ticket
from app.db.session import get_session
from app.schemas.sprint import SprintCreate, SprintOut, SprintUpdate

workspace_sprints_router = APIRouter(prefix="/workspaces/{workspace_id}/sprints", tags=["sprints"])
sprints_router = APIRouter(prefix="/sprints", tags=["sprints"])

_TERMINAL_TICKET_STATUSES = {"done", "release"}


async def _get_sprint_or_404(session: AsyncSession, sprint_id: str) -> Sprint:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None:
        raise AppError(404, "not_found", f"sprint {sprint_id} not found")
    return sprint


@workspace_sprints_router.get("", response_model=list[SprintOut])
async def list_sprints(workspace_id: str, session: AsyncSession = Depends(get_session)):
    await _get_workspace_or_404(session, workspace_id)
    result = await session.scalars(
        select(Sprint).where(Sprint.workspace_id == workspace_id).order_by(Sprint.index)
    )
    return result.all()


@workspace_sprints_router.post("", response_model=SprintOut, status_code=201)
async def create_sprint(
    workspace_id: str, body: SprintCreate, session: AsyncSession = Depends(get_session)
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


@sprints_router.patch("/{sprint_id}", response_model=SprintOut)
async def update_sprint(
    sprint_id: str, body: SprintUpdate, session: AsyncSession = Depends(get_session)
):
    sprint = await _get_sprint_or_404(session, sprint_id)

    new_start = body.start_date if body.start_date is not None else sprint.start_date
    new_end = body.end_date if body.end_date is not None else sprint.end_date
    if new_start and new_end and new_end < new_start:
        raise AppError(422, "invalid_dates", "end_date must be on or after start_date")

    if body.status == "active":
        others = await session.scalars(
            select(Sprint).where(
                Sprint.workspace_id == sprint.workspace_id,
                Sprint.id != sprint.id,
                Sprint.status == "active",
            )
        )
        for other in others:
            other.status = "planned"

    should_carry_over = body.status == "completed" and sprint.status != "completed"

    for field in ("name", "goal", "duration_estimate", "status", "start_date", "end_date"):
        value = getattr(body, field)
        if value is not None:
            setattr(sprint, field, value)

    if should_carry_over:
        await _carry_over_unfinished_tickets(session, sprint)

    await session.commit()
    await session.refresh(sprint)
    return sprint
