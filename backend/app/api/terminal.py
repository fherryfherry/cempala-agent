"""WebSocket route for the browser Terminal menu (ADR-016 — supersedes ADR-005:
this backend may now be reached by more than the owner, so terminal access is
gated like every other workspace route; the underlying risk is unchanged —
opencode --auto already grants agents arbitrary command execution in repo_path,
so an authenticated editor having a shell too crosses no new trust boundary).

Protocol: binary WS frames are raw PTY bytes passthrough both directions. Text WS
frames are JSON control messages — currently only {"type": "resize", "cols", "rows"}.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.core.auth import SESSION_COOKIE, WorkspaceRole, _check_workspace_role, read_session_cookie
from app.core.terminal import cleanup, resize, spawn
from app.db.models import User
from app.db.session import async_session

workspace_terminal_router = APIRouter(prefix="/workspaces/{workspace_id}/terminal", tags=["terminal"])


@workspace_terminal_router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, workspace_id: str) -> None:
    # Cookies are available on the WS handshake request before accept() — reject
    # unauthenticated/unauthorized clients without ever completing the handshake,
    # rather than accept()-then-close() (Starlette supports close() pre-accept).
    token = websocket.cookies.get(SESSION_COOKIE)
    user_id = read_session_cookie(token) if token else None
    if user_id is None:
        await websocket.close(code=4401)
        return

    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None or not user.is_active:
            await websocket.close(code=4401)
            return
        try:
            await _check_workspace_role(session, user, workspace_id, WorkspaceRole.editor)
        except AppError:
            await websocket.close(code=4403)
            return
        try:
            ws = await _get_workspace_or_404(session, workspace_id)
        except AppError:
            await websocket.close(code=4404)
            return

    await websocket.accept()
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
