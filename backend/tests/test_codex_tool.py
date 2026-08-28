"""Tests for CodexTool (fake binary as shell script, no real codex/LLM)."""

import asyncio
import stat
import subprocess
import time

import pytest

from app.agents.base import TOOLS, RunContext
from app.agents.codex_tool import CodexTool
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
        agent_model="gpt-5.1-codex",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="do the thing",
        agent_name="Test Agent",
        ticket_key="",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


async def _collect(ctx):
    return [ev async for ev in CodexTool().run(ctx)]


def test_registry_maps_codex_to_codex_tool():
    assert TOOLS["codex"] is CodexTool


def test_happy_path_maps_events_and_ends_done(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
printf '{"type": "thread.started", "thread_id": "sess-abc"}\n'
printf '{"type": "turn.started"}\n'
printf '{"type": "item.started", "item": {"id": "item_0", "type": "command_execution", "command": "echo hi", "aggregated_output": "", "exit_code": null, "status": "in_progress"}}\n'
printf '{"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "echo hi", "aggregated_output": "hi\\n", "exit_code": 0, "status": "completed"}}\n'
printf '{"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "done"}}\n'
printf '{"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "cache_write_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 1}}\n'
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    types = [e.type for e in events]
    assert types == ["tool_call", "tool_result", "assistant_text", "run_ended"]
    final = events[-1]
    assert final.payload["status"] == "done"
    assert final.payload["session_id"] == "sess-abc"
    assert final.payload["tokens_in"] == 10
    assert final.payload["tokens_out"] == 5


def test_malformed_lines_are_skipped_and_run_continues(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
printf 'not json at all\n'
printf '{"type": "unknown_shape"}\n'
printf '{"type": "item.completed", "item": {"type": "agent_message", "text": "after"}}\n'
printf '{"type": "turn.completed", "usage": {}}\n'
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert [e.type for e in events] == ["assistant_text", "run_ended"]
    assert events[-1].payload["status"] == "done"


def test_binary_not_found_yields_failed_run_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CODEX_BIN", str(tmp_path / "no-such-binary"))

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert len(events) == 1
    assert events[0].type == "run_ended"
    assert events[0].payload["status"] == "failed"
    assert "not found" in events[0].payload["error"]


def test_nonzero_exit_fails_with_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
printf '{"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}\n'
>&2 printf 'boom: something broke\n'
exit 1
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    final = events[-1]
    assert final.type == "run_ended"
    assert final.payload["status"] == "failed"
    assert "boom: something broke" in final.payload["error"]


def test_cancel_actually_kills_child_process(tmp_path, monkeypatch):
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(tmp_path / "codex", f"exec {sleep_marker}\n")
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in CodexTool().run(ctx)]

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


def test_attachments_appended_as_mentions(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
printf '{"type": "turn.completed", "usage": {}}\n'
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, attachments=["/tmp/a.txt"])))
    assert events[-1].payload["status"] == "done"


def test_resume_session_flag(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
printf '{"type": "turn.completed", "usage": {}}\n'
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, prev_session_id="sess-prev")))
    assert events[-1].payload["status"] == "done"


def test_oversized_line_is_skipped_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CODEX_STREAM_LIMIT_BYTES", 1024)
    big = "x" * 8192
    script = _write_script(
        tmp_path / "codex",
        f"""printf '{{"type": "item.completed", "item": {{"type": "agent_message", "text": "{big}"}}}}\\n'
printf '{{"type": "turn.completed", "usage": {{}}}}\\n'
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1
    assert "exceeds stream limit" in errors[0].payload["error"]
    assert events[-1].payload["status"] == "done"


def test_num_handles_bad_values():
    from app.agents.codex_tool import _num

    assert _num("abc") == 0.0
    assert _num(None) == 0.0
    assert _num("3.5") == 3.5


def test_nonzero_exit_truncates_huge_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
python3 -c "print('x' * 5000)" 1>&2
exit 1
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    final = events[-1]
    assert final.payload["status"] == "failed"
    error = final.payload["error"]
    assert len(error) < 5000
    assert error.startswith("x" * 100)
    assert "truncated" in error


def test_non_dict_json_line_skipped(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "codex",
        r"""
printf '[1, 2, 3]\n'
printf '{"type": "thread.started", "thread_id": "sess-abc"}\n'
printf '{"type": "turn.completed", "usage": {}}\n'
""",
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert events[-1].payload["status"] == "done"


def test_terminate_noop_when_process_already_exited(tmp_path):
    async def _run():
        proc = await asyncio.create_subprocess_exec("true")
        await proc.wait()
        await CodexTool._terminate(proc)  # must return immediately, no terminate()/kill()

    asyncio.run(_run())


def test_cancel_kills_process_that_ignores_sigterm(tmp_path, monkeypatch):
    import app.agents.codex_tool as codex_tool_mod

    monkeypatch.setattr(codex_tool_mod, "_TERMINATE_GRACE_SECONDS", 0.2)
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(
        tmp_path / "codex", f"trap '' TERM\nexec {sleep_marker}\n"
    )
    monkeypatch.setattr(settings, "CODEX_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in CodexTool().run(ctx)]

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
