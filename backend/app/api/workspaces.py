import os

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models import Run, Ticket, Workspace
from app.db.session import get_session
from app.schemas.workspace import DEFAULT_GUARDRAILS, WorkspaceCreate, WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _validate_repo_path(repo_path: str) -> None:
    if not os.path.isabs(repo_path):
        raise AppError(422, "invalid_repo_path", "repo_path must be an absolute path")
    if not os.path.exists(repo_path):
        raise AppError(422, "invalid_repo_path", f"repo_path does not exist: {repo_path}")
    if not os.path.isdir(repo_path):
        raise AppError(422, "invalid_repo_path", f"repo_path is not a directory: {repo_path}")


async def _get_workspace_or_404(session: AsyncSession, workspace_id: str) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        raise AppError(404, "not_found", f"workspace {workspace_id} not found")
    return ws


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(Workspace))
    return result.all()


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, session: AsyncSession = Depends(get_session)):
    _validate_repo_path(body.repo_path)

    ws = Workspace(
        name=body.name,
        key=body.key,
        repo_path=body.repo_path,
        guardrails=dict(DEFAULT_GUARDRAILS),
        ticket_counter=0,
    )
    session.add(ws)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "duplicate_key", f"workspace key '{body.key}' already exists")
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    return await _get_workspace_or_404(session, workspace_id)


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: str, body: WorkspaceUpdate, session: AsyncSession = Depends(get_session)
):
    ws = await _get_workspace_or_404(session, workspace_id)

    if body.repo_path is not None:
        _validate_repo_path(body.repo_path)
        ws.repo_path = body.repo_path
    if body.name is not None:
        ws.name = body.name
    if body.guardrails is not None:
        ws.guardrails = body.guardrails

    await session.commit()
    await session.refresh(ws)
    return ws


@router.post("/{workspace_id}/pause", response_model=WorkspaceOut)
async def pause_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    """Kill switch (MAP-031, docs/02-tsd.md §6): stop every run in this workspace and

    reject new schedules until resumed. Sets the cancel event on each executing run
    (the adapter's own terminate/kill handles the actual subprocess death — see
    OpenCodeTool._terminate) and cancels queued runs outright since they never started.
    """
    from app.core import orchestrator  # deferred: orchestrator imports app.api.tickets,

    # which imports this module — a top-level import here would be circular.

    ws = await _get_workspace_or_404(session, workspace_id)
    ws.paused = True

    runs = (
        await session.scalars(
            select(Run)
            .join(Ticket, Run.ticket_id == Ticket.id)
            .where(Ticket.workspace_id == workspace_id, Run.status.in_(("running", "queued")))
        )
    ).all()

    for run in runs:
        if run.status == "running":
            await orchestrator.stop(run.id)
        else:
            await orchestrator.cancel_queued(run.agent_id, run.id)
            run.status = "cancelled"

    await session.commit()
    await session.refresh(ws)
    return ws


@router.post("/{workspace_id}/resume", response_model=WorkspaceOut)
async def resume_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    ws = await _get_workspace_or_404(session, workspace_id)
    ws.paused = False
    await session.commit()
    await session.refresh(ws)
    return ws


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    ws = await _get_workspace_or_404(session, workspace_id)
    await session.delete(ws)
    await session.commit()
