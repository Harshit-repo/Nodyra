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
