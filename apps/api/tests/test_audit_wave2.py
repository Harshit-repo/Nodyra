"""Tests for the production-readiness audit, Wave 2.

Each test maps to a finding ID in docs/production-readiness-audit.md.
"""

import pytest


# ---------------------------------------------------------------------------
# TOK-1 — only genuine session tokens authenticate; purpose tokens must not
# ---------------------------------------------------------------------------

def test_verify_token_rejects_non_session_tokens():
    from app.services.crypto import (
        create_payload_token,
        create_token,
        decode_payload_token,
        verify_token,
    )

    runner = create_payload_token(
        {"sub": "runner-1", "kind": "runner_registration"}, ttl_seconds=3600
    )
    oauth_state = create_payload_token(
        {"credential_type": "github", "redirect_uri": "https://x"}, ttl_seconds=600
    )
    session = create_token("user-1")

    # Purpose tokens must NOT satisfy the session-auth path (auth gate / current_user).
    assert verify_token(runner) is None
    assert verify_token(oauth_state) is None
    # Genuine session token still authenticates.
    assert verify_token(session) == "user-1"
    # Purpose tokens still decode for their own handlers (kind preserved).
    assert decode_payload_token(runner)["kind"] == "runner_registration"


@pytest.mark.asyncio
async def test_runner_token_cannot_pass_auth_gate(client, monkeypatch):
    """End-to-end: a runner registration token must not unlock gated endpoints."""
    from app.config import settings
    from app.services.crypto import create_payload_token

    monkeypatch.setattr(settings, "auth_required", True)
    runner = create_payload_token(
        {"sub": "runner-1", "kind": "runner_registration"}, ttl_seconds=3600
    )
    r = await client.get(
        "/workflows", headers={"Authorization": f"Bearer {runner}"}
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# RD-1 — a runner must not inject events into another runner's run stream
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remote_dispatch_rejects_cross_run_events():
    import asyncio

    from app.services.remote_dispatch import RemoteDispatcher, _AgentConnection

    dispatcher = RemoteDispatcher()
    received: list[dict] = []

    async def cb(event: dict) -> None:
        received.append(event)

    conn_a = _AgentConnection(runner_id="A", ws=object())
    conn_b = _AgentConnection(runner_id="B", ws=object())
    # Run "rA" is owned by connection A.
    conn_a.active_runs["rA"] = asyncio.get_running_loop().create_future()
    dispatcher._run_callbacks["rA"] = cb

    # Runner B tries to inject an event into A's run — must be ignored.
    await dispatcher._handle_agent_message(
        conn_b, {"type": "run_event", "run_id": "rA", "event": {"type": "spoofed"}}
    )
    assert received == []

    # The owning runner's event is delivered normally.
    await dispatcher._handle_agent_message(
        conn_a, {"type": "run_event", "run_id": "rA", "event": {"type": "ok"}}
    )
    assert received == [{"type": "ok"}]
