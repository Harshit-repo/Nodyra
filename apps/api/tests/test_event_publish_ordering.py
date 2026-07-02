"""BUG-6: per-topic Redis publish order must match publish() call order."""
from __future__ import annotations

import asyncio
import json

import pytest


class _RecordingPipe:
    def __init__(self, store: list[str], delay: float) -> None:
        self._store = store
        self._delay = delay
        self._pending: list[str] = []

    def rpush(self, key: str, payload: str) -> None:
        self._pending.append(payload)

    def expire(self, key: str, ttl: int) -> None:
        pass

    def publish(self, channel: str, payload: str) -> None:
        pass

    async def execute(self) -> None:
        await asyncio.sleep(self._delay)
        self._store.extend(self._pending)


class _SlowFirstRedis:
    """First pipeline sleeps; later ones are instant."""

    def __init__(self) -> None:
        self.history: list[str] = []
        self._calls = 0

    def pipeline(self) -> _RecordingPipe:
        self._calls += 1
        return _RecordingPipe(self.history, 0.05 if self._calls == 1 else 0.0)


@pytest.mark.asyncio
async def test_publish_order_preserved_per_topic() -> None:
    from app.services.events import TopicBroker

    broker = TopicBroker(channel_prefix="noodle:run:", name="run")
    broker._mode = "redis"
    broker._redis = _SlowFirstRedis()

    broker.publish("run-1", {"type": "node_chunk", "seq": 1})
    broker.publish("run-1", {"type": "node_finished", "seq": 2})
    while broker._publish_tasks:
        await asyncio.gather(*list(broker._publish_tasks))

    history = [json.loads(payload) for payload in broker._redis.history]
    assert [event["seq"] for event in history] == [1, 2]
