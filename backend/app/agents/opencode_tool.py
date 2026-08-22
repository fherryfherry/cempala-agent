"""OpenCodeTool: the only active AgentTool adapter (docs/02-tsd.md §4.2, MAP-020).

Runs `opencode run --format json ...` as a subprocess, streams stdout
line-by-line, maps each JSON line to an `AdapterEvent`, and yields a final
`run_ended` event carrying status/session_id/tokens/cost for the caller to
persist.

Assumption (undocumented upstream, no real opencode JSON schema to inspect —
noted per MAP-020 scope): each stdout line is a JSON object that MAY carry a
top-level `"session_id"` string, and MAY carry numeric `"tokens_in"`,
`"tokens_out"`, `"cost"` fields. We take the first non-empty `session_id` we
see and sum the numeric fields across all lines where present. Absence of any
of these fields is handled gracefully (defaults 0 / None), since the AC only
requires the accumulation mechanism to not crash on missing fields, not to
match a real binary's exact schema.

Event type mapping assumption: each line's `"type"` field is one of
assistant_text | reasoning | tool_call | tool_result | error — passed through
verbatim as the `AdapterEvent.type` with the full parsed line as payload.
Lines with an unrecognized/missing `"type"`, or that aren't valid JSON, are
skipped (but still scanned for session_id/token/cost above) — never kills
the run.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from app.agents.base import AdapterEvent, RunContext
from app.config import settings

_KNOWN_EVENT_TYPES = {"assistant_text", "reasoning", "tool_call", "tool_result", "error"}
_TERMINATE_GRACE_SECONDS = 5


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class OpenCodeTool:
    async def run(self, ctx: RunContext) -> AsyncIterator[AdapterEvent]:
        cmd = [
            settings.OPENCODE_BIN,
            "run",
            "--format",
            "json",
            "--dir",
            ctx.repo_path,
            "-m",
            ctx.agent_model,
            "--auto",
        ]
        if ctx.prev_session_id:
            cmd += ["-s", ctx.prev_session_id]
        for attachment in ctx.attachments:
            cmd += ["-f", attachment]
        cmd.append(ctx.prompt)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": f"opencode binary not found: {settings.OPENCODE_BIN!r}",
                },
            )
            return

        session_id: str | None = None
        tokens_in = 0.0
        tokens_out = 0.0
        cost = 0.0
        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            async for line in proc.stderr:
                stderr_chunks.append(line)

        stderr_task = asyncio.create_task(drain_stderr())
        cancelled = False

        try:
            while True:
                readline_task = asyncio.create_task(proc.stdout.readline())
                cancel_task = asyncio.create_task(ctx.cancel_event.wait())
                done, pending = await asyncio.wait(
                    {readline_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_task in done:
                    readline_task.cancel()
                    for t in pending:
                        t.cancel()
                    cancelled = True
                    break
                cancel_task.cancel()
                line = readline_task.result()
                if not line:
                    break

                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue

                if session_id is None and data.get("session_id"):
                    session_id = str(data["session_id"])
                if "tokens_in" in data:
                    tokens_in += _num(data["tokens_in"])
                if "tokens_out" in data:
                    tokens_out += _num(data["tokens_out"])
                if "cost" in data:
                    cost += _num(data["cost"])

                event_type = data.get("type")
                if event_type in _KNOWN_EVENT_TYPES:
                    yield AdapterEvent(event_type, data)
        finally:
            pass

        if cancelled:
            await self._terminate(proc)
            stderr_task.cancel()
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "cancelled",
                    "session_id": session_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost": cost,
                },
            )
            return

        await stderr_task
        returncode = await proc.wait()
        stderr_text = b"".join(stderr_chunks).decode(errors="replace").strip()

        if returncode != 0:
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": stderr_text or f"opencode exited with code {returncode}",
                    "session_id": session_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost": cost,
                },
            )
            return

        yield AdapterEvent(
            "run_ended",
            {
                "status": "done",
                "session_id": session_id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": cost,
            },
        )

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Real process termination — safety-critical, not just async-generator stop.

        `process.terminate()`, wait up to 5s, then `kill()` if still alive
        (docs/02-tsd.md §4.2, CLAUDE.md kill-switch note).
        """
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
