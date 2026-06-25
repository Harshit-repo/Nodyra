"""Per-org run metering (multi-tenancy Phase C3).

``record_run_started`` runs inside start_run's transaction (admission time);
``record_run_completion`` is called from the run finalize path. Both bypass
the tenancy filter — they execute in mixed org contexts (run task pinned to
its org, dispatch loop as system) and always address rows by explicit org.

Postgres uses a native upsert so concurrent completions on the hot
(org, day) row don't race; SQLite (single-process dev) uses select-then-
update under the unique constraint.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NodeRun, Run, RunMeter


def _today() -> date:
    return datetime.now(UTC).date()


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes for timezone=True columns; Postgres
    returns aware. Normalise to aware-UTC so subtraction never mixes them."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _meter_row(
    session: AsyncSession, org_id: str, day: date
) -> RunMeter | None:
    return await session.scalar(
        select(RunMeter)
        .where(RunMeter.org_id == org_id, RunMeter.day == day)
        .execution_options(skip_org_filter=True)
    )


async def _upsert(
    session: AsyncSession,
    org_id: str,
    *,
    runs: int = 0,
    compute_seconds: float = 0.0,
    node_runs: int = 0,
) -> None:
    day = _today()
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(RunMeter).values(
            org_id=org_id,
            day=day,
            runs=runs,
            compute_seconds=compute_seconds,
            node_runs=node_runs,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_run_meters_org_day",
                set_={
                    "runs": RunMeter.runs + runs,
                    "compute_seconds": RunMeter.compute_seconds + compute_seconds,
                    "node_runs": RunMeter.node_runs + node_runs,
                },
            )
        )
        return
    row = await _meter_row(session, org_id, day)
    if row is None:
        session.add(
            RunMeter(
                org_id=org_id,
                day=day,
                runs=runs,
                compute_seconds=compute_seconds,
                node_runs=node_runs,
            )
        )
    else:
        row.runs += runs
        row.compute_seconds += compute_seconds
        row.node_runs += node_runs
    await session.flush()


async def runs_today(session: AsyncSession, org_id: str) -> int:
    row = await _meter_row(session, org_id, _today())
    return row.runs if row is not None else 0


async def record_run_started(session: AsyncSession, org_id: str) -> None:
    """+1 run at admission — makes executions/day a hard daily ceiling."""
    await _upsert(session, org_id, runs=1)


async def record_run_completion(session: AsyncSession, run_id: str) -> None:
    """Accumulate compute wall-clock and node_runs rows for a finished run."""
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .execution_options(skip_org_filter=True)
    )
    if run is None or not run.org_id:
        return
    compute_seconds = 0.0
    if run.started_at is not None and run.finished_at is not None:
        compute_seconds = max(
            0.0,
            (_aware(run.finished_at) - _aware(run.started_at)).total_seconds(),
        )
    node_run_count = int(
        await session.scalar(
            select(func.count())
            .select_from(NodeRun)
            .where(NodeRun.run_id == run_id)
        )
        or 0
    )
    await _upsert(
        session,
        run.org_id,
        compute_seconds=compute_seconds,
        node_runs=node_run_count,
    )
