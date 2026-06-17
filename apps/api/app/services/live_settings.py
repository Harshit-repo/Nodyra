"""Runtime-mutable workspace settings backed by the ``system_settings`` row.

Boot-time defaults come from ``app.config.settings`` (env vars). The admin
UI overlays them by editing the singleton row. Consumers that can read on
the hot path (retention loop, output cap, artifact limits, idle reaper)
read through this service so changes take effect without a restart.

Pool sizing and concurrency limits are applied at next pool creation /
restart — the UI surfaces that explicitly.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import settings as boot_settings
from app.db import SessionLocal
from app.models import SystemSetting

_SINGLETON_ID = "singleton"
_CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class LiveSettings:
    max_concurrent_runs: int
    runner_idle_seconds: int
    run_retention_days: int
    run_retention_max_per_workflow: int
    max_output_bytes: int
    max_artifact_bytes: int
    max_artifacts_per_run: int
    app_timezone: str
    worker_rss_soft_budget_bytes: int


def _from_boot() -> LiveSettings:
    return LiveSettings(
        max_concurrent_runs=boot_settings.max_concurrent_runs,
        runner_idle_seconds=boot_settings.runner_idle_seconds,
        run_retention_days=boot_settings.run_retention_days,
        run_retention_max_per_workflow=boot_settings.run_retention_max_per_workflow,
        max_output_bytes=boot_settings.max_output_bytes,
        max_artifact_bytes=boot_settings.max_artifact_bytes,
        max_artifacts_per_run=boot_settings.max_artifacts_per_run,
        app_timezone=boot_settings.app_timezone,
        worker_rss_soft_budget_bytes=boot_settings.worker_rss_soft_budget_bytes,
    )


_cache_value: LiveSettings | None = None
_cache_expires_at: float = 0.0
_cache_lock = asyncio.Lock()


def invalidate_live_settings_cache() -> None:
    """Drop the cached snapshot so the next read re-queries the DB."""
    global _cache_value, _cache_expires_at
    _cache_value = None
    _cache_expires_at = 0.0


async def _load_from_db() -> tuple[LiveSettings, bool]:
    """Return (snapshot, came_from_db). ``came_from_db=False`` means the
    singleton row wasn't found (or the table is missing pre-migration), in
    which case the caller should NOT cache the result — boot-default reads
    are cheap and we want test code that mutates ``settings.X`` mid-test to
    see those changes on the next access without invalidating manually.
    """
    try:
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, _SINGLETON_ID)
            if row is None:
                return _from_boot(), False
            return (
                LiveSettings(
                    max_concurrent_runs=row.max_concurrent_runs,
                    runner_idle_seconds=row.runner_idle_seconds,
                    run_retention_days=row.run_retention_days,
                    run_retention_max_per_workflow=row.run_retention_max_per_workflow,
                    max_output_bytes=row.max_output_bytes,
                    max_artifact_bytes=row.max_artifact_bytes,
                    max_artifacts_per_run=row.max_artifacts_per_run,
                    app_timezone=row.app_timezone,
                    worker_rss_soft_budget_bytes=row.worker_rss_soft_budget_bytes,
                ),
                True,
            )
    except (OperationalError, ProgrammingError):
        # Pre-migration or missing table — fall back to boot defaults.
        return _from_boot(), False


async def get_live_settings() -> LiveSettings:
    """Return the current live settings snapshot, cached for ``_CACHE_TTL_SECONDS``
    when the singleton row exists in the DB. Boot-default reads (no row, or
    pre-migration) bypass the cache so a process that's mutating
    ``settings.X`` directly (tests, certain bootstrap paths) sees changes
    immediately.
    """
    global _cache_value, _cache_expires_at
    now = time.monotonic()
    if _cache_value is not None and now < _cache_expires_at:
        return _cache_value
    async with _cache_lock:
        if _cache_value is not None and time.monotonic() < _cache_expires_at:
            return _cache_value
        value, from_db = await _load_from_db()
        if from_db:
            _cache_value = value
            _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
        return value


async def ensure_singleton_row() -> SystemSetting:
    """Create the singleton row if missing and return it for editing."""
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, _SINGLETON_ID)
        if row is None:
            row = SystemSetting(id=_SINGLETON_ID)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


def apply_updates(row: SystemSetting, updates: dict[str, Any]) -> None:
    """Mutate the row in place with the provided field updates."""
    for key, value in updates.items():
        if value is None:
            continue
        setattr(row, key, value)
