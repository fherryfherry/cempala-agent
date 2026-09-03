"""Login/logout/me (ADR-016). No auth dependency on this router — must be reachable
while logged out."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    create_session_cookie,
    get_current_user,
    verify_password,
)
from app.db.models import User, WorkspaceMember
from app.db.session import get_session
from app.schemas.user import LoginRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "invalid email or password")

    response.set_cookie(
        SESSION_COOKIE,
        create_session_cookie(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return user


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)


@router.get("/me")
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    memberships = (
        await session.scalars(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
        )
    ).all()
    return {
        "user": UserOut.model_validate(user),
        "memberships": [
            {"workspace_id": m.workspace_id, "role": m.role} for m in memberships
        ],
    }
