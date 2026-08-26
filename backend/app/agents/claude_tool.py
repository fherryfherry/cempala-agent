"""ClaudeTool: AgentTool adapter for the `claude` (Claude Code) CLI.

Second real adapter alongside `OpenCodeTool` (ADR-007's "revisit when a third
adapter arrives" — this is that adapter, for `tool_kind="claude"`). Runs
`claude -p --output-format stream-json ...` as a subprocess, streams stdout
line-by-line, maps each JSON line to an `AdapterEvent`, and yields a final
`run_ended` event — same terminal-event contract as `OpenCodeTool`.

Assumption (schema from public docs/`claude --help`, not yet dogfooded against
a live run — see MAP-033 precedent in opencode_tool.py where the real schema
diverged from what was assumed): each stdout line is one JSON object with a
top-level `"type"` in {"system", "assistant", "user", "result"}. `"assistant"`
carries `message.content[]` blocks (`{"type": "text", "text": ...}` or
`{"type": "tool_use", ...}`); `"user"` carries `message.content[]` tool
results; `"result"` is the terminal summary carrying `session_id`,
`total_cost_usd`, and `usage.{input_tokens,output_tokens}`. Unknown/malformed
lines are skipped, never crash the run — same defensive posture as
`opencode_tool.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import AsyncIterator

from app.agents.base import AdapterEvent, RunContext
from app.agents.mcp_config import claude_mcp_config_path
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


class ClaudeTool:
    async def run(self, ctx: RunContext) -> AsyncIterator[AdapterEvent]:
        prompt = ctx.prompt
        for attachment in ctx.attachments:
            # No local-file attachment flag on this CLI (`--file` downloads by
            # file_id, not a local path) — use the same `@path` mention
            # convention a human would type in the Claude Code chat.
            prompt += f"\n@{attachment}"

        cmd = [
            settings.CLAUDE_BIN,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            ctx.agent_model,
            "--permission-mode",
            "bypassPermissions",
        ]
        if ctx.prev_session_id:
            cmd += ["--resume", ctx.prev_session_id]

        # Per-run MCP config (ADR-011), same idea as OpenCodeTool but in
        # Claude's `--mcp-config` JSON shape. Cleaned up when the run finishes.
        mcp_config = claude_mcp_config_path(ctx.workspace_id, ctx.agent_id)
        if mcp_config is not None:
            cmd += ["--mcp-config", mcp_config]

        # Git identity for agent commits — same rationale as OpenCodeTool.
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
                limit=settings.CLAUDE_STREAM_LIMIT_BYTES,
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
                    "error": f"claude binary not found: {settings.CLAUDE_BIN!r}",
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
                                "claude stdout line exceeds stream limit "
                                f"({settings.CLAUDE_STREAM_LIMIT_BYTES} bytes); "
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

            data_type = data.get("type")

            if data_type == "system":
                sid = data.get("session_id")
                if session_id is None and sid:
                    session_id = str(sid)
                continue

            if data_type == "assistant":
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                sid = message.get("session_id") or data.get("session_id")
                if session_id is None and sid:
                    session_id = str(sid)
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text" and isinstance(block.get("text"), str):
                            yield AdapterEvent("assistant_text", {"text": block["text"]})
                        elif block_type == "tool_use":
                            yield AdapterEvent("tool_call", block)
                continue

            if data_type == "user":
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            yield AdapterEvent("tool_result", block)
                continue

            if data_type == "result":
                sid = data.get("session_id")
                if sid:
                    session_id = str(sid)
                cost += _num(data.get("total_cost_usd"))
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                tokens_in += _num(usage.get("input_tokens"))
                tokens_out += _num(usage.get("output_tokens"))
                if data.get("subtype") != "success" or data.get("is_error"):
                    status = "failed"
                    error_message = str(data.get("result") or data.get("subtype") or "claude run failed")
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
                    "error": stderr_text or f"claude exited with code {returncode}",
                    "session_id": session_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost": cost,
                },
            )
            return

        if status == "failed":
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": error_message or "claude run failed",
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
        """Real process termination — same kill-switch contract as OpenCodeTool."""
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
