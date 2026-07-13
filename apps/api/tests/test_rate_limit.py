"""Shared sliding-window rate limiter (H5)."""

import pytest

from app.config import settings
from app.services import rate_limit


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    # Force the in-process path (no Redis) and a clean bucket store per test.
    monkeypatch.setattr(settings, "queue_backend", "none")
    rate_limit.reset()
    yield
    rate_limit.reset()


async def test_allows_up_to_limit_then_blocks() -> None:
    for _ in range(3):
        assert await rate_limit.allow("test", "1.2.3.4", limit=3) is True
    # Fourth hit in the same window is rejected.
    assert await rate_limit.allow("test", "1.2.3.4", limit=3) is False


async def test_distinct_identifiers_have_independent_budgets() -> None:
    assert await rate_limit.allow("test", "a", limit=1) is True
    assert await rate_limit.allow("test", "a", limit=1) is False
    # A different identifier is unaffected.
    assert await rate_limit.allow("test", "b", limit=1) is True


async def test_distinct_buckets_have_independent_budgets() -> None:
    assert await rate_limit.allow("bucket-a", "x", limit=1) is True
    assert await rate_limit.allow("bucket-b", "x", limit=1) is True


async def test_window_slides_so_old_hits_expire(monkeypatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(rate_limit, "_now", lambda: clock["t"])
    assert await rate_limit.allow("test", "ip", limit=1, window_seconds=60) is True
    assert await rate_limit.allow("test", "ip", limit=1, window_seconds=60) is False
    # Advance past the window — the earlier hit ages out.
    clock["t"] += 61
    assert await rate_limit.allow("test", "ip", limit=1, window_seconds=60) is True


async def test_non_positive_limit_always_allows() -> None:
    assert await rate_limit.allow("test", "ip", limit=0) is True


async def test_redis_backend_uses_atomic_lua_eval(monkeypatch) -> None:
    import app.redis_client as redis_module

    calls: list[tuple[str, int, str]] = []
    counts: dict[str, int] = {}

    class FakeRedis:
        async def eval(self, script: str, numkeys: int, *args):
            assert numkeys == 1
            key = str(args[0])
            window = int(args[1])
            calls.append((key, window, script))
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(redis_module, "redis_client", FakeRedis())

    assert await rate_limit.allow("auth", "10.0.0.1", limit=2, window_seconds=30) is True
    assert await rate_limit.allow("auth", "10.0.0.1", limit=2, window_seconds=30) is True
    assert await rate_limit.allow("auth", "10.0.0.1", limit=2, window_seconds=30) is False

    assert len(calls) == 3
    assert calls[0][0] == "nodyra:rl:auth:10.0.0.1"
    assert calls[0][1] == 30
    assert "INCR" in calls[0][2] and "EXPIRE" in calls[0][2]
    assert rate_limit._buckets == {}
    assert rate_limit._redis_degraded is False


async def test_redis_backend_falls_back_when_unavailable(monkeypatch) -> None:
    import app.redis_client as redis_module

    class BrokenRedis:
        async def eval(self, *_args):
            raise OSError("redis down")

    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(redis_module, "redis_client", BrokenRedis())

    assert await rate_limit.allow("auth", "10.0.0.2", limit=1, window_seconds=30) is True
    assert await rate_limit.allow("auth", "10.0.0.2", limit=1, window_seconds=30) is False

    assert "auth:10.0.0.2" in rate_limit._buckets
    assert rate_limit._redis_degraded is True
