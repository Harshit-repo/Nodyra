# Production-grade webhooks + node hardening — design

**Date:** 2026-06-02
**Status:** Approved (brainstorm) — pending spec review before planning.

## Goal

Take Noodle's already-substantial webhook support to production grade: add a
deployable **webhook ingress role**, expand the webhook node with n8n-style
**dropdown-driven conditional options** (auth, security, body, response shaping,
respond-node), give the NDV an **n8n-style "listen for test event" experience
with radiating waves and both URLs in the input column**, and run a **bounded
audit** of other built-in nodes implementing the high-value, low-risk wins.

## What already exists (do not rebuild)

- **Node** (`packages/nodes/noodle_nodes/builtin.py` `webhook_trigger`): params
  `http_method`, `path`, `response_mode` (`On Received` / `Last Node`),
  `response_code`, `auth_type` (`none`/`basic`/`header`/`query`),
  `auth_credentials` (credential type swaps with `auth_type`).
- **Routes** (`apps/api/app/routers/webhooks.py`): `POST /webhook/{path}`
  (production dispatch), `/webhook-test/{path}` (capture), `/webhook-test/{path}/last`
  (poll), `DELETE …/last` (clear). Capture buffer is TTL-bounded, capped, and
  header-redacted. Auth is enforced in `app/services/triggers.py::dispatch_webhook`.
- **Frontend** (`apps/web/src/editor/NodeDetails.tsx` `WebhookPanel`, rendered via
  `NDVPanels.tsx`): shows Test URL + Production URL with copy buttons and a
  "Listen for test event" button that clears, polls `/last`, and pipes the
  captured payload into the node Output panel. Listening currently shows a small
  spinner, **not** radiating waves. Conditional fields use
  `webhookHiddenParam` / `webhookParamLabel` / `webhookCredentialSpec`.
- **Config** (`apps/api/app/config.py`): `webhook_role: inline|ingress|disabled`
  exists but **nothing branches on it** (current default `inline` → changing to
  `ingress`). `runtime_warnings()` already exists.
- **Dedup infra**: `runs` dedup key (`0025_run_dedup_key`) is available to reuse.

## Non-goals (YAGNI)

- Bot / health-check filtering.
- Multi-tenant webhook isolation (covered by the remote-runner trust boundary).
- A visual "Respond" routing builder — the respond node + a mode dropdown suffice.

---

## Unit 1 — Webhook ingress role (backend)

**Behavior of `WEBHOOK_ROLE`:**

| Role | `/webhook/{path}` (production) | `/webhook-test/*` (editor) |
| --- | --- | --- |
| `ingress` (**default**) | served (validate → enqueue via durable queue) | served |
| `inline` | served (dispatch as today) | served |
| `disabled` | **404** | served |

- **Default is `ingress`** — the production-grade posture (validate fast, enqueue
  on the durable run queue, return promptly) is on by default. It works in a
  single all-in-one process too: `webhook_role` only governs the webhook route
  and is independent of `scheduler_role`, so defaulting to `ingress` does not
  disable the in-process scheduler/editor.
- Test/editor paths are **always** served regardless of role, so the builder UX
  works on any replica (per improvement-plan Task 8).
- `inline` keeps the simpler direct-dispatch path for minimal single-user setups.
  The lever that changes request handling is `disabled` (editor-only replicas
  reject production webhooks so a misrouted call fails loudly with 404 instead of
  silently matching nothing).
- **Production note:** `Settings.runtime_warnings()` flags `runtime_mode=production`
  + `webhook_role=inline` (advisory: webhook bursts share the API process; prefer
  `ingress`).

**Files:** `apps/api/app/routers/webhooks.py`, `apps/api/app/config.py`.
**Tests:** `apps/api/tests/test_triggers.py` — `disabled` → 404 on `/webhook/{path}`,
test paths still 200; `inline`/`ingress` accept.

---

## Unit 2 — Webhook node options (auth, security, body) with conditional fields

All new params are rendered as **dropdowns that reveal dependent fields when
selected**, extending the existing `webhookHiddenParam`/`webhookParamLabel`
mechanism (no new framework).

**Auth (`auth_type` gains options):**
- `bearer` — compare `Authorization: Bearer <token>` against a stored token
  credential. Reveals: credential picker (`http_bearer`).
- `jwt` — verify a JWT in a configurable header. Reveals: secret/public-key
  credential, `jwt_algorithm` (HS256/RS256), optional `jwt_header` (default
  `Authorization`). Verifies signature + `exp`.

**Security (independent of `auth_type`):**
- `hmac_verification` (dropdown `off`/`on`). When `on`, reveals: `hmac_header`
  (e.g. `X-Hub-Signature-256`), `hmac_secret` (credential), `hmac_algorithm`
  (sha256/sha1), `hmac_prefix` (e.g. `sha256=`). Verifies HMAC of the **raw**
  body → 401 on mismatch. (GitHub/Stripe/Slack style.)
- `ip_allowlist` (text, comma/newline CIDRs). Non-empty → reject callers outside
  the list with **403**. Honors `X-Forwarded-For` only when a trusted-proxy flag
  is set (default: use socket peer).

**Idempotency:**
- `dedup` (dropdown `off`/`on`). When `on`, reveals `dedup_key` (expression,
  e.g. `{{ $json.headers['x-delivery-id'] }}`). A repeat key within the run
  dedup window is **acknowledged 200 without starting a run** (reuses
  `0025_run_dedup_key`).

**Response shaping (immediate `On Received` mode):**
- `response_data` (dropdown: `First Entry JSON` / `All Entries` / `No Body` /
  `Custom`). `Custom` reveals `response_body` (expression) + `response_headers`
  (key/value map).

**Body capture:**
- `raw_body` (dropdown `off`/`on`) — when `on`, the parsed-JSON convenience is
  kept but the raw bytes are also exposed; `multipart/form-data` and binary
  uploads are written as **artifacts** and passed as artifact refs (so large
  uploads never bloat the DB), consistent with the existing artifacts subsystem.

**Enforcement order in `dispatch_webhook`:** ip_allowlist → auth (none/basic/
header/query/bearer/jwt) → hmac → dedup → dispatch. Each failure has a distinct
status (403 / 401 / 401 / 200-ack).

**Files:** `packages/nodes/noodle_nodes/builtin.py` (params + signature),
`apps/api/app/services/triggers.py` (enforcement + body/dedup),
`apps/api/app/routers/webhooks.py` (raw body / artifacts),
frontend `NodeDetails.tsx`/`NDVPanels.tsx` (conditional fields).
**Coordination:** registration may touch `packages/nodes/noodle_nodes/__init__.py`,
which ChatGPT is editing for AI nodes — rebase/merge carefully, don't clobber.
**Tests:** `test_triggers.py` per auth/security/dedup path; node-registration test.

---

## Unit 3 — Respond-to-Webhook + synchronous response (hybrid A+B)

**`response_mode` gains `Respond Node`** (alongside `On Received`, `Last Node`).

**New node `respond_to_webhook`** (Core/Triggers category): params `response_code`,
`response_body` (expression), `response_headers` (map), `content_type`. When the
engine executes it, it records the intended response for the run.

**Where the response lives:** add a nullable JSON column `runs.webhook_response`
(additive migration, forward-only-safe). `respond_to_webhook` writes
`{status, headers, body}` there; this makes the response readable by a waiting
handler **on any replica** (DB is shared) — the reason DB-poll is required.

**Waiting (hybrid):** for `Last Node` / `Respond Node`, the webhook handler:
1. Dispatches the run, gets `run_id`.
2. Waits for terminal state via **whichever resolves first**:
   - **(B, fast)** subscribe to the run broker for `run_finished`;
   - **(A, safety net)** poll the `Run` record in the DB every ~250ms.
   Bounded by new config `webhook_response_timeout_seconds` (default 30).
3. On completion:
   - `Last Node` → body = last node's output, status = node `response_code`.
   - `Respond Node` → read `runs.webhook_response`; if none recorded, fall back
     to a default 200 + last-node output.
4. On **timeout** → `504` (run keeps executing in the background).
5. On **run error** → `500` with a safe error summary (no secret leakage; reuse
   existing redaction).

B gives low latency when the broker is live (now reliable after the broker
transport fix); A guarantees correctness across replicas / `ingress` mode and if
an event is missed. Sub-workflow bypass and existing dispatch semantics are
unchanged.

**Files:** `apps/api/app/models.py` + new migration, `packages/nodes` (respond
node), `apps/api/app/services/triggers.py`/`runner.py` (record + await),
`apps/api/app/routers/webhooks.py`, `apps/api/app/config.py`.
**Tests:** `test_triggers.py` — Last Node returns last output; Respond Node
returns the recorded response; timeout → 504; error → 500. Run on the Postgres
lane too (response round-trips through the DB column).

---

## Unit 4 — NDV "listen for test event" with waves (frontend)

- Move `WebhookPanel` (Test URL + Production URL + Listen) into the **input
  column** of the NDV for trigger nodes (currently a panel section).
- Replace the spinner with an **n8n-style radiating-waves** animation: concentric
  rings pulsing outward from a center dot while listening; CSS `@keyframes` in
  `editor.css` (`.webhook-waves`), respecting `prefers-reduced-motion`.
- States: idle (button "Listen for test event") → listening (waves + "Listening
  for a test event…" + Stop) → received (waves stop, captured payload renders in
  the Output column; existing `setNodeOutput`). Keep the current `/last` polling.
- Render the new Unit-2 conditional fields in the params column via the existing
  dynamic-field path.

```
┌─ INPUT ──────────────┐     ┌─ PARAMS ───────────┐     ┌─ OUTPUT ─────────┐
│ Test URL    [copy]   │     │ Method   [POST ▾]  │     │ (captured event  │
│ Prod URL    [copy]   │     │ Auth     [JWT  ▾]  │     │  payload renders  │
│                      │     │  └ secret  […]     │     │  here on receive)│
│     (( ((•)) ))      │     │ HMAC     [on   ▾]  │     │                  │
│  Listening for a     │     │  └ header  […]     │     │                  │
│   test event…        │     │  └ secret  […]     │     │                  │
│       [ Stop ]       │     │ Respond  [Node ▾]  │     │                  │
└──────────────────────┘     └────────────────────┘     └──────────────────┘
```

**Files:** `apps/web/src/editor/NodeDetails.tsx`, `NDVPanels.tsx`,
`editor.css`. **Tests:** vitest for listen state machine; `tsc` + `vite build`.

---

## Unit 5 — Other-nodes audit + bounded quick wins

Audit built-in nodes (`packages/nodes`) and key integrations for production gaps:
timeouts, retries/backoff, pagination, error handling, auth/SSRF, large-payload /
artifact handling, idempotency. Deliver a **prioritized findings list**, then
implement the top **≤6 low-risk wins** in this pass; larger items become a
tracked follow-up list (no scope balloon). Likely candidates (to confirm post-
audit): HTTP Request timeout/retry/pagination, Schedule trigger tz correctness,
SMTP/Slack/HTTP error surfacing, DB-node parameterization/SSRF. Each win ships
with a test.

**Files:** `packages/nodes/**` (excluding files ChatGPT is actively editing:
`integrations.py`, `ai_extra.py`, `llm.py`, `__init__.py` overlaps — coordinate).

---

## Cross-cutting

- **Config additions:** `webhook_response_timeout_seconds` (default 30); reuse
  `webhook_role`. Surface both in `/ops/runtime-mode` warnings where relevant.
- **Migrations:** `runs.webhook_response` nullable JSON — additive, forward-only.
- **Security:** HMAC verifies the raw body before parsing; auth/secret values
  never logged or stored in the capture buffer (extend existing redaction);
  IP/JWT/HMAC failures return distinct, non-leaky statuses.
- **Testing:** all backend changes run on **both** the SQLite default lane and
  the new Postgres CI lane (Unit 3's DB round-trip especially). Frontend: vitest
  + `tsc` + `vite build`.
- **Concurrency with ChatGPT's AI-node work:** do not edit
  `app/services/ai_builder.py`, `credential_tests.py`, `packages/nodes/llm.py`,
  `ai_extra.py`, `integrations.py`; coordinate on `packages/nodes/__init__.py`
  (shared node registration). Land this work so it merges cleanly alongside.

## Suggested build order

1. Unit 1 (ingress role) — small, isolated, unblocks the "production" claim.
2. Unit 2 (node options) — backend enforcement + conditional fields.
3. Unit 3 (respond node + sync response) — riskiest; behind tests on both DBs.
4. Unit 4 (NDV waves UX) — frontend, depends on Unit 2 params.
5. Unit 5 (node audit) — independent; can run in parallel.
