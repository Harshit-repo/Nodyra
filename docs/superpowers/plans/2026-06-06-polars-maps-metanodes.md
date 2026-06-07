# Polars, Map Nodes, and Metanodes Plan

## Goal

Extend Noodle's Python-first workflow model without weakening the existing
DuckDB, DatasetRef, and ArtifactRef architecture.

This plan adds three related capabilities:

1. A Polars-powered dataset transform node.
2. Explicit map/fan-out nodes for items and datasets.
3. Workflow-backed metanodes that package a workflow as a reusable node.

The guiding principle is that fan-out and composition should be visible and
controlled. Noodle should not silently turn every downstream node into a
per-row execution engine.

## Non-Goals

- Do not replace DuckDB SQL nodes.
- Do not remove DatasetRef or ArtifactRef.
- Do not add a workflow-wide "map every node by default" toggle.
- Do not start with nested graph storage inside `GraphNode`.
- Do not make every existing node responsible for list mapping semantics.
- Do not add `Map Artifacts` as a separate node in the first implementation.
  Rows can already carry ArtifactRefs, and `Map Items` can pass the full item
  into a child workflow.

## Current Architecture Notes

Noodle already has the key foundations:

- `DatasetRef` is a JSON-friendly reference to a Parquet-backed table.
- `ArtifactRef` is a JSON-friendly reference to a stored file/blob.
- DuckDB dataset nodes read and write Parquet artifacts.
- The engine currently executes each graph node once per workflow run.
- `execute_workflow` already calls a child workflow through `workflow_caller`.
- Subworkflow execution already has host callback, subprocess, inline, and cycle
  handling.

Those pieces should be reused instead of creating a separate map runtime path.

## Phase 1: Polars Transform Node

### User Experience

Add a `Polars Transform` node:

```text
DatasetRef -> Polars Transform -> DatasetRef
```

The node exposes:

```python
input  # Polars LazyFrame
lf     # alias for input
pl     # polars module
```

The user assigns a Polars `DataFrame` or `LazyFrame` to `output`:

```python
output = (
    input
    .filter(pl.col("amount") > 100)
    .group_by("region")
    .agg(pl.col("amount").sum().alias("total_amount"))
    .sort("total_amount", descending=True)
)
```

### Backend Implementation

Add the node in:

```text
packages/nodes/noodle_nodes/datasets.py
```

Implementation outline:

```python
def _polars():
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError(
            "Polars is required for this node. Install with `uv pip install polars`."
        ) from exc
    return pl
```

Node contract:

```python
@node(
    name="Polars Transform",
    id="polars_transform",
    category="Data",
    icon="table",
    requirements=["polars"],
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "code": {
            "multiline": True,
            "placeholder": "output = input.filter(pl.col('amount') > 100)",
            "description": (
                "Polars Python over a DatasetRef. `input` is a LazyFrame; "
                "assign a Polars DataFrame or LazyFrame to `output`."
            ),
        },
    },
)
def polars_transform(input=None, code: str = "output = input"):
    ...
```

Execution steps:

1. Validate `input` is a DatasetRef.
2. Resolve the backing Parquet path.
3. Create `pl.scan_parquet(path)`.
4. AST-validate code using the same validator approach as the Code node.
5. Execute code with namespace `{ "pl": pl, "input": lf, "lf": lf }`.
6. Require `output`.
7. If `output` is a LazyFrame, collect it.
8. Require final value to be a Polars DataFrame.
9. Write Parquet to a reserved artifact path.
10. Return `_finalize_parquet(...)`.

### Tests

Add tests in:

```text
packages/nodes/tests/test_datasets.py
packages/nodes/tests/test_node_requirements.py
```

Test cases:

- Polars transform filters and aggregates rows.
- LazyFrame output is accepted.
- DataFrame output is accepted.
- Missing `output` raises a clear error.
- Non-Polars output raises a clear error.
- Node declares `requirements=["polars"]`.

## Phase 2: Map Items

### User Experience

Add a `Map Items` node:

```text
list -> Map Items(child workflow) -> main/errors
```

Each input item is sent to a child workflow as:

```json
{
  "item": { "id": 1, "email": "a@example.com" },
  "index": 0
}
```

The child workflow starts with Manual Trigger and processes one item.

### Node Parameters

```text
workflow_id
concurrency
on_error: fail | continue
preserve_order
include_input
result_field
```

Recommended defaults:

```text
concurrency = 5
on_error = fail
preserve_order = true
include_input = false
result_field = result
```

Outputs:

```text
main    successful child workflow results
errors  error rows when on_error = continue
```

When `on_error = fail`, the first child error fails the map node. When
`on_error = continue`, successful results stay on `main` and failures are
emitted on `errors`; do not mix success and error envelopes in one list.

### Artifact Pattern

Do not add a separate `Map Artifacts` node at first. A row can already contain
an ArtifactRef field, and the child workflow receives the entire item.

Input:

```json
[
  {
    "invoice_id": "A",
    "pdf": { "__noodle_artifact__": true, "artifact_id": "pdf_1" }
  },
  {
    "invoice_id": "B",
    "pdf": { "__noodle_artifact__": true, "artifact_id": "pdf_2" }
  }
]
```

Child workflow input for the first row:

```json
{
  "item": {
    "invoice_id": "A",
    "pdf": { "__noodle_artifact__": true, "artifact_id": "pdf_1" }
  },
  "index": 0
}
```

Downstream nodes in the child workflow can access the PDF field with an
expression such as:

```text
{{ $json.item.pdf }}
```

If this pattern proves common and the UI needs a shortcut, add a dedicated
artifact convenience node later.

### Backend Implementation

Add the node in:

```text
packages/nodes/noodle_nodes/builtin.py
```

The node declaration should include explicit multi-output ports:

```python
@node(
    name="Map Items",
    id="map_items",
    category="Logic",
    icon="repeat",
    outputs=["main", "errors"],
    params={...},
)
async def map_items(...):
    ...
```

Use the existing `workflow_caller` context var:

```python
from noodle.context import workflow_caller
```

Implementation outline:

```python
async def _map_call_child(
    *,
    caller,
    workflow_id: str,
    payload: dict,
    index: int,
    sem: asyncio.Semaphore,
    on_error: str,
) -> dict:
    async with sem:
        try:
            result = await caller(workflow_id, payload)
            return {"index": index, "ok": True, "result": result}
        except Exception as exc:
            if on_error == "continue":
                return {
                    "index": index,
                    "ok": False,
                    "error": str(exc),
                    "input": payload,
                }
            raise
```

The node should:

1. Normalize input to a list.
2. Validate `workflow_id`.
3. Get `workflow_caller`.
4. Create a semaphore from `concurrency`.
5. Call the child workflow once per item.
6. Preserve order by sorting on `index`.
7. Return successful child results on `main`.
8. Return error envelopes on `errors` when `on_error = continue`.

Result normalization:

```python
successful = [r["result"] for r in ordered if r["ok"]]
errors = [
    {
        "index": r["index"],
        "error": r["error"],
        "input": r["input"],
    }
    for r in ordered
    if not r["ok"]
]
return {"main": successful, "errors": errors}
```

If `errors` is empty, still return an empty list on the `errors` output so
downstream wiring remains predictable.

### Tests

Add tests for:

- One child call per item.
- Results preserve order.
- Concurrency does not change output order.
- `on_error="fail"` raises.
- `on_error="continue"` sends successful results to `main` and failures to
  `errors`.
- Missing `workflow_caller` raises a clear runtime error.
- Rows containing ArtifactRefs can be processed through the same `Map Items`
  pattern without a separate node.

## Phase 3: Map Dataset

### User Experience

Add a `Map Dataset` node:

```text
DatasetRef -> Map Dataset(child workflow per row) -> main/errors
```

Each row is sent to the child workflow as:

```json
{
  "row": {
    "invoice_id": "A",
    "amount": 120.5,
    "pdf": { "__noodle_artifact__": true, "artifact_id": "pdf_1" }
  },
  "index": 0
}
```

The child workflow returns one dict per row. `Map Dataset` collects those dicts
and writes a new DatasetRef.

### Node Parameters

```text
workflow_id
max_rows
concurrency
on_error: fail | continue
output: dataset | records
include_input
result_field
```

Recommended defaults:

```text
max_rows = 10000
concurrency = 5
on_error = fail
output = dataset
include_input = false
result_field = result
```

Outputs:

```text
main    DatasetRef or records, depending on output
errors  error rows when on_error = continue
```

### Backend Implementation

Add in:

```text
packages/nodes/noodle_nodes/datasets.py
```

The node declaration should include explicit multi-output ports:

```python
@node(
    name="Map Dataset",
    id="map_dataset",
    category="Data",
    icon="repeat",
    outputs=["main", "errors"],
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={...},
)
async def map_dataset(...):
    ...
```

Use existing helpers:

```python
materialize_dataset(...)
records_to_dataset(...)
```

Execution steps:

1. Validate input is DatasetRef.
2. Read the dataset row count before materializing rows.
3. If row count exceeds `max_rows`, raise a clear error. Do not silently
   truncate.
4. Materialize rows with `max_rows` only after the row-count check passes.
5. Call child workflow once per row.
6. Normalize each successful child output to a dict.
7. Send failures to the `errors` output when `on_error = continue`.
8. If `output == "records"`, return successful records on `main`.
9. If `output == "dataset"`, write successful records to Parquet and return
   a DatasetRef on `main`.

The row cap failure should be explicit:

```text
Dataset has 50000 rows but max_rows is 10000. Increase max_rows explicitly
or reduce rows upstream before Map Dataset.
```

The cap is a safety control, not truncation behavior.

Start bounded and non-streaming. Streaming/chunked dataset mapping can come
later after the UX and retry semantics are proven.

### Tests

Add tests for:

- Dataset rows are mapped through child workflow.
- Dataset output is a valid DatasetRef.
- Records output returns list.
- Max row cap raises before mapping when the dataset is too large.
- Continue-on-error sends successful rows to `main` and failures to `errors`.

## Phase 4: Workflow-Backed Metanodes

### Product Model

Metanodes should be workflow-backed composite nodes.

A user can mark a workflow as exposed:

```text
Workflow Settings -> Expose as Node
```

Then it appears in the node palette as one reusable node. At runtime it is
equivalent to `Execute Workflow`, but with better naming, category, icon, and
input/output metadata.

This avoids adding nested graph storage before the runtime semantics are stable.

### Metanode Metadata

Add metadata to workflow settings:

```text
expose_as_node: bool
node_name
node_description
node_category
node_icon
input_mode
output_mode
```

Possible input modes:

```text
single input
object input
dataset input
artifact input
```

Possible output modes:

```text
main
dataset
artifact
object
```

### Palette Behavior

The API node manifest endpoint should include exposed workflows as dynamic node
manifests.

The frontend palette displays them like normal nodes, but marks them as
workflow-backed.

When placed on the canvas, the node can either:

1. Store a normal graph node type like `metanode_execute`, with params
   containing `workflow_id`, or
2. Store type `execute_workflow` with richer UI metadata.

Recommended MVP: use `metanode_execute` so custom nodes can have distinct names
and icons in the palette while sharing runtime code.

### Runtime Behavior

`metanode_execute` should call the same `workflow_caller` mechanism as
`execute_workflow`.

The child workflow receives the parent input as its trigger output.

### Debugging

Run UI should show:

- Parent metanode node status.
- Child workflow run link or nested run summary.
- Inputs and outputs at the metanode boundary.

Start simple: show the parent node output and rely on existing child run storage.

## Phase 5: Create Metanode From Selected Nodes

After workflow-backed metanodes work, add a UI action:

```text
Select nodes -> Create Metanode
```

The editor should:

1. Identify selected nodes and internal edges.
2. Determine boundary inputs and outputs.
3. Create a new workflow containing the selected subgraph.
4. Replace the selected nodes with a metanode call.
5. Wire parent inputs/outputs through the new metanode.

This is more complex than workflow-backed metanodes and should not be first.

## UI Strategy

### MVP UI

Use child workflows as the body for maps and metanodes.

Parent workflow:

```text
DatasetRef -> Map Dataset(child workflow = "Parse one invoice") -> DatasetRef
```

Child workflow:

```text
Manual Trigger -> Extract Text -> LLM Parse -> Return Row
```

### Later UI

Render the child workflow as a collapsible embedded group:

```text
Map Dataset Start
  Extract Text
  LLM Parse
  Return Row
Map End
```

This can be a visual affordance over the workflow-backed runtime, not a new
runtime model.

## Risk Areas

### Fan-Out Cost

Map nodes can produce many child workflow calls. Controls are required:

- concurrency
- max items or max rows
- timeout
- fail or continue on error

Node descriptions and docs should make the cost model explicit. For example, a
10,000-row dataset with a 2-second child workflow and concurrency 5 can take
more than an hour of wall time.

At runtime, map nodes should attach cost/progress metadata. After the first
batch completes, estimate remaining wall time from observed child duration:

```json
{
  "map": {
    "count": 10000,
    "completed": 100,
    "avg_child_ms": 2100,
    "concurrency": 5,
    "estimated_remaining_ms": 4158000
  }
}
```

### Output Shape

Map nodes must clearly document whether they return:

- a list
- a DatasetRef
- errors output
- original input merged with result, when configured

Do not mix success and error shapes in a single output list. Use a separate
`errors` output for `on_error = continue`.

### Debuggability

Map nodes should attach debug metadata:

```json
{
  "map": {
    "count": 100,
    "success": 98,
    "error": 2,
    "workflow_id": "..."
  }
}
```

### Environment Requirements

Polars must be a declared node requirement. Metanodes should inherit or display
the requirements of the workflow they wrap.

## Acceptance Criteria

### Polars

- A DatasetRef can be transformed with Polars and returned as a DatasetRef.
- Environment preflight detects missing `polars`.
- DuckDB SQL nodes continue to work unchanged.

### Map Items

- A list can be processed through a child workflow once per item.
- Output ordering is deterministic.
- Error handling is configurable.
- Rows containing ArtifactRefs can be processed with `Map Items` by accessing
  the ArtifactRef field from the full item payload.

### Map Dataset

- DatasetRef rows can be processed through a child workflow.
- Results can be collected into a new DatasetRef.
- A dataset larger than `max_rows` fails before mapping and never silently
  truncates.
- Continue-on-error sends successful rows to `main` and failures to `errors`.

### Metanodes

- A workflow can be exposed as a node.
- The exposed node runs through existing subworkflow execution.
- Users can reuse Python-powered workflow logic without manually wiring
  `Execute Workflow` every time.

## Recommended Implementation Order

1. Add `Polars Transform`.
2. Add shared map helper for child workflow calls.
3. Add `Map Items`.
4. Add `Map Dataset`.
5. Add workflow-backed metanode execution.
6. Add palette support for exposed workflows.
7. Add "create metanode from selected nodes" UI.
8. Add embedded group-style visual editing.
