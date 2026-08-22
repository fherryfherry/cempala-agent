"""StubTool: placeholder AgentTool for tool_kinds without a real adapter yet
(claude/agy/codex, docs/04-tasks.md MAP-021).

Agents may be created with these tool_kind values (MAP-008 didn't restrict
that), but there's nothing to run them with yet. `run()` must still satisfy
the `AgentTool` contract: never raise, always end on a `run_ended` event —
same convention as `OpenCodeTool`'s failure paths (base.py's terminal-event
docstring) — so the orchestrator (MAP-023) can treat every tool_kind
uniformly.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.agents.base import AdapterEvent, RunContext

_MESSAGE = "adapter belum tersedia"


class StubTool:
    async def run(self, ctx: RunContext) -> AsyncIterator[AdapterEvent]:
        yield AdapterEvent("run_ended", {"status": "failed", "error": _MESSAGE})
