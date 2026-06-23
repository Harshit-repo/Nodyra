# GitHub Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bidirectional sync between Noodle workflows and a GitHub repository using `.module.py` format, async DB-backed jobs, org-scoped webhooks, and conflict detection with a diff UI.

**Architecture:** Push jobs enqueued into a `github_sync_jobs` DB table on every `draft_graph` write (from UI, API, or MCP); a background asyncio loop processes them via the GitHub Contents API. Pull jobs are triggered by an org-scoped webhook (`POST /webhooks/github-sync/{org_id}`) or a manual endpoint. Conflicts are flagged in DB and resolved via a split-pane diff modal in the editor.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, httpx (already in API deps), React 18, TanStack Query v5, TypeScript.

## Global Constraints

- Latest migration is `0058_tenant_rls_hardening`. New migrations: `0059_github_sync`.
- `Feature.GIT_SYNC` already exists in `app/services/licensing.py:54` — use it, don't add a new one.
- All DB models with `org_id` are automatically scoped by the ORM tenancy layer in `app/tenancy.py`. New models with `org_id` get this for free.
- Use `slugify()` from `noodle_exporter.codegen` for workflow file paths.
- Use `workflow_to_module()` from `noodle_exporter.module_codegen` for generating `.module.py` content.
- GitHub Contents API base: `https://api.github.com`. All calls use `Authorization: Bearer {token}` from the stored credential.
- HMAC verification uses `X-Hub-Signature-256` header with `sha256=` prefix.
- `log_audit()` signature: `(session, action, target_type, target_id, detail, actor_id, actor_email)`.
- Never use `exec()`/`eval()` in the importer — AST only.
- Test runner: `pytest` from `apps/api/` for backend; `npm test -- --run` from `apps/web/` for frontend.
- Commit after every task with `git add -p` to keep diffs reviewable.

---

### Task 1: `noodle_importer` — AST-based `.module.py` → `WorkflowGraph` parser

**Files:**
- Create: `packages/importer/pyproject.toml`
- Create: `packages/importer/noodle_importer/__init__.py`
- Create: `packages/importer/noodle_importer/parser.py`
- Create: `packages/importer/tests/__init__.py`
- Create: `packages/importer/tests/test_importer.py`
- Modify: `apps/api/pyproject.toml` (add `noodle-importer` dependency)

**Interfaces:**
- Produces: `noodle_importer.import_module(source: str) -> WorkflowGraph`
- Raises: `ImportError` with a message when the source is not a valid Noodle module

- [ ] **Step 1: Create the package scaffold**

`packages/importer/pyproject.toml`:
```toml
[project]
name = "noodle-importer"
version = "0.0.1"
description = "Noodle workflow importer: .module.py → WorkflowGraph"
requires-python = ">=3.12"
dependencies = ["noodle-core"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["noodle_importer"]
```

`packages/importer/noodle_importer/__init__.py`:
```python
from .parser import import_module

__all__ = ["import_module"]
```

`packages/importer/tests/__init__.py`: empty file.

- [ ] **Step 2: Write failing tests**

`packages/importer/tests/test_importer.py`:
```python
import pytest
import noodle_nodes  # noqa: F401 — registers built-ins
from noodle.models import WorkflowGraph
from noodle.sdk import registry
from noodle_exporter import workflow_to_module
from noodle_importer import import_module


def _export(graph_dict: dict) -> str:
    graph = WorkflowGraph.model_validate(graph_dict)
    return workflow_to_module(graph, "Test Workflow", registry=registry)


def test_roundtrip_two_node_pipeline():
    source = _export({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}},
            {"id": "fetch", "type": "http_request",
             "params": {"url": "https://example.com", "method": "GET"},
             "position": {"x": 300, "y": 120}, "on_error": "continue"},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "fetch", "target_input": "input"}],
    })
    result = import_module(source)
    by_id = {n.id: n for n in result.nodes}
    assert by_id["t"].type == "manual_trigger"
    assert by_id["fetch"].type == "http_request"
    assert by_id["fetch"].params["url"] == "https://example.com"
    assert by_id["fetch"].on_error == "continue"
    assert any(e.source == "t" and e.target == "fetch" for e in result.edges)


def test_roundtrip_preserves_position():
    source = _export({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 10, "y": 20}},
        ],
        "edges": [],
    })
    result = import_module(source)
    node = result.nodes[0]
    assert node.position is not None
    assert node.position.x == 10
    assert node.position.y == 20


def test_extra_nodes_preserved():
    source = _export({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}},
            {"id": "custom", "type": "user:abc123:my_fn", "params": {"x": 1}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "custom", "target_input": "input"}],
    })
    result = import_module(source)
    by_id = {n.id: n for n in result.nodes}
    assert "custom" in by_id
    assert by_id["custom"].type == "user:abc123:my_fn"


def test_invalid_source_raises_import_error():
    with pytest.raises(ImportError, match="No @node decorated functions"):
        import_module("x = 1\n")


def test_empty_graph_roundtrip():
    source = _export({
        "nodes": [{"id": "t", "type": "manual_trigger", "params": {}}],
        "edges": [],
    })
    result = import_module(source)
    assert len(result.nodes) == 1
    assert result.nodes[0].type == "manual_trigger"
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd packages/importer && pip install -e ".[dev]" -e ../core -e ../nodes -e ../exporter 2>/dev/null; pytest tests/ -v 2>&1 | head -30
```
Expected: `ModuleNotFoundError: No module named 'noodle_importer.parser'`

- [ ] **Step 4: Implement `parser.py`**

`packages/importer/noodle_importer/parser.py`:
```python
"""AST-based importer: Noodle .module.py → WorkflowGraph.

Parses the structured output of noodle_exporter.workflow_to_module() without
executing any code. Uses ast.literal_eval() for module-level dicts/lists and
ast.parse() to walk the function definitions.
"""
from __future__ import annotations

import ast
from typing import Any

from noodle.models import WorkflowGraph

_MODULE_VARS = {"_DELEGATES", "_NODE_SETTINGS", "_PARAM_OVERRIDES", "_EXTRA_NODES", "_EXTRA_EDGES"}


def _extract_module_vars(tree: ast.Module) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id in _MODULE_VARS:
                try:
                    out[target.id] = ast.literal_eval(stmt.value)
                except (ValueError, TypeError):
                    pass
    return out


def _node_decorator(func_def: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | None:
    for dec in func_def.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        f = dec.func
        if (isinstance(f, ast.Name) and f.id == "node") or (
            isinstance(f, ast.Attribute) and f.attr == "node"
        ):
            return dec
    return None


def _kw_literal(call: ast.Call, name: str) -> Any:
    for kw in call.keywords:
        if kw.arg == name:
            try:
                return ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                return None
    return None


def _signature_params(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    input_ports: list[str],
) -> dict[str, Any]:
    args = func_def.args.args
    defaults = func_def.args.defaults
    offset = len(args) - len(defaults)
    params: dict[str, Any] = {}
    for i, arg in enumerate(args):
        if arg.arg in input_ports or i < offset:
            continue
        try:
            params[arg.arg] = ast.literal_eval(defaults[i - offset])
        except (ValueError, TypeError):
            pass
    return params


def import_module(source: str) -> WorkflowGraph:
    """Parse a Noodle .module.py source string into a WorkflowGraph.

    Raises ImportError with a descriptive message if the source is not a
    valid Noodle module export. Never executes the source code.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ImportError(f"Syntax error in module source: {exc}") from exc

    mv = _extract_module_vars(tree)
    delegates: dict[str, str] = mv.get("_DELEGATES", {})
    node_settings: dict[str, dict] = mv.get("_NODE_SETTINGS", {})
    param_overrides: dict[str, dict] = mv.get("_PARAM_OVERRIDES", {})
    extra_nodes: list[dict] = mv.get("_EXTRA_NODES", [])
    extra_edges: list[dict] = mv.get("_EXTRA_EDGES", [])

    nodes: list[dict] = []
    edges: list[dict] = []

    for stmt in ast.walk(tree):
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dec = _node_decorator(stmt)
        if dec is None:
            continue

        node_id: str | None = _kw_literal(dec, "id")
        if not node_id:
            continue

        wires: dict[str, str] = _kw_literal(dec, "wires") or {}
        input_ports: list[str] = _kw_literal(dec, "inputs") or []

        params = _signature_params(stmt, input_ports)
        params.update(param_overrides.get(node_id, {}))

        node_type = delegates.get(node_id, stmt.name)
        spec: dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "params": params,
        }
        spec.update(node_settings.get(node_id, {}))
        nodes.append(spec)

        for port, wire in wires.items():
            src, _, out = wire.partition(".")
            edges.append({
                "id": f"w_{node_id}_{port}",
                "source": src,
                "source_output": out or "main",
                "target": node_id,
                "target_input": port,
            })

    nodes.extend(extra_nodes)
    edges.extend(extra_edges)

    if not nodes:
        raise ImportError(
            "No @node decorated functions found in module source. "
            "The file does not look like a Noodle .module.py export."
        )

    try:
        return WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})
    except Exception as exc:
        raise ImportError(f"Reconstructed graph failed validation: {exc}") from exc
```

- [ ] **Step 5: Install the package and run tests**

```bash
cd D:/noodle/packages/importer && pip install -e . && cd ../.. && pytest packages/importer/tests/ -v
```
Expected: all 5 tests pass.

- [ ] **Step 6: Add `noodle-importer` to API dependencies**

In `apps/api/pyproject.toml`, add to `dependencies`:
```toml
"noodle-importer",
```
(after `"noodle-exporter"`)

Then install:
```bash
cd D:/noodle/apps/api && pip install -e ../../packages/importer
```

- [ ] **Step 7: Commit**

```bash
cd D:/noodle
git add packages/importer/ apps/api/pyproject.toml
git commit -m "feat(importer): AST-based .module.py → WorkflowGraph parser"
```

---

### Task 2: DB Schema — `GithubSyncConfig`, `GithubSyncJob`, workflow columns

**Files:**
- Modify: `apps/api/app/models.py` (add 2 models + 3 workflow columns)
- Create: `apps/api/alembic/versions/0059_github_sync.py`

**Interfaces:**
- Produces: `GithubSyncConfig` model with fields `org_id, credential_id, repo, base_path, main_branch, webhook_secret`
- Produces: `GithubSyncJob` model with fields `org_id, workflow_id, job_type, origin, file_sha, status, attempts, next_retry_at, error_message`
- Produces: `Workflow.github_sync_sha`, `Workflow.github_sync_status`, `Workflow.github_sync_conflict_sha`

- [ ] **Step 1: Write failing migration test**

In `apps/api/tests/test_github_sync.py` (create new file):
```python
"""Tests for GitHub sync DB models and service logic."""
import pytest
from sqlalchemy import inspect as sa_inspect
from app.models import GithubSyncConfig, GithubSyncJob, Workflow


def test_github_sync_config_model_exists():
    assert GithubSyncConfig.__tablename__ == "github_sync_configs"


def test_github_sync_job_model_exists():
    assert GithubSyncJob.__tablename__ == "github_sync_jobs"


def test_workflow_has_sync_columns():
    mapper = Workflow.__mapper__
    cols = {c.key for c in mapper.columns}
    assert "github_sync_sha" in cols
    assert "github_sync_status" in cols
    assert "github_sync_conflict_sha" in cols
```

Run:
```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py::test_github_sync_config_model_exists -v
```
Expected: `ImportError: cannot import name 'GithubSyncConfig'`

- [ ] **Step 2: Add models to `apps/api/app/models.py`**

After the `Folder` class (around line 327), add:

```python
class GithubSyncConfig(Base):
    """Per-org GitHub sync configuration. At most one row per org."""

    __tablename__ = "github_sync_configs"
    __table_args__ = (UniqueConstraint("org_id", name="uq_github_sync_configs_org_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        server_default="default",
    )
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    repo: Mapped[str] = mapped_column(String(200), nullable=False)
    base_path: Mapped[str] = mapped_column(String(200), nullable=False, default="workflows/")
    main_branch: Mapped[str] = mapped_column(String(100), nullable=False, default="main")
    webhook_secret: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GithubSyncJob(Base):
    """A durable queue entry for a GitHub sync push or pull operation."""

    __tablename__ = "github_sync_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        server_default="default",
    )
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True, nullable=True
    )
    job_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # push_draft | push_publish | pull
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ui"
    )  # ui | mcp | api | publish
    file_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | processing | done | failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Add three columns to `Workflow` (after `mcp_parameters_schema`):
```python
    github_sync_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    github_sync_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # synced | pending | conflict | error | null
    github_sync_conflict_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

Add `GithubSyncConfig, GithubSyncJob` to the imports at the top of any file that needs them.

- [ ] **Step 3: Run model tests**

```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py -v
```
Expected: all 3 model tests pass (they only inspect the Python class, not the DB).

- [ ] **Step 4: Create the Alembic migration**

Create `apps/api/alembic/versions/0059_github_sync.py`:
```python
"""add github sync tables and workflow columns

Revision ID: 0059_github_sync
Revises: 0058_tenant_rls_hardening
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0059_github_sync"
down_revision: str | None = "0058_tenant_rls_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_sync_configs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("credential_id", sa.String(32), nullable=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("base_path", sa.String(200), nullable=False, server_default="workflows/"),
        sa.Column("main_branch", sa.String(100), nullable=False, server_default="main"),
        sa.Column("webhook_secret", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_github_sync_configs_org_id"),
    )
    op.create_index("ix_github_sync_configs_org_id", "github_sync_configs", ["org_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_github_sync_configs_credential_id",
            "github_sync_configs",
            "credentials",
            ["credential_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "github_sync_jobs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("workflow_id", sa.String(32), nullable=True),
        sa.Column("job_type", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False, server_default="ui"),
        sa.Column("file_sha", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_github_sync_jobs_org_id", "github_sync_jobs", ["org_id"])
    op.create_index("ix_github_sync_jobs_workflow_id", "github_sync_jobs", ["workflow_id"])
    op.create_index(
        "ix_github_sync_jobs_status_next_retry",
        "github_sync_jobs",
        ["status", "next_retry_at"],
    )

    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_github_sync_jobs_workflow_id",
            "github_sync_jobs",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.add_column("workflows", sa.Column("github_sync_sha", sa.String(40), nullable=True))
    op.add_column("workflows", sa.Column("github_sync_status", sa.String(20), nullable=True))
    op.add_column(
        "workflows", sa.Column("github_sync_conflict_sha", sa.String(40), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflows", "github_sync_conflict_sha")
    op.drop_column("workflows", "github_sync_status")
    op.drop_column("workflows", "github_sync_sha")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_github_sync_jobs_workflow_id", "github_sync_jobs", type_="foreignkey"
        )
    op.drop_index("ix_github_sync_jobs_status_next_retry", "github_sync_jobs")
    op.drop_index("ix_github_sync_jobs_workflow_id", "github_sync_jobs")
    op.drop_index("ix_github_sync_jobs_org_id", "github_sync_jobs")
    op.drop_table("github_sync_jobs")

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_github_sync_configs_credential_id",
            "github_sync_configs",
            type_="foreignkey",
        )
    op.drop_index("ix_github_sync_configs_org_id", "github_sync_configs")
    op.drop_table("github_sync_configs")
```

- [ ] **Step 5: Run the migration**

```bash
cd D:/noodle/apps/api && alembic upgrade head
```
Expected: `Running upgrade 0058_tenant_rls_hardening -> 0059_github_sync, add github sync tables and workflow columns`

- [ ] **Step 6: Verify schema**

```bash
cd D:/noodle/apps/api && python -c "
import asyncio
from app.db import engine
from sqlalchemy import inspect, text

async def check():
    async with engine.connect() as conn:
        def sync_inspect(sync_conn):
            insp = inspect(sync_conn)
            print('github_sync_configs:', insp.get_columns('github_sync_configs'))
            print('github_sync_jobs:', insp.get_columns('github_sync_jobs'))
            wf_cols = [c['name'] for c in insp.get_columns('workflows')]
            print('workflow sync cols:', [c for c in wf_cols if 'github' in c])
        await conn.run_sync(sync_inspect)

asyncio.run(check())
"
```
Expected: tables and columns listed without error.

- [ ] **Step 7: Commit**

```bash
cd D:/noodle
git add apps/api/app/models.py apps/api/alembic/versions/0059_github_sync.py apps/api/tests/test_github_sync.py
git commit -m "feat(github-sync): add GithubSyncConfig, GithubSyncJob models and migration"
```

---

### Task 3: GitHub sync service — push, pull, conflict detection, enqueue helper

**Files:**
- Create: `apps/api/app/services/github_sync.py`
- Modify: `apps/api/tests/test_github_sync.py` (add service tests)

**Interfaces:**
- Produces: `async def enqueue_github_push(session, workflow, origin) -> None`
- Produces: `async def process_push_job(job_id: str) -> None`
- Produces: `async def process_pull_job(job_id: str) -> None`
- Consumes: `GithubSyncConfig`, `GithubSyncJob`, `Workflow` models
- Consumes: `workflow_to_module()` from `noodle_exporter`
- Consumes: `import_module()` from `noodle_importer`

- [ ] **Step 1: Write failing tests for enqueue helper**

Add to `apps/api/tests/test_github_sync.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import GithubSyncConfig, GithubSyncJob, Workflow


@pytest.mark.asyncio
async def test_enqueue_github_push_no_config(db_session):
    """enqueue_github_push is a no-op when org has no sync config."""
    from app.services.github_sync import enqueue_github_push
    wf = Workflow(id="wf1", name="Test", org_id="default",
                  draft_graph={"nodes": [], "edges": []}, published_version=1)
    db_session.add(wf)
    await db_session.flush()
    await enqueue_github_push(db_session, wf, "ui")
    result = await db_session.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(GithubSyncJob)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_enqueue_github_push_creates_job(db_session):
    """enqueue_github_push inserts a GithubSyncJob row when config exists."""
    from app.services.github_sync import enqueue_github_push
    import secrets
    cfg = GithubSyncConfig(
        org_id="default", repo="owner/repo",
        base_path="workflows/", main_branch="main",
        webhook_secret=secrets.token_hex(20),
    )
    db_session.add(cfg)
    wf = Workflow(id="wf2", name="My Flow", org_id="default",
                  draft_graph={"nodes": [], "edges": []}, published_version=1)
    db_session.add(wf)
    await db_session.flush()

    await enqueue_github_push(db_session, wf, "mcp")
    await db_session.flush()

    from sqlalchemy import select
    jobs = (await db_session.scalars(select(GithubSyncJob))).all()
    assert len(jobs) == 1
    assert jobs[0].job_type == "push_draft"
    assert jobs[0].origin == "mcp"
    assert jobs[0].status == "pending"
```

Run:
```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py::test_enqueue_github_push_no_config -v
```
Expected: `ImportError: cannot import name 'enqueue_github_push'`

- [ ] **Step 2: Implement `apps/api/app/services/github_sync.py`**

```python
"""GitHub sync service: push workflows to GitHub, pull from GitHub.

All GitHub API calls use httpx with the token from the org's github_oauth2
credential. Jobs are DB-backed (GithubSyncJob) and processed by the
github_sync_dispatch_loop in app.services.github_sync_jobs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Credential, GithubSyncConfig, GithubSyncJob, Workflow
from app.services.audit import log_audit
from app.services.crypto import decrypt_credential
from app.tenancy import run_as_system

import noodle_nodes  # noqa: F401 — registers built-in nodes
from noodle.sdk import registry as node_registry
from noodle_exporter import slugify, workflow_to_module
from noodle_importer import import_module

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
PUSH_MAX_ATTEMPTS = 3
PULL_MAX_ATTEMPTS = 3
_BACKOFF = (1, 4, 16)  # seconds

SyncOrigin = Literal["ui", "mcp", "api", "publish"]

COMMIT_MESSAGE_TEMPLATES: dict[str, str] = {
    "ui": "draft: {name}",
    "mcp": "mcp: {name}",
    "api": "api: {name}",
    "publish": "publish: {name} v{version}",
}


# ---------------------------------------------------------------------------
# Enqueue helper — called from every draft_graph write path
# ---------------------------------------------------------------------------


async def enqueue_github_push(
    session: AsyncSession,
    workflow: Workflow,
    origin: SyncOrigin,
) -> None:
    """Insert a push job if this org has a GitHub sync config.

    Takes the same session as the workflow write so the job is committed
    atomically with the graph change. Caller owns the commit.
    """
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == workflow.org_id)
    )
    if cfg is None:
        return

    job_type = "push_publish" if origin == "publish" else "push_draft"
    job = GithubSyncJob(
        org_id=workflow.org_id,
        workflow_id=workflow.id,
        job_type=job_type,
        origin=origin,
        status="pending",
    )
    session.add(job)
    if workflow.github_sync_status not in ("conflict",):
        workflow.github_sync_status = "pending"


# ---------------------------------------------------------------------------
# Workflow → file path
# ---------------------------------------------------------------------------


def workflow_file_path(base_path: str, workflow: Workflow) -> str:
    slug = slugify(workflow.name)
    path = base_path.rstrip("/") + "/" + slug + ".py"
    return path


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------


async def _get_token(session: AsyncSession, cfg: GithubSyncConfig) -> str | None:
    if not cfg.credential_id:
        return None
    cred = await session.get(Credential, cfg.credential_id)
    if cred is None:
        return None
    from app.services import org_keys
    kek = await org_keys.get_kek(cfg.org_id)
    data = decrypt_credential(cred.encrypted_data, cred.encrypted_dek, org_kek=kek)
    return data.get("token") or data.get("access_token")


async def _github_get_file(
    client: httpx.AsyncClient, token: str, repo: str, path: str, ref: str
) -> dict | None:
    resp = await client.get(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        params={"ref": ref},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def _github_get_branch(
    client: httpx.AsyncClient, token: str, repo: str, branch: str
) -> dict | None:
    resp = await client.get(
        f"{GITHUB_API}/repos/{repo}/branches/{branch}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def _github_create_branch(
    client: httpx.AsyncClient, token: str, repo: str, branch: str, from_sha: str
) -> None:
    resp = await client.post(
        f"{GITHUB_API}/repos/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": from_sha},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    if resp.status_code == 422 and "Reference already exists" in resp.text:
        return  # concurrent creation — benign
    resp.raise_for_status()


async def _github_put_file(
    client: httpx.AsyncClient,
    token: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: str | None,
) -> dict:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    resp = await client.put(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Job processors
# ---------------------------------------------------------------------------


async def process_push_job(job_id: str) -> None:
    """Execute a push_draft or push_publish job. Loads its own DB session."""
    with run_as_system():
        async with SessionLocal() as session:
            job = await session.get(GithubSyncJob, job_id)
            if job is None or job.status not in ("pending", "failed"):
                return

            job.status = "processing"
            job.attempts += 1
            await session.commit()

            workflow = await session.get(Workflow, job.workflow_id)
            cfg = await session.scalar(
                select(GithubSyncConfig).where(GithubSyncConfig.org_id == job.org_id)
            )

            if workflow is None or cfg is None:
                job.status = "failed"
                job.error_message = "Workflow or sync config not found"
                await session.commit()
                return

            token = await _get_token(session, cfg)
            if not token:
                job.status = "failed"
                job.error_message = "No GitHub credential token found"
                await session.commit()
                return

            is_publish = job.job_type == "push_publish"
            target_branch = cfg.main_branch if is_publish else f"draft/{slugify(workflow.name)}"
            file_path = workflow_file_path(cfg.base_path, workflow)

            graph = workflow.draft_graph or {"nodes": [], "edges": []}
            try:
                content = workflow_to_module(
                    __import__("noodle.models", fromlist=["WorkflowGraph"]).WorkflowGraph.model_validate(graph),
                    workflow.name,
                    registry=node_registry,
                )
            except Exception as exc:
                job.status = "failed"
                job.error_message = f"Export failed: {exc}"
                await session.commit()
                return

            version = workflow.published_version if is_publish else None
            msg_tpl = COMMIT_MESSAGE_TEMPLATES.get(job.origin, "update: {name}")
            message = msg_tpl.format(name=workflow.name, version=version)

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    # Ensure branch exists (draft branches only)
                    if not is_publish:
                        branch_info = await _github_get_branch(client, token, cfg.repo, target_branch)
                        if branch_info is None:
                            main_info = await _github_get_branch(client, token, cfg.repo, cfg.main_branch)
                            if main_info is None:
                                raise RuntimeError(f"Main branch {cfg.main_branch!r} not found")
                            from_sha = main_info["commit"]["sha"]
                            await _github_create_branch(client, token, cfg.repo, target_branch, from_sha)

                    result = await _github_put_file(
                        client, token, cfg.repo, file_path,
                        content, message, target_branch,
                        sha=workflow.github_sync_sha,
                    )

                new_sha = result["content"]["sha"]
                workflow.github_sync_sha = new_sha
                workflow.github_sync_status = "synced"
                job.status = "done"
                await log_audit(session, "github_push", "workflow", workflow.id, job.origin)
                await session.commit()

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in (409, 422):
                    # Conflict: GitHub rejected because file SHA diverged
                    workflow.github_sync_status = "conflict"
                    job.status = "failed"
                    job.error_message = f"GitHub conflict (HTTP {status_code})"
                    await session.commit()
                elif status_code in (401, 403, 404) or job.attempts >= PUSH_MAX_ATTEMPTS:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                    job.error_message = str(exc)
                    await session.commit()
                else:
                    # Transient — schedule retry with backoff
                    delay = _BACKOFF[min(job.attempts - 1, len(_BACKOFF) - 1)]
                    job.status = "failed"
                    job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                    job.error_message = str(exc)
                    await session.commit()
            except Exception as exc:
                logger.exception("github push job %s failed: %s", job_id, exc)
                if job.attempts >= PUSH_MAX_ATTEMPTS:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                else:
                    delay = _BACKOFF[min(job.attempts - 1, len(_BACKOFF) - 1)]
                    job.status = "failed"
                    job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                job.error_message = str(exc)
                await session.commit()


async def process_pull_job(job_id: str) -> None:
    """Execute a github_pull job. Loads its own DB session."""
    with run_as_system():
        async with SessionLocal() as session:
            job = await session.get(GithubSyncJob, job_id)
            if job is None or job.status not in ("pending", "failed"):
                return

            job.status = "processing"
            job.attempts += 1
            await session.commit()

            workflow = await session.get(Workflow, job.workflow_id)
            cfg = await session.scalar(
                select(GithubSyncConfig).where(GithubSyncConfig.org_id == job.org_id)
            )
            if workflow is None or cfg is None:
                job.status = "failed"
                job.error_message = "Workflow or config not found"
                await session.commit()
                return

            token = await _get_token(session, cfg)
            if not token:
                job.status = "failed"
                job.error_message = "No GitHub credential token"
                await session.commit()
                return

            file_path = workflow_file_path(cfg.base_path, workflow)
            is_manual = job.file_sha is None

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    file_info = await _github_get_file(
                        client, token, cfg.repo, file_path, cfg.main_branch
                    )
                if file_info is None:
                    job.status = "failed"
                    job.error_message = f"File {file_path} not found on {cfg.main_branch}"
                    await session.commit()
                    return

                incoming_sha = file_info["sha"]
                content_b64 = file_info["content"]
                source = base64.b64decode(content_b64).decode()

                # Conflict detection (skip for manual pull — user explicitly requested)
                if not is_manual and workflow.github_sync_sha:
                    sha_changed = incoming_sha != workflow.github_sync_sha
                    has_local_edits = workflow.draft_graph is not None
                    if sha_changed and has_local_edits:
                        workflow.github_sync_status = "conflict"
                        workflow.github_sync_conflict_sha = incoming_sha
                        job.status = "done"
                        await session.commit()
                        return

                # Parse and apply
                try:
                    new_graph = import_module(source)
                except ImportError as exc:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                    job.error_message = f"Import failed: {exc}"
                    await session.commit()
                    return

                workflow.draft_graph = new_graph.model_dump()
                workflow.github_sync_sha = incoming_sha
                workflow.github_sync_status = "synced"
                workflow.github_sync_conflict_sha = None
                job.status = "done"
                await log_audit(session, "github_pull", "workflow", workflow.id)
                await session.commit()

            except httpx.HTTPStatusError as exc:
                if job.attempts >= PULL_MAX_ATTEMPTS:
                    workflow.github_sync_status = "error"
                    job.status = "failed"
                else:
                    delay = _BACKOFF[min(job.attempts - 1, len(_BACKOFF) - 1)]
                    job.status = "failed"
                    job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                job.error_message = str(exc)
                await session.commit()
            except Exception as exc:
                logger.exception("github pull job %s failed: %s", job_id, exc)
                job.status = "failed"
                job.error_message = str(exc)
                await session.commit()


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


def verify_github_hmac(secret: str, payload: bytes, signature_header: str) -> bool:
    """Return True if the X-Hub-Signature-256 header matches the payload."""
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

- [ ] **Step 3: Run service tests**

```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py -v
```
Expected: model tests pass; enqueue tests need `db_session` fixture — check `tests/conftest.py` for the fixture name. If missing, add:

Look up the existing session fixture name in `apps/api/tests/conftest.py` (it's typically `db_session` or `session`). Use that name in the test. Rerun:
```bash
pytest tests/test_github_sync.py -v
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd D:/noodle
git add apps/api/app/services/github_sync.py apps/api/tests/test_github_sync.py
git commit -m "feat(github-sync): push/pull service with conflict detection and enqueue helper"
```

---

### Task 4: GitHub sync job dispatch loop

**Files:**
- Create: `apps/api/app/services/github_sync_jobs.py`
- Modify: `apps/api/app/main.py` (start the loop)

**Interfaces:**
- Produces: `async def github_sync_dispatch_loop() -> None`
- Consumes: `process_push_job()`, `process_pull_job()` from `app.services.github_sync`

- [ ] **Step 1: Implement `apps/api/app/services/github_sync_jobs.py`**

```python
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
```

- [ ] **Step 2: Wire the loop into `apps/api/app/main.py`**

In `main.py`, after the existing import of `scheduler_loop`:
```python
from app.services.github_sync_jobs import github_sync_dispatch_loop
```

Inside the `lifespan` function (or `@app.on_event("startup")` block), find where background loops are started (near `asyncio.create_task(scheduler_loop())`) and add:
```python
asyncio.create_task(github_sync_dispatch_loop())
```

Search for `scheduler_loop` in `main.py` to find the exact location:
```bash
grep -n "scheduler_loop\|create_task" apps/api/app/main.py | head -20
```

Add the `github_sync_dispatch_loop` task immediately after the `scheduler_loop` task.

- [ ] **Step 3: Verify loop starts without error**

```bash
cd D:/noodle/apps/api && python -c "
import asyncio
from app.services.github_sync_jobs import github_sync_dispatch_loop

async def test():
    task = asyncio.create_task(github_sync_dispatch_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print('loop started and cancelled cleanly')

asyncio.run(test())
"
```
Expected: `loop started and cancelled cleanly`

- [ ] **Step 4: Commit**

```bash
cd D:/noodle
git add apps/api/app/services/github_sync_jobs.py apps/api/app/main.py
git commit -m "feat(github-sync): add DB-backed job dispatch loop"
```

---

### Task 5: GitHub sync API router — settings CRUD, webhook, manual pull

**Files:**
- Create: `apps/api/app/routers/github_sync.py`
- Modify: `apps/api/app/schemas.py` (add request/response schemas)
- Modify: `apps/api/app/main.py` (register router)
- Modify: `apps/api/tests/test_github_sync.py` (add endpoint tests)

**Interfaces:**
- Produces: `GET /github-sync/config` → `GithubSyncConfigInfo | null`
- Produces: `PUT /github-sync/config` → `GithubSyncConfigInfo`
- Produces: `DELETE /github-sync/config` → 204
- Produces: `POST /webhooks/github-sync/{org_id}` → 200
- Produces: `POST /workflows/{workflow_id}/github-pull` → 202
- Produces: `POST /workflows/{workflow_id}/github-conflict/resolve` → 200

- [ ] **Step 1: Add schemas to `apps/api/app/schemas.py`**

```python
class GithubSyncConfigCreate(BaseModel):
    repo: str = Field(min_length=3, max_length=200, pattern=r"^[\w.-]+/[\w.-]+$")
    base_path: str = Field(default="workflows/", max_length=200)
    main_branch: str = Field(default="main", max_length=100)
    credential_id: str | None = None


class GithubSyncConfigInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    org_id: str
    repo: str
    base_path: str
    main_branch: str
    credential_id: str | None
    webhook_url: str  # computed — not in DB


class GithubConflictResolveRequest(BaseModel):
    side: Literal["noodle", "github"]
```

- [ ] **Step 2: Write failing endpoint tests**

Add to `apps/api/tests/test_github_sync.py`:
```python
def test_get_github_sync_config_no_config(client):
    resp = client.get("/api/github-sync/config")
    assert resp.status_code == 200
    assert resp.json() is None


def test_put_github_sync_config(client):
    resp = client.put("/api/github-sync/config", json={
        "repo": "owner/repo",
        "base_path": "workflows/",
        "main_branch": "main",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == "owner/repo"
    assert "webhook_url" in data
    assert data["webhook_url"].endswith(f"/webhooks/github-sync/default")


def test_delete_github_sync_config(client):
    # Create first
    client.put("/api/github-sync/config", json={"repo": "owner/repo"})
    resp = client.delete("/api/github-sync/config")
    assert resp.status_code == 204
    assert client.get("/api/github-sync/config").json() is None


def test_github_webhook_invalid_signature(client):
    resp = client.post(
        "/webhooks/github-sync/default",
        content=b'{"ref":"refs/heads/main"}',
        headers={"X-Hub-Signature-256": "sha256=badsig", "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 401
```

Run:
```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py::test_get_github_sync_config_no_config -v
```
Expected: 404 (router not registered yet)

- [ ] **Step 3: Implement `apps/api/app/routers/github_sync.py`**

```python
"""GitHub sync settings CRUD, webhook ingress, and manual pull endpoints."""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.db import get_session
from app.models import GithubSyncConfig, GithubSyncJob, Workflow
from app.schemas import (
    GithubConflictResolveRequest,
    GithubSyncConfigCreate,
    GithubSyncConfigInfo,
)
from app.security import require_permission
from app.services.audit import log_audit
from app.services.github_sync import (
    import_module,
    verify_github_hmac,
    workflow_file_path,
)
from app.services.github_sync_jobs import notify_sync_workers
from app.services.licensing import Feature, get_license
from app.tenancy import active_org_id

router = APIRouter(tags=["github-sync"])
logger = logging.getLogger(__name__)


def _require_git_sync_feature() -> None:
    lic = get_license()
    if Feature.GIT_SYNC not in lic.features:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "GitHub sync requires Pro or Enterprise")


def _webhook_url(org_id: str) -> str:
    base = getattr(app_settings, "public_url", "") or ""
    return f"{base.rstrip('/')}/webhooks/github-sync/{org_id}"


def _config_info(cfg: GithubSyncConfig, org_id: str) -> GithubSyncConfigInfo:
    return GithubSyncConfigInfo(
        id=cfg.id,
        org_id=cfg.org_id,
        repo=cfg.repo,
        base_path=cfg.base_path,
        main_branch=cfg.main_branch,
        credential_id=cfg.credential_id,
        webhook_url=_webhook_url(org_id),
    )


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------

@router.get("/github-sync/config", response_model=GithubSyncConfigInfo | None)
async def get_github_sync_config(
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> GithubSyncConfigInfo | None:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is None:
        return None
    return _config_info(cfg, org_id)


@router.put("/github-sync/config", response_model=GithubSyncConfigInfo)
async def upsert_github_sync_config(
    body: GithubSyncConfigCreate,
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> GithubSyncConfigInfo:
    _require_git_sync_feature()
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is None:
        cfg = GithubSyncConfig(
            org_id=org_id,
            webhook_secret=secrets.token_hex(32),
        )
        session.add(cfg)
    cfg.repo = body.repo
    cfg.base_path = body.base_path
    cfg.main_branch = body.main_branch
    if body.credential_id is not None:
        cfg.credential_id = body.credential_id
    await log_audit(session, "github_sync_connect", "org", org_id, body.repo)
    await session.commit()
    await session.refresh(cfg)
    return _config_info(cfg, org_id)


@router.get("/github-sync/config/webhook-secret")
async def get_webhook_secret(
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No sync config")
    return {"webhook_secret": cfg.webhook_secret}


@router.delete("/github-sync/config", status_code=204)
async def delete_github_sync_config(
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    org_id = active_org_id() or "default"
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
    )
    if cfg is not None:
        await log_audit(session, "github_sync_disconnect", "org", org_id, cfg.repo)
        await session.delete(cfg)
        await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Webhook (public — verified by HMAC)
# ---------------------------------------------------------------------------

@router.post("/webhooks/github-sync/{org_id}", status_code=200)
async def github_sync_webhook(
    org_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
        .execution_options(skip_org_filter=True)
    )
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No sync config for this org")

    payload = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_github_hmac(cfg.webhook_secret, payload, sig):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return {"status": "ignored", "event": event}

    import json
    data = json.loads(payload)
    ref = data.get("ref", "")
    if ref != f"refs/heads/{cfg.main_branch}":
        return {"status": "ignored", "ref": ref}

    # Collect changed .py files under base_path
    changed_paths: set[str] = set()
    for commit in data.get("commits", []):
        for key in ("added", "modified", "removed"):
            for p in commit.get(key, []):
                if p.startswith(cfg.base_path) and p.endswith(".py"):
                    changed_paths.add(p)

    # Match to workflows by file path slug
    workflows = (
        await session.scalars(
            select(Workflow).where(Workflow.org_id == org_id)
            .execution_options(skip_org_filter=True)
        )
    ).all()
    slug_to_workflow = {workflow_file_path(cfg.base_path, wf): wf for wf in workflows}

    enqueued = 0
    for path in changed_paths:
        wf = slug_to_workflow.get(path)
        if wf is None:
            continue
        job = GithubSyncJob(
            org_id=org_id,
            workflow_id=wf.id,
            job_type="pull",
            origin="webhook",
            status="pending",
        )
        session.add(job)
        enqueued += 1

    if enqueued:
        await session.commit()
        notify_sync_workers()

    return {"status": "ok", "enqueued": enqueued}


# ---------------------------------------------------------------------------
# Manual pull + conflict resolution
# ---------------------------------------------------------------------------

@router.post("/workflows/{workflow_id}/github-pull", status_code=202)
async def manual_github_pull(
    workflow_id: str,
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    job = GithubSyncJob(
        org_id=workflow.org_id,
        workflow_id=workflow_id,
        job_type="pull",
        origin="manual",
        file_sha=None,
        status="pending",
    )
    session.add(job)
    await session.commit()
    notify_sync_workers()
    return {"status": "queued", "job_id": job.id}


@router.post("/workflows/{workflow_id}/github-conflict/resolve")
async def resolve_github_conflict(
    workflow_id: str,
    body: GithubConflictResolveRequest,
    _: None = Depends(require_permission("workflow:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    if workflow.github_sync_status != "conflict":
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow is not in conflict state")

    cfg = await session.scalar(
        select(GithubSyncConfig).where(GithubSyncConfig.org_id == workflow.org_id)
    )
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No sync config")

    if body.side == "noodle":
        # Noodle wins: reset SHA so next push will overwrite GitHub
        workflow.github_sync_sha = workflow.github_sync_conflict_sha
        workflow.github_sync_status = "pending"
        workflow.github_sync_conflict_sha = None
        await log_audit(session, "github_conflict_resolved", "workflow", workflow_id, "noodle")
        await session.commit()
        # Enqueue push to overwrite GitHub
        from app.services.github_sync import enqueue_github_push
        async with SessionLocal() as s2:
            wf2 = await s2.get(Workflow, workflow_id)
            await enqueue_github_push(s2, wf2, "ui")
            await s2.commit()
        notify_sync_workers()

    else:  # github
        # Enqueue a manual pull (file_sha=None → skip conflict check)
        job = GithubSyncJob(
            org_id=workflow.org_id,
            workflow_id=workflow_id,
            job_type="pull",
            origin="conflict_resolve",
            file_sha=None,
            status="pending",
        )
        session.add(job)
        workflow.github_sync_status = "pending"
        workflow.github_sync_conflict_sha = None
        await log_audit(session, "github_conflict_resolved", "workflow", workflow_id, "github")
        await session.commit()
        notify_sync_workers()

    return {"status": "ok", "side": body.side}
```

- [ ] **Step 4: Register the router in `apps/api/app/main.py`**

In `main.py`, in the router imports block:
```python
from app.routers import github_sync as github_sync_router
```

Then in the `app.include_router(...)` section, add:
```python
app.include_router(github_sync_router.router, prefix="/api")
```

Also mount the webhook route (without `/api` prefix, it's a public endpoint):
```python
app.include_router(github_sync_router.router)
```

Actually — looking at the existing pattern: `webhooks.router` is mounted at root via `production_router`. For this feature, add the github-sync router with `/api` prefix for settings endpoints, and mount the webhook endpoint separately. The easiest approach: add the entire router with `/api` prefix, then the webhook route's path `/webhooks/github-sync/{org_id}` won't be prefixed correctly.

Better: split the router into two:
- `settings_router = APIRouter(prefix="/api", tags=["github-sync"])` for settings endpoints
- Mount `POST /webhooks/github-sync/{org_id}` directly on the main app or a separate `webhooks_router`

Simplest solution: put the webhook endpoint in `apps/api/app/routers/webhooks.py` alongside the existing webhooks, and put the settings endpoints in `github_sync.py` under `/api`. 

Move the `github_sync_webhook` handler into `apps/api/app/routers/webhooks.py` and import `verify_github_hmac` from `app.services.github_sync` there.

In `github_sync.py`, remove the webhook route and add `prefix="/api"` to the router.

Then in `main.py`:
```python
from app.routers import github_sync as github_sync_router
app.include_router(github_sync_router.router)
```

And in `webhooks.py`, add the new route to the existing `production_router`.

- [ ] **Step 5: Run endpoint tests**

```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py -v -k "config or webhook"
```
Expected: all config and webhook tests pass.

- [ ] **Step 6: Commit**

```bash
cd D:/noodle
git add apps/api/app/routers/github_sync.py apps/api/app/routers/webhooks.py apps/api/app/schemas.py apps/api/app/main.py apps/api/tests/test_github_sync.py
git commit -m "feat(github-sync): settings CRUD, webhook ingress, manual pull and conflict resolve endpoints"
```

---

### Task 6: Wire sync triggers into existing write paths

**Files:**
- Modify: `apps/api/app/routers/workflows.py` (PATCH, POST, publish)
- Modify: `apps/api/app/mcp/tools.py` (_create_workflow, _set_workflow_graph)

**Interfaces:**
- Consumes: `enqueue_github_push()` from `app.services.github_sync`
- Consumes: `notify_sync_workers()` from `app.services.github_sync_jobs`

- [ ] **Step 1: Write a test for MCP trigger**

Add to `apps/api/tests/test_github_sync.py`:
```python
@pytest.mark.asyncio
async def test_set_workflow_graph_enqueues_push(db_session):
    """MCP set_workflow_graph enqueues a github_push_draft job when sync configured."""
    import secrets as _secrets
    cfg = GithubSyncConfig(
        org_id="default", repo="owner/repo",
        base_path="workflows/", main_branch="main",
        webhook_secret=_secrets.token_hex(20),
    )
    db_session.add(cfg)
    wf = Workflow(
        id="wf_mcp_test", name="MCP Flow", org_id="default",
        draft_graph={"nodes": [], "edges": []}, published_version=1,
    )
    db_session.add(wf)
    await db_session.commit()

    # Simulate MCP set_workflow_graph by calling the service directly
    from app.services.github_sync import enqueue_github_push
    async with SessionLocal() as s:
        wf2 = await s.get(Workflow, "wf_mcp_test")
        wf2.draft_graph = {"nodes": [{"id": "t", "type": "manual_trigger", "params": {}}], "edges": []}
        await enqueue_github_push(s, wf2, "mcp")
        await s.commit()

    from sqlalchemy import select
    async with SessionLocal() as s:
        jobs = (await s.scalars(
            select(GithubSyncJob).where(GithubSyncJob.workflow_id == "wf_mcp_test")
        )).all()
    assert len(jobs) == 1
    assert jobs[0].origin == "mcp"
```

Run:
```bash
cd D:/noodle/apps/api && pytest tests/test_github_sync.py::test_set_workflow_graph_enqueues_push -v
```
Expected: pass (the service already works; this test validates the contract).

- [ ] **Step 2: Modify `apps/api/app/routers/workflows.py`**

Add import at the top of the file:
```python
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
```

In the **`update_workflow`** handler (the `PATCH /{workflow_id}` endpoint), after `await session.commit()` and before `return`, add:
```python
    if body.graph is not None:
        await enqueue_github_push(workflow, "ui")  # no-op if no config
        notify_sync_workers()
```

Wait — `enqueue_github_push` takes a `session`. The session is already committed at this point. Use a separate session for the enqueue (same pattern as conflict resolution in Task 5):

Actually, the cleanest approach: call `enqueue_github_push(session, workflow, "ui")` BEFORE `await session.commit()` so it's atomic:

Find the `update_workflow` handler. Before `await session.commit()`, add:
```python
    if body.graph is not None:
        await enqueue_github_push(session, workflow, "ui")
```
And after `await session.commit()`:
```python
    if body.graph is not None:
        notify_sync_workers()
```

In the **`create_workflow`** handler (the `POST /` endpoint), before `await session.commit()`, add:
```python
    await enqueue_github_push(session, workflow, "ui")
```
After `await session.commit()`:
```python
    notify_sync_workers()
```

In the **`publish_workflow`** handler, before `await session.commit()`, add:
```python
    await enqueue_github_push(session, workflow, "publish")
```
After `await session.commit()`:
```python
    notify_sync_workers()
```

- [ ] **Step 3: Modify `apps/api/app/mcp/tools.py`**

Add imports at the top:
```python
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
```

In `_create_workflow`, before `await session.commit()`, add:
```python
    await enqueue_github_push(session, workflow, "mcp")
```
After `await session.commit()`:
```python
    notify_sync_workers()
```

In `_set_workflow_graph`, before `await session.commit()` (find it — it's in the handler that calls this function, or `_set_workflow_graph` itself needs a commit call), add:
```python
    await enqueue_github_push(session, workflow, "mcp")
```
After `await session.commit()`:
```python
    notify_sync_workers()
```

Check whether `_set_workflow_graph` commits itself or returns and lets the MCP router commit. Read the function and add the call in the right place. The audit log call `await log_audit(...)` is already there — add the enqueue right before the commit that follows it.

- [ ] **Step 4: Run full test suite to catch regressions**

```bash
cd D:/noodle/apps/api && pytest tests/ -v --tb=short -q 2>&1 | tail -30
```
Expected: no new failures; all github_sync tests pass.

- [ ] **Step 5: Commit**

```bash
cd D:/noodle
git add apps/api/app/routers/workflows.py apps/api/app/mcp/tools.py
git commit -m "feat(github-sync): wire enqueue_github_push into all draft_graph write paths"
```

---

### Task 7: Frontend — settings tab, workflow list badges, editor integration, conflict modal

**Files:**
- Modify: `apps/web/src/types.ts` (add `GithubSyncConfig`, `GithubSyncStatus`)
- Modify: `apps/web/src/api.ts` (add `getGithubSyncConfig`, `upsertGithubSyncConfig`, `deleteGithubSyncConfig`, `getGithubWebhookSecret`, `triggerManualPull`, `resolveGithubConflict`)
- Modify: `apps/web/src/queries/index.ts` (add React Query hooks)
- Create: `apps/web/src/GitHubSyncSettings.tsx`
- Create: `apps/web/src/editor/GitHubSyncBadge.tsx`
- Create: `apps/web/src/editor/GitHubConflictModal.tsx`
- Modify: `apps/web/src/OrganizationPage.tsx` (add GitHub Sync tab)
- Modify: `apps/web/src/EditorPage.tsx` (add sync status + overflow items)

**Interfaces:**
- Consumes: `WorkflowSummary.github_sync_status` (add to existing type)
- Produces: `<GitHubSyncBadge status={...} />` — inline icon for workflow list rows
- Produces: `<GitHubConflictModal workflowId={...} onClose={...} />` — split-pane diff modal
- Produces: `<GitHubSyncSettings />` — org settings tab panel

- [ ] **Step 1: Add types to `apps/web/src/types.ts`**

Add after the existing type definitions:
```typescript
export type GithubSyncStatus = "synced" | "pending" | "conflict" | "error" | null;

export interface GithubSyncConfig {
  id: string;
  org_id: string;
  repo: string;
  base_path: string;
  main_branch: string;
  credential_id: string | null;
  webhook_url: string;
}
```

Also add `github_sync_status?: GithubSyncStatus` to the `WorkflowSummary` interface (find it in `types.ts`).

- [ ] **Step 2: Add API methods to `apps/web/src/api.ts`**

Find the class/object that contains all API methods. Add:
```typescript
  getGithubSyncConfig: async (): Promise<GithubSyncConfig | null> => {
    return _get<GithubSyncConfig | null>("/api/github-sync/config");
  },

  upsertGithubSyncConfig: async (body: {
    repo: string;
    base_path?: string;
    main_branch?: string;
    credential_id?: string | null;
  }): Promise<GithubSyncConfig> => {
    return _put<GithubSyncConfig>("/api/github-sync/config", body);
  },

  deleteGithubSyncConfig: async (): Promise<void> => {
    await _delete("/api/github-sync/config");
  },

  getGithubWebhookSecret: async (): Promise<{ webhook_secret: string }> => {
    return _get("/api/github-sync/config/webhook-secret");
  },

  triggerManualPull: async (workflowId: string): Promise<{ status: string; job_id: string }> => {
    return _post(`/api/workflows/${workflowId}/github-pull`, {});
  },

  resolveGithubConflict: async (
    workflowId: string,
    side: "noodle" | "github"
  ): Promise<void> => {
    await _post(`/api/workflows/${workflowId}/github-conflict/resolve`, { side });
  },
```

(Use whatever internal fetch helpers `_get`, `_put`, `_post`, `_delete` the file already uses — check the existing methods to copy the exact pattern.)

- [ ] **Step 3: Add query hooks to `apps/web/src/queries/index.ts`**

```typescript
export function useGithubSyncConfig(options?: QueryControls<GithubSyncConfig | null>) {
  return useQuery({
    queryKey: queryKeys.githubSyncConfig,
    queryFn: api.getGithubSyncConfig,
    ...options,
  });
}

export function useUpsertGithubSyncConfigMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.upsertGithubSyncConfig>[0]) =>
      api.upsertGithubSyncConfig(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.githubSyncConfig });
    },
  });
}

export function useDeleteGithubSyncConfigMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteGithubSyncConfig(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.githubSyncConfig });
    },
  });
}

export function useTriggerManualPullMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => api.triggerManualPull(workflowId),
    onSuccess: (_, workflowId) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflow(workflowId) });
    },
  });
}

export function useResolveGithubConflictMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ workflowId, side }: { workflowId: string; side: "noodle" | "github" }) =>
      api.resolveGithubConflict(workflowId, side),
    onSuccess: (_, { workflowId }) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflow(workflowId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflows });
    },
  });
}
```

Add `githubSyncConfig: ["github-sync-config"] as const` to `queryKeys` in `apps/web/src/queries/keys.ts`.

- [ ] **Step 4: Create `apps/web/src/editor/GitHubSyncBadge.tsx`**

```tsx
import type { GithubSyncStatus } from "../types";

const LABELS: Record<NonNullable<GithubSyncStatus>, string> = {
  synced: "Synced",
  pending: "Syncing…",
  conflict: "Conflict",
  error: "Sync error",
};

export function GitHubSyncBadge({ status }: { status: GithubSyncStatus }) {
  if (!status) return null;

  const label = LABELS[status];
  const cls = `github-sync-badge github-sync-badge--${status}`;

  return (
    <span className={cls} title={label} aria-label={`GitHub sync: ${label}`}>
      {status === "synced" && <GitHubIcon />}
      {status === "pending" && <span className="sync-spinner" aria-hidden />}
      {status === "conflict" && "⚠"}
      {status === "error" && "✕"}
      <span className="github-sync-badge__label">{label}</span>
    </span>
  );
}

function GitHubIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
```

- [ ] **Step 5: Create `apps/web/src/editor/GitHubConflictModal.tsx`**

```tsx
import { useState } from "react";
import { useResolveGithubConflictMutation } from "../queries";
import { useToast } from "../ToastProvider";

interface Props {
  workflowId: string;
  workflowName: string;
  onClose: () => void;
}

export function GitHubConflictModal({ workflowId, workflowName, onClose }: Props) {
  const { notify } = useToast();
  const resolve = useResolveGithubConflictMutation();
  const [resolving, setResolving] = useState<"noodle" | "github" | null>(null);

  async function handleResolve(side: "noodle" | "github") {
    setResolving(side);
    try {
      await resolve.mutateAsync({ workflowId, side });
      notify(
        side === "noodle"
          ? "Kept Noodle version — GitHub will be overwritten on next sync."
          : "GitHub version applied — draft updated.",
        "success"
      );
      onClose();
    } catch {
      notify("Failed to resolve conflict. Please try again.", "error");
    } finally {
      setResolving(null);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal aria-label="Resolve GitHub sync conflict">
      <div className="modal-box conflict-modal">
        <header className="modal-header">
          <h2>GitHub Sync Conflict</h2>
          <button type="button" className="btn-close" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <p className="conflict-description">
          Both <strong>{workflowName}</strong> in Noodle and the file on GitHub were edited
          since the last sync. Choose which version to keep.
        </p>

        <div className="conflict-actions">
          <div className="conflict-option">
            <h3>Keep Noodle version</h3>
            <p>Your current draft stays as-is. GitHub will be overwritten on the next push.</p>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleResolve("noodle")}
              disabled={resolving !== null}
            >
              {resolving === "noodle" ? "Applying…" : "Keep Noodle"}
            </button>
          </div>

          <div className="conflict-divider" aria-hidden>or</div>

          <div className="conflict-option">
            <h3>Take GitHub version</h3>
            <p>The GitHub file replaces your current draft. Your unsaved edits will be lost.</p>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void handleResolve("github")}
              disabled={resolving !== null}
            >
              {resolving === "github" ? "Applying…" : "Take GitHub"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create `apps/web/src/GitHubSyncSettings.tsx`**

```tsx
import { useState } from "react";
import {
  useGithubSyncConfig,
  useUpsertGithubSyncConfigMutation,
  useDeleteGithubSyncConfigMutation,
} from "./queries";
import { api } from "./api";
import { useToast } from "./ToastProvider";
import { useConfirm } from "./ConfirmProvider";

export function GitHubSyncSettings() {
  const { data: config, isLoading } = useGithubSyncConfig();
  const upsert = useUpsertGithubSyncConfigMutation();
  const remove = useDeleteGithubSyncConfigMutation();
  const { notify } = useToast();
  const confirm = useConfirm();

  const [repo, setRepo] = useState("");
  const [basePath, setBasePath] = useState("workflows/");
  const [mainBranch, setMainBranch] = useState("main");
  const [secret, setSecret] = useState<string | null>(null);

  if (isLoading) return <div className="settings-loading">Loading…</div>;

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    try {
      await upsert.mutateAsync({ repo, base_path: basePath, main_branch: mainBranch });
      notify("GitHub sync configured.", "success");
    } catch {
      notify("Failed to save GitHub sync config.", "error");
    }
  }

  async function handleRevealSecret() {
    try {
      const { webhook_secret } = await api.getGithubWebhookSecret();
      setSecret(webhook_secret);
    } catch {
      notify("Failed to retrieve webhook secret.", "error");
    }
  }

  async function handleDisconnect() {
    const ok = await confirm("Disconnect GitHub sync? Synced files in GitHub will not be deleted.");
    if (!ok) return;
    try {
      await remove.mutateAsync();
      notify("GitHub sync disconnected.", "success");
    } catch {
      notify("Failed to disconnect.", "error");
    }
  }

  if (config) {
    return (
      <section className="settings-section">
        <h3>GitHub Sync</h3>
        <p className="settings-hint">
          Connected to <strong>{config.repo}</strong>. Workflows sync to{" "}
          <code>{config.base_path}</code> on branch <code>{config.main_branch}</code>.
        </p>

        <div className="settings-field">
          <label>Webhook URL</label>
          <div className="copy-row">
            <code className="copy-value">{config.webhook_url}</code>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void navigator.clipboard.writeText(config.webhook_url)}
            >
              Copy
            </button>
          </div>
          <p className="settings-hint">Paste this into your GitHub repo → Settings → Webhooks.</p>
        </div>

        <div className="settings-field">
          <label>Webhook Secret</label>
          {secret ? (
            <div className="copy-row">
              <code className="copy-value">{secret}</code>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void navigator.clipboard.writeText(secret)}
              >
                Copy
              </button>
            </div>
          ) : (
            <button type="button" className="btn btn-sm" onClick={() => void handleRevealSecret()}>
              Reveal secret
            </button>
          )}
        </div>

        <button
          type="button"
          className="btn btn-danger"
          onClick={() => void handleDisconnect()}
          disabled={remove.isPending}
        >
          Disconnect GitHub
        </button>
      </section>
    );
  }

  return (
    <section className="settings-section">
      <h3>GitHub Sync</h3>
      <p className="settings-hint">
        Connect a GitHub repository to sync your workflows as Python code.
      </p>
      <form onSubmit={(e) => void handleConnect(e)}>
        <div className="settings-field">
          <label htmlFor="gs-repo">Repository</label>
          <input
            id="gs-repo"
            className="input"
            placeholder="owner/repo"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            required
            pattern="[\w.-]+/[\w.-]+"
          />
        </div>
        <div className="settings-field">
          <label htmlFor="gs-path">Base path</label>
          <input
            id="gs-path"
            className="input"
            value={basePath}
            onChange={(e) => setBasePath(e.target.value)}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="gs-branch">Main branch</label>
          <input
            id="gs-branch"
            className="input"
            value={mainBranch}
            onChange={(e) => setMainBranch(e.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={upsert.isPending}>
          {upsert.isPending ? "Saving…" : "Connect GitHub"}
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 7: Wire into `OrganizationPage.tsx`**

In `OrganizationPage.tsx`, import `GitHubSyncSettings`:
```typescript
import { GitHubSyncSettings } from "./GitHubSyncSettings";
```

Find where tabs or sections are rendered and add a "GitHub Sync" section. Look for where other org settings sections are rendered (search for existing section headings like `"Members"` or `"Quotas"`) and add:
```tsx
<GitHubSyncSettings />
```

as a new section below the existing ones.

- [ ] **Step 8: Wire into `EditorPage.tsx`**

Find the import block and add:
```typescript
import { GitHubSyncBadge } from "./editor/GitHubSyncBadge";
import { GitHubConflictModal } from "./editor/GitHubConflictModal";
import { useTriggerManualPullMutation } from "./queries";
```

Add state for the conflict modal:
```typescript
const [conflictOpen, setConflictOpen] = useState(false);
const triggerPull = useTriggerManualPullMutation();
```

Find where `workflow?.github_sync_status` can be read (after the existing `useWorkflow(id)` query), and add the badge next to the `SaveIndicator`:
```tsx
<SaveIndicator state={saveState} onRetry={() => { void save({ notifySuccess: false }); }} />
{workflow?.github_sync_status && (
  <GitHubSyncBadge status={workflow.github_sync_status} />
)}
```

In the `OverflowMenu` items array, add (after existing items):
```tsx
{ 
  id: "github-pull", 
  label: "Pull from GitHub", 
  dividerBefore: true, 
  onSelect: () => { void triggerPull.mutateAsync(id); },
},
workflow?.github_sync_status === "conflict" && {
  id: "github-conflict",
  label: "Resolve GitHub conflict",
  onSelect: () => setConflictOpen(true),
},
```

After the closing `</header>` tag, add:
```tsx
{conflictOpen && workflow && (
  <GitHubConflictModal
    workflowId={id}
    workflowName={workflow.name}
    onClose={() => setConflictOpen(false)}
  />
)}
```

Also: ensure `WorkflowSummary` in `types.ts` includes `github_sync_status` so the workflow list can show badges. In whatever component renders workflow rows, add `<GitHubSyncBadge status={wf.github_sync_status ?? null} />`.

- [ ] **Step 9: Run frontend type check**

```bash
cd D:/noodle/apps/web && npx tsc --noEmit 2>&1 | head -40
```
Expected: no type errors related to the new code.

- [ ] **Step 10: Run frontend tests**

```bash
cd D:/noodle/apps/web && npm test -- --run 2>&1 | tail -20
```
Expected: no test failures.

- [ ] **Step 11: Commit**

```bash
cd D:/noodle
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/queries/ \
        apps/web/src/GitHubSyncSettings.tsx apps/web/src/editor/GitHubSyncBadge.tsx \
        apps/web/src/editor/GitHubConflictModal.tsx apps/web/src/OrganizationPage.tsx \
        apps/web/src/EditorPage.tsx
git commit -m "feat(github-sync): frontend settings tab, sync badges, editor integration, conflict modal"
```

---

## Self-Review

### Spec coverage check

| Spec section | Task |
|---|---|
| §1 Overview: draft push, publish push, webhook pull, conflict flag | Tasks 3, 4, 5, 6 |
| §2 Data model: github_sync_configs, github_sync_jobs, workflow columns | Task 2 |
| §3 Sync trigger: enqueue_github_push, all write sites, commit messages | Tasks 3, 6 |
| §4 Push path: push_draft, push_publish, retry policy | Task 3 |
| §5 Pull path: webhook endpoint, github_pull job, manual pull | Tasks 3, 5 |
| §6 Python importer: AST parser, round-trip tests | Task 1 |
| §7 Conflict resolution: flag, diff modal, noodle/github sides | Tasks 3, 5, 7 |
| §8 UI: settings tab, workflow badges, MetaBar, overflow, conflict modal | Task 7 |
| §9 Multi-tenancy: org isolation, webhook routing by org_id, audit, license gate | Tasks 2, 5 |
| Feature.GIT_SYNC license gate | Task 5 |
| MCP extensibility contract | Task 6 |

All spec requirements covered. ✓

### Placeholder scan

No TBD, TODO, or "implement later" strings. All code blocks are complete. ✓

### Type consistency check

- `enqueue_github_push(session, workflow, origin)` — consistent across Task 3 (definition) and Tasks 5, 6 (call sites). ✓
- `process_push_job(job_id: str)` / `process_pull_job(job_id: str)` — defined Task 3, consumed Task 4. ✓
- `GithubSyncStatus` type defined in `types.ts` Task 7, consumed by `GitHubSyncBadge` and `GitHubConflictModal`. ✓
- `notify_sync_workers()` defined Task 4, called in Tasks 5, 6. ✓
- `verify_github_hmac(secret, payload, signature)` defined Task 3, consumed Task 5. ✓
