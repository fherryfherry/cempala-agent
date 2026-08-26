"""Tests for AgyTool (fake binary as shell script, no real agy/LLM)."""

import asyncio
import stat
import subprocess
import time

import pytest

from app.agents.agy_tool import AgyTool
from app.agents.base import TOOLS, RunContext
from app.config import settings


def _write_script(path, body):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _ctx(tmp_path, **overrides) -> RunContext:
    defaults = dict(
        run_id="run-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        agent_model="gemini-3.7-flash-high",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="do the thing",
        agent_name="Test Agent",
        ticket_key="",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


async def _collect(ctx):
    return [ev async for ev in AgyTool().run(ctx)]


def test_registry_maps_agy_to_agy_tool():
    assert TOOLS["agy"] is AgyTool


def test_happy_path_maps_events_and_ends_done(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "agy",
        r"""
printf '{"event": "init", "conversation_id": "sess-abc", "init": {"cwd": "/tmp", "tools": [], "permission_mode": "request-review"}}\n'
printf '{"event": "step_update", "step_update": {"conversation_id": "sess-abc", "step_index": 0, "state": "DONE", "step_type": "user_input"}}\n'
printf '{"event": "step_update", "step_update": {"conversation_id": "sess-abc", "step_index": 1, "state": "DONE", "step_type": "agent_response", "text_delta": "hi\\n", "usage": {"input_tokens": 10, "output_tokens": 5}}}\n'
printf '{"event": "result", "result": {"conversation_id": "sess-abc", "status": "SUCCESS", "response": "hi\\n", "num_turns": 1, "usage": {"input_tokens": 10, "output_tokens": 5}}}\n'
""",
    )
    monkeypatch.setattr(settings, "AGY_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    types = [e.type for e in events]
    assert types == ["assistant_text", "run_ended"]
    final = events[-1]
    assert final.payload["status"] == "done"
    assert final.payload["session_id"] == "sess-abc"
    assert final.payload["tokens_in"] == 10
    assert final.payload["tokens_out"] == 5


def test_result_status_not_success_yields_failed(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "agy",
        r"""
printf '{"event": "init", "conversation_id": "sess-abc"}\n'
printf '{"event": "result", "result": {"conversation_id": "sess-abc", "status": "ERROR"}}\n'
""",
    )
    monkeypatch.setattr(settings, "AGY_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    final = events[-1]
    assert final.type == "run_ended"
    assert final.payload["status"] == "failed"
    assert "ERROR" in final.payload["error"]


def test_malformed_lines_are_skipped_and_run_continues(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "agy",
        r"""
printf 'not json at all\n'
printf '{"event": "unknown_shape"}\n'
printf '{"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "after"}}\n'
printf '{"event": "result", "result": {"status": "SUCCESS"}}\n'
""",
    )
    monkeypatch.setattr(settings, "AGY_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert [e.type for e in events] == ["assistant_text", "run_ended"]
    assert events[-1].payload["status"] == "done"


def test_binary_not_found_yields_failed_run_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AGY_BIN", str(tmp_path / "no-such-binary"))

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert len(events) == 1
    assert events[0].type == "run_ended"
    assert events[0].payload["status"] == "failed"
    assert "not found" in events[0].payload["error"]


def test_nonzero_exit_fails_with_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "agy",
        r"""
printf '{"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "partial"}}\n'
>&2 printf 'boom: something broke\n'
exit 1
""",
    )
    monkeypatch.setattr(settings, "AGY_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    final = events[-1]
    assert final.type == "run_ended"
    assert final.payload["status"] == "failed"
    assert "boom: something broke" in final.payload["error"]


def test_cancel_actually_kills_child_process(tmp_path, monkeypatch):
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(tmp_path / "agy", f"exec {sleep_marker}\n")
    monkeypatch.setattr(settings, "AGY_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in AgyTool().run(ctx)]

        consume_task = asyncio.create_task(_consume())

        deadline = time.monotonic() + 5
        pids: list[int] = []
        while time.monotonic() < deadline and not pids:
            found = subprocess.run(
                ["pgrep", "-f", sleep_marker], capture_output=True, text=True
            ).stdout.split()
            pids = [int(p) for p in found]
            if not pids:
                await asyncio.sleep(0.05)

        ctx.cancel_event.set()
        events = await consume_task
        return events, pids

    events, pids = asyncio.run(_run_and_cancel())

    assert events[-1].type == "run_ended"
    assert events[-1].payload["status"] == "cancelled"
    assert pids, f"expected to find the spawned `{sleep_marker}` process via pgrep"
    for pid in pids:
        result = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True)
        assert result.returncode != 0, f"pid {pid} is still alive after cancel"
