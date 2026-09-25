# Build Nodyra workflows with Claude (or any MCP agent)

Nodyra ships a full MCP server: 61 tools covering incremental graph editing
(add/patch/remove nodes and edges with optimistic concurrency), validation,
publishing, runs, schedules, environments, and run approvals. Point an
MCP-capable agent at your instance and it can build, test, and deploy
workflows you can watch live on the canvas.

## 1. Create an API token

Settings -> API tokens -> New token. Scopes: `workflow:read`, `workflow:write`,
`workflow:run` (add `workflow:publish` and `deployment:write` if the agent
should publish or manage deployments). Copy the `ndpat_...` value.

## 2. Connect your agent

**Claude Code:**

```bash
claude mcp add --transport http nodyra http://localhost:8000/mcp \
  --header "Authorization: Bearer ndpat_YOUR_TOKEN"
```

Use the same `/mcp` path on the bundled web origin, for example
`https://nodyra.example.com/mcp`; the shipped Vite, nginx, and Helm ingress
configurations proxy that path to the API.

**Claude Desktop / Cursor / any streamable-HTTP client**: add to the MCP config:

```json
{
  "mcpServers": {
    "nodyra": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer ndpat_YOUR_TOKEN" }
    }
  }
}
```

## 3. Try it

Ask the agent: "List my Nodyra workflows", then "Create a workflow that fetches
https://api.github.com/repos/astral-sh/uv, extracts the star count with a Code
node, and runs it". Useful tool names: `list_workflows`, `create_workflow`,
`add_node`, `add_edge`, `validate_workflow_graph`, `run_workflow`, `get_run`.

## 4. Safety model

- Tokens are org-scoped; tools honour the token's scopes.
- All tool calls hit the audit log like any API call.
- `/mcp` is rate-limited at 120 requests per minute per principal.
- Sensitive commands return an approval link. Review the exact command in
  Nodyra with a separate browser session, then let the client retry unchanged
  arguments with the single-use `approval_id`. An agent's approval boolean is
  ignored. See [the approval flow](connect-mcp.md).
- Graph edits accept `expected_graph_revision` for optimistic concurrency, so
  agents editing alongside humans get a conflict error instead of clobbering.
- If `auth_required=false`, read-only MCP tools are anonymous by design. Put
  `/mcp` behind an authenticating proxy before exposing it publicly.

## Expose a workflow AS an MCP tool

Any published workflow can itself become an MCP tool for agents:
`enable_mcp_tool` with a JSON-schema for its parameters. External MCP servers
can likewise become nodes inside workflows: Settings -> MCP Connections.
