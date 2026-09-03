"""Global user management — who is allowed to log in at all (ADR-016). Superadmin
only. No DELETE: `is_active=false` (deactivate) instead, for an audit trail."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.auth import hash_password, require_superadmin
from app.db.models import User
from app.db.session import get_session
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


async def _get_user_or_404(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(404, "not_found", f"user {user_id} not found")
    return user


async def _active_superadmin_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).where(User.is_superadmin.is_(True), User.is_active.is_(True))
    )


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(get_session), _: User = Depends(require_superadmin)
):
    return (await session.scalars(select(User).order_by(User.created_at))).all()


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_superadmin),
):
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        is_superadmin=body.is_superadmin,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "duplicate_email", f"a user with email '{body.email}' already exists")
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    body: UserUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(require_superadmin),
):
    user = await _get_user_or_404(session, user_id)

    would_lose_superadmin = (
        body.is_superadmin is False and user.is_superadmin
    ) or (body.is_active is False and user.is_active and user.is_superadmin)
    if would_lose_superadmin and await _active_superadmin_count(session) <= 1:
        raise AppError(
            409, "last_superadmin", "cannot deactivate/demote the last remaining superadmin"
        )

    if body.is_superadmin is not None:
        user.is_superadmin = body.is_superadmin
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)

    await session.commit()
    await session.refresh(user)
    return user
