import os
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models import ArtifactGroup, Run, Sprint, Ticket, Workspace
from app.db.session import get_session
from app.schemas.workspace import (
    DEFAULT_GUARDRAILS,
    DEFAULT_WORKFLOW_PROMPT,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Bare/relative repo_path values are created here instead of being rejected —
# relative to the backend process's cwd (backend/ under `make dev`), mirroring how
# STORAGE_DIR's default ("../storage") is also resolved relative to that same cwd.
_WORKSPACES_DIR = Path("workspaces")


def _resolve_repo_path(repo_path: str) -> str:
    """Validate/create repo_path, returning the absolute path to use.

    - Absolute + exists as a directory: used as-is.
    - Absolute + doesn't exist: created at that exact path (mkdir -p).
    - Absolute + exists but isn't a directory: rejected.
    - Not absolute (bare name / relative): sanitized to a flat directory name (no
      traversal, same flattening approach as attachment filename sanitization) and
      created under _WORKSPACES_DIR, e.g. "myproject" -> "<cwd>/workspaces/myproject".
    """
    if not repo_path or not repo_path.strip():
        raise AppError(422, "invalid_repo_path", "repo_path is required")

    if os.path.isabs(repo_path):
        if os.path.exists(repo_path):
            if not os.path.isdir(repo_path):
                raise AppError(422, "invalid_repo_path", f"repo_path is not a directory: {repo_path}")
            return repo_path
        os.makedirs(repo_path, exist_ok=True)
        return repo_path

    name = os.path.basename(repo_path.rstrip("/\\"))
    if not name or name in (".", ".."):
        raise AppError(422, "invalid_repo_path", f"invalid repo_path: {repo_path}")
    target = (_WORKSPACES_DIR / name).resolve()
    if target.exists() and not target.is_dir():
        raise AppError(422, "invalid_repo_path", f"repo_path is not a directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


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
    repo_path = _resolve_repo_path(body.repo_path)

    ws = Workspace(
        name=body.name,
        key=body.key,
        repo_path=repo_path,
        description=body.description,
        guardrails=dict(DEFAULT_GUARDRAILS),
        workflow_prompt=DEFAULT_WORKFLOW_PROMPT,
        ticket_counter=0,
        sprint_creator_roles=["pm"],
    )
    session.add(ws)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "duplicate_key", f"workspace key '{body.key}' already exists")
    await session.refresh(ws)
    return ws


@router.get("/workflow-prompt-default")
async def get_workflow_prompt_default():
    """The default workflow prompt, for the Settings page's "reset to default" button."""
    return {"workflow_prompt": DEFAULT_WORKFLOW_PROMPT}


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    return await _get_workspace_or_404(session, workspace_id)


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: str, body: WorkspaceUpdate, session: AsyncSession = Depends(get_session)
):
    ws = await _get_workspace_or_404(session, workspace_id)

    if body.repo_path is not None:
        ws.repo_path = _resolve_repo_path(body.repo_path)
    if body.name is not None:
        ws.name = body.name
    if body.description is not None:
        ws.description = body.description
    if body.guardrails is not None:
        ws.guardrails = body.guardrails
    if body.workflow_prompt is not None:
        ws.workflow_prompt = body.workflow_prompt
    if body.time_unit is not None:
        ws.time_unit = body.time_unit
    if body.timezone is not None:
        ws.timezone = body.timezone
    if body.sprint_creator_roles is not None:
        ws.sprint_creator_roles = body.sprint_creator_roles

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


@router.post("/{workspace_id}/reset", response_model=WorkspaceOut)
async def reset_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    """Wipe all tickets (cascades to comments/attachments/runs/events — chat history and

    Activity are just those, per docs/03-agent-design.md: chat = comments on tickets
    assigned to the PM), sprints, and artifact groups for this workspace, and restart the
    ticket-key counter. Keeps the workspace itself, its agents, and its settings intact.

    Requires the workspace to already be paused with nothing running/queued, so a
    background run's own DB writes can't race a delete out from under it — same
    safety bar as MAP-031's kill switch, just stricter (pause() only signals
    cancellation; it doesn't wait for the subprocess to actually stop).
    """
    ws = await _get_workspace_or_404(session, workspace_id)
    if not ws.paused:
        raise AppError(409, "not_paused", "pause the workspace before resetting its data")

    active = await session.scalar(
        select(Run)
        .join(Ticket, Run.ticket_id == Ticket.id)
        .where(Ticket.workspace_id == workspace_id, Run.status.in_(("running", "queued")))
    )
    if active is not None:
        raise AppError(
            409, "runs_in_progress", "wait for running/queued runs to finish before resetting"
        )

    await session.execute(delete(Ticket).where(Ticket.workspace_id == workspace_id))
    await session.execute(delete(Sprint).where(Sprint.workspace_id == workspace_id))
    await session.execute(delete(ArtifactGroup).where(ArtifactGroup.workspace_id == workspace_id))
    ws.ticket_counter = 0
    await session.commit()
    await session.refresh(ws)
    return ws


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str, session: AsyncSession = Depends(get_session)):
    ws = await _get_workspace_or_404(session, workspace_id)
    await session.delete(ws)
    await session.commit()
