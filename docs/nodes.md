# Writing a node

There are two ways to ship a node:

1. **Built-in nodes** — a Python function with the `@node` decorator in
   `packages/nodes`. Ship at install time, available everywhere.
2. **Code modules** — a `.py` file uploaded through the editor. Each
   top-level function becomes a node. Scoped global / per-environment /
   per-workflow.

Both paths produce the same node manifest the editor consumes; the difference
is where the source lives and when it's loaded.

## Built-in node example

```python
from typing import Any

from noodle.sdk import node


@node(
    name="HTTP Request",
    id="http_request",
    category="Transform",
    icon="globe",
    params={
        "url": {"placeholder": "https://api.example.com/data"},
        "method": {"choices": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
    },
)
def http_request(input: Any = None, url: str = "", method: str = "GET") -> Any:
    """Call an HTTP API and return the JSON body."""
    import requests
    response = requests.request(method, url, timeout=30)
    try:
        return response.json()
    except ValueError:
        return response.text
```

### How the SDK reads your function

- Parameters whose names appear in `@node(inputs=[...])` (default
  `["input"]`) are **input ports** — they receive data from upstream nodes
  via edges. Triggers pass `inputs=[]`.
- All other function parameters are **config parameters** — edited in the
  inspector, never wired. Type annotations drive the form field (`str`,
  `int`, `bool`, `dict`, `list`, `Any`). The `params=` decorator argument
  attaches UI metadata (choices, placeholder, multiline, description).
- The return value becomes the node's output. To declare multiple named
  outputs (for branching), pass `@node(outputs=["true", "false"])` and
  return a dict; omitting a key marks that branch as untaken.

### Categories and icons

Set `category` to one of `Triggers`, `Logic`, `AI`, `Data`, `Transform`,
`Integrations`, `Utility`. The editor colours nodes by category. `icon` is a
name from the built-in icon set (`play`, `clock`, `webhook`, `branch`,
`switch`, `filter`, `merge`, `pencil`, `sort`, `limit`, `aggregate`,
`dedupe`, `tag`, `code`, `globe`, `calendar`, `braces`, `import`, `message`,
`mail`, `sheet`, `page`, `github`, `database`, `storage`, `ai`, `card`,
`table`, `dot`, `pause`).

### Async nodes

`async def` nodes are awaited by the engine — use them for IO-bound work.

### Per-node controls

The Settings tab in the inspector exposes:

- `disabled` — skip the node and downstream consumers.
- `on_error` — `stop` (default), `continue`, or `continue_branch`.
- `retry_on_fail`, `retries`, `retry_wait_seconds`, `retry_backoff` —
  retries with exponential backoff + small jitter.
- `timeout_seconds` — wraps the call in `asyncio.wait_for`. Sync nodes are
  hopped into a worker thread only when a timeout is set, so non-timeout
  paths keep the cheap direct-call semantics.
- `always_output_data` — emit empty output instead of skipping consumers
  when this node has no result.

### Triggers

Triggers have no input port. The runtime injects the triggering event into
the trigger node's output via the engine's `cache` (e.g. the Webhook
trigger's output is the captured request).

## Code modules (upload-to-nodes)

Open the **Code Library** page (or the Functions drawer in a workflow) and
upload a `.py` file. Discovery is AST-only — uploaded code is never executed
in the API process. Each top-level `def` becomes a node:

```python
def add(x: int = 0, y: int = 0) -> int:
    """Return x + y."""
    return x + y
```

The resulting node has:

- One virtual `input` port (the upstream envelope, available as
  `{{ $json }}` in expressions).
- Every function parameter rendered in the inspector. Required params
  (no default) are marked required; defaulted params start with the
  default literal.

To wire upstream data into a parameter, write an expression in the
inspector field: `{{ $json.x }}` reads `x` off the wired upstream, and
`{{ $node["other_id"].main.field }}` reads from a non-wired node. The
inspector's ƒx toggle flips between fixed and expression mode with explicit
visual state.

The engine filters kwargs to the function's real signature, so the virtual
`input` port is not passed to functions that don't declare an `input`
parameter.

### Starter graph

After uploading a procedural file, click **Build starter graph**. The
backend walks `<var> = <call>` assignments, infers edges from variable flow
(first var reference becomes a wired edge into the `input` port and sets
that field to `{{ $json }}`; subsequent references become cross-node
`{{ $node["id"].main }}` expressions), and pre-populates literal arguments
as default params. The graph is applied locally — the canvas is dirtied
and you review/save.

### Scopes

- **Global** — every workflow's palette sees it.
- **Environment** — only workflows on that env see it.
- **Workflow** — only that workflow's palette sees it. Stored alongside the
  workflow.

The runtime subprocess loads global + env modules at startup and the
workflow's modules at run time, so workflow scope doesn't leak across runs
sharing a warm process.

## Artifacts

For outputs that would be too large to stuff into `NodeRun.output` —
DataFrames, CSV/PDF/Excel exports, screenshots, scraped HTML, model
outputs — write to artifact storage instead. Inside a Code node or user
function:

```python
import pandas as pd

df = pd.read_csv(input["source"])
output = artifacts.write_dataframe(df, name="processed.csv")
```

`artifacts` is available in Code node scope automatically; user modules can
`import noodle.artifacts as artifacts`. Helpers:

- `artifacts.write_bytes(data, name, content_type)`
- `artifacts.write_text(text, name)`
- `artifacts.write_json(value, name)`
- `artifacts.write_dataframe(df, name, format="csv"|"json")`
- `artifacts.read_bytes(ref)` / `read_text` / `read_json` / `read_dataframe`
- `artifacts.open(ref, mode="rb")`

Writers return a small JSON-friendly ref:

```json
{
  "__noodle_artifact__": true,
  "version": 1,
  "artifact_id": "…",
  "run_id": "…",
  "node_id": "…",
  "name": "processed.csv",
  "kind": "dataframe",
  "content_type": "text/csv; charset=utf-8",
  "size_bytes": 12482,
  "storage_backend": "local",
  "storage_key": "runs/…/…/…-processed.csv"
}
```

Refs flow through edges, pinned data, and retry caches like any other JSON
value. The UI renders them as artifact cards with size/type metadata and a
download link. Retention prune drops files for expired runs automatically.

## DatasetRef tables

DatasetRef is the preferred shape for tabular data that may be too large to
copy through every node as inline JSON. A DatasetRef is a small JSON-friendly
reference to a Parquet-backed table plus schema, row-count, and preview
metadata. See the [DatasetRef guide](datasetref.md) for user-facing patterns.

Typical graph:

```text
records / CSV / DataFrame
  -> Records To Dataset or CSV Parse
  -> Dataset Filter / Dataset Select Columns / DuckDB SQL / Dataset Limit
  -> Dataset Preview or CSV Write
  -> optional Dataset To Records for inline-only consumers
```

Declare DatasetRef ports with `input_kinds` / `output_kinds`:

```python
@node(
    name="My Dataset Transform",
    id="my_dataset_transform",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
)
def my_dataset_transform(input=None):
    ...
```

The editor uses those port kinds to block confusing wires and offer quick
fixes such as **Records To Dataset** or **Dataset To Records**.

## Typed values across the wire

The engine serializes non-JSON Python types into typed envelopes only when
values leave the Python process (WebSocket events, persisted `NodeRun.output`,
pinned data, retry caches). In-process node-to-node hand-off uses real
Python objects. Handled types: DataFrame, datetime/date/time, Decimal, tuple,
set/frozenset, bytes/bytearray, generic objects (preview-only, non-restorable).

The frontend recognizes typed envelopes and renders DataFrames as tables
with dtype/shape metadata, scalars with type badges, bytes with byte-length
previews.

## Testing

The engine is async and importable. A complete graph round-trip:

```python
from noodle.engine import execute
from noodle.models import Edge, GraphNode, WorkflowGraph
from noodle.sdk import registry
import noodle_nodes  # registers the built-ins

graph = WorkflowGraph(
    nodes=[
        GraphNode(id="t", type="manual_trigger", params={"data": {"n": 3}}),
        GraphNode(id="c", type="code", params={"code": "output = input['n'] * 2"}),
    ],
    edges=[Edge(source="t", target="c")],
)
result = await execute(graph, registry)
assert result.nodes["c"].outputs["main"] == 6
```

## Official integration nodes

The built-in library includes first-pass official nodes for Slack, Discord,
SMTP/Gmail SMTP, Google Sheets, Notion, GitHub, Postgres, MySQL, S3/OpenStack
compatible stores, OpenAI, Anthropic, Stripe, and Airtable.

Most call public HTTP APIs with `requests`. `Postgres Query`, `MySQL Query`,
and `S3` nodes need Python drivers in the selected environment:
`psycopg[binary]`, `PyMySQL`, and `boto3` respectively.

## AI nodes

The canonical AI surface lives in the `AI` category. Provider-specific nodes
still exist for direct OpenAI/Anthropic/Pinecone operations, but new workflow
designs should prefer the normalized nodes:

- `AI Prompt Template` renders system/user messages from upstream data with
  Jinja templates.
- `AI Chat` calls OpenAI, Anthropic, OpenAI-compatible, Ollama, or Azure OpenAI
  chat APIs and returns a normalized `{text, usage, model, finish_reason}`
  envelope.
- `AI Structured Output` asks for JSON and validates the result against a JSON
  Schema.
- `AI Text Chunker`, `AI Batch Embeddings`, `AI Vector Retriever`, and
  `AI RAG Answer` provide the core RAG path.
- `AI Map Dataset` runs an LLM over a DatasetRef row-by-row and returns a new
  DatasetRef, keeping table data artifact-backed.
- `AI Tool` and `AI Agent` provide a bounded tool-using agent loop with
  `max_steps`, visible step logs, and explicit side-effect approval.
- `AI Moderation Guard`, `AI Vision Analyze`, and `AI Image Generate` cover
  guardrails and multimodal workflows.

Use an `LLM provider` credential for the normalized nodes. It can hold an
OpenAI, Anthropic, OpenAI-compatible, Ollama, or Azure OpenAI configuration.
Pinecone and provider-specific nodes have their own credential presets.
