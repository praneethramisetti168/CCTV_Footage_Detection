"""
In-process event bus using asyncio.Queue.

The detection pipeline runs in a background thread and publishes events
via publish_threadsafe().  Async consumers subscribe and process them.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, maxsize: int = 2000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers: List[Callable] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------ setup

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Must be called once from the main async thread before the pipeline starts."""
        self._loop = loop

    def subscribe(self, callback: Callable) -> None:
        """Register an async or sync callback to receive every event."""
        self._subscribers.append(callback)

    # ------------------------------------------------------------------ publish

    def publish_threadsafe(self, event: Any) -> None:
        """
        Thread-safe publish — call from the pipeline background thread.
        Silently drops events if the queue is full to avoid blocking.
        """
        if self._loop is None or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._enqueue(event), self._loop)
        except RuntimeError:
            pass

    async def _enqueue(self, event: Any) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("EventBus queue full — dropping event")

    async def publish(self, event: Any) -> None:
        """Async publish — call from within the event loop."""
        await self._queue.put(event)

    # ------------------------------------------------------------------ process

    async def process(self) -> None:
        """
        Long-running background task — dequeues events and fans them out
        to all subscribers.  Run as an asyncio.Task.
        """
        logger.info("EventBus processor started")
        while True:
            try:
                event = await self._queue.get()
                for subscriber in self._subscribers:
                    try:
                        if asyncio.iscoroutinefunction(subscriber):
                            await subscriber(event)
                        else:
                            subscriber(event)
                    except Exception as exc:
                        logger.error("Subscriber %s raised: %s", subscriber, exc)
                self._queue.task_done()
            except asyncio.CancelledError:
                logger.info("EventBus processor cancelled")
                break
            except Exception as exc:
                logger.error("EventBus processor error: %s", exc)


# Singleton instance
event_bus = EventBus()
