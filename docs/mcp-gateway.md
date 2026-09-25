# Managed MCP gateway

The optional gateway lives in Nodyra's API. It authenticates MCP clients,
enforces operator-approved tool contracts and argument restrictions, and
records each managed external call separately from the workflow's result.
Domain-specific MCP servers stay where their integrations run; there is no
additional gateway service to deploy.

This feature is available in the source tree after 1.0.5. The existing 1.0.5
release archive does not contain it. Build the matching API, worker, and web
source together and apply migrations before enabling it.

## Enable it

For Compose, add these settings to `deploy/.env` and rebuild/recreate the API
and workers using your normal deployment command:

```dotenv
MCP_GATEWAY_ENABLED=true
MCP_GATEWAY_RATE_LIMIT_PER_MINUTE=120
MCP_GATEWAY_MAX_ARGUMENTS_BYTES=65536
```

Keep these values identical on every API and worker. The Helm equivalents are
`mcpGateway.enabled`, `mcpGateway.rateLimitPerMinute`, and
`mcpGateway.maxArgumentsBytes`. Gateway endpoints return 404 while disabled;
existing registered connections keep their legacy execution behavior.

Enabling the gateway makes managed calls fail closed until their contracts are
approved. Stage approvals and workflow versions in a maintenance window before
resuming schedules. Authentication is required on gateway endpoints even when
general development authentication is disabled. Keep public access behind HTTPS.

With `QUEUE_BACKEND=redis`, organization and actor quotas use the shared Redis
counter. If Redis is unavailable, calls are denied. Without Redis, counters are
local to each process; use that mode only for a single process installation.

## Review and approve a connection

1. Create a registered connection in **Settings → MCP Connections**. Configure
   its URL, encrypted authentication, and explicit tool allowlist.
2. As an admin or owner, request
   `GET /mcp-gateway/{connection_id}/catalog`. This negotiates an upstream session
   and returns validated tools, capabilities, and a `catalog_digest`.
3. Review the actual schemas, permission annotations, and destination arguments.
   Discovery and synchronization alone never authorize a call.
4. Send `PUT /mcp-gateway/{connection_id}/policy` with the reviewed digest,
   selected tool names, optional JSON Schema restrictions, and approved published
   workflow version IDs. The server discovers the catalog again and refuses
   approval if it changed during review.

Example request body for an upstream tool called `write_record`:

```json
{
  "catalog_digest": "COPY_THE_64_CHARACTER_DIGEST_FROM_CATALOG",
  "tools": {
    "write_record": {
      "type": "object",
      "properties": {"destination": {"const": "approved-test-table"}},
      "required": ["destination"]
    }
  },
  "workflow_version_ids": ["COPY_A_PUBLISHED_VERSION_ID"]
}
```

Replace every placeholder with your own reviewed values. Each tool is checked
against both its upstream input schema and these additional restrictions. `{}`
adds no extra restriction. Schemas may only reference local definitions; remote
schema references are rejected without fetching them. A policy supports up to
100 tools and 100 workflow versions.

Manage policies using a session with `mcp_gateway:manage`, or a PAT with that
scope and an admin/owner role. In a multi-organization installation, include
`X-Org-Id` for the intended organization. Browser cookie requests also require
the normal CSRF token for writes. Review the saved policy with
`GET /mcp-gateway/{connection_id}/policy`; revoke it with `DELETE` on that path.

Policy management is currently an operator API. The connection's existing
Settings screen remains the place to register servers and discover nodes.

## Connect a client or workflow

An MCP client uses `https://nodyra.example.com/mcp-gateway/{connection_id}` with
a PAT granting `mcp_gateway:call`; the account also needs editor, admin, or owner
permissions. This stateless Streamable HTTP endpoint supports `initialize`,
`ping`, `tools/list`, and `tools/call`. It exposes only explicitly approved tools.
Use the same-origin URL on installations with the bundled web proxy, or the
API's public origin. Legacy HTTP+SSE and stdio are not supported by this gateway.

Managed local workflow nodes using registered connections enter the same policy
service from the in-process, warm subprocess, and sandbox callback paths.
The host supplies the actor, organization, run, and workflow version. These are
not accepted from a worker's tool arguments. Each run must use an approved
published version whose original graph digest matches the policy. A modified
draft cannot borrow an approval from an older published graph. Child workflows
need their own approved published versions and retain the initiating actor.

Each call initializes an upstream MCP session and checks the current tool
contract and capabilities in that same session before dispatch. Required fields,
schemas, permission annotations, and capability changes require a new explicit
approval. Presentation-only description changes do not. Connection URL, headers,
authentication, or allowlist edits invalidate approval. Immediately before
dispatch, Nodyra rechecks policy revision, run status, run actor permission, and queue
lease ownership. This blocks stale worker attempts and changes observed during
discovery. Revocation cannot undo a call already dispatched, and there is no
distributed transaction with the upstream server. Interactive clients are
authenticated and authorized when their HTTP request is admitted; changing
their credentials does not cancel a request already in flight.

## Inspect what actually happened

Admins can request `GET /mcp-gateway/{connection_id}/invocations?limit=25`.
Records remain available after a connection is removed. A gateway response
includes `io.nodyra/correlationId` in `_meta`, matching the invocation ID.

Each record contains actor identity, organization, run and workflow version,
graph digest, tool, policy revision, argument digest, decision, reason, and
timestamps. Arguments use canonical JSON and a keyed HMAC; raw arguments,
argument names, provider bodies, and connection secrets are not stored in this
ledger. Result digests also use HMAC. Rotating `SECRET_KEY` changes these digests
and requires renewed connection approvals.

| Outcome | Meaning |
| --- | --- |
| `not_dispatched` | Policy, arguments, discovery, or identity checks denied the tool before execution. |
| `dispatch_pending` | Intent was saved. A crash or audit storage failure left the final result unrecorded. Reconcile before retrying. |
| `provider_reported_success` | The upstream MCP server returned a successful tool result. |
| `tool_error` | The dispatched tool returned an error. Partial downstream effects may still exist. |
| `outcome_unknown` | Dispatch was attempted but a transport or response failure prevents a reliable conclusion. |

Nodyra persists intent before starting dispatch. If that write fails, no tool
call starts. It never automatically retries an ambiguous external write.
Provider-reported success is not independent proof that Slack or a database
committed a change. For that assurance, use a provider receipt or an explicit
read-back step in the workflow and reconcile it using the correlation ID.

Gateway execution records are distinct from the ordinary `mcp_tool_call` audit
events used when the gateway is disabled. Policy changes use the normal audit
log. Gateway records and command approvals follow `AUDIT_LOG_RETENTION_DAYS`
alongside ordinary audit events (0 disables pruning). Protect database access
and apply your backup policy.

## Command approval and scope limits

Inbound Nodyra commands such as creating, editing, running, and publishing
workflows have a separate, single-use browser review flow. See
[Connect an MCP client](connect-mcp.md). Operator contract approval authorizes
outbound managed tool calls; it does not replace that command review.

This is an application execution boundary, not a universal network sandbox.
Arbitrary Python, direct-URL MCP integration nodes, and external runners that
do not use managed callbacks are outside it. Published graph approval does not
freeze separately stored code modules, installed dependencies, or the upstream
server implementation. Restrict author access and use container isolation and
network egress rules for stronger control. See the [security policy](../SECURITY.md).

Remote agent/Kubernetes subworkflow callbacks are refused while the gateway is
enabled because those transports do not yet carry a trusted MCP execution
context. Use managed local workers for workflows that need this boundary.

Transport behavior follows the MCP [Streamable HTTP specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
