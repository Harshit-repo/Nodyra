# MCP support

> Looking to connect Claude Desktop, Codex, Cursor or another LLM and build
> workflows through MCP? See the step-by-step guide in
> [`connect-mcp.md`](./connect-mcp.md) (clients, tool catalogue, and hosting
> behind SSL for OAuth). This page is the concise reference.

## Noodle as an MCP server

Noodle exposes an MCP server at `POST /mcp` (streamable HTTP, stateless).
Disable with `MCP_SERVER_ENABLED=false`.

Connect from Claude Code:

    claude mcp add --transport http noodle http://localhost:8000/mcp \
      --header "Authorization: Bearer <session token>"

When `AUTH_REQUIRED=false` (local dev) the header may be omitted.
Run tools need a token whose role allows `workflow:run` (editor+);
builder tools need `workflow:write` (editor+).

### Tools

| Tool | Purpose |
|------|---------|
| `list_workflows` / `get_workflow` | discover workflows |
| `run_workflow` | run + wait, returns last-node output |
| `get_run` | poll a still-running run |
| `list_node_types` / `get_node_type` | discover node palette |
| `create_workflow` / `set_workflow_graph` / `validate_graph` / `publish_workflow` | build workflows |

Workflows with **MCP** enabled in the editor toolbar additionally
appear as their own tools (published version runs).

## Noodle as an MCP client

Create an **MCP Server** credential (URL + optional bearer token), then use:

- **MCP Tools** (`mcp_tools`) — supplies the server's tools to an AI Agent's
  tools port. Side-effecting by default, so agent approval gating applies.
- **MCP Call Tool** (`mcp_call_tool`) — call one named tool in the data flow.
- **MCP List Tools** (`mcp_list_tools`) — inspect a server's tool list.

Only HTTP(S) MCP servers are supported (no stdio). URLs are SSRF-guarded.
