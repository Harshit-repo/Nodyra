# Connecting an LLM to Nodyra over MCP

Nodyra ships a built-in **MCP server** at `POST /mcp`. Any MCP-capable client —
Claude Code, Claude Desktop, OpenAI Codex, Cursor, Continue, or your own
script — can connect to it and then **discover node types, build workflow
graphs, run them, and publish versions** entirely through tool calls.

This guide covers:

1. [The endpoint & authentication](#1-endpoint--authentication)
2. [Connecting each client](#2-connecting-each-client) (Claude Code / Desktop / Codex / Cursor / generic)
3. [The tool catalogue](#3-tool-catalogue)
4. [Building & testing workflows through MCP](#4-building--testing-workflows-through-mcp)
5. [Hosting Nodyra behind SSL so you can test OAuth](#5-hosting-nodyra-behind-ssl-so-you-can-test-oauth)

---

## Human approval is bound to the exact action

Permission-bearing static tools that create, edit, run, retry, or publish work
require a **server-owned, single-use approval**. Reads (`get_*` / `list_*`) and
`cancel_run` do not require a new command approval. Published workflows exposed
as dynamic MCP tools follow their publication and execution policy.

On the first sensitive `tools/call`, Nodyra returns `isError: true` with a JSON
object in the text content:

```json
{
  "error": "human_approval_required",
  "approval_id": "<32-character approval ID>",
  "review_path": "/mcp-approvals/<approval ID>",
  "org_id": "<workspace ID>",
  "correlation_id": "<request correlation ID>",
  "expires_at": "<expiry timestamp>"
}
```

The client must show that path on the **Nodyra web app's origin**. The token's
owner signs in separately in a browser, selects the matching workspace, reviews
the displayed tool, arguments and target revision, and approves or denies it.
Only then should the client retry the **same arguments** with the returned
`approval_id` added. Approval expires after 15 minutes and is consumed once,
including when the subsequent execution fails. A timeout is not permission to
repeat an external effect: inspect the recorded outcome before requesting a new
review.

The server binds the grant to the requesting credential, actor, organization,
tool, normalized arguments and current target snapshot. A different token,
changed arguments, edited workflow, expired grant or replay is rejected.
`approved_by_user` remains accepted for compatibility but **cannot authorize an
action**.

For `resolve_run_approval`, pass the waiting workflow approval's ID as
`run_approval_id`. The separate `approval_id` field is reserved for the browser
review grant authorizing that decision. Existing callers must rename the old
run-target `approval_id` argument to `run_approval_id`.

Repeating the same pending request with the same credential reuses its review
ID. Each actor can have up to 25 pending reviews per workspace. Command approval
records and gateway invocation records follow `AUDIT_LOG_RETENTION_DAYS`
(90 days by default; `0` disables automatic cleanup).

Use a scoped `ndpat_...` automation token for the MCP client and keep the browser
session separate. The approval API rejects bearer/PAT approval requests and
rejects using the requesting session credential as its own browser reviewer.
The browser decision uses session cookies and CSRF protection. Do not hand an
agent your browser session cookie or automate the approval decision.

The policy includes `create_workflow`, `run_workflow`, `retry_run`, node and
workflow edits, and these consequential operations:

- `add_environment_package`
- `apply_workflow_patch`
- `create_code_node`
- `create_environment`
- `create_schedule`
- `delete_schedule`
- `delete_workflow`
- `publish_workflow`
- `rebuild_environment`
- `remove_edge`
- `remove_environment_package`
- `remove_node`
- `resolve_run_approval`
- `rollback_workflow`
- `set_environment_packages`
- `set_workflow_graph`
- `toggle_schedule`
- `toggle_workflow`
- `update_code`
- `update_schedule`

An approval does not expand RBAC or token scopes. The actor still needs the
underlying permission when the action is requested, reviewed and executed.

## Choosing which workflows an agent may run

Command approvals and workflow opt-in are independent server-enforced controls.
Approval binds one command; opt-in limits which workflows MCP may execute.

By default `run_workflow` accepts any workflow id in the workspace. That is
usually what you want on a laptop, where the agent and the workflows are both
yours. It is not what you want on an instance whose workflows send mail, post to
Slack, move money, or call a metered API — there, an agent that guesses or reads
an id can run any of them.

Set `MCP_RUN_REQUIRES_OPT_IN=true` and Nodyra will run only the workflows you
have explicitly exposed:

```bash
# docker-compose.yml, or the api service's environment
MCP_RUN_REQUIRES_OPT_IN=true
```

You opt a workflow in with `enable_mcp_tool` (or the MCP panel in the workflow's
settings), and out again with `disable_mcp_tool`. Exposing a workflow already
publishes it as its own named tool, so the same gesture now does both jobs: it
decides what the agent can *see* and what it can *run*.

A workflow that has not been exposed is refused with a message that names it and
says how to allow it, so the agent reports a decision to you rather than
retrying against what looks like an outage:

```
This deployment only lets MCP run workflows that have been exposed as tools,
and 'Payroll' has not been. Ask the workspace owner to run enable_mcp_tool for
it, or to enable it in the workflow's MCP settings. Retrying will not help.
```

**It defaults to off, deliberately.** Existing integrations call `run_workflow`
on workflows that were never exposed, and turning this on by default would break
them silently on upgrade. For any instance an agent can reach over a network,
turning it on is the recommended posture.

The restriction covers `retry_run` as well as `run_workflow`. That is worth
stating because it did not, at first: retrying re-executes by run id and reached
the execution path directly, so any run id of a revoked workflow was a way
around the setting. Both entry points are now gated, and
`apps/api/tests/test_mcp_run_allowlist.py` fails if a new tool that can start an
execution is added without being checked against the allowlist.

What this does *not* change is authorisation. An MCP session still acts as a
real user with that user's permissions; the allowlist narrows what that user's
agent may execute, it does not widen anything. Scope the token too.

## 1. Endpoint & authentication

| | |
|---|---|
| **URL** | `POST http://localhost:8000/mcp` (prod: `https://your-host/mcp`) |
| **Transport** | MCP 2025-11-25 Streamable HTTP, **stateless JSON** (exactly one JSON-RPC 2.0 message per POST). `GET`/`DELETE` return `405`. |
| **Auth** | `Authorization: Bearer <session token or org-scoped automation token>`. Required whenever `AUTH_REQUIRED=true`. |
| **Disable** | Set `MCP_SERVER_ENABLED=false`. |

### Getting a token

Sign in to Nodyra in your browser to review approvals. For read-only API
experiments, a session bearer token can also be obtained by logging in:

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}'
# -> {"token":"eyJ...","user":{...}}
```

Session tokens carry an `exp`
expiry — when calls start returning `401 Invalid or expired token`, log in
again for a fresh one. When `AUTH_REQUIRED=false` (pure local dev) the header
may be omitted for read-only local development. Sensitive commands still need
an authenticated requester and independent browser approval.

For agents, create a revocable automation token with `POST /auth/api-tokens`.
Keep this token distinct from the browser session used for human review:

```bash
curl -s -X POST https://your-host/auth/api-tokens \
  -H "Authorization: Bearer <session-token>" \
  -H "X-Org-Id: <org-id>" \
  -H "Content-Type: application/json" \
  -d '{"name":"production-agent","scopes":["workflow:run","workflow:write"],"expires_in_days":90}'
```

The returned `ndpat_...` secret is shown once, is permanently bound to that
organization, and can be revoked with `DELETE /auth/api-tokens/{id}`. Remote
clients can discover authorization metadata at
`/.well-known/oauth-protected-resource/mcp`. Configure
`MCP_AUTHORIZATION_SERVER_URL` and `MCP_OAUTH_INTROSPECTION_URL` when an
external OAuth 2.1 authorization server protects the deployment. Introspection
responses must include `active: true`, a Nodyra user id/email in `sub`, the
tenant in `org_id`, and space-delimited Nodyra permissions in `scope`.

### Calling external tools through the gateway

The workflow-authoring server at `/mcp` is separate from the managed external
tool gateway at `/mcp-gateway/{connection_id}`. To invoke that gateway with a
PAT, explicitly grant **`mcp_gateway:call`**; workflow edit/run scopes alone do not grant
external tool access. The owner must also configure an enabled MCP connection,
approve its discovered tool catalog, and set its tool policy. Credentials for
the downstream server stay with Nodyra.

See [the MCP gateway guide](mcp-gateway.md) for deployment, catalog review,
schema drift behavior, limits, and execution outcomes. Neither MCP endpoint
makes provider-reported success proof that a downstream write committed.

### What each token can do (RBAC)

Tool permissions map onto Nodyra's existing role table:

| Tool group | Permission | Minimum role |
|---|---|---|
| `list_*`, `get_*`, `validate_graph` | none | viewer (no auth needed if `AUTH_REQUIRED=false`) |
| `run_workflow`, per-workflow tools | `workflow:run` | editor; static run commands also need a bound approval |
| `create_workflow`, `set_workflow_graph`, `publish_workflow` | `workflow:write` | editor plus a bound approval |
| External gateway tool calls | `mcp_gateway:call` | editor, admin or owner; explicitly scoped on the PAT |

A transport-level auth failure is HTTP `401`. A *tool*-level failure (bad node
type, missing param, run error) comes back as a normal MCP result with
`isError: true` so the calling model can read the message and self-correct.

---

## 2. Connecting each client

> **stdio vs HTTP.** Claude Code, Cursor and a `curl` script speak HTTP MCP
> natively. Claude Desktop and Codex historically speak **stdio** only, so they
> reach a remote HTTP server through the tiny [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
> bridge (`npx mcp-remote <url> --header ...`). Both paths are shown below.

### Claude Code (native HTTP)

```bash
claude mcp add --transport http nodyra http://localhost:8000/mcp \
  --header "Authorization: Bearer <token>"
```

Then in a Claude Code session the `nodyra` tools appear automatically. Remove
with `claude mcp remove nodyra`.

### Claude Desktop

Two options:

**a) Custom connector (paid plans).** Settings → Connectors → *Add custom
connector* → paste the URL `https://your-host/mcp`. This requires a public
**HTTPS** URL (see [section 5](#5-hosting-nodyra-behind-ssl-so-you-can-test-oauth)) —
`localhost` http is rejected by the connectors UI.

**b) `mcp-remote` bridge (works on any plan, even against localhost).** Edit
`claude_desktop_config.json`
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "nodyra": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "http://localhost:8000/mcp",
        "--header", "Authorization: Bearer ${NODYRA_TOKEN}"
      ],
      "env": { "NODYRA_TOKEN": "eyJ...your token..." }
    }
  }
}
```

Restart Claude Desktop. The Nodyra tools appear under the 🔌 / tools menu.

### OpenAI Codex CLI

Edit `~/.codex/config.toml`:

```toml
[mcp_servers.nodyra]
command = "npx"
args = ["-y", "mcp-remote", "http://localhost:8000/mcp",
        "--header", "Authorization: Bearer eyJ...your token..."]
```

Run `codex` and the `nodyra` tools are available. (Newer Codex builds also
accept a `url = "..."` streamable-HTTP entry directly; the `mcp-remote` form
above works regardless of version.)

### Cursor / Continue / generic `mcp.json`

```json
{
  "mcpServers": {
    "nodyra": {
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer eyJ...your token..." }
    }
  }
}
```

### Raw script (no SDK)

See [`scripts/mcp_smoke.py`](../scripts/mcp_smoke.py), a stdlib-only client for
Nodyra's JSON HTTP endpoint. It initializes the protocol, creates a workflow,
validates and saves its graph, runs it, and publishes only after a successful
run. Run it from an **interactive terminal**:

```bash
NODYRA_MCP_TOKEN=ndpat_... \
NODYRA_MCP_URL=http://localhost:8000/mcp \
NODYRA_WEB_URL=http://localhost:5173 \
python scripts/mcp_smoke.py
```

Set `NODYRA_WEB_URL` to your actual browser origin (for example the Compose web
port or your public HTTPS URL). At each sensitive action the script prints its
review link. Approve it in Nodyra, then press Enter in the terminal to retry the
exact command. Pressing Enter alone does not grant approval; the server checks
the browser decision. The script never calls the review decision API and never
sets `approved_by_user`. Without an interactive terminal, it stops and prints
the review link and exact `tools/call` retry payload.

---

## 3. Tool catalogue

Static tools (always present):

| Tool | Purpose |
|---|---|
| `list_workflows` | List workflows (id, name, active, node count). Optional `search`, `limit`. |
| `get_workflow` | One workflow's metadata + current draft graph. |
| `list_node_types` | Discover the node palette. Filter by `category` / `search` to keep results small. |
| `get_node_type` | Full manifest for one node: params (type, choices, default, required) + input/output ports. **Call this before placing a node.** |
| `run_workflow` | Run + wait up to `wait_seconds` (0–300, default 60). Returns `{run_id, status, output}`, node errors on failure, or `status:"running"`. `parameters` seeds the trigger node; `use_draft` (default `true`). |
| `get_run` | Poll a still-running / finished run by `run_id`. |
| `create_workflow` | Create an empty workflow, returns its id. |
| `set_workflow_graph` | Replace the draft graph (`{nodes, edges}`). Validates shape + node types. |
| `validate_graph` | Validate a graph without saving. |
| `publish_workflow` | Publish the current draft as a new immutable version. |
| `update_workflow_settings` | Set environment, runner pool, concurrency, timeout, alerts, folder and MCP input schema. |
| `get_workflow_version` / `diff_workflow_versions` | Inspect and compare immutable versions or the current draft. |
| `create_schedule` / `update_schedule` / `toggle_schedule` / `delete_schedule` | Manage version-pinned, policy-validated schedules. |
| `get_node_run` | Detailed node logs, timings, debug data, output and errors. |
| `list_run_approvals` / `resolve_run_approval` | Operate waiting AI-tool approvals. |

**Dynamic per-workflow tools.** Any workflow with **MCP enabled** in the editor
toolbar additionally shows up as its *own* tool (running the published
version), named from `mcp_tool_name` or `workflow_<slug>_<id6>`. Give it an
`mcp_description` and an `mcp_parameters_schema` so the calling model knows how
to invoke it.

### Graph shape

```jsonc
{
  "nodes": [
    { "id": "trigger", "type": "manual_trigger",
      "params": { "data": { "value": 21 } }, "position": { "x": 0, "y": 0 } },
    { "id": "double", "type": "code",
      "params": { "code": "output = {'doubled': input['value'] * 2}" },
      "position": { "x": 320, "y": 0 } }
  ],
  "edges": [
    { "source": "trigger", "source_output": "main",
      "target": "double", "target_input": "input" }
  ]
}
```

`source_output` defaults to `"main"` and `target_input` to `"input"`, so both
can be omitted for simple chains. **Every runnable workflow needs a trigger
node** (e.g. `manual_trigger`, `chat_trigger`, `webhook_trigger`).

---

## 4. Building & testing workflows through MCP

The reliable loop an LLM should follow (and the one `mcp_smoke.py` demonstrates):

1. `list_node_types` (with `search`/`category`) → shortlist nodes.
2. `get_node_type` on each chosen node → exact param names, defaults, ports.
3. `create_workflow` → request review, retry with its approval ID, get `workflow_id`.
4. `set_workflow_graph` → save the draft (returns validation errors as readable
   text — fix and retry).
5. `run_workflow` (`use_draft: true`) → review this run, then check `status`/`output`/`errors`.
6. Review and retry `publish_workflow` once green. Each command uses its own grant.

### Example after browser approvals

After approving each requested action, a successful local smoke run has this
shape (approval prompts omitted):

```
initialize -> {'name': 'nodyra', 'version': '0.0.1'}
create_workflow -> 1e2def5b5cf34305bde7819d8959ea02
validate_graph -> {'valid': True, 'node_count': 2, 'edge_count': 1}
set_workflow_graph -> {... 'node_count': 2, 'edge_count': 1}
run_workflow -> {"run_id": "...", "status": "success", "output": {"doubled": 42}}
publish_workflow -> {... "version": 2}
```

> Tip: the `manual_trigger`'s `data` object is passed straight through as the
> downstream node's `input`. So `data: {"value": 21}` arrives as
> `input["value"]`, **not** `input["data"]["value"]`. Use `get_node_type` and a
> first `run_workflow` to confirm the shape — exactly how the model
> self-corrects.

### Prompts to try once a client is connected

> "List Nodyra node types in the *Triggers* and *AI* categories, then build a
> workflow with a chat trigger that summarises the incoming message with an LLM
> node, run it on the draft, and publish it when it succeeds."

> "Create a workflow that fetches `https://api.github.com/repos/python/cpython`
> with an `http_request` node and returns the `stargazers_count`. Run and show
> me the output."

Validation errors come back as `isError` text so the client can correct an
invalid graph. A `human_approval_required` response must instead be shown to the
user; the model cannot satisfy it by setting a boolean or modifying the command.

---

## 5. Hosting Nodyra behind SSL so you can test OAuth

OAuth providers (Google, Microsoft, Slack, GitHub) require an **HTTPS redirect
URI** — `http://localhost` is rejected by most of them. To run the real
provider OAuth flow you need Nodyra reachable over a public HTTPS origin.

### How Nodyra builds the redirect URI

The credential OAuth flow (`POST /credentials/oauth/start` →
`GET /credentials/oauth/callback`) computes the redirect URI as:

```
${OAUTH_REDIRECT_BASE_URL}/credentials/oauth/callback
```

falling back to the request's own origin when `OAUTH_REDIRECT_BASE_URL` is
unset. So the two things you must do are: (a) put Nodyra behind HTTPS, and
(b) point `OAUTH_REDIRECT_BASE_URL` at that HTTPS origin.

### Step 1 — get a public HTTPS URL

Pick whichever fits; all three terminate TLS for you:

**Cloudflare Tunnel (recommended for testing — free, no port-forwarding):**

```bash
# Quick throwaway URL:
cloudflared tunnel --url http://localhost:8000
# -> https://random-words.trycloudflare.com  (TLS already valid)

# Or a named tunnel bound to your own domain (stable URL):
cloudflared tunnel login
cloudflared tunnel create nodyra
cloudflared tunnel route dns nodyra nodyra.yourdomain.com
cloudflared tunnel run --url http://localhost:8000 nodyra
```

**ngrok (fastest one-off):**

```bash
ngrok http 8000        # -> https://xxxx.ngrok-free.app
```

**Caddy reverse proxy (real domain, production-grade, auto Let's Encrypt):**

```caddyfile
# Caddyfile — Caddy fetches & renews the cert automatically
nodyra.yourdomain.com {
    reverse_proxy localhost:8000
}
```

```bash
caddy run   # needs ports 80+443 reachable and DNS pointing at the host
```

### Step 2 — point Nodyra at the HTTPS origin

In `deploy/.env` (consumed by `deploy/docker-compose.yml`):

```dotenv
PUBLIC_API_URL=https://nodyra.yourdomain.com
OAUTH_REDIRECT_BASE_URL=https://nodyra.yourdomain.com

# Provider client credentials (only the ones you're testing):
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
SLACK_OAUTH_CLIENT_ID=...
SLACK_OAUTH_CLIENT_SECRET=...
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
MICROSOFT_OAUTH_CLIENT_ID=...
MICROSOFT_OAUTH_CLIENT_SECRET=...
```

If the SPA is served from a different origin, also widen
`CORS_ORIGINS` to include it. Then rebuild/restart so the API picks up the new
env (code is baked into the images):

```bash
docker compose -f deploy/docker-compose.yml up -d --build api worker
```

### Step 3 — register the callback in each provider console

Add this **exact** authorised redirect URI to every OAuth app you create:

```
https://nodyra.yourdomain.com/credentials/oauth/callback
```

- **Google** → APIs & Services → Credentials → OAuth client → *Authorised
  redirect URIs*.
- **Microsoft (Entra)** → App registrations → Authentication → *Redirect URIs*
  (platform: Web).
- **Slack** → your app → OAuth & Permissions → *Redirect URLs*.
- **GitHub** → Developer settings → OAuth Apps → *Authorization callback URL*.

### Step 4 — test it

In the Nodyra web UI: **Credentials → New → pick an OAuth type** (e.g. Google
Sheets, Slack, Outlook, GitHub) → *Connect*. A popup runs the provider flow and
posts back to `…/credentials/oauth/callback`, which stores the encrypted token.
Tokens auto-refresh from the stored `refresh_token` when they near expiry.

> The same public HTTPS origin also lets remote MCP clients (Claude Desktop
> custom connectors) reach `https://nodyra.yourdomain.com/mcp` — so steps 1–2
> double as MCP exposure. Keep `AUTH_REQUIRED=true` and a bearer token on that
> public endpoint.
