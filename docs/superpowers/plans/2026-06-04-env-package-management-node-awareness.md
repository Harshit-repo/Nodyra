# Env Package Management + Node↔Env Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move env package management into a drawer (count-only card, comma-add, requirements.txt diff import), let nodes declare the pip packages they need, surface missing packages in the editor and block runs that would fail, and show which nodes depend on which packages.

**Architecture:** Add a `requirements` field to `NodeManifest` (the single source the editor and API already read). A shared canonical-name helper in core powers three comparisons: the editor banner, a blocking pre-run preflight in `start_run`, and a reverse-lookup `package-usage` endpoint that tags packages in the env drawer. The env page gains a bulk `PUT /environments/{id}/packages` endpoint backing a new `PackageDrawer`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (async), Pydantic v2, pytest; React + TypeScript + Vitest (web).

**Spec:** `docs/superpowers/specs/2026-06-04-env-package-management-node-awareness-design.md`

---

## File Structure

**Create:**
- `packages/core/noodle/packages.py` — canonical package-name helpers (shared by API + nodes).
- `packages/core/tests/test_packages.py` — tests for the helpers.
- `apps/api/app/services/package_preflight.py` — collect a graph's required-but-missing packages.
- `apps/web/src/PackageDrawer.tsx` — the env package manager drawer.
- `apps/web/src/PackageDrawer.test.tsx` — drawer + requirements.txt diff tests.
- `apps/web/src/editor/missingPackages.ts` — pure helper: compute a node's missing packages vs an env.
- `apps/web/src/editor/missingPackages.test.ts` — tests for that helper.

**Modify:**
- `packages/core/noodle/models.py` — add `requirements` to `NodeManifest`.
- `packages/core/noodle/sdk.py` — `node()` + `_build_manifest()` thread `requirements`.
- `packages/core/tests/test_sdk.py` — assert decorator carries `requirements`.
- `packages/nodes/noodle_nodes/integrations_v2/specs.py` — add `requirements` to `OperationSpec` + `ProviderTriggerSpec`.
- `packages/nodes/noodle_nodes/integrations_v2/node_factory.py` — pass `requirements` into manifests.
- `packages/nodes/noodle_nodes/datasets.py`, `ml.py`, `charts.py` — declare requirements on heavy nodes.
- `packages/nodes/tests/test_integrations_v2_registry.py` — assert factory default empty `requirements`.
- `apps/api/app/schemas.py` — add `requirements` to env package schemas; add `PackageUsageInfo`; add `requirements` won't go on `EnvironmentInfo`.
- `apps/api/app/routers/environments.py` — bulk `PUT /{id}/packages` + `GET /{id}/package-usage`.
- `apps/api/app/services/runner.py` — call preflight in `start_run`.
- `apps/api/tests/test_environments.py`, `apps/api/tests/test_runs.py` — endpoint + preflight tests.
- `apps/web/src/types.ts` — add `requirements` to `NodeManifest`; add `PackageUsage` type.
- `apps/web/src/api.ts` — `setPackages`, `packageUsage` methods.
- `apps/web/src/EnvironmentsPage.tsx` — count-only Packages tile opens `PackageDrawer`.

---

## Task 1: Canonical package-name helper (core)

**Files:**
- Create: `packages/core/noodle/packages.py`
- Test: `packages/core/tests/test_packages.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/test_packages.py
from noodle.packages import canonical_package_name, missing_packages


def test_canonical_strips_specifier_extras_and_normalizes():
    assert canonical_package_name("scikit-learn") == "scikit-learn"
    assert canonical_package_name("scikit_learn") == "scikit-learn"
    assert canonical_package_name("DuckDB>=0.9") == "duckdb"
    assert canonical_package_name("uvicorn[standard]==0.30") == "uvicorn"
    assert canonical_package_name("  pandas ; python_version>'3.8'  ") == "pandas"


def test_missing_packages_ignores_versions_and_case():
    required = ["duckdb>=0.9", "Pandas", "cairosvg"]
    installed = ["duckdb==1.1.0", "pandas"]
    assert missing_packages(required, installed) == ["cairosvg"]


def test_missing_packages_empty_when_all_present():
    assert missing_packages(["numpy"], ["numpy==2.1"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m pytest tests/test_packages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'noodle.packages'`

- [ ] **Step 3: Write minimal implementation**

```python
# packages/core/noodle/packages.py
"""Helpers for comparing pip requirement strings by canonical project name.

Used by the editor (missing-package banner), the run preflight, and the env
package-usage scan so they all agree on what "the env has this package" means.
"""

from __future__ import annotations

import re

# Matches the project-name prefix of a requirement specifier, e.g. the
# "scikit-learn" in "scikit-learn[extra]>=1.0 ; python_version>'3.8'".
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def canonical_package_name(spec: str) -> str:
    """Return the PEP 503 canonical project name from a requirement specifier.

    Strips environment markers, extras, and version constraints, then lowercases
    and collapses runs of ``-``/``_``/``.`` to a single ``-``.
    """
    head = spec.strip().split(";", 1)[0].strip()
    match = _NAME_RE.match(head)
    name = match.group(0) if match else head
    return re.sub(r"[-_.]+", "-", name).lower()


def missing_packages(required: list[str], installed: list[str]) -> list[str]:
    """Return the requirement specifiers in ``required`` whose canonical name is
    not present in ``installed`` (comparison ignores versions/extras/case)."""
    have = {canonical_package_name(p) for p in installed if p.strip()}
    return [req for req in required if canonical_package_name(req) not in have]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m pytest tests/test_packages.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/packages.py packages/core/tests/test_packages.py
git commit -m "feat(core): canonical package-name + missing-packages helpers"
```

---

## Task 2: Add `requirements` to NodeManifest + `@node` decorator

**Files:**
- Modify: `packages/core/noodle/models.py:127-129` (after `params`/`outputs` block)
- Modify: `packages/core/noodle/sdk.py` (`node()` ~793, `_build_manifest()` ~218, manifest construction ~838)
- Test: `packages/core/tests/test_sdk.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/test_sdk.py  (append)
def test_node_decorator_carries_requirements():
    from noodle.sdk import NodeRegistry, node

    reg = NodeRegistry()

    @node(name="Heavy", id="heavy_x", requirements=["duckdb>=0.9"], registry=reg)
    def heavy_x(input=None):
        return input

    manifest = reg.get("heavy_x").manifest
    assert manifest.requirements == ["duckdb>=0.9"]


def test_node_decorator_requirements_default_empty():
    from noodle.sdk import NodeRegistry, node

    reg = NodeRegistry()

    @node(name="Light", id="light_x", registry=reg)
    def light_x(input=None):
        return input

    assert reg.get("light_x").manifest.requirements == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m pytest tests/test_sdk.py -k requirements -v`
Expected: FAIL with `TypeError: node() got an unexpected keyword argument 'requirements'`

- [ ] **Step 3a: Add the field to NodeManifest**

In `packages/core/noodle/models.py`, inside `class NodeManifest`, add after the `outputs` field (line 129):

```python
    outputs: list[PortSpec] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
```

- [ ] **Step 3b: Thread it through `_build_manifest`**

In `packages/core/noodle/sdk.py`, add a parameter to `_build_manifest` (after `tool_side_effecting: bool = True` in its signature, ~line 237):

```python
    tool_side_effecting: bool = True,
    requirements: list[str] | None = None,
) -> NodeManifest:
```

And in the `return NodeManifest(...)` at the end of `_build_manifest`, add after `outputs=[...]`:

```python
        outputs=[
            PortSpec(name=o, data_kind=out_kinds.get(o, "any"))
            for o in outputs
        ],
        requirements=list(requirements or []),
    )
```

- [ ] **Step 3c: Add the kwarg to `node()`**

In `packages/core/noodle/sdk.py`, add to the `node()` signature (after `tool_side_effecting: bool = True,` ~line 813):

```python
    tool_side_effecting: bool = True,
    requirements: list[str] | None = None,
    registry: NodeRegistry = registry,
```

And pass it into the `_build_manifest(...)` call inside `node()`'s `decorator` (after `tool_side_effecting=tool_side_effecting,` ~line 856):

```python
            usable_as_tool=usable_as_tool,
            tool_side_effecting=tool_side_effecting,
            requirements=requirements,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m pytest tests/test_sdk.py -k requirements -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/models.py packages/core/noodle/sdk.py packages/core/tests/test_sdk.py
git commit -m "feat(core): nodes can declare pip requirements on their manifest"
```

---

## Task 3: Declare requirements on heavy built-in nodes

**Files:**
- Modify: `packages/nodes/noodle_nodes/datasets.py` (`@node` decorators at 260, 339, 395, 420, 447, 477, 517, 558, 594)
- Modify: `packages/nodes/noodle_nodes/ml.py` (`@node` decorators at 279, 393, 504, 556, 642, 718, 806, 855, 951)
- Modify: `packages/nodes/noodle_nodes/charts.py` (`@node` decorator at 633 only — "Chart To Image")
- Test: `packages/nodes/tests/test_node_requirements.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# packages/nodes/tests/test_node_requirements.py
import noodle_nodes  # noqa: F401 - registers built-in nodes
from noodle.packages import canonical_package_name
from noodle.sdk import registry


def _requirements(node_id: str) -> set[str]:
    return {canonical_package_name(r) for r in registry.get(node_id).manifest.requirements}


def test_duckdb_sql_node_requires_duckdb():
    # The DuckDB SQL node id — verify via the manifest name->id mapping below.
    ids = {m.id: m for m in registry.manifests()}
    duck = next(m for m in ids.values() if m.name == "DuckDB SQL")
    assert "duckdb" in {canonical_package_name(r) for r in duck.requirements}


def test_train_classifier_requires_sklearn_stack():
    ids = {m.name: m for m in registry.manifests()}
    reqs = {canonical_package_name(r) for r in ids["Train Classifier"].requirements}
    assert {"scikit-learn", "joblib", "pandas"} <= reqs


def test_chart_to_image_requires_cairosvg():
    ids = {m.name: m for m in registry.manifests()}
    reqs = {canonical_package_name(r) for r in ids["Chart To Image"].requirements}
    assert "cairosvg" in reqs


def test_plain_chart_node_has_no_requirements():
    ids = {m.name: m for m in registry.manifests()}
    assert ids["Chart"].requirements == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/nodes && python -m pytest tests/test_node_requirements.py -v`
Expected: FAIL on the assertions (requirements are empty lists).

- [ ] **Step 3: Add `requirements=` to the decorators**

For each dataset-operating node in `datasets.py` (all nine decorators listed above), add the kwarg, e.g. for the DuckDB SQL node at line 594:

```python
@node(
    name="DuckDB SQL",
    # ... existing kwargs ...
    requirements=["duckdb"],
)
```

Apply `requirements=["duckdb"]` to all nine `datasets.py` decorators.

For the ML training/predict/eval/save/load nodes in `ml.py` (the nine decorators listed), add:

```python
    requirements=["scikit-learn", "joblib", "pandas"],
```

For `charts.py`, add to **only** the "Chart To Image" decorator at line 633:

```python
@node(
    name="Chart To Image",
    # ... existing kwargs ...
    requirements=["cairosvg"],
)
```

Leave the SVG-only chart nodes (Chart, Metrics Chart, Build Report) with no `requirements`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/nodes && python -m pytest tests/test_node_requirements.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/datasets.py packages/nodes/noodle_nodes/ml.py packages/nodes/noodle_nodes/charts.py packages/nodes/tests/test_node_requirements.py
git commit -m "feat(nodes): declare pip requirements on duckdb/ml/cairosvg nodes"
```

---

## Task 4: integration_v2 specs + factory carry `requirements`

**Files:**
- Modify: `packages/nodes/noodle_nodes/integrations_v2/specs.py` (`OperationSpec` ~59, `ProviderTriggerSpec`)
- Modify: `packages/nodes/noodle_nodes/integrations_v2/node_factory.py` (`operation_manifest` ~48, `trigger_manifest` ~123)
- Test: `packages/nodes/tests/test_integrations_v2_registry.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# packages/nodes/tests/test_integrations_v2_registry.py  (append)
def test_operation_manifest_defaults_to_empty_requirements():
    from noodle_nodes.integrations_v2.node_factory import operation_manifest
    from noodle_nodes.integrations_v2.specs import OperationSpec

    spec = OperationSpec(
        node_id="acme.thing.do",
        name="Acme Do",
        provider="acme",
        resource="thing",
        operation="do",
    )
    assert operation_manifest(spec).requirements == []


def test_operation_manifest_passes_requirements_through():
    from noodle_nodes.integrations_v2.node_factory import operation_manifest
    from noodle_nodes.integrations_v2.specs import OperationSpec

    spec = OperationSpec(
        node_id="acme.thing.do",
        name="Acme Do",
        provider="acme",
        resource="thing",
        operation="do",
        requirements=("acme-sdk>=2",),
    )
    assert operation_manifest(spec).requirements == ["acme-sdk>=2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/nodes && python -m pytest tests/test_integrations_v2_registry.py -k requirements -v`
Expected: FAIL with `TypeError: OperationSpec.__init__() got an unexpected keyword argument 'requirements'`

- [ ] **Step 3a: Add the field to the specs**

In `packages/nodes/noodle_nodes/integrations_v2/specs.py`, add to `OperationSpec` (after `output_kind` ~line 75):

```python
    input_kind: PortDataKind = PortDataKind.main
    output_kind: PortDataKind = PortDataKind.main
    requirements: Sequence[str] = field(default_factory=tuple)
```

Find `class ProviderTriggerSpec` in the same file and add the identical `requirements: Sequence[str] = field(default_factory=tuple)` field to it.

- [ ] **Step 3b: Pass through both manifest builders**

In `node_factory.py`, in `operation_manifest`'s `return NodeManifest(...)` add after `outputs=[...]`:

```python
        outputs=[
            PortSpec(
                name="main",
                description="Operation result.",
                data_kind=spec.output_kind,
            )
        ],
        requirements=list(spec.requirements),
    )
```

Do the same in `trigger_manifest`'s `NodeManifest(...)`: add `requirements=list(spec.requirements),`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/nodes && python -m pytest tests/test_integrations_v2_registry.py -k requirements -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/specs.py packages/nodes/noodle_nodes/integrations_v2/node_factory.py packages/nodes/tests/test_integrations_v2_registry.py
git commit -m "feat(nodes): integration v2 specs can declare requirements (default empty)"
```

---

## Task 5: Bulk `PUT /environments/{id}/packages`

**Files:**
- Modify: `apps/api/app/schemas.py:144` (add `PackageListRequest`)
- Modify: `apps/api/app/routers/environments.py` (add `set_packages` route)
- Test: `apps/api/tests/test_environments.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_environments.py  (append; follow this file's existing
# client/fixture pattern — reuse the helper that creates an env and returns its id)
async def test_put_packages_replaces_list_and_dedups(client):
    env = (await client.post("/environments", json={"name": "pkgtest"})).json()
    resp = await client.put(
        f"/environments/{env['id']}/packages",
        json={"packages": ["pandas", "pandas==2.1", "  ", "duckdb"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # blank dropped; duplicate canonical name collapsed (last specifier wins)
    assert body["packages"] == ["pandas==2.1", "duckdb"]
    assert body["status"] == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_environments.py -k put_packages -v`
Expected: FAIL with 405 Method Not Allowed (route missing).

- [ ] **Step 3a: Add the request schema**

In `apps/api/app/schemas.py`, after `class PackageRequest` (line 146):

```python
class PackageListRequest(BaseModel):
    packages: list[str] = Field(default_factory=list)
```

- [ ] **Step 3b: Add the route**

In `apps/api/app/routers/environments.py`, add the import at top:

```python
from noodle.packages import canonical_package_name
```

and add `PackageListRequest` to the existing `from app.schemas import (...)` block. Then add this route after `add_package`:

```python
@router.put(
    "/{env_id}/packages",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def set_packages(
    env_id: str,
    body: PackageListRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Replace the env's full package list (dedup by canonical name, last wins).

    Backs both the comma-separated add and the requirements.txt import on the
    env page; the client computes the desired final list.
    """
    env = await _load(session, env_id)
    deduped: dict[str, str] = {}
    for raw in body.packages:
        spec = raw.strip()
        if spec:
            deduped[canonical_package_name(spec)] = spec
    packages = list(deduped.values())
    if packages != list(env.packages):
        env.packages = packages
        env.status = "pending"
        await session.commit()
        await session.refresh(env)
        background.add_task(build_environment, env.id)
    return _to_info(env, await _pool_name(session, env.runner_pool_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_environments.py -k put_packages -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/environments.py apps/api/tests/test_environments.py
git commit -m "feat(api): bulk PUT environments/{id}/packages with canonical dedup"
```

---

## Task 6: `GET /environments/{id}/package-usage`

**Files:**
- Modify: `apps/api/app/schemas.py` (add `PackageUsageEntry`, `PackageUsageInfo`)
- Modify: `apps/api/app/routers/environments.py` (add `package_usage` route)
- Test: `apps/api/tests/test_environments.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_environments.py  (append)
async def test_package_usage_maps_packages_to_nodes(client):
    # Global env exists at startup; bind a workflow to it with a duckdb node.
    envs = (await client.get("/environments")).json()
    glob = next(e for e in envs if e["is_global"])
    wf = (await client.post("/workflows", json={"name": "uses-duckdb"})).json()
    graph = {
        "nodes": [
            {"id": "n1", "type": "duckdb_sql", "label": "My Query",
             "params": {}, "position": {"x": 0, "y": 0}},
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{wf['id']}",
        json={"environment_id": glob["id"], "graph": graph},
    )
    resp = await client.get(f"/environments/{glob['id']}/package-usage")
    assert resp.status_code == 200
    usage = {u["package"]: u["used_by"] for u in resp.json()["packages"]}
    assert "duckdb" in usage
    assert any(
        e["workflow_id"] == wf["id"] and e["node_id"] == "n1"
        for e in usage["duckdb"]
    )
```

> Note: confirm the DuckDB SQL node's registered id with
> `registry.get(...)`/`manifests()` (it is the function name in `datasets.py`).
> Replace `"duckdb_sql"` in the test with the real id if different.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_environments.py -k package_usage -v`
Expected: FAIL with 404 (route missing).

- [ ] **Step 3a: Add the schemas**

In `apps/api/app/schemas.py`:

```python
class PackageUsageEntry(BaseModel):
    workflow_id: str
    workflow_name: str
    node_id: str
    node_label: str


class PackageUsagePackage(BaseModel):
    package: str  # canonical name
    used_by: list[PackageUsageEntry]


class PackageUsageInfo(BaseModel):
    packages: list[PackageUsagePackage]
```

- [ ] **Step 3b: Add the route**

In `apps/api/app/routers/environments.py`, add imports:

```python
import noodle_nodes  # noqa: F401 - registers built-in nodes
from noodle.sdk import registry as node_registry
from app.models import Workflow
from app.schemas import PackageUsageEntry, PackageUsageInfo, PackageUsagePackage
```

(merge the `app.schemas` names into the existing import block). Then:

```python
def _node_requirements_by_type() -> dict[str, list[str]]:
    return {m.id: m.requirements for m in node_registry.manifests() if m.requirements}


@router.get("/{env_id}/package-usage", response_model=PackageUsageInfo)
async def package_usage(
    env_id: str, session: AsyncSession = Depends(get_session)
):
    """Map each required package (canonical name) to the workflow nodes needing it.

    Scans every workflow bound to this env (plus null-env workflows when this is
    the global env), reading each workflow's draft graph (falling back to its
    latest published version's graph)."""
    env = await _load(session, env_id)
    reqs_by_type = _node_requirements_by_type()

    stmt = select(Workflow).options(selectinload(Workflow.versions))
    if env.is_global:
        stmt = stmt.where(
            (Workflow.environment_id == env_id)
            | (Workflow.environment_id.is_(None))
        )
    else:
        stmt = stmt.where(Workflow.environment_id == env_id)
    workflows = (await session.scalars(stmt)).all()

    usage: dict[str, list[PackageUsageEntry]] = {}
    for wf in workflows:
        graph = wf.draft_graph
        if graph is None and wf.versions:
            graph = wf.versions[-1].graph
        nodes = (graph or {}).get("nodes") or []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            reqs = reqs_by_type.get(n.get("type"))
            if not reqs:
                continue
            label = n.get("label") or n.get("type") or n.get("id") or ""
            for req in reqs:
                key = canonical_package_name(req)
                usage.setdefault(key, []).append(
                    PackageUsageEntry(
                        workflow_id=wf.id,
                        workflow_name=wf.name,
                        node_id=str(n.get("id") or ""),
                        node_label=str(label),
                    )
                )
    return PackageUsageInfo(
        packages=[
            PackageUsagePackage(package=k, used_by=v) for k, v in sorted(usage.items())
        ]
    )
```

Add `from sqlalchemy.orm import selectinload` if not already imported (check the top of the file; `select` is already imported).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_environments.py -k package_usage -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/environments.py apps/api/tests/test_environments.py
git commit -m "feat(api): environments/{id}/package-usage reverse-lookup"
```

---

## Task 7: Blocking pre-run preflight

**Files:**
- Create: `apps/api/app/services/package_preflight.py`
- Modify: `apps/api/app/services/runner.py` (`start_run`, in the env-resolution block ~643)
- Test: `apps/api/tests/test_runs.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_runs.py  (append; follow this file's client/run fixtures)
async def test_run_blocked_when_node_package_missing(client):
    # New env with no extra packages, bound to a workflow whose node needs duckdb.
    env = (await client.post(
        "/environments", json={"name": "bare", "packages": []}
    )).json()
    wf = (await client.post("/workflows", json={"name": "needs-duckdb"})).json()
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "q", "type": "duckdb_sql", "params": {},
             "position": {"x": 1, "y": 0}},
        ],
        "edges": [{"source": "t", "target": "q"}],
    }
    await client.put(
        f"/workflows/{wf['id']}",
        json={"environment_id": env["id"], "graph": graph},
    )
    resp = await client.post(f"/workflows/{wf['id']}/run")
    assert resp.status_code == 400
    assert "duckdb" in resp.json()["detail"].lower()
```

> Replace `"duckdb_sql"`/`"manual_trigger"` with the real registered ids if they differ.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_runs.py -k blocked_when_node_package_missing -v`
Expected: FAIL (run is accepted, returns 200).

- [ ] **Step 3a: Create the preflight service**

```python
# apps/api/app/services/package_preflight.py
"""Pre-run check: does the resolved env have every package its nodes need?"""

from __future__ import annotations

from noodle.packages import canonical_package_name
from noodle.sdk import registry as node_registry


def find_missing_packages(graph: dict, env_packages: list[str]) -> dict[str, list[str]]:
    """Return {missing_specifier: [node ids needing it]} for a graph + env.

    A package is "missing" when no installed package shares its canonical name.
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

- [ ] **Step 3b: Call it in `start_run`**

In `apps/api/app/services/runner.py`, add near the other service imports:

```python
from app.services.package_preflight import find_missing_packages, format_missing
```

In `start_run`, after the block that resolves `wf_obj` and `env_obj` for the runner pool (the `if env_id:` block ~643), add an explicit env-package check. Resolve the env's package list (the bound env, or the global env when unbound) and raise `ValueError` (the router already maps `ValueError` → 400):

```python
        # Preflight: block the run if a node needs a package the env lacks.
        if wf_obj is None:
            wf_obj = await session.get(Workflow, workflow_id)
        preflight_env = None
        if wf_obj and wf_obj.environment_id:
            preflight_env = await session.get(Environment, wf_obj.environment_id)
        if preflight_env is None:
            preflight_env = await session.scalar(
                select(Environment).where(Environment.is_global.is_(True))
            )
        if preflight_env is not None:
            missing = find_missing_packages(graph, list(preflight_env.packages))
            if missing:
                raise ValueError(format_missing(missing))
```

Place this inside the existing `async with SessionLocal() as session:` block, before the run record is created. `Environment` and `select` are already imported in this module.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_runs.py -k blocked_when_node_package_missing -v`
Expected: PASS

- [ ] **Step 5: Run the broader run suite to confirm no regressions**

Run: `cd apps/api && python -m pytest tests/test_runs.py -q`
Expected: PASS (existing tests still green — graphs without heavy nodes are unaffected).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/package_preflight.py apps/api/app/services/runner.py apps/api/tests/test_runs.py
git commit -m "feat(api): block runs whose nodes need packages the env lacks"
```

---

## Task 8: Web types + API client methods

**Files:**
- Modify: `apps/web/src/types.ts` (`NodeManifest`; add `PackageUsage`)
- Modify: `apps/web/src/api.ts` (`setPackages`, `packageUsage`)

- [ ] **Step 1: Add the `requirements` field to `NodeManifest`**

In `apps/web/src/types.ts`, find `interface NodeManifest` and add:

```typescript
  requirements: string[];
```

(If existing manifests are constructed in tests/mocks without it, make it `requirements?: string[]` to avoid breaking them; prefer required if the type is only populated from the API.)

- [ ] **Step 2: Add the usage types**

```typescript
// apps/web/src/types.ts
export interface PackageUsageEntry {
  workflow_id: string;
  workflow_name: string;
  node_id: string;
  node_label: string;
}

export interface PackageUsagePackage {
  package: string;
  used_by: PackageUsageEntry[];
}

export interface PackageUsage {
  packages: PackageUsagePackage[];
}
```

- [ ] **Step 3: Add the API methods**

In `apps/web/src/api.ts`, after `removePackage` (line 224), add:

```typescript
  setPackages: (id: string, packages: string[]) =>
    request<Environment>(`/environments/${id}/packages`, {
      method: "PUT",
      body: JSON.stringify({ packages }),
    }),
  packageUsage: (id: string) =>
    request<PackageUsage>(`/environments/${id}/package-usage`),
```

Add `PackageUsage` to the type imports at the top of `api.ts`.

- [ ] **Step 4: Typecheck**

Run: `cd apps/web && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts
git commit -m "feat(web): types + client for node requirements and package usage"
```

---

## Task 9: requirements.txt parse + diff helper (web)

**Files:**
- Create: `apps/web/src/editor/missingPackages.ts` (also hosts shared package utils)
- Test: `apps/web/src/editor/missingPackages.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// apps/web/src/editor/missingPackages.test.ts
import { describe, expect, it } from "vitest";
import {
  canonicalName,
  missingFor,
  parseRequirementsTxt,
  diffPackages,
} from "./missingPackages";

describe("canonicalName", () => {
  it("normalizes specifiers", () => {
    expect(canonicalName("scikit_learn>=1.0")).toBe("scikit-learn");
    expect(canonicalName("DuckDB[x]==1.1")).toBe("duckdb");
  });
});

describe("missingFor", () => {
  it("returns requirements absent from the env", () => {
    expect(missingFor(["duckdb>=0.9", "pandas"], ["pandas==2.1"])).toEqual([
      "duckdb>=0.9",
    ]);
  });
});

describe("parseRequirementsTxt", () => {
  it("ignores comments, blanks, and includes", () => {
    const text = "# comment\n\npandas==2.1\n-r other.txt\nnumpy ; python_version>'3.8'\n";
    expect(parseRequirementsTxt(text)).toEqual(["pandas==2.1", "numpy"]);
  });
});

describe("diffPackages", () => {
  it("computes add / present / removable by canonical name", () => {
    const d = diffPackages(["pandas==2.1", "numpy"], ["pandas", "duckdb"]);
    expect(d.toAdd).toEqual(["numpy"]);
    expect(d.alreadyPresent).toEqual(["pandas==2.1"]);
    expect(d.installedNotInFile).toEqual(["duckdb"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/missingPackages.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

```typescript
// apps/web/src/editor/missingPackages.ts
export function canonicalName(spec: string): string {
  const head = spec.trim().split(";")[0].trim();
  const match = head.match(/[A-Za-z0-9][A-Za-z0-9._-]*/);
  const name = match ? match[0] : head;
  return name.replace(/[-_.]+/g, "-").toLowerCase();
}

export function missingFor(requirements: string[], installed: string[]): string[] {
  const have = new Set(installed.filter((p) => p.trim()).map(canonicalName));
  return requirements.filter((r) => !have.has(canonicalName(r)));
}

export function parseRequirementsTxt(text: string): string[] {
  const out: string[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.split("#")[0].trim();
    if (!line || line.startsWith("-")) continue; // skip blanks/comments/includes
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/missingPackages.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/missingPackages.ts apps/web/src/editor/missingPackages.test.ts
git commit -m "feat(web): package canonicalization, missing-for, requirements.txt diff"
```

---

## Task 10: PackageDrawer component

**Files:**
- Create: `apps/web/src/PackageDrawer.tsx`
- Test: `apps/web/src/PackageDrawer.test.tsx`

The drawer takes an env, fetches `packageUsage`, shows: comma-add input, requirements.txt drop/browse with a diff preview (merge default, opt-in removals, node-required removal warnings), and a searchable installed list with green node tags and removal warnings. All mutations call `api.setPackages` with the computed final list and then `onChanged()`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/PackageDrawer.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PackageDrawer } from "./PackageDrawer";
import { api } from "./api";

const env = {
  id: "e1", name: "data-science", is_global: false, python_version: "3.12",
  packages: ["pandas", "duckdb"], status: "ready", status_detail: "",
  description: "", runner_pool_size: 1, runner_pool_max: null,
  effective_pool_max: 1, runner_pool_id: null, runner_pool_name: null,
  worker_rss_estimate_bytes: null, created_at: "", updated_at: "",
} as never;

afterEach(() => vi.restoreAllMocks());

describe("PackageDrawer", () => {
  it("adds comma-separated packages via setPackages", async () => {
    vi.spyOn(api, "packageUsage").mockResolvedValue({ packages: [] });
    const setPackages = vi
      .spyOn(api, "setPackages")
      .mockResolvedValue(env);
    const onChanged = vi.fn();
    render(<PackageDrawer env={env} onClose={() => {}} onChanged={onChanged} />);

    fireEvent.change(screen.getByPlaceholderText(/pandas, numpy/i), {
      target: { value: "numpy, httpx>=0.27" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() =>
      expect(setPackages).toHaveBeenCalledWith("e1", [
        "pandas",
        "duckdb",
        "numpy",
        "httpx>=0.27",
      ]),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("tags packages used by nodes", async () => {
    vi.spyOn(api, "packageUsage").mockResolvedValue({
      packages: [
        {
          package: "duckdb",
          used_by: [
            { workflow_id: "w1", workflow_name: "WF", node_id: "n1", node_label: "Query" },
          ],
        },
      ],
    });
    render(<PackageDrawer env={env} onClose={() => {}} onChanged={() => {}} />);
    expect(await screen.findByText(/node: Query/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/PackageDrawer.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the component**

```tsx
// apps/web/src/PackageDrawer.tsx
import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { canonicalName, diffPackages, parseRequirementsTxt } from "./editor/missingPackages";
import type { Environment, PackageUsagePackage } from "./types";

export function PackageDrawer({
  env,
  onClose,
  onChanged,
}: {
  env: Environment;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [usage, setUsage] = useState<PackageUsagePackage[]>([]);
  const [entry, setEntry] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingImport, setPendingImport] = useState<string[] | null>(null);
  const [removeStale, setRemoveStale] = useState(false);

  useEffect(() => {
    api.packageUsage(env.id).then((u) => setUsage(u.packages)).catch(() => setUsage([]));
  }, [env.id]);

  const usageByCanon = useMemo(() => {
    const m = new Map<string, PackageUsagePackage>();
    for (const p of usage) m.set(p.package, p);
    return m;
  }, [usage]);

  async function commit(packages: string[]) {
    setBusy(true);
    try {
      await api.setPackages(env.id, packages);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function addEntry() {
    const additions = entry.split(",").map((s) => s.trim()).filter(Boolean);
    if (additions.length === 0) return;
    const have = new Set(env.packages.map(canonicalName));
    const merged = [...env.packages];
    for (const a of additions) if (!have.has(canonicalName(a))) merged.push(a);
    setEntry("");
    await commit(merged);
  }

  async function onFile(file: File) {
    const specs = parseRequirementsTxt(await file.text());
    setPendingImport(specs);
    setRemoveStale(false);
  }

  const importDiff = pendingImport
    ? diffPackages(pendingImport, env.packages)
    : null;

  async function applyImport() {
    if (!importDiff) return;
    let result = [...env.packages, ...importDiff.toAdd];
    if (removeStale) {
      const removeSet = new Set(importDiff.installedNotInFile.map(canonicalName));
      result = result.filter((p) => !removeSet.has(canonicalName(p)));
    }
    setPendingImport(null);
    await commit(result);
  }

  async function removeOne(pkg: string) {
    const used = usageByCanon.get(canonicalName(pkg));
    if (used && used.used_by.length > 0) {
      const ok = window.confirm(
        `${pkg} is required by ${used.used_by.length} node(s). Remove anyway?`,
      );
      if (!ok) return;
    }
    await commit(env.packages.filter((p) => p !== pkg));
  }

  const visible = env.packages.filter((p) =>
    canonicalName(p).includes(canonicalName(filter)) || !filter.trim(),
  );

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="pkg-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="pkg-drawer-head">
          <div>
            <h3>{env.name} · packages</h3>
            <p className="muted">{env.packages.length} installed · rebuild runs on save</p>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Close">×</button>
        </header>

        <label className="field-label">Add packages</label>
        <div className="env-add">
          <input
            className="field-input"
            placeholder="pandas, numpy==2.1, httpx>=0.27 …"
            value={entry}
            onChange={(e) => setEntry(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void addEntry()}
          />
          <button className="btn btn-sm" disabled={busy} onClick={() => void addEntry()}>
            Add
          </button>
        </div>

        <label className="field-label">Import requirements.txt</label>
        <div
          className="pkg-dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files[0];
            if (f) void onFile(f);
          }}
        >
          <input
            type="file"
            accept=".txt"
            onChange={(e) => e.target.files?.[0] && void onFile(e.target.files[0])}
          />
          Drop a requirements.txt or browse
        </div>

        {importDiff && (
          <div className="pkg-import-diff">
            <p>
              +{importDiff.toAdd.length} to add · {importDiff.alreadyPresent.length} present
              · {importDiff.installedNotInFile.length} installed but not in file
            </p>
            {importDiff.installedNotInFile.length > 0 && (
              <label>
                <input
                  type="checkbox"
                  checked={removeStale}
                  onChange={(e) => setRemoveStale(e.target.checked)}
                />
                Also remove packages not in the file
                {removeStale &&
                  importDiff.installedNotInFile.some((p) =>
                    (usageByCanon.get(canonicalName(p))?.used_by.length ?? 0) > 0,
                  ) && (
                    <span className="warn-text"> ⚠ some are required by nodes</span>
                  )}
              </label>
            )}
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setPendingImport(null)}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={() => void applyImport()}>
                Apply
              </button>
            </div>
          </div>
        )}

        <label className="field-label">Installed</label>
        <input
          className="field-input"
          placeholder="filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <ul className="pkg-list">
          {visible.length === 0 && <li className="muted">No packages</li>}
          {visible.map((p) => {
            const used = usageByCanon.get(canonicalName(p));
            return (
              <li key={p} className="pkg-list-row">
                <span>
                  {p}
                  {used?.used_by.map((u) => (
                    <span className="pkg-tag-node" key={u.workflow_id + u.node_id}>
                      node: {u.node_label}
                    </span>
                  ))}
                </span>
                <button aria-label={`remove ${p}`} onClick={() => void removeOne(p)}>×</button>
              </li>
            );
          })}
        </ul>
      </aside>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/PackageDrawer.test.tsx`
Expected: PASS

- [ ] **Step 5: Add styles**

Append drawer styles to the app stylesheet (find where `.env-card` is defined, e.g. `apps/web/src/*.css`, and add `.drawer-overlay`, `.pkg-drawer`, `.pkg-drawer-head`, `.pkg-dropzone`, `.pkg-import-diff`, `.pkg-list`, `.pkg-list-row`, `.pkg-tag-node` following the existing modal/overlay styling). Reuse `.modal-overlay` patterns for the backdrop.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/PackageDrawer.tsx apps/web/src/PackageDrawer.test.tsx apps/web/src/*.css
git commit -m "feat(web): PackageDrawer with comma-add, requirements.txt diff, node tags"
```

---

## Task 11: EnvCard → count-only Packages tile opens the drawer

**Files:**
- Modify: `apps/web/src/EnvironmentsPage.tsx` (`EnvCard` 499-665)

- [ ] **Step 1: Wire the drawer into EnvCard**

In `EnvironmentsPage.tsx`, import the drawer:

```typescript
import { PackageDrawer } from "./PackageDrawer";
```

In `EnvCard`, add state:

```typescript
  const [showPackages, setShowPackages] = useState(false);
```

Replace the Packages health tile (lines 578-581) so it's a button:

```tsx
        <button
          type="button"
          className="env-health-tile-btn"
          onClick={() => setShowPackages(true)}
        >
          <span>Packages</span>
          <strong>{env.packages.length} ›</strong>
        </button>
```

Delete the inline `<div className="env-packages">…</div>` block (593-605) and the `<div className="env-add">…</div>` block (607-618), plus the now-unused `pkg`/`busy`/`add`/`remove` state and handlers (512-513, 523-538).

Render the drawer near the other modals (after the `EditEnvModal` block):

```tsx
      {showPackages && (
        <PackageDrawer
          env={env}
          onClose={() => setShowPackages(false)}
          onChanged={onChanged}
        />
      )}
```

- [ ] **Step 2: Typecheck + run the web test suite**

Run: `cd apps/web && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/EnvironmentsPage.tsx
git commit -m "feat(web): env card shows package count tile that opens the drawer"
```

---

## Task 12: Editor missing-package banner + fix actions

**Files:**
- Modify: the NDV component (find it: `grep -rl "from-ai\|NodeDetail\|ndv" apps/web/src/editor`; it's the panel that renders a selected node's params).
- Reuse: `apps/web/src/editor/missingPackages.ts` (`missingFor`).
- Test: `apps/web/src/editor/missingPackages.test.ts` already covers the pure logic; add a component test alongside the NDV's existing tests if the file has one.

- [ ] **Step 1: Locate the NDV and how it accesses the manifest + workflow env**

Run: `grep -rn "manifest" apps/web/src/editor/*.tsx | grep -i "param\|ndv\|detail" | head`
The NDV already has the selected node's `NodeManifest` (now with `requirements`) and the editor store holds the workflow's `environment_id`. Confirm how the store exposes the current environment's package list (it loads environments via `api.listEnvironments()`); if not present, add a derived `currentEnvPackages: string[]` selector that looks up the workflow's `environment_id` (falling back to the global env) in the loaded environments.

- [ ] **Step 2: Write the failing test (pure banner-state helper)**

Add to `apps/web/src/editor/missingPackages.test.ts`:

```typescript
import { missingFor } from "./missingPackages";

describe("banner state", () => {
  it("flags a node whose requirement is absent from the env", () => {
    const manifestRequirements = ["duckdb>=0.9"];
    const envPackages = ["pandas"];
    expect(missingFor(manifestRequirements, envPackages)).toEqual(["duckdb>=0.9"]);
  });
});
```

Run: `cd apps/web && npx vitest run src/editor/missingPackages.test.ts`
Expected: PASS (logic already exists; this locks the banner contract).

- [ ] **Step 3: Render the banner in the NDV**

In the NDV component, compute and render:

```tsx
import { missingFor } from "./missingPackages";
// ...
const missing = missingFor(manifest.requirements ?? [], currentEnvPackages);
// ...
{missing.length > 0 && (
  <div className="ndv-missing-pkgs warn-text">
    <p>
      This node needs {missing.join(", ")}, not installed in{" "}
      <strong>{currentEnvName}</strong>.
    </p>
    <div className="modal-actions">
      <button
        className="btn btn-sm btn-primary"
        onClick={() =>
          void api
            .setPackages(currentEnvId, [...currentEnvPackages, ...missing])
            .then(onEnvChanged)
        }
      >
        Add to {currentEnvName}
      </button>
      <button className="btn btn-sm" onClick={openEnvSwitcher}>
        Switch environment…
      </button>
    </div>
  </div>
)}
```

Wire `currentEnvId`/`currentEnvName`/`currentEnvPackages`/`onEnvChanged` from the editor store (the same source used elsewhere for the workflow's environment). `openEnvSwitcher` opens the existing environment picker (the editor already lets a workflow choose an env — reuse that control; if it lives in a settings panel, route the button there). Highlight envs that already satisfy `missing` using `missingFor(missing, env.packages).length === 0`.

- [ ] **Step 4: Add a small canvas badge (optional within this task)**

In the node card component, show a warning dot when `missingFor(manifest.requirements ?? [], currentEnvPackages).length > 0`. Reuse an existing badge/indicator class.

- [ ] **Step 5: Typecheck + full web tests**

Run: `cd apps/web && npx tsc --noEmit && npx vitest run`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/
git commit -m "feat(web): NDV banner for missing node packages with add/switch actions"
```

---

## Task 13: Full-suite verification

- [ ] **Step 1: Backend**

Run: `cd apps/api && python -m pytest -q` and `cd packages/core && python -m pytest -q` and `cd packages/nodes && python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Web**

Run: `cd apps/web && npx tsc --noEmit && npx vitest run && npx eslint src --max-warnings 0`
Expected: all green.

- [ ] **Step 3: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint + typecheck fixes for env package management"
```

---

## Self-Review Notes

- **Spec coverage:** A→Tasks 2,3,4; B→Task 12; C→Task 7; D→Tasks 5,9,10,11; E→Tasks 6,10. Canonicalization (cross-cutting)→Task 1 (Python) + Task 9 (TS mirror). Out-of-scope items (optional deps, static analysis) intentionally have no task.
- **Type consistency:** `canonical_package_name`/`missing_packages` (Python) mirror `canonicalName`/`missingFor` (TS). `PackageUsageEntry`/`PackageUsagePackage`/`PackageUsageInfo` are identical across `schemas.py`, `types.ts`, and the drawer. `setPackages`/`packageUsage` API names match between `api.ts` and the routes.
- **Verify-before-coding gaps the implementer must resolve:** the registered ids for the DuckDB SQL / manual trigger / Chart To Image nodes (Tasks 3, 6, 7 note this), and exactly how the editor store exposes the workflow's current env package list (Task 12 Step 1). These are lookups, not design changes.
