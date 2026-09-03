"""CRUD for the global `role` table — docs/superpowers/specs/
2026-08-27-dynamic-roles-design.md.

Global, not per-workspace: one `role` table shared by all workspaces. The `"pm"`
role is special — undeletable, flags immutable (`pm_flags_locked`), prompt
editable. All 8 builtin roles are undeletable (`builtin_role`); custom roles can
be created/edited/deleted freely, except deletion is rejected (`role_in_use`)
while any agent still references the key.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.auth import get_current_user, require_superadmin
from app.db.models import Agent, Role, User
from app.db.session import get_session
from app.schemas.role import RoleCreate, RoleOut, RoleUpdate

router = APIRouter(prefix="/roles", tags=["roles"])

_AGENT_COUNT = (
    select(func.count())
    .where(Agent.role == Role.key)
    .correlate(Role)
    .scalar_subquery()
)


async def _get_role_or_404(session: AsyncSession, key: str) -> Role:
    role = await session.scalar(select(Role).where(Role.key == key))
    if role is None:
        raise AppError(404, "not_found", f"role '{key}' not found")
    return role


@router.get("", response_model=list[RoleOut])
async def list_roles(
    session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)
):
    rows = (
        await session.execute(
            select(Role, _AGENT_COUNT.label("agent_count")).order_by(Role.created_at)
        )
    ).all()
    return [
        RoleOut.model_validate(
            {**RoleOut.model_validate(role).model_dump(), "agent_count": count}
        )
        for role, count in rows
    ]


@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleCreate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_superadmin),
):
    role = Role(
        key=body.key,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        may_declare_tickets=body.may_declare_tickets,
        may_manage_artifacts=body.may_manage_artifacts,
        is_reviewer=body.is_reviewer,
    )
    session.add(role)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "duplicate_key", f"role key '{body.key}' already exists")
    await session.refresh(role)
    return RoleOut.model_validate(
        {**RoleOut.model_validate(role).model_dump(), "agent_count": 0}
    )


@router.patch("/{key}", response_model=RoleOut)
async def update_role(
    key: str,
    body: RoleUpdate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_superadmin),
):
    role = await _get_role_or_404(session, key)

    if role.key == "pm" and (
        (body.may_declare_tickets is not None and body.may_declare_tickets != role.may_declare_tickets)
        or (body.may_manage_artifacts is not None and body.may_manage_artifacts != role.may_manage_artifacts)
        or (body.is_reviewer is not None and body.is_reviewer != role.is_reviewer)
    ):
        raise AppError(403, "pm_flags_locked", "pm role flags are immutable")

    for field in (
        "name",
        "description",
        "system_prompt",
        "may_declare_tickets",
        "may_manage_artifacts",
        "is_reviewer",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(role, field, value)

    # description/system_prompt honor explicit null (clearing).
    if "description" in body.model_fields_set:
        role.description = body.description
    if "system_prompt" in body.model_fields_set:
        role.system_prompt = body.system_prompt

    await session.commit()
    await session.refresh(role)
    agent_count = await session.scalar(
        select(func.count()).where(Agent.role == role.key)
    )
    return RoleOut.model_validate(
        {**RoleOut.model_validate(role).model_dump(), "agent_count": agent_count or 0}
    )


@router.delete("/{key}", status_code=204)
async def delete_role(
    key: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_superadmin),
):
    role = await _get_role_or_404(session, key)

    if role.is_builtin:
        raise AppError(403, "builtin_role", f"role '{key}' is builtin and cannot be deleted")

    agent_count = await session.scalar(select(func.count()).where(Agent.role == key))
    if agent_count:
        raise AppError(
            409,
            "role_in_use",
            f"role '{key}' is still used by {agent_count} agent(s); reassign or delete them first",
        )

    await session.delete(role)
    await session.commit()
