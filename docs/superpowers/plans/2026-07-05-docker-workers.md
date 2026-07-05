# One-Click Docker Workers + Autoscaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user add Docker-backed workers to an agent runner pool with one click, autoscale them within resource limits based on queue depth, target a local or remote Docker daemon, and optionally run each workflow in a hardened sandbox container.

**Architecture:** Reuse the existing agent-runner machinery (signed registration tokens, outbound-WS heartbeats, offline-requeue, drain). A new `docker_workers` service spawns long-lived `nodyra-runner` containers (image built locally from the server's wheel cache), tags them in `Runner.capabilities`, and a background loop scales them per pool via a **pure** `plan_scaling` function. A per-run **sandbox** path is added to the runner agent so a Docker-capable runner executes runs in disposable hardened sibling containers. All new pool config lives in existing JSON columns — no migrations.

**Tech Stack:** FastAPI, SQLAlchemy (async), Pydantic, the `docker` Python SDK, pytest/pytest-asyncio, React + vitest.

**Spec:** `docs/superpowers/specs/2026-07-05-docker-workers-design.md`

**Conventions to follow (verified in the codebase):**
- Queue-style unit tests spin up their own temp SQLite engine/session (see `apps/api/tests/test_run_queue.py` fixture) — reuse that pattern for model/service tests that don't need the app client.
- Hardened container spawn kwargs live in `apps/api/app/services/container_runtime.py::hardening_kwargs`; the agent package cannot import `app.*`, so the agent gets its own small copy (cross-referenced in comments).
- `IMAGE_SCHEMA_VERSION` lives in `container_runtime.py` (currently `"v3"`); the agent image embeds its own schema constant.
- Registration-token mint currently inline in `runner_pools.py::create_registration_token` (~lines 515-572).
- `settings` limits already exist: `sandbox_max_cpu` (4.0), `sandbox_max_memory_mb` (8192).

---

## Task 1: Shared runner-token mint helper (refactor, no behavior change)

**Files:**
- Create: `apps/api/app/services/runner_tokens.py`
- Modify: `apps/api/app/routers/runner_pools.py` (`create_registration_token`, ssh_onboard token mint)
- Test: `apps/api/tests/test_runner_token_ttl.py` (existing — must still pass)

- [ ] **Step 1: Write the helper**

Create `apps/api/app/services/runner_tokens.py`:

```python
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
```

- [ ] **Step 2: Rewire `create_registration_token`**

In `apps/api/app/routers/runner_pools.py`, replace the body that builds the Runner + token (currently ~lines 537-563) with:

```python
    body = body or RegistrationTokenRequest()
    runner, token, expires_at = await mint_runner_registration(
        session,
        pool_id,
        org_id=pool.org_id,
        name=(body.name or f"runner-{pool.name[:20]}"),
        max_concurrent_runs=body.max_concurrent_runs or 1,
        capabilities=body.capabilities or {},
    )
```

Add the import near the other service imports:

```python
from app.services.runner_tokens import mint_runner_registration
```

Leave the `api_url` derivation and `RegistrationTokenResponse` return untouched.

- [ ] **Step 3: Run the existing token tests**

Run: `uv run pytest apps/api/tests/test_runner_token_ttl.py apps/api/tests/test_runner_pools.py -q`
Expected: PASS (behavior unchanged).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/services/runner_tokens.py apps/api/app/routers/runner_pools.py
git commit -m "refactor(runners): extract shared runner-token mint helper"
```

---

## Task 2: Pure autoscaling planner

**Files:**
- Create: `apps/api/app/services/docker_workers.py`
- Test: `apps/api/tests/test_docker_workers.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_docker_workers.py`:

```python
"""Docker-worker autoscaling planner + spawn/remove/reconcile."""

from app.services.docker_workers import (
    PoolState,
    RunnerState,
    SpawnRunner,
    RemoveRunner,
    plan_scaling,
)


def _pool(**kw):
    base = dict(
        pool_id="p1",
        queued=0,
        runners=[],
        enabled=True,
        min_runners=0,
        max_runners=4,
        idle_seconds=300,
    )
    base.update(kw)
    return PoolState(**base)


def _runner(rid="r1", current=0, idle=0.0, draining=False, online=True):
    return RunnerState(
        runner_id=rid,
        current_runs=current,
        idle_seconds=idle,
        draining=draining,
        online=online,
    )


def test_scale_up_on_backlog_no_runners():
    actions = plan_scaling([_pool(queued=3)])
    assert actions == [SpawnRunner("p1")]


def test_scale_up_when_all_saturated():
    actions = plan_scaling([_pool(queued=5, runners=[_runner(current=2)], max_runners=4)])
    # max_concurrent per runner is 2 → saturated → spawn one more
    assert actions == [SpawnRunner("p1")]


def test_no_scale_up_when_capacity_free():
    actions = plan_scaling([_pool(queued=5, runners=[_runner(current=0)])])
    assert actions == []


def test_respect_max_runners():
    runners = [_runner(rid=f"r{i}", current=2) for i in range(4)]
    actions = plan_scaling([_pool(queued=9, runners=runners, max_runners=4)])
    assert actions == []


def test_scale_down_idle_runner():
    r = _runner(current=0, idle=600, draining=True)
    actions = plan_scaling([_pool(queued=0, runners=[r], min_runners=0)])
    assert actions == [RemoveRunner("r1")]


def test_no_scale_down_when_busy():
    r = _runner(current=1, idle=600, draining=True)
    assert plan_scaling([_pool(queued=0, runners=[r])]) == []


def test_respect_min_runners():
    r = _runner(current=0, idle=600, draining=True)
    assert plan_scaling([_pool(runners=[r], min_runners=1)]) == []


def test_disabled_pool_no_actions():
    assert plan_scaling([_pool(queued=9, enabled=False)]) == []


def test_clamp_min_over_max():
    # min > max → treated as max; no spawn beyond max
    runners = [_runner(rid=f"r{i}", current=2) for i in range(2)]
    actions = plan_scaling([_pool(queued=9, runners=runners, min_runners=5, max_runners=2)])
    assert actions == []


def test_one_action_per_pool_per_tick():
    actions = plan_scaling([_pool(queued=99, runners=[_runner(current=2)], max_runners=8)])
    assert len(actions) == 1
```

Note: `max_concurrent per runner` is carried on `RunnerState` implicitly via
`current_runs` vs a per-runner cap; model it as: a runner is "saturated" when
`current_runs >= max_concurrent_runs`. Add `max_concurrent_runs` to
`RunnerState` (default 2) and set it in the saturated tests. Adjust the two
saturated-test constructors to pass `max_concurrent_runs=2` explicitly.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the planner**

Create `apps/api/app/services/docker_workers.py`:

```python
"""One-click Docker workers: spawn long-lived nodyra-runner containers,
autoscale them per pool by queue depth, target a local or remote daemon.

Split into a PURE planner (plan_scaling — unit-testable, no I/O) and Docker
I/O (spawn/remove/reconcile/loop). Reuses the agent-runner trust model:
containers run the nodyra-runner agent, register via a signed token, and
connect outbound over WebSocket, so heartbeat/offline-requeue/drain all apply.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_MAX_RUNNERS_CEILING = 32


@dataclass(frozen=True)
class RunnerState:
    runner_id: str
    current_runs: int = 0
    idle_seconds: float = 0.0
    draining: bool = False
    online: bool = True
    max_concurrent_runs: int = 2


@dataclass(frozen=True)
class PoolState:
    pool_id: str
    queued: int = 0
    runners: list[RunnerState] = field(default_factory=list)
    enabled: bool = True
    min_runners: int = 0
    max_runners: int = 4
    idle_seconds: float = 300.0


@dataclass(frozen=True)
class SpawnRunner:
    pool_id: str


@dataclass(frozen=True)
class RemoveRunner:
    runner_id: str


Action = SpawnRunner | RemoveRunner


def plan_scaling(pools: list[PoolState]) -> list[Action]:
    """Pure: at most one action per pool per tick. Scale up on backlog when
    all online runners are saturated and count < max; else scale down one
    idle, drained runner while count > min. Never touch a busy runner."""
    actions: list[Action] = []
    for pool in pools:
        if not pool.enabled:
            continue
        max_runners = min(pool.max_runners, _MAX_RUNNERS_CEILING)
        min_runners = min(pool.min_runners, max_runners)
        count = len(pool.runners)

        if count < min_runners:
            actions.append(SpawnRunner(pool.pool_id))
            continue

        online = [r for r in pool.runners if r.online]
        has_free = any(
            r.current_runs < r.max_concurrent_runs for r in online
        )
        if pool.queued > 0 and not has_free and count < max_runners:
            actions.append(SpawnRunner(pool.pool_id))
            continue

        if count > min_runners:
            idle = [
                r for r in pool.runners
                if r.current_runs == 0
                and r.idle_seconds >= pool.idle_seconds
                and r.draining
            ]
            if idle:
                actions.append(RemoveRunner(idle[0].runner_id))
    return actions
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/docker_workers.py apps/api/tests/test_docker_workers.py
git commit -m "feat(runners): pure Docker-worker autoscaling planner"
```

---

## Task 3: Agent image builder

**Files:**
- Modify: `apps/api/app/services/docker_workers.py`
- Test: `apps/api/tests/test_docker_workers.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_docker_workers.py`:

```python
class FakeImages:
    def __init__(self, existing=()):
        self._have = set(existing)
        self.built = []

    def get(self, tag):
        if tag not in self._have:
            raise KeyError(tag)
        return object()

    def build(self, **kw):
        self.built.append(kw)
        self._have.add(kw.get("tag"))
        return (object(), iter(()))


class FakeContainers:
    def __init__(self):
        self.run_calls = []
        self._by_name = {}

    def run(self, image, **kw):
        self.run_calls.append({"image": image, **kw})
        name = kw.get("name")
        c = type("C", (), {"name": name, "removed": False})()
        self._by_name[name] = c
        return c

    def get(self, name):
        if name not in self._by_name:
            raise KeyError(name)
        return self._by_name[name]

    def list(self, **kw):
        return list(self._by_name.values())


class FakeDockerClient:
    def __init__(self, existing_images=()):
        self.images = FakeImages(existing_images)
        self.containers = FakeContainers()

    def info(self):
        return {"Runtimes": {"runc": {}}}


async def test_ensure_agent_image_builds_when_absent(monkeypatch):
    from app.services import docker_workers

    async def _fake_wheels(*a, **k):
        from pathlib import Path
        return [Path("nodyra_core.whl")]

    monkeypatch.setattr(docker_workers, "ensure_wheels", _fake_wheels)
    monkeypatch.setattr(docker_workers, "_agent_build_context", lambda wheels: b"ctx")
    client = FakeDockerClient()
    tag = await docker_workers.ensure_agent_image(client)
    assert tag == docker_workers.agent_image_tag()
    assert client.images.built  # built once
    # Cache hit: second call does not rebuild
    client.images.built.clear()
    await docker_workers.ensure_agent_image(client)
    assert not client.images.built
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -k agent_image -q`
Expected: FAIL (`ensure_agent_image` undefined).

- [ ] **Step 3: Implement the image builder**

Append to `apps/api/app/services/docker_workers.py` (add imports at top):

```python
import io
import tarfile
from pathlib import Path

from app.services.wheel_index import ensure_wheels

AGENT_IMAGE_SCHEMA = "v1"


def agent_image_tag() -> str:
    return f"nodyra-runner-agent:{AGENT_IMAGE_SCHEMA}"


_ENTRYPOINT = (
    "#!/bin/sh\nset -e\n"
    'nodyra-runner register --api-url "$NODYRA_API_URL" '
    '--token "$NODYRA_RUNNER_TOKEN" --name "$NODYRA_RUNNER_NAME"\n'
    "exec nodyra-runner start\n"
)


def _agent_dockerfile() -> str:
    return (
        "FROM python:3.12-slim\n"
        "RUN pip install --no-cache-dir uv\n"
        "COPY wheels /wheels\n"
        "RUN pip install --no-cache-dir nodyra-runner --find-links /wheels\n"
        "RUN useradd --create-home --uid 65533 --shell /usr/sbin/nologin runner\n"
        "COPY entrypoint.sh /entrypoint.sh\n"
        "RUN chmod +x /entrypoint.sh && chown -R runner /home/runner\n"
        "USER runner\n"
        'ENTRYPOINT ["/entrypoint.sh"]\n'
    )


def _agent_build_context(wheels: list[Path]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        df = _agent_dockerfile().encode()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(df)
        tar.addfile(info, io.BytesIO(df))
        ep = _ENTRYPOINT.encode()
        ep_info = tarfile.TarInfo("entrypoint.sh")
        ep_info.size = len(ep)
        tar.addfile(ep_info, io.BytesIO(ep))
        for wheel in wheels:
            tar.add(wheel, arcname=f"wheels/{wheel.name}")
    buf.seek(0)
    return buf.read()


async def ensure_agent_image(client) -> str:
    """Build the nodyra-runner-agent image from the server's wheel cache if
    absent. Sync Docker calls run in the default executor."""
    tag = agent_image_tag()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, client.images.get, tag)
        return tag
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass
    wheels = await ensure_wheels()
    context = _agent_build_context(list(wheels))
    await loop.run_in_executor(
        None,
        lambda: client.images.build(
            fileobj=io.BytesIO(context), custom_context=True, tag=tag, rm=True
        ),
    )
    logger.info("built agent image %s", tag)
    return tag
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/docker_workers.py apps/api/tests/test_docker_workers.py
git commit -m "feat(runners): build nodyra-runner agent image from wheel cache"
```

---

## Task 4: Spawn / remove / reconcile Docker runners

**Files:**
- Modify: `apps/api/app/services/docker_workers.py`
- Test: `apps/api/tests/test_docker_workers.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_docker_workers.py` (uses the temp-session fixture pattern from `test_run_queue.py` — copy that `session` fixture into this file if not already present, plus a helper to create a pool):

```python
import pytest_asyncio
import os, tempfile
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app import models  # noqa: F401
from app.db import Base
from app.models import RunnerPool, Runner


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    engine = create_async_engine(f"sqlite+aiosqlite:///{handle.name}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
    try:
        os.unlink(handle.name)
    except OSError:
        pass


async def test_spawn_docker_runner_sets_caps_and_labels(session, monkeypatch):
    from app.services import docker_workers

    pool = RunnerPool(
        name="dw", provider="agent",
        provider_config={
            "docker_runner": {"cpu": 1.0, "memory_mb": 512, "pids": 256,
                              "max_concurrent_runs": 2, "sandbox": True},
            "docker_api_url": "http://host.docker.internal:8000",
        },
    )
    session.add(pool)
    await session.commit()

    client = FakeDockerClient(existing_images=(docker_workers.agent_image_tag(),))
    runner = await docker_workers.spawn_docker_runner(session, pool, client=client)

    assert runner.capabilities["docker_managed"] is True
    assert runner.capabilities["sandbox"] is True
    call = client.containers.run_calls[0]
    assert call["nano_cpus"] == 1_000_000_000
    assert call["mem_limit"] == "512m"
    assert call["pids_limit"] == 256
    assert call["labels"]["nodyra.pool"] == pool.id
    assert call["labels"]["nodyra.runner"] == runner.id
    # sandbox=True → docker socket mounted into the runner
    assert any("docker.sock" in str(v) for v in call["volumes"])
    assert call["environment"]["NODYRA_RUNNER_TOKEN"]


async def test_spawn_no_socket_when_sandbox_off(session):
    from app.services import docker_workers
    pool = RunnerPool(name="dw2", provider="agent",
                      provider_config={"docker_runner": {"sandbox": False},
                                       "docker_api_url": "http://x:8000"})
    session.add(pool); await session.commit()
    client = FakeDockerClient(existing_images=(docker_workers.agent_image_tag(),))
    await docker_workers.spawn_docker_runner(session, pool, client=client)
    call = client.containers.run_calls[0]
    assert not call.get("volumes")


async def test_remove_busy_runner_refused(session):
    from app.services import docker_workers
    pool = RunnerPool(name="dw3", provider="agent", provider_config={})
    session.add(pool); await session.commit()
    r = Runner(pool_id=pool.id, name="r", status="online", current_runs=1,
               capabilities={"docker_managed": True, "container_name": "c"})
    session.add(r); await session.commit()
    client = FakeDockerClient()
    with pytest.raises(docker_workers.RunnerBusy):
        await docker_workers.remove_docker_runner(session, r, client=client)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -k "spawn or remove" -q`
Expected: FAIL (`spawn_docker_runner` undefined).

- [ ] **Step 3: Implement spawn/remove/reconcile**

Append to `apps/api/app/services/docker_workers.py`:

```python
from app.config import settings
from app.models import Runner, RunnerPool
from app.services.runner_tokens import mint_runner_registration


class RunnerBusy(Exception):
    """Raised when removing a runner that still has in-flight runs."""


class DaemonUnreachable(Exception):
    """Raised when the configured Docker daemon can't be contacted."""


def _docker_client(cfg: dict):
    try:
        import docker  # noqa: PLC0415
    except ImportError as exc:
        raise DaemonUnreachable(
            "the 'docker' package is required for Docker workers"
        ) from exc
    host = (cfg or {}).get("docker_host") or ""
    try:
        return docker.DockerClient(base_url=host) if host else docker.from_env()
    except Exception as exc:  # noqa: BLE001
        raise DaemonUnreachable(str(exc)) from exc


def _resolve_api_url(cfg: dict) -> str:
    url = (cfg or {}).get("docker_api_url") or settings.public_api_url
    if not url and not (cfg or {}).get("docker_host"):
        url = "http://host.docker.internal:8000"
    if not url:
        raise DaemonUnreachable(
            "cannot resolve an API URL for the runner to dial back to; set "
            "provider_config.docker_api_url or PUBLIC_API_URL"
        )
    return url.rstrip("/")


async def spawn_docker_runner(session, pool: RunnerPool, *, client=None, name=None) -> Runner:
    cfg = pool.provider_config or {}
    rc = cfg.get("docker_runner") or {}
    sandbox = bool(rc.get("sandbox"))
    client = client or _docker_client(cfg)
    await ensure_agent_image(client)

    runner, token, _ = await mint_runner_registration(
        session, pool.id, org_id=pool.org_id,
        name=name or f"docker-{pool.name[:16]}",
        max_concurrent_runs=int(rc.get("max_concurrent_runs", 2)),
        capabilities={"docker_managed": True, "sandbox": sandbox},
    )
    container_name = f"nodyra-worker-{runner.id[:12]}"
    api_url = _resolve_api_url(cfg)

    volumes = {}
    if sandbox:
        volumes["/var/run/docker.sock"] = {
            "bind": "/var/run/docker.sock", "mode": "rw"
        }
    run_kwargs = dict(
        detach=True,
        name=container_name,
        environment={
            "NODYRA_API_URL": api_url,
            "NODYRA_RUNNER_TOKEN": token,
            "NODYRA_RUNNER_NAME": runner.name,
        },
        network=cfg.get("docker_network") or None,
        mem_limit=f"{int(rc.get('memory_mb', 1024))}m",
        nano_cpus=int(float(rc.get("cpu", 1.0)) * 1_000_000_000),
        pids_limit=int(rc.get("pids", 512)),
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
        security_opt=["no-new-privileges:true"],
        init=True,
        extra_hosts={"host.docker.internal": "host-gateway"},
        volumes=volumes or None,
        labels={
            "nodyra.managed": "true",
            "nodyra.pool": pool.id,
            "nodyra.runner": runner.id,
        },
    )
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, lambda: client.containers.run(agent_image_tag(), **run_kwargs)
        )
    except Exception as exc:  # noqa: BLE001 — roll back the placeholder row
        await session.delete(runner)
        await session.commit()
        raise DaemonUnreachable(f"failed to start runner container: {exc}") from exc

    caps = dict(runner.capabilities or {})
    caps["container_name"] = container_name
    runner.capabilities = caps
    await session.commit()
    await session.refresh(runner)
    return runner


async def remove_docker_runner(session, runner: Runner, *, client=None, force=False) -> None:
    if runner.current_runs > 0 and not force:
        raise RunnerBusy(f"runner {runner.id} has {runner.current_runs} in-flight run(s)")
    container_name = (runner.capabilities or {}).get("container_name")
    if client is not None and container_name:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, lambda: client.containers.get(container_name).remove(force=True)
            )
        except Exception:  # noqa: BLE001 — already gone
            pass
    await session.delete(runner)
    await session.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/docker_workers.py apps/api/tests/test_docker_workers.py
git commit -m "feat(runners): spawn/remove Docker runner containers"
```

---

## Task 5: Autoscale loop + reconcile

**Files:**
- Modify: `apps/api/app/services/docker_workers.py`, `apps/api/app/config.py`, `apps/api/app/main.py`, `apps/api/app/worker_main.py`
- Test: `apps/api/tests/test_docker_workers.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_docker_workers.py`:

```python
async def test_build_pool_states_counts_queue_and_runners(session):
    from app.services import docker_workers
    from app.models import RunQueueEntry
    pool = RunnerPool(name="dw", provider="agent",
                      provider_config={"docker_autoscale": {"enabled": True,
                          "min_runners": 0, "max_runners": 3, "idle_seconds": 300}})
    session.add(pool); await session.commit()
    session.add(Runner(pool_id=pool.id, name="r", status="online",
                       current_runs=0, capabilities={"docker_managed": True}))
    session.add(RunQueueEntry(run_id="x", workflow_id="w",
                              runner_pool_id=pool.id, status="queued"))
    await session.commit()
    states = await docker_workers._build_pool_states(session)
    assert len(states) == 1
    assert states[0].queued == 1
    assert states[0].enabled is True
    assert len(states[0].runners) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -k build_pool_states -q`
Expected: FAIL (`_build_pool_states` undefined).

- [ ] **Step 3: Implement snapshot + loop**

Add to `apps/api/app/config.py` (near the other runner settings, after `runner_ghost_ttl_hours`):

```python
    # Docker-workers autoscaler tick (seconds). The loop runs where a Docker
    # daemon is reachable (worker + inline API) and only acts on agent pools
    # with docker_autoscale.enabled or existing docker-managed runners.
    docker_autoscale_tick_seconds: float = 30.0
```

Append to `apps/api/app/services/docker_workers.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RunQueueEntry


def _idle_seconds(runner: Runner, now: datetime) -> float:
    last = runner.last_seen_at
    if last is None:
        return 0.0
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return max(0.0, (now - last).total_seconds())


async def _build_pool_states(session) -> list[PoolState]:
    now = datetime.now(UTC)
    pools = (await session.scalars(
        select(RunnerPool).where(RunnerPool.provider == "agent")
    )).all()
    queued_rows = (await session.execute(
        select(RunQueueEntry.runner_pool_id, func.count())
        .where(RunQueueEntry.status == "queued")
        .group_by(RunQueueEntry.runner_pool_id)
    )).all()
    queued_by_pool = {pid: n for pid, n in queued_rows}
    runners = (await session.scalars(select(Runner))).all()
    by_pool: dict[str, list[Runner]] = {}
    for r in runners:
        if (r.capabilities or {}).get("docker_managed"):
            by_pool.setdefault(r.pool_id, []).append(r)

    states: list[PoolState] = []
    for pool in pools:
        cfg = pool.provider_config or {}
        auto = cfg.get("docker_autoscale") or {}
        managed = by_pool.get(pool.id, [])
        # Only surface pools that either autoscale or already have managed runners.
        if not auto.get("enabled") and not managed:
            continue
        states.append(PoolState(
            pool_id=pool.id,
            queued=int(queued_by_pool.get(pool.id, 0)),
            runners=[
                RunnerState(
                    runner_id=r.id,
                    current_runs=r.current_runs,
                    idle_seconds=_idle_seconds(r, now),
                    draining=r.status in ("draining", "offline", "online"),
                    online=r.status in ("online", "busy"),
                    max_concurrent_runs=r.max_concurrent_runs or 2,
                )
                for r in managed
            ],
            enabled=bool(auto.get("enabled")),
            min_runners=int(auto.get("min_runners", 0)),
            max_runners=int(auto.get("max_runners", 4)),
            idle_seconds=float(auto.get("idle_seconds", 300)),
        ))
    return states


async def _apply_action(action: Action) -> None:
    async with SessionLocal() as session:
        if isinstance(action, SpawnRunner):
            pool = await session.get(RunnerPool, action.pool_id)
            if pool is None:
                return
            try:
                await spawn_docker_runner(session, pool)
            except DaemonUnreachable as exc:
                logger.warning("autoscale spawn skipped for %s: %s", action.pool_id, exc)
        elif isinstance(action, RemoveRunner):
            runner = await session.get(Runner, action.runner_id)
            if runner is not None:
                cfg_pool = await session.get(RunnerPool, runner.pool_id)
                client = None
                try:
                    client = _docker_client((cfg_pool.provider_config or {}) if cfg_pool else {})
                except DaemonUnreachable:
                    pass
                await remove_docker_runner(session, runner, client=client, force=False)


async def docker_workers_autoscale_loop() -> None:
    """Tick: snapshot → plan_scaling → apply. Never lets one tick kill the loop."""
    while True:
        try:
            async with SessionLocal() as session:
                states = await _build_pool_states(session)
            for action in plan_scaling(states):
                try:
                    await _apply_action(action)
                except RunnerBusy:
                    pass
                except Exception:  # noqa: BLE001
                    logger.exception("autoscale action failed: %s", action)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("docker autoscale loop tick failed")
        await asyncio.sleep(settings.docker_autoscale_tick_seconds)
```

Wire the loop into `apps/api/app/main.py` lifespan (alongside the other
`_as_system` loops; only where a daemon can be reached — inline dispatch):

```python
    docker_autoscale = (
        asyncio.create_task(_as_system(docker_workers_autoscale_loop)())
        if dispatch_inline
        else None
    )
```

Add the import at the top of `main.py`:

```python
from app.services.docker_workers import docker_workers_autoscale_loop
```

Add `docker_autoscale` to the shutdown cancel tuple in `main.py` lifespan.

Wire into `apps/api/app/worker_main.py` `tasks` list (the worker always has a
daemon when sandbox is configured):

```python
        asyncio.create_task(_as_system(docker_workers_autoscale_loop)()),
```

with `from app.services.docker_workers import docker_workers_autoscale_loop`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest apps/api/tests/test_docker_workers.py -q`
Expected: PASS.

- [ ] **Step 5: Verify app still imports**

Run: `uv run python -c "import app.main, app.worker_main"`
Expected: no error.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/docker_workers.py apps/api/app/config.py apps/api/app/main.py apps/api/app/worker_main.py apps/api/tests/test_docker_workers.py
git commit -m "feat(runners): Docker-worker autoscale loop"
```

---

## Task 6: API endpoints + config validation

**Files:**
- Modify: `apps/api/app/routers/runner_pools.py`, `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_docker_workers_api.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_docker_workers_api.py`:

```python
"""Endpoint + validation tests for Docker-worker provisioning.

Uses the app client fixture (conftest). Patches spawn to avoid real Docker.
"""

import pytest


async def _make_agent_pool(client):
    resp = await client.post("/runner-pools", json={"name": "dw", "provider": "agent"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def test_add_docker_runner_requires_agent_pool(client, monkeypatch):
    from app.services import docker_workers

    async def _fake_spawn(session, pool, **kw):
        from app.models import Runner
        r = Runner(pool_id=pool.id, name="docker-x", status="offline",
                   capabilities={"docker_managed": True})
        session.add(r); await session.commit(); await session.refresh(r)
        return r

    monkeypatch.setattr(docker_workers, "spawn_docker_runner", _fake_spawn)
    pool_id = await _make_agent_pool(client)
    resp = await client.post(f"/runner-pools/{pool_id}/docker-runners", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["capabilities"]["docker_managed"] is True


async def test_add_docker_runner_daemon_unreachable_502(client, monkeypatch):
    from app.services import docker_workers

    async def _boom(session, pool, **kw):
        raise docker_workers.DaemonUnreachable("no daemon")

    monkeypatch.setattr(docker_workers, "spawn_docker_runner", _boom)
    pool_id = await _make_agent_pool(client)
    resp = await client.post(f"/runner-pools/{pool_id}/docker-runners", json={})
    assert resp.status_code == 502


async def test_autoscale_config_validation_bounds(client):
    pool_id = await _make_agent_pool(client)
    bad = {"provider_config": {"docker_autoscale": {"min_runners": 5, "max_runners": 2}}}
    resp = await client.patch(f"/runner-pools/{pool_id}", json=bad)
    assert resp.status_code == 422

    bad2 = {"provider_config": {"docker_host": "http://evil"}}
    resp = await client.patch(f"/runner-pools/{pool_id}", json=bad2)
    assert resp.status_code == 422

    ok = {"provider_config": {"docker_autoscale": {"min_runners": 0, "max_runners": 4,
          "idle_seconds": 300}, "docker_runner": {"cpu": 1.0, "memory_mb": 1024}}}
    resp = await client.patch(f"/runner-pools/{pool_id}", json=ok)
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/api/tests/test_docker_workers_api.py -q`
Expected: FAIL (endpoint 404 / no validation).

- [ ] **Step 3: Implement validation + endpoints**

Add a validator function in `apps/api/app/routers/runner_pools.py`:

```python
_DOCKER_HOST_SCHEMES = ("tcp://", "ssh://", "unix://", "npipe://")


def _validate_docker_pool_config(cfg: dict) -> None:
    from app.config import settings as _s
    host = cfg.get("docker_host") or ""
    if host and not host.startswith(_DOCKER_HOST_SCHEMES):
        raise HTTPException(422, f"docker_host must start with one of {_DOCKER_HOST_SCHEMES}")
    rc = cfg.get("docker_runner") or {}
    if rc:
        cpu = float(rc.get("cpu", 1.0))
        if not (0 < cpu <= _s.sandbox_max_cpu):
            raise HTTPException(422, f"cpu must be in (0, {_s.sandbox_max_cpu}]")
        mem = int(rc.get("memory_mb", 1024))
        if not (128 <= mem <= _s.sandbox_max_memory_mb):
            raise HTTPException(422, f"memory_mb must be in [128, {_s.sandbox_max_memory_mb}]")
    auto = cfg.get("docker_autoscale") or {}
    if auto:
        mn = int(auto.get("min_runners", 0))
        mx = int(auto.get("max_runners", 4))
        if not (0 <= mn <= mx <= 32):
            raise HTTPException(422, "require 0 <= min_runners <= max_runners <= 32")
        if int(auto.get("idle_seconds", 300)) < 30:
            raise HTTPException(422, "idle_seconds must be >= 30")
```

In the existing `PATCH /{pool_id}` handler (`update_runner_pool`), after
loading the pool and before applying `provider_config`, call the validator
when `provider_config` is present:

```python
    if body.provider_config is not None:
        _validate_docker_pool_config(body.provider_config)
```

Add the two endpoints (after `create_registration_token`):

```python
@router.post(
    "/{pool_id}/docker-runners",
    response_model=RunnerInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def add_docker_runner(
    pool_id: str,
    body: dict | None = None,
    session: AsyncSession = Depends(get_session),
) -> RunnerInfo:
    from app.services import docker_workers

    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    if pool.provider != "agent":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Docker runners attach to agent pools")
    try:
        runner = await docker_workers.spawn_docker_runner(
            session, pool, name=(body or {}).get("name")
        )
    except docker_workers.DaemonUnreachable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return _runner_info(runner)


@router.delete(
    "/{pool_id}/docker-runners/{runner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def remove_docker_runner_ep(
    pool_id: str,
    runner_id: str,
    force: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> None:
    from app.services import docker_workers

    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    pool = await session.get(RunnerPool, pool_id)
    client = None
    try:
        client = docker_workers._docker_client((pool.provider_config or {}) if pool else {})
    except docker_workers.DaemonUnreachable:
        pass
    try:
        await docker_workers.remove_docker_runner(session, runner, client=client, force=force)
    except docker_workers.RunnerBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest apps/api/tests/test_docker_workers_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/runner_pools.py apps/api/app/schemas.py apps/api/tests/test_docker_workers_api.py
git commit -m "feat(runners): Docker-runner endpoints + pool config validation"
```

---

## Task 7: Agent sandbox execution path

**Files:**
- Create: `packages/runner/nodyra_runner_agent/sandbox_exec.py`
- Modify: `packages/runner/nodyra_runner_agent/agent.py`
- Modify: `apps/api/app/services/providers/agent.py` (dispatch guard + `sandbox_required` flag)
- Test: `packages/runner/tests/test_sandbox_exec.py`

- [ ] **Step 1: Write the failing test**

Create `packages/runner/tests/test_sandbox_exec.py`:

```python
"""Agent-side hardened sandbox execution."""

import pytest

from nodyra_runner_agent import sandbox_exec


def test_hardening_floor_present():
    kw = sandbox_exec.sandbox_run_kwargs(cpu=1.0, memory_mb=512, pids=128)
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["read_only"] is True
    assert kw["network_mode"] == "none"
    assert kw["mem_limit"] == "512m"
    assert kw["nano_cpus"] == 1_000_000_000
    assert kw["pids_limit"] == 128
    assert "/tmp" in kw["tmpfs"]


async def test_run_sandboxed_refuses_without_daemon(monkeypatch):
    monkeypatch.setattr(sandbox_exec, "_client", lambda: (_ for _ in ()).throw(
        RuntimeError("no daemon")))
    events = []
    status = await sandbox_exec.run_workflow_sandboxed(
        run_id="r", graph={}, cache=None, targets=None, workflow_modules=[],
        on_event=lambda e: events.append(e) or _async_none(),
        env_payload={},
    )
    assert status == "error"
    assert any(e.get("type") == "run_error" for e in events)


def _async_none():
    import asyncio
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest packages/runner/tests/test_sandbox_exec.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the sandbox path**

Create `packages/runner/nodyra_runner_agent/sandbox_exec.py`:

```python
"""Run a workflow in a disposable hardened container from the agent.

The agent cannot import ``app.*``; this is a deliberately small copy of the
platform's container hardening floor (see
apps/api/app/services/container_runtime.py::hardening_kwargs) plus the no-TTY
demuxer. Keep the two in sync when the security floor changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("nodyra_runner")

EventCallback = Callable[[dict], Awaitable[None]]


def sandbox_run_kwargs(*, cpu: float, memory_mb: int, pids: int) -> dict[str, Any]:
    return {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=256m"},
        "mem_limit": f"{int(memory_mb)}m",
        "nano_cpus": int(float(cpu) * 1_000_000_000),
        "pids_limit": int(pids),
        "network_mode": "none",
        "init": True,
        "environment": {"HOME": "/tmp"},
    }


def _client():
    import docker  # noqa: PLC0415
    return docker.from_env()


class _Demuxer:
    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> bytes:
        self._buf += chunk
        out = b""
        while len(self._buf) >= 8:
            size = int.from_bytes(self._buf[4:8], "big")
            if len(self._buf) < 8 + size:
                break
            out += self._buf[8:8 + size]
            self._buf = self._buf[8 + size:]
        return out


async def run_workflow_sandboxed(
    *,
    run_id: str,
    graph: dict,
    cache: dict | None,
    targets: list[str] | None,
    workflow_modules: list[dict],
    on_event: EventCallback,
    env_payload: dict,
    image_tag: str = "python:3.12-slim",
) -> str:
    """Execute a run in a hardened disposable container. Returns status."""
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        await on_event({"type": "run_error",
                        "error": f"sandbox requested but no Docker daemon: {exc}"})
        return "error"
    overrides = (env_payload or {}).get("sandbox") or {}
    kwargs = sandbox_run_kwargs(
        cpu=overrides.get("cpu", 1.0),
        memory_mb=overrides.get("memory_mb", 1024),
        pids=overrides.get("pids", 256),
    )
    # (spawn container running `python -m nodyra_runtime`, drive the same
    # newline-delimited JSON protocol as process_pool.run_workflow_subprocess,
    # forward events to on_event, return the final status. Mirror the recv/
    # demux loop from apps/api/app/services/providers/docker.py.)
    ...
    return "error"  # replaced by the real loop below
```

Then port the recv/demux/protocol loop from
`apps/api/app/services/providers/docker.py::assign_docker_run` (lines ~95-185)
into the body marked `...`, sending the `{"type": "run", ...}` message on the
runtime's `ready` event and returning the `result` status. Remove the trailing
placeholder return once the loop is in place.

In `packages/runner/nodyra_runner_agent/agent.py::_handle_run`, select the
sandbox path when the assignment requests it:

```python
        if msg.get("sandbox_required"):
            from nodyra_runner_agent.sandbox_exec import run_workflow_sandboxed
            status = await run_workflow_sandboxed(
                run_id=run_id, graph=msg.get("graph", {}),
                cache=msg.get("cache"), targets=msg.get("targets"),
                workflow_modules=msg.get("workflow_modules", []),
                on_event=self._emit, env_payload=msg.get("env", {}),
            )
        else:
            # existing subprocess path
            ...
```

(Adapt to the actual `_handle_run` structure — locate where
`run_workflow_subprocess` is currently called and branch before it, reusing the
same `on_event`/status handling.)

In `apps/api/app/services/providers/agent.py::assign_agent_run`, set
`sandbox_required` in the `run_assigned` payload when the run's resolved
execution mode is sandboxed AND the target runner advertises
`capabilities.sandbox`; and refuse to assign a sandbox-required run to a
non-sandbox runner (defence-in-depth; label routing normally prevents this):

```python
    runner_caps = runner.capabilities or {}
    if sandbox_required and not runner_caps.get("sandbox"):
        raise RuntimeError("sandbox-required run cannot dispatch to a non-sandbox runner")
    payload["sandbox_required"] = bool(sandbox_required)
```

(`sandbox_required` is derived from the run's execution mode — thread it in
from the caller the same way `env_payload` is passed.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest packages/runner/tests/test_sandbox_exec.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/runner/nodyra_runner_agent/sandbox_exec.py packages/runner/nodyra_runner_agent/agent.py apps/api/app/services/providers/agent.py packages/runner/tests/test_sandbox_exec.py
git commit -m "feat(runner): agent-side hardened sandbox execution path"
```

---

## Task 8: Frontend — API client + types

**Files:**
- Modify: `apps/web/src/api.ts`, `apps/web/src/types.ts`
- Test: (covered by Task 9 component test)

- [ ] **Step 1: Add types**

In `apps/web/src/types.ts`, extend the runner-pool config shape (find where
`RunnerPoolInfo` / provider config types live) with an optional docker block:

```typescript
export interface DockerWorkerConfig {
  docker_host?: string;
  docker_network?: string;
  docker_api_url?: string;
  docker_runner?: {
    cpu?: number; memory_mb?: number; pids?: number;
    max_concurrent_runs?: number; sandbox?: boolean;
  };
  docker_autoscale?: {
    enabled?: boolean; min_runners?: number; max_runners?: number;
    idle_seconds?: number;
  };
}
```

- [ ] **Step 2: Add API methods**

In `apps/web/src/api.ts`, inside `runnerPoolsApi` (after the existing runner
methods), add:

```typescript
  addDockerRunner: (poolId: string, name?: string) =>
    request<RunnerInfo>(`/runner-pools/${poolId}/docker-runners`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  removeDockerRunner: (poolId: string, runnerId: string, force = false) =>
    request<void>(
      `/runner-pools/${poolId}/docker-runners/${runnerId}?force=${force}`,
      { method: "DELETE" }
    ),
```

- [ ] **Step 3: Typecheck**

Run: `cd apps/web && npm run typecheck`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/types.ts
git commit -m "feat(web): Docker-worker API client + types"
```

---

## Task 9: Frontend — UI card + button

**Files:**
- Modify: `apps/web/src/RunnerPoolsPage.tsx`
- Test: `apps/web/src/RunnerPoolsPage.dockerWorkers.test.tsx`

- [ ] **Step 1: Write the failing component test**

Create `apps/web/src/RunnerPoolsPage.dockerWorkers.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DockerWorkerCard } from "./RunnerPoolsPage";

describe("DockerWorkerCard", () => {
  it("shows the sandbox checkbox with a trust warning", () => {
    render(<DockerWorkerCard poolId="p1" config={{}} canWrite onSave={vi.fn()} onAddRunner={vi.fn()} />);
    expect(screen.getByLabelText(/sandboxed execution support/i)).toBeInTheDocument();
    expect(screen.getByText(/root-equivalent/i)).toBeInTheDocument();
  });

  it("calls onAddRunner when the button is clicked", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    render(<DockerWorkerCard poolId="p1" config={{}} canWrite onSave={vi.fn()} onAddRunner={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: /add docker runner/i }));
    await waitFor(() => expect(onAdd).toHaveBeenCalled());
  });

  it("hides mutating controls when canWrite is false", () => {
    render(<DockerWorkerCard poolId="p1" config={{}} canWrite={false} onSave={vi.fn()} onAddRunner={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /add docker runner/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run RunnerPoolsPage.dockerWorkers -t "DockerWorkerCard"`
Expected: FAIL (`DockerWorkerCard` not exported).

- [ ] **Step 3: Implement the card**

In `apps/web/src/RunnerPoolsPage.tsx`, add and **export** a `DockerWorkerCard`
component (place near `PoolCard`). It renders:
- an **Add Docker Runner** button (calls `onAddRunner`, shows a spinner while
  pending, toast on error) — only when `canWrite`;
- an autoscale section: enable toggle, min/max runners (number inputs), CPU,
  memory (MB), idle-seconds; a daemon choice (Local / Remote with a
  `docker_host` text input shown when Remote); and a **Sandboxed execution
  support** checkbox with inline helper text containing the word
  "root-equivalent" describing the DooD trust model;
- a **Save** button that calls `onSave(nextConfig)` (wired by the parent to
  `runnerPoolsApi.update(poolId, { provider_config })`).

Signature:

```tsx
export function DockerWorkerCard({
  poolId, config, canWrite, onSave, onAddRunner,
}: {
  poolId: string;
  config: DockerWorkerConfig;
  canWrite: boolean;
  onSave: (next: DockerWorkerConfig) => void | Promise<void>;
  onAddRunner: () => void | Promise<void>;
}) { /* ... */ }
```

Render `DockerWorkerCard` inside the agent-pool branch of `PoolCard`, wiring
`onAddRunner` to `runnerPoolsApi.addDockerRunner(poolId)` (+ query invalidate)
and `onSave` to `runnerPoolsApi.update`. Add a `docker` badge on runner rows
whose `capabilities.docker_managed` is true, with a remove action calling
`runnerPoolsApi.removeDockerRunner`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npx vitest run RunnerPoolsPage.dockerWorkers`
Expected: PASS (3 tests).

- [ ] **Step 5: Typecheck + commit**

```bash
cd apps/web && npm run typecheck
git add apps/web/src/RunnerPoolsPage.tsx apps/web/src/RunnerPoolsPage.dockerWorkers.test.tsx
git commit -m "feat(web): one-click Docker worker card + autoscale + sandbox checkbox"
```

---

## Task 10: Docs + full verification

**Files:**
- Modify: `docs/deployment/workers.md`
- Modify: `.env.example` (document `DOCKER_AUTOSCALE_TICK_SECONDS`)

- [ ] **Step 1: Document the feature**

Add a "One-click Docker runners" subsection to `docs/deployment/workers.md`
under §2 (agent runners) covering: the Add Docker Runner button, autoscale
bounds (min/max/idle), local vs remote daemon (`docker_host`), and the sandbox
checkbox with its DooD trust note (socket into the runner is root-equivalent
on the daemon host; runs execute in hardened per-run containers).

- [ ] **Step 2: Document the setting**

Add to `.env.example`:

```
# Docker-workers autoscaler tick (seconds). The loop adds/removes Docker
# runner containers to match queue depth within each pool's min/max.
# DOCKER_AUTOSCALE_TICK_SECONDS=30
```

- [ ] **Step 3: Run the full affected suites**

Run:
```bash
uv run pytest apps/api/tests/test_docker_workers.py apps/api/tests/test_docker_workers_api.py apps/api/tests/test_runner_pools.py apps/api/tests/test_runner_token_ttl.py packages/runner/tests/test_sandbox_exec.py -q
uv run ruff check .
cd apps/web && npm run typecheck && npx vitest run RunnerPoolsPage
```
Expected: all PASS, ruff clean, typecheck clean.

- [ ] **Step 4: Commit**

```bash
git add docs/deployment/workers.md .env.example
git commit -m "docs(runners): document one-click Docker workers + autoscaling"
```

---

## Self-Review Notes

- **Spec coverage:** Part 1 preset (Task 9 card renders a create-pool path via
  existing `POST /runner-pools`); Part 2 spawn/autoscale (Tasks 2-6); daemon
  choice local/remote (Tasks 4/6 `docker_host`); sandbox checkbox (Tasks 4/7/9);
  resource limits (Tasks 4/6 validation + spawn kwargs); no migration (JSON
  columns only). All covered.
- **Type consistency:** `plan_scaling`, `PoolState`, `RunnerState`,
  `SpawnRunner`, `RemoveRunner`, `spawn_docker_runner`, `remove_docker_runner`,
  `ensure_agent_image`, `agent_image_tag`, `DaemonUnreachable`, `RunnerBusy`,
  `docker_workers_autoscale_loop`, `DockerWorkerConfig`, `DockerWorkerCard`
  names are consistent across tasks.
- **Trust model:** documented in spec + card copy + workers.md; runner
  container is platform-trust (like the compose worker); run code executes in
  hardened siblings.
- **Degradation:** no daemon → 502 on button, autoscale loop logs one line and
  idles; feature is fully behind explicit config.
```
