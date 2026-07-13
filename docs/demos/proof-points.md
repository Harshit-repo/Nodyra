# Product Proof-Point Demos

These demos back the three product wedges: Python-native, MCP-native, and
AI-inspectable. They are deterministic and run without external LLM calls.

## Python-Native

The proof script creates a workflow with a first-class Python `code` node,
tests that node in isolation with `POST /workflows/{id}/nodes/{node_id}/test`,
then runs the whole workflow.

```bash
uv run python scripts/proof_point_demos.py --base-url http://localhost:8000
```

Expected proof: node output `transform.main == 42` and workflow run status
`success`.

## MCP-Native

The MCP smoke script drives the same lifecycle through the MCP server:
initialize, create workflow, set graph, validate, run, and publish.

```bash
NODYRA_MCP_URL=http://localhost:8000/mcp uv run python scripts/mcp_smoke.py --no-token
```

Expected proof: the MCP-created workflow publishes after a successful run.

## AI-Inspectable

The proof script calls the AI-inspection surfaces without requiring an external
model provider:

- `POST /workflows/{id}/explain`
- `POST /workflows/{id}/generate-tests`
- `POST /workflows/{id}/checks`
- `POST /workflows/{id}/checks/run`
- `POST /workflows/{id}/publish`

Expected proof: generated test metadata is present, the human-readable workflow
check passes, and a published version is created.

## CI Coverage

The `compose-smoke` CI job boots the full compose stack, waits for
`/health/ready`, runs a REST smoke, runs `scripts/mcp_smoke.py`, then runs
`scripts/proof_point_demos.py`. That gives every proof point a live-stack gate.
