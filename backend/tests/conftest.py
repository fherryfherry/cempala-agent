"""Shared pytest configuration — disables the routine + auto-check schedulers
during tests so they don't open sessions on disposed test engines mid-suite
(race that corrupted in-memory test DBs after the 60s first-tick delay).
"""

import pytest
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, hash_password
from app.db.models import User
from app.db.session import get_session
from app.main import app

# Fixed test superadmin — every test file's `client` fixture builds its own
# throwaway in-memory DB and overrides `get_session` independently, so this
# dependency override (applied to every test, ADR-016) resolves against
# *whichever* DB is current at request time via its own `Depends(get_session)`,
# lazily inserting the row so per-test DBs don't need their own bootstrap.
# Individual auth-specific tests (tests/test_auth_api.py) reassign or remove
# `app.dependency_overrides[get_current_user]` within the test body to exercise
# the real login/RBAC paths instead of this blanket bypass.
_TEST_USER_ID = "test-superadmin-0001"
_TEST_USER_EMAIL = "test-superadmin@example.com"


async def _test_current_user(session: AsyncSession = Depends(get_session)) -> User:
    user = await session.get(User, _TEST_USER_ID)
    if user is None:
        user = User(
            id=_TEST_USER_ID,
            email=_TEST_USER_EMAIL,
            password_hash=hash_password("test-password"),
            is_superadmin=True,
        )
        session.add(user)
        await session.commit()
    return user


@pytest.fixture(autouse=True)
def _auth_as_superadmin():
    app.dependency_overrides[get_current_user] = _test_current_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def _no_env_admin_bootstrap(monkeypatch):
    """Every `client` fixture's TestClient startup runs the real lifespan,
    which calls `bootstrap_admin()` — that reads `settings.ADMIN_EMAIL`/
    `ADMIN_PASSWORD`, which pydantic-settings loads from `backend/.env`
    regardless of test context. A developer's local `.env` (set for their own
    dev DB) would otherwise silently inject an extra superadmin into every
    test's throwaway DB, breaking any test that counts superadmins (e.g. the
    last-superadmin guard). Tests must not depend on what's in `.env`."""
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_EMAIL", None)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", None)


@pytest.fixture(autouse=True)
def _disable_background_schedulers(monkeypatch):
    """Replace run_scheduler/run_auto_check with no-ops for every test — the real
    schedulers are tested directly via their `_tick()` functions in their own
    test files.
    """
    import app.main as main_mod
    import app.core.routine_scheduler as rs
    import app.core.auto_check as ac

    async def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr(main_mod, "run_scheduler", _noop)
    monkeypatch.setattr(rs, "run_scheduler", _noop)
    monkeypatch.setattr(main_mod, "run_auto_check", _noop)
    monkeypatch.setattr(ac, "run_auto_check", _noop)