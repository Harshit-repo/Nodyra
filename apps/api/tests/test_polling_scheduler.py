"""Tests for the provider trigger polling pass in the scheduler."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.specs import (
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)


def _make_poll_spec(poll_fn):
    return ProviderTriggerSpec(
        node_id="test_poll",
        name="Test Poll",
        provider="test",
        resource="res",
        event="poll",
        poll=poll_fn,
        poll_interval_seconds=60,
    )


@pytest.mark.asyncio
async def test_poll_subscriptions_skips_non_due():
    """Subscriptions whose next_poll_at is in the future must not fire."""
    from app.services.triggers import _poll_subscriptions

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    mock_sub = MagicMock()
    mock_sub.id = "sub1"
    mock_sub.status = "active"
    mock_sub.node_type = "test_poll"
    mock_sub.config = {"next_poll_at": future}

    fired = []
    spec = _make_poll_spec(
        lambda ctx: (fired.append(1), ProviderTriggerPollResult(events=[], cursor={}))[1]
    )

    mock_registered = MagicMock()
    mock_registered.spec = spec

    with (
        patch(
            "app.services.triggers._load_active_poll_subscriptions",
            new_callable=AsyncMock,
            return_value=[mock_sub],
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.is_registered_provider_trigger",
            return_value=True,
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.get_registered_provider_trigger",
            return_value=mock_registered,
        ),
    ):
        await _poll_subscriptions(datetime.now(UTC))

    assert fired == []


@pytest.mark.asyncio
async def test_poll_subscriptions_fires_overdue():
    """Subscriptions past next_poll_at must have _execute_poll called."""
    from app.services.triggers import _poll_subscriptions

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    mock_sub = MagicMock()
    mock_sub.id = "sub1"
    mock_sub.status = "active"
    mock_sub.node_type = "test_poll"
    mock_sub.config = {"next_poll_at": past, "poll_cursor": {}}

    spec = _make_poll_spec(lambda ctx: ProviderTriggerPollResult(events=[], cursor={}))
    mock_registered = MagicMock()
    mock_registered.spec = spec

    with (
        patch(
            "app.services.triggers._load_active_poll_subscriptions",
            new_callable=AsyncMock,
            return_value=[mock_sub],
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.is_registered_provider_trigger",
            return_value=True,
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.get_registered_provider_trigger",
            return_value=mock_registered,
        ),
        patch(
            "app.services.triggers._execute_poll",
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        await _poll_subscriptions(datetime.now(UTC))

    mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_poll_subscriptions_fires_when_no_next_poll_at():
    """Subscriptions with no next_poll_at should also fire (first overdue pass)."""
    from app.services.triggers import _poll_subscriptions

    mock_sub = MagicMock()
    mock_sub.id = "sub2"
    mock_sub.status = "active"
    mock_sub.node_type = "test_poll"
    mock_sub.config = {}  # no next_poll_at set yet

    spec = _make_poll_spec(lambda ctx: ProviderTriggerPollResult(events=[], cursor={}))
    mock_registered = MagicMock()
    mock_registered.spec = spec

    with (
        patch(
            "app.services.triggers._load_active_poll_subscriptions",
            new_callable=AsyncMock,
            return_value=[mock_sub],
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.is_registered_provider_trigger",
            return_value=True,
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.get_registered_provider_trigger",
            return_value=mock_registered,
        ),
        patch(
            "app.services.triggers._execute_poll",
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        await _poll_subscriptions(datetime.now(UTC))

    mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_poll_subscriptions_skips_non_poll_specs():
    """Specs without a poll hook must be skipped even if subscription is overdue."""
    from app.services.triggers import _poll_subscriptions

    mock_sub = MagicMock()
    mock_sub.id = "sub3"
    mock_sub.status = "active"
    mock_sub.node_type = "webhook_only_trigger"
    mock_sub.config = {}

    spec = ProviderTriggerSpec(
        node_id="webhook_only_trigger",
        name="Webhook Only",
        provider="test",
        resource="res",
        event="webhook",
        poll=None,  # no poll hook
    )
    mock_registered = MagicMock()
    mock_registered.spec = spec

    with (
        patch(
            "app.services.triggers._load_active_poll_subscriptions",
            new_callable=AsyncMock,
            return_value=[mock_sub],
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.is_registered_provider_trigger",
            return_value=True,
        ),
        patch(
            "noodle_nodes.integrations_v2.registry.get_registered_provider_trigger",
            return_value=mock_registered,
        ),
        patch(
            "app.services.triggers._execute_poll",
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        await _poll_subscriptions(datetime.now(UTC))

    mock_execute.assert_not_called()
