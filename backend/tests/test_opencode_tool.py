"""Tests for MAP-020 OpenCodeTool (fake binary as shell script, no real opencode/LLM)."""

import asyncio
import random
import stat
import subprocess
import time

import pytest

from app.agents.base import RunContext
from app.agents.opencode_tool import OpenCodeTool
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
        agent_model="opencode/big-pickle",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="do the thing",
    )
    defaults.update(overrides)
    return RunContext(**defaults)


async def _collect(ctx):
    events = []
    async for ev in OpenCodeTool().run(ctx):
        events.append(ev)
    return events


def test_happy_path_maps_events_and_ends_done(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
printf '{"type": "assistant_text", "text": "hi", "session_id": "sess-abc", "tokens_in": 10, "tokens_out": 5, "cost": 0.01}\n'
printf '{"type": "tool_call", "name": "read"}\n'
printf '{"type": "tool_result", "output": "ok"}\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    types = [e.type for e in events]
    assert types == ["assistant_text", "tool_call", "tool_result", "run_ended"]
    final = events[-1]
    assert final.payload["status"] == "done"
    assert final.payload["session_id"] == "sess-abc"
    assert final.payload["tokens_in"] == 10
    assert final.payload["tokens_out"] == 5
    assert final.payload["cost"] == pytest.approx(0.01)


def test_binary_not_found_fails_without_crashing(tmp_path):
    ctx = _ctx(tmp_path, repo_path=str(tmp_path))
    # No monkeypatch: OPENCODE_BIN left as an explicitly bogus path via ctx-independent settings.
    from app.config import settings as real_settings

    orig = real_settings.OPENCODE_BIN
    real_settings.OPENCODE_BIN = "/nonexistent/opencode-binary-xyz"
    try:
        events = asyncio.run(_collect(ctx))
    finally:
        real_settings.OPENCODE_BIN = orig

    assert len(events) == 1
    assert events[0].type == "run_ended"
    assert events[0].payload["status"] == "failed"
    assert "not found" in events[0].payload["error"]


def test_non_json_lines_are_skipped(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
printf 'not json at all\n'
printf '{"type": "assistant_text", "text": "hi"}\n'
printf 'also garbage {{{\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert [e.type for e in events] == ["assistant_text", "run_ended"]
    assert events[-1].payload["status"] == "done"


def test_nonzero_exit_fails_with_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
printf '{"type": "assistant_text", "text": "partial"}\n'
>&2 printf 'boom: something broke\n'
exit 1
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert events[0].type == "assistant_text"
    final = events[-1]
    assert final.type == "run_ended"
    assert final.payload["status"] == "failed"
    assert "boom: something broke" in final.payload["error"]


def test_unknown_event_type_skipped_but_still_run_ended(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""printf '{"type": "mystery"}\n'""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert [e.type for e in events] == ["run_ended"]
    assert events[0].payload["status"] == "done"


def test_session_id_absent_handled_gracefully(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""printf '{"type": "assistant_text", "text": "hi"}\n'""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assert events[-1].payload["session_id"] is None


def test_prev_session_id_flag_included(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        # Echo argv back as a JSON assistant_text line so the test can inspect it.
        r"""
python3 -c "import sys, json; print(json.dumps({'type': 'assistant_text', 'text': ' '.join(sys.argv[1:])}))" "$@"
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    ctx = _ctx(tmp_path, prev_session_id="sess-prev-1")
    events = asyncio.run(_collect(ctx))

    assistant = next(e for e in events if e.type == "assistant_text")
    assert "-s sess-prev-1" in assistant.payload["text"]


def test_attachment_flags_included(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
python3 -c "import sys, json; print(json.dumps({'type': 'assistant_text', 'text': ' '.join(sys.argv[1:])}))" "$@"
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    att1 = str(tmp_path / "a.png")
    att2 = str(tmp_path / "b.png")
    ctx = _ctx(tmp_path, attachments=[att1, att2])
    events = asyncio.run(_collect(ctx))

    assistant = next(e for e in events if e.type == "assistant_text")
    text = assistant.payload["text"]
    assert f"-f {att1}" in text
    assert f"-f {att2}" in text


def test_cancel_actually_kills_child_process(tmp_path, monkeypatch):
    """Not just internal state — verified via `ps -p <pid>` that the OS process is gone.

    Uses a unique sleep duration per test run (not a fixed literal like "sleep 30")
    so `pgrep -f` can never collide with a process leaked by an unrelated, unclean
    prior test run still alive on the machine — a real failure mode hit in practice.
    """
    sleep_marker = f"sleep {random.randint(100000, 999999)}"
    script = _write_script(tmp_path / "opencode", f"exec {sleep_marker}\n")
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    async def _run_and_cancel():
        ctx = _ctx(tmp_path)

        async def _consume():
            events = []
            async for ev in OpenCodeTool().run(ctx):
                events.append(ev)
            return events

        consume_task = asyncio.create_task(_consume())

        # Poll for the subprocess to actually spawn rather than a fixed sleep — under
        # full-suite load a fixed delay isn't always long enough.
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
