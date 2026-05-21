"""In-process pub/sub for live run events.

Each run gets a channel. Events are buffered so a WebSocket that connects
mid-run (or after) still receives the full sequence. A production deployment
would back this with Redis pub/sub; the interface stays the same.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

Event = dict[str, Any]


class RunBroker:
    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._finished: set[str] = set()

    def publish(self, run_id: str, event: Event) -> None:
        self._events.setdefault(run_id, []).append(event)
        for queue in self._subscribers.get(run_id, set()):
            queue.put_nowait(event)
        if event.get("type") == "run_finished":
            self._finished.add(run_id)

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


broker = RunBroker()
