"""Durable, DB-backed environment build queue."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Environment, EnvironmentBuildJob, User

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "leased", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "superseded")
_NOTIFY_CHANNEL = "nodyra:environment-builds:notify"
_wakeup: asyncio.Event | None = None


def _get_wakeup() -> asyncio.Event:
    global _wakeup
    if _wakeup is None:
        _wakeup = asyncio.Event()
    return _wakeup


async def notify_environment_build_workers() -> None:
    """Wake build dispatch loops after the enqueueing transaction commits."""
    _get_wakeup().set()
    try:
        from app.redis_client import redis_client

        await redis_client.publish(_NOTIFY_CHANNEL, "1")
    except Exception:
        logger.debug("environment build notify: Redis publish failed; polling fallback remains active")


async def _redis_subscriber() -> None:
    try:
        from app.redis_client import redis_client

        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(_NOTIFY_CHANNEL)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    _get_wakeup().set()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("environment build Redis subscriber exited; polling fallback remains active")
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(_NOTIFY_CHANNEL)
                await pubsub.aclose()
    except Exception:
        pass


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _worker_id() -> str:
    return f"{socket.gethostname()}:{id(asyncio.current_task())}"


def _environment_hash(env: Environment) -> str:
    payload = {
        "packages": list(env.packages or []),
        "python_version": env.python_version,
        "backend": env.backend,
        "backend_config": env.backend_config or {},
        "interpreter": env.interpreter,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_kwargs(env: Environment) -> dict[str, Any]:
    return {
        "packages_hash": _environment_hash(env),
        "package_snapshot": list(env.packages or []),
        "python_version": str(env.python_version or ""),
        "backend": str(env.backend or ""),
        "backend_config": dict(env.backend_config or {}),
        "interpreter": str(env.interpreter or "cpython"),
    }


def _append_attempt(
    job: EnvironmentBuildJob,
    *,
    event: str,
    error: str | None,
    ts: datetime,
) -> None:
    history = list(job.attempts_log or [])
    history.append(
        {
            "attempt": job.attempts,
            "event": event,
            "error": error,
            "ts": ts.isoformat(),
        }
    )
    job.attempts_log = history


async def enqueue_environment_build(
    session: AsyncSession,
    env: Environment,
    *,
    reason: str,
    requested_by: User | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> EnvironmentBuildJob:
    """Persist a build request and supersede stale queued requests.

    The caller owns commit/rollback. Call notify_environment_build_workers()
    only after that commit succeeds.
    """
    moment = _now(now)
    snapshot = _snapshot_kwargs(env)
    active = (
        await session.scalars(
            select(EnvironmentBuildJob)
            .where(
                EnvironmentBuildJob.environment_id == env.id,
                EnvironmentBuildJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(EnvironmentBuildJob.created_at.asc())
            .execution_options(skip_org_filter=True)
        )
    ).all()

    for job in active:
        if job.packages_hash == snapshot["packages_hash"]:
            return job
        if job.status in ("queued", "leased"):
            job.status = "superseded"
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = moment
            job.last_error = "superseded by a newer environment build request"
            _append_attempt(job, event="superseded", error=job.last_error, ts=moment)

    env.status = "pending"
    env.status_detail = ""
    job = EnvironmentBuildJob(
        org_id=env.org_id,
        environment_id=env.id,
        reason=reason[:40],
        status="queued",
        available_at=moment,
        max_attempts=max_attempts or settings.environment_build_queue_default_max_attempts,
        requested_by_user_id=requested_by.id if requested_by else None,
        requested_by_email=requested_by.email if requested_by else None,
        **snapshot,
    )
    session.add(job)
    await session.flush()
    return job


async def get_environment_build_job(
    session: AsyncSession,
    job_id: str,
) -> EnvironmentBuildJob | None:
    return await session.scalar(
        select(EnvironmentBuildJob)
        .where(EnvironmentBuildJob.id == job_id)
        .execution_options(skip_org_filter=True)
    )


async def lease_environment_build(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> EnvironmentBuildJob | None:
    moment = _now(now)
    stmt = (
        select(EnvironmentBuildJob)
        .where(
            EnvironmentBuildJob.status == "queued",
            EnvironmentBuildJob.available_at <= moment,
        )
        .order_by(EnvironmentBuildJob.available_at.asc())
        .limit(1)
        .execution_options(skip_org_filter=True)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = await session.scalar(stmt)
    if job is None:
        return None
    job.status = "leased"
    job.lease_owner = worker_id
    job.lease_expires_at = moment + timedelta(
        seconds=settings.environment_build_queue_lease_seconds
    )
    job.attempts += 1
    await session.flush()
    return job


async def _has_other_active_job(
    session: AsyncSession,
    job: EnvironmentBuildJob,
) -> bool:
    other = await session.scalar(
        select(EnvironmentBuildJob.id)
        .where(
            EnvironmentBuildJob.environment_id == job.environment_id,
            EnvironmentBuildJob.id != job.id,
            EnvironmentBuildJob.status.in_(ACTIVE_STATUSES),
        )
        .limit(1)
        .execution_options(skip_org_filter=True)
    )
    return other is not None


async def complete_environment_build(
    session: AsyncSession,
    *,
    job_id: str,
    now: datetime | None = None,
) -> bool:
    job = await get_environment_build_job(session, job_id)
    if job is None:
        return False
    moment = _now(now)
    job.status = "succeeded"
    job.lease_owner = None
    job.lease_expires_at = None
    job.finished_at = moment
    _append_attempt(job, event="succeeded", error=None, ts=moment)
    if await _has_other_active_job(session, job):
        env = await session.get(Environment, job.environment_id)
        if env is not None:
            env.status = "pending"
            env.status_detail = "A newer environment build is queued."
    return True


async def fail_environment_build(
    session: AsyncSession,
    *,
    job_id: str,
    error: str,
    retryable: bool = True,
    now: datetime | None = None,
) -> EnvironmentBuildJob | None:
    job = await get_environment_build_job(session, job_id)
    if job is None:
        return None
    moment = _now(now)
    job.last_error = error
    job.lease_owner = None
    job.lease_expires_at = None
    if retryable and job.attempts < job.max_attempts:
        backoff = min(
            settings.environment_build_queue_retry_backoff_base_seconds
            * (2 ** max(job.attempts - 1, 0)),
            settings.environment_build_queue_retry_backoff_max_seconds,
        )
        job.status = "queued"
        job.available_at = moment + timedelta(seconds=backoff)
        _append_attempt(job, event="retry_scheduled", error=error, ts=moment)
        env = await session.get(Environment, job.environment_id)
        if env is not None:
            env.status = "pending"
            env.status_detail = f"Retrying environment build after failure: {error}"
        return job

    job.status = "failed"
    job.finished_at = moment
    _append_attempt(job, event="failed", error=error, ts=moment)
    env = await session.get(Environment, job.environment_id)
    if env is not None:
        if await _has_other_active_job(session, job):
            env.status = "pending"
            env.status_detail = "A newer environment build is queued."
        else:
            env.status = "error"
            env.status_detail = error
    return job


async def requeue_expired_environment_build_leases(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    moment = _now(now)
    rows = (
        await session.scalars(
            select(EnvironmentBuildJob)
            .where(
                EnvironmentBuildJob.status.in_(("leased", "running")),
                EnvironmentBuildJob.lease_expires_at.is_not(None),
                EnvironmentBuildJob.lease_expires_at <= moment,
            )
            .execution_options(skip_org_filter=True)
        )
    ).all()
    acted = 0
    for job in rows:
        if job.lease_expires_at is None or _as_aware(job.lease_expires_at) > moment:
            continue
        acted += 1
        await fail_environment_build(
            session,
            job_id=job.id,
            error="lease expired (worker lost)",
            retryable=True,
            now=moment,
        )
    return acted


async def _heartbeat_until_stopped(
    job_id: str,
    stop: asyncio.Event,
    *,
    worker_id: str,
) -> None:
    lease_seconds = max(settings.environment_build_queue_lease_seconds, 3)
    interval = max(1.0, lease_seconds / 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass
        async with SessionLocal() as session:
            job = await get_environment_build_job(session, job_id)
            if job is None or job.status != "running":
                return
            job.lease_owner = worker_id
            job.lease_expires_at = _now(None) + timedelta(seconds=lease_seconds)
            await session.commit()


async def process_environment_build_job(job_id: str) -> None:
    worker = _worker_id()
    async with SessionLocal() as session:
        job = await get_environment_build_job(session, job_id)
        if job is None or job.status not in ("leased", "queued", "running"):
            return
        env = await session.get(Environment, job.environment_id)
        if env is None:
            job.status = "cancelled"
            job.finished_at = _now(None)
            await session.commit()
            return
        if _environment_hash(env) != job.packages_hash:
            moment = _now(None)
            job.status = "superseded"
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = moment
            job.last_error = "environment changed before this build started"
            _append_attempt(job, event="superseded", error=job.last_error, ts=moment)
            if await _has_other_active_job(session, job):
                env.status = "pending"
                env.status_detail = "A newer environment build is queued."
            await session.commit()
            await notify_environment_build_workers()
            return
        moment = _now(None)
        job.status = "running"
        job.lease_owner = worker
        job.lease_expires_at = moment + timedelta(
            seconds=settings.environment_build_queue_lease_seconds
        )
        job.started_at = job.started_at or moment
        registry_build = job.reason.startswith("registry:")
        env.status = "building"
        env.status_detail = ""
        await session.commit()

    stop = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_until_stopped(job_id, stop, worker_id=worker)
    )
    retryable_failure = False
    try:
        from app.services.backends import build_environment

        await build_environment(job.environment_id)
        async with SessionLocal() as session:
            env = await session.get(Environment, job.environment_id)
            if env is not None and env.status == "ready":
                await complete_environment_build(session, job_id=job_id)
                if registry_build:
                    from app.services.metrics import registry_install_total

                    registry_install_total.inc(status="succeeded")
            else:
                detail = env.status_detail if env is not None else "environment missing"
                retryable_failure = True
                failed_job = await fail_environment_build(
                    session,
                    job_id=job_id,
                    error=detail or "environment build failed",
                    retryable=True,
                )
                if registry_build and failed_job is not None and failed_job.status == "failed":
                    from app.services.metrics import registry_install_total

                    registry_install_total.inc(status="failed")
            await session.commit()
    except Exception as exc:
        retryable_failure = True
        async with SessionLocal() as session:
            failed_job = await fail_environment_build(
                session,
                job_id=job_id,
                error=f"{type(exc).__name__}: {exc}",
                retryable=True,
            )
            if registry_build and failed_job is not None and failed_job.status == "failed":
                from app.services.metrics import registry_install_total

                registry_install_total.inc(status="failed")
            await session.commit()
        logger.exception("environment build job %s failed unexpectedly", job_id)
    finally:
        stop.set()
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        if retryable_failure:
            await notify_environment_build_workers()


async def run_environment_build_dispatch_loop() -> None:
    logger.info("environment_build_dispatch_loop: started")
    worker = _worker_id()
    subscriber = asyncio.create_task(_redis_subscriber())
    in_flight: set[asyncio.Task] = set()
    try:
        while True:
            _get_wakeup().clear()
            try:
                done = {task for task in in_flight if task.done()}
                for task in done:
                    in_flight.remove(task)
                    with contextlib.suppress(Exception):
                        task.result()

                async with SessionLocal() as session:
                    requeued = await requeue_expired_environment_build_leases(session)
                    await session.commit()
                    if requeued:
                        logger.info("environment builds: requeued %d expired lease(s)", requeued)

                max_jobs = max(settings.environment_build_queue_max_concurrent_jobs, 0)
                while len(in_flight) < max_jobs:
                    async with SessionLocal() as session:
                        job = await lease_environment_build(session, worker_id=worker)
                        await session.commit()
                        job_id = job.id if job is not None else None
                    if job_id is None:
                        break
                    in_flight.add(asyncio.create_task(process_environment_build_job(job_id)))
            except Exception:
                logger.exception("environment build dispatch loop tick failed")

            try:
                await asyncio.wait_for(
                    _get_wakeup().wait(),
                    timeout=settings.environment_build_queue_poll_seconds,
                )
            except TimeoutError:
                pass
    finally:
        subscriber.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await subscriber
        if in_flight:
            await asyncio.wait(
                in_flight,
                timeout=settings.queue_dispatch_shutdown_timeout_seconds,
            )
