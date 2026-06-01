"""Run-event broker backed by Redis pub/sub with in-process fallback buffer.

Architecture
------------
- Each run gets a Redis channel: ``noodle:run:<run_id>``.
- Events are published as JSON strings to that channel.
- A full copy of every event is also pushed to a Redis list
  ``noodle:run:<run_id>:history`` so late-joining subscribers (e.g. a WebSocket
  that opens after the run has already emitted several events) still receive the
  complete sequence.
- The list TTL is ``RUN_EVENT_TTL_SECONDS`` (1 hour by default).
- ``subscribe()`` is an async generator: it replays history first, then yields
  live events until a ``run_finished`` event arrives.

The ``RunBroker`` class is kept for interface compatibility — callers do
``broker.publish(run_id, event)`` and ``async for e in broker.subscribe(run_id)``.

Graceful degradation
--------------------
If Redis is unavailable on startup (e.g. unit tests without a live Redis), the
broker falls back silently to the pure in-process implementation.  Set
``settings.redis_url`` to an empty string to force the in-process fallback.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

Event = dict[str, Any]

RUN_EVENT_TTL_SECONDS = 60 * 60  # 1 hour
_REAP_TICK_SECONDS = 60
_CHANNEL_PREFIX = "noodle:run:"
_HISTORY_SUFFIX = ":history"


class RunBroker:
    """Pub/sub broker with Redis backend and in-process fallback."""

    def __init__(self) -> None:
        # In-process fallback state (used when Redis is unavailable or for
        # unit tests).
        self._events: dict[str, list[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._finished: dict[str, float] = {}
        self._redis_ok: bool = True  # set False if Redis ping fails on first use

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _channel(self, run_id: str) -> str:
        return f"{_CHANNEL_PREFIX}{run_id}"

    def _history_key(self, run_id: str) -> str:
        return f"{_CHANNEL_PREFIX}{run_id}{_HISTORY_SUFFIX}"

    async def _get_redis(self):  # type: ignore[return]
        """Return the app-wide Redis client, or None if unavailable."""
        if not self._redis_ok:
            return None
        try:
            from app.redis_client import redis_client  # local import to avoid circular
            await redis_client.ping()
            return redis_client
        except Exception:
            self._redis_ok = False
            logger.warning("Redis unavailable — falling back to in-process event broker")
            return None

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, run_id: str, event: Event) -> None:
        """Publish an event (usually from an async engine context).

        Redis fan-out needs a running event loop. When called without one
        (synchronous callers and tests), fall back to synchronous in-process
        buffering so subscribers and the reaper still observe the event
        instead of raising ``RuntimeError: no running event loop``.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._publish_inprocess(run_id, event)
            return
        loop.create_task(self._async_publish(run_id, event))

    async def _async_publish(self, run_id: str, event: Event) -> None:
        payload = json.dumps(event)
        r = await self._get_redis()
        if r is not None:
            try:
                pipe = r.pipeline()
                pipe.rpush(self._history_key(run_id), payload)
                pipe.expire(self._history_key(run_id), RUN_EVENT_TTL_SECONDS)
                pipe.publish(self._channel(run_id), payload)
                await pipe.execute()
                return
            except Exception:
                logger.exception("Redis publish failed for run %s — using in-process", run_id)

        # Fallback: in-process
        self._publish_inprocess(run_id, event)

    def _publish_inprocess(self, run_id: str, event: Event) -> None:
        self._events.setdefault(run_id, []).append(event)
        for queue in self._subscribers.get(run_id, set()):
            queue.put_nowait(event)
        if event.get("type") == "run_finished":
            self._finished[run_id] = time.monotonic()

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    async def subscribe(self, run_id: str) -> AsyncIterator[Event]:
        r = await self._get_redis()
        if r is not None:
            async for event in self._redis_subscribe(run_id, r):
                yield event
            return

        # Fallback: in-process
        async for event in self._inprocess_subscribe(run_id):
            yield event

    async def _redis_subscribe(self, run_id: str, r: Any) -> AsyncIterator[Event]:
        history_key = self._history_key(run_id)
        channel = self._channel(run_id)

        # 1. Replay history before subscribing to avoid race.
        try:
            raw_history: list[str] = await r.lrange(history_key, 0, -1)
        except Exception:
            raw_history = []

        already_finished = False
        for raw in raw_history:
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                continue
            yield event
            if event.get("type") == "run_finished":
                already_finished = True
                break

        if already_finished:
            return

        # 2. Subscribe for live events.
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
                yield event
                if event.get("type") == "run_finished":
                    return
        except Exception:
            logger.exception("Redis subscribe error for run %s", run_id)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    async def _inprocess_subscribe(self, run_id: str) -> AsyncIterator[Event]:
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
            subs = self._subscribers.get(run_id)
            if subs is not None:
                subs.discard(queue)
                if not subs and run_id in self._finished:
                    self._subscribers.pop(run_id, None)
                    self._events.pop(run_id, None)
                    self._finished.pop(run_id, None)

    # ------------------------------------------------------------------
    # Reap (in-process fallback only; Redis TTL handles Redis-side cleanup)
    # ------------------------------------------------------------------

    def reap(self, ttl_seconds: float = RUN_EVENT_TTL_SECONDS) -> int:
        if not self._finished:
            return 0
        now = time.monotonic()
        dropped = 0
        for run_id, finished_at in list(self._finished.items()):
            if now - finished_at < ttl_seconds:
                continue
            if self._subscribers.get(run_id):
                continue
            self._events.pop(run_id, None)
            self._subscribers.pop(run_id, None)
            self._finished.pop(run_id, None)
            dropped += 1
        return dropped


broker = RunBroker()


async def broker_reaper_loop() -> None:
    """Background loop: reaps stale in-process buffers (Redis TTL handles Redis side)."""
    while True:
        try:
            await asyncio.sleep(_REAP_TICK_SECONDS)
        except asyncio.CancelledError:
            raise
        try:
            dropped = broker.reap()
            if dropped:
                logger.info("broker reaped %d finished-run buffers", dropped)
        except Exception:  # noqa: BLE001
            logger.exception("broker reaper iteration failed")
