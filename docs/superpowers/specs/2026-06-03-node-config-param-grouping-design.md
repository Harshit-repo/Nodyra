# Node-config param grouping + "Add option" chips — design

**Date:** 2026-06-03
**Branch:** `feat/ndv-param-grouping`
**Status:** Approved (brainstorm; direction B chosen + refined in the visual companion).

## Goal

Make every node's config panel (NDV) default to a clean, essentials-only view,
with optional parameters tucked behind **"Add option" chips** (n8n-style). This
is a single, generic, manifest-driven mechanism — not webhook-specific — so all
~100 nodes benefit at once. It replaces the bespoke webhook "Advanced options"
fold (`webhookAdvancedParam`) shipped earlier.

This is **sub-project A** of the broader "production pass for each node" request.
Sub-project B (per-node new options, pagination, idempotency, description/icon
polish) is a separate, prioritized backlog and explicitly out of scope here.

## What exists today

- **Param spec:** `packages/core/noodle/models.py::ParamSpec` (name, type,
  required, default, description, placeholder, choices, multiline, key_value,
  credential). Built from each param's `meta` dict in
  `packages/core/noodle/sdk.py` (two paths: decorated functions + module nodes).
- **Frontend:** `apps/web/src/types.ts::ParamSpec` mirrors it. `NDVPanels.tsx`
  `ParametersTab` renders every visible param; webhook uses `webhookHiddenParam`
  (dependent visibility), `webhookParamLabel` (friendly labels), and the
  bespoke `webhookAdvancedParam` + a `<details>` "Advanced options" fold.

## Non-goals (YAGNI)

- No per-group descriptions/icons/ordering metadata (Approach 3) — a flat
  `group` string per param is enough today.
- No change to dependent-field visibility (`webhookHiddenParam` stays).
- No backend behaviour change — grouping is presentation only.
- No per-node new options in this pass (that's sub-project B).

## Design (Approach 1 — manifest-driven `group`)

### Backend

- Add `group: str | None = None` to `ParamSpec`.
- In `sdk.py`, thread `group=meta.get("group")` where `ParamSpec(...)` is built
  (both the decorated-function path and the module-node path).
- A param with a non-empty `group` is **optional/grouped**; a param with no
  `group` is **core** (always shown). Within a group, dependent visibility still
  applies.

### Frontend

- Add `group?: string` to `ParamSpec` in `types.ts`.
- Replace the webhook-specific `webhookAdvancedParam` with a generic
  `paramGroup(spec): string | null` returning `spec.group ?? null`.
- `ParametersTab` rendering becomes generic for **all** nodes:
  1. Split visible specs (after `webhookHiddenParam`) into **core** (no group)
     and **grouped**.
  2. Render core fields.
  3. For each distinct group (in first-appearance order), it is **expanded** if
     the user added it this session **or** any param in it already holds a
     non-default value (so saved workflows never hide their config); otherwise
     render a `+ <Group>` chip.
  4. An expanded group is a titled block with a `×` that **clears its params'
     values** (back to default) and collapses it to a chip.
- Added/removed group state is local component state keyed by node id; "already
  has a non-default value" is derived from `params` + the manifest defaults.

### Webhook adopter

- Tag the webhook node's optional params with `group=` in `builtin.py`:
  `Authentication` (auth_type, auth_credentials, auth_jwt_header),
  `Security` (hmac_*, ip_allowlist, trust_proxy), `Idempotency` (dedup,
  dedup_key), `Body` (raw_body), `Response` (response_data, response_body,
  response_headers). Core stays: http_method, path, response_mode, response_code.
- Delete `webhookAdvancedParam` + the `<details>` fold; the generic chips replace
  them. `webhookHiddenParam`/`webhookParamLabel` remain.

### Roll-out (first wave)

- Tag `http_request` `timeout_seconds`/`max_retries` with `group="Options"`.
- Spot-tag a couple of other high-option nodes to prove generality; the rest are
  trivially adoptable later by adding `group=` (no UI change needed).

## Data flow

`@node(params={... "x": {"group": "Security"}})` → `sdk.py` builds
`ParamSpec(group="Security")` → manifest JSON → `types.ts ParamSpec.group` →
`paramGroup(spec)` → chip / expanded group in `ParametersTab`.

## Error handling / edge cases

- Unknown/empty group → treated as core (shown inline).
- A group whose every param is `webhookHiddenParam`-hidden renders no chip.
- Removing a group writes defaults for its params via the existing
  `updateParams` (atomic), so no half-set state.

## Testing

- **Backend:** unit test that a param with `group=` surfaces `group` on the
  built manifest (both SDK paths); existing node-registration tests stay green.
- **Frontend (vitest):** `paramGroup` classification; the core/group split;
  auto-expand when a grouped param already has a value; chip → expand → remove
  clears values. `tsc` + `vite build`.
- Migrate `webhookFields.test.ts` off `webhookAdvancedParam` to `paramGroup`.

## Sub-project B backlog (deferred, tracked)

HTTP Request pagination; per-node idempotency keys; richer error surfacing;
per-node new options; description/icon polish pass. Pulled in later, prioritized
separately, each with its own tests.
