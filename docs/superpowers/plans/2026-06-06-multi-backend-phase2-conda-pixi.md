# Multi-Backend Phase 2: conda + pixi + system_requirements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conda and pixi environment backends (with auto-download of micromamba/pixi binaries) and `system_requirements` on `@node` for guiding users on OS-level dependencies.

**Architecture:** Extract the existing venv build logic into a `backends/` package with a `get_backend()` dispatcher, then add `CondaBackend` and `PixiBackend` alongside `VenvBackend`. The old `venv.py` becomes a thin shim so existing imports keep working. `ensure_tool()` auto-downloads static micromamba/pixi binaries on first use.

**Tech Stack:** Python `asyncio`, `httpx` (already in deps), `pathlib`, Pydantic, React/TypeScript.

---

## File Map

**Create:**
- `apps/api/app/services/backends/__init__.py` — dispatcher + `build_environment()` orchestrator
- `apps/api/app/services/backends/base.py` — `EnvironmentBackend` Protocol, shared `venv_dir()`, `_run()`
- `apps/api/app/services/backends/venv.py` — `VenvBackend` class (logic extracted from `venv.py`)
- `apps/api/app/services/backends/tools.py` — `ensure_tool("micromamba"|"pixi")` + `TOOLS_DIR`
- `apps/api/app/services/backends/conda.py` — `CondaBackend`
- `apps/api/app/services/backends/pixi.py` — `PixiBackend`, `_write_pixi_toml()`, `_split_packages()`, `_current_platform()`
- `apps/api/tests/test_backends.py` — dispatcher + all backend unit tests
- `apps/api/tests/test_ensure_tool.py` — `ensure_tool` unit tests
- `packages/nodes/tests/test_system_requirements.py` — node system_requirements round-trip tests

**Modify:**
- `packages/core/noodle/models.py` — add `SystemRequirement` model, `system_requirements` field on `NodeManifest`
- `packages/core/noodle/sdk.py` — `_build_manifest()` + `node()` accept `system_requirements`
- `apps/api/app/services/venv.py` — replace body with thin shim re-exporting from `backends`
- `apps/api/app/routers/environments.py` — update import; dynamic `list_backends()` with version detection
- `apps/web/src/types.ts` — add `SystemRequirement` interface, `system_requirements` on `NodeManifest`
- `apps/web/src/editor/NodeDetails.tsx` — render system_requirements section (context-aware per backend)
- `apps/web/src/editor/NodeCard.tsx` — orange dot badge when `system_requirements` non-empty
- `apps/web/src/EnvironmentsPage.tsx` — backend badge on env cards + backend selector tabs in create dialog
- `apps/web/src/PackageDrawer.tsx` — channels field for conda/pixi environments

---

## Task 1: `SystemRequirement` model + `@node` extension

**Files:**
- Modify: `packages/core/noodle/models.py`
- Modify: `packages/core/noodle/sdk.py`
- Create: `packages/nodes/tests/test_system_requirements.py`
- Modify: `apps/web/src/types.ts`

- [ ] **Step 1: Write the failing tests**

Create `packages/nodes/tests/test_system_requirements.py`:

```python
"""Tests for system_requirements on @node manifests."""
from __future__ import annotations
import noodle_nodes  # noqa: F401


def test_node_with_system_requirements_manifest() -> None:
    from noodle.sdk import node, registry as _r
    from noodle.sdk import NodeRegistry

    _reg = NodeRegistry()

    @node(
        name="TestSysReq",
        id="test_sysreq_node_phase2",
        registry=_reg,
        system_requirements=[
            {
                "name": "ghostscript",
                "apt": "ghostscript",
                "brew": "ghostscript",
                "windows": "https://ghostscript.com/releases",
                "dockerfile_hint": "RUN apt-get install -y ghostscript",
            }
        ],
    )
    def test_fn(input=None):
        return {}

    manifest = _reg.get("test_sysreq_node_phase2").manifest
    assert len(manifest.system_requirements) == 1
    sr = manifest.system_requirements[0]
    assert sr.name == "ghostscript"
    assert sr.apt == "ghostscript"
    assert sr.brew == "ghostscript"
    assert "ghostscript.com" in sr.windows
    assert "apt-get" in sr.dockerfile_hint


def test_node_without_system_requirements_defaults_to_empty() -> None:
    from noodle.sdk import node, NodeRegistry

    _reg = NodeRegistry()

    @node(name="TestNoSysReq", id="test_nosysreq_phase2", registry=_reg)
    def test_fn2(input=None):
        return {}

    manifest = _reg.get("test_nosysreq_phase2").manifest
    assert manifest.system_requirements == []


def test_system_requirement_all_fields_optional_except_name() -> None:
    from noodle.models import SystemRequirement
    sr = SystemRequirement(name="libssl")
    assert sr.name == "libssl"
    assert sr.apt == ""
    assert sr.brew == ""
    assert sr.windows == ""
    assert sr.dockerfile_hint == ""
    assert sr.note == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && uv run pytest tests/test_system_requirements.py -v
```
Expected: FAIL — `SystemRequirement` not found, `node()` doesn't accept `system_requirements`.

- [ ] **Step 3: Add `SystemRequirement` to `models.py`**

In `packages/core/noodle/models.py`, add after the `CredentialSpec` class (line ~33) and before `ParamSpec`:

```python
class SystemRequirement(BaseModel):
    """An OS-level dependency a node needs (e.g. ghostscript, libzbar)."""

    name: str
    apt: str = ""
    brew: str = ""
    windows: str = ""
    dockerfile_hint: str = ""
    note: str = ""
```

Then in `NodeManifest` (line ~130), add after `requirements`:
```python
    system_requirements: list[SystemRequirement] = Field(default_factory=list)
```

- [ ] **Step 4: Update `_build_manifest()` in `sdk.py`**

In `packages/core/noodle/sdk.py`:

1. At the top, import `SystemRequirement`:
```python
from noodle.models import CredentialSpec, NodeManifest, ParamSpec, PortSpec, SystemRequirement
```

2. In `_build_manifest()` signature (line ~239), add parameter after `requirements`:
```python
    system_requirements: list[dict] | None = None,
```

3. In the `return NodeManifest(...)` call in `_build_manifest()` (line ~277), add after `requirements=list(requirements or [])`:
```python
        system_requirements=[
            SystemRequirement.model_validate(sr) for sr in (system_requirements or [])
        ],
```

4. In the `node()` decorator signature (line ~816), add after `requirements`:
```python
    system_requirements: list[dict] | None = None,
```

5. In the `_build_manifest(...)` call inside `node()` (line ~841), add after `requirements=requirements`:
```python
            system_requirements=system_requirements,
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd packages/nodes && uv run pytest tests/test_system_requirements.py -v
```
Expected: 3 PASSED.

- [ ] **Step 6: Update `types.ts` with `SystemRequirement`**

In `apps/web/src/types.ts`, add before (or after) the `Environment` interface:

```typescript
export interface SystemRequirement {
  name: string;
  apt?: string;
  brew?: string;
  windows?: string;
  dockerfile_hint?: string;
  note?: string;
}
```

And on the `NodeManifest` interface, add:
```typescript
  system_requirements?: SystemRequirement[];
```

(If `NodeManifest` interface is not yet in `types.ts`, check `editor/` files for where the manifest type is defined and add it there.)

- [ ] **Step 7: Run the full nodes test suite**

```
cd packages/nodes && uv run pytest tests/ -q
```
Expected: all pass, no regressions.

- [ ] **Step 8: Commit**

```
git add packages/core/noodle/models.py packages/core/noodle/sdk.py
git add packages/nodes/tests/test_system_requirements.py apps/web/src/types.ts
git commit -m "feat(sdk): add system_requirements to @node decorator and NodeManifest"
```

---

## Task 2: Backend `base.py` Protocol + dispatcher scaffold

**Files:**
- Create: `apps/api/app/services/backends/__init__.py`
- Create: `apps/api/app/services/backends/base.py`
- Create: `apps/api/tests/test_backends.py`

- [ ] **Step 1: Write the failing dispatcher tests**

Create `apps/api/tests/test_backends.py`:

```python
"""Unit tests for the backend dispatcher and Protocol."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def _make_env(backend: str):
    env = MagicMock()
    env.backend = backend
    return env


def test_get_backend_returns_venv_backend() -> None:
    from app.services.backends import get_backend
    from app.services.backends.venv import VenvBackend
    assert isinstance(get_backend(_make_env("venv")), VenvBackend)


def test_get_backend_returns_conda_backend() -> None:
    from app.services.backends import get_backend
    from app.services.backends.conda import CondaBackend
    assert isinstance(get_backend(_make_env("conda")), CondaBackend)


def test_get_backend_returns_pixi_backend() -> None:
    from app.services.backends import get_backend
    from app.services.backends.pixi import PixiBackend
    assert isinstance(get_backend(_make_env("pixi")), PixiBackend)


def test_get_backend_raises_for_unknown() -> None:
    from app.services.backends import get_backend
    with pytest.raises(ValueError, match="unknown_backend"):
        get_backend(_make_env("unknown_backend"))
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && uv run pytest tests/test_backends.py -v
```
Expected: FAIL — `app.services.backends` does not exist.

- [ ] **Step 3: Create `backends/` package directory**

Windows: `mkdir apps/api/app/services/backends`
Or just create the files — Python will treat the directory as a package once `__init__.py` exists.

- [ ] **Step 4: Create `backends/base.py`**

Create `apps/api/app/services/backends/base.py`:

```python
"""Shared base types and utilities for all environment backends."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.config import settings


def venv_dir(env_id: str) -> Path:
    """Stable directory for a given environment id. Shared by all backends."""
    return Path(settings.envs_dir).resolve() / env_id


async def _run(*args: str) -> tuple[int, str]:
    """Run a subprocess, return (returncode, combined stdout+stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


@runtime_checkable
class EnvironmentBackend(Protocol):
    async def build(self, env: object) -> tuple[str, str]: ...
    # Returns ("ready"|"error", log_tail)

    def python_path(self, env_id: str) -> Path | None: ...
    # Stable path to the Python binary; None for Docker (no local binary).

    async def destroy(self, env_id: str) -> None: ...
    # Tear down the environment.
```

- [ ] **Step 5: Create `backends/__init__.py`**

Create `apps/api/app/services/backends/__init__.py`:

```python
"""Backend dispatcher: routes build/destroy to the correct backend class."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Environment
from app.services.backends.base import EnvironmentBackend

logger = logging.getLogger(__name__)

_build_locks: dict[str, asyncio.Lock] = {}
_build_locks_lock = asyncio.Lock()


def get_backend(env: Environment) -> EnvironmentBackend:
    from app.services.backends.conda import CondaBackend
    from app.services.backends.pixi import PixiBackend
    from app.services.backends.venv import VenvBackend

    match env.backend:
        case "venv":
            return VenvBackend()
        case "conda":
            return CondaBackend()
        case "pixi":
            return PixiBackend()
        case other:
            raise ValueError(f"Unknown backend: {other!r}")


async def _build_lock(env_id: str) -> asyncio.Lock:
    async with _build_locks_lock:
        lock = _build_locks.get(env_id)
        if lock is None:
            lock = asyncio.Lock()
            _build_locks[env_id] = lock
        return lock


async def build_environment(env_id: str) -> None:
    """Build or rebuild the environment, serialised per-env."""
    lock = await _build_lock(env_id)
    async with lock:
        await _build_environment_locked(env_id)


async def _build_environment_locked(env_id: str) -> None:
    """Inner builder. Caller must already hold the per-env build lock."""
    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is None:
            return
        env.status = "building"
        env.status_detail = ""
        await session.commit()
        # Snapshot fields needed outside the session
        _env = env

    if _env.backend == "venv" and not settings.enable_venv_builds:
        status, detail = "ready", "Venv builds are disabled in this environment."
    else:
        backend = get_backend(_env)
        try:
            status, detail = await backend.build(_env)
        except Exception as exc:  # noqa: BLE001
            status, detail = "error", f"{type(exc).__name__}: {exc}"

    rss_estimate: int | None = None
    if _env.backend == "venv" and status == "ready" and settings.enable_venv_builds:
        from app.services.backends.venv import _measure_worker_rss
        rss_estimate = await _measure_worker_rss(env_id)

    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is not None:
            env.status = status
            env.status_detail = detail
            if rss_estimate is not None:
                env.worker_rss_estimate_bytes = rss_estimate
            await session.commit()


async def ensure_environment_ready(env_id: str) -> Path:
    """Return the Python binary path for env_id, rebuilding if absent."""
    lock = await _build_lock(env_id)
    async with lock:
        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is None:
                raise RuntimeError(f"environment '{env_id}' not found")
            backend = get_backend(env)
            status = str(env.status or "")
            detail = str(env.status_detail or "")

        candidate = backend.python_path(env_id)

        if candidate is not None and candidate.exists() and status == "ready":
            return candidate

        if not settings.enable_venv_builds and env.backend == "venv":
            raise RuntimeError(
                f"environment '{env_id}' is not available and venv builds are disabled"
            )

        await _build_environment_locked(env_id)

        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is not None:
                backend = get_backend(env)
                status = str(env.status or "")
                detail = str(env.status_detail or "")
            else:
                status = "missing"
                detail = ""

        candidate = backend.python_path(env_id)
        if candidate is None or not candidate.exists() or status != "ready":
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"environment '{env_id}' is not ready after rebuild{suffix}"
            )
        return candidate
```

- [ ] **Step 6: Create stub `VenvBackend`, `CondaBackend`, `PixiBackend` so dispatcher tests pass**

Create `apps/api/app/services/backends/venv.py` with just enough for the dispatcher test:

```python
from app.services.backends.base import EnvironmentBackend  # noqa: F401

class VenvBackend:
    async def build(self, env) -> tuple[str, str]:
        raise NotImplementedError
    def python_path(self, env_id: str):
        raise NotImplementedError
    async def destroy(self, env_id: str) -> None:
        raise NotImplementedError
```

Create `apps/api/app/services/backends/conda.py` with stub:

```python
class CondaBackend:
    async def build(self, env) -> tuple[str, str]:
        raise NotImplementedError
    def python_path(self, env_id: str):
        raise NotImplementedError
    async def destroy(self, env_id: str) -> None:
        raise NotImplementedError
```

Create `apps/api/app/services/backends/pixi.py` with stub:

```python
class PixiBackend:
    async def build(self, env) -> tuple[str, str]:
        raise NotImplementedError
    def python_path(self, env_id: str):
        raise NotImplementedError
    async def destroy(self, env_id: str) -> None:
        raise NotImplementedError
```

- [ ] **Step 7: Run dispatcher tests — verify they pass**

```
cd apps/api && uv run pytest tests/test_backends.py -v
```
Expected: 4 PASSED.

- [ ] **Step 8: Commit**

```
git add apps/api/app/services/backends/
git add apps/api/tests/test_backends.py
git commit -m "feat(backends): add backend dispatcher scaffold and Protocol"
```

---

## Task 3: Extract `VenvBackend` from `venv.py`

**Files:**
- Modify: `apps/api/app/services/backends/venv.py` (replace stub with real VenvBackend)
- Modify: `apps/api/app/services/venv.py` (replace body with thin shim)
- Modify: `apps/api/app/routers/environments.py` (update import)

- [ ] **Step 1: Write the failing VenvBackend tests**

Add to `apps/api/tests/test_backends.py`:

```python
import sys
from unittest.mock import AsyncMock, MagicMock, patch


def test_venv_backend_python_path_posix() -> None:
    from app.services.backends.venv import VenvBackend
    b = VenvBackend()
    with patch("sys.platform", "linux"):
        with patch("app.services.backends.venv.venv_dir", return_value=MagicMock(spec=__import__("pathlib").Path)):
            path = b.python_path("env123")
    assert path is not None
    assert "bin" in str(path) or "python" in str(path)


def test_venv_backend_python_path_win32() -> None:
    from app.services.backends.venv import VenvBackend
    b = VenvBackend()
    with patch("sys.platform", "win32"):
        with patch("app.services.backends.venv.venv_dir", return_value=__import__("pathlib").Path("/fake")):
            path = b.python_path("env123")
    assert "Scripts" in str(path) or "python" in str(path)


@pytest.mark.asyncio
async def test_venv_backend_build_passes_index_urls(tmp_path) -> None:
    from app.services.backends.venv import VenvBackend
    env = MagicMock()
    env.id = "test-venv"
    env.python_version = "3.12"
    env.packages = ["numpy"]
    env.backend_config = {"index_urls": ["https://download.pytorch.org/whl/cu121"]}

    captured: list[tuple] = []

    async def mock_do_build(env_id, python_version, packages, index_urls=None):
        captured.append((env_id, python_version, packages, index_urls))
        return "ready", "ok"

    with patch("app.services.backends.venv._do_build", side_effect=mock_do_build):
        b = VenvBackend()
        status, log = await b.build(env)

    assert status == "ready"
    assert captured[0][3] == ["https://download.pytorch.org/whl/cu121"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && uv run pytest tests/test_backends.py::test_venv_backend_python_path_posix tests/test_backends.py::test_venv_backend_python_path_win32 tests/test_backends.py::test_venv_backend_build_passes_index_urls -v
```
Expected: FAIL.

- [ ] **Step 3: Implement full `VenvBackend` in `backends/venv.py`**

Replace the stub `apps/api/app/services/backends/venv.py` with the full implementation:

```python
"""VenvBackend: uv-managed virtual environments."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from app.config import settings
from app.services.backends.base import _run, venv_dir


def venv_python(env_id: str) -> Path:
    base = venv_dir(env_id)
    if sys.platform == "win32":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


class VenvBackend:
    async def build(self, env) -> tuple[str, str]:
        index_urls = list((env.backend_config or {}).get("index_urls", []))
        return await _do_build(env.id, env.python_version, list(env.packages), index_urls)

    def python_path(self, env_id: str) -> Path:
        return venv_python(env_id)

    async def destroy(self, env_id: str) -> None:
        target = venv_dir(env_id)
        if target.exists():
            shutil.rmtree(target)


def _workspace_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def _local_noodle_packages() -> list[str]:
    root = _workspace_root()
    if root is None:
        return []
    paths: list[str] = []
    for name in ("core", "nodes", "runtime"):
        candidate = root / "packages" / name
        if candidate.is_dir():
            paths.append(str(candidate))
    return paths


async def _do_build(
    env_id: str,
    python_version: str,
    packages: list[str],
    index_urls: list[str] | None = None,
) -> tuple[str, str]:
    target = venv_dir(env_id)
    try:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        code, log = await _run("uv", "venv", "--python", python_version, str(target))
        if code != 0:
            return "error", log.strip()[-4000:]

        to_install = [*_local_noodle_packages(), *packages]
        if to_install:
            extra_index_args: list[str] = []
            for url in (index_urls or []):
                extra_index_args += ["--extra-index-url", url]
            code, install_log = await _run(
                "uv", "pip", "install",
                "--python", str(venv_python(env_id)),
                *extra_index_args,
                *to_install,
            )
            log = f"{log}\n{install_log}"

        status = "ready" if code == 0 else "error"
        return status, log.strip()[-4000:]
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}"


async def _measure_worker_rss(env_id: str) -> int | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None

    python = venv_python(env_id)
    if not python.exists():
        return None

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(python), "-u", "-m", "noodle_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None:
            return None
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        except TimeoutError:
            return None
        if not line:
            return None
        try:
            ready = json.loads(line)
        except json.JSONDecodeError:
            return None
        if ready.get("type") != "ready":
            return None
        try:
            rss = int(psutil.Process(process.pid).memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            return None
        return rss if rss > 0 else None
    except (OSError, RuntimeError):
        return None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass
```

- [ ] **Step 4: Replace `venv.py` body with thin shim**

Replace the entire body of `apps/api/app/services/venv.py` with:

```python
"""Thin shim — re-exports from backends package for backward compatibility."""
from app.services.backends import build_environment, ensure_environment_ready
from app.services.backends.base import venv_dir
from app.services.backends.venv import VenvBackend, _do_build, venv_python

__all__ = [
    "build_environment",
    "ensure_environment_ready",
    "venv_dir",
    "venv_python",
    "VenvBackend",
    "_do_build",
]
```

- [ ] **Step 5: Update `environments.py` import**

In `apps/api/app/routers/environments.py`, change line:
```python
from app.services.venv import build_environment
```
to:
```python
from app.services.backends import build_environment
```

- [ ] **Step 6: Run the VenvBackend tests and the existing venv service tests**

```
cd apps/api && uv run pytest tests/test_backends.py tests/test_venv_service.py -v
```
Expected: all PASS.

- [ ] **Step 7: Run the full API test suite**

```
cd apps/api && uv run pytest tests/ -q --timeout=60
```
Expected: all pass.

- [ ] **Step 8: Commit**

```
git add apps/api/app/services/backends/venv.py apps/api/app/services/venv.py
git add apps/api/app/routers/environments.py apps/api/tests/test_backends.py
git commit -m "refactor(backends): extract VenvBackend; venv.py becomes thin shim"
```

---

## Task 4: `tools.py` — `ensure_tool()` auto-download

**Files:**
- Create: `apps/api/app/services/backends/tools.py`
- Create: `apps/api/tests/test_ensure_tool.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_ensure_tool.py`:

```python
"""Unit tests for ensure_tool() auto-download."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_ensure_tool_returns_existing_binary(tmp_path: Path) -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    dest = tmp_path / f"micromamba{suffix}"
    dest.write_bytes(b"fake binary content")

    with patch("app.services.backends.tools.TOOLS_DIR", tmp_path):
        from app.services.backends.tools import ensure_tool
        result = await ensure_tool("micromamba")

    assert result == dest
    # Should NOT have called httpx — binary was already there
    assert result.read_bytes() == b"fake binary content"


@pytest.mark.asyncio
async def test_ensure_tool_downloads_when_absent(tmp_path: Path) -> None:
    mock_response = MagicMock()
    mock_response.content = b"downloaded binary"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.backends.tools.TOOLS_DIR", tmp_path):
        with patch("httpx.AsyncClient", return_value=mock_client):
            from importlib import reload
            import app.services.backends.tools as tools_mod
            reload(tools_mod)  # re-bind TOOLS_DIR patch
            result = await tools_mod.ensure_tool("micromamba")

    suffix = ".exe" if sys.platform == "win32" else ""
    assert result == tmp_path / f"micromamba{suffix}"
    assert result.exists()
    assert result.read_bytes() == b"downloaded binary"


@pytest.mark.asyncio
async def test_ensure_tool_raises_on_http_error(tmp_path: Path) -> None:
    import httpx

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.backends.tools.TOOLS_DIR", tmp_path):
        with patch("httpx.AsyncClient", return_value=mock_client):
            from importlib import reload
            import app.services.backends.tools as tools_mod
            reload(tools_mod)
            with pytest.raises(httpx.HTTPStatusError):
                await tools_mod.ensure_tool("pixi")
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && uv run pytest tests/test_ensure_tool.py -v
```
Expected: FAIL — `app.services.backends.tools` does not exist.

- [ ] **Step 3: Create `backends/tools.py`**

Create `apps/api/app/services/backends/tools.py`:

```python
"""Auto-download static tool binaries (micromamba, pixi) on first use."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TOOLS_DIR = Path(settings.envs_dir).resolve().parent / "tools"

_MICROMAMBA_URLS: dict[str, str] = {
    "linux":  "https://micro.mamba.pm/api/micromamba/linux-64/latest",
    "darwin": "https://micro.mamba.pm/api/micromamba/osx-arm64/latest",
    "win32":  "https://micro.mamba.pm/api/micromamba/win-64/latest",
}
_PIXI_URLS: dict[str, str] = {
    "linux":  "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-unknown-linux-musl",
    "darwin": "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-aarch64-apple-darwin",
    "win32":  "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-pc-windows-msvc.exe",
}


async def ensure_tool(name: Literal["micromamba", "pixi"]) -> Path:
    """Return path to tool binary, downloading it first if absent."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    dest = TOOLS_DIR / f"{name}{suffix}"
    if dest.exists():
        return dest

    platform = sys.platform
    urls = _MICROMAMBA_URLS if name == "micromamba" else _PIXI_URLS
    url = urls.get(platform)
    if url is None:
        raise RuntimeError(
            f"No {name} download URL for platform {platform!r}. "
            "Install it manually and ensure it is on PATH."
        )
    logger.info("Downloading %s from %s", name, url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        r = await client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    if sys.platform != "win32":
        dest.chmod(0o755)
    logger.info("Downloaded %s to %s", name, dest)
    return dest
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd apps/api && uv run pytest tests/test_ensure_tool.py -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```
git add apps/api/app/services/backends/tools.py apps/api/tests/test_ensure_tool.py
git commit -m "feat(backends): add ensure_tool() for micromamba and pixi auto-download"
```

---

## Task 5: `CondaBackend`

**Files:**
- Modify: `apps/api/app/services/backends/conda.py` (replace stub with full implementation)
- Modify: `apps/api/tests/test_backends.py` (add conda tests)

- [ ] **Step 1: Write failing conda tests**

Add to `apps/api/tests/test_backends.py`:

```python
@pytest.mark.asyncio
async def test_conda_build_calls_micromamba_with_channels(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend

    env = MagicMock()
    env.id = "conda-test"
    env.python_version = "3.11"
    env.packages = ["numpy", "pandas"]
    env.backend_config = {"channels": ["conda-forge", "nvidia"]}

    calls: list[tuple] = []

    async def mock_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    fake_solver = tmp_path / "micromamba"
    fake_solver.write_bytes(b"fake")

    with patch("app.services.backends.conda._run", side_effect=mock_run):
        with patch("app.services.backends.conda.ensure_tool", return_value=fake_solver):
            with patch("app.services.backends.conda.venv_dir", return_value=tmp_path / "env"):
                b = CondaBackend()
                status, _ = await b.build(env)

    assert status == "ready"
    assert len(calls) == 1
    cmd = calls[0]
    assert str(fake_solver) == cmd[0]
    assert "create" in cmd
    assert "--yes" in cmd
    assert "-c" in cmd
    chan_idx = list(cmd).index("-c")
    assert cmd[chan_idx + 1] == "conda-forge"
    assert "python=3.11" in cmd
    assert "numpy" in cmd
    assert "pandas" in cmd


@pytest.mark.asyncio
async def test_conda_build_returns_error_on_nonzero_exit(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend

    env = MagicMock()
    env.id = "conda-fail"
    env.python_version = "3.12"
    env.packages = []
    env.backend_config = {}

    async def mock_run(*args: str) -> tuple[int, str]:
        return 1, "solver error: package not found"

    with patch("app.services.backends.conda._run", side_effect=mock_run):
        with patch("app.services.backends.conda.ensure_tool", return_value=tmp_path / "micromamba"):
            with patch("app.services.backends.conda.venv_dir", return_value=tmp_path / "env"):
                b = CondaBackend()
                status, log = await b.build(env)

    assert status == "error"
    assert "solver error" in log


def test_conda_python_path_posix(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend
    b = CondaBackend()
    with patch("sys.platform", "linux"):
        with patch("app.services.backends.conda.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert str(p).endswith("bin/python")


def test_conda_python_path_win32(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend
    b = CondaBackend()
    with patch("sys.platform", "win32"):
        with patch("app.services.backends.conda.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert "Scripts" in str(p) and "python.exe" in str(p)
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && uv run pytest tests/test_backends.py::test_conda_build_calls_micromamba_with_channels -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `CondaBackend`**

Replace the stub `apps/api/app/services/backends/conda.py` with:

```python
"""CondaBackend: micromamba/mamba/conda-managed environments."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.services.backends.base import _run, venv_dir
from app.services.backends.tools import ensure_tool


class CondaBackend:
    async def build(self, env) -> tuple[str, str]:
        solver = await self._solver_cmd()
        channels = (env.backend_config or {}).get("channels", ["conda-forge", "defaults"])
        channel_args = [arg for c in channels for arg in ("-c", c)]
        env_dir = venv_dir(env.id)
        if env_dir.exists():
            shutil.rmtree(env_dir)
        code, log = await _run(
            str(solver), "create", "--yes",
            "--prefix", str(env_dir),
            f"python={env.python_version}",
            *channel_args,
            *list(env.packages),
        )
        return ("ready" if code == 0 else "error"), log[-4000:]

    def python_path(self, env_id: str) -> Path:
        base = venv_dir(env_id)
        if sys.platform == "win32":
            return base / "Scripts" / "python.exe"
        return base / "bin" / "python"

    async def destroy(self, env_id: str) -> None:
        shutil.rmtree(venv_dir(env_id), ignore_errors=True)

    async def _solver_cmd(self) -> Path:
        managed = await ensure_tool("micromamba")
        if managed.exists():
            return managed
        for cmd in ("mamba", "conda"):
            if shutil.which(cmd):
                return Path(shutil.which(cmd))  # type: ignore[arg-type]
        raise RuntimeError(
            "micromamba download failed and no system mamba/conda found. "
            "Check your network connection and try rebuilding."
        )
```

- [ ] **Step 4: Run the conda tests**

```
cd apps/api && uv run pytest tests/test_backends.py -k "conda" -v
```
Expected: all conda tests PASS.

- [ ] **Step 5: Commit**

```
git add apps/api/app/services/backends/conda.py apps/api/tests/test_backends.py
git commit -m "feat(backends): add CondaBackend with micromamba solver"
```

---

## Task 6: `PixiBackend`

**Files:**
- Modify: `apps/api/app/services/backends/pixi.py` (replace stub)
- Modify: `apps/api/tests/test_backends.py` (add pixi tests)

- [ ] **Step 1: Write failing pixi tests**

Add to `apps/api/tests/test_backends.py`:

```python
def test_pixi_split_packages_routes_pypi_suffix() -> None:
    from app.services.backends.pixi import _split_packages
    conda, pypi = _split_packages(["numpy", "httpx @ pypi", "pandas", "mylib@pypi"])
    assert set(conda) == {"numpy", "pandas"}
    assert set(pypi) == {"httpx", "mylib"}


def test_pixi_split_packages_no_pypi() -> None:
    from app.services.backends.pixi import _split_packages
    conda, pypi = _split_packages(["numpy", "scipy"])
    assert set(conda) == {"numpy", "scipy"}
    assert pypi == []


def test_pixi_write_toml_puts_conda_in_dependencies(tmp_path) -> None:
    from app.services.backends.pixi import _write_pixi_toml
    import sys

    env = MagicMock()
    env.id = "pixi-test"
    env.python_version = "3.12"
    env.packages = ["numpy", "httpx @ pypi"]
    env.backend_config = {"channels": ["conda-forge"]}

    toml_path = tmp_path / "pixi.toml"
    _write_pixi_toml(toml_path, env)
    content = toml_path.read_text()

    assert 'name = "noodle-env-pixi-test"' in content
    assert '"conda-forge"' in content
    assert "python" in content
    assert "3.12" in content
    assert "[dependencies]" in content
    assert "numpy" in content
    assert "[pypi-dependencies]" in content
    assert "httpx" in content


def test_pixi_write_toml_no_pypi_packages(tmp_path) -> None:
    from app.services.backends.pixi import _write_pixi_toml

    env = MagicMock()
    env.id = "pixi-test2"
    env.python_version = "3.11"
    env.packages = ["pandas"]
    env.backend_config = {}

    toml_path = tmp_path / "pixi.toml"
    _write_pixi_toml(toml_path, env)
    content = toml_path.read_text()

    assert "pandas" in content
    # Empty [pypi-dependencies] section is fine but no actual packages
    conda_section = content.split("[pypi-dependencies]")[0] if "[pypi-dependencies]" in content else content
    assert "pandas" in conda_section


def test_pixi_python_path_posix(tmp_path) -> None:
    from app.services.backends.pixi import PixiBackend
    b = PixiBackend()
    with patch("sys.platform", "linux"):
        with patch("app.services.backends.pixi.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert ".pixi" in str(p)
    assert "bin/python" in str(p)


def test_pixi_python_path_win32(tmp_path) -> None:
    from app.services.backends.pixi import PixiBackend
    b = PixiBackend()
    with patch("sys.platform", "win32"):
        with patch("app.services.backends.pixi.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert ".pixi" in str(p)
    assert "python.exe" in str(p)


@pytest.mark.asyncio
async def test_pixi_build_calls_pixi_install(tmp_path) -> None:
    from app.services.backends.pixi import PixiBackend

    env = MagicMock()
    env.id = "pixi-build"
    env.python_version = "3.12"
    env.packages = ["numpy"]
    env.backend_config = {"channels": ["conda-forge"]}

    calls: list[tuple] = []

    async def mock_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    fake_pixi = tmp_path / "pixi"

    with patch("app.services.backends.pixi._run", side_effect=mock_run):
        with patch("app.services.backends.pixi.ensure_tool", return_value=fake_pixi):
            with patch("app.services.backends.pixi.venv_dir", return_value=tmp_path / "env"):
                b = PixiBackend()
                status, _ = await b.build(env)

    assert status == "ready"
    assert len(calls) == 1
    cmd = calls[0]
    assert str(fake_pixi) == cmd[0]
    assert "install" in cmd
    assert "--manifest-path" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && uv run pytest tests/test_backends.py -k "pixi" -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `PixiBackend`**

Replace the stub `apps/api/app/services/backends/pixi.py` with:

```python
"""PixiBackend: pixi-managed conda+PyPI lockfile environments."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from app.services.backends.base import _run, venv_dir
from app.services.backends.tools import ensure_tool

_PYPI_SUFFIX_RE = re.compile(r"\s*@\s*pypi\s*$", re.IGNORECASE)


def _split_packages(packages: list[str]) -> tuple[list[str], list[str]]:
    """Route packages to conda deps vs pixi PyPI deps based on `@ pypi` suffix."""
    conda: list[str] = []
    pypi: list[str] = []
    for pkg in packages:
        if _PYPI_SUFFIX_RE.search(pkg):
            pypi.append(_PYPI_SUFFIX_RE.sub("", pkg).strip())
        else:
            conda.append(pkg)
    return conda, pypi


def _current_platform() -> str:
    mapping = {
        "linux":  "linux-64",
        "darwin": "osx-arm64",
        "win32":  "win-64",
    }
    plat = mapping.get(sys.platform)
    if plat is None:
        raise RuntimeError(
            f"Unsupported platform for pixi: {sys.platform!r}. "
            "pixi supports linux-64, osx-arm64, and win-64."
        )
    return plat


def _write_pixi_toml(toml_path: Path, env) -> None:
    channels = (env.backend_config or {}).get("channels", ["conda-forge"])
    channel_lines = "\n".join(f'  "{c}",' for c in channels)
    conda_pkgs, pypi_pkgs = _split_packages(list(env.packages))
    conda_lines = "\n".join(f'{p} = "*"' for p in conda_pkgs)
    pypi_lines = "\n".join(f'{p} = "*"' for p in pypi_pkgs)
    toml_path.write_text(
        f'[project]\n'
        f'name = "noodle-env-{env.id}"\n'
        f'channels = [\n{channel_lines}\n]\n'
        f'platforms = ["{_current_platform()}"]\n\n'
        f'[dependencies]\n'
        f'python = "{env.python_version}.*"\n'
        f'{conda_lines}\n\n'
        f'[pypi-dependencies]\n'
        f'{pypi_lines}\n'
    )


class PixiBackend:
    async def build(self, env) -> tuple[str, str]:
        pixi_bin = await ensure_tool("pixi")
        env_dir = venv_dir(env.id)
        if env_dir.exists():
            shutil.rmtree(env_dir)
        env_dir.mkdir(parents=True)
        _write_pixi_toml(env_dir / "pixi.toml", env)
        code, log = await _run(
            str(pixi_bin), "install",
            "--manifest-path", str(env_dir / "pixi.toml"),
        )
        return ("ready" if code == 0 else "error"), log[-4000:]

    def python_path(self, env_id: str) -> Path:
        base = venv_dir(env_id)
        if sys.platform == "win32":
            return base / ".pixi" / "envs" / "default" / "Scripts" / "python.exe"
        return base / ".pixi" / "envs" / "default" / "bin" / "python"

    async def destroy(self, env_id: str) -> None:
        shutil.rmtree(venv_dir(env_id), ignore_errors=True)
```

- [ ] **Step 4: Run pixi tests**

```
cd apps/api && uv run pytest tests/test_backends.py -k "pixi" -v
```
Expected: all pixi tests PASS.

- [ ] **Step 5: Run the full backends test suite**

```
cd apps/api && uv run pytest tests/test_backends.py tests/test_ensure_tool.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```
git add apps/api/app/services/backends/pixi.py apps/api/tests/test_backends.py
git commit -m "feat(backends): add PixiBackend with pixi.toml generation and @ pypi routing"
```

---

## Task 7: Update `list_backends()` endpoint

**Files:**
- Modify: `apps/api/app/routers/environments.py`
- Modify: `apps/api/tests/test_environments.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/test_environments.py` after the existing backends tests:

```python
async def test_backends_conda_is_always_available(client) -> None:
    """conda and pixi must always be available (even before download) for the UI."""
    resp = await client.get("/environments/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conda"]["available"] is True
    assert data["pixi"]["available"] is True


async def test_backends_venv_available_when_uv_present(client) -> None:
    """venv backend is available when uv is on PATH."""
    import shutil
    resp = await client.get("/environments/backends")
    assert resp.status_code == 200
    data = resp.json()
    # In dev environment uv should be available
    expected = shutil.which("uv") is not None
    assert data["venv"]["available"] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && uv run pytest tests/test_environments.py::test_backends_conda_is_always_available -v
```
Expected: FAIL — currently `conda.available` is `False`.

- [ ] **Step 3: Update `list_backends()` in `environments.py`**

Replace the existing `list_backends()` function:

```python
@router.get("/backends")
async def list_backends() -> dict:
    """Return server platform and available backends.

    conda and pixi are always available — their binaries auto-download on first use.
    Only Docker is greyed out when the daemon is unreachable.
    The ``platform`` field is used by the frontend PEP 508 marker evaluator.
    """
    import shutil
    import sys
    from app.services.backends.tools import TOOLS_DIR

    def _tool_version(name: str) -> str | None:
        """Return cached version string for a managed tool, or None if not yet downloaded."""
        suffix = ".exe" if sys.platform == "win32" else ""
        binary = TOOLS_DIR / f"{name}{suffix}"
        if binary.exists():
            return f"{name} (managed)"  # version check on startup is expensive; return presence indicator
        system = shutil.which(name)
        if system:
            return f"{name} (system)"
        return None

    uv_path = shutil.which("uv")
    micromamba_version = _tool_version("micromamba") or (
        "mamba (system)" if shutil.which("mamba") else
        "conda (system)" if shutil.which("conda") else
        None
    )
    pixi_version = _tool_version("pixi")
    docker_available = shutil.which("docker") is not None

    return {
        "platform": sys.platform,
        "venv": {
            "available": uv_path is not None,
            "version": None,
            "managed": False,
        },
        "conda": {
            "available": True,  # always — auto-downloads on first use
            "version": micromamba_version,
            "managed": micromamba_version is not None and "managed" in (micromamba_version or ""),
        },
        "pixi": {
            "available": True,  # always — auto-downloads on first use
            "version": pixi_version,
            "managed": pixi_version is not None and "managed" in (pixi_version or ""),
        },
        "docker": {
            "available": docker_available,
            "version": None,
            "managed": False,
        },
    }
```

- [ ] **Step 4: Run the new tests**

```
cd apps/api && uv run pytest tests/test_environments.py -k "backends" -v
```
Expected: all backend tests PASS.

- [ ] **Step 5: Run full API test suite**

```
cd apps/api && uv run pytest tests/ -q --timeout=60
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add apps/api/app/routers/environments.py apps/api/tests/test_environments.py
git commit -m "feat(backends): conda/pixi always available in list_backends; show managed/system version"
```

---

## Task 8: Frontend — `system_requirements` section in NodeDetails + NodeCard badge

**Files:**
- Modify: `apps/web/src/editor/NodeCard.tsx`
- Modify: `apps/web/src/editor/NodeDetails.tsx`

Check where `NodeManifest` type is used in the editor and add `system_requirements` to it if a separate frontend type exists.

- [ ] **Step 1: Find the frontend NodeManifest type**

Search for `NodeManifest` in the web src:
```
grep -rn "NodeManifest\|system_requirements" apps/web/src/
```

If `NodeManifest` is defined in `apps/web/src/types.ts` as an interface, add `system_requirements?: SystemRequirement[]` (already done in Task 1 Step 6). If it's defined elsewhere, add it there.

- [ ] **Step 2: Add orange badge to `NodeCard.tsx`**

Find the section in `NodeCard.tsx` that renders the missing-package yellow badge. Immediately after it, add the `system_requirements` orange badge.

The pattern to follow: look for where `missingPackages` count badge is rendered (a small badge or dot near the node card title), then add an orange variant when `manifest.system_requirements?.length` is non-zero.

In `NodeCard.tsx`, find the badge rendering section and add:

```tsx
{(manifest.system_requirements?.length ?? 0) > 0 && (
  <span
    className="node-badge node-badge--sysreq"
    title={`System dependencies required: ${manifest.system_requirements!.map((r) => r.name).join(", ")}`}
  >
    ●
  </span>
)}
```

In the corresponding CSS (or Tailwind class), the orange badge should be `color: #f97316` (Tailwind orange-500) or use an existing orange utility class from the project.

- [ ] **Step 3: Add `system_requirements` section to `NodeDetails.tsx`**

In `NodeDetails.tsx`, after the `requirements` section (the missing packages list), add a `system_requirements` section. This section is context-aware based on the active environment's `backend`:

```tsx
{manifest.system_requirements && manifest.system_requirements.length > 0 && (
  <div className="node-details-section">
    <div className="node-details-section-title">System dependencies</div>
    {manifest.system_requirements.map((sr) => (
      <div key={sr.name} className="sysreq-item">
        <div className="sysreq-name">{sr.name}</div>
        {env?.backend === "docker" ? (
          sr.dockerfile_hint ? (
            <div className="sysreq-hint">
              <span className="sysreq-label">Add to Dockerfile:</span>
              <code className="sysreq-code">{sr.dockerfile_hint}</code>
            </div>
          ) : (
            <div className="sysreq-hint">Must be included in your Docker image.</div>
          )
        ) : (
          <div className="sysreq-hint">
            <span className="sysreq-label">Must be installed on the server.</span>
            {sr.apt && <div><strong>Linux:</strong> <code>apt install {sr.apt}</code></div>}
            {sr.brew && <div><strong>macOS:</strong> <code>brew install {sr.brew}</code></div>}
            {sr.windows && (
              <div>
                <strong>Windows:</strong>{" "}
                {sr.windows.startsWith("http") ? (
                  <a href={sr.windows} target="_blank" rel="noreferrer">{sr.windows}</a>
                ) : (
                  <span>{sr.windows}</span>
                )}
              </div>
            )}
            {sr.note && <div className="sysreq-note">{sr.note}</div>}
          </div>
        )}
      </div>
    ))}
  </div>
)}
```

The `env` variable should already be in scope (it is the active environment passed down to NodeDetails). Check how the env is accessed; it may need to be threaded via props or a context hook.

- [ ] **Step 4: Run TypeScript type-check**

```
cd apps/web && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Commit**

```
git add apps/web/src/editor/NodeCard.tsx apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(ui): add system_requirements section in NodeDetails and orange badge in NodeCard"
```

---

## Task 9: Frontend — backend badge + backend selector tabs + channels in PackageDrawer

**Files:**
- Modify: `apps/web/src/EnvironmentsPage.tsx`
- Modify: `apps/web/src/PackageDrawer.tsx`

- [ ] **Step 1: Add backend badge to env cards in `EnvironmentsPage.tsx`**

Find where env cards are rendered (the component that maps over environments and shows name/status). Add a small badge showing the `backend` value with distinct colours:

```tsx
const BACKEND_BADGE: Record<string, { label: string; color: string }> = {
  venv:   { label: "venv",   color: "#22c55e" },   // green
  conda:  { label: "conda",  color: "#3b82f6" },   // blue
  pixi:   { label: "pixi",   color: "#14b8a6" },   // teal
  docker: { label: "docker", color: "#a855f7" },   // purple
};

// Inside the env card render:
const badge = BACKEND_BADGE[env.backend] ?? { label: env.backend, color: "#6b7280" };
// ...
<span
  className="env-backend-badge"
  style={{ background: badge.color, color: "#fff", borderRadius: 4, padding: "1px 6px", fontSize: 11 }}
>
  {badge.label}
</span>
```

- [ ] **Step 2: Add backend selector tabs to the create-environment dialog**

Find the create environment form/dialog in `EnvironmentsPage.tsx`. Add a tab row at the top:

```tsx
type BackendTab = "venv" | "conda" | "pixi";
const [backendTab, setBackendTab] = useState<BackendTab>("venv");

// Tabs render:
<div className="backend-tabs">
  {(["venv", "conda", "pixi"] as BackendTab[]).map((b) => (
    <button
      key={b}
      className={`backend-tab${backendTab === b ? " backend-tab--active" : ""}`}
      onClick={() => setBackendTab(b)}
      type="button"
    >
      {b === "venv" ? "uv + venv" : b}
    </button>
  ))}
  <button className="backend-tab backend-tab--disabled" disabled type="button" title="Docker — coming soon">
    Docker
  </button>
</div>
```

Wire `backendTab` into the create payload:
```typescript
backend: backendTab,
backend_config: backendTab === "conda" || backendTab === "pixi"
  ? { channels: channelList }
  : backendTab === "venv"
  ? { index_urls: indexUrlList }
  : {},
```

Add a `channelList` state (string array, default `["conda-forge"]`) shown only when `backendTab === "conda" || backendTab === "pixi"`.
Add an `indexUrlList` state (string array, default `[]`) shown only when `backendTab === "venv"`.

- [ ] **Step 3: Add channels field to `PackageDrawer.tsx`**

In `PackageDrawer.tsx`, show a "Channels" section at the top when `env.backend === "conda" || env.backend === "pixi"`:

```tsx
const channels: string[] = useMemo(
  () => (env.backend_config?.channels as string[] | undefined) ?? [],
  [env.backend_config]
);

// Render above the packages list:
{(env.backend === "conda" || env.backend === "pixi") && (
  <div className="package-drawer-section">
    <div className="section-title">Channels</div>
    <div className="channels-list">
      {channels.length === 0 && <span className="muted">conda-forge (default)</span>}
      {channels.map((c) => (
        <div key={c} className="channel-row">{c}</div>
      ))}
    </div>
    <p className="hint">
      Edit channels via the environment settings.{" "}
      {env.backend === "pixi" && (
        <span>Suffix packages with <code>@ pypi</code> to install from PyPI instead of conda.</span>
      )}
    </p>
  </div>
)}
```

- [ ] **Step 4: Run TypeScript type-check**

```
cd apps/web && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Run the existing web tests**

```
cd apps/web && npx vitest run
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add apps/web/src/EnvironmentsPage.tsx apps/web/src/PackageDrawer.tsx
git commit -m "feat(ui): backend badge on env cards, selector tabs in create dialog, channels in PackageDrawer"
```

---

## Self-Review Checklist

Before calling Phase 2 complete, verify:

- [ ] `SystemRequirement.model_validate({"name": "x"})` works with all-optional fields
- [ ] `get_backend(env_with_backend_venv)` returns `VenvBackend`, conda → `CondaBackend`, pixi → `PixiBackend`
- [ ] Old `from app.services.venv import build_environment` still works (shim)
- [ ] Old `from app.services.venv import ensure_environment_ready` still works (shim)
- [ ] `ensure_tool` does not re-download an already-present binary
- [ ] `_split_packages(["a @ pypi", "b"])` → `(["b"], ["a"])`
- [ ] `list_backends()` returns `conda.available == True` and `pixi.available == True`
- [ ] `NodeManifest.system_requirements` defaults to `[]` for old nodes (backward compat)
- [ ] All `apps/api` tests pass: `uv run pytest tests/ -q --timeout=60`
- [ ] All `packages/nodes` tests pass: `uv run pytest tests/ -q`
- [ ] TypeScript: `npx tsc --noEmit` clean

---

## Commit Summary

Phase 2 adds 6 new Python files, modifies 7 existing ones, and touches 4 frontend files. Total new tests: ~35. All changes are backward-compatible — existing venv environments, imports, and API calls work unchanged.
