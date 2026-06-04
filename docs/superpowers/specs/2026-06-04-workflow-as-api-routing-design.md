# Workflow-as-API: REST routing — design

**Date:** 2026-06-04
**Status:** Phase 1 shipped (path-param routing on the webhook node). Phase 2
**backend** shipped (API Endpoint node + route-table dispatch); Phase 2 **editor
UI** (routes-table + dynamic output handles in the NDV) is the next sub-phase.

## Goal

Let users serve data through a real API built from a workflow — "webhooks, but
it responds with data like an API." Investigation showed the *synchronous
respond-with-data* half already exists (webhook `response_mode` =
`Last Node`/`Respond Node` + the `respond_to_webhook` node). The genuine gap was
**resource-oriented routing**: `triggers.py` matched the webhook path by exact
string compare and the route `/webhook/{path}` captured a single segment, so
`/customers/{id}` was impossible.

## Scope decision (phased)

- **Phase 1 (this change):** add path-param routing to the *existing* webhook
  node — the smallest change that closes the real capability gap.
- **Phase 2 (deferred):** a dedicated multi-output "API Endpoint" node with a
  per-route table (method + sub-path → output branch), for the
  "one workflow = a CRUD API" authoring UX. Built on Phase 1's routing engine.

## Phase 1 — what shipped

1. **Path templates** — the webhook `path` param accepts `{name}` segments:
   `products/{id}`, `customers/{id}/orders`. New pure matcher
   `triggers._match_webhook_path(template, path) -> dict | None`: equal segment
   count, literals match exactly, `{name}` captures one segment. No-placeholder
   templates match exactly (back-compat).
2. **Captured params** — exposed on the trigger output as `$json.params`
   (e.g. `{{ $json.params.id }}`), injected per matched node in
   `dispatch_webhook` (each template captures different segments).
3. **Method-aware matching** — when a node pins `http_method`, only the matching
   request method fires it (so `GET /items/{id}` and `DELETE /items/{id}` are
   independent workflows). A node with no method set matches any method
   (back-compat for older graphs). *Behavior change:* previously any method on a
   matching path fired; acceptable pre-release.
4. **Multi-segment route** — `/webhook/{path}` → `/webhook/{path:path}` so
   `/webhook/customers/42/orders` reaches the dispatcher.

### Multi-match semantics (unchanged)
Every matching active workflow fires (fan-out). No literal-over-param precedence
in Phase 1; relevant only to the Phase 2 single-endpoint node.

### Deliberately deferred to Phase 2
- Editor "Listen for test event" capture for multi-segment templated paths
  (the `/webhook-test/*` route stays single-segment; the NDV gets reworked with
  the API Endpoint node anyway). No frontend changes in Phase 1.
- OpenAPI/docs generation and consumer API-key/rate-limit management (explicitly
  out of scope per brainstorming).

## Files
- `apps/api/app/services/triggers.py` — `_match_webhook_path`/`_path_segments`,
  matcher + method check + param injection in `dispatch_webhook`.
- `apps/api/app/routers/webhooks.py` — `/webhook/{path:path}`.
- `packages/nodes/noodle_nodes/builtin.py` — `path` param docs (template syntax).

## Tests (SQLite lane; no schema change / migration)
- `apps/api/tests/test_triggers.py`: matcher unit tests (exact, single/nested
  param, segment-count mismatch, slash trimming); routing integration
  (`/webhook/products/42` → `$json.params.id == "42"`); method gating
  (DELETE does not fire a GET resource). Full `test_triggers.py` green (39).

---

## Phase 2 backend — what shipped (2026-06-04)

The dedicated **API Endpoint node**: one workflow declares a base path + a route
table; each route is its own output branch. Built on Phase 1's routing engine.
Response model = **Option A** (last node's output; drop in a `Respond to Webhook`
node for custom status/headers), reusing the webhook `response_mode`.

- **`triggers._match_api_route(base_path, routes, method, path)`** — pure router:
  the most-specific matching route wins (specificity = literal segment count),
  ties broken by row order, method filtered per row; returns `(output, params)`
  or `None`. A node is a router → exactly one branch fires.
- **`dispatch_webhook`** now handles `node_type == "api_endpoint"` beside
  `webhook_trigger`: resolves the matched route's output port + params, seeds the
  run with `{node_id: {output: payload}}` so only that branch runs, and reuses the
  existing auth + `response_mode` (Last Node / Respond Node) pipeline.
- **`api_endpoint` node** (`packages/nodes/noodle_nodes/builtin.py`, Triggers):
  params `base_path`, `routes` (list of `{method, path, output}`), `response_mode`,
  `response_code`, `auth_type`/`auth_jwt_header`/`auth_credentials`;
  `outputs=["main"]` with per-route ports via `outputs_override`.
- **`graph_utils.TRIGGER_TYPES`** gains `api_endpoint`; `test_builtin_nodes`
  trigger set updated.

**Tests:** `test_api_endpoint_*` (item/collection routing + params, Last-Node
response, POST→create branch, basic-auth reject) + `_match_api_route` unit tests.
Full `test_triggers.py` + `test_trigger_gated_runs` = **61 passed**; node package
**90 passed**. No migration; no frontend changes.

### Deferred to the editor sub-phase (next)
Routes-table param editor, deriving `outputs_override` from `routes` so the canvas
renders one handle per route, NDV wiring, reusing the webhook NDV auth fields.
Also still deferred (identical to webhook when added): HMAC / IP / dedup /
raw-body, and `405` on path-match-but-method-miss. Until the editor lands the node
is fully usable via graph JSON / the API.
