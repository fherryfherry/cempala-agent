"""Shared pytest configuration — disables the routine + auto-check schedulers
during tests so they don't open sessions on disposed test engines mid-suite
(race that corrupted in-memory test DBs after the 60s first-tick delay).
"""

import pytest


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