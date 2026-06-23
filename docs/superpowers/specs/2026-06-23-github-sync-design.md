# GitHub Sync for Workflows — Design Spec

**Date:** 2026-06-23  
**Status:** Approved  
**Scope:** Bidirectional sync between Noodle workflows and a GitHub repository, using Python code-first format, async queue jobs, and conflict detection.

---

## 1. Overview

Workflows in Noodle can be connected to a GitHub repository at the org level. Once connected:

- Every draft save pushes a `.module.py` file to a per-workflow `draft/<slug>` branch
- Every publish pushes to `main` with a versioned commit message
- GitHub pushes (merged PRs, direct commits) webhook back into Noodle and update the draft
- Conflicts (both sides changed independently) are flagged and require manual resolution via a diff UI

File format is the existing `noodle_exporter` code-first `.module.py` output. A new `noodle_importer` package reverses this for the pull path.

**Workflow slug:** The file path for each workflow is derived by running the workflow name through the existing `slugify()` function from `noodle_exporter` (e.g. `"My Billing Flow"` → `my-billing-flow.py`). Slugs must be unique per org; on collision during first sync, the workflow ID suffix is appended (`my-billing-flow-{id[:8]}.py`).

---

## 2. Data Model

### New table: `github_sync_configs`

One row per org. Unique constraint on `org_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | `String(32)` | Primary key |
| `org_id` | `String(32)` FK | `organizations.id`, CASCADE delete |
| `credential_id` | `String(32)` FK | `credentials.id`, SET NULL on delete |
| `repo` | `String(200)` | `owner/name` format |
| `base_path` | `String(200)` | Default `workflows/` |
| `main_branch` | `String(100)` | Default `main` |
| `webhook_secret` | `Text` | HMAC secret for verifying GitHub payloads |
| `created_at` | `DateTime(timezone=True)` | |
| `updated_at` | `DateTime(timezone=True)` | |

### New columns on `workflows`

| Column | Type | Notes |
|---|---|---|
| `github_sync_sha` | `String(40)` nullable | Last SHA Noodle pushed or pulled — used for conflict detection |
| `github_sync_status` | `String(20)` nullable | `synced`, `pending`, `conflict`, `error`, or `null` (not connected) |
| `github_sync_conflict_sha` | `String(40)` nullable | Incoming SHA that triggered a conflict — used to fetch the conflicting file |

### Credential ownership

The GitHub OAuth2 credential used for sync is stored as an org-scoped credential (`scope = "global"`, `org_id` set, `workflow_id = null`), labelled `"GitHub Sync — {repo}"`. It is not tied to any individual user, so the sync survives user churn.

---

## 3. Push Path (Noodle → GitHub)

### `github_push_draft` job

Triggered on every autosave of `draft_graph`. Payload: `{ org_id, workflow_id }`.

1. Load `github_sync_configs` for the org — skip if none
2. Load workflow, generate `{base_path}/{slug}.py` content via `workflow_to_module()`
3. Ensure branch `draft/{slug}` exists (create from `main` if not)
4. Call GitHub Contents API `PUT /repos/{repo}/contents/{path}` with `branch=draft/{slug}` and `sha=workflow.github_sync_sha` (omit SHA if file is new)
5. GitHub rejects the write if the branch has diverged from the stored SHA — on 409/422, set `github_sync_status = "conflict"`, store the conflicting SHA in `github_sync_conflict_sha`, stop
6. On success: update `github_sync_sha` to the new file SHA returned by GitHub, set `github_sync_status = "synced"`

### `github_push_publish` job

Same as `github_push_draft` but targets `main_branch`. Commit message: `publish: {workflow.name} v{version}`. On success, also updates `github_sync_sha`.

### Retry policy

Jobs retry up to 3 times with exponential backoff (1s, 4s, 16s) on transient GitHub errors (rate limit 429, 5xx). Permanent errors (401 bad credential, 404 repo not found) set `github_sync_status = "error"` and do not retry.

---

## 4. Pull Path (GitHub → Noodle)

### Webhook endpoint

`POST /webhooks/github-sync/{org_id}`

- Looks up `github_sync_configs` for `org_id` — 404 if not found
- Verifies `X-Hub-Signature-256` HMAC against the org's `webhook_secret` — 401 on failure
- Parses GitHub push payload, extracts changed file paths from all commits
- For each changed path matching `{base_path}/*.py`: derives workflow slug, finds the matching workflow by slug, enqueues a `github_pull` job
- Returns 200 immediately — all processing is async

The webhook URL exposed to users: `https://{noodle_host}/webhooks/github-sync/{org_id}`

### `github_pull` job

Payload: `{ org_id, workflow_id, file_sha: str | None }`. `file_sha` is the blob SHA from the webhook push event; `None` for manual pulls (always fetches latest from `main_branch`).

1. Fetch file content via GitHub Contents API `GET /repos/{repo}/contents/{path}`
2. Decode base64 content
3. Check conflict: if `workflow.github_sync_sha` is set **and** the fetched file's blob SHA differs from `github_sync_sha` **and** `workflow.draft_graph` has local changes relative to the last-synced content (i.e. both Noodle and GitHub changed independently) → set `github_sync_status = "conflict"`, store the fetched file's blob SHA in `github_sync_conflict_sha`, stop. If only GitHub changed (Noodle has no local edits), pull proceeds without conflict.
4. Run `noodle_importer.import_module(source)` → `WorkflowGraph`
5. On parse error: set `github_sync_status = "error"`, log the error, stop (never silently corrupt)
6. On success: write `workflow.draft_graph`, update `github_sync_sha = file_sha`, set `github_sync_status = "synced"`

### Manual pull

`POST /workflows/{workflow_id}/github-pull` — enqueues a `github_pull` job with `file_sha: None`, which causes the job to fetch the latest file from `main_branch` and skip the conflict check (user is explicitly requesting an overwrite). Available in the editor overflow menu.

---

## 5. Python Importer (`noodle_importer`)

New package at `packages/importer/noodle_importer/`. Single public function:

```python
def import_module(source: str) -> WorkflowGraph: ...
```

**Strategy:** Static AST parsing via Python's built-in `ast` module. No `exec`/`eval`. The exporter's output is structured and consistent, so the importer can rely on it.

**Parsing rules:**

| Exporter output | Importer reads |
|---|---|
| `@workflow(name="...", ...)` on the top-level function | Workflow metadata |
| `@node(type="...", params={...})` decorated function | One graph node; `type` and `params` extracted from decorator kwargs |
| Function parameters with type annotations | Input port connections (edges from upstream nodes) |
| Call graph (which function calls which) | Edge list |

**Error handling:** Any file that deviates from the expected structure (hand-edited beyond the exporter's format) raises `ImportError` with a descriptive message. The caller (`github_pull` job) catches this and sets status to `error` — it never overwrites the workflow with corrupt data.

**Testing:** Full round-trip property tests: generate a `WorkflowGraph`, export via `workflow_to_module()`, import via `import_module()`, assert the graphs are equivalent.

---

## 6. Conflict Resolution

When `github_sync_status = "conflict"`:

- The workflow is **not modified** — Noodle's current `draft_graph` is preserved
- `github_sync_conflict_sha` holds the incoming file SHA

**UI flow:** User opens the "Resolve conflict" action (editor overflow menu or workflow list conflict badge). A split-pane diff modal opens:

- **Left panel:** Noodle's current `draft_graph` rendered as `.module.py` via `workflow_to_module()`
- **Right panel:** The incoming GitHub file, fetched on demand using `github_sync_conflict_sha`
- **Two actions:**
  - **Keep Noodle** — discards the GitHub change; resets `github_sync_sha` to the conflict SHA (so the next push will overwrite GitHub), clears conflict state
  - **Take GitHub** — runs `import_module()` on the right panel content, writes to `draft_graph`, sets `github_sync_sha = github_sync_conflict_sha`, clears conflict state

Both actions are logged to the audit trail as `github_conflict_resolved` with `{side: "noodle" | "github"}`.

---

## 7. UI

### Org Settings — "GitHub Sync" tab

- Connect button → triggers existing `github_oauth2` OAuth flow; stores result as org-scoped credential
- Fields: Repo (`owner/name`), Base path (default `workflows/`), Main branch (default `main`)
- Displays generated webhook URL and secret (copyable) to paste into GitHub repo settings
- Connection status chip: `Connected`, `Token expired`, `Repo not found`
- Disconnect button (revokes credential, removes config, does not delete synced files from GitHub)

### Workflow list — sync badge

Small icon appended to each workflow row:

| Status | Badge |
|---|---|
| `synced` | Green GitHub mark icon |
| `pending` | Spinning sync icon |
| `conflict` | Amber warning icon + "Conflict" label |
| `error` | Red alert icon |
| `null` | Nothing |

### Editor — MetaBar + Overflow menu

- MetaBar sync status inline next to SaveIndicator: `Synced · 2m ago` / `Syncing…` / `Conflict`
- Overflow menu additions:
  - **Pull from GitHub** — triggers manual pull; disabled if no sync config
  - **Resolve conflict** — opens conflict diff modal; only visible when `github_sync_status = "conflict"`

No new pages required — all surfaces live within existing settings and editor components.

---

## 8. Multi-Tenancy

### Org isolation

`github_sync_configs` is keyed on `org_id`. All jobs load credentials, workflow data, and config scoped to the carrying `org_id`. No cross-org access is possible.

### Webhook routing

Webhook URL encodes the org: `/webhooks/github-sync/{org_id}`. On receipt, Noodle fetches that org's config and verifies the HMAC. This avoids scanning all org configs to find a matching secret (which would be O(N) and leak timing information).

A forged or misrouted request fails HMAC verification → 401, no data accessed.

### Audit logging

All sync operations call `log_audit()` with the correct `org_id`:

| Action | Resource |
|---|---|
| `github_push` | `workflow:{id}` |
| `github_pull` | `workflow:{id}` |
| `github_conflict_resolved` | `workflow:{id}` |
| `github_sync_connect` | `org:{id}` |
| `github_sync_disconnect` | `org:{id}` |

### License gating

GitHub sync is gated behind a `github_sync` entitlement. Checked:
- In the settings UI (shows upgrade prompt instead of the connection form)
- At job enqueue time (jobs dropped silently if entitlement missing — this handles revoked licenses mid-sync gracefully)

---

## 9. Out of Scope

- Per-workflow repo overrides (org-level repo only)
- GitHub App authentication (PAT/OAuth2 only for now)
- Syncing credentials, environments, or code modules to GitHub
- Automatic PR creation on publish (future enhancement)
- Branch protection rule management
