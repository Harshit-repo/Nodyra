# Phase 1 — Stabilize: Audit Fix Landing, Open Bugs, Soak, Hygiene

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the 2026-07-02 audit fixes as clean commits, close the four open bugs from the audit (BUG-6 event ordering, BUG-7 lease-requeue locking, BUG-8 httpx SSRF connect-time gap, BUG-10 cancel-test flake), add checkpoint debouncing, run the P0 soak test, protect `main`, and sweep repo hygiene.

**Architecture:** All changes are surgical fixes inside existing modules (`app/services/events.py`, `app/services/queue.py`, `app/services/runner.py`) plus one new security module in `packages/nodes`. No schema changes, no new dependencies.

**Tech Stack:** FastAPI + SQLAlchemy async + Redis (backend), pytest, httpx.

**Parent plan:** `docs/superpowers/plans/2026-07-02-nodyra-master-roadmap.md`

## Global Constraints

- This phase runs **before** the Nodyra rename (Phase 2): keep all existing `noodle` names (`noodle_nodes`, `NOODLE_ALLOW_PRIVATE_EGRESS`, `noodle:` Redis keys) exactly as they are.
- Run backend tests with: `uv run pytest <paths>` from repo root (fallback if uv is broken: `.venv\Scripts\python.exe -m pytest <paths>`).
- Run web tests with: `npm test -- --run` and `npm run typecheck` in `apps/web`.
- `ruff check <files>` must be clean on every touched Python file before each commit.
- The working tree contains **unrelated in-flight work** (web editor changes, `environment_builds`, migrations 0078/0079, `workflow_events`). Never `git add -A` or `git add .` — stage only the files each task names.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Commit the audit fixes already in the working tree

The five audit fixes (report: `FABLE5_NOODLE_FULL_AUDIT_OPTIMIZATION_AND_PRODUCT_IDEAS.md` §Fixes Made) exist as uncommitted edits. Land them as two commits.

**Files:**
- Commit (fixes): `apps/api/app/routers/ops.py`, `apps/api/app/services/queue.py`, `apps/api/app/services/events.py`, `apps/api/app/services/runner.py`, `apps/api/tests/conftest.py`, `apps/api/tests/test_ops.py`, `apps/api/tests/test_run_queue.py`, `apps/api/tests/test_runner_live_artifact_caps.py`
- Commit (reports): `FABLE5_NOODLE_FULL_AUDIT_OPTIMIZATION_AND_PRODUCT_IDEAS.md`, `FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md` → moved to `docs/audits/`

- [ ] **Step 1: Review the staged surface.** Run `git diff --stat apps/api/app/routers/ops.py apps/api/app/services/queue.py apps/api/app/services/events.py apps/api/app/services/runner.py apps/api/tests/conftest.py apps/api/tests/test_ops.py apps/api/tests/test_run_queue.py`. Confirm the diffs match the audit report's "Files Changed" descriptions (require_role dep, lease exclude_local, publish-task refs, live artifact caps, fake isolator shutdown). If a file contains changes clearly unrelated to those descriptions, STOP and report back instead of committing.

- [ ] **Step 2: Verify green before committing.**

Run: `uv run pytest apps/api/tests/test_ops.py apps/api/tests/test_run_queue.py apps/api/tests/test_runner_live_artifact_caps.py apps/api/tests/test_tracing.py apps/api/tests/test_workflow_events.py`
Expected: all pass (≈50+ tests, 0 failures).

- [ ] **Step 3: Commit the fixes** (single commit; the fixes were verified together):

```bash
git add apps/api/app/routers/ops.py apps/api/app/services/queue.py apps/api/app/services/events.py apps/api/app/services/runner.py apps/api/tests/conftest.py apps/api/tests/test_ops.py apps/api/tests/test_run_queue.py apps/api/tests/test_runner_live_artifact_caps.py
git commit -m "fix: land 2026-07-02 audit fixes (ops auth, queue HOL blocking, event task refs, live artifact caps, test isolator)

- ops.py: require_role('viewer') on 4 monitoring GETs (was current_user -> 401 on no-auth instances)
- queue.py: lease(exclude_local=) so a saturated local pool cannot starve remote pools
- events.py: strong refs for fire-and-forget publish tasks (GC could drop events)
- runner.py: live-settings artifact caps actually reach the artifact store
- conftest.py: fake process isolator gains no-op shutdown()

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Move the audit reports into docs and commit:**

```bash
mkdir -p docs/audits
git mv FABLE5_NOODLE_FULL_AUDIT_OPTIMIZATION_AND_PRODUCT_IDEAS.md docs/audits/2026-07-02-fable5-full-audit.md 2>/dev/null || { git add FABLE5_NOODLE_FULL_AUDIT_OPTIMIZATION_AND_PRODUCT_IDEAS.md && git mv FABLE5_NOODLE_FULL_AUDIT_OPTIMIZATION_AND_PRODUCT_IDEAS.md docs/audits/2026-07-02-fable5-full-audit.md; }
git mv FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md docs/audits/2026-07-02-fable5-exploration.md 2>/dev/null || { git add FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md && git mv FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md docs/audits/2026-07-02-fable5-exploration.md; }
git commit -m "docs: file 2026-07-02 audit reports under docs/audits

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Note: untracked files can't be `git mv`'d directly — the fallback `git add` first handles that.)

---

### Task 2: Repo hygiene sweep

**Files:**
- Delete: all stray `*.png` at repo root (`audit-*.png`, `final-*.png`, `hero-*.png`, `nodyra-full-*.png`, `runners-*.png`, `section-*.png`, `env-new.png`, and similar), directories `.tmp/`, `.fable5-tmp/`
- Modify: `.gitignore`

- [ ] **Step 1: List what will be removed** (screenshots are session artifacts, reproducible from the app):

```bash
git status --porcelain | grep -E '^\?\?.*\.png$'
```

Confirm every listed PNG is at repo root (no `/` in the path before the filename) — `brand/` assets must NOT appear. If any tracked or non-root PNG appears, exclude it.

- [ ] **Step 2: Remove only the verified artifacts and ignore future ones:**

Do **not** use a broad `rm ./*.png`: it can delete an intentional root asset if one is added later. Delete only the untracked root files listed in Step 1. On PowerShell:

```powershell
$root = (Resolve-Path .).Path
$pngs = git status --porcelain |
  Where-Object { $_ -match '^\?\? [^/\\]+\.png$' } |
  ForEach-Object { $_.Substring(3) }
foreach ($name in $pngs) {
  $target = Join-Path $root $name
  if ((Resolve-Path -LiteralPath $target).Path.StartsWith($root)) {
    Remove-Item -LiteralPath $target
  }
}
foreach ($dir in '.tmp', '.fable5-tmp') {
  $target = Join-Path $root $dir
  if (Test-Path -LiteralPath $target) {
    $resolved = (Resolve-Path -LiteralPath $target).Path
    if ($resolved.StartsWith($root)) {
      Remove-Item -LiteralPath $resolved -Recurse -Force
    }
  }
}
```

Append to `.gitignore`:

```gitignore
# Session artifacts — screenshots and scratch dirs dropped at repo root
/*.png
.tmp/
.fable5-tmp/
```

- [ ] **Step 3: Commit:**

```bash
git add .gitignore
git commit -m "chore: sweep root screenshot artifacts, ignore scratch dirs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: BUG-6 — Guarantee per-topic event publish ordering

**Files:**
- Modify: `apps/api/app/services/events.py` (publish path, ~lines 79–160)
- Test: `apps/api/tests/test_event_publish_ordering.py` (new)

**Interfaces:**
- Consumes: existing `publish(topic_id, event)` / `_async_publish(topic_id, event)` on the broker base class in `events.py` (find the class name with `grep -n "^class" apps/api/app/services/events.py` — it is the base class whose `__init__` sets `self._publish_tasks`).
- Produces: same public API; adds private `self._publish_chains: dict[str, asyncio.Task]` and `_ordered_publish(prev, topic_id, event)`.

Design: today each `publish()` spawns an independent task, so two bursts on one topic can interleave their Redis `rpush` pipelines and corrupt history order. Fix by **chaining**: each new publish task first awaits the previous task for the same topic, guaranteeing FIFO per topic with zero long-lived infrastructure.

- [ ] **Step 1: Write the failing test.** Create `apps/api/tests/test_event_publish_ordering.py`:

```python
"""BUG-6: per-topic Redis publish order must match publish() call order."""
from __future__ import annotations

import asyncio

import pytest


class _RecordingPipe:
    def __init__(self, store: list[str], delay: float) -> None:
        self._store = store
        self._delay = delay
        self._pending: list[str] = []

    def rpush(self, key: str, payload: str) -> None:
        self._pending.append(payload)

    def expire(self, key: str, ttl: int) -> None:
        pass

    def publish(self, channel: str, payload: str) -> None:
        pass

    async def execute(self) -> None:
        await asyncio.sleep(self._delay)
        self._store.extend(self._pending)


class _SlowFirstRedis:
    """First pipeline sleeps 50ms; later ones are instant. Without ordering,
    the second publish lands in history before the first."""

    def __init__(self) -> None:
        self.history: list[str] = []
        self._calls = 0

    def pipeline(self) -> _RecordingPipe:
        self._calls += 1
        return _RecordingPipe(self.history, 0.05 if self._calls == 1 else 0.0)


@pytest.mark.asyncio
async def test_publish_order_preserved_per_topic() -> None:
    from app.services import events as events_mod

    broker = events_mod.RunEventBroker()  # adjust to the actual concrete class
    broker._mode = "redis"
    broker._redis = _SlowFirstRedis()

    broker.publish("run-1", {"type": "node_chunk", "seq": 1})
    broker.publish("run-1", {"type": "node_finished", "seq": 2})
    await asyncio.sleep(0.2)  # let both chained tasks drain

    history = [__import__("json").loads(p) for p in broker._redis.history]
    assert [e["seq"] for e in history] == [1, 2]
```

Adjust the concrete broker class name after `grep -n "^class" apps/api/app/services/events.py` (the run broker is constructed with `channel_prefix="noodle:run:"`). If the constructor requires arguments, mirror how `events.py` itself instantiates its module-level singletons.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest apps/api/tests/test_event_publish_ordering.py -v`
Expected: FAIL — `[2, 1] != [1, 2]` (the slow first pipeline finishes last).

- [ ] **Step 3: Implement chaining in `events.py`.** In the broker base `__init__`, next to `self._publish_tasks`, add:

```python
        # BUG-6: per-topic FIFO. Each publish task awaits its predecessor for
        # the same topic before touching Redis, so pipelined rpush order can
        # never diverge from publish() call order under bursts.
        self._publish_chains: dict[str, asyncio.Task] = {}
```

In `publish()`, replace:

```python
        task = loop.create_task(self._async_publish(topic_id, event))
```

with:

```python
        prev = self._publish_chains.get(topic_id)
        task = loop.create_task(self._ordered_publish(prev, topic_id, event))
        self._publish_chains[topic_id] = task
        task.add_done_callback(
            lambda t, tid=topic_id: self._publish_chains.pop(tid, None)
            if self._publish_chains.get(tid) is t
            else None
        )
```

(keep the existing `self._publish_tasks.add(task)` / `add_done_callback(self._publish_tasks.discard)` lines). Add the method:

```python
    async def _ordered_publish(
        self, prev: asyncio.Task | None, topic_id: str, event: Event
    ) -> None:
        if prev is not None:
            try:
                await prev
            except (Exception, asyncio.CancelledError):
                # Predecessor already logged its own failure; we only need
                # its completion for ordering.
                pass
        await self._async_publish(topic_id, event)
```

- [ ] **Step 4: Run the new test and the existing broker suites**

Run: `uv run pytest apps/api/tests/test_event_publish_ordering.py apps/api/tests/test_workflow_events.py -v` and `uv run pytest apps/api/tests -k "event" -q`
Expected: all PASS.

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check apps/api/app/services/events.py apps/api/tests/test_event_publish_ordering.py
git add apps/api/app/services/events.py apps/api/tests/test_event_publish_ordering.py
git commit -m "fix(events): chain per-topic publish tasks so Redis history order matches publish order (BUG-6)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: BUG-7 — Lock expired-lease requeue across replicas

**Files:**
- Modify: `apps/api/app/services/queue.py` (`requeue_expired_leases`, ~line 561)
- Test: `apps/api/tests/test_run_queue.py` (append one test)

- [ ] **Step 1: Extract the statement and write the failing test.** Append to `apps/api/tests/test_run_queue.py`:

```python
def test_requeue_expired_leases_locks_rows_on_postgres() -> None:
    """BUG-7: two dispatch replicas must not both requeue the same expired
    lease (double attempts_log append). The SELECT must take
    FOR UPDATE SKIP LOCKED on Postgres; SQLite ignores the clause."""
    from datetime import datetime, timezone

    from sqlalchemy.dialects import postgresql

    from app.services.queue import _expired_lease_stmt

    stmt = _expired_lease_stmt(datetime.now(timezone.utc))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
```

- [ ] **Step 2: Run it to verify failure**

Run: `uv run pytest apps/api/tests/test_run_queue.py::test_requeue_expired_leases_locks_rows_on_postgres -v`
Expected: FAIL — `ImportError: cannot import name '_expired_lease_stmt'`.

- [ ] **Step 3: Implement.** In `queue.py`, directly above `requeue_expired_leases`, add:

```python
def _expired_lease_stmt(moment: datetime):
    """Expired-lease scan. FOR UPDATE SKIP LOCKED so concurrent dispatch
    replicas each requeue a disjoint set (SQLite ignores the locking clause,
    which is fine — it has no row locks and tests run single-process)."""
    return (
        select(RunQueueEntry)
        .where(
            RunQueueEntry.status == "leased",
            RunQueueEntry.lease_expires_at.is_not(None),
            RunQueueEntry.lease_expires_at <= moment,
        )
        .with_for_update(skip_locked=True)
    )
```

and in `requeue_expired_leases` replace the inline `select(RunQueueEntry).where(...)` with `_expired_lease_stmt(moment)`.

- [ ] **Step 4: Run the queue suite**

Run: `uv run pytest apps/api/tests/test_run_queue.py -v`
Expected: all PASS (30 tests incl. the new one).

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check apps/api/app/services/queue.py apps/api/tests/test_run_queue.py
git add apps/api/app/services/queue.py apps/api/tests/test_run_queue.py
git commit -m "fix(queue): FOR UPDATE SKIP LOCKED on expired-lease requeue scan (BUG-7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: BUG-8 — Close the httpx DNS-rebinding window (pinned-IP requests)

**Files:**
- Create: `packages/nodes/noodle_nodes/httpx_security.py`
- Modify: `apps/api/app/services/mcp_client.py` (both call sites, ~lines 93–125)
- Test: `packages/nodes/tests/test_httpx_security.py` (new)

**Interfaces:**
- Consumes: `assert_public_http_url(url, *, context)` from `noodle_nodes/http_security.py:106`.
- Produces: `resolve_pinned(url, *, context) -> PinnedURL` where `PinnedURL(url: str, host: str)`; `pinned_request_kwargs(pinned) -> dict` returning `{"headers": {"Host": host}, "extensions": {"sni_hostname": host}}`.

Design: `assert_public_http_url` re-resolves DNS at pre-flight, but httpx resolves *again* at connect — a rebinding attacker can flip the record in between. Fix: resolve once, validate **every** returned address, rewrite the URL host to the validated IP literal, and carry the original hostname via `Host` header + httpcore's `sni_hostname` extension (TLS verification still checks the certificate against the hostname because httpcore passes `sni_hostname` as `server_hostname`).

- [ ] **Step 1: Write the failing tests.** Create `packages/nodes/tests/test_httpx_security.py`:

```python
"""BUG-8: pin httpx egress to the pre-validated public IP."""
from __future__ import annotations

import socket

import pytest

from noodle_nodes.httpx_security import (
    PrivateAddressError,
    pinned_request_kwargs,
    resolve_pinned,
)


def _fake_getaddrinfo(addrs: list[str]):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addrs]
    return fake


def test_resolve_pinned_rewrites_host_to_validated_ip(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
    pinned = resolve_pinned("https://mcp.example.com/rpc?x=1", context="test")
    assert pinned.url == "https://93.184.216.34:443/rpc?x=1"
    assert pinned.host == "mcp.example.com"


def test_resolve_pinned_rejects_private_resolution(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["10.0.0.5"]))
    with pytest.raises(PrivateAddressError):
        resolve_pinned("https://rebind.example.com/", context="test")


def test_resolve_pinned_rejects_mixed_public_private(monkeypatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34", "169.254.169.254"])
    )
    with pytest.raises(PrivateAddressError):
        resolve_pinned("https://rebind.example.com/", context="test")


def test_ip_literal_url_passes_through(monkeypatch) -> None:
    pinned = resolve_pinned("https://93.184.216.34/x", context="test")
    assert pinned.url == "https://93.184.216.34:443/x"
    assert pinned.host == "93.184.216.34"


def test_private_egress_env_disables_pinning(monkeypatch) -> None:
    monkeypatch.setenv("NOODLE_ALLOW_PRIVATE_EGRESS", "1")
    pinned = resolve_pinned("http://10.0.0.5:8080/local", context="test")
    assert pinned.url == "http://10.0.0.5:8080/local"


def test_pinned_request_kwargs_carries_host_and_sni(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
    pinned = resolve_pinned("https://mcp.example.com/", context="test")
    kwargs = pinned_request_kwargs(pinned)
    assert kwargs["headers"]["Host"] == "mcp.example.com"
    assert kwargs["extensions"]["sni_hostname"] == "mcp.example.com"
```

Note: check how `http_security.py` reads the private-egress env var (`grep -n "ALLOW_PRIVATE_EGRESS" packages/nodes/noodle_nodes/http_security.py`) and mirror the same truthy parsing in the last test if it differs from `"1"`.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest packages/nodes/tests/test_httpx_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noodle_nodes.httpx_security'`.

- [ ] **Step 3: Implement `packages/nodes/noodle_nodes/httpx_security.py`:**

```python
"""SSRF hardening for httpx egress: pin connections to a pre-validated IP.

``assert_public_http_url`` re-resolves DNS at pre-flight, but httpx resolves
again at connect time — a DNS-rebinding attacker can flip the record between
the two lookups. The ``requests`` path is covered by a connect-time hook in
``http_security``; this module is the httpx equivalent: resolve once,
validate every address, then connect to the validated IP literal while
sending SNI/Host for the original hostname (TLS verification still checks
the certificate against the hostname, because httpcore passes
``sni_hostname`` as ``server_hostname``).
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

from noodle_nodes.http_security import assert_public_http_url


class PrivateAddressError(ValueError):
    """Raised when a hostname resolves to a non-public address."""


class PinnedURL(NamedTuple):
    url: str   # scheme://<validated-ip>:<port>/path?query
    host: str  # original hostname, for Host header + SNI


def _private_egress_allowed() -> bool:
    return os.environ.get("NOODLE_ALLOW_PRIVATE_EGRESS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_pinned(url: str, *, context: str = "HTTP request") -> PinnedURL:
    """Validate *url* against the egress policy and return a pinned form.

    The returned URL has its host replaced by a validated public IP literal
    so the subsequent TCP connect cannot be redirected by a second DNS
    answer. Callers must send the original hostname via
    ``pinned_request_kwargs``.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if _private_egress_allowed():
        return PinnedURL(url=url, host=host)

    assert_public_http_url(url, context=context)
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        # Already an IP (validated above); normalise the port in for symmetry.
        ip_str = f"[{host}]" if literal.version == 6 else host
        netloc = f"{ip_str}:{port}"
        return PinnedURL(
            url=urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)),
            host=host,
        )

    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addrs = sorted({info[4][0] for info in infos})
    if not addrs:
        raise PrivateAddressError(f"{context}: could not resolve {host!r}")
    for addr in addrs:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not _is_public(ip):
            raise PrivateAddressError(
                f"{context}: {host!r} resolves to non-public address {addr}"
            )
    pinned_ip = addrs[0]
    ip_str = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    netloc = f"{ip_str}:{port}"
    return PinnedURL(
        url=urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)),
        host=host,
    )


def pinned_request_kwargs(pinned: PinnedURL) -> dict[str, Any]:
    """Per-request kwargs that restore the original hostname for the server."""
    return {
        "headers": {"Host": pinned.host},
        "extensions": {"sni_hostname": pinned.host},
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/nodes/tests/test_httpx_security.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Wire into the MCP client.** Read `apps/api/app/services/mcp_client.py` around lines 90–130. At both call sites, apply this transformation — before:

```python
    from noodle_nodes.http_security import assert_public_http_url
    assert_public_http_url(conn.url, context="MCP connection")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(conn.url, json=payload, headers=headers)
```

after (merge, don't clobber, any headers the call already sends — the pinned `Host` entry must win):

```python
    from noodle_nodes.httpx_security import pinned_request_kwargs, resolve_pinned
    pinned = resolve_pinned(conn.url, context="MCP connection")
    extra = pinned_request_kwargs(pinned)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            pinned.url,
            json=payload,
            headers={**headers, **extra["headers"]},
            extensions=extra["extensions"],
        )
```

If a call site builds the URL as `conn.url + "/some/path"`, pin the **joined** URL, not the base. Keep exceptions flowing: `PrivateAddressError` is a `ValueError` and must surface as the same connection-error handling the pre-flight `UnsafeHttpTargetError` already gets (check the surrounding `except` clauses; add `PrivateAddressError` where `UnsafeHttpTargetError` is caught, or confirm both derive from `ValueError` and are caught together).

- [ ] **Step 6: Run the MCP client suites**

Run: `uv run pytest apps/api/tests -k "mcp" -q`
Expected: all PASS. Some tests may stub `httpx` — if any fail because the mocked URL no longer matches (IP literal instead of hostname), update the test's mock to match on any host or monkeypatch `resolve_pinned` to identity; note which tests you touched.

- [ ] **Step 7: Ruff + commit**

```bash
uv run ruff check packages/nodes/noodle_nodes/httpx_security.py packages/nodes/tests/test_httpx_security.py apps/api/app/services/mcp_client.py
git add packages/nodes/noodle_nodes/httpx_security.py packages/nodes/tests/test_httpx_security.py apps/api/app/services/mcp_client.py
git commit -m "security: pin MCP httpx egress to pre-validated IPs, closing the DNS-rebinding window (BUG-8)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Checkpoint debounce (write-amplification fix)

**Files:**
- Modify: `apps/api/app/services/runner.py` (`_save_checkpoint` call sites; the function itself is at ~line 183)
- Test: `apps/api/tests/test_checkpoint_debounce.py` (new)

**Interfaces:**
- Produces: `class _CheckpointDebouncer` in `runner.py` with `should_persist() -> bool`, `mark_persisted() -> None`, and `has_deferred: bool`.

Design: today every completed node triggers an UPDATE+commit of `Run.checkpoint` — a 200-node graph pays 200 commits. Debounce to at most one checkpoint per 250 ms, but **always** persist the final state after the last node (crash-recovery still loses at most 250 ms of progress).

- [ ] **Step 1: Write the failing test.** Create `apps/api/tests/test_checkpoint_debounce.py`:

```python
"""Checkpoint commits are debounced to one per interval, with a final flush."""
from __future__ import annotations

from app.services.runner import _CheckpointDebouncer


def test_debouncer_allows_first_then_suppresses_within_interval() -> None:
    clock = [100.0]
    d = _CheckpointDebouncer(interval=0.25, clock=lambda: clock[0])
    assert d.should_persist() is True
    d.mark_persisted()
    clock[0] += 0.1
    assert d.should_persist() is False
    assert d.has_deferred is True
    clock[0] += 0.2  # 0.3s since last persist
    assert d.should_persist() is True
    d.mark_persisted()
    assert d.has_deferred is False


def test_debouncer_deferred_flag_drives_final_flush() -> None:
    clock = [0.0]
    d = _CheckpointDebouncer(interval=0.25, clock=lambda: clock[0])
    assert d.should_persist() is True
    d.mark_persisted()
    clock[0] += 0.01
    assert d.should_persist() is False   # this node's checkpoint was skipped
    assert d.has_deferred is True        # so the run-end flush must run
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest apps/api/tests/test_checkpoint_debounce.py -v`
Expected: FAIL — `ImportError: cannot import name '_CheckpointDebouncer'`.

- [ ] **Step 3: Implement in `runner.py`** (place directly above `_save_checkpoint`):

```python
class _CheckpointDebouncer:
    """Coalesce per-node checkpoint commits.

    ``_save_checkpoint`` runs an UPDATE+commit after every completed node;
    a 200-node graph is 200 commits. Cap persistence at one write per
    *interval* seconds. When a write is skipped, ``has_deferred`` stays true
    so the run-completion path can flush the final state unconditionally —
    crash recovery loses at most *interval* seconds of progress.
    """

    def __init__(self, interval: float = 0.25, *, clock=None) -> None:
        import time as _time

        self._interval = interval
        self._clock = clock or _time.monotonic
        self._last_persist: float | None = None
        self.has_deferred = False

    def should_persist(self) -> bool:
        now = self._clock()
        if self._last_persist is None or now - self._last_persist >= self._interval:
            return True
        self.has_deferred = True
        return False

    def mark_persisted(self) -> None:
        self._last_persist = self._clock()
        self.has_deferred = False
```

- [ ] **Step 4: Wire it into the run loop.** Find where `_save_checkpoint` is awaited after node completion (`grep -n "_save_checkpoint" apps/api/app/services/runner.py`). In `_execute_run_impl` (or the hook that calls it), create one `debouncer = _CheckpointDebouncer()` per run before the engine starts, and wrap the per-node call:

```python
                if debouncer.should_persist():
                    await _save_checkpoint(...existing args unchanged...)
                    debouncer.mark_persisted()
```

Then, on the run-completion path (immediately before the run is marked terminal — success, error, or cancelled), flush any skipped state:

```python
            if debouncer.has_deferred:
                await _save_checkpoint(...same args, final state...)
                debouncer.mark_persisted()
```

The existing checkpoint args (`node_outputs`, `completed`, `last_node_id`, `_accumulated`) are all still in scope at the completion path — reuse the same variables the per-node call uses.

- [ ] **Step 5: Run checkpoint/resume suites**

Run: `uv run pytest apps/api/tests/test_checkpoint_debounce.py -v && uv run pytest apps/api/tests -k "checkpoint or resume" -q`
Expected: all PASS. If an existing test asserts a checkpoint exists after each individual node, it will now fail — fix by injecting a zero-interval debouncer is NOT possible from outside, so instead those tests should assert on the final flushed checkpoint. Report any such test changes in the commit body.

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff check apps/api/app/services/runner.py apps/api/tests/test_checkpoint_debounce.py
git add apps/api/app/services/runner.py apps/api/tests/test_checkpoint_debounce.py
git commit -m "perf(runner): debounce checkpoint commits to one per 250ms with final flush

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: BUG-10 — Deflake `test_running_run_can_be_cancelled`

**Files:**
- Modify: the test file containing `test_running_run_can_be_cancelled` (find it: `grep -rn "test_running_run_can_be_cancelled" apps/api/tests`)

- [ ] **Step 1: Read the current test.** It fails order-dependently with `'running' == 'cancelled'` — a fixed-delay assertion racing the cancel.

- [ ] **Step 2: Rewrite with deterministic polling.** Add this helper at the top of the test file (or reuse an existing one if the file already has a poll helper):

```python
async def _poll_until(fetch, predicate, *, timeout: float = 8.0, interval: float = 0.05):
    """Await ``fetch()`` every *interval* until ``predicate(value)`` or timeout."""
    import asyncio as _asyncio
    import time as _time

    deadline = _time.monotonic() + timeout
    value = await fetch()
    while not predicate(value):
        if _time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting; last value: {value!r}")
        await _asyncio.sleep(interval)
        value = await fetch()
    return value
```

Restructure the test to: (1) start the run; (2) `_poll_until` status == `"running"` **before** issuing the cancel; (3) issue the cancel; (4) `_poll_until` status == `"cancelled"`. Replace every fixed `asyncio.sleep(...)`-then-assert with these polls. Keep the test's existing fixtures and client calls — only the waiting strategy changes.

- [ ] **Step 3: Verify in isolation and in a hostile order**

Run: `uv run pytest apps/api/tests -k "test_running_run_can_be_cancelled" -v` (3 times), then run its whole file plus two neighbouring files together.
Expected: PASS every time.

- [ ] **Step 4: Commit**

```bash
git add <test file>
git commit -m "test: deflake test_running_run_can_be_cancelled with deterministic status polling (BUG-10)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Dev environment repair + full-suite gate

- [ ] **Step 1: Repair the venv** (BUG-9 — `signxml` missing locally; C: drive was cleaned on 2026-07-02 so uv works again):

Run: `uv sync --all-packages`
Expected: completes without disk errors; `uv run python -c "import signxml; print('ok')"` prints `ok`.

- [ ] **Step 2: Full backend suite**

Run: `uv run pytest apps/api/tests packages/core/tests packages/nodes/tests packages/runtime/tests -q`
Expected: **0 failed** (≈2,850 passed, ~94 skipped). The 3 previous `signxml` failures must now pass.

- [ ] **Step 3: Web suite**

Run in `apps/web`: `npm run typecheck && npm test -- --run`
Expected: clean typecheck; 439+ passed.

- [ ] **Step 4:** No commit (environment only). Report the counts.

---

### Task 9: Protect `main` (P0 — red-tip prevention)

The regression that shipped in `81c0e690` would have been caught by the existing CI (`.github/workflows/ci.yml` already runs python/postgres/web/e2e lanes). The gap is enforcement.

- [ ] **Step 1: Enable branch protection requiring the CI lanes** (needs a GitHub remote + admin):

```bash
gh api -X PUT "repos/{owner}/{repo}/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": ["python", "postgres", "web"]},
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

First confirm the exact check names with `gh api repos/{owner}/{repo}/commits/main/check-runs --jq '.check_runs[].name'` and substitute them into `contexts`. If there is no GitHub remote yet, record this as a manual step in the master roadmap checklist and skip.

- [ ] **Step 2: Verify:** `gh api repos/{owner}/{repo}/branches/main/protection --jq '.required_status_checks.contexts'` lists the lanes.

---

### Task 10: Compose soak test (P0 #4)

**Files:**
- Create: `scripts/soak_test.py`

This is the one task needing a live stack (Docker). It exercises: 100-run burst → worker kill mid-flight → lease recovery → cancel storm.

- [ ] **Step 1: Create `scripts/soak_test.py`:**

```python
"""Compose soak test: burst 100 runs, kill the worker mid-flight, verify
every run still reaches a terminal state, then cancel-storm 20 running runs.

Usage:
    python scripts/soak_test.py --base-url http://localhost:8000 --token <PAT>

Requires the compose stack running (postgres/redis/api/worker). The script
creates its own throwaway workflow (manual trigger -> code node sleeping 2s).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

import httpx

TERMINAL = {"success", "error", "cancelled", "failed", "dead_letter"}
SLEEP_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "name": "Start", "params": {},
         "position": [0, 0]},
        {"id": "c", "type": "code", "name": "Sleep",
         "params": {"code": "import time\ntime.sleep(2)\noutput = {'ok': True}"},
         "position": [200, 0]},
    ],
    "edges": [{"source": "t", "target": "c"}],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--token", default=None)
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--worker-service", default="worker",
                    help="compose service name to kill mid-flight")
    ap.add_argument("--compose-file", default="deploy/docker-compose.yml")
    args = ap.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    c = httpx.Client(base_url=args.base_url, headers=headers, timeout=30)

    wf = c.post("/workflows", json={"name": f"soak-{int(time.time())}"}).raise_for_status().json()
    wf_id = wf["id"]
    c.put(f"/workflows/{wf_id}", json={"draft_graph": SLEEP_GRAPH}).raise_for_status()
    c.post(f"/workflows/{wf_id}/publish").raise_for_status()
    print(f"workflow {wf_id} published")

    run_ids: list[str] = []
    t0 = time.monotonic()
    for i in range(args.runs):
        r = c.post(f"/workflows/{wf_id}/run", json={"mode": "manual"}).raise_for_status().json()
        run_ids.append(r["id"])
    print(f"enqueued {len(run_ids)} runs in {time.monotonic() - t0:.1f}s")

    time.sleep(5)  # let some runs go in-flight
    print(f"killing worker service '{args.worker_service}' ...")
    subprocess.run(["docker", "compose", "-f", args.compose_file, "kill",
                    args.worker_service], check=True)
    time.sleep(10)
    print("restarting worker ...")
    subprocess.run(["docker", "compose", "-f", args.compose_file, "start",
                    args.worker_service], check=True)

    # Cancel storm: cancel up to 20 currently-running runs.
    cancelled = 0
    for rid in run_ids:
        if cancelled >= 20:
            break
        status = c.get(f"/runs/{rid}").raise_for_status().json()["status"]
        if status == "running":
            c.post(f"/runs/{rid}/cancel")
            cancelled += 1
    print(f"cancel storm: {cancelled} cancels issued")

    deadline = time.monotonic() + 15 * 60
    pending = set(run_ids)
    while pending and time.monotonic() < deadline:
        for rid in list(pending):
            status = c.get(f"/runs/{rid}").raise_for_status().json()["status"]
            if status in TERMINAL:
                pending.discard(rid)
        print(f"pending: {len(pending)}")
        time.sleep(5)

    if pending:
        print(f"FAIL: {len(pending)} runs never reached a terminal state: "
              f"{sorted(pending)[:10]}...")
        return 1
    print("PASS: all runs reached terminal states after worker kill + cancel storm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Bring up the stack and adjust names.** `docker compose -f deploy/docker-compose.yml config --services` — confirm the worker service name (pass `--worker-service` if it differs). Confirm the run-status field values against `apps/api/app/models.py` (`grep -n "status" apps/api/app/models.py | head -30`) and fix `TERMINAL` if the codebase uses different terminal names.

- [ ] **Step 3: Run it**

Run: `docker compose -f deploy/docker-compose.yml up -d && uv run python scripts/soak_test.py --token <admin PAT>`
Expected: `PASS: all runs reached terminal states...`, exit 0. If runs wedge, capture `docker compose logs worker --tail 200` and the stuck run ids, and report — do not "fix" the queue blindly; the failure itself is the audit deliverable.

- [ ] **Step 4: Commit the script**

```bash
git add scripts/soak_test.py
git commit -m "test: add compose soak script (run burst, worker kill, cancel storm)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review checklist (executor)

- `uv run pytest apps/api/tests packages/core/tests packages/nodes/tests packages/runtime/tests -q` → 0 failures.
- `git status` shows only the pre-existing unrelated in-flight files as modified.
- All 8 code/test commits present, each scoped to its task's files.
