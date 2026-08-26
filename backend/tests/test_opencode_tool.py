"""Tests for MAP-020 OpenCodeTool (fake binary as shell script, no real opencode/LLM)."""

import asyncio
import json
import os
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
        agent_name="Test Agent",
        ticket_key="",
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


def test_oversized_line_is_skipped_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_STREAM_LIMIT_BYTES", 1024)
    big = "x" * 8192
    script = _write_script(
        tmp_path / "opencode",
        f"""printf '{{"type": "tool_result", "output": "{big}"}}\\n'
printf '{{"type": "assistant_text", "text": "after"}}\\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1
    assert "exceeds stream limit" in errors[0].payload["error"]
    assert [e.type for e in events] == ["error", "assistant_text", "run_ended"]
    assert events[-1].payload["status"] == "done"


def test_oversized_line_does_not_break_surrounding_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_STREAM_LIMIT_BYTES", 1024)
    big = "y" * 8192
    script = _write_script(
        tmp_path / "opencode",
        f"""printf '{{"type": "assistant_text", "text": "before"}}\\n'
printf '{{"type": "tool_result", "output": "{big}"}}\\n'
printf '{{"type": "assistant_text", "text": "after"}}\\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    texts = [e.payload.get("text") for e in events if e.type == "assistant_text"]
    assert texts == ["before", "after"]
    assert [e.type for e in events] == [
        "assistant_text",
        "error",
        "assistant_text",
        "run_ended",
    ]
    assert events[-1].payload["status"] == "done"


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


def test_nonzero_exit_strips_ansi_from_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
>&2 printf '\033[91m\033[1mError: \033[0mFile not found: whatever\n'
exit 1
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    final = events[-1]
    assert final.payload["status"] == "failed"
    assert "\x1b" not in final.payload["error"]
    assert final.payload["error"] == "Error: File not found: whatever"


def test_nonzero_exit_truncates_huge_stderr(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
python3 -c "print('x' * 5000)" 1>&2
exit 1
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    final = events[-1]
    assert final.payload["status"] == "failed"
    error = final.payload["error"]
    assert len(error) < 5000
    assert error.startswith("x" * 100)
    assert "truncated" in error


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


def test_mcp_config_env_and_cleanup(tmp_path, monkeypatch):
    """MCP wiring (ADR-011): a per-run opencode.json is written, its path is passed
    via OPENCODE_CONFIG env, and the file is removed when the run finishes."""
    script = _write_script(
        tmp_path / "opencode",
        r"""
python3 -c "import os, json; print(json.dumps({'type': 'assistant_text', 'text': os.environ.get('OPENCODE_CONFIG','')}))" "$@"
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    monkeypatch.setattr(settings, "MAP_MCP_ENABLED", True)

    ctx = _ctx(tmp_path, workspace_id="ws-mcp", agent_id="agent-mcp")
    events = asyncio.run(_collect(ctx))

    assistant = next(e for e in events if e.type == "assistant_text")
    config_path = assistant.payload["text"]
    assert config_path, "OPENCODE_CONFIG env must be set for the subprocess"

    import json as _json

    # The file is cleaned up after the run — so copy it inside the subprocess first.
    # Re-run with a script that copies the config before we inspect it.
    copied = tmp_path / "mcp-config-copy.json"
    script2 = _write_script(
        tmp_path / "opencode",
        f"""
python3 -c "import os, shutil, json; shutil.copy(os.environ['OPENCODE_CONFIG'], {str(copied)!r}); print(json.dumps({{'type': 'assistant_text', 'text': os.environ.get('OPENCODE_CONFIG','')}}))" "$@"
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script2)
    ctx2 = _ctx(tmp_path, workspace_id="ws-mcp", agent_id="agent-mcp")
    events2 = asyncio.run(_collect(ctx2))
    assistant2 = next(e for e in events2 if e.type == "assistant_text")
    path2 = assistant2.payload["text"]

    with open(copied) as f:
        config = json.load(f)
    assert "map-tickets" in config["mcp"]
    assert config["mcp"]["map-tickets"]["env"]["MAP_WORKSPACE_ID"] == "ws-mcp"
    assert config["mcp"]["map-tickets"]["env"]["MAP_AGENT_ID"] == "agent-mcp"

    # Config file must be cleaned up after the run.
    assert not os.path.exists(path2)
    assert not os.path.exists(config_path)


def test_mcp_config_disabled_skips_env(tmp_path, monkeypatch):
    script = _write_script(
        tmp_path / "opencode",
        r"""
python3 -c "import os, json; print(json.dumps({'type': 'assistant_text', 'text': os.environ.get('OPENCODE_CONFIG','NONE')}))" "$@"
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)
    monkeypatch.setattr(settings, "MAP_MCP_ENABLED", False)

    events = asyncio.run(_collect(_ctx(tmp_path)))

    assistant = next(e for e in events if e.type == "assistant_text")
    assert assistant.payload["text"] == "NONE"


_ARGV_ECHO_SCRIPT = r"""
python3 -c "import sys, json; print(json.dumps({'type': 'assistant_text', 'text': json.dumps(sys.argv[1:])}))" "$@"
"""


def test_attachment_flags_included(tmp_path, monkeypatch):
    script = _write_script(tmp_path / "opencode", _ARGV_ECHO_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    att1 = str(tmp_path / "a.png")
    att2 = str(tmp_path / "b.png")
    ctx = _ctx(tmp_path, attachments=[att1, att2])
    events = asyncio.run(_collect(ctx))

    assistant = next(e for e in events if e.type == "assistant_text")
    argv = json.loads(assistant.payload["text"])
    assert f"--file={att1}" in argv
    assert f"--file={att2}" in argv


def test_prompt_precedes_attachment_flags(tmp_path, monkeypatch):
    """Regression test: opencode's real CLI parser (yargs) treats both the
    `message` positional and `--file`/`-f` as array-typed. Once an array-typed
    flag like `--file` appears on the command line, it keeps consuming every
    subsequent bare token into its own array — regardless of `--file=value`
    vs `-f value` form — so a prompt placed *after* `--file` gets swallowed
    as a "file" instead of becoming the message, and opencode fails with
    "File not found: <the whole prompt>". Confirmed against the real
    installed opencode 1.18.18 binary: placing the prompt *before* any
    `--file` flags fixes it. This fake shell-script binary can't reproduce
    yargs' parsing itself, but it locks in the argv order our code must keep
    emitting: the prompt comes before any `--file=...` tokens.
    """
    script = _write_script(tmp_path / "opencode", _ARGV_ECHO_SCRIPT)
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    att1 = str(tmp_path / "a.png")
    ctx = _ctx(tmp_path, attachments=[att1], prompt="do the thing")
    events = asyncio.run(_collect(ctx))

    assistant = next(e for e in events if e.type == "assistant_text")
    argv = json.loads(assistant.payload["text"])
    assert argv.index("do the thing") < argv.index(f"--file={att1}")
    assert f"--file={att1}" in argv
    assert not any(token.startswith("-f") and token != f"--file={att1}" for token in argv)


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


def test_git_author_identity_env(tmp_path, monkeypatch):
    """GIT_AUTHOR_NAME/EMAIL and GIT_COMMITTER_NAME/EMAIL are set on the subprocess."""
    script = _write_script(
        tmp_path / "opencode",
        """
printf '{"type": "assistant_text", "text": "git-author-name:%s", "session_id": "sess-1"}\n' "$GIT_AUTHOR_NAME"
printf '{"type": "assistant_text", "text": "git-author-email:%s", "session_id": "sess-1"}\n' "$GIT_AUTHOR_EMAIL"
printf '{"type": "assistant_text", "text": "git-committer-name:%s", "session_id": "sess-1"}\n' "$GIT_COMMITTER_NAME"
printf '{"type": "assistant_text", "text": "git-committer-email:%s", "session_id": "sess-1"}\n' "$GIT_COMMITTER_EMAIL"
printf '{"type": "run_ended", "status": "done", "session_id": "sess-1"}\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, agent_name="Budi")))
    final = events[-1]
    assert final.type == "run_ended"
    assert final.payload["status"] == "done"
    output = "".join(
        e.payload.get("text", "")
        for e in events
        if e.type == "assistant_text"
    )
    assert "git-author-name:Budi" in output
    assert "git-author-email:budi@agent.local" in output
    assert "git-committer-name:Budi" in output
    assert "git-committer-email:budi@agent.local" in output


def test_git_author_identity_fallback_to_agent_id(tmp_path, monkeypatch):
    """When agent_name is empty, agent_id is used for the git name."""
    script = _write_script(
        tmp_path / "opencode",
        """
printf '{"type": "assistant_text", "text": "git-author-name:%s", "session_id": "sess-1"}\n' "$GIT_AUTHOR_NAME"
printf '{"type": "assistant_text", "text": "git-author-email:%s", "session_id": "sess-1"}\n' "$GIT_AUTHOR_EMAIL"
printf '{"type": "run_ended", "status": "done", "session_id": "sess-1"}\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, agent_name="", agent_id="agent-XYZ-123")))
    final = events[-1]
    assert final.type == "run_ended"
    output = "".join(
        e.payload.get("text", "")
        for e in events
        if e.type == "assistant_text"
    )
    assert "git-author-name:agent-XYZ-123" in output
    assert "git-author-email:agent-xyz-123@agent.local" in output


def test_git_author_identity_special_chars_in_name(tmp_path, monkeypatch):
    """Names with spaces, parentheses, etc. are slugified for the email."""
    script = _write_script(
        tmp_path / "opencode",
        """
printf '{"type": "assistant_text", "text": "git-author-name:%s", "session_id": "sess-1"}\n' "$GIT_AUTHOR_NAME"
printf '{"type": "assistant_text", "text": "git-author-email:%s", "session_id": "sess-1"}\n' "$GIT_AUTHOR_EMAIL"
printf '{"type": "run_ended", "status": "done", "session_id": "sess-1"}\n'
""",
    )
    monkeypatch.setattr(settings, "OPENCODE_BIN", script)

    events = asyncio.run(_collect(_ctx(tmp_path, agent_name="Budi (Engineer)")))
    final = events[-1]
    assert final.type == "run_ended"
    output = "".join(
        e.payload.get("text", "")
        for e in events
        if e.type == "assistant_text"
    )
    assert "git-author-name:Budi (Engineer)" in output
