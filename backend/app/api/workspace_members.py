"""Per-workspace membership CRUD (ADR-016) — a workspace `admin` grants/changes/
revokes access *within their own workspace* for existing users; creating new user
accounts is a separate, superadmin-only concern (`app/api/users.py`)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.core.auth import WorkspaceRole, require_workspace_role
from app.db.models import User, WorkspaceMember
from app.db.session import get_session
from app.schemas.workspace_member import (
    WorkspaceMemberCreate,
    WorkspaceMemberOut,
    WorkspaceMemberUpdate,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/members", tags=["workspace-members"])

_VALID_ROLES = {"viewer", "editor", "admin"}


async def _get_member_or_404(session: AsyncSession, workspace_id: str, member_id: str) -> WorkspaceMember:
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.id == member_id, WorkspaceMember.workspace_id == workspace_id
        )
    )
    if member is None:
        raise AppError(404, "not_found", f"member {member_id} not found in this workspace")
    return member


async def _active_admin_count(session: AsyncSession, workspace_id: str) -> int:
    return await session.scalar(
        select(func.count()).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "admin"
        )
    )


async def _to_out(session: AsyncSession, member: WorkspaceMember) -> WorkspaceMemberOut:
    email = await session.scalar(select(User.email).where(User.id == member.user_id))
    return WorkspaceMemberOut(
        id=member.id,
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=email or "",
        role=member.role,
        created_at=member.created_at,
    )


@router.get("", response_model=list[WorkspaceMemberOut])
async def list_members(
    workspace_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.viewer)),
):
    await _get_workspace_or_404(session, workspace_id)
    members = (
        await session.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at)
        )
    ).all()
    return [await _to_out(session, m) for m in members]


@router.post("", response_model=WorkspaceMemberOut, status_code=201)
async def add_member(
    workspace_id: str,
    body: WorkspaceMemberCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.admin)),
):
    await _get_workspace_or_404(session, workspace_id)
    if body.role not in _VALID_ROLES:
        raise AppError(422, "invalid_role", f"role must be one of {sorted(_VALID_ROLES)}")
    target = await session.get(User, body.user_id)
    if target is None:
        raise AppError(422, "invalid_reference", "user_id does not exist")

    member = WorkspaceMember(workspace_id=workspace_id, user_id=body.user_id, role=body.role)
    session.add(member)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "already_member", "this user is already a member of the workspace")
    await session.refresh(member)
    return await _to_out(session, member)


@router.patch("/{member_id}", response_model=WorkspaceMemberOut)
async def update_member(
    workspace_id: str,
    member_id: str,
    body: WorkspaceMemberUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.admin)),
):
    member = await _get_member_or_404(session, workspace_id, member_id)
    if body.role not in _VALID_ROLES:
        raise AppError(422, "invalid_role", f"role must be one of {sorted(_VALID_ROLES)}")

    if member.role == "admin" and body.role != "admin" and await _active_admin_count(session, workspace_id) <= 1:
        raise AppError(409, "last_admin", "cannot demote the last admin of this workspace")

    member.role = body.role
    await session.commit()
    await session.refresh(member)
    return await _to_out(session, member)


@router.delete("/{member_id}", status_code=204)
async def remove_member(
    workspace_id: str,
    member_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_workspace_role(WorkspaceRole.admin)),
):
    member = await _get_member_or_404(session, workspace_id, member_id)
    if member.role == "admin" and await _active_admin_count(session, workspace_id) <= 1:
        raise AppError(409, "last_admin", "cannot remove the last admin of this workspace")
    await session.delete(member)
    await session.commit()
