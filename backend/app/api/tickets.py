from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.core.auth import WorkspaceRole, require_ticket_role, require_workspace_role
from app.core.state_machine import can_transition
from app.db import session as db_session
from app.db.models import Agent, Attachment, Comment, CommentMention, Run, Ticket, Workspace
from app.db.session import get_session
from app.schemas.ticket import (
    AttachmentOut,
    CommentOut,
    RunOut,
    TicketCreate,
    TicketDetail,
    TicketOut,
    TicketUpdate,
)

workspace_tickets_router = APIRouter(
    prefix="/workspaces/{workspace_id}/tickets", tags=["tickets"]
)
tickets_router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _get_ticket_or_404(session: AsyncSession, key: str) -> Ticket:
    ticket = await session.scalar(select(Ticket).where(Ticket.key == key))
    if ticket is None:
        raise AppError(404, "not_found", f"ticket {key} not found")
    return ticket


async def _next_key(session: AsyncSession, workspace: Workspace) -> str:
    """Atomically bump workspace.ticket_counter and derive the ticket key from it.

    UPDATE ... RETURNING runs in the same transaction as the ticket insert (both
    committed together below), so the increment and the insert succeed or fail as a
    unit. SQLite serializes concurrent writers at the file level (busy_timeout is set
    in db/session.py so a blocked writer waits instead of erroring), which is enough
    to guarantee unique, monotonically increasing numbers under concurrent requests.
    """
    result = await session.execute(
        update(Workspace)
        .where(Workspace.id == workspace.id)
        .values(ticket_counter=Workspace.ticket_counter + 1)
        .returning(Workspace.ticket_counter)
    )
    n = result.scalar_one()
    return f"{workspace.key}-{n:03d}"


async def _validate_parent(session: AsyncSession, workspace_id: str, parent_id: str) -> None:
    parent = await session.get(Ticket, parent_id)
    if parent is None or parent.workspace_id != workspace_id:
        raise AppError(422, "invalid_parent", f"parent ticket {parent_id} not found")
    if parent.parent_id is not None:
        raise AppError(422, "nesting_too_deep", "parent ticket already has a parent (max 1 level)")


@workspace_tickets_router.get("", response_model=list[TicketOut])
async def list_tickets(
    workspace_id: str,
    status: str | None = None,
    assignee_id: str | None = None,
    parent_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.viewer)),
):
    await _get_workspace_or_404(session, workspace_id)

    stmt = select(Ticket).where(Ticket.workspace_id == workspace_id)
    if status is not None:
        stmt = stmt.where(Ticket.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Ticket.assignee_id == assignee_id)
    if parent_id is not None:
        stmt = stmt.where(Ticket.parent_id == parent_id)
    # Most-recently-updated first — the freshest state is what callers (Board,
    # MCP tools) care about.
    stmt = stmt.order_by(Ticket.updated_at.desc())
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.scalars(stmt)
    return result.all()


@workspace_tickets_router.post("", response_model=TicketOut, status_code=201)
async def create_ticket(
    workspace_id: str,
    body: TicketCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.editor)),
):
    workspace = await _get_workspace_or_404(session, workspace_id)

    if body.parent_id is None and not body.is_new_epic:
        raise AppError(
            422,
            "epic_required",
            "parent_id is required (every ticket needs an epic) unless is_new_epic is true",
        )
    if body.parent_id is not None and body.is_new_epic:
        raise AppError(
            422, "invalid_epic_flag", "cannot set both parent_id and is_new_epic"
        )
    if body.parent_id is not None:
        await _validate_parent(session, workspace_id, body.parent_id)

    key = await _next_key(session, workspace)
    ticket = Ticket(
        workspace_id=workspace_id,
        key=key,
        title=body.title,
        description=body.description,
        priority=body.priority,
        assignee_id=body.assignee_id,
        parent_id=body.parent_id,
        category=body.category,
        sprint_id=body.sprint_id,
        duration_estimate=body.duration_estimate,
    )
    session.add(ticket)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(422, "invalid_reference", "assignee_id or parent_id does not exist")
    await session.refresh(ticket)
    if ticket.assignee_id is not None:
        from app.core import orchestrator

        await orchestrator._auto_schedule_assignee(session, db_session.async_session, ticket)
    return ticket


@tickets_router.get("/{key}", response_model=TicketDetail)
async def get_ticket(
    key: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_ticket_role(WorkspaceRole.viewer)),
):
    ticket = await _get_ticket_or_404(session, key)

    comments = (
        await session.scalars(select(Comment).where(Comment.ticket_id == ticket.id))
    ).all()
    attachments = (
        await session.scalars(select(Attachment).where(Attachment.ticket_id == ticket.id))
    ).all()
    runs = (await session.scalars(select(Run).where(Run.ticket_id == ticket.id))).all()
    children = (
        await session.scalars(select(Ticket).where(Ticket.parent_id == ticket.id))
    ).all()
    parent = await session.get(Ticket, ticket.parent_id) if ticket.parent_id else None

    comment_out = []
    for c in comments:
        agent_ids = (
            await session.scalars(
                select(CommentMention.agent_id).where(CommentMention.comment_id == c.id)
            )
        ).all()
        names: list[str] = []
        if agent_ids:
            names = list(
                (await session.scalars(select(Agent.name).where(Agent.id.in_(agent_ids)))).all()
            )
        comment_out.append(
            CommentOut(**CommentOut.model_validate(c).model_dump(exclude={"mentions"}), mentions=names)
        )

    return TicketDetail(
        **TicketOut.model_validate(ticket).model_dump(),
        comments=comment_out,
        attachments=[AttachmentOut.model_validate(a) for a in attachments],
        runs=[RunOut.model_validate(r) for r in runs],
        children=[TicketOut.model_validate(c) for c in children],
        parent=TicketOut.model_validate(parent) if parent else None,
    )


@tickets_router.patch("/{key}", response_model=TicketOut)
async def update_ticket(
    key: str,
    body: TicketUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_ticket_role(WorkspaceRole.editor)),
):
    ticket = await _get_ticket_or_404(session, key)

    old_status = ticket.status
    old_assignee_id = ticket.assignee_id
    if body.status is not None and body.status != old_status:
        actor_role = None
        if body.actor_agent_id is not None:
            actor = await session.get(Agent, body.actor_agent_id)
            if actor is None:
                raise AppError(422, "invalid_reference", "actor_agent_id does not exist")
            actor_role = actor.role

        allowed, reason = can_transition(old_status, body.status, actor_role)
        if not allowed:
            raise AppError(422, "illegal_transition", reason)

    for field in (
        "title",
        "description",
        "priority",
        "assignee_id",
        "status",
        "category",
        "sprint_id",
        "duration_estimate",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(ticket, field, value)

    if body.status is not None and body.status != old_status:
        session.add(
            Comment(
                ticket_id=ticket.id,
                author_agent_id=None,
                is_system=True,
                body=f"Status changed from {old_status} to {body.status}",
            )
        )
        if body.status != "blocked":
            ticket.blocked_reason = None
            if old_status == "blocked":
                from datetime import datetime, timezone

                ticket.loop_reset_at = datetime.now(timezone.utc)
                # handoff_depth is otherwise monotonic (app/core/orchestrator.py's
                # _handoff() only ever increments it) — without this, a ticket that
                # ever hit max_handoff_depth would stay permanently unable to make
                # further agent-to-agent progress even after a human unblocks it,
                # the same class of bug loop_reset_at fixes for the loop detector.
                ticket.handoff_depth = 0

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(422, "invalid_reference", "assignee_id does not exist")
    await session.refresh(ticket)
    if body.assignee_id is not None and body.assignee_id != old_assignee_id:
        from app.core import orchestrator

        await orchestrator._auto_schedule_assignee(session, db_session.async_session, ticket)
    return ticket


@tickets_router.delete("/{key}", status_code=204)
async def delete_ticket(
    key: str,
    actor_agent_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_ticket_role(WorkspaceRole.admin)),
):
    """Permanently delete a ticket (cascades to comments/attachments/runs).

    `actor_agent_id` optional (owner path): when set, only a PM agent may delete —
    ticket deletion is a project-level decision, not something any role can do.
    """
    ticket = await _get_ticket_or_404(session, key)

    if actor_agent_id is not None:
        actor = await session.get(Agent, actor_agent_id)
        if actor is None:
            raise AppError(422, "invalid_reference", "actor_agent_id does not exist")
        if actor.role != "pm":
            raise AppError(403, "pm_only", "hanya PM yang boleh menghapus tiket")

    await session.delete(ticket)
    await session.commit()
