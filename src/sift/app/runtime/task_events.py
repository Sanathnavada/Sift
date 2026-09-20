"""In-memory notifications for task-card stream updates."""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[None]


class TaskEventNotifier:
    """Small task-scoped pub/sub helper for waking SSE subscribers."""

    SYSTEM_STATUS_TOPIC = "__system_status__"

    def __init__(self) -> None:
        self._subscribers: dict[str, set[_Subscriber]] = {}
        self._lock = threading.Lock()

    @asynccontextmanager
    async def subscribe(self, task_id: str) -> AsyncIterator[asyncio.Queue[None]]:
        subscriber = _Subscriber(
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(maxsize=1),
        )
        with self._lock:
            self._subscribers.setdefault(task_id, set()).add(subscriber)
        try:
            yield subscriber.queue
        finally:
            with self._lock:
                subscribers = self._subscribers.get(task_id)
                if not subscribers:
                    return
                subscribers.discard(subscriber)
                if not subscribers:
                    self._subscribers.pop(task_id, None)

    def publish(self, task_id: str) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(task_id, ()))
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(self._notify, subscriber.queue)

    def subscribe_system_status(self) -> AsyncIterator[asyncio.Queue[None]]:
        return self.subscribe(self.SYSTEM_STATUS_TOPIC)

    def publish_system_status(self) -> None:
        self.publish(self.SYSTEM_STATUS_TOPIC)

    @staticmethod
    def _notify(queue: asyncio.Queue[None]) -> None:
        if queue.full():
            return
        queue.put_nowait(None)


task_event_notifier = TaskEventNotifier()
