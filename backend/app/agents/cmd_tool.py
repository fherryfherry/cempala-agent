"""CmdTool: AgentTool adapter for the `cmd` (Command Code) CLI.

Fifth real adapter alongside opencode/claude/agy/codex (ADR-007's "revisit when a
third adapter arrives" — already revisited twice since). Runs
`cmd -p --output-format json --yolo --tools-all ...` as a subprocess, streams stdout
line-by-line, maps each JSON line to an `AdapterEvent`, and yields a final `run_ended`
event — same terminal-event contract as the other adapters.

`--tools-all` matters here specifically: `cmd --help` documents `-p` (headless) mode as
withholding some tools by default independent of `--yolo` (which only bypasses
permission *prompts*, not the headless tool allowlist) — `--tools-all` is the flag that
actually makes this the same tier as opencode's `--auto`/claude's `bypassPermissions`
(ADR-010: the portal deliberately grants agents full shell access, not a sandboxed
subset).

No per-run MCP wiring (unlike opencode/claude): `cmd --help`/`cmd mcp --help` (checked
against the real installed v1.36.0 binary) expose no per-run inline config flag —
`cmd mcp add`/`add-json` only write to a persistent `local`/`project`/`user` scope,
which is the wrong lifetime for a per-run map-tickets server. `"cmd"` is deliberately
left out of `MCP_TOOL_KINDS` (app/agents/mcp_config.py), same as codex/agy.

Schema confirmed live against the real installed `cmd` CLI (not guessed — decompiled
from the installed npm package's bundle, `command-code/dist/cli.mjs`, then cross-checked
against a real `cmd -p ... --output-format json` run). Every streamed line is
`{"type": "event", "event": {...}}`; the inner `event.type` is one of `run_start`
(`sessionId`), `text_delta`/`thinking_delta` (`delta` — incremental text chunks, not
full blocks, mapped 1:1 to `assistant_text`/`reasoning` AdapterEvents and concatenated
by the orchestrator), `tool_queued`/`tool_completed` (`toolCallId`, `toolName`,
`input`/`result` — mapped to `tool_call`/`tool_result`), plus `turn_start`/`turn_end`/
`message_start`/`message_end`/`model_request_start`/`model_trace`/`thinking_start`/
`thinking_end`/`tool_running`/`run_error`/`run_end`, which carry no information this
adapter needs beyond what the terminal `result` line already repeats authoritatively
(so they're skipped, not because the schema is unknown). The final line is
`{"type": "result", "subtype": "success"|"error"|"max_turns", "sessionId", "usage":
{"inputTokens", "outputTokens", ...no cost field...}, "finalText", "error"?}` — no `$`
cost, same as CodexTool's Codex CLI. Unknown/malformed lines are still skipped
defensively, never crash the run — same posture as every other adapter here.
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


class CmdTool:
    async def run(self, ctx: RunContext) -> AsyncIterator[AdapterEvent]:
        prompt = ctx.prompt
        for attachment in ctx.attachments:
            # No local-file attachment flag documented for this CLI — use the same
            # `@path` mention convention a human would type in a Command Code chat.
            prompt += f"\n@{attachment}"

        cmd = [
            settings.CMD_BIN,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--yolo",
            "--tools-all",
            "-m",
            ctx.agent_model,
        ]
        if ctx.prev_session_id:
            cmd += ["--resume", ctx.prev_session_id]

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
                limit=settings.CMD_STREAM_LIMIT_BYTES,
                env=env,
            )
        except FileNotFoundError:
            yield AdapterEvent(
                "run_ended",
                {
                    "status": "failed",
                    "error": f"cmd binary not found: {settings.CMD_BIN!r}",
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
                                "cmd stdout line exceeds stream limit "
                                f"({settings.CMD_STREAM_LIMIT_BYTES} bytes); "
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

            if data_type == "event":
                event = data.get("event")
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")

                if event_type == "run_start":
                    sid = event.get("sessionId")
                    if session_id is None and sid:
                        session_id = str(sid)
                    continue

                if event_type == "text_delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        yield AdapterEvent("assistant_text", {"text": delta})
                    continue

                if event_type == "thinking_delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        yield AdapterEvent("reasoning", {"text": delta})
                    continue

                if event_type == "tool_queued":
                    yield AdapterEvent(
                        "tool_call",
                        {
                            "toolCallId": event.get("toolCallId"),
                            "name": event.get("toolName"),
                            "input": event.get("input"),
                        },
                    )
                    continue

                if event_type == "tool_completed":
                    yield AdapterEvent(
                        "tool_result",
                        {
                            "toolCallId": event.get("toolCallId"),
                            "name": event.get("toolName"),
                            "result": event.get("result"),
                        },
                    )
                    continue

                # turn_start/turn_end/message_start/message_end/model_request_start/
                # model_trace/thinking_start/thinking_end/tool_running/run_error/
                # run_end — nothing this adapter needs beyond the terminal `result`
                # line, which repeats the authoritative outcome. Skip, never kill.
                continue

            if data_type == "result":
                sid = data.get("sessionId")
                if sid:
                    session_id = str(sid)
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                tokens_in += _num(usage.get("inputTokens"))
                tokens_out += _num(usage.get("outputTokens"))
                if data.get("subtype") != "success":
                    status = "failed"
                    error_message = str(data.get("error") or data.get("subtype") or "cmd run failed")
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
                    "error": error_message or stderr_text or f"cmd exited with code {returncode}",
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
                    "error": error_message or "cmd run failed",
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
