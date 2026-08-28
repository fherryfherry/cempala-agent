"""Tests for CmdTool (fake binary as shell script, no real cmd/LLM).

Fixtures use the real `cmd --output-format json` shape confirmed against the
installed v1.36.0 binary (`{"type": "event", "event": {...}}` for streaming events,
`{"type": "result", ...}` for the terminal summary — see cmd_tool.py's docstring).
"""

import asyncio
import stat
import subprocess
import time

import pytest

from app.agents.base import RunContext
from app.agents.cmd_tool import CmdTool
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
        agent_model="claude-sonnet-5",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="do the thing",
        agent_name="Test Agent",
        ticket_key="",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


async def _collect(ctx):
    return [ev async for ev in CmdTool().run(ctx)]


def test_happy_path_maps_events_and_ends_done(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "cmd",
        r"""
printf '{"type": "event", "event": {"type": "run_start", "sessionId": "sess-abc"}}\n'
printf '{"type": "event", "event": {"type": "text_delta", "delta": "hi"}}\n'
printf '{"type": "event", "event": {"type": "text_delta", "delta": " there"}}\n'
printf '{"type": "event", "event": {"type": "tool_queued", "toolCallId": "t1", "toolName": "Read", "input": {"path": "a.py"}}}\n'
printf '{"type": "event", "event": {"type": "tool_completed", "toolCallId": "t1", "toolName": "Read", "result": "ok"}}\n'
printf '{"type": "result", "subtype": "success", "sessionId": "sess-abc", "usage": {"inputTokens": 10, "outputTokens": 5}}\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    types = [e.type for e in events]
    assert types == ["assistant_text", "assistant_text", "tool_call", "tool_result", "run_ended"]
    assert events[0].payload["text"] == "hi"
    assert events[1].payload["text"] == " there"
    assert events[2].payload == {"toolCallId": "t1", "name": "Read", "input": {"path": "a.py"}}
    assert events[3].payload == {"toolCallId": "t1", "name": "Read", "result": "ok"}
    final = events[-1]
    assert final.payload["status"] == "done"
    assert final.payload["session_id"] == "sess-abc"
    assert final.payload["tokens_in"] == 10
    assert final.payload["tokens_out"] == 5
    assert final.payload["cost"] == 0.0


def test_thinking_delta_maps_to_reasoning(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "cmd",
        r"""
printf '{"type": "event", "event": {"type": "thinking_delta", "delta": "hmm"}}\n'
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert [e.type for e in events] == ["reasoning", "run_ended"]
    assert events[0].payload["text"] == "hmm"


def test_malformed_and_unrecognized_lines_are_skipped(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "cmd",
        r"""
printf 'not json at all\n'
printf '{"type": "event", "event": {"type": "turn_start", "turnNumber": 1}}\n'
printf '{"type": "unknown_shape"}\n'
printf '{"type": "event", "event": {"type": "text_delta", "delta": "after"}}\n'
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert [e.type for e in events] == ["assistant_text", "run_ended"]
    assert events[-1].payload["status"] == "done"


def test_binary_not_found_yields_failed_run_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CMD_BIN", str(tmp_path / "no-such-binary"))

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert len(events) == 1
    assert events[0].type == "run_ended"
    assert events[0].payload["status"] == "failed"
    assert "not found" in events[0].payload["error"]


def test_cancel_actually_kills_child_process(tmp_path, monkeypatch):
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(tmp_path / "cmd", f"exec {sleep_marker}\n")
    monkeypatch.setattr(settings, "CMD_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in CmdTool().run(ctx)]

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


def test_result_error_subtype_yields_failed(tmp_path, monkeypatch):
    # Real shape captured from a live run against insufficient-credits billing error.
    script = _write_script(
        tmp_path / "cmd",
        r"""
printf '{"type": "result", "subtype": "error", "sessionId": "sess-x", "usage": {"inputTokens": 0, "outputTokens": 0}, "finalText": "", "error": "Error: You have insufficient credits to make this request."}\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert events[-1].type == "run_ended"
    assert events[-1].payload["status"] == "failed"
    assert "insufficient credits" in events[-1].payload["error"]


def test_max_turns_subtype_yields_failed(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "cmd",
        r"""
printf '{"type": "result", "subtype": "max_turns"}\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "failed"
    assert "max_turns" in events[-1].payload["error"]


def test_resume_session_flag(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "cmd",
        r"""
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, prev_session_id="sess-prev")))
    assert events[-1].payload["status"] == "done"


def test_nonzero_exit_fails_with_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "cmd",
        r"""
>&2 printf 'boom\n'
exit 1
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "failed"
    assert "boom" in events[-1].payload["error"]


def test_oversized_line_is_skipped_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CMD_STREAM_LIMIT_BYTES", 1024)
    big = "x" * 8192
    script = _write_script(
        tmp_path / "cmd",
        f"""printf '{{"type": "event", "event": {{"type": "text_delta", "delta": "{big}"}}}}\\n'
printf '{{"type": "result", "subtype": "success"}}\\n'
""",
    )
    monkeypatch.setattr(settings, "CMD_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1
    assert "exceeds stream limit" in errors[0].payload["error"]
    assert events[-1].payload["status"] == "done"


def test_terminate_noop_when_process_already_exited(tmp_path):
    async def _run():
        proc = await asyncio.create_subprocess_exec("true")
        await proc.wait()
        await CmdTool._terminate(proc)  # must return immediately, no terminate()/kill()

    asyncio.run(_run())


def test_cancel_kills_process_that_ignores_sigterm(tmp_path, monkeypatch):
    import app.agents.cmd_tool as cmd_tool_mod

    monkeypatch.setattr(cmd_tool_mod, "_TERMINATE_GRACE_SECONDS", 0.2)
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(tmp_path / "cmd", f"trap '' TERM\nexec {sleep_marker}\n")
    monkeypatch.setattr(settings, "CMD_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in CmdTool().run(ctx)]

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

    assert events[-1].payload["status"] == "cancelled"
    for pid in pids:
        deadline = time.monotonic() + 5
        alive = True
        while time.monotonic() < deadline and alive:
            result = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True)
            alive = result.returncode == 0
        assert not alive, f"pid {pid} survived SIGKILL after ignoring SIGTERM"
