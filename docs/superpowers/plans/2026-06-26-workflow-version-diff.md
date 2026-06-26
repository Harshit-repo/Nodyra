# Workflow Version Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-grade visual version diff to the workflow editor — a full-page canvas overlay showing added/removed/changed nodes between any two workflow versions.

**Architecture:** Backend adds `node_count` + `published` to the version list response and a new `/graph` endpoint for lazy graph loading. Frontend adds a pure `diffWorkflowGraphs` function, a `WorkflowDiffView` overlay with a ReactFlow canvas, and `DiffNode`/`DiffEdge` wrappers that apply colour-coded rings. `WorkflowHistory` gains a "Compare" button that opens the overlay.

**Tech Stack:** FastAPI (Python), SQLAlchemy async, `@xyflow/react` v12, React 18, Vitest, React Testing Library, Playwright

## Global Constraints

- `@xyflow/react` version: `^12.3.5` — use `useReactFlow()` hook for `fitView`; wrap diff canvas in `<ReactFlowProvider>`
- All new frontend files use `.tsx` for components, `.ts` for pure logic
- Deep equality: never use `JSON.stringify` for object comparison — use the `deepEqual` utility defined in Task 3
- `position` (`x`, `y`) excluded from node diff comparison — position-only moves are not changes
- All new API tests go in `apps/api/tests/test_workflow_versions.py`
- All new frontend unit tests colocate with their source file (`*.test.ts` / `*.test.tsx`)
- TDD: write failing test first, then implement

---

## File Map

**Create:**
- `apps/api/tests/test_workflow_versions.py` — backend version endpoint tests
- `apps/web/src/editor/nodeTypes.ts` — shared nodeTypes/edgeTypes registry (moved from Canvas.tsx)
- `apps/web/src/editor/diffWorkflowGraphs.ts` — pure diff function + deepEqual + DiffContext
- `apps/web/src/editor/diffWorkflowGraphs.test.ts` — unit tests for diff function
- `apps/web/src/editor/DiffNode.tsx` — node wrapper with coloured ring
- `apps/web/src/editor/DiffEdge.tsx` — edge wrapper with ghost/dashed styling
- `apps/web/src/editor/DiffSummaryBar.tsx` — summary strip + zoom button
- `apps/web/src/editor/NodeParamDiffPanel.tsx` — right sidebar param diff table
- `apps/web/src/editor/WorkflowDiffView.tsx` — main overlay component
- `apps/web/src/editor/WorkflowDiffView.test.tsx` — integration tests
- `apps/web/e2e/workflow-diff.e2e.ts` — Playwright E2E

**Modify:**
- `apps/api/app/schemas.py` — add `node_count: int`, `published: bool` to `WorkflowVersionInfo`
- `apps/api/app/routers/workflows.py` — update `list_versions`, add `get_version_graph` endpoint
- `apps/web/src/types.ts` — update `WorkflowVersionInfo`, add `DiffStatus`
- `apps/web/src/api.ts` — add `getVersionGraph`
- `apps/web/src/editor/Canvas.tsx` — import nodeTypes/edgeTypes from `nodeTypes.ts` instead of defining inline
- `apps/web/src/editor/WorkflowHistory.tsx` — use `node_count`, add Compare button, show notes + published badge
- `apps/web/src/EditorPage.tsx` — render `WorkflowDiffView` when triggered

---

### Task 1: Backend — version list enhancements + graph endpoint

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/routers/workflows.py`
- Create: `apps/api/tests/test_workflow_versions.py`

**Interfaces:**
- Produces: `GET /workflows/{id}/versions` now returns `node_count: int`, `published: bool`, `notes: str` per version (no `graph`)
- Produces: `GET /workflows/{id}/versions/{version_id}/graph` returns `{ "graph": { "nodes": [...], "edges": [...] } }`

- [ ] **Step 1: Write failing tests**

Create `apps/api/tests/test_workflow_versions.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_versions_includes_node_count_and_published(
    client: AsyncClient, auth_headers: dict, workflow_id: str
):
    """list_versions returns node_count and published, no graph."""
    resp = await client.get(
        f"/workflows/{workflow_id}/versions", headers=auth_headers
    )
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) >= 1
    v = versions[0]
    assert "node_count" in v
    assert "published" in v
    assert "notes" in v
    assert "graph" not in v  # graph stripped from list response
    assert isinstance(v["node_count"], int)
    assert isinstance(v["published"], bool)


@pytest.mark.asyncio
async def test_get_version_graph_returns_graph(
    client: AsyncClient, auth_headers: dict, workflow_id: str
):
    """New /graph sub-endpoint returns the full graph for one version."""
    # Get version list first to find a version ID
    resp = await client.get(
        f"/workflows/{workflow_id}/versions", headers=auth_headers
    )
    assert resp.status_code == 200
    version_id = resp.json()[0]["id"]

    resp = await client.get(
        f"/workflows/{workflow_id}/versions/{version_id}/graph",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "graph" in body
    assert "nodes" in body["graph"]
    assert "edges" in body["graph"]


@pytest.mark.asyncio
async def test_get_version_graph_404_unknown_version(
    client: AsyncClient, auth_headers: dict, workflow_id: str
):
    resp = await client.get(
        f"/workflows/{workflow_id}/versions/nonexistent/graph",
        headers=auth_headers,
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_workflow_versions.py -v
```
Expected: FAIL — `node_count` not in response, `/graph` endpoint doesn't exist.

- [ ] **Step 3: Update `WorkflowVersionInfo` schema**

In `apps/api/app/schemas.py`, find the `WorkflowVersionInfo` class (line ~155) and update:

```python
class WorkflowVersionInfo(BaseModel):
    id: str
    version: int
    notes: str = ""
    created_at: datetime
    node_count: int = 0
    published: bool = False
```

- [ ] **Step 4: Update `list_versions` and add graph endpoint**

In `apps/api/app/routers/workflows.py`, find `list_versions` (~line 404) and replace it, then add the graph endpoint after `get_version`:

```python
@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionInfo])
async def list_versions(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_permission("workflow:read")),
):
    workflow = await _load(session, workflow_id)
    return [
        WorkflowVersionInfo(
            id=v.id,
            version=v.version,
            notes=v.notes,
            created_at=v.created_at,
            node_count=len((v.graph or {}).get("nodes", [])),
            published=(v.version == workflow.published_version),
        )
        for v in workflow.versions
    ]


class WorkflowVersionGraph(BaseModel):
    graph: dict


@router.get(
    "/{workflow_id}/versions/{version_id}/graph",
    response_model=WorkflowVersionGraph,
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def get_version_graph(
    workflow_id: str,
    version_id: str,
    session: AsyncSession = Depends(get_session),
):
    workflow = await _load(session, workflow_id)
    for v in workflow.versions:
        if v.id == version_id:
            return WorkflowVersionGraph(graph=v.graph or {"nodes": [], "edges": []})
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
```

Also add `WorkflowVersionGraph` to the imports at the top of `schemas.py`:
```python
class WorkflowVersionGraph(BaseModel):
    graph: dict
```

Or define it inline in the router — either is fine since it's a simple response shape.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_workflow_versions.py -v
```
Expected: all 3 PASS.

- [ ] **Step 6: Run full API test suite to check for regressions**

```bash
cd apps/api && python -m pytest tests/ -x -q
```
Expected: same pass count as before (any pre-existing failures are unchanged).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/workflows.py apps/api/tests/test_workflow_versions.py
git commit -m "feat(api): version list node_count+published + graph endpoint"
```

---

### Task 2: Frontend types + API client

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`

**Interfaces:**
- Produces: `WorkflowVersionInfo` frontend type with `node_count`, `published`, `notes`; `graph` removed
- Produces: `DiffStatus` type exported from `types.ts`
- Produces: `api.getVersionGraph(workflowId, versionId): Promise<WorkflowGraph>`

- [ ] **Step 1: Update `WorkflowVersionInfo` in `types.ts`**

Find `WorkflowVersionInfo` (~line 167) and replace:

```ts
export interface WorkflowVersionInfo {
  id: string;
  version: number;
  notes: string;
  created_at: string;
  published: boolean;
  node_count: number;
}

export type DiffStatus = 'added' | 'removed' | 'changed' | 'unchanged';
```

- [ ] **Step 2: Add `getVersionGraph` to `api.ts`**

Find `listWorkflowVersions` (~line 359) and add after it:

```ts
getVersionGraph: (workflowId: string, versionId: string) =>
  request<{ graph: WorkflowGraph }>(`/workflows/${workflowId}/versions/${versionId}/graph`),
```

- [ ] **Step 3: Fix the TypeScript errors from removing `graph` from `WorkflowVersionInfo`**

`WorkflowHistory.tsx` currently references `v.graph?.nodes?.length`. After removing `graph`, update those references to use `v.node_count`:

In `apps/web/src/editor/WorkflowHistory.tsx`, find `nodeDiff` (line ~35):
```ts
function nodeDiff(idx: number): string {
  if (idx >= versions.length - 1) return "";
  const diff = versions[idx].node_count - versions[idx + 1].node_count;
  if (diff === 0) return "";
  return diff > 0 ? ` +${diff}` : ` ${diff}`;
}
```

And remove the `selectedDiff` function and the `diff` variable entirely (the old text-based diff is replaced by the visual diff view — the panel below will now just show the notes and a Compare button).

Remove these lines from `WorkflowHistory.tsx`:
- The `nodeLabel` function
- The `selectedDiff` function
- The `const diff = selectedDiff()` line
- The entire `{diff ? ... : ...}` JSX block

Replace the selected version preview section with a simpler panel:
```tsx
{selected && (
  <div className="history-preview">
    <p className="history-preview-title">v{selected.version} — {formatDate(selected.created_at)}</p>
    {selected.notes && <p className="muted" style={{ fontSize: 12 }}>{selected.notes}</p>}
    <p className="muted" style={{ fontSize: 12 }}>
      {selected.node_count} nodes
    </p>
    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
      <button
        className="btn btn-sm btn-primary"
        onClick={() => { onRestore(selected); onClose(); }}
      >
        Restore v{selected.version}
      </button>
      {versions.length >= 2 && (
        <button
          className="btn btn-sm"
          onClick={() => { onCompare(selected); onClose(); }}
        >
          Compare
        </button>
      )}
    </div>
  </div>
)}
```

Update the `Props` interface and function signature for `WorkflowHistory`:
```ts
interface Props {
  workflowId: string;
  onClose: () => void;
  onRestore: (version: WorkflowVersionInfo) => void;
  onCompare: (version: WorkflowVersionInfo) => void;
}
```

Note: `onRestore` now receives the full `WorkflowVersionInfo` (not just `graph`) — `EditorPage` will fetch the graph separately. Update the `WorkflowHistory` import in `EditorPage.tsx` accordingly in Task 7.

- [ ] **Step 4: Check TypeScript compiles**

```bash
cd apps/web && npx tsc --noEmit
```
Expected: 0 errors (fix any remaining type errors before committing).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/editor/WorkflowHistory.tsx
git commit -m "feat(web): WorkflowVersionInfo type + getVersionGraph API + history Compare button"
```

---

### Task 3: `diffWorkflowGraphs` pure function (TDD)

**Files:**
- Create: `apps/web/src/editor/diffWorkflowGraphs.ts`
- Create: `apps/web/src/editor/diffWorkflowGraphs.test.ts`

**Interfaces:**
- Produces: `diffWorkflowGraphs(base: WorkflowGraph, compare: WorkflowGraph): DiffResult`
- Produces: `DiffResult` type (local to this file, re-exported)
- Produces: `DiffContext` — `React.Context<Map<string, DiffStatus>>`

- [ ] **Step 1: Write failing tests**

Create `apps/web/src/editor/diffWorkflowGraphs.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { diffWorkflowGraphs } from './diffWorkflowGraphs';
import type { WorkflowGraph } from '../types';

const node = (id: string, extra: object = {}) => ({
  id,
  type: 'noodle',
  position: { x: 0, y: 0 },
  data: { type: 'noodle', config: {}, label: id, ...extra },
});

const edge = (id: string, source: string, target: string) => ({
  id, source, target, type: 'default',
});

describe('diffWorkflowGraphs', () => {
  it('marks added nodes correctly', () => {
    const base: WorkflowGraph = { nodes: [node('a')], edges: [] };
    const compare: WorkflowGraph = { nodes: [node('a'), node('b')], edges: [] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.added).toEqual(['b']);
    expect(result.unchanged).toContain('a');
  });

  it('marks removed nodes correctly and includes them in removedNodes', () => {
    const base: WorkflowGraph = { nodes: [node('a'), node('b')], edges: [] };
    const compare: WorkflowGraph = { nodes: [node('a')], edges: [] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.removed).toEqual(['b']);
    expect(result.removedNodes.map(n => n.id)).toEqual(['b']);
  });

  it('marks changed nodes when a config param differs', () => {
    const base: WorkflowGraph = { nodes: [node('a', { config: { prompt: 'hello' } })], edges: [] };
    const compare: WorkflowGraph = { nodes: [node('a', { config: { prompt: 'world' } })], edges: [] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.changed).toEqual(['a']);
    expect(result.changedParams['a']).toEqual([
      { key: 'config', before: { prompt: 'hello' }, after: { prompt: 'world' } },
    ]);
  });

  it('does NOT mark changed when only position differs', () => {
    const base: WorkflowGraph = { nodes: [{ ...node('a'), position: { x: 0, y: 0 } }], edges: [] };
    const compare: WorkflowGraph = { nodes: [{ ...node('a'), position: { x: 999, y: 999 } }], edges: [] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.unchanged).toContain('a');
    expect(result.changed).toHaveLength(0);
  });

  it('does NOT mark changed when object key order differs (deepEqual, not JSON.stringify)', () => {
    const base: WorkflowGraph = { nodes: [node('a', { config: { a: 1, b: 2 } })], edges: [] };
    const compare: WorkflowGraph = { nodes: [node('a', { config: { b: 2, a: 1 } })], edges: [] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.unchanged).toContain('a');
    expect(result.changed).toHaveLength(0);
  });

  it('treats same-ID type change as remove+add, not changed', () => {
    const base: WorkflowGraph = { nodes: [{ ...node('a'), type: 'noodle' }], edges: [] };
    const compare: WorkflowGraph = { nodes: [{ ...node('a'), type: 'sticky' }], edges: [] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.removed).toContain('a');
    expect(result.added).toContain('a');
    expect(result.changed).toHaveLength(0);
  });

  it('handles null nodes/edges without throwing', () => {
    const base = { nodes: null, edges: undefined } as unknown as WorkflowGraph;
    const compare = { nodes: [], edges: [] };
    expect(() => diffWorkflowGraphs(base, compare)).not.toThrow();
    const result = diffWorkflowGraphs(base, compare);
    expect(result.added).toHaveLength(0);
  });

  it('returns all unchanged when same version passed twice', () => {
    const graph: WorkflowGraph = { nodes: [node('a'), node('b')], edges: [] };
    const result = diffWorkflowGraphs(graph, graph);
    expect(result.changed).toHaveLength(0);
    expect(result.added).toHaveLength(0);
    expect(result.removed).toHaveLength(0);
    expect(result.unchanged).toHaveLength(2);
  });

  it('marks added/removed edges', () => {
    const base: WorkflowGraph = { nodes: [node('a'), node('b')], edges: [edge('e1', 'a', 'b')] };
    const compare: WorkflowGraph = { nodes: [node('a'), node('b'), node('c')], edges: [edge('e2', 'b', 'c')] };
    const result = diffWorkflowGraphs(base, compare);
    expect(result.removedEdges).toContain('e1');
    expect(result.addedEdges).toContain('e2');
  });
});
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd apps/web && npx vitest run src/editor/diffWorkflowGraphs.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `diffWorkflowGraphs.ts`**

Create `apps/web/src/editor/diffWorkflowGraphs.ts`:

```ts
import { createContext } from 'react';
import type { WorkflowGraph, DiffStatus } from '../types';
import type { GraphNode, GraphEdge } from '../types';

export type ParamDiff = { key: string; before: unknown; after: unknown };

export type DiffResult = {
  added: string[];
  removed: string[];
  changed: string[];
  unchanged: string[];
  removedNodes: GraphNode[];
  addedEdges: string[];
  removedEdges: string[];
  changedParams: Record<string, ParamDiff[]>;
};

/** O(1) status lookup provided by WorkflowDiffView to all DiffNode children. */
export const DiffContext = createContext<Map<string, DiffStatus>>(new Map());

/** Recursively compares two values for deep equality, ignoring key order in objects. */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, (b as unknown[])[i]));
  }
  const aKeys = Object.keys(a as object).sort();
  const bKeys = Object.keys(b as object).sort();
  if (aKeys.length !== bKeys.length) return false;
  if (!aKeys.every((k, i) => k === bKeys[i])) return false;
  return aKeys.every(k =>
    deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k])
  );
}

/** Compare node data fields, excluding position. */
function nodeDataEqual(a: GraphNode, b: GraphNode): boolean {
  const { position: _pa, ...restA } = a as GraphNode & { position: unknown };
  const { position: _pb, ...restB } = b as GraphNode & { position: unknown };
  return deepEqual(restA, restB);
}

export function diffWorkflowGraphs(
  base: WorkflowGraph,
  compare: WorkflowGraph,
): DiffResult {
  const baseNodes: GraphNode[] = base?.nodes ?? [];
  const compareNodes: GraphNode[] = compare?.nodes ?? [];
  const baseEdges: GraphEdge[] = base?.edges ?? [];
  const compareEdges: GraphEdge[] = compare?.edges ?? [];

  const baseMap = new Map(baseNodes.map(n => [n.id, n]));
  const compareMap = new Map(compareNodes.map(n => [n.id, n]));

  const added: string[] = [];
  const removed: string[] = [];
  const changed: string[] = [];
  const unchanged: string[] = [];
  const changedParams: Record<string, ParamDiff[]> = {};

  for (const node of compareNodes) {
    const prev = baseMap.get(node.id);
    if (!prev) {
      added.push(node.id);
    } else if (prev.type !== node.type) {
      // type change = remove + add
      removed.push(node.id);
      added.push(node.id);
    } else if (!nodeDataEqual(prev, node)) {
      changed.push(node.id);
      // Compute per-key diffs on `data` fields
      const diffs: ParamDiff[] = [];
      const aData = prev.data as Record<string, unknown> ?? {};
      const bData = node.data as Record<string, unknown> ?? {};
      const allKeys = new Set([...Object.keys(aData), ...Object.keys(bData)]);
      for (const key of allKeys) {
        if (!deepEqual(aData[key], bData[key])) {
          diffs.push({ key, before: aData[key], after: bData[key] });
        }
      }
      changedParams[node.id] = diffs;
    } else {
      unchanged.push(node.id);
    }
  }

  const removedNodes: GraphNode[] = [];
  for (const node of baseNodes) {
    if (!compareMap.has(node.id)) {
      removed.push(node.id);
      removedNodes.push(node);
    }
  }

  const baseEdgeIds = new Set(baseEdges.map(e => e.id));
  const compareEdgeIds = new Set(compareEdges.map(e => e.id));
  const addedEdges = compareEdges.filter(e => !baseEdgeIds.has(e.id)).map(e => e.id);
  const removedEdges = baseEdges.filter(e => !compareEdgeIds.has(e.id)).map(e => e.id);

  return { added, removed, changed, unchanged, removedNodes, addedEdges, removedEdges, changedParams };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/web && npx vitest run src/editor/diffWorkflowGraphs.test.ts
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/diffWorkflowGraphs.ts apps/web/src/editor/diffWorkflowGraphs.test.ts
git commit -m "feat(web): diffWorkflowGraphs pure function with deepEqual"
```

---

### Task 4: Extract shared `nodeTypes` + `DiffNode` + `DiffEdge`

**Files:**
- Create: `apps/web/src/editor/nodeTypes.ts`
- Create: `apps/web/src/editor/DiffNode.tsx`
- Create: `apps/web/src/editor/DiffEdge.tsx`
- Modify: `apps/web/src/editor/Canvas.tsx`

**Interfaces:**
- Produces: `nodeTypes` and `edgeTypes` exported from `nodeTypes.ts`
- Produces: `diffNodeTypes` and `diffEdgeTypes` exported from `nodeTypes.ts`
- Produces: `DiffNode` component that reads `DiffContext` and applies ring styling
- Produces: `DiffEdge` component that renders ghost edges

- [ ] **Step 1: Create `nodeTypes.ts`**

Create `apps/web/src/editor/nodeTypes.ts`:

```ts
import type { NodeProps, EdgeProps } from '@xyflow/react';
import { NodeCard } from './NodeCard';
import { StickyNote } from './StickyNote';
import { NodeGroup } from './NodeGroup';
import { MapGroupNode } from './MapGroupNode';
import { LoopFrame } from './LoopFrame';
import { MetaBar } from './MetaBar';
import { NoodleEdge } from './NoodleEdge';
import { DiffNode } from './DiffNode';
import { DiffEdge } from './DiffEdge';

export const nodeTypes = {
  noodle: NodeCard,
  sticky: StickyNote,
  group: NodeGroup,
  mapGroup: MapGroupNode,
  loopFrame: LoopFrame,
  metaBar: MetaBar,
};

export const edgeTypes = { default: NoodleEdge };

// Stable module-level maps — defined outside any component to avoid
// ReactFlow remounting nodes on re-render.
export const diffNodeTypes = Object.fromEntries(
  Object.entries(nodeTypes).map(([type, Component]) => [
    type,
    (props: NodeProps) => <DiffNode {...props} WrappedComponent={Component as React.ComponentType<NodeProps>} />,
  ])
) as typeof nodeTypes;

export const diffEdgeTypes = { default: DiffEdge };
```

Add the React import at the top:
```ts
import React from 'react';
```

- [ ] **Step 2: Update `Canvas.tsx` to import from `nodeTypes.ts`**

In `apps/web/src/editor/Canvas.tsx`, find (~line 54):
```ts
const nodeTypes = {
  noodle: NodeCard,
  sticky: StickyNote,
  group: NodeGroup,
  mapGroup: MapGroupNode,
  loopFrame: LoopFrame,
  metaBar: MetaBar,
};
const edgeTypes = { default: NoodleEdge };
```

Replace with:
```ts
import { nodeTypes, edgeTypes } from './nodeTypes';
```

Remove the individual component imports from Canvas.tsx that are now only needed in `nodeTypes.ts` (NodeCard, StickyNote, NodeGroup, MapGroupNode, LoopFrame, MetaBar, NoodleEdge) — but only if they are not used elsewhere in Canvas.tsx. Check by searching for each name in the file before removing.

- [ ] **Step 3: Create `DiffNode.tsx`**

Create `apps/web/src/editor/DiffNode.tsx`:

```tsx
import { useContext } from 'react';
import type { NodeProps } from '@xyflow/react';
import { DiffContext } from './diffWorkflowGraphs';
import type { DiffStatus } from '../types';

const ringStyle: Record<DiffStatus, React.CSSProperties> = {
  added:     { boxShadow: '0 0 0 2px #22c55e', borderRadius: 6 },
  removed:   { boxShadow: '0 0 0 2px #f87171', borderRadius: 6, opacity: 0.4, pointerEvents: 'none' },
  changed:   { boxShadow: '0 0 0 2px #f59e0b', borderRadius: 6, cursor: 'pointer' },
  unchanged: {},
};

interface DiffNodeProps extends NodeProps {
  WrappedComponent: React.ComponentType<NodeProps>;
}

export function DiffNode({ WrappedComponent, ...props }: DiffNodeProps) {
  const statusMap = useContext(DiffContext);
  const status: DiffStatus = statusMap.get(props.id) ?? 'unchanged';
  return (
    <div style={ringStyle[status]}>
      <WrappedComponent {...props} />
    </div>
  );
}
```

- [ ] **Step 4: Create `DiffEdge.tsx`**

Create `apps/web/src/editor/DiffEdge.tsx`:

```tsx
import { BaseEdge, getStraightPath } from '@xyflow/react';
import type { EdgeProps } from '@xyflow/react';
import type { DiffStatus } from '../types';

interface DiffEdgeData {
  diffStatus?: DiffStatus;
  [key: string]: unknown;
}

export function DiffEdge(props: EdgeProps<DiffEdgeData>) {
  const { sourceX, sourceY, targetX, targetY, data } = props;
  const status = data?.diffStatus ?? 'unchanged';

  const [edgePath] = getStraightPath({ sourceX, sourceY, targetX, targetY });

  if (status === 'removed') {
    return (
      <BaseEdge
        path={edgePath}
        style={{ stroke: '#f87171', strokeDasharray: '5 4', opacity: 0.5 }}
      />
    );
  }
  if (status === 'added') {
    return (
      <BaseEdge
        path={edgePath}
        style={{ stroke: '#22c55e', strokeWidth: 2 }}
      />
    );
  }
  // unchanged — render normally using NoodleEdge internals; fall back to BaseEdge
  return <BaseEdge path={edgePath} />;
}
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd apps/web && npx tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/nodeTypes.ts apps/web/src/editor/DiffNode.tsx apps/web/src/editor/DiffEdge.tsx apps/web/src/editor/Canvas.tsx
git commit -m "feat(web): shared nodeTypes + DiffNode/DiffEdge wrappers"
```

---

### Task 5: `DiffSummaryBar` + `NodeParamDiffPanel`

**Files:**
- Create: `apps/web/src/editor/DiffSummaryBar.tsx`
- Create: `apps/web/src/editor/NodeParamDiffPanel.tsx`

**Interfaces:**
- Consumes: `DiffResult` from `diffWorkflowGraphs.ts`
- Produces: `DiffSummaryBar` — renders counts, "Zoom to changes" button; calls `useReactFlow().fitView`
- Produces: `NodeParamDiffPanel` — renders before/after table for a node's changed params

- [ ] **Step 1: Create `DiffSummaryBar.tsx`**

Create `apps/web/src/editor/DiffSummaryBar.tsx`:

```tsx
import { useEffect } from 'react';
import { useReactFlow } from '@xyflow/react';
import type { DiffResult } from './diffWorkflowGraphs';

interface Props {
  diff: DiffResult;
  autoZoom: boolean;
}

export function DiffSummaryBar({ diff, autoZoom }: Props) {
  const { fitView } = useReactFlow();
  const { added, removed, changed, unchanged } = diff;
  const hasDiff = added.length + removed.length + changed.length > 0;

  useEffect(() => {
    if (!autoZoom || !hasDiff) return;
    const ids = [...added, ...removed, ...changed].map(id => ({ id }));
    // Defer until after ReactFlow has rendered the nodes
    const t = setTimeout(() => fitView({ nodes: ids, padding: 0.3, duration: 300 }), 50);
    return () => clearTimeout(t);
  }, [autoZoom, hasDiff, added, removed, changed, fitView]);

  return (
    <div className="diff-summary-bar">
      {added.length > 0 && (
        <span className="diff-count diff-count--added">● {added.length} added</span>
      )}
      {removed.length > 0 && (
        <span className="diff-count diff-count--removed">● {removed.length} removed</span>
      )}
      {changed.length > 0 && (
        <span className="diff-count diff-count--changed">● {changed.length} changed</span>
      )}
      <span className="diff-count diff-count--unchanged">· {unchanged.length} unchanged</span>
      {hasDiff && (
        <button
          className="btn btn-sm diff-zoom-btn"
          onClick={() => {
            const ids = [...added, ...removed, ...changed].map(id => ({ id }));
            fitView({ nodes: ids, padding: 0.3, duration: 300 });
          }}
        >
          Zoom to changes
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `NodeParamDiffPanel.tsx`**

Create `apps/web/src/editor/NodeParamDiffPanel.tsx`:

```tsx
import { useState } from 'react';
import type { ParamDiff } from './diffWorkflowGraphs';

interface Props {
  nodeId: string;
  nodeLabel: string;
  params: ParamDiff[];
  onClose: () => void;
}

function formatValue(v: unknown): string {
  if (v === undefined) return '(none)';
  if (v === null) return 'null';
  if (typeof v === 'string') return v;
  return JSON.stringify(v, null, 2);
}

export function NodeParamDiffPanel({ nodeId: _nodeId, nodeLabel, params, onClose }: Props) {
  const [showAll, setShowAll] = useState(false);
  const changedCount = params.length;

  return (
    <div className="param-diff-panel">
      <div className="param-diff-header">
        <span className="param-diff-title">{nodeLabel}</span>
        <button className="ndv-close" onClick={onClose} aria-label="Close param diff">×</button>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>
        {changedCount} changed param{changedCount !== 1 ? 's' : ''}
      </p>
      <table className="param-diff-table">
        <thead>
          <tr>
            <th>Param</th>
            <th>Before</th>
            <th>After</th>
          </tr>
        </thead>
        <tbody>
          {params.map(({ key, before, after }) => (
            <tr key={key}>
              <td className="param-diff-key">{key}</td>
              <td className="param-diff-before">
                <pre>{formatValue(before)}</pre>
              </td>
              <td className="param-diff-after">
                <pre>{formatValue(after)}</pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!showAll && (
        <button
          className="btn btn-sm"
          style={{ marginTop: 8 }}
          onClick={() => setShowAll(true)}
        >
          Show all params
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 3: TypeScript check**

```bash
cd apps/web && npx tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/editor/DiffSummaryBar.tsx apps/web/src/editor/NodeParamDiffPanel.tsx
git commit -m "feat(web): DiffSummaryBar and NodeParamDiffPanel components"
```

---

### Task 6: `WorkflowDiffView` main overlay (TDD)

**Files:**
- Create: `apps/web/src/editor/WorkflowDiffView.tsx`
- Create: `apps/web/src/editor/WorkflowDiffView.test.tsx`

**Interfaces:**
- Consumes: `diffNodeTypes`, `diffEdgeTypes` from `nodeTypes.ts`
- Consumes: `diffWorkflowGraphs`, `DiffContext` from `diffWorkflowGraphs.ts`
- Consumes: `api.getVersionGraph`, `api.listWorkflowVersions`
- Consumes: `DiffSummaryBar`, `NodeParamDiffPanel`
- Produces: `WorkflowDiffView` component with props `{ workflowId, initialVersion, versions, currentDraftGraph, onClose }`

- [ ] **Step 1: Write failing integration tests**

Create `apps/web/src/editor/WorkflowDiffView.test.tsx`:

```tsx
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { WorkflowDiffView } from './WorkflowDiffView';
import type { WorkflowVersionInfo, WorkflowGraph } from '../types';

// Mock ReactFlow — integration tests verify logic, not canvas rendering
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children: React.ReactNode }) => <div data-testid="rf-canvas">{children}</div>,
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useReactFlow: () => ({ fitView: vi.fn() }),
  Background: () => null,
  Controls: () => null,
}));

vi.mock('../api', () => ({
  api: {
    getVersionGraph: vi.fn(),
  },
}));

import { api } from '../api';

const v1: WorkflowVersionInfo = { id: 'v1', version: 1, notes: '', created_at: '2026-01-01T00:00:00Z', published: true, node_count: 1 };
const v2: WorkflowVersionInfo = { id: 'v2', version: 2, notes: 'Added node B', created_at: '2026-01-02T00:00:00Z', published: false, node_count: 2 };

const graphV1: WorkflowGraph = { nodes: [{ id: 'a', type: 'noodle', position: { x: 0, y: 0 }, data: { type: 'noodle', config: {}, label: 'A' } }], edges: [] };
const graphV2: WorkflowGraph = { nodes: [
  { id: 'a', type: 'noodle', position: { x: 0, y: 0 }, data: { type: 'noodle', config: {}, label: 'A' } },
  { id: 'b', type: 'noodle', position: { x: 200, y: 0 }, data: { type: 'noodle', config: {}, label: 'B' } },
], edges: [] };

describe('WorkflowDiffView', () => {
  beforeEach(() => {
    vi.mocked(api.getVersionGraph).mockImplementation(async (_wfId, versionId) => {
      return { graph: versionId === 'v1' ? graphV1 : graphV2 };
    });
  });

  it('renders overlay with version pickers', async () => {
    render(
      <WorkflowDiffView
        workflowId="wf1"
        initialVersion={v2}
        versions={[v2, v1]}
        currentDraftGraph={graphV2}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('rf-canvas')).toBeInTheDocument());
  });

  it('shows correct default versions (compare=clicked, base=one before)', async () => {
    render(
      <WorkflowDiffView
        workflowId="wf1"
        initialVersion={v2}
        versions={[v2, v1]}
        currentDraftGraph={graphV2}
        onClose={vi.fn()}
      />
    );
    await waitFor(() => {
      expect(screen.getByLabelText('Compare')).toHaveValue('v2');
      expect(screen.getByLabelText('Base')).toHaveValue('v1');
    });
  });

  it('shows "Current draft" as first compare option', async () => {
    render(
      <WorkflowDiffView
        workflowId="wf1"
        initialVersion={v2}
        versions={[v2, v1]}
        currentDraftGraph={graphV2}
        onClose={vi.fn()}
      />
    );
    const compareSelect = screen.getByLabelText('Compare');
    const options = Array.from((compareSelect as HTMLSelectElement).options).map(o => o.text);
    expect(options[0]).toMatch(/current draft/i);
  });

  it('shows "No differences" when graphs are identical', async () => {
    vi.mocked(api.getVersionGraph).mockResolvedValue({ graph: graphV1 });
    render(
      <WorkflowDiffView
        workflowId="wf1"
        initialVersion={v1}
        versions={[v2, v1]}
        currentDraftGraph={graphV1}
        onClose={vi.fn()}
      />
    );
    await waitFor(() => {
      expect(screen.getByText(/no differences/i)).toBeInTheDocument();
    });
  });

  it('shows retry button on graph fetch failure', async () => {
    vi.mocked(api.getVersionGraph).mockRejectedValueOnce(new Error('network error'));
    render(
      <WorkflowDiffView
        workflowId="wf1"
        initialVersion={v2}
        versions={[v2, v1]}
        currentDraftGraph={graphV2}
        onClose={vi.fn()}
      />
    );
    await waitFor(() => expect(screen.getByText(/retry/i)).toBeInTheDocument());
  });

  it('calls onClose when × is clicked', () => {
    const onClose = vi.fn();
    render(
      <WorkflowDiffView
        workflowId="wf1"
        initialVersion={v2}
        versions={[v2, v1]}
        currentDraftGraph={graphV2}
        onClose={onClose}
      />
    );
    fireEvent.click(screen.getByLabelText('Close diff view'));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd apps/web && npx vitest run src/editor/WorkflowDiffView.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `WorkflowDiffView.tsx`**

Create `apps/web/src/editor/WorkflowDiffView.tsx`:

```tsx
import { useState, useEffect, useMemo, useRef } from 'react';
import { ReactFlow, ReactFlowProvider, Background, Controls } from '@xyflow/react';
import type { WorkflowVersionInfo, WorkflowGraph, DiffStatus } from '../types';
import { api } from '../api';
import { diffWorkflowGraphs, DiffContext } from './diffWorkflowGraphs';
import type { DiffResult } from './diffWorkflowGraphs';
import { diffNodeTypes, diffEdgeTypes } from './nodeTypes';
import { DiffSummaryBar } from './DiffSummaryBar';
import { NodeParamDiffPanel } from './NodeParamDiffPanel';

type FetchState = 'idle' | 'loading' | 'loaded' | 'error';

interface VersionState {
  info: WorkflowVersionInfo | 'draft';
  graph: WorkflowGraph | null;
  fetchState: FetchState;
}

interface Props {
  workflowId: string;
  initialVersion: WorkflowVersionInfo;
  versions: WorkflowVersionInfo[];
  currentDraftGraph: WorkflowGraph;
  onClose: () => void;
}

const DRAFT_SENTINEL = 'draft';
const MAX_RENDER_NODES = 300;

export function WorkflowDiffView({ workflowId, initialVersion, versions, currentDraftGraph, onClose }: Props) {
  const prevVersion = versions.find(v => v.version === initialVersion.version - 1) ?? versions[versions.length - 1];

  const [compareState, setCompareState] = useState<VersionState>({
    info: initialVersion,
    graph: null,
    fetchState: 'idle',
  });
  const [baseState, setBaseState] = useState<VersionState>({
    info: prevVersion ?? initialVersion,
    graph: null,
    fetchState: 'idle',
  });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [autoZoom, setAutoZoom] = useState(true);

  const compareAbortRef = useRef<AbortController | null>(null);
  const baseAbortRef = useRef<AbortController | null>(null);

  async function fetchGraph(
    info: WorkflowVersionInfo | 'draft',
    abortRef: React.MutableRefObject<AbortController | null>,
    setState: React.Dispatch<React.SetStateAction<VersionState>>,
  ) {
    if (info === DRAFT_SENTINEL) {
      setState(s => ({ ...s, graph: currentDraftGraph, fetchState: 'loaded' }));
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState(s => ({ ...s, fetchState: 'loading' }));
    try {
      const { graph } = await api.getVersionGraph(workflowId, info.id);
      if (ctrl.signal.aborted) return;
      setState(s => ({ ...s, graph, fetchState: 'loaded' }));
    } catch {
      if (ctrl.signal.aborted) return;
      setState(s => ({ ...s, fetchState: 'error' }));
    }
  }

  useEffect(() => {
    fetchGraph(compareState.info, compareAbortRef, setCompareState);
    return () => compareAbortRef.current?.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareState.info]);

  useEffect(() => {
    fetchGraph(baseState.info, baseAbortRef, setBaseState);
    return () => baseAbortRef.current?.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseState.info]);

  const diff: DiffResult | null = useMemo(() => {
    if (!compareState.graph || !baseState.graph) return null;
    return diffWorkflowGraphs(baseState.graph, compareState.graph);
  }, [baseState.graph, compareState.graph]);

  const statusMap = useMemo<Map<string, DiffStatus>>(() => {
    if (!diff) return new Map();
    return new Map<string, DiffStatus>([
      ...diff.added.map(id => [id, 'added'] as [string, DiffStatus]),
      ...diff.changed.map(id => [id, 'changed'] as [string, DiffStatus]),
      ...diff.unchanged.map(id => [id, 'unchanged'] as [string, DiffStatus]),
    ]);
  }, [diff]);

  const { renderNodes, renderEdges } = useMemo(() => {
    if (!diff || !compareState.graph || !baseState.graph) return { renderNodes: [], renderEdges: [] };

    const allCompareNodes = compareState.graph.nodes ?? [];
    const allRemovedNodes = diff.removedNodes;
    const total = allCompareNodes.length + allRemovedNodes.length;
    const tooLarge = total > MAX_RENDER_NODES;

    const compareNodes = tooLarge
      ? allCompareNodes.filter(n => diff.added.includes(n.id) || diff.changed.includes(n.id))
      : allCompareNodes;
    const removedNodes = tooLarge
      ? allRemovedNodes.filter(n => diff.removed.includes(n.id))
      : allRemovedNodes;

    const nodes = [
      ...compareNodes.map(n => ({ ...n, data: { ...n.data, diffStatus: statusMap.get(n.id) ?? 'unchanged' } })),
      ...removedNodes.map(n => ({ ...n, data: { ...n.data, diffStatus: 'removed' as DiffStatus }, draggable: false, selectable: false })),
    ];

    const nodeIds = new Set(nodes.map(n => n.id));
    const baseEdgeMap = new Map((baseState.graph.edges ?? []).map(e => [e.id, e]));
    const edges = [
      ...(compareState.graph.edges ?? []).map(e => ({
        ...e,
        data: { ...e.data, diffStatus: diff.addedEdges.includes(e.id) ? 'added' : 'unchanged' },
      })),
      ...diff.removedEdges
        .map(id => baseEdgeMap.get(id))
        .filter((e): e is NonNullable<typeof e> => !!e && nodeIds.has(e.source) && nodeIds.has(e.target))
        .map(e => ({ ...e, data: { ...e.data, diffStatus: 'removed' as DiffStatus } })),
    ];

    return { renderNodes: nodes, renderEdges: edges, tooLarge };
  }, [diff, compareState.graph, baseState.graph, statusMap]);

  const tooLarge = renderNodes.length > MAX_RENDER_NODES;

  const compareValue = compareState.info === DRAFT_SENTINEL ? DRAFT_SENTINEL : (compareState.info as WorkflowVersionInfo).id;
  const baseValue = baseState.info === DRAFT_SENTINEL ? DRAFT_SENTINEL : (baseState.info as WorkflowVersionInfo).id;

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }

  const selectedNode = selectedNodeId && diff
    ? (compareState.graph?.nodes ?? []).find(n => n.id === selectedNodeId)
    : null;

  return (
    <div
      className="diff-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Workflow version diff"
      onKeyDown={handleKeyDown}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}
    >
      {/* Picker bar */}
      <div className="diff-picker-bar">
        <label htmlFor="diff-base-select">Base</label>
        <select
          id="diff-base-select"
          aria-label="Base"
          value={baseValue}
          onChange={e => {
            const val = e.target.value;
            const info = val === DRAFT_SENTINEL ? DRAFT_SENTINEL : versions.find(v => v.id === val)!;
            setBaseState({ info, graph: null, fetchState: 'idle' });
            setSelectedNodeId(null);
            setAutoZoom(false);
          }}
        >
          {versions.map(v => (
            <option key={v.id} value={v.id} disabled={v.id === compareValue}>
              v{v.version} {v.published ? '✓' : ''}{v.notes ? ` — ${v.notes}` : ''}
            </option>
          ))}
        </select>

        <span className="diff-arrow">→</span>

        <label htmlFor="diff-compare-select">Compare</label>
        <select
          id="diff-compare-select"
          aria-label="Compare"
          value={compareValue}
          onChange={e => {
            const val = e.target.value;
            const info = val === DRAFT_SENTINEL ? DRAFT_SENTINEL : versions.find(v => v.id === val)!;
            setCompareState({ info, graph: null, fetchState: 'idle' });
            setSelectedNodeId(null);
            setAutoZoom(false);
          }}
        >
          <option value={DRAFT_SENTINEL}>Current draft (unsaved)</option>
          {versions.map(v => (
            <option key={v.id} value={v.id} disabled={v.id === baseValue}>
              v{v.version} {v.published ? '✓' : ''}{v.notes ? ` — ${v.notes}` : ''}
            </option>
          ))}
        </select>

        <button className="ndv-close" onClick={onClose} aria-label="Close diff view">×</button>
      </div>

      {/* Summary bar + canvas + panel */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <ReactFlowProvider>
          {diff && (
            <DiffSummaryBar diff={diff} autoZoom={autoZoom} />
          )}

          {tooLarge && (
            <div className="diff-warning-banner">
              Workflow too large to show all nodes — displaying changed nodes only.
              <button className="btn btn-sm" onClick={() => {/* no-op: full render is already skipped */}}>
                Show all
              </button>
            </div>
          )}

          <div style={{ flex: 1, position: 'relative' }}>
            {(compareState.fetchState === 'loading' || baseState.fetchState === 'loading') && (
              <div className="diff-loading-overlay" style={{ opacity: 0.3, pointerEvents: 'none' }} />
            )}

            {compareState.fetchState === 'error' && (
              <p className="diff-error">
                Failed to load compare version.{' '}
                <button className="link-btn" onClick={() => fetchGraph(compareState.info, compareAbortRef, setCompareState)}>
                  Retry
                </button>
              </p>
            )}

            {baseState.fetchState === 'error' && (
              <p className="diff-error">
                Failed to load base version.{' '}
                <button className="link-btn" onClick={() => fetchGraph(baseState.info, baseAbortRef, setBaseState)}>
                  Retry
                </button>
              </p>
            )}

            {diff && diff.added.length === 0 && diff.removed.length === 0 && diff.changed.length === 0 && (
              <div className="diff-no-changes">
                No differences — this workflow matches the selected version.
              </div>
            )}

            <DiffContext.Provider value={statusMap}>
              <ReactFlow
                nodes={renderNodes}
                edges={renderEdges}
                nodeTypes={diffNodeTypes}
                edgeTypes={diffEdgeTypes}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={false}
                onNodeClick={(_, node) => {
                  if (statusMap.get(node.id) === 'changed') {
                    setSelectedNodeId(prev => prev === node.id ? null : node.id);
                  }
                }}
                fitView
              >
                <Background />
                <Controls />
              </ReactFlow>
            </DiffContext.Provider>

            {selectedNode && diff && diff.changedParams[selectedNode.id] && (
              <div style={{ position: 'absolute', top: 0, right: 0, height: '100%', width: 280, zIndex: 10 }}>
                <NodeParamDiffPanel
                  nodeId={selectedNode.id}
                  nodeLabel={(selectedNode.data as { label?: string }).label ?? selectedNode.id}
                  params={diff.changedParams[selectedNode.id]}
                  onClose={() => setSelectedNodeId(null)}
                />
              </div>
            )}
          </div>
        </ReactFlowProvider>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run integration tests**

```bash
cd apps/web && npx vitest run src/editor/WorkflowDiffView.test.tsx
```
Expected: all 5 tests PASS. Fix any failures before continuing.

- [ ] **Step 5: TypeScript check**

```bash
cd apps/web && npx tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/WorkflowDiffView.tsx apps/web/src/editor/WorkflowDiffView.test.tsx
git commit -m "feat(web): WorkflowDiffView overlay with diff canvas"
```

---

### Task 7: Wire up `WorkflowHistory` + `EditorPage`

**Files:**
- Modify: `apps/web/src/editor/WorkflowHistory.tsx` (already partially updated in Task 2)
- Modify: `apps/web/src/EditorPage.tsx`

**Interfaces:**
- Consumes: `WorkflowDiffView` from `WorkflowDiffView.tsx`
- Consumes: updated `WorkflowHistory` props from Task 2

- [ ] **Step 1: Finish `WorkflowHistory.tsx` updates**

Ensure the full updated `WorkflowHistory.tsx` includes the node count badge and published badge in the version list. The version list item should render:

```tsx
<li
  key={v.id}
  className={`history-item${selected?.id === v.id ? " history-item--selected" : ""}`}
  onClick={() => setSelected(selected?.id === v.id ? null : v)}
>
  <span className="history-version">v{v.version}</span>
  {v.published && <span className="history-badge history-badge--published">published</span>}
  <span className="history-date muted">{formatDate(v.created_at)}</span>
  <span className="muted" style={{ fontSize: 11 }}>{v.node_count} nodes</span>
</li>
```

The `nodeDiff` helper and the old text diff preview section should already be removed from Task 2. The selected preview section uses the simpler form from Task 2 with Restore + Compare buttons.

- [ ] **Step 2: Add diff state to `EditorPage.tsx`**

In `apps/web/src/EditorPage.tsx`, find where `showHistory` state is declared and add:

```ts
const [diffVersion, setDiffVersion] = useState<WorkflowVersionInfo | null>(null);
const [historyVersions, setHistoryVersions] = useState<WorkflowVersionInfo[]>([]);
```

Add `WorkflowDiffView` import at the top:
```ts
import { WorkflowDiffView } from "./editor/WorkflowDiffView";
```

- [ ] **Step 3: Update `WorkflowHistory` usage in `EditorPage`**

Find the `WorkflowHistory` block in `EditorPage.tsx` (~line 1796) and update:

```tsx
{showHistory && id && (
  <WorkflowHistory
    workflowId={id}
    onClose={() => setShowHistory(false)}
    onRestore={(version) => {
      // Fetch the graph for this version then restore it
      api.getVersionGraph(id, version.id).then(({ graph }) => {
        setRestoreGraph(graph);
      });
    }}
    onCompare={(version) => {
      setShowHistory(false);
      setDiffVersion(version);
    }}
  />
)}
```

Also capture `versions` from the history fetch. Since `WorkflowHistory` fetches versions internally, pass them out via a new `onVersionsLoaded` prop:

In `WorkflowHistory.tsx`, add to Props:
```ts
onVersionsLoaded?: (versions: WorkflowVersionInfo[]) => void;
```

And call it after `setVersions`:
```ts
.then((vs) => {
  const sorted = [...vs].sort((a, b) => b.version - a.version);
  setVersions(sorted);
  onVersionsLoaded?.(sorted);
})
```

In `EditorPage.tsx`:
```tsx
<WorkflowHistory
  workflowId={id}
  onClose={() => setShowHistory(false)}
  onVersionsLoaded={(vs) => setHistoryVersions(vs)}
  onRestore={(version) => {
    api.getVersionGraph(id, version.id).then(({ graph }) => {
      setRestoreGraph(graph);
    });
  }}
  onCompare={(version) => {
    setShowHistory(false);
    setDiffVersion(version);
  }}
/>
```

- [ ] **Step 4: Render `WorkflowDiffView` in `EditorPage`**

After the `WorkflowHistory` block, add:

```tsx
{diffVersion && id && graph && (
  <WorkflowDiffView
    workflowId={id}
    initialVersion={diffVersion}
    versions={historyVersions}
    currentDraftGraph={graph}
    onClose={() => setDiffVersion(null)}
  />
)}
```

`graph` is the current in-memory draft graph already available in `EditorPage` state.

- [ ] **Step 5: TypeScript check**

```bash
cd apps/web && npx tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 6: Run full vitest suite**

```bash
cd apps/web && npx vitest run
```
Expected: same pass count as before + new tests passing. Fix any regressions.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/WorkflowHistory.tsx apps/web/src/EditorPage.tsx
git commit -m "feat(web): wire WorkflowHistory Compare button → WorkflowDiffView"
```

---

### Task 8: E2E test + CSS stubs

**Files:**
- Create: `apps/web/e2e/workflow-diff.e2e.ts`
- Modify: existing CSS file (add diff-specific class stubs so the overlay renders visibly)

**Interfaces:**
- Consumes: the full wired-up feature from Tasks 1–7

- [ ] **Step 1: Add minimal CSS for diff overlay**

In `apps/web/src/settings.css` (or the main CSS file used by the editor), add:

```css
.diff-overlay {
  background: var(--bg, #1a1a2e);
  color: var(--fg, #e0e0e0);
}
.diff-picker-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border, #333);
  flex-shrink: 0;
}
.diff-summary-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 16px;
  border-bottom: 1px solid var(--border, #333);
  font-size: 13px;
  flex-shrink: 0;
}
.diff-count--added  { color: #22c55e; }
.diff-count--removed { color: #f87171; }
.diff-count--changed { color: #f59e0b; }
.diff-count--unchanged { color: var(--muted, #888); }
.diff-no-changes {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  color: var(--muted, #888);
  pointer-events: none;
}
.diff-warning-banner {
  background: #3b2a00;
  color: #f59e0b;
  padding: 6px 16px;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.diff-error {
  color: #f87171;
  padding: 8px 16px;
  font-size: 13px;
}
.param-diff-panel {
  height: 100%;
  background: var(--surface, #1e1e2e);
  border-left: 1px solid var(--border, #333);
  padding: 12px;
  overflow-y: auto;
}
.param-diff-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.param-diff-title { font-weight: 600; }
.param-diff-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.param-diff-table th, .param-diff-table td {
  text-align: left;
  padding: 4px 6px;
  border-bottom: 1px solid var(--border, #333);
  vertical-align: top;
}
.param-diff-key { color: var(--muted, #888); white-space: nowrap; }
.param-diff-before { color: #f87171; }
.param-diff-after  { color: #22c55e; }
.param-diff-table pre { margin: 0; white-space: pre-wrap; word-break: break-all; }
```

- [ ] **Step 2: Write E2E test**

Create `apps/web/e2e/workflow-diff.e2e.ts`:

```ts
import { test, expect } from '@playwright/test';
import { login, createWorkflow, addNode, publishWorkflow } from './helpers';

test.describe('Workflow version diff', () => {
  test('shows diff overlay with correct node status after two publishes', async ({ page }) => {
    await login(page);
    const wfId = await createWorkflow(page, 'Diff Test Workflow');

    // Publish v1 with one node (node A is added by createWorkflow)
    await publishWorkflow(page, 'Initial version');

    // Add a second node and publish v2
    await addNode(page, 'http_request', 'Node B');
    await publishWorkflow(page, 'Added Node B');

    // Open version history
    await page.getByRole('button', { name: /history/i }).click();
    await expect(page.getByText('v2')).toBeVisible();

    // Click Compare on v2
    await page.getByText('v2').click();
    await page.getByRole('button', { name: /compare/i }).click();

    // Diff overlay should open
    await expect(page.getByRole('dialog', { name: /version diff/i })).toBeVisible();

    // Summary bar should show 1 added
    await expect(page.getByText(/1 added/i)).toBeVisible();

    // Zoom to changes button visible
    await expect(page.getByRole('button', { name: /zoom to changes/i })).toBeVisible();

    // Version label appears in picker
    await expect(page.getByRole('option', { name: /added node b/i })).toBeDefined();

    // Close overlay
    await page.getByRole('button', { name: /close diff view/i }).click();
    await expect(page.getByRole('dialog', { name: /version diff/i })).not.toBeVisible();
  });

  test('shows "No differences" when comparing identical versions', async ({ page }) => {
    await login(page);
    await createWorkflow(page, 'No-diff Workflow');
    await publishWorkflow(page, 'v1');

    await page.getByRole('button', { name: /history/i }).click();
    await page.getByText('v1').click();

    // Compare v1 to draft (which hasn't changed)
    await page.getByRole('button', { name: /compare/i }).click();

    // Default compare is v1, base is also v1 equivalent (only 1 version)
    // Change compare picker to "Current draft"
    await page.getByLabel('Compare').selectOption({ label: /current draft/i });

    await expect(page.getByText(/no differences/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /zoom to changes/i })).not.toBeVisible();
  });
});
```

- [ ] **Step 3: Run E2E tests**

Start the app first (or use the existing E2E setup):
```bash
cd apps/web && npx playwright test e2e/workflow-diff.e2e.ts --headed
```
Expected: both tests PASS. Fix any selector mismatches before committing.

- [ ] **Step 4: Run full test suite one final time**

```bash
# Backend
cd apps/api && python -m pytest tests/ -x -q

# Frontend unit
cd apps/web && npx vitest run

# TypeScript
cd apps/web && npx tsc --noEmit
```
Expected: all pass, no regressions.

- [ ] **Step 5: Final commit**

```bash
git add apps/web/e2e/workflow-diff.e2e.ts apps/web/src/settings.css
git commit -m "feat(web): workflow diff E2E test + CSS"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Full-page overlay portal (z-index 1000) — Task 6
- ✅ Two version pickers, Base/Compare — Task 6
- ✅ "Current draft" as synthetic option — Task 6
- ✅ `diffWorkflowGraphs` pure function with deepEqual — Task 3
- ✅ Position excluded from diff — Task 3
- ✅ Type change = remove+add — Task 3
- ✅ DiffNode with colour rings — Task 4
- ✅ DiffEdge ghost rendering, dangling edge guard — Task 4
- ✅ `DiffContext` provided by WorkflowDiffView — Task 6
- ✅ `diffNodeTypes` module-level stable — Task 4
- ✅ Ghost node injection with statusMap — Task 6
- ✅ DiffSummaryBar with fitView after render (useEffect, 50ms defer) — Task 5
- ✅ "Zoom to changes" hidden when no diff — Task 5
- ✅ "No differences" empty state — Task 6
- ✅ NodeParamDiffPanel right sidebar — Task 5
- ✅ Lazy graph loading per picker — Task 6
- ✅ AbortController on unmount — Task 6
- ✅ Retry on fetch failure — Task 6
- ✅ >300 node cap with warning — Task 6
- ✅ node_count in list response — Task 1
- ✅ published badge in picker — Task 7
- ✅ notes displayed in picker and preview — Tasks 2 + 7
- ✅ `getVersionGraph` endpoint — Task 1
- ✅ `WorkflowHistory` Compare button — Task 2 + 7
- ✅ CSS classes — Task 8
- ✅ E2E test — Task 8
