"""AgentTool protocol + RunContext (docs/02-tsd.md §4.1).

Plain data in, no ORM objects — the adapter (MAP-020) and later the
orchestrator (MAP-023) both need to construct/consume this without a DB
session, same rationale as `agents/prompts.py`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass
class RunContext:
    """Everything one `AgentTool.run()` call needs.

    `workspace_id`/`ticket_id`/`agent_id` are ids, not ORM rows — the adapter
    only ever needs `agent_model` (the "provider/model" string) and
    `repo_path` off of them; carrying full rows here would give this
    DB-agnostic module an implicit ORM dependency for no benefit.
    """

    run_id: str
    workspace_id: str
    agent_id: str
    agent_model: str  # "provider/model", passed to `opencode run -m`
    ticket_id: str
    repo_path: str
    prompt: str
    attachments: list[str] = field(default_factory=list)  # file paths, passed via `-f`
    prev_session_id: str | None = None  # passed via `-s` when set
    guardrails: dict = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

@dataclass
class AdapterEvent:
    """Lightweight domain event yielded by an `AgentTool.run()`.

    Not the DB `Event` ORM row — the caller (orchestrator, MAP-023) maps
    this to `EventBus.publish(type=..., payload=...)`. Terminal event is
    always `type="run_ended"` with `payload["status"]` in
    {"done", "failed", "cancelled"} and `payload["error"]` set on failure.
    """

    type: str
    payload: dict


class AgentTool(Protocol):
    async def run(self, ctx: RunContext) -> AsyncIterator[AdapterEvent]: ...


def _tools() -> dict[str, type]:
    """Deferred import to dodge a circular import (opencode_tool/stub_tool import base)."""
    from app.agents.opencode_tool import OpenCodeTool
    from app.agents.stub_tool import StubTool

    return {"opencode": OpenCodeTool, "claude": StubTool, "agy": StubTool, "codex": StubTool}


TOOLS: dict[str, type] = _tools()
