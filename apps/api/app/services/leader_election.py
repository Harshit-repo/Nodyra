"""DB-backed advisory leader election.

Used by the in-process scheduler + retention loops so that, when multiple API
replicas are running, only one of them fires schedules and prunes data at a
time. The election is intentionally minimal:

* SQLite: there is only ever one writer process worth running; we always
  return ``True`` and let the operator know via the runtime-mode warnings.
* PostgreSQL: a dedicated long-lived connection holds a *session-scoped*
  ``pg_advisory_lock``. If that connection dies the lock is released and
  another replica picks it up on its next retry.

There is no fencing — this is best-effort. The scheduler tolerates duplicate
fires because the per-run idempotency key + ScheduleState row check prevent
double-firing in practice. The lock exists to keep things tidy under normal
operation.
"""

from __future__ import annotations

import asyncio
import logging
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal

logger = logging.getLogger("nodyra.leader")


def _stable_key(name: str) -> int:
    """Map a human-readable lock name to a 64-bit signed int for
    ``pg_advisory_lock``. ``zlib.crc32`` returns 32 bits; we sign-extend so
    Postgres accepts the result.
    """
    raw = zlib.crc32(name.encode("utf-8"))
    if raw & 0x8000_0000:
        raw -= 0x1_0000_0000
    return int(raw)


def _is_postgres() -> bool:
    return settings.database_url.startswith(("postgresql", "postgres+"))


@asynccontextmanager
async def hold_leader_lock(name: str) -> AsyncIterator[bool]:
    """Try to acquire an advisory lock for ``name`` for the duration of the
    context. Yields ``True`` if acquired, ``False`` otherwise.

    Uses an ORM session from the shared pool so no dedicated connection is
    burned. The session-scoped PG advisory lock is released explicitly
    (``pg_advisory_unlock``) before the session closes, keeping the pooled
    connection clean for its next consumer.
    """
    if not _is_postgres():
        yield True
        return

    key = _stable_key(name)
    async with SessionLocal() as session:
        conn = await session.connection()
        result = await conn.scalar(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
        )
        acquired = bool(result)
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            try:
                conn = await session.connection()
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": key}
                )
            except Exception:  # noqa: BLE001
                logger.exception("pg_advisory_unlock(%s) failed", name)


async def run_with_leader_election(
    loop_factory: Callable[[], Awaitable[None]],
    *,
    name: str,
    retry_seconds: float = 5.0,
) -> None:
    """Run ``loop_factory()`` while we hold the named leader lock.

    If acquisition fails, sleep ``retry_seconds`` and try again. If the inner
    loop returns or raises, release the lock and re-enter the acquisition
    cycle — this lets us recover from a transient DB blip.
    """
    while True:
        try:
            async with hold_leader_lock(name) as acquired:
                if not acquired:
                    logger.debug(
                        "leader lock %s not acquired; retrying in %.1fs",
                        name, retry_seconds,
                    )
                    await asyncio.sleep(retry_seconds)
                    continue
                logger.info("acquired leader lock %s", name)
                await loop_factory()
            # If the inner loop returns (it normally runs forever), give the
            # event loop a tick before re-acquiring — keeps a misbehaving
            # loop from busy-spinning and lets cancellation land.
            await asyncio.sleep(retry_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("leader loop %s crashed; retrying", name)
            await asyncio.sleep(retry_seconds)
