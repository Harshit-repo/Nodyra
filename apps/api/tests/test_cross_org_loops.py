"""C2 — Cross-org background-loop correctness tests.

Each test verifies two things:
  1. Without run_as_system(): the loop silently skips non-default-org data
     (the isolation gap that would silently skip tenants if the _as_system
     wrapper were accidentally removed from main.py).
  2. With run_as_system(): the loop processes ALL orgs correctly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import app.services.queue as queue_module
import app.services.remote_dispatch as remote_dispatch_module
import app.services.retention as retention_module
import app.services.triggers as triggers_module
from app.config import settings
from app.models import (
    Organization,
    Run,
    Runner,
    RunnerPool,
    RunQueueEntry,
    Workflow,
)
from app.services.queue import requeue_expired_leases
from app.services.remote_dispatch import dispatcher
from app.tenancy import run_as_system


@pytest.fixture(autouse=True)
def _mt_on():
    old = settings.multi_tenancy_enabled
    settings.multi_tenancy_enabled = True
    yield
    settings.multi_tenancy_enabled = old


async def _insert(session_factory, *objects):
    """Insert rows bypassing the org filter (system context)."""
    async with session_factory() as session:
        with run_as_system():
            for obj in objects:
                session.add(obj)
                # The arguments are deliberately parent-before-child. Flush
                # each step so the fixture does not depend on SQLite's lax FK
                # behavior or on ORM relationships that these raw rows omit.
                await session.flush()
            await session.commit()


async def _count(session_factory, model, **where):
    async with session_factory() as session:
        with run_as_system():
            stmt = select(func.count()).select_from(model)
            for k, v in where.items():
                stmt = stmt.where(getattr(model, k) == v)
            return int(await session.scalar(stmt) or 0)


OLD = datetime.now(UTC) - timedelta(days=365)


async def test_retention_skips_non_default_org_without_system(client):
    """prune_old_runs() without run_as_system() must not touch org-b runs."""
    settings.run_retention_days = 1
    settings.run_retention_max_per_workflow = 0

    org_b_id = "org-b-retention-" + uuid.uuid4().hex[:8]
    wf_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex

    await _insert(
        retention_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Retention", slug=org_b_id),
        Workflow(id=wf_id, org_id=org_b_id, name="WF", active=True),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="success",
            started_at=OLD,
            finished_at=OLD,
        ),
    )

    # Without run_as_system — should NOT prune org-b
    await retention_module.prune_old_runs(datetime.now(UTC))
    assert await _count(retention_module.SessionLocal, Run, id=run_id) == 1, (
        "prune without run_as_system must not delete non-default-org runs"
    )

    # With run_as_system — should prune org-b
    with run_as_system():
        await retention_module.prune_old_runs(datetime.now(UTC))
    assert await _count(retention_module.SessionLocal, Run, id=run_id) == 0, (
        "prune with run_as_system must delete non-default-org runs"
    )


async def test_queue_requeue_skips_non_default_org_without_system(client):
    """requeue_expired_leases() without system context must not see org-b entries."""
    org_b_id = "org-b-queue-" + uuid.uuid4().hex[:8]
    wf_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    entry_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)

    await _insert(
        queue_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Queue", slug=org_b_id),
        Workflow(id=wf_id, org_id=org_b_id, name="WF Q", active=True),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="running",
            started_at=now,
        ),
        RunQueueEntry(
            id=entry_id,
            org_id=org_b_id,
            run_id=run_id,
            workflow_id=wf_id,
            status="leased",
            leased_by="lost-worker",
            lease_expires_at=expired,
            attempts=1,
            max_attempts=3,
        ),
    )

    # Without run_as_system — org-b entry must be invisible
    async with queue_module.SessionLocal() as session:
        acted = await requeue_expired_leases(session, now=now)
    assert acted == 0, "requeue without run_as_system must not touch org-b entries"

    # With run_as_system — must see and requeue the org-b entry
    async with queue_module.SessionLocal() as session:
        with run_as_system():
            acted = await requeue_expired_leases(session, now=now)
    assert acted == 1, "requeue with run_as_system must process org-b entries"


async def test_startup_interrupted_cleanup_runs_as_system(client, monkeypatch):
    """Startup recovery must cancel interrupted runs outside the default org."""
    import app.main as main_module

    org_b_id = "org-b-startup-" + uuid.uuid4().hex[:8]
    wf_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    entry_id = uuid.uuid4().hex
    now = datetime.now(UTC)

    await _insert(
        queue_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Startup", slug=org_b_id),
        Workflow(id=wf_id, org_id=org_b_id, name="WF Startup", active=True),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="running",
            started_at=now,
        ),
        RunQueueEntry(
            id=entry_id,
            org_id=org_b_id,
            run_id=run_id,
            workflow_id=wf_id,
            status="running",
            leased_by="worker",
            lease_expires_at=now + timedelta(seconds=30),
            attempts=1,
            max_attempts=3,
        ),
    )
    monkeypatch.setattr(main_module, "SessionLocal", queue_module.SessionLocal)

    await main_module._mark_interrupted_runs()

    async with queue_module.SessionLocal() as session:
        with run_as_system():
            run = await session.get(Run, run_id)
            entry = await session.get(RunQueueEntry, entry_id)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert entry.status == "cancelled"
    assert entry.leased_by is None
    assert entry.lease_expires_at is None


async def test_scheduler_session_isolates_org_b_workflows(client):
    """Scheduler session queries are org-filtered without run_as_system()."""
    org_b_id = "org-b-sched-" + uuid.uuid4().hex[:8]
    wf_id = uuid.uuid4().hex

    await _insert(
        triggers_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Sched", slug=org_b_id),
        Workflow(id=wf_id, org_id=org_b_id, name="Sched WF", active=True),
    )

    # Without run_as_system: active workflow in org-b must be invisible
    async with triggers_module.SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Workflow).where(Workflow.active.is_(True), Workflow.id == wf_id)
            )
        ).all()
    assert len(rows) == 0, "scheduler session without run_as_system must not see org-b workflows"

    # With run_as_system: must be visible
    async with triggers_module.SessionLocal() as session:
        with run_as_system():
            rows = (
                await session.scalars(
                    select(Workflow).where(Workflow.active.is_(True), Workflow.id == wf_id)
                )
            ).all()
    assert len(rows) == 1, "scheduler session with run_as_system must see org-b workflows"


async def test_heartbeat_skips_non_default_org_runs_without_system(client):
    """mark_stale_runners_offline without run_as_system must not requeue org-b runs.

    Runner has no org_id so it IS found as stale in both calls.  The gap is
    on the Run query: without run_as_system the org-b run is invisible so it
    never gets requeued.  With run_as_system the run is visible and requeued.
    """
    org_b_id = "org-b-hb-" + uuid.uuid4().hex[:8]
    pool_id = uuid.uuid4().hex
    runner_id = uuid.uuid4().hex
    wf_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex

    offline_seconds = 0  # any runner with past last_seen_at qualifies
    stale_time = datetime.now(UTC) - timedelta(seconds=5)

    entry_id = uuid.uuid4().hex
    now = datetime.now(UTC)

    await _insert(
        remote_dispatch_module.SessionLocal,
        Organization(id=org_b_id, name="Org B HB", slug=org_b_id),
        RunnerPool(id=pool_id, org_id=org_b_id, name="pool-b", provider="agent"),
        Runner(
            id=runner_id,
            pool_id=pool_id,
            name="runner-b",
            status="online",
            token_hash="",
            last_seen_at=stale_time,
        ),
        Workflow(id=wf_id, org_id=org_b_id, name="WF HB", active=True),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="running",
            started_at=stale_time,
            runner_id=runner_id,
        ),
        # RunQueueEntry is required — mark_stale_runners_offline skips runs
        # that have no queue entry (line: `if entry is None: continue`).
        RunQueueEntry(
            id=entry_id,
            org_id=org_b_id,
            run_id=run_id,
            workflow_id=wf_id,
            status="leased",
            leased_by=runner_id,
            lease_expires_at=now + timedelta(seconds=60),
            attempts=1,
            max_attempts=3,
        ),
    )

    # WITHOUT run_as_system: runner found (no org_id) → marked offline, but
    # the org-b Run is NOT visible → it must NOT be requeued.
    await dispatcher.mark_stale_runners_offline(offline_after_seconds=offline_seconds)

    async with remote_dispatch_module.SessionLocal() as session:
        with run_as_system():
            status_val = await session.scalar(select(Run.status).where(Run.id == run_id))
    assert status_val == "running", (
        "heartbeat without run_as_system must not requeue org-b in-flight runs"
    )

    # Reset runner to online so the second call can find it as stale again,
    # and restore run + queue entry so they are eligible for requeue.
    async with remote_dispatch_module.SessionLocal() as session:
        with run_as_system():
            runner = await session.get(Runner, runner_id)
            run = await session.get(Run, run_id)
            entry = await session.get(RunQueueEntry, entry_id)
            runner.status = "online"
            runner.last_seen_at = stale_time
            run.status = "running"
            run.runner_id = runner_id
            entry.status = "leased"
            entry.leased_by = runner_id
            entry.lease_expires_at = now + timedelta(seconds=60)
            await session.commit()

    # WITH run_as_system: Run is visible → must be requeued.
    with run_as_system():
        await dispatcher.mark_stale_runners_offline(offline_after_seconds=offline_seconds)

    async with remote_dispatch_module.SessionLocal() as session:
        with run_as_system():
            status_val = await session.scalar(select(Run.status).where(Run.id == run_id))
    assert status_val in ("pending", "queued"), (
        "heartbeat with run_as_system must requeue org-b in-flight runs when runner is stale"
    )


def test_main_loop_tasks_all_wrapped_in_as_system():
    """Static guard: every asyncio.create_task call in main.py must go through
    _as_system() or _make_loop_task() so MT filtering is bypassed for cross-org work.

    Fail this test if someone adds a bare asyncio.create_task(some_loop())
    without the system-context wrapper.
    """
    import ast
    import inspect

    import app.main as main_module

    src = inspect.getsource(main_module)
    tree = ast.parse(src)

    create_task_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_task"
        ):
            if node.args:
                create_task_calls.append(ast.unparse(node.args[0]))

    # broker_reaper_loop is exempt: it's a pure in-process event broker with
    # no DB queries, so org filtering is irrelevant there.
    _EXEMPT = {"broker_reaper_loop()"}
    bare_calls = [
        c
        for c in create_task_calls
        if "_as_system(" not in c and "_make_loop_task(" not in c and c not in _EXEMPT
    ]
    assert not bare_calls, (
        f"Found asyncio.create_task calls NOT wrapped in _as_system or "
        f"_make_loop_task: {bare_calls}. Background loops must use _as_system() "
        "so all orgs are visible under MT."
    )
