# Sandboxed Run Execution (MT Phase D, slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared subprocess pool with disposable, hardened, per-run containers (warm-pooled per `(org, env)`) as an opt-in platform execution mode, enforced when multi-tenancy is on.

**Architecture:** A new `SandboxExecutor` plugs into the existing executor seam (`app/services/executors/base.py`). Container-spawning machinery shared with the docker runner-pool provider is extracted into `app/services/container_runtime.py` (image build, isolation-runtime probe, hardened spawn kwargs). A `SandboxPool` in `app/services/sandbox_pool.py` holds warm containers keyed by `(org_id, environment_id)` and drives the existing `noodle_runtime` stdin/stdout JSON protocol, including the `call_workflow` host callback the docker provider currently lacks.

**Tech Stack:** Python 3.12, FastAPI, docker SDK for Python, pytest + pytest-asyncio. Tests use a fake Docker client (no daemon needed) except one env-gated integration test.

**Spec:** `docs/superpowers/specs/2026-06-12-sandboxed-run-execution-design.md`

**Key existing code to understand before starting:**
- `apps/api/app/services/executors/base.py` — `RunExecutionContext` / `RunOutcome` / `RunExecutor` protocol. Executors own NO DB access.
- `apps/api/app/services/providers/docker.py` — current per-run container driver: `ensure_docker_image` + attach-socket protocol loop. Note an existing bug: it sends the run message immediately after attach (line ~144) AND again on the `ready` event (line ~172). The extraction fixes this (send only after `ready`).
- `apps/api/app/services/runtime_pool.py` — the warm subprocess pool, including `_handle_call_workflow` (lines 318–361), which the sandbox worker mirrors.
- `packages/runtime/noodle_runtime/server.py` — the protocol: first line out is `{"type":"ready"}`; host sends one `run` message; events stream out; terminal is `{"type":"result","status":...}`. After `result` the runtime loops and can accept another `run` message — this is what makes warm container reuse possible.
- `apps/api/app/services/runner.py` lines 815–873 — the dispatch fork (remote pool vs local subprocess). The sandbox slots in between.

**Conventions:** run all test commands from `D:\noodle\apps\api` (`cd apps/api` first). Commit after every green task. All new settings use the existing `app.config.Settings` pydantic-settings pattern (env var = UPPER_SNAKE of field name).

---

### Task 1: Settings + multi-tenancy sandbox policy

**Files:**
- Modify: `apps/api/app/config.py` (after `multi_tenancy_enabled`, ~line 177)
- Create: `apps/api/app/services/sandbox_policy.py`
- Test: `apps/api/tests/test_sandbox_policy.py`

- [x] **Step 1: Write the failing tests**

```python
"""Sandbox mode settings and the MT enforcement policy."""
import pytest

from app.config import settings
from app.services.sandbox_policy import enforce_sandbox_policy


def test_defaults_are_off_and_strict():
    assert settings.execution_sandbox == "off"
    assert settings.sandbox_runtime == "auto"
    assert settings.sandbox_policy_strict is True


def test_single_tenant_any_mode_passes(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    for mode in ("off", "auto", "required"):
        monkeypatch.setattr(settings, "execution_sandbox", mode)
        enforce_sandbox_policy()  # must not raise


@pytest.mark.parametrize("mode", ["off", "auto"])
def test_mt_requires_sandbox_required(monkeypatch, mode):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", mode)
    monkeypatch.setattr(settings, "sandbox_policy_strict", True)
    with pytest.raises(RuntimeError, match="execution_sandbox=required"):
        enforce_sandbox_policy()


def test_mt_with_required_passes(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    enforce_sandbox_policy()


def test_strictness_escape_hatch(monkeypatch):
    """Trusted-tenant deployments (and the MT test suite) can opt out."""
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    monkeypatch.setattr(settings, "sandbox_policy_strict", False)
    enforce_sandbox_policy()  # must not raise


def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "definitely-not-a-mode")
    with pytest.raises(RuntimeError, match="execution_sandbox"):
        enforce_sandbox_policy()
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_policy.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'execution_sandbox'` / `ModuleNotFoundError: sandbox_policy`

- [x] **Step 3: Add settings to `apps/api/app/config.py`**

Insert directly after the `multi_tenancy_enabled` block (~line 177), matching the surrounding comment style:

```python
    # Sandboxed execution (MT Phase D slice 1). "off": runs use the warm
    # subprocess pool (today's behaviour). "auto": use disposable hardened
    # containers when a Docker daemon is reachable, else fall back to
    # subprocess with a startup warning. "required": refuse to start the
    # dispatching process without a usable daemon + runtime.
    execution_sandbox: str = "off"
    # Container isolation runtime: auto-probe (kata > runsc > runc) or pin.
    sandbox_runtime: str = "auto"
    # Docker daemon for sandbox containers; empty = environment default
    # (DOCKER_HOST / the mounted socket).
    sandbox_docker_host: str = ""
    # Dedicated bridge network for run containers — keeps tenant code off
    # the compose project network (no postgres/redis/minio reachability).
    sandbox_network: str = "noodle-sandbox"
    # Per-container resource ceilings.
    sandbox_mem_limit: str = "1g"
    sandbox_cpu_limit: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_tmpfs_size: str = "256m"
    # Warm pool: idle containers kept per (org, env) key / globally, idle
    # TTL, and a recycle ceiling bounding state accumulation per container.
    sandbox_warm_per_key: int = 1
    sandbox_warm_total: int = 8
    sandbox_warm_ttl_seconds: float = 300.0
    sandbox_max_runs_per_container: int = 50
    # Seconds to wait for a fresh container's {"type":"ready"} handshake.
    sandbox_ready_timeout_seconds: float = 60.0
    # When True (default), multi_tenancy_enabled requires
    # execution_sandbox=required at startup. Setting False acknowledges
    # shared-kernel execution for trusted-tenant deployments.
    sandbox_policy_strict: bool = True
```

- [x] **Step 4: Create `apps/api/app/services/sandbox_policy.py`**

```python
"""Startup policy: multi-tenant deployments must not share the subprocess pool.

Called from the API lifespan and worker_main before any run can dispatch.
Kept separate from sandbox_pool so importing the policy never imports the
docker SDK.
"""

from app.config import settings

_VALID_MODES = ("off", "auto", "required")


def enforce_sandbox_policy() -> None:
    """Raise RuntimeError when the configuration is unsafe or invalid."""
    if settings.execution_sandbox not in _VALID_MODES:
        raise RuntimeError(
            f"invalid execution_sandbox={settings.execution_sandbox!r}: "
            f"expected one of {_VALID_MODES}"
        )
    if not settings.multi_tenancy_enabled or not settings.sandbox_policy_strict:
        return
    if settings.execution_sandbox != "required":
        raise RuntimeError(
            "multi_tenancy_enabled=true requires execution_sandbox=required "
            "(tenant code must not share the worker's kernel namespace). "
            "Set EXECUTION_SANDBOX=required, or SANDBOX_POLICY_STRICT=false "
            "to explicitly accept shared-kernel execution for trusted tenants."
        )
```

- [x] **Step 5: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_policy.py -v`
Expected: 6 passed (parametrized counts as 2)

- [x] **Step 6: Keep the MT test suite green**

Find every test fixture that flips MT on: `grep -rn "multi_tenancy_enabled" apps/api/tests --include="*.py" -l`. In each fixture that sets `settings.multi_tenancy_enabled = True` (or monkeypatches it), also set `settings.sandbox_policy_strict = False` the same way. The cleanest variant: if there is a shared MT fixture in a conftest, one line there covers all. Then run the MT-touching suites:

Run: `cd apps/api && python -m pytest tests/ -k "tenan or org" -q`
Expected: same pass count as before this task (the policy is not yet called at app startup, so this is precautionary for Task 12 — do it now anyway).

- [x] **Step 7: Commit**

```bash
git add apps/api/app/config.py apps/api/app/services/sandbox_policy.py apps/api/tests/test_sandbox_policy.py apps/api/tests/conftest.py
git commit -m "feat(sandbox): execution_sandbox settings + MT startup policy"
```

---

### Task 2: Fake Docker client test infrastructure

**Files:**
- Create: `apps/api/tests/sandbox_fakes.py`

No TDD cycle — this is shared test infrastructure consumed by Tasks 3–12. It must faithfully model the slices of the docker SDK we use: `client.info()`, `client.ping()`, `client.images.get/build`, `client.networks.get/create`, `client.containers.run/get`, `container.attach_socket(...)._sock` with `sendall/recv/settimeout`, `container.remove(force=True)`.

- [x] **Step 1: Create `apps/api/tests/sandbox_fakes.py`**

```python
"""Fake docker SDK surface for sandbox tests (no daemon required).

The sandbox code calls the sync SDK via run_in_executor, so these fakes are
plain-sync. FakeRawSock.recv blocks on a queue.Queue exactly like a real
attach socket blocks on the wire; feed events with .feed(dict) and simulate
container death with .feed_eof().
"""

import json
import queue
import socket as socket_mod


class FakeRawSock:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def feed(self, obj: dict) -> None:
        self._q.put((json.dumps(obj) + "\n").encode())

    def feed_raw(self, data: bytes) -> None:
        self._q.put(data)

    def feed_eof(self) -> None:
        self._q.put(b"")

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _n: int) -> bytes:
        try:
            return self._q.get(timeout=self.timeout if self.timeout else 5.0)
        except queue.Empty:
            raise socket_mod.timeout("fake recv timeout")

    def settimeout(self, t: float) -> None:
        self.timeout = t

    def sent_messages(self) -> list[dict]:
        """Decode every newline-framed JSON message written by the host."""
        blob = b"".join(self.sent)
        return [json.loads(l) for l in blob.split(b"\n") if l.strip()]


class FakeSock:
    """docker's attach_socket return — code reaches the raw socket via _sock."""

    def __init__(self):
        self._sock = FakeRawSock()


class FakeContainer:
    def __init__(self, name: str):
        self.name = name
        self.removed = False
        self.sock = FakeSock()

    def attach_socket(self, params=None):
        return self.sock

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self, client: "FakeDockerClient"):
        self._client = client

    def run(self, image: str, **kwargs) -> FakeContainer:
        self._client.run_calls.append({"image": image, **kwargs})
        c = FakeContainer(kwargs.get("name", f"c{len(self._client.containers_made)}"))
        # A real noodle_runtime emits ready as its first line.
        if self._client.auto_ready:
            c.sock._sock.feed({"type": "ready"})
        self._client.containers_made.append(c)
        return c

    def get(self, name: str) -> FakeContainer:
        for c in self._client.containers_made:
            if c.name == name:
                return c
        raise KeyError(name)


class _FakeImages:
    def __init__(self):
        self.built: list[str] = []
        self.existing: set[str] = set()

    def get(self, tag: str):
        if tag in self.existing or tag in self.built:
            return object()
        raise KeyError(tag)  # NotFound — triggers build

    def build(self, fileobj=None, tag: str = "", rm: bool = True):
        self.built.append(tag)
        return (object(), iter(()))


class _FakeNetworks:
    def __init__(self):
        self.existing: set[str] = set()
        self.create_error: Exception | None = None

    def get(self, name: str):
        if name in self.existing:
            return name
        raise KeyError(name)

    def create(self, name: str, **kwargs):
        if self.create_error is not None:
            err, self.create_error = self.create_error, None
            raise err
        self.existing.add(name)
        return name


class FakeDockerClient:
    def __init__(self, runtimes: tuple[str, ...] = ("runc",), auto_ready: bool = True):
        self.run_calls: list[dict] = []
        self.containers_made: list[FakeContainer] = []
        self.auto_ready = auto_ready
        self.containers = _FakeContainers(self)
        self.images = _FakeImages()
        self.networks = _FakeNetworks()
        self._runtimes = runtimes

    def info(self) -> dict:
        return {"Runtimes": {r: {"path": r} for r in self._runtimes}}

    def ping(self) -> bool:
        return True
```

- [x] **Step 2: Sanity-import**

Run: `cd apps/api && python -c "from tests.sandbox_fakes import FakeDockerClient; c = FakeDockerClient(); c.containers.run('img', name='x'); print('ok')"`
Expected: `ok`

- [x] **Step 3: Commit**

```bash
git add apps/api/tests/sandbox_fakes.py
git commit -m "test(sandbox): fake docker SDK surface for daemon-free tests"
```

---

### Task 3: Extract `container_runtime.py` — image build + versioned tags + non-root user

**Files:**
- Create: `apps/api/app/services/container_runtime.py`
- Modify: `apps/api/app/services/providers/docker.py` (delete the moved code, import instead)
- Test: `apps/api/tests/test_sandbox_container_runtime.py`

- [x] **Step 1: Write the failing tests**

```python
"""Shared container machinery: image tags, dockerfile generation, build."""
import pytest

from app.services.container_runtime import (
    IMAGE_SCHEMA_VERSION,
    _validate_packages,
    _validate_python_version,
    ensure_docker_image,
    image_tag_for,
)
from tests.sandbox_fakes import FakeDockerClient


def test_image_tag_includes_schema_version():
    payload = {"id": "env1", "packages_hash": "abc123"}
    tag = image_tag_for(payload)
    assert tag == f"noodle-env:env1-abc123-{IMAGE_SCHEMA_VERSION}"


def test_image_tag_defaults():
    assert image_tag_for({}) == f"noodle-env:default-latest-{IMAGE_SCHEMA_VERSION}"


def test_build_skipped_when_cached():
    client = FakeDockerClient()
    client.images.existing.add("sometag")
    ensure_docker_image(client, "sometag", {"python_version": "3.12", "packages": []})
    assert client.images.built == []


def test_build_dockerfile_runs_as_nonroot(monkeypatch):
    captured = {}
    client = FakeDockerClient()
    orig = client.images.build

    def capture(fileobj=None, tag="", rm=True):
        captured["dockerfile"] = fileobj.read().decode()
        return orig(fileobj=fileobj, tag=tag, rm=rm)

    client.images.build = capture
    ensure_docker_image(client, "t1", {"python_version": "3.12", "packages": ["requests==2.31.0"]})
    df = captured["dockerfile"]
    assert "USER noodle" in df
    assert "useradd" in df
    assert "requests==2.31.0" in df
    # install happens BEFORE dropping privileges
    assert df.index("uv pip install") < df.index("USER noodle")


def test_package_validation_blocks_shell_metacharacters():
    with pytest.raises(ValueError):
        _validate_packages(["requests; curl evil.sh | sh"])
    assert _validate_packages(["numpy>=1.26,<2", " ", "pandas[excel]==2.2.0"]) == [
        "numpy>=1.26,<2",
        "pandas[excel]==2.2.0",
    ]


def test_python_version_validation():
    assert _validate_python_version("3.12") == "3.12"
    with pytest.raises(ValueError):
        _validate_python_version("3.12; rm -rf /")
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.container_runtime`

- [x] **Step 3: Create `apps/api/app/services/container_runtime.py`**

Move `_PKG_SPEC_RE`, `_PY_VERSION_RE`, `_validate_packages`, `_validate_python_version`, and `ensure_docker_image` verbatim from `providers/docker.py` (including the RD-2 comment block), then add the tag helper and the non-root dockerfile change:

```python
"""Shared container-spawning machinery (sandbox executor + docker provider).

Per-env image builds with RD-2 package validation, schema-versioned image
tags, the isolation-runtime probe, and the hardened ``containers.run``
keyword set live here so a hardening fix lands on every container Noodle
ever spawns. No DB access; safe to import from worker_main.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Bump whenever the generated Dockerfile changes shape — stale images built
# from the old recipe (e.g. root-running v1 images) must never be reused.
IMAGE_SCHEMA_VERSION = "v2"

# ... (move _PKG_SPEC_RE, _PY_VERSION_RE, _validate_packages,
#      _validate_python_version here verbatim, with their comments) ...


def image_tag_for(env_payload: dict) -> str:
    return (
        f"noodle-env:{env_payload.get('id', 'default')}"
        f"-{env_payload.get('packages_hash', 'latest')}"
        f"-{IMAGE_SCHEMA_VERSION}"
    )


def ensure_docker_image(client: Any, image_tag: str, env_payload: dict) -> None:
    """Build the env image if absent. Sync — call via run_in_executor."""
    try:
        client.images.get(image_tag)
        return  # cache hit
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass

    python_version = _validate_python_version(env_payload.get("python_version", "3.12"))
    packages = _validate_packages(env_payload.get("packages") or [])
    packages_str = " ".join(packages)
    install_line = (
        f"RUN uv pip install --system noodle-runtime noodle-nodes noodle-core {packages_str}".rstrip()
    )

    # Non-root: installs run as root, the runtime does not. HOME is /tmp at
    # runtime (tmpfs) because the rootfs — including /home — is read-only.
    dockerfile = (
        f"FROM python:{python_version}-slim\n"
        "RUN pip install uv --quiet\n"
        f"{install_line}\n"
        "RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin noodle\n"
        "USER noodle\n"
        'ENTRYPOINT ["python", "-u", "-m", "noodle_runtime"]\n'
    )

    import io  # noqa: PLC0415

    client.images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=image_tag, rm=True)
    logger.info("built docker image %s", image_tag)
```

- [x] **Step 4: Update `providers/docker.py` to consume the shared module**

Delete the moved regexes/functions and `ensure_docker_image` from `providers/docker.py`; replace with:

```python
from app.services.container_runtime import (
    ensure_docker_image,
    image_tag_for,
)
```

and replace the inline tag construction (lines ~97–100) with `image_tag = image_tag_for(env_payload)`.

- [x] **Step 5: Run tests + the existing suites that touch the provider**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py tests/test_executors.py tests/test_architecture_fixes.py -v`
Expected: all pass (the provider tests exercise the moved validation through the new import path)

- [x] **Step 6: Commit**

```bash
git add apps/api/app/services/container_runtime.py apps/api/app/services/providers/docker.py apps/api/tests/test_sandbox_container_runtime.py
git commit -m "refactor(sandbox): extract shared container_runtime; v2 non-root env images"
```

---

### Task 4: Isolation runtime probe

**Files:**
- Modify: `apps/api/app/services/container_runtime.py`
- Test: `apps/api/tests/test_sandbox_container_runtime.py` (append)

- [x] **Step 1: Write the failing tests (append to the test file)**

```python
from app.services.container_runtime import detect_runtime


def test_probe_auto_prefers_strongest():
    assert detect_runtime(FakeDockerClient(runtimes=("runc",)), "auto") == "runc"
    assert detect_runtime(FakeDockerClient(runtimes=("runc", "runsc")), "auto") == "runsc"
    assert detect_runtime(FakeDockerClient(runtimes=("runc", "runsc", "kata")), "auto") == "kata"


def test_probe_explicit_runtime_must_exist():
    assert detect_runtime(FakeDockerClient(runtimes=("runc", "runsc")), "runsc") == "runsc"
    with pytest.raises(RuntimeError, match="runsc"):
        detect_runtime(FakeDockerClient(runtimes=("runc",)), "runsc")


def test_probe_runc_always_available():
    """Docker Desktop daemons sometimes omit Runtimes from info()."""

    class NoRuntimesClient(FakeDockerClient):
        def info(self):
            return {}

    assert detect_runtime(NoRuntimesClient(), "auto") == "runc"
    assert detect_runtime(NoRuntimesClient(), "runc") == "runc"


def test_probe_invalid_value():
    with pytest.raises(ValueError, match="sandbox_runtime"):
        detect_runtime(FakeDockerClient(), "qemu")
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py -k probe -v`
Expected: FAIL — `ImportError: detect_runtime`

- [x] **Step 3: Implement in `container_runtime.py`**

```python
_RUNTIME_PREFERENCE = ("kata", "runsc", "runc")  # strongest first
_VALID_RUNTIMES = ("auto", "runc", "runsc", "kata")


def detect_runtime(client: Any, configured: str) -> str:
    """Resolve the isolation runtime for this daemon. Sync — call at startup.

    Explicit values fail fast when the daemon doesn't list them; ``auto``
    picks the strongest available. ``runc`` is the daemon default and is
    treated as always present (some daemons omit it from info()).
    """
    if configured not in _VALID_RUNTIMES:
        raise ValueError(
            f"invalid sandbox_runtime={configured!r}: expected one of {_VALID_RUNTIMES}"
        )
    info = client.info() or {}
    available = set((info.get("Runtimes") or {}).keys()) | {"runc"}
    if configured != "auto":
        if configured not in available:
            raise RuntimeError(
                f"sandbox_runtime={configured!r} is not installed on this Docker "
                f"daemon (available: {sorted(available)}). Install it or use 'auto'."
            )
        return configured
    for candidate in _RUNTIME_PREFERENCE:
        if candidate in available:
            return candidate
    return "runc"
```

- [x] **Step 4: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py -v`
Expected: all pass

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/container_runtime.py apps/api/tests/test_sandbox_container_runtime.py
git commit -m "feat(sandbox): isolation runtime probe (kata > runsc > runc)"
```

---

### Task 5: Hardened spawn kwargs + sandbox network

**Files:**
- Modify: `apps/api/app/services/container_runtime.py`
- Test: `apps/api/tests/test_sandbox_container_runtime.py` (append)

- [x] **Step 1: Write the failing tests (append)**

```python
from app.services.container_runtime import ensure_sandbox_network, hardening_kwargs


def test_hardening_kwargs_complete():
    kw = hardening_kwargs(runtime="runsc", network="noodle-sandbox")
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["read_only"] is True
    assert kw["tmpfs"] == {"/tmp": "size=256m"}
    assert kw["mem_limit"] == "1g"
    assert kw["nano_cpus"] == 1_000_000_000
    assert kw["pids_limit"] == 256
    assert kw["network"] == "noodle-sandbox"
    assert kw["runtime"] == "runsc"
    # rootfs is read-only, so HOME must point at the writable tmpfs
    assert kw["environment"] == {"HOME": "/tmp"}


def test_hardening_kwargs_overrides():
    kw = hardening_kwargs(
        runtime="runc", network="bridge",
        overrides={"mem_limit": "4g", "pids_limit": 1024, "nano_cpus": 2_000_000_000},
    )
    assert kw["mem_limit"] == "4g"
    assert kw["pids_limit"] == 1024
    assert kw["nano_cpus"] == 2_000_000_000
    # overrides can't strip the security floor
    assert kw["cap_drop"] == ["ALL"]
    assert kw["read_only"] is True


def test_ensure_sandbox_network_creates_once():
    client = FakeDockerClient()
    assert ensure_sandbox_network(client, "noodle-sandbox") == "noodle-sandbox"
    assert "noodle-sandbox" in client.networks.existing
    ensure_sandbox_network(client, "noodle-sandbox")  # idempotent, no error


def test_ensure_sandbox_network_creation_race():
    """Two workers racing to create: create raises, but get then succeeds."""
    client = FakeDockerClient()
    client.networks.existing.add("noodle-sandbox")  # appears between get+create
    client.networks.create_error = RuntimeError("conflict: network exists")
    # Simulate: first get misses, create conflicts, re-get hits.
    client.networks.existing.discard("noodle-sandbox")

    orig_create = client.networks.create

    def create_then_appear(name, **kw):
        client.networks.existing.add(name)
        raise RuntimeError("409 conflict")

    client.networks.create = create_then_appear
    assert ensure_sandbox_network(client, "noodle-sandbox") == "noodle-sandbox"


def test_ensure_sandbox_network_hard_failure():
    client = FakeDockerClient()

    def always_fail(name, **kw):
        raise RuntimeError("daemon on fire")

    client.networks.create = always_fail
    with pytest.raises(RuntimeError, match="sandbox network"):
        ensure_sandbox_network(client, "noodle-sandbox")
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py -k "hardening or network" -v`
Expected: FAIL — ImportError

- [x] **Step 3: Implement in `container_runtime.py`**

```python
# Spawn kwargs every Noodle-launched container gets. Resource ceilings are
# overridable (per runner-pool provider_config, later per org_limits); the
# security floor — cap_drop / no-new-privileges / read-only rootfs — is not.
_OVERRIDABLE = ("mem_limit", "nano_cpus", "pids_limit", "tmpfs", "network")


def hardening_kwargs(
    *, runtime: str, network: str, overrides: dict | None = None
) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": f"size={settings.sandbox_tmpfs_size}"},
        "mem_limit": settings.sandbox_mem_limit,
        "nano_cpus": int(settings.sandbox_cpu_limit * 1_000_000_000),
        "pids_limit": settings.sandbox_pids_limit,
        "network": network,
        "runtime": runtime,
        # rootfs is read-only; /tmp is the only writable surface.
        "environment": {"HOME": "/tmp"},
    }
    for key, value in (overrides or {}).items():
        if key in _OVERRIDABLE:
            kw[key] = value
    return kw


def ensure_sandbox_network(client: Any, name: str | None = None) -> str:
    """Get-or-create the dedicated bridge network. Handles the two-workers-
    racing-to-create case by re-checking after a failed create."""
    name = name or settings.sandbox_network
    try:
        client.networks.get(name)
        return name
    except Exception:  # noqa: BLE001 — NotFound
        pass
    try:
        client.networks.create(name, driver="bridge")
        return name
    except Exception as exc:  # noqa: BLE001 — possibly a concurrent create
        try:
            client.networks.get(name)
            return name
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"could not create sandbox network {name!r}: {exc}") from exc
```

- [x] **Step 4: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py -v`
Expected: all pass

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/container_runtime.py apps/api/tests/test_sandbox_container_runtime.py
git commit -m "feat(sandbox): hardened spawn kwargs + dedicated sandbox network"
```

---

### Task 6: Apply hardening to the docker runner-pool provider (+ fix double-send)

**Files:**
- Modify: `apps/api/app/services/providers/docker.py`
- Test: `apps/api/tests/test_sandbox_container_runtime.py` (append)

The provider currently spawns with NO hardening and sends the run message twice (once right after attach at line ~144, again on the `ready` event at line ~172 — the runtime queues the duplicate and would try to execute the run a second time if the host didn't tear the container down on `result`). Fix both.

- [x] **Step 1: Change the spawn call in `assign_docker_run`**

Replace the `client.containers.run(...)` call (lines ~126–136) with:

```python
        from app.services.container_runtime import hardening_kwargs

        spawn_kwargs = hardening_kwargs(
            runtime=cfg.get("runtime") or "runc",
            network=network,
            overrides=cfg.get("limits") or {},
        )
        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                image_tag,
                detach=True,
                stdin_open=True,
                remove=False,
                name=container_name,
                **spawn_kwargs,
            ),
        )
```

(Move the import to the top of the file with the Task 3 imports. `cfg["runtime"]` gives runner pools per-pool runtime selection; `cfg["limits"]` lets an operator raise resource ceilings per pool.)

- [x] **Step 2: Delete the pre-ready send**

Remove the block at lines ~143–144 (`# Write the run message to stdin.` + the `sendall` call). The `ready`-event send inside the loop (line ~172) is now the only send.

- [x] **Step 3: Write a regression test (append to test file)**

```python
import asyncio


def test_docker_provider_spawns_hardened(monkeypatch):
    """assign_docker_run passes the security floor to containers.run and
    sends the run message exactly once (after ready)."""
    from app.services.providers import docker as provider

    client = FakeDockerClient()

    class FakeSession:
        async def get(self, model, pid):
            class P:
                provider_config = {"runtime": "runsc", "limits": {"mem_limit": "2g"}}
            return P()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import docker as docker_sdk  # the real SDK module is imported inside the fn
    monkeypatch.setattr(docker_sdk, "from_env", lambda: client)

    events: list[dict] = []

    async def on_event(e):
        events.append(e)

    async def scenario():
        task = asyncio.create_task(
            provider.assign_docker_run(
                lambda: FakeSession(), "run123", "pool1",
                {"id": "env1", "packages_hash": "h", "python_version": "3.12", "packages": []},
                {"nodes": [], "edges": []}, None, None, [], on_event,
            )
        )
        await asyncio.sleep(0.3)  # let it spawn + consume ready
        sock = client.containers_made[0].sock._sock
        sock.feed({"type": "result", "status": "success"})
        return await task

    status = asyncio.run(scenario())
    assert status == "success"
    call = client.run_calls[0]
    assert call["cap_drop"] == ["ALL"]
    assert call["runtime"] == "runsc"
    assert call["mem_limit"] == "2g"          # pool override applied
    assert call["read_only"] is True
    run_msgs = [m for m in client.containers_made[0].sock._sock.sent_messages()
                if m.get("type") == "run"]
    assert len(run_msgs) == 1                  # double-send fixed
    assert client.containers_made[0].removed   # torn down in finally
```

Note: if the provider builds its client via `docker.from_env()` inside the function, the `monkeypatch.setattr(docker_sdk, "from_env", ...)` line above intercepts it. If the `docker` package is not installed in the dev venv, install it: `pip install docker`.

- [x] **Step 4: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_container_runtime.py -v`
Expected: all pass

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/providers/docker.py apps/api/tests/test_sandbox_container_runtime.py
git commit -m "fix(docker-provider): hardened spawns, per-pool runtime/limits, single run-message send"
```

---

### Task 7: `SandboxWorker` — spawn + ready handshake

**Files:**
- Create: `apps/api/app/services/sandbox_pool.py`
- Test: `apps/api/tests/test_sandbox_pool.py`

- [x] **Step 1: Write the failing tests**

```python
"""SandboxWorker lifecycle: spawn, ready handshake, protocol, teardown."""
import asyncio

import pytest

from app.config import settings
from app.services.sandbox_pool import SandboxWorker
from tests.sandbox_fakes import FakeDockerClient

ENV = {"id": "env1", "packages_hash": "h1", "python_version": "3.12", "packages": []}


def test_spawn_waits_for_ready_and_is_hardened():
    client = FakeDockerClient(runtimes=("runc", "runsc"))

    async def scenario():
        return await SandboxWorker.spawn(
            client, key=("org1", "env1"), env_payload=ENV,
            runtime="runsc", network="noodle-sandbox",
        )

    worker = asyncio.run(scenario())
    assert worker.key == ("org1", "env1")
    assert not worker.dead
    call = client.run_calls[0]
    assert call["cap_drop"] == ["ALL"]
    assert call["runtime"] == "runsc"
    assert call["image"].endswith("-v2")
    assert call["name"].startswith("noodle-sbx-")


def test_spawn_ready_timeout_kills_container(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_ready_timeout_seconds", 0.2)
    client = FakeDockerClient(auto_ready=False)  # runtime never says ready

    async def scenario():
        await SandboxWorker.spawn(
            client, key=("org1", "env1"), env_payload=ENV,
            runtime="runc", network="noodle-sandbox",
        )

    with pytest.raises(RuntimeError, match="ready"):
        asyncio.run(scenario())
    assert client.containers_made[0].removed


def test_spawn_container_dies_before_ready():
    client = FakeDockerClient(auto_ready=False)

    async def scenario():
        task = asyncio.create_task(SandboxWorker.spawn(
            client, key=("o", "e"), env_payload=ENV,
            runtime="runc", network="noodle-sandbox",
        ))
        await asyncio.sleep(0.1)
        client.containers_made[0].sock._sock.feed_eof()
        await task

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
    assert client.containers_made[0].removed
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -v`
Expected: FAIL — ModuleNotFoundError

- [x] **Step 3: Create `apps/api/app/services/sandbox_pool.py` with `SandboxWorker` spawn/handshake/teardown**

```python
"""Warm per-(org, environment) sandbox container pool (MT Phase D slice 1).

SandboxWorker wraps one hardened container running ``noodle_runtime`` plus
its attach socket. SandboxPool hands a worker to at most one run at a time
and returns clean workers to a bounded warm list keyed by
``(org_id, environment_id)`` — reuse is strictly within one key, so
cross-tenant container reuse is impossible by construction.

The wire protocol is the same newline-framed JSON ``noodle_runtime`` speaks
to the subprocess pool (see packages/runtime/noodle_runtime/server.py),
including the ``call_workflow`` host callback that runtime_pool brokers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app.config import settings
from app.services.container_runtime import (
    ensure_docker_image,
    hardening_kwargs,
    image_tag_for,
)
from noodle.serialization import deserialize_value, serialize_value

logger = logging.getLogger(__name__)

# Events forwarded verbatim to the host's on_event (same set as the docker
# runner-pool provider).
_FORWARDED_EVENTS = frozenset({
    "node_started", "node_finished", "agent_action_requested",
    "agent_tool_started", "agent_tool_approval_required",
    "agent_tool_auto_approved", "agent_tool_finished",
    "agent_action_completed", "agent_tool_approval_decided",
    "run_error", "run_cancelled", "module_error",
})


class SandboxWorker:
    """One container + attach socket; drives one run at a time."""

    def __init__(self, client: Any, container: Any, sock: Any, *,
                 key: tuple[str | None, str | None], image_tag: str) -> None:
        self.client = client
        self.container = container
        self._sock = sock
        self.key = key
        self.image_tag = image_tag
        self.runs_completed = 0
        self.idle_since = time.monotonic()
        self.dead = False
        self._buf = b""
        self._write_lock = asyncio.Lock()

    @classmethod
    async def spawn(cls, client: Any, *, key: tuple[str | None, str | None],
                    env_payload: dict, runtime: str, network: str) -> "SandboxWorker":
        loop = asyncio.get_running_loop()
        tag = image_tag_for(env_payload)
        await loop.run_in_executor(None, ensure_docker_image, client, tag, env_payload)
        name = f"noodle-sbx-{uuid.uuid4().hex[:12]}"
        spawn_kwargs = hardening_kwargs(runtime=runtime, network=network)
        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                tag, detach=True, stdin_open=True, remove=False,
                name=name, **spawn_kwargs,
            ),
        )
        worker: SandboxWorker | None = None
        try:
            sock = await loop.run_in_executor(None, lambda: container.attach_socket(
                params={"stdin": True, "stdout": True, "stderr": False, "stream": True}
            ))
            worker = cls(client, container, sock, key=key, image_tag=tag)
            await worker._await_ready(loop)
            return worker
        except BaseException:
            if worker is not None:
                await worker.close()
            else:
                try:
                    await loop.run_in_executor(None, lambda: container.remove(force=True))
                except Exception:  # noqa: BLE001
                    pass
            raise

    async def _await_ready(self, loop: asyncio.AbstractEventLoop) -> None:
        await loop.run_in_executor(
            None, self._sock._sock.settimeout, settings.sandbox_ready_timeout_seconds
        )
        try:
            event = await self._read_event(loop)
        except RuntimeError as exc:
            raise RuntimeError(
                f"sandbox container {self.container.name} did not become "
                f"ready within {settings.sandbox_ready_timeout_seconds}s: {exc}"
            ) from exc
        if event.get("type") != "ready":
            raise RuntimeError(
                f"sandbox container {self.container.name} sent "
                f"{event.get('type')!r} before ready"
            )

    async def _read_event(self, loop: asyncio.AbstractEventLoop) -> dict:
        """Next JSON event from the attach socket (skips undecodable lines)."""
        while True:
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue  # node stdout noise on the protocol stream
            try:
                chunk = await loop.run_in_executor(None, self._sock._sock.recv, 4096)
            except Exception as exc:  # noqa: BLE001 — socket.timeout et al.
                self.dead = True
                raise RuntimeError(f"sandbox socket read failed: {exc}") from exc
            if not chunk:
                self.dead = True
                raise RuntimeError("sandbox container closed its output stream")
            self._buf += chunk

    async def _send(self, message: dict, loop: asyncio.AbstractEventLoop) -> None:
        payload = json.dumps(serialize_value(message)).encode() + b"\n"
        async with self._write_lock:
            try:
                await loop.run_in_executor(None, self._sock._sock.sendall, payload)
            except Exception as exc:  # noqa: BLE001 — broken pipe = dead container
                self.dead = True
                raise RuntimeError(f"sandbox socket write failed: {exc}") from exc

    async def close(self) -> None:
        """Force-remove the container. Idempotent; never raises."""
        self.dead = True
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: self.container.remove(force=True))
        except Exception:  # noqa: BLE001 — already gone
            pass
```

- [x] **Step 4: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -v`
Expected: 3 passed

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/sandbox_pool.py apps/api/tests/test_sandbox_pool.py
git commit -m "feat(sandbox): SandboxWorker spawn + ready handshake + teardown"
```

---

### Task 8: `SandboxWorker.run` — protocol loop, events, dirty exits, timeouts

**Files:**
- Modify: `apps/api/app/services/sandbox_pool.py`
- Test: `apps/api/tests/test_sandbox_pool.py` (append)

- [x] **Step 1: Write the failing tests (append)**

```python
async def _spawned_worker(client):
    return await SandboxWorker.spawn(
        client, key=("org1", "env1"), env_payload=ENV,
        runtime="runc", network="noodle-sandbox",
    )


def test_run_forwards_events_and_returns_status():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock
        sock.feed({"type": "node_started", "node_id": "n1"})
        sock.feed({"type": "node_finished", "node_id": "n1", "status": "success"})
        sock.feed({"type": "result", "status": "success"})
        events = []

        async def on_event(e):
            events.append(e)

        status = await worker.run(
            "run1", graph={"nodes": []}, cache=None, targets=None,
            workflow_modules=[], on_event=on_event,
        )
        return status, events, worker

    status, events, worker = asyncio.run(scenario())
    assert status == "success"
    assert [e["type"] for e in events] == ["node_started", "node_finished"]
    assert not worker.dead
    assert worker.runs_completed == 1
    # the run message went over the wire with the run id
    sock = client.containers_made[0].sock._sock
    run_msgs = [m for m in sock.sent_messages() if m.get("type") == "run"]
    assert len(run_msgs) == 1 and run_msgs[0]["request_id"] == "run1"


def test_run_container_death_marks_dead():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        client.containers_made[0].sock._sock.feed_eof()  # dies mid-run

        async def on_event(e):
            pass

        with pytest.raises(RuntimeError):
            await worker.run("run1", graph={}, cache=None, targets=None,
                             workflow_modules=[], on_event=on_event)
        return worker

    worker = asyncio.run(scenario())
    assert worker.dead


def test_run_timeout_marks_dead(monkeypatch):
    monkeypatch.setattr(settings, "workflow_run_timeout_seconds", 0.2)
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        # feed nothing after ready: recv times out

        async def on_event(e):
            pass

        with pytest.raises(RuntimeError, match="read failed"):
            await worker.run("run1", graph={}, cache=None, targets=None,
                             workflow_modules=[], on_event=on_event)
        return worker

    worker = asyncio.run(scenario())
    assert worker.dead


def test_run_runtime_error_event_is_dirty():
    """A terminal {"type":"error"} (unrecoverable runtime failure) surfaces as
    run_error and the container is not reusable."""
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock
        sock.feed({"type": "error", "error": "import explosion"})
        events = []

        async def on_event(e):
            events.append(e)

        status = await worker.run("run1", graph={}, cache=None, targets=None,
                                  workflow_modules=[], on_event=on_event)
        return status, events, worker

    status, events, worker = asyncio.run(scenario())
    assert status == "error"
    assert events[0]["type"] == "run_error" and "import explosion" in events[0]["error"]
    assert worker.dead  # no result event → never pooled again
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -k run -v`
Expected: FAIL — `AttributeError: 'SandboxWorker' object has no attribute 'run'`

- [x] **Step 3: Implement `run` on `SandboxWorker`**

```python
    async def run(
        self,
        run_id: str,
        *,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event,
        subworkflow_resolver=None,
        subworkflow_meta: dict | None = None,
        pause_on_approval: bool = False,
        agent_action_resume: dict | None = None,
        run_timeout: float | None = None,
    ) -> str:
        """Execute one run on this container. Raises on transport failure
        (caller surfaces run_error); a missing ``result`` event marks the
        worker dead so the pool never reuses it."""
        loop = asyncio.get_running_loop()
        timeout = (
            run_timeout if (run_timeout and run_timeout > 0)
            else (settings.workflow_run_timeout_seconds or 3600.0)
        )
        await loop.run_in_executor(None, self._sock._sock.settimeout, timeout)
        await self._send({
            "type": "run",
            "request_id": run_id,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
            "pause_on_approval": pause_on_approval,
            "agent_action_resume": agent_action_resume or {},
            "subworkflow_meta": subworkflow_meta or {},
        }, loop)

        callbacks: set[asyncio.Task] = set()
        status = "error"
        clean = False
        try:
            while True:
                event = await self._read_event(loop)
                etype = event.get("type")
                if etype == "call_workflow":
                    task = asyncio.create_task(
                        self._handle_call_workflow(event, subworkflow_resolver, loop)
                    )
                    callbacks.add(task)
                    task.add_done_callback(callbacks.discard)
                elif etype in _FORWARDED_EVENTS:
                    await on_event(event)
                elif etype == "result":
                    status = str(event.get("status", "error"))
                    clean = True
                    break
                elif etype == "error":
                    await on_event({
                        "type": "run_error",
                        "error": str(event.get("error", "runtime failure")),
                    })
                    break
                # unknown event types are ignored (forward-compat)
        finally:
            self.runs_completed += 1
            if not clean:
                self.dead = True
            for task in callbacks:
                task.cancel()
        return status

    async def _handle_call_workflow(self, event: dict, subworkflow_resolver,
                                    loop: asyncio.AbstractEventLoop) -> None:
        """Mirror of runtime_pool._handle_call_workflow over the attach socket."""
        from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowCall

        callback_id = event.get("callback_id", "")
        try:
            if subworkflow_resolver is None:
                raise RuntimeError("sandbox runner has no host-side sub-workflow resolver")
            call = SubworkflowCall.from_payload(
                {**event, "input": deserialize_value(event.get("input"))}
            )
            outcome = await subworkflow_resolver(call, parent_env_id=self.key[1])
            if isinstance(outcome, InlineSubworkflow):
                await self._send({
                    "type": "call_workflow_response",
                    "callback_id": callback_id,
                    "inline_graph": outcome.graph,
                    "inline_cache": outcome.cache,
                    "inline_targets": outcome.targets,
                    "inline_sources": list(outcome.sources),
                }, loop)
            else:
                await self._send({
                    "type": "call_workflow_response",
                    "callback_id": callback_id,
                    "result": outcome,
                }, loop)
        except Exception as exc:  # noqa: BLE001 — surface back into the run
            try:
                await self._send({
                    "type": "call_workflow_error",
                    "callback_id": callback_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }, loop)
            except RuntimeError:
                pass  # container died; the read loop reports it
```

- [x] **Step 4: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -v`
Expected: all pass

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/sandbox_pool.py apps/api/tests/test_sandbox_pool.py
git commit -m "feat(sandbox): SandboxWorker.run protocol loop with dirty-exit tracking"
```

---

### Task 9: `call_workflow` bridging test

**Files:**
- Test: `apps/api/tests/test_sandbox_pool.py` (append)

The implementation landed in Task 8; this task proves the bridge end-to-end against the fake socket, including the error reply path.

- [x] **Step 1: Write the tests**

```python
def test_call_workflow_bridged_to_resolver():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock
        resolved = asyncio.Event()

        async def resolver(call, parent_env_id=None):
            assert parent_env_id == "env1"
            resolved.set()
            return {"answer": 42}

        async def on_event(e):
            pass

        run_task = asyncio.create_task(worker.run(
            "run1", graph={}, cache=None, targets=None, workflow_modules=[],
            on_event=on_event, subworkflow_resolver=resolver,
        ))
        sock.feed({"type": "call_workflow", "callback_id": "cb1",
                   "request_id": "run1", "workflow_id": "wf2", "input": None,
                   "depth": 1, "chain": ["wf1"]})
        await asyncio.wait_for(resolved.wait(), 3)
        # wait until the response hits the wire, then finish the run
        for _ in range(50):
            if any(m.get("type") == "call_workflow_response"
                   for m in sock.sent_messages()):
                break
            await asyncio.sleep(0.05)
        sock.feed({"type": "result", "status": "success"})
        status = await run_task
        return status, sock.sent_messages()

    status, messages = asyncio.run(scenario())
    assert status == "success"
    responses = [m for m in messages if m.get("type") == "call_workflow_response"]
    assert responses == [{"type": "call_workflow_response", "callback_id": "cb1",
                          "result": {"answer": 42}}]


def test_call_workflow_resolver_error_replied():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock

        async def resolver(call, parent_env_id=None):
            raise ValueError("no such workflow")

        async def on_event(e):
            pass

        run_task = asyncio.create_task(worker.run(
            "run1", graph={}, cache=None, targets=None, workflow_modules=[],
            on_event=on_event, subworkflow_resolver=resolver,
        ))
        sock.feed({"type": "call_workflow", "callback_id": "cb2",
                   "request_id": "run1", "workflow_id": "missing", "input": None,
                   "depth": 1, "chain": []})
        for _ in range(50):
            if any(m.get("type") == "call_workflow_error"
                   for m in sock.sent_messages()):
                break
            await asyncio.sleep(0.05)
        sock.feed({"type": "result", "status": "error"})
        await run_task
        return sock.sent_messages()

    messages = asyncio.run(scenario())
    errors = [m for m in messages if m.get("type") == "call_workflow_error"]
    assert len(errors) == 1 and "no such workflow" in errors[0]["error"]
```

Note: if `SubworkflowCall.from_payload` requires additional keys, run `grep -n "def from_payload" packages/core/noodle/engine/subworkflows.py`, read the method, and extend the fed `call_workflow` event dicts to match — do NOT change `from_payload`.

- [x] **Step 2: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -k call_workflow -v`
Expected: 2 passed

- [x] **Step 3: Commit**

```bash
git add apps/api/tests/test_sandbox_pool.py
git commit -m "test(sandbox): call_workflow host-callback bridging incl. error replies"
```

---

### Task 10: `SandboxPool` — warm reuse, eviction, recycling

**Files:**
- Modify: `apps/api/app/services/sandbox_pool.py`
- Test: `apps/api/tests/test_sandbox_pool.py` (append)

- [x] **Step 1: Write the failing tests (append)**

```python
from app.services.sandbox_pool import SandboxPool


def _make_pool(client) -> SandboxPool:
    p = SandboxPool()
    p.configure(client, runtime="runc", network="noodle-sandbox")
    return p


def _dispatch(pool, run_id, org="org1", env="env1", **kw):
    async def on_event(e):
        kw.setdefault("_events", []).append(e)

    return pool.dispatch(
        run_id, org_id=org, env_id=env,
        env_payload={"id": env, "packages_hash": "h1",
                     "python_version": "3.12", "packages": []},
        graph={}, cache=None, targets=None, workflow_modules=[],
        on_event=on_event,
    )


def test_warm_reuse_within_key():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1"))
        await asyncio.sleep(0.2)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        assert await t1 == "success"
        # second run, same key: reuses the warm container
        t2 = asyncio.create_task(_dispatch(pool, "r2"))
        await asyncio.sleep(0.2)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        assert await t2 == "success"

    asyncio.run(scenario())
    assert len(client.containers_made) == 1  # one container served both runs


def test_no_cross_key_reuse():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1", org="orgA"))
        await asyncio.sleep(0.2)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        await t1
        t2 = asyncio.create_task(_dispatch(pool, "r2", org="orgB"))  # different org!
        await asyncio.sleep(0.2)
        client.containers_made[1].sock._sock.feed({"type": "result", "status": "success"})
        await t2

    asyncio.run(scenario())
    assert len(client.containers_made) == 2  # orgB never got orgA's container


def test_dirty_exit_not_pooled():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1"))
        await asyncio.sleep(0.2)
        client.containers_made[0].sock._sock.feed_eof()  # crash
        assert await t1 == "error"
        t2 = asyncio.create_task(_dispatch(pool, "r2"))
        await asyncio.sleep(0.2)
        client.containers_made[1].sock._sock.feed({"type": "result", "status": "success"})
        await t2

    asyncio.run(scenario())
    assert client.containers_made[0].removed
    assert len(client.containers_made) == 2


def test_stale_image_not_reused():
    """Env packages changed (new packages_hash) → warm container discarded."""
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1"))
        await asyncio.sleep(0.2)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        await t1

        async def on_event(e):
            pass

        t2 = asyncio.create_task(pool.dispatch(
            "r2", org_id="org1", env_id="env1",
            env_payload={"id": "env1", "packages_hash": "CHANGED",
                         "python_version": "3.12", "packages": []},
            graph={}, cache=None, targets=None, workflow_modules=[],
            on_event=on_event,
        ))
        await asyncio.sleep(0.2)
        client.containers_made[1].sock._sock.feed({"type": "result", "status": "success"})
        await t2

    asyncio.run(scenario())
    assert client.containers_made[0].removed  # stale-image worker destroyed
    assert len(client.containers_made) == 2


def test_recycle_after_max_runs(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max_runs_per_container", 1)
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        for rid in ("r1", "r2"):
            t = asyncio.create_task(_dispatch(pool, rid))
            await asyncio.sleep(0.2)
            client.containers_made[-1].sock._sock.feed({"type": "result", "status": "success"})
            await t

    asyncio.run(scenario())
    assert len(client.containers_made) == 2  # recycled after every run
    assert client.containers_made[0].removed


def test_warm_total_cap_evicts_lru(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_warm_total", 1)
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        for rid, org in (("r1", "orgA"), ("r2", "orgB")):
            t = asyncio.create_task(_dispatch(pool, rid, org=org))
            await asyncio.sleep(0.2)
            client.containers_made[-1].sock._sock.feed({"type": "result", "status": "success"})
            await t

    asyncio.run(scenario())
    # orgA's idle worker was evicted to make room for orgB's
    assert client.containers_made[0].removed
    assert not client.containers_made[1].removed


def test_cancel_force_removes(monkeypatch):
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t = asyncio.create_task(_dispatch(pool, "r1"))
        await asyncio.sleep(0.2)
        assert await pool.cancel("r1") is True
        # the read loop now sees EOF/error from the removed container
        client.containers_made[0].sock._sock.feed_eof()
        status = await t
        assert status == "error"
        assert await pool.cancel("r1") is False  # already gone

    asyncio.run(scenario())
    assert client.containers_made[0].removed


def test_flush_closes_idle():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t = asyncio.create_task(_dispatch(pool, "r1"))
        await asyncio.sleep(0.2)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        await t
        await pool.flush()

    asyncio.run(scenario())
    assert client.containers_made[0].removed
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -k "pool or reuse or dirty or recycle or cap or cancel or flush or stale" -v`
Expected: FAIL — ImportError on `SandboxPool`

- [x] **Step 3: Implement `SandboxPool` in `sandbox_pool.py`**

```python
class SandboxPool:
    """Bounded warm pool of SandboxWorkers keyed by (org_id, environment_id)."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._runtime: str = "runc"
        self._network: str = ""
        self._idle: dict[tuple[str | None, str | None], list[SandboxWorker]] = {}
        self._active: dict[str, SandboxWorker] = {}  # run_id -> worker
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    def configure(self, client: Any, *, runtime: str, network: str) -> None:
        self._client = client
        self._runtime = runtime
        self._network = network

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def describe(self) -> str:
        if not self.enabled:
            return "inactive"
        idle = sum(len(v) for v in self._idle.values())
        return f"runtime={self._runtime} idle={idle} active={len(self._active)}"

    async def dispatch(self, run_id: str, *, org_id: str | None, env_id: str | None,
                       env_payload: dict, graph: dict, cache: dict | None,
                       targets: list[str] | None, workflow_modules: list[dict],
                       on_event, subworkflow_resolver=None,
                       subworkflow_meta: dict | None = None,
                       run_timeout: float | None = None,
                       pause_on_approval: bool = False,
                       agent_action_resume: dict | None = None) -> str:
        if self._client is None:
            raise RuntimeError("sandbox pool is not configured")
        self._ensure_reaper()
        key = (org_id, env_id)
        worker = await self._acquire(key, env_payload)
        self._active[run_id] = worker
        try:
            status = await worker.run(
                run_id, graph=graph, cache=cache, targets=targets,
                workflow_modules=workflow_modules, on_event=on_event,
                subworkflow_resolver=subworkflow_resolver,
                subworkflow_meta=subworkflow_meta,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
                run_timeout=run_timeout,
            )
        except asyncio.CancelledError:
            await worker.close()  # cancelled task ⇒ hard-kill the container
            raise
        except Exception as exc:  # noqa: BLE001 — transport/spawn failure
            logger.exception("sandbox run failed run_id=%s: %s", run_id, exc)
            await on_event({"type": "run_error", "error": str(exc)})
            status = "error"
        finally:
            self._active.pop(run_id, None)
            await self._release(worker)
        return status

    async def cancel(self, run_id: str) -> bool:
        worker = self._active.get(run_id)
        if worker is None:
            return False
        await worker.close()  # read loop sees EOF; dispatch's finally cleans up
        return True

    async def flush(self) -> None:
        async with self._lock:
            workers = [w for lst in self._idle.values() for w in lst]
            self._idle.clear()
        for w in workers:
            await w.close()

    async def _acquire(self, key, env_payload: dict) -> SandboxWorker:
        wanted_tag = image_tag_for(env_payload)
        stale: list[SandboxWorker] = []
        worker: SandboxWorker | None = None
        async with self._lock:
            bucket = self._idle.get(key) or []
            while bucket:
                candidate = bucket.pop()
                if candidate.dead or candidate.image_tag != wanted_tag:
                    stale.append(candidate)
                else:
                    worker = candidate
                    break
            if not bucket:
                self._idle.pop(key, None)
        for s in stale:
            await s.close()
        if worker is not None:
            return worker
        return await SandboxWorker.spawn(
            self._client, key=key, env_payload=env_payload,
            runtime=self._runtime, network=self._network,
        )

    async def _release(self, worker: SandboxWorker) -> None:
        if worker.dead or worker.runs_completed >= settings.sandbox_max_runs_per_container:
            await worker.close()
            return
        evicted: list[SandboxWorker] = []
        async with self._lock:
            bucket = self._idle.setdefault(worker.key, [])
            if len(bucket) >= settings.sandbox_warm_per_key:
                await_close = bucket.pop(0)
                evicted.append(await_close)
            worker.idle_since = time.monotonic()
            bucket.append(worker)
            total = sum(len(v) for v in self._idle.values())
            while total > settings.sandbox_warm_total:
                lru_key = min(
                    (k for k, v in self._idle.items() if v),
                    key=lambda k: self._idle[k][0].idle_since,
                )
                evicted.append(self._idle[lru_key].pop(0))
                if not self._idle[lru_key]:
                    self._idle.pop(lru_key)
                total -= 1
        for w in evicted:
            await w.close()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_idle())

    async def _reap_idle(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                cutoff = time.monotonic() - settings.sandbox_warm_ttl_seconds
                expired: list[SandboxWorker] = []
                async with self._lock:
                    for key in list(self._idle):
                        keep = [w for w in self._idle[key] if w.idle_since >= cutoff]
                        expired.extend(w for w in self._idle[key] if w.idle_since < cutoff)
                        if keep:
                            self._idle[key] = keep
                        else:
                            self._idle.pop(key)
                for w in expired:
                    await w.close()
        except asyncio.CancelledError:
            return


# Module-level singleton, mirroring runtime_pool's pattern.
pool = SandboxPool()
```

- [x] **Step 4: Run the whole sandbox suite**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py tests/test_sandbox_container_runtime.py tests/test_sandbox_policy.py -v`
Expected: all pass

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/sandbox_pool.py apps/api/tests/test_sandbox_pool.py
git commit -m "feat(sandbox): warm SandboxPool with per-key reuse, LRU/TTL eviction, recycle, cancel"
```

---

### Task 11: `init_sandbox` startup probe (off / auto / required)

**Files:**
- Modify: `apps/api/app/services/sandbox_pool.py`
- Test: `apps/api/tests/test_sandbox_pool.py` (append)

- [x] **Step 1: Write the failing tests (append)**

```python
from app.services import sandbox_pool as sp


def test_init_off_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)
    assert asyncio.run(sp.init_sandbox()) is None
    assert not fresh.enabled


def test_init_auto_falls_back_without_daemon(monkeypatch, caplog):
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)

    def no_daemon():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sp, "_make_docker_client", no_daemon)
    assert asyncio.run(sp.init_sandbox()) is None
    assert not fresh.enabled
    assert any("falling back" in r.message for r in caplog.records)


def test_init_required_raises_without_daemon(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)

    def no_daemon():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sp, "_make_docker_client", no_daemon)
    with pytest.raises(RuntimeError, match="execution_sandbox=required"):
        asyncio.run(sp.init_sandbox())


def test_init_required_with_daemon(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    monkeypatch.setattr(settings, "sandbox_runtime", "auto")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)
    client = FakeDockerClient(runtimes=("runc", "runsc"))
    monkeypatch.setattr(sp, "_make_docker_client", lambda: client)
    assert asyncio.run(sp.init_sandbox()) == "runsc"
    assert fresh.enabled
    assert "noodle-sandbox" in client.networks.existing
```

- [x] **Step 2: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -k init -v`
Expected: FAIL — no `init_sandbox`

- [x] **Step 3: Implement in `sandbox_pool.py`**

```python
def _make_docker_client() -> Any:
    """Construct the SDK client (separated for test monkeypatching)."""
    import docker  # noqa: PLC0415 — optional dependency, sandbox-mode only

    if settings.sandbox_docker_host:
        return docker.DockerClient(base_url=settings.sandbox_docker_host)
    return docker.from_env()


async def init_sandbox() -> str | None:
    """Probe the daemon per execution_sandbox mode; configure the pool.

    Returns the active isolation runtime, or None when the sandbox is off /
    unavailable-in-auto. Call once from worker_main and the API lifespan
    (after enforce_sandbox_policy)."""
    from app.services.container_runtime import detect_runtime, ensure_sandbox_network

    mode = settings.execution_sandbox
    if mode == "off":
        return None
    loop = asyncio.get_running_loop()
    try:
        client = await loop.run_in_executor(None, _make_docker_client)
        await loop.run_in_executor(None, client.ping)
        runtime = await loop.run_in_executor(
            None, detect_runtime, client, settings.sandbox_runtime
        )
        network = await loop.run_in_executor(None, ensure_sandbox_network, client)
    except Exception as exc:
        if mode == "required":
            raise RuntimeError(
                f"execution_sandbox=required but no usable Docker daemon/"
                f"runtime: {exc}"
            ) from exc
        logger.warning(
            "execution_sandbox=auto: no usable Docker daemon (%s); "
            "falling back to the subprocess runner", exc,
        )
        return None
    pool.configure(client, runtime=runtime, network=network)
    logger.info("sandbox execution active: runtime=%s network=%s", runtime, network)
    return runtime
```

- [x] **Step 4: Run tests**

Run: `cd apps/api && python -m pytest tests/test_sandbox_pool.py -v`
Expected: all pass

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/sandbox_pool.py apps/api/tests/test_sandbox_pool.py
git commit -m "feat(sandbox): init_sandbox startup probe with auto-fallback and required fail-fast"
```

---

### Task 12: `SandboxExecutor` + runner wiring

**Files:**
- Create: `apps/api/app/services/executors/sandbox.py`
- Modify: `apps/api/app/services/executors/base.py` (add `org_id` to the ctx)
- Modify: `apps/api/app/services/runner.py` (executor instantiation + dispatch fork)
- Test: `apps/api/tests/test_executors.py` (append)

- [x] **Step 1: Add `org_id` to `RunExecutionContext` in `executors/base.py`**

```python
class RunExecutionContext(TypedDict):
    run_id: str
    workflow_id: str
    org_id: str | None               # sandbox pool key; None single-tenant
    graph: dict                      # credential refs already resolved
    ...
```

Then find `_build_ctx` in `runner.py` (`grep -n "_build_ctx" apps/api/app/services/runner.py`) and add an `org_id: str | None = None` parameter that flows into the dict; every existing call site keeps working via the default.

- [x] **Step 2: Write the failing executor test (append to `tests/test_executors.py`, following its FakePool style)**

```python
def test_sandbox_executor_delegates_and_cancels():
    from app.services.executors.sandbox import SandboxExecutor

    calls = {}

    class FakeSandboxPool:
        enabled = True

        async def dispatch(self, run_id, **kw):
            calls["run_id"] = run_id
            calls["org_id"] = kw["org_id"]
            calls["env_id"] = kw["env_id"]
            calls["resolver"] = kw["subworkflow_resolver"]
            return "success"

        async def cancel(self, run_id):
            calls["cancelled"] = run_id
            return True

    async def resolver(call, parent_env_id=None):
        return None

    ex = SandboxExecutor(pool=FakeSandboxPool(), subworkflow_resolver=resolver)
    ctx = {
        "run_id": "r1", "workflow_id": "wf1", "org_id": "org9",
        "graph": {}, "cache": None, "targets": None,
        "environment_id": "env5", "runner_pool_id": None,
        "env_payload": {"id": "env5"}, "workflow_modules": [],
        "run_timeout": None, "default_timeouts": {},
        "pause_on_approval": False, "agent_action_resume": None,
        "subworkflow_meta": None,
    }

    async def on_event(e):
        pass

    outcome = asyncio.run(ex.execute(ctx, on_event))
    assert outcome.status == "success"
    assert calls["org_id"] == "org9" and calls["env_id"] == "env5"
    assert calls["resolver"] is resolver
    assert asyncio.run(ex.cancel("r1")) is True
    assert calls["cancelled"] == "r1"
```

- [x] **Step 3: Run to verify failure**

Run: `cd apps/api && python -m pytest tests/test_executors.py -k sandbox -v`
Expected: FAIL — ModuleNotFoundError

- [x] **Step 4: Create `apps/api/app/services/executors/sandbox.py`**

```python
"""Sandbox execution: disposable hardened container via the SandboxPool."""

from __future__ import annotations

from typing import Any

from app.services.executors.base import (
    EventCallback,
    RunExecutionContext,
    RunOutcome,
)


class SandboxExecutor:
    """Adapter over ``sandbox_pool.pool`` — the per-(org, env) warm
    container pool. No DB access, mirroring LocalExecutor."""

    def __init__(self, *, pool: Any, subworkflow_resolver: Any) -> None:
        self._pool = pool
        self._subworkflow_resolver = subworkflow_resolver

    @property
    def active(self) -> bool:
        return bool(self._pool.enabled)

    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome:
        status = await self._pool.dispatch(
            ctx["run_id"],
            org_id=ctx.get("org_id"),
            env_id=ctx["environment_id"],
            env_payload=ctx["env_payload"] or {},
            graph=ctx["graph"],
            cache=ctx["cache"],
            targets=ctx["targets"],
            workflow_modules=ctx["workflow_modules"],
            on_event=on_event,
            subworkflow_resolver=self._subworkflow_resolver,
            subworkflow_meta=ctx.get("subworkflow_meta"),
            run_timeout=ctx["run_timeout"],
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
        return RunOutcome(status=str(status))

    async def cancel(self, run_id: str) -> bool:
        return await self._pool.cancel(run_id)
```

- [x] **Step 5: Wire into `runner.py`**

Next to `local_executor` (~line 170):

```python
from app.services import sandbox_pool
from app.services.executors.sandbox import SandboxExecutor

sandbox_executor = SandboxExecutor(
    pool=sandbox_pool.pool,
    subworkflow_resolver=resolve_subworkflow,
)
```

In `_execute_run_impl`, change the dispatch fork (~line 815, `if settings.use_subprocess_runner:`). The current `else:` branch under `if runner_pool_id:` becomes a three-way fork:

```python
        if settings.use_subprocess_runner:
            if runner_pool_id:
                # ... existing remote path unchanged ...
            elif sandbox_pool.pool.enabled:
                # Sandbox path: same prepared inputs as local, but the run
                # executes in a disposable hardened container keyed by
                # (org, env). env_payload drives the per-env image.
                env_payload = await _build_env_payload_for_run(env_id)
                outcome = await sandbox_executor.execute(
                    _build_ctx(
                        run_id=run_id, workflow_id=workflow_id,
                        org_id=trace_org,
                        graph=graph_dict, cache=cache, targets=targets,
                        environment_id=env_id, runner_pool_id=None,
                        env_payload=env_payload,
                        workflow_modules=workflow_modules,
                        run_timeout=run_timeout,
                        agent_action_resume=agent_action_resume,
                        subworkflow_meta=sub_meta.to_payload(),
                    ),
                    on_event,
                )
                status = outcome.status
            else:
                # ... existing local path unchanged ...
```

`trace_org` is the org id `_execute_run` already resolves (line ~625) and passes in as the `trace_org` parameter — confirm the parameter name with `grep -n "trace_org" apps/api/app/services/runner.py` and reuse it.

Also wire cancellation: find `cancel_run` in runner.py (`grep -n "async def cancel_run" apps/api/app/services/runner.py`). The local path cancels the asyncio task, which now propagates `CancelledError` into `pool.dispatch`, which hard-kills the container (Task 10) — so task-cancel already works. Add a direct fallback right after the task-cancel attempt for robustness:

```python
    if await sandbox_pool.pool.cancel(run_id):
        return True
```

(placed so it runs only if the task-based cancel didn't already return).

- [x] **Step 6: Run executor + runner suites**

Run: `cd apps/api && python -m pytest tests/test_executors.py tests/test_runs.py -v`
Expected: all pass — with sandbox off and `pool.enabled` False, every existing test takes the unchanged local path.

- [x] **Step 7: Commit**

```bash
git add apps/api/app/services/executors/sandbox.py apps/api/app/services/executors/base.py apps/api/app/services/runner.py apps/api/tests/test_executors.py
git commit -m "feat(sandbox): SandboxExecutor wired into the run dispatch fork"
```

---

### Task 13: Startup wiring + health visibility

**Files:**
- Modify: `apps/api/app/main.py` (lifespan startup)
- Modify: `apps/api/app/worker_main.py` (startup)
- Modify: `apps/api/app/routers/health.py`
- Test: `apps/api/tests/test_sandbox_policy.py` (append)

- [x] **Step 1: Locate the startup seams**

Run: `grep -n "lifespan\|async def.*startup\|ENABLE_INPROCESS" apps/api/app/main.py | head` and `grep -n "def main\|async def\|run_forever\|asyncio.run" apps/api/app/worker_main.py`. Identify where each process finishes config/DB setup and before it starts accepting work.

- [x] **Step 2: Add to both startup paths**

In `main.py` lifespan startup (after DB/redis init) and in `worker_main`'s async startup, insert:

```python
    from app.services.sandbox_policy import enforce_sandbox_policy
    from app.services.sandbox_pool import init_sandbox

    enforce_sandbox_policy()
    await init_sandbox()
```

In `main.py`, only when this process can dispatch runs — guard with the same condition the dispatcher uses (`settings.dispatch_role != "disabled"` or equivalent; check with `grep -n "dispatch_role" apps/api/app/main.py apps/api/app/config.py`). `enforce_sandbox_policy()` runs unconditionally (it's a config-validity check). In `worker_main` both run unconditionally.

In `main.py` lifespan **shutdown**, and worker_main's shutdown path, add:

```python
    from app.services.sandbox_pool import pool as _sandbox_pool
    await _sandbox_pool.flush()
```

- [x] **Step 3: Extend `/health/ready` in `routers/health.py`**

After the redis check, add (non-fatal — auto mode running on subprocess is healthy):

```python
    from app.config import settings
    from app.services.sandbox_pool import pool as sandbox_pool

    if settings.execution_sandbox != "off":
        checks["sandbox"] = sandbox_pool.describe()
        if settings.execution_sandbox == "required" and not sandbox_pool.enabled:
            healthy = False
            checks["sandbox"] = "error: required but inactive"
```

- [x] **Step 4: Write tests (append to `tests/test_sandbox_policy.py`)**

```python
import asyncio


def test_health_reports_sandbox_state(monkeypatch):
    from fastapi.testclient import TestClient  # or the project's client fixture style
    # Simplest: call the route function directly.
    from app.routers import health
    from app.services import sandbox_pool as sp

    monkeypatch.setattr(settings, "execution_sandbox", "required")
    fresh = sp.SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)
    resp = asyncio.run(health.ready())
    assert resp.status_code == 503  # required but pool never initialized

    monkeypatch.setattr(settings, "execution_sandbox", "off")
    resp = asyncio.run(health.ready())
    import json as _json
    body = _json.loads(resp.body)
    assert "sandbox" not in body["checks"]
```

Note: `health.ready()` touches the DB and redis; in the test environment those checks may report errors — assert only on the `sandbox` key and on 503-when-required-inactive. If the DB check already forces 503 in tests, assert `checks["sandbox"] == "error: required but inactive"` instead of the status code.

- [x] **Step 5: Run tests + a broad smoke**

Run: `cd apps/api && python -m pytest tests/test_sandbox_policy.py tests/test_runs.py -q`
Expected: pass. Then the full suite: `cd apps/api && python -m pytest tests/ -q -x --timeout=300` — expected: same results as before this branch (no new failures; sandbox defaults off).

- [x] **Step 6: Commit**

```bash
git add apps/api/app/main.py apps/api/app/worker_main.py apps/api/app/routers/health.py apps/api/tests/test_sandbox_policy.py
git commit -m "feat(sandbox): startup policy+probe wiring, pool flush on shutdown, health visibility"
```

---

### Task 14: Compose, worker image dependency, deployment docs

**Files:**
- Modify: `deploy/docker-compose.yml` (worker service)
- Modify: `deploy/Dockerfile.python` (only if `docker` pkg missing)
- Modify: `docs/deployment.md`

- [x] **Step 1: Ensure the worker image has the docker SDK**

Run: `grep -rn "docker" deploy/Dockerfile.python apps/api/pyproject.toml pyproject.toml | grep -iv dockerfile`. If the `docker` Python package is not an api dependency, add it to the api project's dependencies (it is already required by the docker runner-pool provider, so it likely belongs in the main dependency list — match however `redis`/optional deps are declared there).

- [x] **Step 2: Add the commented sandbox variant to `deploy/docker-compose.yml`** under the `worker` service's `environment:` block:

```yaml
      # --- Sandboxed execution (hardened / multi-tenant) ------------------
      # Run each workflow in a disposable hardened container on the HOST
      # Docker daemon instead of a subprocess inside this container.
      # 1. Uncomment the two env vars and the docker.sock volume below.
      # 2. Linux hosts with gVisor installed are picked up automatically
      #    (SANDBOX_RUNTIME=auto probes kata > runsc > runc).
      # Required (enforced at startup) when MULTI_TENANCY_ENABLED=true.
      # NOTE: the socket grants this container root-equivalent control of
      # the host daemon — acceptable because tenant code no longer executes
      # in this container; that is the point. See docs/deployment.md.
      # EXECUTION_SANDBOX: required
      # SANDBOX_RUNTIME: auto
```

and under the worker's `volumes:`:

```yaml
      # - /var/run/docker.sock:/var/run/docker.sock
```

- [x] **Step 3: Document in `docs/deployment.md`**

Add a `## Sandboxed execution` section covering: what it is (per-run hardened containers, warm-pooled per org+env); the three modes (`off`/`auto`/`required`) and that MT forces `required`; the tier table (runc everywhere incl. Docker Desktop Win/mac, runsc on Linux/WSL2 with gVisor installed + install pointer to gvisor.dev/docs/user_guide/install, kata future); the compose socket-mount recipe from Step 2; resource-limit settings (`SANDBOX_MEM_LIMIT` etc.); the dedicated `noodle-sandbox` network and the note that stricter egress filtering is operator-supplied via `SANDBOX_NETWORK`; and the K8s direction (Jobs + runtimeClassName, follow-up slice). Keep it ~60 lines, same tone as the file's existing sections.

- [x] **Step 4: Validate compose syntax**

Run: `docker compose -f deploy/docker-compose.yml config -q` (if docker is available locally; otherwise `python -c "import yaml; yaml.safe_load(open('deploy/docker-compose.yml'))"`)
Expected: no errors

- [x] **Step 5: Commit**

```bash
git add deploy/docker-compose.yml deploy/Dockerfile.python docs/deployment.md apps/api/pyproject.toml pyproject.toml
git commit -m "docs(sandbox): compose sandbox variant, worker docker-sdk dep, deployment guide"
```

---

### Task 15: End-to-end integration test (env-gated) + final verification

**Files:**
- Create: `apps/api/tests/test_sandbox_integration.py`

- [x] **Step 1: Write the gated integration test**

```python
"""Real-daemon sandbox integration. Skipped unless NOODLE_SANDBOX_IT=1.

Run manually on a machine with Docker:
    NOODLE_SANDBOX_IT=1 python -m pytest tests/test_sandbox_integration.py -v
Builds a real noodle-env image on first run (slow); subsequent runs reuse it.
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NOODLE_SANDBOX_IT") != "1",
    reason="set NOODLE_SANDBOX_IT=1 to run sandbox integration tests",
)


def test_run_executes_in_real_container(monkeypatch):
    from app.config import settings
    from app.services import sandbox_pool as sp

    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    fresh = sp.SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)

    async def scenario():
        runtime = await sp.init_sandbox()
        assert runtime is not None, "no Docker daemon — cannot run integration test"
        events = []

        async def on_event(e):
            events.append(e)

        # Minimal graph: one code node returning a value. Mirror the graph
        # shape used by tests in tests/test_runs.py (copy a passing minimal
        # graph fixture from there).
        graph = {
            "nodes": [{
                "id": "n1", "type": "code",
                "params": {"code": "def main():\n    return {'ok': True}"},
            }],
            "edges": [],
        }
        status = await fresh.dispatch(
            "it-run-1", org_id="it-org", env_id=None,
            env_payload={"id": "default", "packages_hash": "it",
                         "python_version": "3.12", "packages": []},
            graph=graph, cache=None, targets=None, workflow_modules=[],
            on_event=on_event,
        )
        # warm reuse: second run, same key, must not build a new container
        status2 = await fresh.dispatch(
            "it-run-2", org_id="it-org", env_id=None,
            env_payload={"id": "default", "packages_hash": "it",
                         "python_version": "3.12", "packages": []},
            graph=graph, cache=None, targets=None, workflow_modules=[],
            on_event=on_event,
        )
        await fresh.flush()
        return status, status2, events

    status, status2, events = asyncio.run(scenario())
    assert status == "success", f"events: {events}"
    assert status2 == "success"
    assert any(e["type"] == "node_finished" for e in events)
```

Adjust the graph dict to whatever minimal shape `tests/test_runs.py` uses for a passing single-node run (`grep -n "nodes.*code\|def.*graph" apps/api/tests/test_runs.py | head`) — the engine's node param schema is authoritative, not this plan.

- [x] **Step 2: Run gated (skipped) in CI mode**

Run: `cd apps/api && python -m pytest tests/test_sandbox_integration.py -v`
Expected: 1 skipped

- [x] **Step 3: If a Docker daemon is available on this machine, run for real**

Run: `cd apps/api && NOODLE_SANDBOX_IT=1 python -m pytest tests/test_sandbox_integration.py -v` (PowerShell: `$env:NOODLE_SANDBOX_IT="1"; python -m pytest tests/test_sandbox_integration.py -v`)
Expected: 1 passed (first run is slow — image build). If it fails, debug with `docker logs <noodle-sbx-...>` before changing code.

- [x] **Step 4: Full-suite verification**

Run: `cd apps/api && python -m pytest tests/ -q`
Expected: zero new failures vs. the branch baseline.

- [x] **Step 5: Commit**

```bash
git add apps/api/tests/test_sandbox_integration.py
git commit -m "test(sandbox): env-gated real-daemon integration test incl. warm reuse"
```

---

## Edge cases covered (verification map)

| Edge case | Covered by |
|---|---|
| MT on + sandbox off → refuse startup | Task 1 policy tests |
| Trusted-tenant escape hatch (`sandbox_policy_strict=false`) | Task 1 |
| Stale v1 (root) images after Dockerfile change | Task 3 `IMAGE_SCHEMA_VERSION` tag bump |
| Malicious package specifier → shell injection at build | Task 3 (RD-2 validation preserved) |
| Pinned runtime not installed on daemon | Task 4 fail-fast |
| Daemon omits `Runtimes` from info() (Docker Desktop) | Task 4 |
| Resource overrides can't strip security floor | Task 5 |
| Two workers racing to create the network | Task 5 |
| Read-only rootfs breaking HOME writes | Task 5 (`HOME=/tmp` on tmpfs) |
| Runner-pool docker provider unhardened / double run-send | Task 6 |
| Container never says ready (bad image, slow daemon) | Task 7 ready timeout |
| Container dies before ready | Task 7 |
| Container dies mid-run (EOF) | Task 8 → dirty, removed |
| Run wall-clock timeout | Task 8 (socket timeout → dead) |
| Runtime unrecoverable `error` event | Task 8 (no result → dirty) |
| Node stdout noise / partial JSON on protocol stream | Task 8 `_read_event` line buffering |
| Sub-workflow callback inside container | Tasks 8–9 (`call_workflow` bridge incl. error reply) |
| Cross-tenant container reuse | Task 10 (keying + explicit test) |
| Env packages changed while container warm | Task 10 stale-image test |
| Unbounded container accumulation | Task 10 (per-key cap, global LRU cap, TTL reaper, run-count recycle) |
| Cancel mid-run | Task 10 (`cancel` force-remove; `CancelledError` → hard-kill) |
| Worker shutdown leaking warm containers | Task 13 (`flush()` on shutdown) |
| `required` but daemon down | Task 11 fail-fast; Task 13 health 503 |
| `auto` without daemon (bare-metal self-host) | Task 11 warn + subprocess fallback |
| Concurrent runs, same tenant | Pool acquire pops idle (exclusive); second run spawns fresh — admission already bounded by `max_concurrent_runs` |
| Tenant code reaching postgres/redis/minio | Task 5 dedicated bridge network (egress filtering documented as operator-supplied, Task 14) |
| Secrets leaking into containers | No env vars beyond `HOME=/tmp`; credentials travel in the run message exactly like the existing docker provider |

## Explicitly deferred (matches spec non-goals)

- Kubernetes Jobs + RuntimeClass executor (next slice; seam is ready)
- Kata/Firecracker tier activation (config value `kata` already accepted by the probe)
- iptables/egress filtering inside the sandbox network
- Per-org resource limits sourced from `org_limits`
- License-gating `multi_tenancy_enabled` (licensing project)
