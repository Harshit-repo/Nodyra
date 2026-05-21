# Writing a node

A node is a plain Python function. The `@node` decorator describes it for
the editor and registers it with the engine.

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

## How the SDK reads your function

- Parameters whose names appear in `@node(inputs=[...])` (default `["input"]`)
  are **input ports** — they receive data from upstream nodes via edges.
  Triggers pass `inputs=[]`.
- All other function parameters are **config parameters** — edited in the
  inspector, never wired. Type annotations drive the form field (`str`,
  `int`, `bool`, `dict`, `list`, `Any`). The `params=` decorator argument
  attaches UI metadata (choices, placeholder, multiline, description).
- The return value becomes the node's output. To declare multiple named
  outputs (for branching), pass `@node(outputs=["true", "false"])` and
  return a dict; omitting a key marks that branch as untaken.

## Categories and icons

Set `category` to one of `Triggers`, `Logic`, `Data`, `Transform`,
`Integrations`, `Utility`. The editor colours nodes by category. `icon` is a
name from the built-in icon set (`play`, `clock`, `webhook`, `branch`,
`switch`, `filter`, `merge`, `pencil`, `sort`, `limit`, `aggregate`, `dedupe`,
`tag`, `code`, `globe`, `calendar`, `braces`, `import`, `message`, `mail`,
`sheet`, `page`, `github`, `database`, `storage`, `ai`, `card`, `table`,
`dot`, `pause`).

## Official integration nodes

The built-in library includes first-pass official nodes for Slack, Discord,
SMTP/Gmail SMTP, Google Sheets, Notion, GitHub, Postgres, MySQL, S3/OpenStack
compatible stores, OpenAI, Anthropic, Stripe, and Airtable.

Most of these call public HTTP APIs with `requests`. `Postgres Query`,
`MySQL Query`, and `S3` nodes need Python drivers in the selected environment:
`psycopg[binary]`, `PyMySQL`, and `boto3` respectively.

## Async nodes

`async def` nodes are awaited by the engine — use them for IO-bound work.

## Triggers

Triggers have no input port. The runtime injects the triggering event into
the trigger node's output via the engine's `cache` (e.g. the Webhook
trigger's output is the captured request).

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
