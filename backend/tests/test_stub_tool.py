"""Tests for MAP-021 StubTool + TOOLS registry."""

import pytest

from app.agents.base import TOOLS, RunContext
from app.agents.claude_tool import ClaudeTool
from app.agents.opencode_tool import OpenCodeTool
from app.agents.stub_tool import StubTool


def _ctx(tmp_path) -> RunContext:
    return RunContext(
        run_id="run-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        agent_model="claude/whatever",
        ticket_id="ticket-1",
        repo_path=str(tmp_path),
        prompt="do the thing",
        agent_name="stub-agent",
        ticket_key="",
    )


@pytest.mark.asyncio
async def test_run_yields_events_without_raising(tmp_path):
    events = [ev async for ev in StubTool().run(_ctx(tmp_path))]
    assert events


@pytest.mark.asyncio
async def test_terminal_event_is_failed_with_readable_error(tmp_path):
    events = [ev async for ev in StubTool().run(_ctx(tmp_path))]
    terminal = events[-1]
    assert terminal.type == "run_ended"
    assert terminal.payload["status"] == "failed"
    assert terminal.payload.get("error")


def test_registry_maps_all_tool_kinds():
    assert TOOLS == {
        "opencode": OpenCodeTool,
        "claude": ClaudeTool,
        "agy": StubTool,
        "codex": StubTool,
    }
