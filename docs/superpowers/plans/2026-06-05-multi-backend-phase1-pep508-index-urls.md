# Multi-Backend Phase 1 — PEP 508 Markers + venv `index_urls` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend` + `backend_config` columns to environments, thread `index_urls` through the venv builder so users can point to CUDA/private wheels, and filter PEP 508 platform markers in preflight and the frontend so Windows-only and Linux-only requirements don't trigger spurious "missing package" warnings.

**Architecture:** Two new nullable-with-server-default columns (`backend`, `backend_config`) land via a zero-downtime Alembic migration. `VenvBackend` reads `backend_config.index_urls` and passes `--extra-index-url` flags to `uv pip install`. A new `GET /environments/backends` endpoint exposes the server platform so the TypeScript `missingFor()` helper can skip requirements whose `sys_platform` marker doesn't match.

**Tech Stack:** Python / FastAPI / SQLAlchemy / Alembic / uv — TypeScript / Vitest

---

## File Map

| File | Change |
|------|--------|
| `apps/api/alembic/versions/0035_environment_backend.py` | **Create** — migration: `backend` + `backend_config` columns |
| `apps/api/app/models.py` | **Modify** — add two fields to `Environment` ORM model |
| `apps/api/app/schemas.py` | **Modify** — add fields to `EnvironmentCreate`, `EnvironmentUpdate`, `EnvironmentInfo` |
| `apps/api/app/services/venv.py` | **Modify** — `_build_environment_locked` + `_do_build` pass `index_urls` |
| `apps/api/app/services/package_preflight.py` | **Modify** — `_marker_applies()` + filter in `find_missing_packages()` |
| `apps/api/app/routers/environments.py` | **Modify** — `_to_info`, `create_environment`, `update_environment`; add `GET /backends` |
| `apps/web/src/types.ts` | **Modify** — `Environment` interface |
| `apps/web/src/editor/missingPackages.ts` | **Modify** — `evaluateMarker()` + updated `missingFor()` |
| `apps/api/tests/test_environments.py` | **Modify** — add three new tests |
| `apps/web/src/editor/missingPackages.test.ts` | **Modify** — add marker-filtering tests |

---

## Task 1: DB Migration — `backend` + `backend_config` columns

**Files:**
- Create: `apps/api/alembic/versions/0035_environment_backend.py`

- [ ] **Step 1: Write the migration file**

```python
# apps/api/alembic/versions/0035_environment_backend.py
"""environments: backend + backend_config columns

Revision ID: 0035_environment_backend
Revises: 0034_provider_triggers
Create Date: 2026-06-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_environment_backend"
down_revision: str | None = "0034_provider_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    existing = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "backend" not in existing:
            batch.add_column(
                sa.Column("backend", sa.String(20), server_default="venv", nullable=False)
            )
        if "backend_config" not in existing:
            batch.add_column(
                sa.Column("backend_config", sa.JSON, server_default="{}", nullable=False)
            )


def downgrade() -> None:
    existing = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "backend_config" in existing:
            batch.drop_column("backend_config")
        if "backend" in existing:
            batch.drop_column("backend")
```

- [ ] **Step 2: Run migration to verify it applies cleanly**

```bash
cd apps/api
uv run alembic upgrade head
```

Expected: `Running upgrade 0034_provider_triggers -> 0035_environment_backend, ...`

- [ ] **Step 3: Verify downgrade works**

```bash
cd apps/api
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: both commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add apps/api/alembic/versions/0035_environment_backend.py
git commit -m "feat(api): add backend + backend_config columns to environments"
```

---

## Task 2: ORM Model + Pydantic Schemas

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/schemas.py`

- [ ] **Step 1: Write the failing test**

Add at the bottom of `apps/api/tests/test_environments.py`:

```python
async def test_environment_backend_config_roundtrip(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/environments",
            json={
                "name": "CUDA env",
                "backend": "venv",
                "backend_config": {"index_urls": ["https://download.pytorch.org/whl/cu121"]},
            },
        )
    ).json()
    assert created["backend"] == "venv"
    assert created["backend_config"] == {"index_urls": ["https://download.pytorch.org/whl/cu121"]}

    fetched = (await client.get(f"/environments/{created['id']}")).json()
    assert fetched["backend"] == "venv"
    assert fetched["backend_config"] == {"index_urls": ["https://download.pytorch.org/whl/cu121"]}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api
uv run pytest tests/test_environments.py::test_environment_backend_config_roundtrip -v
```

Expected: FAIL — `KeyError: 'backend'` or 422 Unprocessable Entity.

- [ ] **Step 3: Add fields to the ORM model**

In `apps/api/app/models.py`, locate the `Environment` class (around line 31). After the `runner_pool_id` mapped column, add:

```python
    backend: Mapped[str] = mapped_column(String(20), default="venv", nullable=False)
    backend_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
```

The import block at the top of `models.py` already has `JSON` and `String` — no new imports needed.

- [ ] **Step 4: Add fields to the Pydantic schemas**

In `apps/api/app/schemas.py`, make these three targeted edits:

**`EnvironmentCreate`** — add two optional fields after `runner_pool_id`:
```python
class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    python_version: str = "3.12"
    packages: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=2000)
    runner_pool_size: int = Field(default=1, ge=0, le=32)
    runner_pool_max: int | None = Field(default=None, ge=1, le=64)
    runner_pool_id: str | None = None
    backend: str = "venv"
    backend_config: dict = Field(default_factory=dict)
```

**`EnvironmentUpdate`** — add one optional field after `runner_pool_set`:
```python
class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    runner_pool_size: int | None = Field(default=None, ge=0, le=32)
    runner_pool_max: int | None = Field(default=None, ge=1, le=64)
    runner_pool_id: str | None = Field(default=None)
    runner_pool_set: bool = Field(default=False)
    backend_config: dict | None = None
```

**`EnvironmentInfo`** — add two fields after `worker_rss_estimate_bytes`:
```python
class EnvironmentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    is_global: bool
    python_version: str
    packages: list[str]
    status: str
    status_detail: str
    description: str = ""
    runner_pool_size: int = 1
    runner_pool_max: int | None = None
    effective_pool_max: int = 1
    runner_pool_id: str | None = None
    runner_pool_name: str | None = None
    worker_rss_estimate_bytes: int | None = None
    backend: str = "venv"
    backend_config: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 5: Update `_to_info()` and `create_environment` in the router**

In `apps/api/app/routers/environments.py`, update `_to_info` to include the new fields:

```python
def _to_info(env: Environment, pool_name: str | None = None) -> EnvironmentInfo:
    return EnvironmentInfo(
        id=env.id,
        name=env.name,
        is_global=env.is_global,
        python_version=env.python_version,
        packages=list(env.packages),
        status=env.status,
        status_detail=env.status_detail,
        description=env.description,
        runner_pool_size=env.runner_pool_size,
        runner_pool_max=env.runner_pool_max,
        effective_pool_max=_effective_pool_max(env),
        runner_pool_id=env.runner_pool_id,
        runner_pool_name=pool_name,
        worker_rss_estimate_bytes=env.worker_rss_estimate_bytes,
        backend=env.backend,
        backend_config=dict(env.backend_config or {}),
        created_at=env.created_at,
        updated_at=env.updated_at,
    )
```

Update `create_environment` to pass the new fields when constructing `Environment`:

```python
    env = Environment(
        name=body.name,
        python_version=body.python_version,
        packages=body.packages,
        description=body.description,
        runner_pool_size=body.runner_pool_size,
        runner_pool_max=body.runner_pool_max,
        runner_pool_id=body.runner_pool_id,
        backend=body.backend,
        backend_config=body.backend_config,
        status="pending",
    )
```

Update `update_environment` to handle `backend_config` changes (which trigger a rebuild). Add this block inside the `update_environment` handler, after the existing field-update section and before `session.commit()`:

```python
    needs_rebuild = False
    if body.backend_config is not None:
        env.backend_config = body.backend_config
        needs_rebuild = True

    await log_audit(session, "update", "environment", env.id, env.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(env)
    if needs_rebuild:
        env.status = "pending"
        await session.commit()
        background.add_task(build_environment, env.id)
    return _to_info(env, await _pool_name(session, env.runner_pool_id))
```

Note: `update_environment` needs `BackgroundTasks` added to its signature. Change:

```python
async def update_environment(
    env_id: str,
    body: EnvironmentUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
```

to:

```python
async def update_environment(
    env_id: str,
    body: EnvironmentUpdate,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
```

- [ ] **Step 6: Run the failing test to verify it now passes**

```bash
cd apps/api
uv run pytest tests/test_environments.py::test_environment_backend_config_roundtrip -v
```

Expected: PASS.

- [ ] **Step 7: Run the full environment test suite**

```bash
cd apps/api
uv run pytest tests/test_environments.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/models.py apps/api/app/schemas.py apps/api/app/routers/environments.py apps/api/tests/test_environments.py
git commit -m "feat(api): expose backend + backend_config on environment CRUD"
```

---

## Task 3: venv `index_urls` threading

**Files:**
- Modify: `apps/api/app/services/venv.py`

- [ ] **Step 1: Write the failing test**

Add a new file `apps/api/tests/test_venv_service.py`:

```python
"""Unit tests for venv.py — no actual uv subprocess calls."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.venv import _do_build


@pytest.mark.asyncio
async def test_do_build_passes_extra_index_urls(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    with patch("app.services.venv._run", side_effect=fake_run):
        with patch("app.services.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"):
            with patch("app.services.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.venv._local_noodle_packages", return_value=[]):
                    status, _ = await _do_build(
                        "test-env",
                        "3.12",
                        ["pandas"],
                        index_urls=["https://download.pytorch.org/whl/cu121"],
                    )

    assert status == "ready"
    install_call = next(c for c in calls if "pip" in c)
    assert "--extra-index-url" in install_call
    idx = install_call.index("--extra-index-url")
    assert install_call[idx + 1] == "https://download.pytorch.org/whl/cu121"


@pytest.mark.asyncio
async def test_do_build_no_extra_index_urls_when_empty(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    with patch("app.services.venv._run", side_effect=fake_run):
        with patch("app.services.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"):
            with patch("app.services.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.venv._local_noodle_packages", return_value=[]):
                    status, _ = await _do_build("test-env", "3.12", ["pandas"])

    assert status == "ready"
    install_call = next(c for c in calls if "pip" in c)
    assert "--extra-index-url" not in install_call
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api
uv run pytest tests/test_venv_service.py -v
```

Expected: FAIL — `_do_build() got an unexpected keyword argument 'index_urls'`

- [ ] **Step 3: Update `_do_build` to accept and use `index_urls`**

In `apps/api/app/services/venv.py`, replace the `_do_build` function signature and the install section:

```python
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
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_python(env_id)),
                *extra_index_args,
                *to_install,
            )
            log = f"{log}\n{install_log}"

        status = "ready" if code == 0 else "error"
        return status, log.strip()[-4000:]
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}"
```

- [ ] **Step 4: Update `_build_environment_locked` to extract and pass `index_urls`**

In `apps/api/app/services/venv.py`, update `_build_environment_locked` to also read `backend_config`:

```python
async def _build_environment_locked(env_id: str) -> None:
    """Inner builder. Caller must already hold the per-env build lock."""
    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is None:
            return
        env.status = "building"
        env.status_detail = ""
        await session.commit()
        packages = list(env.packages)
        python_version = env.python_version
        index_urls = list((env.backend_config or {}).get("index_urls", []))

    if not settings.enable_venv_builds:
        status, detail = "ready", "Venv builds are disabled in this environment."
    else:
        status, detail = await _do_build(env_id, python_version, packages, index_urls)

    rss_estimate: int | None = None
    if status == "ready" and settings.enable_venv_builds:
        rss_estimate = await _measure_worker_rss(env_id)

    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is not None:
            env.status = status
            env.status_detail = detail
            if rss_estimate is not None:
                env.worker_rss_estimate_bytes = rss_estimate
            await session.commit()
```

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
cd apps/api
uv run pytest tests/test_venv_service.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
cd apps/api
uv run pytest -x -q
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/venv.py apps/api/tests/test_venv_service.py
git commit -m "feat(api): thread index_urls through venv builder to uv pip install"
```

---

## Task 4: PEP 508 marker filtering — Python preflight

**Files:**
- Modify: `apps/api/app/services/package_preflight.py`

- [ ] **Step 1: Write the failing test**

Add a new file `apps/api/tests/test_package_preflight.py`:

```python
"""Tests for package_preflight.py — PEP 508 marker awareness."""
import sys
from unittest.mock import patch

from app.services.package_preflight import _marker_applies, find_missing_packages


def test_marker_applies_eq_match() -> None:
    assert _marker_applies(f"sys_platform == '{sys.platform}'") is True


def test_marker_applies_eq_no_match() -> None:
    other = "linux" if sys.platform != "linux" else "win32"
    assert _marker_applies(f"sys_platform == '{other}'") is False


def test_marker_applies_ne_match() -> None:
    other = "linux" if sys.platform != "linux" else "win32"
    assert _marker_applies(f"sys_platform != '{other}'") is True


def test_marker_applies_ne_no_match() -> None:
    assert _marker_applies(f"sys_platform != '{sys.platform}'") is False


def test_marker_applies_unknown_returns_true() -> None:
    assert _marker_applies("python_version >= '3.9'") is True


def test_find_missing_packages_skips_wrong_platform_req() -> None:
    """A requirement marked for a different platform must not appear as missing."""
    other_platform = "linux" if sys.platform != "linux" else "win32"

    fake_manifests = [
        type("M", (), {
            "id": "my_node",
            "requirements": [f"some-pkg>=1.0; sys_platform=='{other_platform}'"],
        })()
    ]

    graph = {"nodes": [{"type": "my_node", "id": "n1"}]}
    with patch("app.services.package_preflight.node_registry") as mock_reg:
        mock_reg.manifests.return_value = fake_manifests
        result = find_missing_packages(graph, env_packages=[])

    assert result == {}, f"Expected no missing packages but got {result}"


def test_find_missing_packages_includes_matching_platform_req() -> None:
    """A requirement whose marker matches the current platform IS reported missing."""
    fake_manifests = [
        type("M", (), {
            "id": "my_node",
            "requirements": [f"some-pkg>=1.0; sys_platform=='{sys.platform}'"],
        })()
    ]

    graph = {"nodes": [{"type": "my_node", "id": "n1"}]}
    with patch("app.services.package_preflight.node_registry") as mock_reg:
        mock_reg.manifests.return_value = fake_manifests
        result = find_missing_packages(graph, env_packages=[])

    assert f"some-pkg>=1.0; sys_platform=='{sys.platform}'" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api
uv run pytest tests/test_package_preflight.py -v
```

Expected: FAIL — `ImportError: cannot import name '_marker_applies'`

- [ ] **Step 3: Implement `_marker_applies` and update `find_missing_packages`**

Replace the contents of `apps/api/app/services/package_preflight.py`:

```python
"""Pre-run check: does the resolved env have every package its nodes need?"""

from __future__ import annotations

import re
import sys

from noodle.packages import canonical_package_name
from noodle.sdk import registry as node_registry

_MARKER_EQ = re.compile(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]")
_MARKER_NE = re.compile(r"sys_platform\s*!=\s*['\"]([^'\"]+)['\"]")


def _marker_applies(marker: str) -> bool:
    """Return True if the PEP 508 sys_platform marker applies to this server."""
    m = _MARKER_EQ.search(marker)
    if m:
        return sys.platform == m.group(1)
    m = _MARKER_NE.search(marker)
    if m:
        return sys.platform != m.group(1)
    return True  # unknown marker: safe fallback — treat as applicable


def _requirement_applies(req: str) -> bool:
    """Return False if req has a sys_platform marker that excludes this server."""
    if ";" not in req:
        return True
    _, marker = req.split(";", 1)
    return _marker_applies(marker.strip())


def find_missing_packages(graph: dict, env_packages: list[str]) -> dict[str, list[str]]:
    """Return {missing_specifier: [node ids needing it]} for a graph + env.

    Requirements whose PEP 508 sys_platform marker does not match the current
    server platform are skipped — they are not needed here.
    """
    reqs_by_type = {
        m.id: m.requirements for m in node_registry.manifests() if m.requirements
    }
    have = {canonical_package_name(p) for p in env_packages if p.strip()}
    missing: dict[str, list[str]] = {}
    nodes = (graph or {}).get("nodes") or [] if isinstance(graph, dict) else []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        for req in reqs_by_type.get(n.get("type"), []):
            if not _requirement_applies(req):
                continue
            if canonical_package_name(req) not in have:
                missing.setdefault(req, []).append(str(n.get("id") or ""))
    return missing


def format_missing(missing: dict[str, list[str]]) -> str:
    parts = [f"{pkg} (needed by {', '.join(ids)})" for pkg, ids in missing.items()]
    return (
        "This workflow's environment is missing packages required by its nodes: "
        + "; ".join(parts)
        + ". Add them to the environment or switch the workflow to an env that has them."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/api
uv run pytest tests/test_package_preflight.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 5: Run the full test suite**

```bash
cd apps/api
uv run pytest -x -q
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/package_preflight.py apps/api/tests/test_package_preflight.py
git commit -m "feat(api): filter PEP 508 sys_platform markers in package preflight"
```

---

## Task 5: `GET /environments/backends` endpoint

**Files:**
- Modify: `apps/api/app/routers/environments.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_environments.py`:

```python
async def test_backends_endpoint_returns_platform(client: AsyncClient) -> None:
    import sys
    resp = await client.get("/environments/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == sys.platform
    assert data["venv"]["available"] is True  # uv is always present in dev
    assert "conda" in data
    assert "pixi" in data
    assert "docker" in data
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api
uv run pytest tests/test_environments.py::test_backends_endpoint_returns_platform -v
```

Expected: FAIL — 404 or 405.

- [ ] **Step 3: Add the endpoint to the router**

In `apps/api/app/routers/environments.py`, add this route **before** the `@router.get("/{env_id}")` route (around line 187) so FastAPI doesn't interpret "backends" as an `env_id`. Also add `import shutil, sys` at the top of the file.

Add `import shutil` and `import sys` to the imports at the top of the router (they are stdlib, no package needed). Then add this route:

```python
@router.get("/backends")
async def list_backends() -> dict:
    """Return the server platform and which environment backends are available.

    The ``platform`` field is used by the frontend to evaluate PEP 508
    sys_platform markers when checking for missing packages.
    """
    import shutil
    import sys

    uv_path = shutil.which("uv")
    return {
        "platform": sys.platform,
        "venv": {
            "available": uv_path is not None,
            "version": None,
            "managed": False,
        },
        "conda": {"available": False, "version": None, "managed": False},
        "pixi": {"available": False, "version": None, "managed": False},
        "docker": {"available": False, "version": None, "managed": False},
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd apps/api
uv run pytest tests/test_environments.py::test_backends_endpoint_returns_platform -v
```

Expected: PASS.

- [ ] **Step 5: Run the full environment tests**

```bash
cd apps/api
uv run pytest tests/test_environments.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/environments.py apps/api/tests/test_environments.py
git commit -m "feat(api): add GET /environments/backends returning platform + availability"
```

---

## Task 6: Frontend — `Environment` type update

**Files:**
- Modify: `apps/web/src/types.ts`

- [ ] **Step 1: Update the `Environment` interface**

In `apps/web/src/types.ts`, find the `Environment` interface (around line 211) and add two fields before `created_at`:

```typescript
export interface Environment {
  id: string;
  name: string;
  is_global: boolean;
  python_version: string;
  packages: string[];
  status: string;
  status_detail: string;
  description: string;
  runner_pool_size: number;
  runner_pool_max: number | null;
  effective_pool_max: number;
  runner_pool_id: string | null;
  runner_pool_name: string | null;
  worker_rss_estimate_bytes: number | null;
  backend: string;
  backend_config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 2: Run TypeScript type-check to verify no errors**

```bash
cd apps/web
npx tsc --noEmit
```

Expected: exit 0 (or same errors as before — do not introduce new ones).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/types.ts
git commit -m "feat(web): add backend + backend_config to Environment type"
```

---

## Task 7: Frontend — PEP 508 marker filtering in `missingFor`

**Files:**
- Modify: `apps/web/src/editor/missingPackages.ts`
- Modify: `apps/web/src/editor/missingPackages.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `apps/web/src/editor/missingPackages.test.ts`:

```typescript
import { evaluateMarker } from "./missingPackages";

describe("evaluateMarker", () => {
  it("returns true for eq match", () => {
    expect(evaluateMarker("sys_platform == 'linux'", "linux")).toBe(true);
  });

  it("returns false for eq no-match", () => {
    expect(evaluateMarker("sys_platform == 'win32'", "linux")).toBe(false);
  });

  it("returns true for ne match (different platform)", () => {
    expect(evaluateMarker("sys_platform != 'win32'", "linux")).toBe(true);
  });

  it("returns false for ne no-match (same platform)", () => {
    expect(evaluateMarker("sys_platform != 'linux'", "linux")).toBe(false);
  });

  it("returns true for unknown marker (safe fallback)", () => {
    expect(evaluateMarker("python_version >= '3.9'", "linux")).toBe(true);
  });
});

describe("missingFor with platform filtering", () => {
  it("skips requirements whose sys_platform marker does not match", () => {
    const reqs = [
      "pyzbar>=0.1.9; sys_platform!='win32'",
      "zxing-cpp>=2.2; sys_platform=='win32'",
      "pillow>=10.0",
    ];
    // On linux: pyzbar applies, zxing-cpp does not, pillow always applies.
    // If nothing is installed, only pyzbar and pillow should be missing.
    const missing = missingFor(reqs, [], "linux");
    expect(missing).toContain("pyzbar>=0.1.9; sys_platform!='win32'");
    expect(missing).toContain("pillow>=10.0");
    expect(missing).not.toContain("zxing-cpp>=2.2; sys_platform=='win32'");
  });

  it("shows all as missing when no platform provided (backwards compat)", () => {
    const reqs = ["pyzbar>=0.1.9; sys_platform!='win32'", "pillow>=10.0"];
    const missing = missingFor(reqs, []);
    expect(missing).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/web
npx vitest run src/editor/missingPackages.test.ts
```

Expected: FAIL — `evaluateMarker is not exported` / `missingFor` doesn't accept 3 args.

- [ ] **Step 3: Implement `evaluateMarker` and update `missingFor`**

Replace `apps/web/src/editor/missingPackages.ts` with:

```typescript
export function canonicalName(spec: string): string {
  const head = spec.trim().split(";")[0].trim();
  const match = head.match(/[A-Za-z0-9][A-Za-z0-9._-]*/);
  const name = match ? match[0] : head;
  return name.replace(/[-_.]+/g, "-").toLowerCase();
}

export function evaluateMarker(marker: string, platform: string): boolean {
  const eq = marker.match(/sys_platform\s*==\s*['"]([^'"]+)['"]/);
  if (eq) return platform === eq[1];
  const ne = marker.match(/sys_platform\s*!=\s*['"]([^'"]+)['"]/);
  if (ne) return platform !== ne[1];
  return true; // unknown marker: safe fallback
}

export function missingFor(
  requirements: string[],
  installed: string[],
  platform?: string,
): string[] {
  const have = new Set(installed.filter((p) => p.trim()).map(canonicalName));
  return requirements.filter((r) => {
    const semicolonIdx = r.indexOf(";");
    if (semicolonIdx !== -1 && platform) {
      const marker = r.slice(semicolonIdx + 1).trim();
      if (!evaluateMarker(marker, platform)) return false;
    }
    return !have.has(canonicalName(r));
  });
}

export function parseRequirementsTxt(text: string): string[] {
  const out: string[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.split("#")[0].trim();
    if (!line || line.startsWith("-")) continue;
    out.push(line.split(";")[0].trim());
  }
  return out;
}

export interface PackageDiff {
  toAdd: string[];
  alreadyPresent: string[];
  installedNotInFile: string[];
}

export function diffPackages(fileSpecs: string[], installed: string[]): PackageDiff {
  const installedByCanon = new Map(installed.map((p) => [canonicalName(p), p]));
  const fileCanon = new Set(fileSpecs.map(canonicalName));
  const toAdd: string[] = [];
  const alreadyPresent: string[] = [];
  for (const spec of fileSpecs) {
    (installedByCanon.has(canonicalName(spec)) ? alreadyPresent : toAdd).push(spec);
  }
  const installedNotInFile = installed.filter((p) => !fileCanon.has(canonicalName(p)));
  return { toAdd, alreadyPresent, installedNotInFile };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/web
npx vitest run src/editor/missingPackages.test.ts
```

Expected: PASS (all tests including the original ones).

- [ ] **Step 5: Run the full frontend test suite**

```bash
cd apps/web
npx vitest run
```

Expected: no new failures.

- [ ] **Step 6: TypeScript check**

```bash
cd apps/web
npx tsc --noEmit
```

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/missingPackages.ts apps/web/src/editor/missingPackages.test.ts
git commit -m "feat(web): add evaluateMarker + platform-aware missingFor for PEP 508 markers"
```

---

## Task 8: Wire platform into frontend missing-package checks

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`
- Modify: `apps/web/src/editor/NodeCard.tsx`
- Modify: `apps/web/src/editor/NDVPanels.tsx`
- Modify: `apps/web/src/PackageDrawer.tsx`

The `missingFor()` calls in these files need a `platform` string. The platform comes from `GET /environments/backends`. Add a small hook and thread it through.

- [ ] **Step 1: Write the failing test**

In `apps/api/tests/test_environments.py`, add a test confirming the API integration is coherent (the TypeScript callers are hard to integration-test from the API layer, so this verifies the endpoint returns what callers need):

```python
async def test_backends_platform_field_is_valid_string(client: AsyncClient) -> None:
    data = (await client.get("/environments/backends")).json()
    assert isinstance(data["platform"], str)
    assert len(data["platform"]) > 0
```

Run: `uv run pytest tests/test_environments.py::test_backends_platform_field_is_valid_string -v` → PASS (endpoint already works).

- [ ] **Step 2: Check which files call `missingFor`**

```bash
grep -rn "missingFor(" apps/web/src --include="*.tsx" --include="*.ts"
```

Expected output lists: `NDVPanels.tsx`, `NodeCard.tsx`, `NodeDetails.tsx`, `PackageDrawer.tsx`.

- [ ] **Step 3: Add a `useServerPlatform` hook**

Create `apps/web/src/hooks/useServerPlatform.ts`:

```typescript
import { useEffect, useState } from "react";
import { apiFetch } from "../api";

let cached: string | null = null;

export function useServerPlatform(): string | null {
  const [platform, setPlatform] = useState<string | null>(cached);

  useEffect(() => {
    if (cached) return;
    apiFetch("/environments/backends")
      .then((r) => r.json())
      .then((d) => {
        cached = d.platform ?? null;
        setPlatform(cached);
      })
      .catch(() => {});
  }, []);

  return platform;
}
```

(If `apiFetch` is not the right helper in this codebase, check `apps/web/src/api.ts` for the correct fetch wrapper and use that. The pattern is: fetch `/environments/backends`, read `.platform`.)

- [ ] **Step 4: Verify the api helper name**

```bash
grep -n "export.*fetch\|export.*apiFetch\|export.*client" apps/web/src/api.ts | head -10
```

Use whatever fetch wrapper is exported. If none, use `fetch("/api/environments/backends")` directly.

- [ ] **Step 5: Thread platform into each `missingFor` callsite**

In each of the four files, import `useServerPlatform` and pass its return value as the third argument to `missingFor`:

**`apps/web/src/editor/NodeDetails.tsx`** (line ~3003):
```typescript
import { useServerPlatform } from "../hooks/useServerPlatform";
// inside the component:
const platform = useServerPlatform();
// update the existing call:
const missingPkgs = missingFor(manifest.requirements ?? [], envPackages, platform ?? undefined);
```

**`apps/web/src/editor/NodeCard.tsx`** — same pattern.

**`apps/web/src/editor/NDVPanels.tsx`** — same pattern.

**`apps/web/src/PackageDrawer.tsx`** — same pattern.

- [ ] **Step 6: TypeScript check**

```bash
cd apps/web
npx tsc --noEmit
```

Expected: exit 0.

- [ ] **Step 7: Run all frontend tests**

```bash
cd apps/web
npx vitest run
```

Expected: no failures.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/hooks/useServerPlatform.ts \
        apps/web/src/editor/NodeDetails.tsx \
        apps/web/src/editor/NodeCard.tsx \
        apps/web/src/editor/NDVPanels.tsx \
        apps/web/src/PackageDrawer.tsx \
        apps/api/tests/test_environments.py
git commit -m "feat(web): wire server platform into missingFor for PEP 508 marker filtering"
```

---

## Task 9: Swap `barcode_qr_decode` to `zxing-cpp` on Windows

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`

This is the concrete payoff of Phase 1 — the first node that uses platform-adaptive requirements.

- [ ] **Step 1: Write the failing test**

In `packages/nodes/tests/test_document_intelligence.py`, add:

```python
def test_barcode_qr_decode_requirements_are_platform_adaptive() -> None:
    from noodle_nodes.document_intelligence import barcode_qr_decode
    from noodle.sdk import registry

    manifest = next(m for m in registry.manifests() if m.id == "barcode_qr_decode")
    reqs = manifest.requirements

    win_reqs = [r for r in reqs if "win32" in r and "zxing" in r]
    nix_reqs = [r for r in reqs if "win32" in r and "pyzbar" in r]
    # zxing-cpp is win32-only, pyzbar is non-win32
    assert any("zxing" in r and "win32" in r for r in reqs), "zxing-cpp win32 req missing"
    assert any("pyzbar" in r and "win32" in r for r in reqs), "pyzbar non-win32 req missing"
```

Run: `pytest packages/nodes/tests/test_document_intelligence.py::test_barcode_qr_decode_requirements_are_platform_adaptive -v` → FAIL.

- [ ] **Step 2: Update the `barcode_qr_decode` node**

In `packages/nodes/noodle_nodes/document_intelligence.py`, find the `barcode_qr_decode` node's `@node` decorator and update its `requirements` list:

```python
@node(
    name="Barcode / QR Decode",
    id="barcode_qr_decode",
    category="Document Intelligence",
    icon="scan",
    requirements=[
        "zxing-cpp>=2.2; sys_platform=='win32'",
        "pyzbar>=0.1.9; sys_platform!='win32'",
        "pillow>=10.0",
    ],
    params={
        "input": {"description": "Image artifact containing the barcode or QR code"},
    },
)
def barcode_qr_decode(input=None):
    import sys
    from PIL import Image as _Image
    from noodle.artifacts import read_bytes as _read_bytes

    if input is None:
        raise ValueError("No image provided.")

    data = _read_bytes(input)
    img = _Image.open(__import__("io").BytesIO(data))

    if sys.platform == "win32":
        try:
            import zxing_cpp as _zx
        except ImportError as exc:
            raise RuntimeError(
                "zxing-cpp is required on Windows. Add 'zxing-cpp>=2.2' to the workflow "
                "environment, rebuild it, then run again."
            ) from exc
        results = _zx.read_barcodes(img)
        decoded = [r.text for r in results if r.text]
    else:
        try:
            from pyzbar import pyzbar as _pyzbar
        except ImportError as exc:
            raise RuntimeError(
                "pyzbar is required on Linux/macOS. Add 'pyzbar>=0.1.9' to the workflow "
                "environment, rebuild it, then run again."
            ) from exc
        decoded = [d.data.decode("utf-8", "replace") for d in _pyzbar.decode(img)]

    if not decoded:
        raise ValueError("No barcode or QR code detected in the provided image.")

    return {"main": decoded[0] if len(decoded) == 1 else decoded}
```

- [ ] **Step 3: Run the test to verify it passes**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py::test_barcode_qr_decode_requirements_are_platform_adaptive -v
```

Expected: PASS.

- [ ] **Step 4: Run the full document intelligence test suite**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -v
```

Expected: all pass (import-safety test still passes — neither pyzbar nor zxing_cpp imported at module scope).

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): use zxing-cpp on Windows and pyzbar on Linux/macOS for barcode decode"
```

---

## Self-Review

**Spec coverage check:**
- [x] `index_urls` on venv → Task 3
- [x] PEP 508 markers in preflight → Task 4
- [x] PEP 508 markers in frontend `missingFor` → Task 7
- [x] `GET /environments/backends` returning platform → Task 5
- [x] DB migration (`backend` + `backend_config`) → Task 1
- [x] Model + schema changes → Task 2
- [x] `backend`/`backend_config` in `_to_info` + create + update → Task 2
- [x] `backend` + `backend_config` in frontend `Environment` type → Task 6
- [x] Frontend callers updated to pass platform → Task 8
- [x] `barcode_qr_decode` PEP 508 markers → Task 9
- [x] Spec says "conda tab never greyed out" — Phase 2 concern, not Phase 1

**Placeholder scan:** No TBD or TODO found.

**Type consistency:** `missingFor(reqs, installed, platform?)` used consistently across Tasks 7 and 8. `_do_build(env_id, python_version, packages, index_urls?)` consistent across Tasks 3 and 4.
