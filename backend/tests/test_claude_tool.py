"""Tests for ClaudeTool (fake binary as shell script, no real claude/LLM)."""

import asyncio
import stat
import subprocess
import time

import pytest

from app.agents.base import RunContext
from app.agents.claude_tool import ClaudeTool
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
        agent_model="sonnet",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="do the thing",
        agent_name="Test Agent",
        ticket_key="",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


async def _collect(ctx):
    return [ev async for ev in ClaudeTool().run(ctx)]


def test_happy_path_maps_events_and_ends_done(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "system", "subtype": "init", "session_id": "sess-abc"}\n'
printf '{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}\n'
printf '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}\n'
printf '{"type": "result", "subtype": "success", "session_id": "sess-abc", "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    types = [e.type for e in events]
    assert types == ["assistant_text", "tool_call", "run_ended"]
    final = events[-1]
    assert final.payload["status"] == "done"
    assert final.payload["session_id"] == "sess-abc"
    assert final.payload["tokens_in"] == 10
    assert final.payload["tokens_out"] == 5
    assert final.payload["cost"] == pytest.approx(0.01)


def test_malformed_lines_are_skipped_and_run_continues(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf 'not json at all\n'
printf '{"type": "unknown_shape"}\n'
printf '{"type": "assistant", "message": {"content": [{"type": "text", "text": "after"}]}}\n'
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert [e.type for e in events] == ["assistant_text", "run_ended"]
    assert events[-1].payload["status"] == "done"


def test_binary_not_found_yields_failed_run_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_BIN", str(tmp_path / "no-such-binary"))

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert len(events) == 1
    assert events[0].type == "run_ended"
    assert events[0].payload["status"] == "failed"
    assert "not found" in events[0].payload["error"]


def test_cancel_actually_kills_child_process(tmp_path, monkeypatch):
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(tmp_path / "claude", f"exec {sleep_marker}\n")
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in ClaudeTool().run(ctx)]

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
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "result", "subtype": "error_max_turns", "is_error": true, "result": "too many turns"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert events[-1].type == "run_ended"
    assert events[-1].payload["status"] == "failed"
    assert "too many turns" in events[-1].payload["error"]


def test_attachments_appended_as_mentions(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, attachments=["/tmp/a.txt", "/tmp/b.txt"])))
    assert events[-1].payload["status"] == "done"


def test_resume_session_flag(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, prev_session_id="sess-prev")))
    assert events[-1].payload["status"] == "done"


def test_user_tool_result_mapped(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}\n'
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert [e.type for e in events] == ["tool_result", "run_ended"]


def test_nonzero_exit_fails_with_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
>&2 printf 'boom\n'
exit 1
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "failed"
    assert "boom" in events[-1].payload["error"]


def test_nonzero_exit_truncates_huge_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
python3 -c "print('x' * 5000)" 1>&2
exit 1
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "failed"
    assert "truncated" in events[-1].payload["error"]


def test_oversized_line_is_skipped_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_STREAM_LIMIT_BYTES", 1024)
    big = "x" * 8192
    script = _write_script(
        tmp_path / "claude",
        f"""printf '{{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "{big}"}}]}}}}\\n'
printf '{{"type": "result", "subtype": "success"}}\\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1
    assert "exceeds stream limit" in errors[0].payload["error"]
    assert events[-1].payload["status"] == "done"


def test_non_dict_json_line_skipped(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '[1, 2, 3]\n'
printf '{"type": "result", "subtype": "success", "session_id": "sess-abc"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "done"


def test_blank_stdout_line_skipped(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '\n'
printf '{"type": "result", "subtype": "success", "session_id": "sess-abc"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "done"


def test_assistant_message_level_session_id_and_non_dict_block_skipped(tmp_path, monkeypatch):
    """session_id can arrive nested in message.session_id (not just the top-level
    "system" event), and a non-dict entry in message.content[] must be skipped."""
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "assistant", "message": {"session_id": "sess-nested", "content": ["not-a-dict", {"type": "text", "text": "hi"}]}}\n'
printf '{"type": "result", "subtype": "success"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "done"
    assert events[-1].payload["session_id"] == "sess-nested"


def test_mcp_unlink_error_swallowed_on_binary_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_BIN", str(tmp_path / "no-such-binary"))
    monkeypatch.setattr("os.unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "failed"


def test_mcp_unlink_error_swallowed_in_finally(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "claude",
        r"""
printf '{"type": "result", "subtype": "success", "session_id": "sess-abc"}\n'
""",
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)
    monkeypatch.setattr("os.unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))

    events = asyncio.run(_collect(_ctx(tmp_path)))
    assert events[-1].payload["status"] == "done"


def test_terminate_noop_when_process_already_exited(tmp_path):
    async def _run():
        proc = await asyncio.create_subprocess_exec("true")
        await proc.wait()
        await ClaudeTool._terminate(proc)  # must return immediately, no terminate()/kill()

    asyncio.run(_run())


def test_cancel_kills_process_that_ignores_sigterm(tmp_path, monkeypatch):
    import app.agents.claude_tool as claude_tool_mod

    monkeypatch.setattr(claude_tool_mod, "_TERMINATE_GRACE_SECONDS", 0.2)
    sleep_marker = f"sleep {__import__('random').randint(100000, 999999)}"
    script = _write_script(
        tmp_path / "claude", f"trap '' TERM\nexec {sleep_marker}\n"
    )
    monkeypatch.setattr(settings, "CLAUDE_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            return [ev async for ev in ClaudeTool().run(ctx)]

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
