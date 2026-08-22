"""EventBus: persist events to DB, then fan out to bounded per-subscriber queues.

No FastAPI dependency — used by both the orchestrator (publisher) and the SSE
endpoint (subscriber), from different layers.
"""

import asyncio
from collections import defaultdict
from typing import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event

_QUEUE_MAXSIZE = 1000

# Synthetic marker pushed into a subscriber's queue when it overflowed and the
# oldest pending event had to be dropped to make room. The DB row for the
# dropped event is untouched — only this subscriber's live queue lost it.
OVERFLOW_MARKER = {"type": "overflow"}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def publish(
        self, session: AsyncSession, *, run_id: str, workspace_id: str, type: str, payload: dict
    ) -> Event:
        # ponytail: query MAX(seq) per publish (simple, correct across restarts)
        # rather than an in-memory counter; upgrade to a cached per-run counter
        # if publish throughput ever becomes the bottleneck.
        max_seq = await session.scalar(select(func.max(Event.seq)).where(Event.run_id == run_id))
        ev = Event(
            run_id=run_id,
            workspace_id=workspace_id,
            seq=(max_seq or 0) + 1,
            type=type,
            payload=payload,
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)

        # DB write is already durable at this point. Subscriber delivery is
        # best-effort and must never block or slow the publisher.
        for queue in self._subscribers.get(workspace_id, ()):
            self._push_nonblocking(queue, ev)

        return ev

    def _push_nonblocking(self, queue: asyncio.Queue, ev: Event) -> None:
        try:
            queue.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(OVERFLOW_MARKER)
            except asyncio.QueueFull:
                queue.get_nowait()
                queue.put_nowait(OVERFLOW_MARKER)
            try:
                queue.put_nowait(ev)
            except asyncio.QueueFull:
                queue.get_nowait()
                queue.put_nowait(ev)

    def subscribe(self, workspace_id: str, maxsize: int = _QUEUE_MAXSIZE) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers[workspace_id].add(queue)
        return queue

    def unsubscribe(self, workspace_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(workspace_id, set()).discard(queue)

    async def stream(self, workspace_id: str) -> AsyncIterator[Event | dict]:
        """Convenience async-iterator wrapper around subscribe/unsubscribe."""
        queue = self.subscribe(workspace_id)
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(workspace_id, queue)
