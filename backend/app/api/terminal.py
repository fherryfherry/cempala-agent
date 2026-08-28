"""WebSocket route for the browser Terminal menu (ADR-005: localhost-only, no auth —
this doesn't cross a new trust boundary since opencode --auto already grants agents
arbitrary command execution in repo_path; the owner already has native terminal
access to their own machine).

Protocol: binary WS frames are raw PTY bytes passthrough both directions. Text WS
frames are JSON control messages — currently only {"type": "resize", "cols", "rows"}.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.workspaces import _get_workspace_or_404
from app.core.terminal import cleanup, resize, spawn
from app.db.session import async_session

workspace_terminal_router = APIRouter(prefix="/workspaces/{workspace_id}/terminal", tags=["terminal"])


@workspace_terminal_router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, workspace_id: str) -> None:
    await websocket.accept()
    async with async_session() as session:
        try:
            ws = await _get_workspace_or_404(session, workspace_id)
        except Exception:
            await websocket.close(code=4404)
            return
    if not os.path.isdir(ws.repo_path):
        await websocket.close(code=4404)
        return

    pty_session = spawn(ws.repo_path)
    loop = asyncio.get_event_loop()

    async def pump_pty_to_ws() -> None:
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, pty_session.fd, 4096)
            except OSError:
                break
            if not data:
                break
            await websocket.send_bytes(data)

    reader_task = asyncio.create_task(pump_pty_to_ws())
    try:
        while True:
            msg = await websocket.receive()
            # websocket.receive() (the raw ASGI-level call) does NOT raise
            # WebSocketDisconnect itself — only receive_text()/receive_bytes() do.
            # A disconnect surfaces as a {"type": "websocket.disconnect"} message;
            # calling receive() again after that raises RuntimeError in Starlette.
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                os.write(pty_session.fd, msg["bytes"])
            elif msg.get("text") is not None:
                with contextlib.suppress(json.JSONDecodeError):
                    ctrl = json.loads(msg["text"])
                    if ctrl.get("type") == "resize":
                        resize(pty_session.fd, ctrl.get("cols", 80), ctrl.get("rows", 24))
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        # cleanup() does a blocking os.waitpid() — must run off the event loop
        # thread, or a single slow/stuck reap freezes every other request this
        # single-threaded asyncio server is handling (real incident: rapid
        # connect/disconnect cycles during frontend dev reloads hung the backend).
        await loop.run_in_executor(None, cleanup, pty_session)
