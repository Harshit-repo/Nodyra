"""Background loop that processes pending GithubSyncJob rows.

Polls the DB every POLL_INTERVAL_SECONDS for pending or retryable jobs and
dispatches them to process_push_job / process_pull_job. Runs in the API
process (not the worker) since GitHub API calls are lightweight HTTP requests.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import GithubSyncJob
from app.services.github_sync import process_pull_job, process_push_job
from app.tenancy import run_as_system

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10
MAX_CONCURRENT_JOBS = 5

_wakeup: asyncio.Event | None = None


def _get_wakeup() -> asyncio.Event:
    global _wakeup
    if _wakeup is None:
        _wakeup = asyncio.Event()
    return _wakeup


def notify_sync_workers() -> None:
    """Wake the dispatch loop immediately (call after commit that adds a job)."""
    _get_wakeup().set()


async def github_sync_dispatch_loop() -> None:
    """Poll for pending GithubSyncJob rows and process them."""
    logger.info("github_sync_dispatch_loop: started")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

    while True:
        _get_wakeup().clear()
        try:
            await _dispatch_pending(semaphore)
        except Exception:
            logger.exception("github_sync_dispatch_loop: error in dispatch cycle")
        try:
            await asyncio.wait_for(_get_wakeup().wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _dispatch_pending(semaphore: asyncio.Semaphore) -> None:
    now = datetime.now(UTC)
    with run_as_system():
        async with SessionLocal() as session:
            rows = (
                await session.scalars(
                    select(GithubSyncJob)
                    .where(
                        GithubSyncJob.status.in_(("pending", "failed")),
                        (GithubSyncJob.next_retry_at.is_(None))
                        | (GithubSyncJob.next_retry_at <= now),
                    )
                    .limit(MAX_CONCURRENT_JOBS)
                    .execution_options(skip_org_filter=True)
                )
            ).all()
            job_ids = [(j.id, j.job_type) for j in rows]

    tasks = []
    for job_id, job_type in job_ids:
        if job_type in ("push_draft", "push_publish"):
            processor = process_push_job
        else:
            processor = process_pull_job

        async def _run(jid=job_id, proc=processor):
            async with semaphore:
                try:
                    await proc(jid)
                except Exception:
                    logger.exception("github sync job %s raised unexpectedly", jid)

        tasks.append(asyncio.create_task(_run()))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
