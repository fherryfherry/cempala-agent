"""Shared pytest configuration — disables the routine scheduler during tests so it
doesn't open sessions on disposed test engines mid-suite (race that corrupted
in-memory test DBs after the 60s first-tick delay).
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_routine_scheduler(monkeypatch):
    """Replace run_scheduler with a no-op for every test — the real scheduler is
    tested directly via routine_scheduler._tick() in test_routines.py.
    """
    import app.main as main_mod
    import app.core.routine_scheduler as rs

    async def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr(main_mod, "run_scheduler", _noop)
    monkeypatch.setattr(rs, "run_scheduler", _noop)