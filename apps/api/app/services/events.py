"""In-process pub/sub for live run events.

Each run gets a channel. Events are buffered so a WebSocket that connects
mid-run (or after) still receives the full sequence. A production deployment
would back this with Redis pub/sub; the interface stays the same.

Memory bound: finished runs' buffers are dropped as soon as the last
subscriber disconnects, and a periodic reaper drops anything older than
``RUN_EVENT_TTL_SECONDS`` to catch runs nobody ever subscribed to (production
webhook fires, scheduler ticks, etc.) — otherwise the broker grew unbounded
for the life of the API process.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

Event = dict[str, Any]

# Drop a finished run's buffer this long after the last event lands, even if
# nobody ever subscribed. 1h matches a generous "open a fresh tab and inspect
# this run" window without retaining forever.
RUN_EVENT_TTL_SECONDS = 60 * 60
_REAP_TICK_SECONDS = 60


class RunBroker:
    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._finished: dict[str, float] = {}  # run_id -> monotonic finished_at

    def publish(self, run_id: str, event: Event) -> None:
        self._events.setdefault(run_id, []).append(event)
        for queue in self._subscribers.get(run_id, set()):
            queue.put_nowait(event)
        if event.get("type") == "run_finished":
            self._finished[run_id] = time.monotonic()

    async def subscribe(self, run_id: str) -> AsyncIterator[Event]:
        # Buffer copy + registration happen without an await, so no event
        # published in between can be missed or duplicated.
        queue: asyncio.Queue[Event] = asyncio.Queue()
        for event in self._events.get(run_id, []):
            queue.put_nowait(event)
        self._subscribers.setdefault(run_id, set()).add(queue)
        already_finished = run_id in self._finished

        try:
            while True:
                if already_finished and queue.empty():
                    return
                event = await queue.get()
                yield event
                if event.get("type") == "run_finished":
                    return
        finally:
            subscribers = self._subscribers.get(run_id)
            if subscribers is not None:
                subscribers.discard(queue)
                # Eager cleanup: once the run is finished AND no live
                # subscribers remain, the buffer is dead weight.
                if not subscribers and run_id in self._finished:
                    self._subscribers.pop(run_id, None)
                    self._events.pop(run_id, None)
                    self._finished.pop(run_id, None)

    def reap(self, ttl_seconds: float = RUN_EVENT_TTL_SECONDS) -> int:
        """Drop finished runs older than ``ttl_seconds`` with no subscribers.

        Returns the number of runs dropped. Called periodically by
        :func:`broker_reaper_loop`; also useful in tests.
        """
        if not self._finished:
            return 0
        now = time.monotonic()
        dropped = 0
        for run_id, finished_at in list(self._finished.items()):
            if now - finished_at < ttl_seconds:
                continue
            if self._subscribers.get(run_id):
                continue  # someone is still streaming this; leave it
            self._events.pop(run_id, None)
            self._subscribers.pop(run_id, None)
            self._finished.pop(run_id, None)
            dropped += 1
        return dropped


broker = RunBroker()


async def broker_reaper_loop() -> None:
    """Background loop that drops stale finished-run buffers.

    Started from the lifespan in :mod:`app.main`. Sleeps in chunks so a
    shutdown cancel resolves quickly.
    """
    while True:
        try:
            await asyncio.sleep(_REAP_TICK_SECONDS)
        except asyncio.CancelledError:
            raise
        try:
            dropped = broker.reap()
            if dropped:
                logger.info("broker reaped %d finished-run buffers", dropped)
        except Exception:  # noqa: BLE001 - a bad iteration must not kill the loop
            logger.exception("broker reaper iteration failed")
