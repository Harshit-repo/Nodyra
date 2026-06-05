# Design: Polling & Webhook Triggers + File Read Nodes

**Date:** 2026-06-06  
**Status:** Draft — awaiting implementation plan

---

## Goals

1. Add webhook-based provider triggers for Slack, Stripe, Microsoft Graph, Google Drive, Google Sheets, and Airtable using the existing `ProviderTriggerSpec` infrastructure.
2. Extend `ProviderTriggerSpec` with a polling hook so providers without webhook support (Notion, RSS, IMAP) can participate in the same trigger lifecycle.
3. Add a File Upload Trigger that lets users upload a file from their browser to start a workflow.
4. Add generic file-reading nodes (Text, CSV, JSON, XML) that accept either a server-side path or a browser-uploaded artifact, with an optional `output_as_dataset` toggle that emits a DatasetRef instead of raw content.

---

## Non-Goals

- No new UI for subscription health monitoring (uses existing workflow status surface).
- No changes to the schedule_trigger, webhook_trigger, or other built-in triggers.
- No support for writing files back to a local filesystem (only cloud storage already covers that).
- No new provider families beyond those listed (HubSpot, Shopify, etc. are not in scope).

---

## Architecture

### 1. Polling Hook Extension to `ProviderTriggerSpec`

Add two optional fields to the frozen dataclass:

```python
@dataclass(frozen=True)
class ProviderTriggerSpec:
    ...
    # Existing fields unchanged
    activate: ProviderTriggerActivate | None = None
    deactivate: ProviderTriggerDeactivate | None = None
    handle_event: ProviderTriggerHandleEvent | None = None

    # NEW
    poll: ProviderTriggerPoll | None = None
    poll_interval_seconds: int = 300   # default 5 min; configurable per spec
```

**`ProviderTriggerPoll` signature:**

```python
class ProviderTriggerPollContext(TypedDict):
    params: dict[str, Any]          # resolved node params / credentials
    cursor: dict[str, Any]          # last-persisted cursor (empty on first run)

ProviderTriggerPollResult = TypedDict("ProviderTriggerPollResult", {
    "events": list[dict[str, Any]], # zero or more events to fire; each becomes a run
    "cursor": dict[str, Any],       # new cursor to persist after this poll
})

ProviderTriggerPoll = Callable[[ProviderTriggerPollContext], ProviderTriggerPollResult]
```

**Lifecycle for polling subscriptions:**

- `activate` is optional. Polling specs with no `activate` still get a `ProviderTriggerSubscription` row created (status = active) on workflow activation. The `external_id` is empty.
- The cursor is stored in `subscription.config["poll_cursor"]`.
- `deactivate` is optional. If absent, deactivation just marks the row deleted.
- `handle_event` must be `None` when `poll` is set — a spec cannot be both push and poll.

**Scheduler integration:**

The existing `scheduler_loop` in `apps/api/app/services/triggers.py` runs a `_tick()` every 30 s. Add a second pass in the same tick:

```
for each ProviderTriggerSubscription where status = "active"
    spec = registry.get_provider_trigger(subscription.node_type)
    if spec.poll is None: continue
    if now < subscription.config["next_poll_at"]: continue
    result = spec.poll({params, cursor})
    for event in result["events"]:
        start_run(workflow_id, trigger_node_id, payload=event)
    subscription.config["poll_cursor"] = result["cursor"]
    subscription.config["next_poll_at"] = now + spec.poll_interval_seconds
```

Errors in `poll` are stored in `subscription.error` and surfaced in the workflow UI, same as activation errors.

### 2. File Upload Infrastructure

**New API endpoint:** `POST /artifacts/upload`

- Auth: session-authenticated (user must be logged in).
- Body: multipart/form-data with a single `file` field.
- Query params: `workflow_id` (optional — links the artifact to a workflow context).
- Response: `{ "artifact_id": "...", "name": "...", "content_type": "...", "size_bytes": ... }`.
- Limits: respect `settings.max_upload_size_bytes` (default 50 MB).
- Storage: same `ArtifactStore` backend used by runner-uploaded artifacts. Artifacts from user uploads get `kind = "upload"`.

**New param widget type: `file_upload`**

Add `"file_upload"` as a valid `widget` value in `OperationParamSpec`. In the node inspector, this renders a file-picker button. On selection the browser posts the file to `POST /artifacts/upload` and stores the returned `artifact_id` as the parameter value. The inspector shows the filename and size once uploaded, with a clear/replace button.

A node param typed `file_upload` always has type `"string"` (stores the artifact_id). The node executor resolves the artifact_id to a readable stream via `ArtifactStore.open()`.

---

## Triggers Catalog

### Group 1 — Webhook Triggers (existing `ProviderTriggerSpec`, push-based)

All of these use the existing `activate` / `deactivate` / `handle_event` pattern. No new infrastructure.

#### Slack Message Event Trigger (`slack_event_trigger_v2`)

- **Activation:** Registers a Slack Events API app subscription. Requires bot token + signing secret credential.
- **Challenge:** Slack sends a `url_verification` event on activation; `handle_event` must respond with `{"challenge": "..."}`.
- **Signature:** HMAC-SHA256 over `v0:{timestamp}:{raw_body}` using signing secret; reject if `X-Slack-Signature` doesn't match.
- **Dedup:** `X-Slack-Retry-Num` header — drop retries for already-processed events using `event_id` dedupe key.
- **Params:** credentials (bot token + signing secret), event types (message, reaction_added, member_joined_channel, file_shared — multi-select).
- **Payload fields:** `team_id`, `event_type`, `event`, `authorizations`.
- **Filter:** optional channel ID filter (only fire for events from a specific channel).

#### Stripe Event Trigger (`stripe_event_trigger_v2`)

- **Activation:** No provider-side registration needed; user configures a Stripe webhook endpoint manually pointing to the Noodle callback URL, OR we call `POST /v1/webhook_endpoints` via Stripe API to auto-register.
- **Signature:** `Stripe-Signature` header; verify via `t=` timestamp + HMAC-SHA256 `whsec_` key.
- **Params:** credentials (Stripe API key + webhook signing secret), event types (multi-select: payment_intent.succeeded, invoice.paid, customer.created, subscription.updated, etc.).
- **Dedup:** Stripe event `id` field.
- **Payload fields:** `id`, `type`, `livemode`, `data.object` (the Stripe resource), `created`.

#### Microsoft Graph Change Notification Trigger (`microsoft_graph_notification_trigger_v2`)

- **Activation:** `POST /v1.0/subscriptions` to create a Graph subscription. Resource options: `/me/messages`, `/me/events`, `/me/mailFolders('Inbox')/messages`.
- **Challenge:** Graph sends a `validationToken` query param on activation; respond with it as plain text with status 200.
- **Expiry:** Graph subscriptions expire (3–4320 minutes depending on resource). Store `expirationDateTime` in subscription config. The scheduler renewal pass calls `PATCH /v1.0/subscriptions/{id}` before expiry.
- **Params:** credentials (Microsoft Graph OAuth), resource type (messages / calendar events), folder filter (optional).
- **Dedup:** `clientState` round-trip + notification `subscriptionId` + `changeType` + resource `id`.
- **Payload fields:** `changeType`, `resource`, `resourceData`, `subscriptionId`.

#### Google Drive File Trigger (`google_drive_file_trigger_v2`)

- **Activation:** `POST https://www.googleapis.com/drive/v3/files/{fileId}/watch` (or `changes.watch` for folder-level watching). Returns a channel `id` and `expiration`.
- **Expiry:** Up to 7 days. Renewal: re-issue the watch before expiry; store new channel id and expiration.
- **Stop:** `POST https://www.googleapis.com/drive/v3/channels/stop` with channel id on deactivation.
- **Params:** credentials (Google OAuth), watch mode (single file / folder), file/folder ID, event filter (created / modified / deleted — multi-select). Note: Drive watch channels notify for all changes; Noodle filters by `X-Goog-Resource-State` header client-side, not at the provider.
- **Signature:** No HMAC; validate the `X-Goog-Channel-ID` header matches stored channel id.
- **Dedup:** `X-Goog-Resource-State` + `X-Goog-Message-Number`.
- **Payload fields:** Google sends minimal notification headers only (no body); the trigger should fetch the changed file's metadata as the payload.

#### Google Sheets Change Trigger (`google_sheets_change_trigger_v2`)

- Same mechanism as Google Drive (watch channels via Drive API, applied to the spreadsheet file).
- **Params:** credentials (Google OAuth), spreadsheet ID.
- **Payload:** Minimal header notification; trigger fetches the sheet's last-modified metadata to include in the payload.
- **Note:** This fires on ANY edit to the spreadsheet — not scoped to a specific sheet or range. Use the polling "new row" trigger for row-level detection.

#### Airtable Record Changed Trigger (`airtable_record_trigger_v2`)

- **Activation:** `POST https://api.airtable.com/v0/bases/{baseId}/webhooks` with notification URL, data types (tableData), and change types (created/updated/deleted).
- **Expiry:** Webhooks expire after 7 days of inactivity. Renewal: `POST .../webhooks/{id}/refresh`.
- **Dedup:** Airtable sends a cursor in each notification; use it to fetch the actual changed records via `GET .../webhooks/{id}/payloads?cursor={cursor}`.
- **Params:** credentials (Airtable token), base ID, table ID, change types (created / updated / deleted — multi-select).
- **Payload fields:** `timestamp`, `baseId`, `webhookId`, `actionMetadata`, `createdFieldsById`, `changedFieldsById`, `destroyedFieldIds`, `changedRecordsById`, `createdRecordsById`, `destroyedRecordIds`.

### Group 2 — Polling Triggers (require poll-hook extension)

All of these set `poll` on the spec; `activate`, `deactivate`, and `handle_event` are all `None`.

#### Notion New Database Page Trigger (`notion_database_trigger_v2`)

- **Poll:** `POST /v1/databases/{database_id}/query` with `filter.timestamp = created_time`, `filter.created_time.after = <cursor_timestamp>`, `sorts = [{timestamp: created_time, direction: ascending}]`.
- **Cursor:** `{ "last_created_time": ISO8601 }` — updated to the latest `created_time` in results.
- **Events:** One event per new page found. Each event includes the full Notion page object.
- **Poll interval:** 5 minutes (configurable in params: 1 / 5 / 15 / 30 / 60 min).
- **Params:** credentials (Notion integration token), database ID, poll interval.
- **Dedup:** page `id` — skip any page id already fired (store last N ids in cursor to handle clock skew).

#### Google Sheets New Row Trigger (`google_sheets_new_row_trigger_v2`)

- **Poll:** `GET https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{range}` — compare row count against cursor.
- **Cursor:** `{ "last_row_index": int }` — the last row index seen.
- **Events:** One event per new row, containing a dict of `{column_header: cell_value}`.
- **Poll interval:** 1 / 5 / 15 / 30 / 60 min (default 5 min).
- **Params:** credentials (Google OAuth), spreadsheet ID, sheet name, header row number (default 1), poll interval.
- **First run:** establish cursor from current row count, fire no events (avoids flooding on first activation).

#### Airtable New Record Trigger (`airtable_new_record_trigger_v2`)

- **Poll:** `GET /v0/{baseId}/{tableId}?sort[0][field]=Created Time&sort[0][direction]=asc&filterByFormula=IS_AFTER({Created Time}, "{cursor_time}")`.
- **Cursor:** `{ "last_created_time": ISO8601 }`.
- **Events:** One event per new record, containing the full Airtable record object.
- **Poll interval:** 1 / 5 / 15 / 30 min (default 5 min).
- **Params:** credentials (Airtable token), base ID, table ID, poll interval.
- **Note:** This is the polling fallback. Prefer `airtable_record_trigger_v2` (webhook) where the webhook setup is feasible.

#### RSS / Atom Feed Trigger (`rss_feed_trigger`)

- **No credentials.** Fetches a public URL.
- **Poll:** HTTP GET the feed URL; parse RSS 2.0 or Atom 1.0.
- **Cursor:** `{ "seen_guids": [last 500 guids] }` — ring buffer to detect new items without a reliable pubDate.
- **Events:** One event per new item: `{ title, link, description, pub_date, guid, feed_url }`.
- **Poll interval:** 5 / 15 / 30 / 60 min (default 15 min).
- **Params:** feed URL, poll interval, max items per poll (default 10 — avoids flooding on first run).
- **First run:** populate seen_guids from current feed items; fire no events.

#### IMAP New Email Trigger (`imap_email_trigger`)

- **Poll:** IMAP SEARCH UID > last_uid in mailbox; fetch headers of matching messages.
- **Cursor:** `{ "last_uid": int }`.
- **Events:** One event per new message: `{ subject, from, to, date, message_id, snippet }`.
- **Poll interval:** 1 / 5 / 15 min (default 5 min).
- **Params:** credentials (IMAP — host, port, username, password, use_ssl), mailbox (default INBOX), subject filter (optional substring), from filter (optional), poll interval.
- **Credential type:** new `imap` credential type with fields: host, port, username, password, use_ssl.

### Group 3 — File Upload Trigger

#### File Upload Trigger (`file_upload_trigger`)

- **Category:** Triggers.
- **Role:** Built-in trigger (not a provider trigger spec).
- **Behavior:** In the workflow run panel, shows a file-picker instead of the standard "Run" button. User selects a file; browser POSTs to `POST /artifacts/upload`; the workflow starts with the artifact as the trigger payload.
- **Params:** accepted MIME types (optional — comma-separated; default: all), max file size (MB, default 50).
- **Payload fields:** `artifact_id`, `filename`, `content_type`, `size_bytes`.
- **Automation context:** When triggered from an API or schedule (non-interactive), this trigger is a no-op / blocks. It is exclusively for human-in-the-loop runs.

---

## File Reading Nodes

All four nodes share a common dual-source pattern:

```
Source A: server path (string param — direct filesystem read)
Source B: uploaded file (file_upload widget param — artifact_id)
```

If both are provided, the uploaded file (Source B) takes precedence. If neither is provided, the node fails with a clear error.

### `read_text_file`

- **Inputs:** none (data source nodes).
- **Params:**
  - `path` (string, optional) — server-side file path.
  - `file` (file_upload widget, optional) — browser-uploaded file artifact.
  - `encoding` (string, default `utf-8`).
- **Output:** `{ text: string, filename: string, size_bytes: int }`.
- **No `output_as_dataset` toggle** — plain text cannot be meaningfully converted to tabular data.

### `read_csv_file`

- **Params:**
  - `path` (string, optional).
  - `file` (file_upload widget, optional).
  - `delimiter` (string, default `,`).
  - `has_header` (boolean, default true).
  - `output_as_dataset` (boolean, default true) — when true, output is a DatasetRef (Parquet-backed); when false, output is `{ rows: list[dict], filename: string }` raw JSON.
- **Output (dataset mode):** DatasetRef.
- **Output (raw mode):** `{ rows: list[dict], filename: string, row_count: int }`.
- **Implementation:** reads file content → pipes through existing `csv_parse` logic when in dataset mode.

### `read_json_file`

- **Params:**
  - `path` (string, optional).
  - `file` (file_upload widget, optional).
  - `output_as_dataset` (boolean, default false) — when the JSON root is an array of objects, converts to DatasetRef; errors if root is a scalar or non-uniform array.
- **Output (dataset mode):** DatasetRef (only valid if JSON is array-of-objects).
- **Output (raw mode):** `{ data: any, filename: string }`.

### `read_xml_file`

- **Params:**
  - `path` (string, optional).
  - `file` (file_upload widget, optional).
  - `row_xpath` (string, optional) — XPath expression to select repeated elements as rows (e.g. `//item`). Required for dataset mode.
  - `output_as_dataset` (boolean, default false) — when true, uses `row_xpath` to extract rows into a DatasetRef.
- **Output (dataset mode):** DatasetRef.
- **Output (raw mode):** `{ data: dict, filename: string }` (XML parsed to dict via `xmltodict`).
- **Requirements:** `xmltodict` — declared in the node spec's `requirements` tuple; editor prompts install if absent.

---

## Phasing

| Phase | Deliverables | Rationale |
|---|---|---|
| **P0** | Poll-hook extension (`ProviderTriggerSpec.poll` + `poll_interval_seconds`); scheduler polling pass; `POST /artifacts/upload` endpoint; `file_upload` widget type | Unblocks all polling triggers and file upload nodes |
| **P1** | Slack event trigger; Stripe event trigger | High-demand, simple signing, no expiry/renewal complexity |
| **P2** | Google Sheets new-row trigger (polling); Notion new-page trigger (polling); RSS feed trigger (polling) | Core "data watching" use cases; depend only on P0 |
| **P3** | `read_text_file`, `read_csv_file`, `read_json_file`, `read_xml_file` nodes | File reading nodes; depend on P0 for upload widget |
| **P4** | Google Drive file trigger (webhook + expiry renewal); Google Sheets change trigger (webhook + expiry renewal); Airtable record trigger (webhook + expiry renewal); Microsoft Graph notification trigger (webhook + expiry renewal) | Expiring subscription providers — renewal logic adds complexity; batch together |
| **P5** | IMAP email trigger; Airtable new-record polling trigger; File Upload Trigger built-in | Lower priority; IMAP needs new credential type |

---

## Key Invariants

- A `ProviderTriggerSpec` may have `poll` OR `handle_event`, never both.
- Polling specs with no `activate` still get a subscription row; cursor starts as `{}`.
- All polling triggers must set `poll_interval_seconds` ≥ 60 to avoid hammering provider APIs.
- `output_as_dataset = true` on JSON/XML nodes must fail clearly if the data shape doesn't support tabular conversion (not silently emit an empty DatasetRef).
- File upload artifacts are subject to the same retention policies as run artifacts.
- The `file_upload_trigger` must be clearly documented as interactive-only; attempting to use it in a deployed/scheduled workflow should surface a clear error, not silently no-op.
- All expiring webhook subscriptions (Drive, Sheets, Graph, Airtable) must store `expires_at` in the subscription row and be handled by a renewal pass before the implementation is marked complete.
