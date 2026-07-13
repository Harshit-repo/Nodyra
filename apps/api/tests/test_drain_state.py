from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import drain_state


@pytest.fixture(autouse=True)
def _restore_drain_state(monkeypatch):
    original_backend = settings.queue_backend
    original_local = settings.queue_drain
    drain_state.reset_local_cache()
    yield
    settings.queue_backend = original_backend
    settings.queue_drain = original_local
    drain_state.reset_local_cache()


@pytest.mark.asyncio
async def test_shared_drain_write_precedes_local_success(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(drain_state, "redis_client", fake)
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(settings, "queue_drain", False)

    assert await drain_state.set_draining(True) is True
    fake.set.assert_awaited_once_with("nodyra:queue:draining", "1")
    assert settings.queue_drain is True


@pytest.mark.asyncio
async def test_shared_drain_write_failure_does_not_claim_success(monkeypatch):
    fake = AsyncMock()
    fake.set.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(drain_state, "redis_client", fake)
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(settings, "queue_drain", False)

    with pytest.raises(ConnectionError, match="redis down"):
        await drain_state.set_draining(True)
    assert settings.queue_drain is False


@pytest.mark.asyncio
async def test_shared_drain_read_reaches_worker_process(monkeypatch):
    fake = AsyncMock()
    fake.get.return_value = "1"
    monkeypatch.setattr(drain_state, "redis_client", fake)
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(settings, "queue_drain", False)

    assert await drain_state.is_draining() is True


@pytest.mark.asyncio
async def test_shared_drain_read_failure_fails_closed(monkeypatch):
    fake = AsyncMock()
    fake.get.side_effect = TimeoutError("redis timeout")
    monkeypatch.setattr(drain_state, "redis_client", fake)
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(settings, "queue_drain", False)

    assert await drain_state.is_draining() is True
