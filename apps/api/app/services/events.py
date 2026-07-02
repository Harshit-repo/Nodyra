"""Event brokers backed by Redis pub/sub with in-process fallback buffers.

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
Workflow draft-change events use the same transport through
``workflow_broker.publish(workflow_id, event)``.

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
_HISTORY_MAX_EVENTS = 10_000  # safety cap: never replay more than this many events
_REAP_TICK_SECONDS = 60
_HISTORY_SUFFIX = ":history"


class TopicBroker:
    """Pub/sub broker with Redis backend and in-process fallback."""

    def __init__(
        self,
        *,
        channel_prefix: str,
        name: str,
        terminal_event_type: str | None = None,
    ) -> None:
        self._channel_prefix = channel_prefix
        self._name = name
        self._terminal_event_type = terminal_event_type
        # In-process fallback state (used when Redis is unavailable or for
        # unit tests).
        self._events: dict[str, list[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._finished: dict[str, float] = {}
        # Last time ANY event was buffered for a topic, used to reap buffers
        # that never emit a terminal event (workflow streams are intentionally
        # open-ended). Without this, such buffers leak until process restart.
        self._last_activity: dict[str, float] = {}
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
        # Strong references to in-flight fire-and-forget publish tasks. The
        # event loop only keeps weak refs to tasks, so a discarded
        # ``create_task`` result can be garbage-collected mid-flight and the
        # event silently dropped (asyncio docs: "Save a reference to the
        # result of this function").
        self._publish_tasks: set[asyncio.Task] = set()
        self._publish_chains: dict[str, asyncio.Task] = {}

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
            logger.warning("Redis unavailable — using in-process %s event broker", self._name)
            return self._mode
        self._redis = redis_client
        self._mode = "redis"
        logger.info("%s event broker using Redis transport", self._name.capitalize())
        return self._mode

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _channel(self, topic_id: str) -> str:
        return f"{self._channel_prefix}{topic_id}"

    def _history_key(self, topic_id: str) -> str:
        return f"{self._channel_prefix}{topic_id}{_HISTORY_SUFFIX}"

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, topic_id: str, event: Event) -> None:
        """Publish an event for ``topic_id``.

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
            self._publish_inprocess(topic_id, event)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._publish_oneshot(topic_id, event))
            return
        prev = self._publish_chains.get(topic_id)
        task = loop.create_task(self._ordered_publish(prev, topic_id, event))
        self._publish_chains[topic_id] = task
        task.add_done_callback(
            lambda t, tid=topic_id: (
                self._publish_chains.pop(tid, None) if self._publish_chains.get(tid) is t else None
            )
        )
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    async def _ordered_publish(
        self,
        prev: asyncio.Task | None,
        topic_id: str,
        event: Event,
    ) -> None:
        if prev is not None:
            try:
                await prev
            except (Exception, asyncio.CancelledError):
                # Predecessor handled/logged its own failure; we only need its
                # completion to preserve per-topic FIFO.
                pass
        await self._async_publish(topic_id, event)

    async def _async_publish(self, topic_id: str, event: Event) -> None:
        payload = json.dumps(event)
        r = self._redis
        if r is not None:
            for attempt in range(2):
                try:
                    pipe = r.pipeline()
                    pipe.rpush(self._history_key(topic_id), payload)
                    pipe.expire(self._history_key(topic_id), RUN_EVENT_TTL_SECONDS)
                    pipe.publish(self._channel(topic_id), payload)
                    await pipe.execute()
                    return
                except Exception:
                    if attempt == 0:
                        logger.warning(
                            "Redis publish failed for %s %s (attempt %d) — retrying",
                            self._name,
                            topic_id,
                            attempt + 1,
                        )
                        await asyncio.sleep(0.1)
                    else:
                        logger.exception(
                            "Redis publish failed for %s %s after retry — "
                            "event will not reach cross-process subscribers",
                            self._name,
                            topic_id,
                        )

        # Fallback: in-process.  In split topologies the in-process buffer
        # won't be consumed by subscribers on other replicas — the event is
        # lost to the shared broker — but local in-process subscribers
        # (single-process dev, inline mode) still get it.
        self._publish_inprocess(topic_id, event)

    async def _publish_oneshot(self, topic_id: str, event: Event) -> None:
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
            pipe.rpush(self._history_key(topic_id), payload)
            pipe.expire(self._history_key(topic_id), RUN_EVENT_TTL_SECONDS)
            pipe.publish(self._channel(topic_id), payload)
            await pipe.execute()
        except Exception:
            logger.exception("Redis one-shot publish failed for %s %s", self._name, topic_id)
            self._publish_inprocess(topic_id, event)
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    def _publish_inprocess(self, topic_id: str, event: Event) -> None:
        self._events.setdefault(topic_id, []).append(event)
        self._last_activity[topic_id] = time.monotonic()
        for queue in self._subscribers.get(topic_id, set()):
            queue.put_nowait(event)
        if self._terminal_event_type and event.get("type") == self._terminal_event_type:
            self._finished[topic_id] = time.monotonic()

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    async def subscribe(self, topic_id: str) -> AsyncIterator[Event]:
        if self._mode == "redis" and self._redis is not None:
            async for event in self._redis_subscribe(topic_id, self._redis):
                yield event
            return

        # In-process (default until ``connect()`` pins Redis).
        async for event in self._inprocess_subscribe(topic_id):
            yield event

    async def _redis_subscribe(self, topic_id: str, r: Any) -> AsyncIterator[Event]:
        history_key = self._history_key(topic_id)
        channel = self._channel(topic_id)

        # 1. Replay history before subscribing to avoid race.
        # Cap at _HISTORY_MAX_EVENTS so a very long-running workflow with
        # thousands of events doesn't OOM the subscriber on reconnect.
        try:
            raw_history: list[str] = await r.lrange(history_key, -_HISTORY_MAX_EVENTS, -1)
        except Exception:
            raw_history = []

        already_finished = False
        for raw in raw_history:
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                continue
            yield event
            if self._terminal_event_type and event.get("type") == self._terminal_event_type:
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
                if self._terminal_event_type and event.get("type") == self._terminal_event_type:
                    return
        except Exception:
            logger.exception("Redis subscribe error for %s %s", self._name, topic_id)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    async def _inprocess_subscribe(self, topic_id: str) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        for event in self._events.get(topic_id, []):
            queue.put_nowait(event)
        self._subscribers.setdefault(topic_id, set()).add(queue)
        already_finished = topic_id in self._finished

        try:
            while True:
                if already_finished and queue.empty():
                    return
                event = await queue.get()
                yield event
                if self._terminal_event_type and event.get("type") == self._terminal_event_type:
                    return
        finally:
            subs = self._subscribers.get(topic_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(topic_id, None)
                    if topic_id in self._finished:
                        # Terminal event observed and the last subscriber left:
                        # safe to evict the in-process buffer immediately.
                        self._events.pop(topic_id, None)
                        self._finished.pop(topic_id, None)
                        self._last_activity.pop(topic_id, None)
                    # If the topic has no terminal event yet, _events and
                    # _finished are intentionally left in place so a
                    # reconnecting subscriber can replay buffered events. The
                    # broker_reaper_loop evicts them after RUN_EVENT_TTL_SECONDS.

    # ------------------------------------------------------------------
    # Reap (in-process fallback only; Redis TTL handles Redis-side cleanup)
    # ------------------------------------------------------------------

    def reap(self, ttl_seconds: float = RUN_EVENT_TTL_SECONDS) -> int:
        now = time.monotonic()
        dropped = 0
        for topic_id, finished_at in list(self._finished.items()):
            if now - finished_at < ttl_seconds:
                continue
            if self._subscribers.get(topic_id):
                continue
            self._events.pop(topic_id, None)
            self._subscribers.pop(topic_id, None)
            self._finished.pop(topic_id, None)
            self._last_activity.pop(topic_id, None)
            dropped += 1
        # Reap buffers of topics that never emitted a terminal event. A topic
        # that has had no new events for the full TTL and has no live subscriber
        # is safe to drop: any reconnecting subscriber would get an empty replay
        # either way, matching the Redis history TTL behavior.
        for topic_id, last in list(self._last_activity.items()):
            if topic_id in self._finished:
                continue  # handled by the finished-run sweep above
            if now - last < ttl_seconds:
                continue
            if self._subscribers.get(topic_id):
                continue
            self._events.pop(topic_id, None)
            self._subscribers.pop(topic_id, None)
            self._last_activity.pop(topic_id, None)
            dropped += 1
        return dropped


class RunBroker(TopicBroker):
    def __init__(self) -> None:
        super().__init__(
            channel_prefix="noodle:run:",
            name="run",
            terminal_event_type="run_finished",
        )


class WorkflowEventBroker(TopicBroker):
    def __init__(self) -> None:
        super().__init__(channel_prefix="noodle:workflow:", name="workflow")


broker = RunBroker()
workflow_broker = WorkflowEventBroker()


async def broker_reaper_loop() -> None:
    """Background loop: reaps stale in-process buffers (Redis TTL handles Redis side)."""
    while True:
        try:
            await asyncio.sleep(_REAP_TICK_SECONDS)
        except asyncio.CancelledError:
            raise
        try:
            dropped = broker.reap() + workflow_broker.reap()
            if dropped:
                logger.info("broker reaped %d stale event buffers", dropped)
        except Exception:  # noqa: BLE001
            logger.exception("broker reaper iteration failed")
