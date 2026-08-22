from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.db.models import Attachment, Comment, Run, Ticket, Workspace
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
    session: AsyncSession = Depends(get_session),
):
    await _get_workspace_or_404(session, workspace_id)

    stmt = select(Ticket).where(Ticket.workspace_id == workspace_id)
    if status is not None:
        stmt = stmt.where(Ticket.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Ticket.assignee_id == assignee_id)
    if parent_id is not None:
        stmt = stmt.where(Ticket.parent_id == parent_id)

    result = await session.scalars(stmt)
    return result.all()


@workspace_tickets_router.post("", response_model=TicketOut, status_code=201)
async def create_ticket(
    workspace_id: str, body: TicketCreate, session: AsyncSession = Depends(get_session)
):
    workspace = await _get_workspace_or_404(session, workspace_id)

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
    )
    session.add(ticket)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(422, "invalid_reference", "assignee_id or parent_id does not exist")
    await session.refresh(ticket)
    return ticket


@tickets_router.get("/{key}", response_model=TicketDetail)
async def get_ticket(key: str, session: AsyncSession = Depends(get_session)):
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

    return TicketDetail(
        **TicketOut.model_validate(ticket).model_dump(),
        comments=[CommentOut.model_validate(c) for c in comments],
        attachments=[AttachmentOut.model_validate(a) for a in attachments],
        runs=[RunOut.model_validate(r) for r in runs],
        children=[TicketOut.model_validate(c) for c in children],
    )


@tickets_router.patch("/{key}", response_model=TicketOut)
async def update_ticket(key: str, body: TicketUpdate, session: AsyncSession = Depends(get_session)):
    ticket = await _get_ticket_or_404(session, key)

    for field in ("title", "description", "priority", "assignee_id", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(ticket, field, value)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(422, "invalid_reference", "assignee_id does not exist")
    await session.refresh(ticket)
    return ticket


@tickets_router.delete("/{key}", status_code=204)
async def delete_ticket(key: str, session: AsyncSession = Depends(get_session)):
    ticket = await _get_ticket_or_404(session, key)
    await session.delete(ticket)
    await session.commit()
