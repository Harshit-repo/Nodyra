# Connecting an LLM to Noodle over MCP

Noodle ships a built-in **MCP server** at `POST /mcp`. Any MCP-capable client —
Claude Code, Claude Desktop, OpenAI Codex, Cursor, Continue, or your own
script — can connect to it and then **discover node types, build workflow
graphs, run them, and publish versions** entirely through tool calls.

This guide covers:

1. [The endpoint & authentication](#1-endpoint--authentication)
2. [Connecting each client](#2-connecting-each-client) (Claude Code / Desktop / Codex / Cursor / generic)
3. [The tool catalogue](#3-tool-catalogue)
4. [Building & testing workflows through MCP](#4-building--testing-workflows-through-mcp)
5. [Hosting Noodle behind SSL so you can test OAuth](#5-hosting-noodle-behind-ssl-so-you-can-test-oauth)

---

## 1. Endpoint & authentication

| | |
|---|---|
| **URL** | `POST http://localhost:8000/mcp` (prod: `https://your-host/mcp`) |
| **Transport** | MCP 2025-11-25 Streamable HTTP, **stateless JSON** (exactly one JSON-RPC 2.0 message per POST). `GET`/`DELETE` return `405`. |
| **Auth** | `Authorization: Bearer <session token or org-scoped automation token>`. Required whenever `AUTH_REQUIRED=true`. |
| **Disable** | Set `MCP_SERVER_ENABLED=false`. |

### Getting a token

Noodle uses the same session token everywhere. Mint one by logging in:

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}'
# -> {"token":"eyJ...","user":{...}}
```

Use the `token` value as the bearer token. Session tokens carry an `exp`
expiry — when calls start returning `401 Invalid or expired token`, log in
again for a fresh one. When `AUTH_REQUIRED=false` (pure local dev) the header
may be omitted entirely.

For unattended agents, create a revocable token with `POST /auth/api-tokens`:

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
responses must include `active: true`, a Noodle user id/email in `sub`, the
tenant in `org_id`, and space-delimited Noodle permissions in `scope`.

### What each token can do (RBAC)

Tool permissions map onto Noodle's existing role table:

| Tool group | Permission | Minimum role |
|---|---|---|
| `list_*`, `get_*`, `validate_graph` | none | viewer (no auth needed if `AUTH_REQUIRED=false`) |
| `run_workflow`, per-workflow tools | `workflow:run` | editor |
| `create_workflow`, `set_workflow_graph`, `publish_workflow` | `workflow:write` | editor |

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
claude mcp add --transport http noodle http://localhost:8000/mcp \
  --header "Authorization: Bearer <token>"
```

Then in a Claude Code session the `noodle` tools appear automatically. Remove
with `claude mcp remove noodle`.

### Claude Desktop

Two options:

**a) Custom connector (paid plans).** Settings → Connectors → *Add custom
connector* → paste the URL `https://your-host/mcp`. This requires a public
**HTTPS** URL (see [section 5](#5-hosting-noodle-behind-ssl-so-you-can-test-oauth)) —
`localhost` http is rejected by the connectors UI.

**b) `mcp-remote` bridge (works on any plan, even against localhost).** Edit
`claude_desktop_config.json`
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "noodle": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "http://localhost:8000/mcp",
        "--header", "Authorization: Bearer ${NOODLE_TOKEN}"
      ],
      "env": { "NOODLE_TOKEN": "eyJ...your token..." }
    }
  }
}
```

Restart Claude Desktop. The Noodle tools appear under the 🔌 / tools menu.

### OpenAI Codex CLI

Edit `~/.codex/config.toml`:

```toml
[mcp_servers.noodle]
command = "npx"
args = ["-y", "mcp-remote", "http://localhost:8000/mcp",
        "--header", "Authorization: Bearer eyJ...your token..."]
```

Run `codex` and the `noodle` tools are available. (Newer Codex builds also
accept a `url = "..."` streamable-HTTP entry directly; the `mcp-remote` form
above works regardless of version.)

### Cursor / Continue / generic `mcp.json`

```json
{
  "mcpServers": {
    "noodle": {
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer eyJ...your token..." }
    }
  }
}
```

### Raw script (no SDK)

See [`scripts/mcp_smoke.py`](../scripts/mcp_smoke.py) — a ~80-line stdlib-only
client that runs the full `initialize → create → set_graph → validate → run →
publish` loop. Drive it with:

```bash
NOODLE_MCP_TOKEN=eyJ... python scripts/mcp_smoke.py
```

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
3. `create_workflow` → get `workflow_id`.
4. `set_workflow_graph` → save the draft (returns validation errors as readable
   text — fix and retry).
5. `run_workflow` (`use_draft: true`) → check `status`/`output`/`errors`.
6. `publish_workflow` once green.

### Verified example

Running `scripts/mcp_smoke.py` against the local stack produces:

```
initialize -> {'name': 'noodle', 'version': '0.0.1'}
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

> "List Noodle node types in the *Triggers* and *AI* categories, then build a
> workflow with a chat trigger that summarises the incoming message with an LLM
> node, run it on the draft, and publish it when it succeeds."

> "Create a workflow that fetches `https://api.github.com/repos/python/cpython`
> with an `http_request` node and returns the `stargazers_count`. Run and show
> me the output."

Because run errors and validation errors come back as `isError` text, the model
will iterate (wrong key, missing param, unknown node id) until the run is green.

---

## 5. Hosting Noodle behind SSL so you can test OAuth

OAuth providers (Google, Microsoft, Slack, GitHub) require an **HTTPS redirect
URI** — `http://localhost` is rejected by most of them. To run the real
provider OAuth flow you need Noodle reachable over a public HTTPS origin.

### How Noodle builds the redirect URI

The credential OAuth flow (`POST /credentials/oauth/start` →
`GET /credentials/oauth/callback`) computes the redirect URI as:

```
${OAUTH_REDIRECT_BASE_URL}/credentials/oauth/callback
```

falling back to the request's own origin when `OAUTH_REDIRECT_BASE_URL` is
unset. So the two things you must do are: (a) put Noodle behind HTTPS, and
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
cloudflared tunnel create noodle
cloudflared tunnel route dns noodle noodle.yourdomain.com
cloudflared tunnel run --url http://localhost:8000 noodle
```

**ngrok (fastest one-off):**

```bash
ngrok http 8000        # -> https://xxxx.ngrok-free.app
```

**Caddy reverse proxy (real domain, production-grade, auto Let's Encrypt):**

```caddyfile
# Caddyfile — Caddy fetches & renews the cert automatically
noodle.yourdomain.com {
    reverse_proxy localhost:8000
}
```

```bash
caddy run   # needs ports 80+443 reachable and DNS pointing at the host
```

### Step 2 — point Noodle at the HTTPS origin

In `deploy/.env` (consumed by `deploy/docker-compose.yml`):

```dotenv
PUBLIC_API_URL=https://noodle.yourdomain.com
OAUTH_REDIRECT_BASE_URL=https://noodle.yourdomain.com

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
https://noodle.yourdomain.com/credentials/oauth/callback
```

- **Google** → APIs & Services → Credentials → OAuth client → *Authorised
  redirect URIs*.
- **Microsoft (Entra)** → App registrations → Authentication → *Redirect URIs*
  (platform: Web).
- **Slack** → your app → OAuth & Permissions → *Redirect URLs*.
- **GitHub** → Developer settings → OAuth Apps → *Authorization callback URL*.

### Step 4 — test it

In the Noodle web UI: **Credentials → New → pick an OAuth type** (e.g. Google
Sheets, Slack, Outlook, GitHub) → *Connect*. A popup runs the provider flow and
posts back to `…/credentials/oauth/callback`, which stores the encrypted token.
Tokens auto-refresh from the stored `refresh_token` when they near expiry.

> The same public HTTPS origin also lets remote MCP clients (Claude Desktop
> custom connectors) reach `https://noodle.yourdomain.com/mcp` — so steps 1–2
> double as MCP exposure. Keep `AUTH_REQUIRED=true` and a bearer token on that
> public endpoint.
