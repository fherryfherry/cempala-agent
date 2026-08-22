"""GET /workspaces/{id}/events/stream — SSE feed with DB replay on reconnect (MAP-022).

Event.id is a TEXT uuid4 hex (not sortable), so "everything after since_event_id" is
resolved via that row's created_at as a cutoff, not string/id comparison. If
since_event_id isn't found (never sent, or DB reset), we replay everything for the
workspace rather than silently dropping history.

Race-free reconnect: subscribe() to the live bus *before* querying the DB, so any
event published during the replay query is already buffered in the queue. Replayed
ids are tracked and skipped if they show up again from the live queue.
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OVERFLOW_MARKER, event_bus
from app.db.models import Event
from app.db.session import get_session

router = APIRouter(prefix="/workspaces", tags=["events"])

_HEARTBEAT_SECONDS = 15
# ponytail: short poll so a closed tab (request.is_disconnected()) is noticed quickly
# instead of waiting out the full heartbeat interval; the heartbeat itself still only
# fires every _HEARTBEAT_SECONDS.
_POLL_SECONDS = 1


def _sse(ev: Event) -> str:
    payload = {"id": ev.id, "run_id": ev.run_id, "type": ev.type, "payload": ev.payload}
    return f"id: {ev.id}\ndata: {json.dumps(payload)}\n\n"


async def _replay_rows(session: AsyncSession, workspace_id: str, since_event_id: str | None) -> list[Event]:
    cutoff = None
    if since_event_id:
        cutoff = await session.scalar(select(Event.created_at).where(Event.id == since_event_id))
        # not found (never sent / DB reset) -> fall through and replay everything

    stmt = select(Event).where(Event.workspace_id == workspace_id)
    if cutoff is not None:
        stmt = stmt.where(Event.created_at > cutoff)
    stmt = stmt.order_by(Event.created_at)
    return list((await session.scalars(stmt)).all())


async def _event_stream(request: Request, session: AsyncSession, workspace_id: str, since_event_id: str | None):
    queue = event_bus.subscribe(workspace_id)
    try:
        replayed_ids: set[str] = set()
        rows = await _replay_rows(session, workspace_id, since_event_id)
        for ev in rows:
            replayed_ids.add(ev.id)
            yield _sse(ev)

        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_POLL_SECONDS)
            except asyncio.TimeoutError:
                if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
                    last_heartbeat = time.monotonic()
                    yield ": ping\n\n"
                continue

            if item is OVERFLOW_MARKER:
                continue
            if item.id in replayed_ids:
                continue  # already sent during replay (race with live buffering)
            # ponytail: replayed_ids grows unbounded for the connection's lifetime;
            # fine for a single stream, cap/expire it if connections start living for days.
            yield _sse(item)
    finally:
        event_bus.unsubscribe(workspace_id, queue)


@router.get("/{workspace_id}/events/stream")
async def stream_events(
    workspace_id: str,
    request: Request,
    since_event_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    since = since_event_id or request.headers.get("last-event-id")
    return StreamingResponse(
        _event_stream(request, session, workspace_id, since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
