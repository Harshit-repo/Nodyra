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
import contextlib
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
        # Transport is pinned ONCE by ``connect()`` (called from app startup),
        # not probed per call. ``publish()`` and ``subscribe()`` then always
        # agree on where events live. The old per-call Redis probe could
        # split-brain: a *synchronous* publish (no running loop) buffered the
        # event in-process while an *async* subscribe saw Redis was reachable
        # and blocked on its pub/sub channel forever waiting for an event that
        # never arrived there. Defaulting to ``"inprocess"`` makes a bare
        # ``RunBroker()`` (unit tests, single-process dev) fully deterministic
        # and Redis-independent; production calls ``connect()`` to opt into the
        # shared Redis transport that fans out across replicas.
        self._mode: str = "inprocess"
        self._redis: Any = None

    async def connect(self) -> str:
        """Probe Redis once and pin the broker transport. Call from the app
        lifespan on startup. Idempotent and safe when Redis is absent: the
        broker then stays in in-process mode (dev / single replica / tests).

        Returns the chosen mode (``"redis"`` or ``"inprocess"``) so startup can
        log it.
        """
        try:
            from app.redis_client import redis_client  # local import: avoid cycle
            await redis_client.ping()
        except Exception:
            self._mode = "inprocess"
            self._redis = None
            logger.warning("Redis unavailable — using in-process event broker")
            return self._mode
        self._redis = redis_client
        self._mode = "redis"
        logger.info("Run-event broker using Redis transport")
        return self._mode

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _channel(self, run_id: str) -> str:
        return f"{_CHANNEL_PREFIX}{run_id}"

    def _history_key(self, run_id: str) -> str:
        return f"{_CHANNEL_PREFIX}{run_id}{_HISTORY_SUFFIX}"

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, run_id: str, event: Event) -> None:
        """Publish an event for ``run_id``.

        Transport follows the pinned ``self._mode`` so it can never diverge
        from what ``subscribe()`` reads:

        - in-process mode → buffer synchronously.
        - Redis mode + a running loop (the production path: every publisher
          runs inside the engine's async context) → schedule the async Redis
          publish on that loop.
        - Redis mode + *no* running loop (a synchronous caller after
          ``connect()``; not hit in production) → do a one-shot Redis publish
          so the event still reaches Redis subscribers instead of being
          silently buffered where no Redis subscriber would ever look.
        """
        if self._mode != "redis" or self._redis is None:
            self._publish_inprocess(run_id, event)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._publish_oneshot(run_id, event))
            return
        loop.create_task(self._async_publish(run_id, event))

    async def _async_publish(self, run_id: str, event: Event) -> None:
        payload = json.dumps(event)
        r = self._redis
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

    async def _publish_oneshot(self, run_id: str, event: Event) -> None:
        """Publish a single event over a short-lived Redis connection.

        Used only when a synchronous caller publishes in Redis mode (no running
        loop to reuse the shared, loop-bound client). Rare and non-production;
        kept correct so the broker never splits its transport.
        """
        import redis.asyncio as aioredis

        from app.config import settings

        client = aioredis.from_url(settings.redis_url)
        try:
            payload = json.dumps(event)
            pipe = client.pipeline()
            pipe.rpush(self._history_key(run_id), payload)
            pipe.expire(self._history_key(run_id), RUN_EVENT_TTL_SECONDS)
            pipe.publish(self._channel(run_id), payload)
            await pipe.execute()
        except Exception:
            logger.exception("Redis one-shot publish failed for run %s", run_id)
            self._publish_inprocess(run_id, event)
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

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
        if self._mode == "redis" and self._redis is not None:
            async for event in self._redis_subscribe(run_id, self._redis):
                yield event
            return

        # In-process (default until ``connect()`` pins Redis).
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
