"""MAP-023: POST /tickets/{key}/run, POST /runs/{id}/stop, GET /runs/{id},
GET /workspaces/{id}/runs.

Schedules real background work (`app.core.orchestrator`) — the request handler creates
the `Run` row and returns immediately with status `queued`/`running`; execution happens
in an `asyncio.Task` outside the request/response cycle, using its own DB session
(the request's session closes as soon as the response is sent).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_or_404
from app.api.errors import AppError
from app.api.tickets import _get_ticket_or_404
from app.api.workspaces import _get_workspace_or_404
from app.core import orchestrator
from app.db import session as db_session
from app.db.models import Event, Run, Ticket
from app.db.session import get_session
from app.schemas.run import EventOut, RunCreate, RunDetail, RunOut

runs_router = APIRouter(prefix="/runs", tags=["runs"])
ticket_run_router = APIRouter(prefix="/tickets/{key}/run", tags=["runs"])
workspace_runs_router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])


async def _get_run_or_404(session: AsyncSession, run_id: str) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise AppError(404, "not_found", f"run {run_id} not found")
    return run


@ticket_run_router.post("", response_model=RunOut, status_code=201)
async def start_run(key: str, body: RunCreate, session: AsyncSession = Depends(get_session)):
    ticket = await _get_ticket_or_404(session, key)

    agent_id = body.agent_id or ticket.assignee_id
    if agent_id is None:
        raise AppError(
            422,
            "no_agent",
            "agent_id was not provided and the ticket has no assignee",
        )
    agent = await _get_agent_or_404(session, agent_id)

    try:
        run = await orchestrator.schedule(
            session,
            db_session.async_session,
            ticket=ticket,
            agent=agent,
            trigger="manual",
        )
    except RuntimeError as exc:
        raise AppError(409, "workspace_paused", str(exc))

    return run


@runs_router.post("/{run_id}/stop", response_model=RunOut)
async def stop_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await _get_run_or_404(session, run_id)

    if run.status == "running":
        await orchestrator.stop(run_id)
        # Actual status flip to "cancelled" happens in execute()'s run_ended handling,
        # once the adapter reacts to cancel_event and finishes. Return current state.
    elif run.status == "queued":
        await orchestrator.cancel_queued(run.agent_id, run_id)
        run.status = "cancelled"
        await session.commit()
        await session.refresh(run)

    return run


@runs_router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    offset: int = 0,
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
):
    run = await _get_run_or_404(session, run_id)
    events = (
        await session.scalars(
            select(Event)
            .where(Event.run_id == run_id)
            .order_by(Event.seq)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return RunDetail(
        **RunOut.model_validate(run).model_dump(),
        events=[EventOut.model_validate(e) for e in events],
    )


@workspace_runs_router.get("", response_model=list[RunOut])
async def list_runs(
    workspace_id: str,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    await _get_workspace_or_404(session, workspace_id)

    stmt = select(Run).join(Ticket, Run.ticket_id == Ticket.id).where(
        Ticket.workspace_id == workspace_id
    )
    if status is not None:
        stmt = stmt.where(Run.status == status)

    result = await session.scalars(stmt)
    return result.all()
