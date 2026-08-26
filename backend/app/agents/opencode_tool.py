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
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator

from app.agents.base import AdapterEvent, RunContext
from app.agents.mcp_config import mcp_config_path
from app.config import settings

_GIT_ENV_PREFIX = "GIT_"

_KNOWN_EVENT_TYPES = {"assistant_text", "reasoning", "tool_call", "tool_result", "error"}
_TERMINATE_GRACE_SECONDS = 5
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_STDERR_MAX_CHARS = 2000


def _clean_stderr(text: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", text).strip()
    if len(text) > _STDERR_MAX_CHARS:
        text = text[:_STDERR_MAX_CHARS] + f"... (truncated, {len(text)} chars total)"
    return text


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
        # opencode's CLI (yargs) treats `message` and `--file` as array-typed:
        # once an array-typed flag like `--file` appears, it keeps consuming
        # subsequent bare tokens into its own array — even in `--file=value`
        # form — so a prompt placed after `--file` gets swallowed as a
        # "file" instead of becoming the message (confirmed against the
        # real 1.18.18 binary). The prompt must come before any `--file`.
        cmd.append(ctx.prompt)
        for attachment in ctx.attachments:
            cmd.append(f"--file={attachment}")

        # Per-run MCP config (ADR-011): expose ticket/artifact/memory tools to the
        # agent via a temp opencode.json. Cleaned up when the run finishes.
        mcp_config = mcp_config_path(ctx.workspace_id, ctx.agent_id)

        # Git identity for agent commits: author/committer name + dummy email.
        # GIT_AUTHOR_* env vars override repo/global git config for every commit
        # made by the agent during this run — injected here so all child processes
        # (git add → git commit → ...) inherit them automatically.
        git_name = ctx.agent_name or ctx.agent_id
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", git_name.lower()).strip("-") or ctx.agent_id
        git_email = f"{slug}@agent.local"
        git_env = {
            "GIT_AUTHOR_NAME": git_name,
            "GIT_AUTHOR_EMAIL": git_email,
            "GIT_COMMITTER_NAME": git_name,
            "GIT_COMMITTER_EMAIL": git_email,
        }

        base_env = {**os.environ, **git_env}
        if mcp_config is not None:
            base_env["OPENCODE_CONFIG"] = mcp_config
        env = base_env

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=settings.OPENCODE_STREAM_LIMIT_BYTES,
                env=env,
            )
        except FileNotFoundError:
            if mcp_config is not None:
                try:
                    os.unlink(mcp_config)
                except OSError:
                    pass
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": f"opencode binary not found: {settings.OPENCODE_BIN!r}",
                },
            )
            return

        try:
            async for ev in self._run_stream(proc, ctx):
                yield ev
        finally:
            if mcp_config is not None:
                try:
                    os.unlink(mcp_config)
                except OSError:
                    pass

    async def _run_stream(
        self, proc: asyncio.subprocess.Process, ctx: RunContext
    ) -> AsyncIterator[AdapterEvent]:
        session_id: str | None = None
        tokens_in = 0.0
        tokens_out = 0.0
        cost = 0.0
        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            async for line in proc.stderr:
                try:
                    stderr_chunks.append(line)
                except ValueError:
                    pass

        stderr_task = asyncio.create_task(drain_stderr())
        cancelled = False
        limit_error_reported = False

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
                try:
                    line = readline_task.result()
                except ValueError:
                    if not limit_error_reported:
                        limit_error_reported = True
                        yield AdapterEvent(
                            "error",
                            {
                                "error": (
                                    "opencode stdout line exceeds stream limit "
                                    f"({settings.OPENCODE_STREAM_LIMIT_BYTES} bytes); "
                                    "line skipped, run continues"
                                )
                            },
                        )
                    continue
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

                # ponytail: real opencode 1.18.18 CLI JSON (observed live, MAP-033 dogfood) uses
                # "sessionID" (not "session_id") and nests tokens/cost inside
                # part.tokens/part.cost on "step_finish" lines, not top-level. MAP-020's fake
                # test binary encoded the originally-assumed flat schema, which real output
                # never matches. Kept both shapes so the fake-binary tests still pass.
                sid = data.get("session_id") or data.get("sessionID")
                if session_id is None and sid:
                    session_id = str(sid)
                if "tokens_in" in data:
                    tokens_in += _num(data["tokens_in"])
                if "tokens_out" in data:
                    tokens_out += _num(data["tokens_out"])
                if "cost" in data:
                    cost += _num(data["cost"])

                part = data.get("part") if isinstance(data.get("part"), dict) else None
                if part is not None:
                    part_tokens = part.get("tokens")
                    if isinstance(part_tokens, dict):
                        tokens_in += _num(part_tokens.get("input"))
                        tokens_out += _num(part_tokens.get("output"))
                    if "cost" in part:
                        cost += _num(part["cost"])

                event_type = data.get("type")
                if event_type in _KNOWN_EVENT_TYPES:
                    yield AdapterEvent(event_type, data)
                elif event_type == "text" and part is not None and isinstance(part.get("text"), str):
                    yield AdapterEvent("assistant_text", {**data, "text": part["text"]})
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
        stderr_text = _clean_stderr(b"".join(stderr_chunks).decode(errors="replace"))

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
