"""Shared runner registration-token mint (used by the token endpoint,
SSH onboarding, and Docker-worker spawning so all three produce identical,
revocable, runner-bound tokens)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Runner
from app.services.crypto import create_payload_token


async def mint_runner_registration(
    session: AsyncSession,
    pool_id: str,
    *,
    org_id: str,
    name: str,
    max_concurrent_runs: int = 1,
    capabilities: dict | None = None,
) -> tuple[Runner, str, datetime]:
    """Create a placeholder Runner row + a signed registration token bound to
    it. Commits the row (so the token's ``sub`` references a real runner) and
    returns (runner, token, expires_at). Caller may further mutate + commit."""
    runner = Runner(
        pool_id=pool_id,
        name=name,
        status="offline",
        max_concurrent_runs=max_concurrent_runs or 1,
        capabilities=capabilities or {},
    )
    session.add(runner)
    await session.commit()
    await session.refresh(runner)

    ttl = settings.runner_token_ttl_days * 86_400
    token = create_payload_token(
        {
            "sub": runner.id,
            "pool_id": pool_id,
            "org_id": runner.org_id,
            "kind": "runner_registration",
        },
        ttl_seconds=ttl,
    )
    expires_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + ttl, tz=UTC)
    runner.token_expires_at = expires_at
    await session.commit()
    return runner, token, expires_at
