"""AgyTool: AgentTool adapter for Google's `agy` (Antigravity) CLI.

Third real adapter alongside `OpenCodeTool`/`ClaudeTool`/`CodexTool` (agy used to be
`StubTool`, per ADR-006's "revisit when a third adapter arrives"). Runs
`agy -p ... --output-format stream-json ...` as a subprocess, streams stdout
line-by-line, maps each JSON line to an `AdapterEvent`, and yields a final
`run_ended` event — same terminal-event contract as the other adapters.

Schema confirmed live against the real installed `agy` CLI (not guessed): each
stdout line is one JSON object keyed by `"event"` (not `"type"`, unlike the other
three adapters). `"init"` carries `conversation_id` (used as `session_id`).
`"step_update"` with `step_update.step_type == "agent_response"` carries incremental
`text_delta` chunks. `"result"` is the terminal summary: `result.status == "SUCCESS"`
on success (anything else treated as failed), plus `result.usage.input_tokens`/
`output_tokens` (no cost field). Unknown/malformed lines are skipped, never crash the
run — same defensive posture as the other adapters.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import AsyncIterator

from app.agents.base import AdapterEvent, RunContext
from app.config import settings

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


class AgyTool:
    async def run(self, ctx: RunContext) -> AsyncIterator[AdapterEvent]:
        prompt = ctx.prompt
        for attachment in ctx.attachments:
            # No generic local-file attach flag on this CLI — use the same `@path`
            # mention convention as ClaudeTool/CodexTool.
            prompt += f"\n@{attachment}"

        cmd = [
            settings.AGY_BIN,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--model",
            ctx.agent_model,
        ]
        if ctx.prev_session_id:
            cmd += ["--conversation", ctx.prev_session_id]

        # Git identity for agent commits — same rationale as the other adapters.
        git_name = ctx.agent_name or ctx.agent_id
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", git_name.lower()).strip("-") or ctx.agent_id
        git_email = f"{slug}@agent.local"
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": git_name,
            "GIT_AUTHOR_EMAIL": git_email,
            "GIT_COMMITTER_NAME": git_name,
            "GIT_COMMITTER_EMAIL": git_email,
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=ctx.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=settings.AGY_STREAM_LIMIT_BYTES,
                env=env,
            )
        except FileNotFoundError:
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": f"agy binary not found: {settings.AGY_BIN!r}",
                },
            )
            return

        async for ev in self._run_stream(proc, ctx):
            yield ev

    async def _run_stream(
        self, proc: asyncio.subprocess.Process, ctx: RunContext
    ) -> AsyncIterator[AdapterEvent]:
        session_id: str | None = None
        tokens_in = 0.0
        tokens_out = 0.0
        status = "done"
        error_message: str | None = None
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
                                "agy stdout line exceeds stream limit "
                                f"({settings.AGY_STREAM_LIMIT_BYTES} bytes); "
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

            event = data.get("event")

            if event == "init":
                cid = data.get("conversation_id")
                if session_id is None and cid:
                    session_id = str(cid)
                continue

            if event == "step_update":
                step_update = data.get("step_update") if isinstance(data.get("step_update"), dict) else {}
                if step_update.get("step_type") == "agent_response":
                    text_delta = step_update.get("text_delta")
                    if isinstance(text_delta, str) and text_delta:
                        yield AdapterEvent("assistant_text", {"text": text_delta})
                continue

            if event == "result":
                result = data.get("result") if isinstance(data.get("result"), dict) else {}
                cid = result.get("conversation_id")
                if cid:
                    session_id = str(cid)
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                tokens_in += _num(usage.get("input_tokens"))
                tokens_out += _num(usage.get("output_tokens"))
                if result.get("status") != "SUCCESS":
                    status = "failed"
                    error_message = str(result.get("status") or "agy run failed")
                continue

            # Unrecognized shape — skip, never kill the run.

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
                    "cost": 0.0,
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
                    "error": stderr_text or f"agy exited with code {returncode}",
                    "session_id": session_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost": 0.0,
                },
            )
            return

        if status == "failed":
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": error_message or "agy run failed",
                    "session_id": session_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost": 0.0,
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
                "cost": 0.0,
            },
        )

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Real process termination — same kill-switch contract as the other adapters."""
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
