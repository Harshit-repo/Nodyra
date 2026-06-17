"""Per-org effective quota resolution (multi-tenancy Phase C).

Override chain: ``org_settings`` row (NULL column = inherit) → instance
default (``live_settings`` for the concurrency cap, ``config.settings`` /
node-level defaults for the rest). 0 always means unlimited.

Reads are cached per org for a short TTL (same pattern as live_settings) —
the queue lease path consults limits on every dispatch tick.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgSettings

_CACHE_TTL_SECONDS = 5.0

# Built-in fan-out default of the map nodes (max_rows) — the instance-level
# ceiling when no org override exists.
DEFAULT_MAX_MAP_WIDTH = 10_000


@dataclass(frozen=True)
class EffectiveLimits:
    max_concurrent_runs: int
    executions_per_day: int
    max_map_width: int
    max_loop_iterations: int
    max_inflight_subworkflows: int
    storage_quota_bytes: int


_cache: dict[str, tuple[float, EffectiveLimits]] = {}


def invalidate_limits_cache() -> None:
    _cache.clear()


async def _instance_defaults() -> EffectiveLimits:
    from app.services.live_settings import get_live_settings

    live = await get_live_settings()
    return EffectiveLimits(
        max_concurrent_runs=live.max_concurrent_runs,
        executions_per_day=0,
        max_map_width=DEFAULT_MAX_MAP_WIDTH,
        max_loop_iterations=0,
        # 0 -> runtime_pool falls back to its global sub-workflow cap.
        max_inflight_subworkflows=0,
        storage_quota_bytes=0,
    )


async def effective_limits(
    session: AsyncSession, org_id: str | None
) -> EffectiveLimits:
    key = org_id or "default"
    cached = _cache.get(key)
    now = time.monotonic()
    if cached is not None and now < cached[0]:
        return cached[1]
    defaults = await _instance_defaults()
    row = None
    if org_id:
        row = await session.scalar(
            select(OrgSettings)
            .where(OrgSettings.org_id == org_id)
            .execution_options(skip_org_filter=True)
        )
    if row is None:
        limits = defaults
    else:
        limits = EffectiveLimits(
            max_concurrent_runs=(
                row.max_concurrent_runs
                if row.max_concurrent_runs is not None
                else defaults.max_concurrent_runs
            ),
            executions_per_day=(
                row.executions_per_day
                if row.executions_per_day is not None
                else defaults.executions_per_day
            ),
            max_map_width=(
                row.max_map_width
                if row.max_map_width is not None
                else defaults.max_map_width
            ),
            max_loop_iterations=(
                row.max_loop_iterations
                if row.max_loop_iterations is not None
                else defaults.max_loop_iterations
            ),
            max_inflight_subworkflows=(
                row.max_inflight_subworkflows
                if row.max_inflight_subworkflows is not None
                else defaults.max_inflight_subworkflows
            ),
            storage_quota_bytes=(
                row.storage_quota_bytes
                if row.storage_quota_bytes is not None
                else defaults.storage_quota_bytes
            ),
        )
    _cache[key] = (now + _CACHE_TTL_SECONDS, limits)
    return limits


__all__ = ["EffectiveLimits", "effective_limits", "invalidate_limits_cache"]
