# Runners Production Hardening — Design Spec

**Date:** 2026-06-26  
**Branch:** feat/runners-production  
**Status:** Approved for implementation

---

## Overview

Three sequential slices that bring runner pools to production grade before
external user onboarding. Every change is backward-compatible with existing
runner agents.

---

## Slice 1 — Reliability

### 1.1 Long-Lived Runner Tokens

**Problem:** Registration tokens are 24 h. A runner that has been running for
days silently fails to reconnect after a restart because its token has expired
with no indication in the UI.

**Solution:** Extend default TTL to 1 year. Add `RUNNER_TOKEN_TTL_DAYS`
config setting (default 365). SSH-onboard and manual registration both use it.

**Schema change:**
- Add `token_expires_at: DateTime(timezone=True)` to the `Runner` model.
  Set at token creation time so it can be displayed in the UI and used to
  surface expiry warnings.

**Edge cases:**
- Existing runners have no `token_expires_at` → column nullable; treat NULL
  as "unknown expiry" in the UI (no warning shown, no false alarms).
- Token revocation: unchanged — deleting the runner row still invalidates the
  token immediately regardless of TTL.
- `token_expires_at` is informational only; the actual expiry lives in the
  JWT `exp` claim. They are set from the same value at creation time.

---

### 1.2 Ghost Runner Auto-Cleanup

**Problem:** Minting a token pre-creates an offline runner row. Abandoned
installs (token minted but agent never ran) inflate the registered count
indefinitely.

**Definition of a ghost:** `last_seen_at IS NULL AND status = 'offline' AND
created_at < NOW() - ghost_ttl`.

**Solution:**

**Background task** (runs on startup loop, every 60 min):
- Delete ghost runners where `ghost_ttl = RUNNER_GHOST_TTL_HOURS` config
  (default 48 h).
- Safety gate: skip runners that have a `Run` row in `queued` or `running`
  state pointing to them (checked via `runs.runner_id`). This prevents a
  race where a run is dispatched to a freshly minted runner before it connects.
- Uses `SELECT FOR UPDATE SKIP LOCKED` on the runner row to avoid concurrent
  cleanup conflicts.
- Logs count of cleaned rows at INFO level.

**Manual endpoint:** `POST /runner-pools/{pool_id}/cleanup-ghosts`
(requires `runner_pool:write`). Separate path to avoid routing conflicts with
the existing `DELETE /runner-pools/{pool_id}/runners/{runner_id}` dynamic
segment. Same safety gate as background task. Returns `{"cleaned": N}`.

**Ghost count in RunnerPoolInfo:** Add `ghost_count: int` to `RunnerPoolInfo`
and compute it in `_pool_info()` alongside `online_count`. This avoids a
separate API call — the badge is visible without expanding the pool.

**Background task registration:** Registered in `main.py` lifespan alongside
existing background loops (`_retention_loop`, `_scheduler_loop`). The cleanup
coroutine runs in a `while True: await asyncio.sleep(3600)` loop, same pattern
as existing tasks.

**UI:** Pool card header shows a `Clean up N unconnected` link (ghost-count
badge) when `pool.ghost_count > 0`. Clicking triggers the manual endpoint and
refreshes the pool.

**Edge cases:**
- Runner connects at exactly the moment the cleanup query runs: `SELECT FOR
  UPDATE` serialises the race; the connect path sets `status='online'` and
  `last_seen_at`, which excludes it from the ghost predicate.
- Cleanup runs while the API is under load: `SKIP LOCKED` ensures the loop
  doesn't block waiting on locked rows.
- `ghost_ttl = 0`: disable auto-cleanup (set to 0 to opt out in dev).
- Draining runners with `last_seen_at IS NULL`: the cleanup `WHERE` clause
  must include `AND status != 'draining'` so a runner that was drained before
  ever connecting is never auto-deleted by the ghost sweeper.

---

### 1.3 Drain Mode

**Problem:** No graceful way to quiesce a runner before taking a machine down
for maintenance. The only options today are delete (abrupt) or hope.

**Solution:**

**New runner status: `draining`** (added to the valid status set alongside
`online`, `busy`, `offline`).

**API:** `POST /runner-pools/{pool_id}/runners/{runner_id}/drain` (body:
`{"drain": true|false}`). Sets `status = 'draining'` (or restores to
`'online'`/`'offline'` based on the runner's last-seen). Sends a
`{"type": "drain"}` WebSocket message to the runner if it is currently
connected so the agent can stop accepting new work gracefully.

**Dispatch:** Dispatcher skips runners with `status = 'draining'` when
looking for available capacity — they are treated as if at full capacity.

**Auto-complete hook:** When a draining runner's `current_runs` reaches 0,
the API sends a `{"type": "drain_complete"}` WS message. The implementation
hook lives in the run-completion path in `runner.py` — specifically in the
section that decrements `runner.current_runs` after a run finishes. After
decrement: `if runner.status == 'draining' and runner.current_runs == 0:
  await dispatcher.send_to_runner(runner_id, {"type": "drain_complete"})`.
The runner may then disconnect cleanly. The runner row stays in `draining`
state until the operator manually removes it or undrains it.

Note: the `{"type": "drain"}` WS message sent on the drain API call is a
**courtesy signal** only — drain correctness is enforced server-side by the
dispatcher skipping `draining` runners. The WS message is advisory so the
runner can log it; it does not change the dispatch outcome.

**UI:** Each runner row in the expanded pool shows a `Drain` / `Undrain`
toggle button. While draining, the status dot uses a distinct "draining"
colour (yellow). When `current_runs = 0` and status is draining, show
"safe to remove" indicator.

**Edge cases:**
- Runner is offline when drained: set status to `draining` in DB. The runner
  won't receive the WS message but will not be dispatched to on reconnect
  because the drain flag is checked against DB status at dispatch time.
- Operator deletes a draining runner that has active runs: follows existing
  delete path — in-flight runs are marked `interrupted` by the reaper.
- Undrain: always set `status = 'online'`, never write `'busy'` directly.
  `busy` is only written by the dispatcher when assigning a run — writing it
  from the drain endpoint would be a lie about the runner's actual state.
  The next dispatch tick or WS heartbeat will reflect the correct state.
- Drain all runners in a pool simultaneously: runs queue up normally; they
  will execute once at least one runner is undraining.

---

### 1.4 SSH Runner Restart

**Problem:** SSH-onboarded runners store encrypted credentials but there is no
API to use them for remote restart. When a systemd service crashes, the
operator must SSH in manually.

**Solution:**

**New endpoint:** `POST /runner-pools/{pool_id}/runners/{runner_id}/restart`
(requires `runner_pool:write`). Returns `{"log": "..."}`.

**`ssh_host` in RunnerInfo:** Add `ssh_host: str | None` to the `RunnerInfo`
Pydantic schema and populate it in `_runner_info()`. The frontend uses
`ssh_host != null` to conditionally show the Restart button. The field
carries only `"user@host:port"` — not the decrypted credentials.

**Rate limiting:** The endpoint is guarded by an in-process rate limiter of
**5 calls per runner per minute** (keyed on `runner_id`). Exceeding it returns
HTTP 429. This prevents UI bugs or misbehaving clients from hammering external
hosts with SSH connections.

**Implementation:**
1. Check rate limit (5/min per runner_id). Return 429 if exceeded.
2. Load runner, verify `ssh_host IS NOT NULL` (400 if null — not SSH-onboarded).
   Decrypt `ssh_credentials`.
3. Build a minimal restart script that does NOT re-register (long-lived token
   from Slice 1.1 is still valid):
   - If `use_systemd=true` in stored creds:
     `sudo systemctl restart noodle-runner`
   - Else: `pkill -f noodle_runner_agent.agent || true` then
     `nohup python3 -m noodle_runner_agent.agent start > ~/noodle-runner.log 2>&1 &`
4. Connect via asyncssh (30 s timeout), run the script, return combined
   stdout/stderr in `{"log": "..."}`.
5. If asyncssh is not installed: HTTP 400 with message
   "asyncssh is required on the API host for SSH operations".

**UI:** `RunnerInfo.ssh_host` is non-null for SSH-onboarded runners. Each
such runner row shows a `Restart` button alongside Edit/Remove. Clicking opens
a small modal that calls the endpoint and streams the log output. After success
the runners list auto-refreshes after 5 s (time for the agent to reconnect).

**Edge cases:**
- Runner is currently online/busy: allow restart (operator's choice). Show a
  warning in the modal: "This runner has N active runs — restarting will
  interrupt them."
- SSH credentials are stale (password changed, key rotated): asyncssh raises
  an auth error; surface as HTTP 400 with the raw error message.
- Restart script exits non-zero: return the log with an error flag; do not
  raise an unhandled 500.
- Concurrent restarts: rate limiter prevents overlapping calls. systemd
  handles a double-restart gracefully anyway.

---

## Slice 2 — Correctness

### 2.1 AWS Secret Encryption + Redact on Read

**Problem:** `aws_secret_access_key` is stored in `provider_config` as plain
JSON. It is returned to the frontend on every GET, visible in DB dumps, and
never encrypted at rest.

**Solution:**

**Schema change:** Add `aws_secret_key_enc: Text nullable` to `RunnerPool`.
Stores `encrypt_data({"aws_secret_access_key": "<value>"})` (same Fernet
helper used by `ssh_credentials`).

**Save path (both CREATE and UPDATE):** The extract-encrypt-strip logic is
factored into a shared helper `_extract_aws_secret(pool, provider_config)`
called from both `create_runner_pool` and `update_runner_pool`. When
`provider_config` contains `aws_secret_access_key`:
1. Encrypt and store in `pool.aws_secret_key_enc`.
2. Strip `aws_secret_access_key` from the `provider_config` dict.
3. Commit both changes atomically.
For PATCH: if the frontend sends `provider_config` without `aws_secret_access_key`
(user didn't change the secret), and `aws_secret_key_enc` is already set,
leave `aws_secret_key_enc` unchanged. Only overwrite when a new non-empty
value is explicitly supplied.

**Read path:** `RunnerPoolInfo` schema adds `aws_secret_configured: bool`.
The `aws_secret_access_key` field is **never** included in API responses.

**Frontend:** In the pool edit dialog, when `aws_secret_configured = true`:
- Show a masked placeholder `••••••••` and a `Change` button.
- Clicking `Change` clears the field and lets the operator type a new value.
- Submitting with the field blank (and `aws_secret_configured = true`) = no
  change to the stored secret (PATCH omits the key).

**Migration:** Alembic migration `add_aws_secret_key_enc`:
1. Add nullable `aws_secret_key_enc` column.
2. Data migration: for each pool where `provider_config` contains
   `aws_secret_access_key`, encrypt and populate the new column, strip from
   JSON. Done in a loop within the migration so it handles large tables safely.

**Edge cases:**
- IAM-role mode (no key stored): `aws_secret_key_enc = NULL`,
  `aws_secret_configured = false`. No masking, no Change button.
- Partial migration (column added, data migration failed): the save path
  checks both the new column and the old JSON key. If the JSON still has the
  key, it re-encrypts on next write.
- Key rotation: operator clicks `Change`, enters new value, saves. Old
  encrypted value is overwritten atomically.

---

### 2.2 Label-Aware Dispatch

**Problem:** Runners have a `capabilities` dict (labels) that can be set via
the UI, but the dispatch loop ignores them entirely. This misleads operators
into thinking label routing works.

**Solution:**

**Run-level label requirements:** Add `required_labels: JSONB nullable` to the
`Run` model (default NULL = no requirement). `start_run()` accepts an optional
`required_labels` dict. Manual run trigger UI exposes this as an advanced
collapsible "Run on specific runner labels" field.

**Dispatch filter — all paths:** The `capabilities @> required_labels` filter
must be applied in **every** dispatch path to prevent labelled runs being
routed to wrong runners:
- `run_queue_dispatch_loop` in `queue.py` — the DB query that selects an
  eligible runner from the pool must add:
  ```sql
  AND (run.required_labels IS NULL OR runner.capabilities @> run.required_labels)
  ```
- Any inline dispatch in `runner.py` — same filter applied before assigning.

JSONB containment (`@>`) means a runner must have **at least** the required
labels; extra labels on the runner are fine. Values are string-compared.
SQLite (used in some dev environments) does not support `@>` — use Python-level
dict subset check as a fallback when `settings.db_url` starts with `sqlite`.

**Health signal — `label_mismatch_queued`:**
New field on `RunnerPoolHealth`. Computed in the health endpoint as follows:
1. For each pool, fetch all `RunQueueEntry` rows with `status='queued'` and
   `available_at < NOW() - 30s` (already stuck). Join to `Run` to get
   `required_labels`.
2. Fetch all online runners for the pool (`status IN ('online', 'busy')`).
3. A run is a mismatch if `required_labels IS NOT NULL AND required_labels != {}`
   and no online runner satisfies `runner.capabilities @> required_labels`.
4. `label_mismatch_queued` = count of such runs.
This computation is O(queued_runs × online_runners) per pool, which is small
in practice. Cap at 100 runs checked per pool to bound the cost.

**UI:** `HealthStrip` shows `⚠ N label mismatch` in amber when
`label_mismatch_queued > 0`. Tooltip: "These runs need labels not available
on any online runner."

**Edge cases:**
- `required_labels = {}` (empty dict): treated identically to NULL —
  `{} @> {}` is true for any runner.
- No runners in pool match the required labels and none are online: runs
  queue normally. The label_mismatch signal surfaces the problem without
  blocking admission.
- Runner comes online with matching labels while labelled runs are queued:
  the dispatch loop's next tick picks them up (existing polling behaviour).
- Mixed pool (some runners have the label, some don't): only labelled runners
  receive label-filtered runs. Un-labelled runs continue to dispatch to any
  available runner (NULL required_labels).
- Docker/K8s pools: `required_labels` is stored on the run but silently
  ignored for non-agent pools (they don't have persistent runners). Document
  this limitation; the label_mismatch counter only fires for agent pools.
- Migration: `required_labels` column is nullable with no default — existing
  runs are unaffected.

---

## Slice 3 — Observability

### 3.1 Per-Pool Run History API

**New endpoint:** `GET /runner-pools/{pool_id}/run-history`

**Query params:**
- `days` (int, 1–30, default 7)
- Bucket size: auto-derived (hour if days ≤ 7, day if days > 7)

**Response:**
```json
[
  {
    "bucket_start": "2026-06-25T14:00:00Z",
    "success": 12,
    "error": 1,
    "total": 13,
    "avg_duration_seconds": 42.3
  }
]
```

**Implementation:** Single GROUP BY query on `runs` using `date_trunc` on
`finished_at`, filtered by `runner_pool_id` and `finished_at >= cutoff`.
Includes only runs with `status IN ('success', 'error')`.

**Edge cases:**
- Pool has no runs: return `[]` (not 404).
- `days > 30`: clamp to 30 and return with a `X-Clamped: true` header.
- Very large tables: query uses the existing `runner_pool_id` + `finished_at`
  index. Add composite index in migration if absent.

---

### 3.2 Sparklines UI

Each `PoolCard` renders a 24-hour mini sparkline below the `HealthStrip`.

**Data:** Uses `GET /runner-pools/{pool_id}/run-history?days=1` (hourly
buckets, 24 data points). Fetched once on pool card mount with
`useRunnerPoolHistory(pool_id)` — not on the 5 s health poll (history changes
at minute granularity, not seconds).

**Render:** Pure SVG path — no chart library dependency. Two stacked bars per
bucket: green for success, red for error. Width = 100%, height = 32px. Zero
data = flat line (not blank).

**Edge cases:**
- All zero data: render flat baseline (communicates "no runs" rather than
  missing component).
- Single spike with all others zero: auto-scale to max bucket value so the
  spike is visible.
- Fetch error: hide the sparkline (don't show an error state in the health
  strip — the live numbers are more important).

---

### 3.3 K8s / Docker Pool Run Visibility

When a non-agent pool is expanded, show a "Recent runs" mini-table (last 10
runs dispatched through this pool) using the existing
`GET /runs?runner_pool_id={id}&limit=10` query param (add `runner_pool_id`
filter to the runs list endpoint if not already present).

Columns: workflow name, status pill, started at, duration. Links to the run
detail page.

**Edge cases:**
- No runs: show "No runs dispatched yet."
- Runs list endpoint already supports `runner_pool_id` filter: verify before
  adding a duplicate.

---

### 3.4 Pool Search / Filter

**Client-side only** — no API change. A search input above the pool list
filters cards in real time by pool name or provider string (case-insensitive).

**Show only when** `pools.length > 5` (below that the list is short enough to
scan visually).

**Edge cases:**
- Filter clears on navigation away and back (no URL state persistence needed
  for v1).
- All pools filtered out: show "No pools match your search." with a clear
  button.

---

### 3.5 Token Expiry Indicator

**Schema:** `token_expires_at` added in Slice 1.1. This slice uses it in the
UI.

**Runner table display rules:**
- `token_expires_at` is null: no indicator (legacy runner, unknown expiry).
- `token_expires_at` is within 7 days: amber ⚠ icon with tooltip "Token
  expires <date> — mint a new one to keep this runner connected."
- `token_expires_at` is in the past and runner is offline: red ✕ icon with
  tooltip "Token expired — runner will not reconnect. Delete and re-register."
- `token_expires_at` is past and runner is online: no warning (runner is
  connected, expiry only matters on reconnect).

---

## Migration Plan

All three slices require Alembic migrations:

| Migration | Slice | Changes |
|---|---|---|
| `add_runner_token_expires_at` | 1.1 | Add nullable `token_expires_at` to `runners` |
| `add_runner_drain_status` | 1.3 | No column change — `status` is a plain String(20), `draining` is a valid new value |
| `add_aws_secret_key_enc` | 2.1 | Add nullable `aws_secret_key_enc` to `runner_pools`; data migration to encrypt existing secrets |
| `add_run_required_labels` | 2.2 | Add nullable JSONB `required_labels` to `runs` |
| `add_runner_pool_history_index` | 3.1 | Composite index `(runner_pool_id, finished_at)` on `runs` if absent |

Each migration is independent and reversible. No existing data is deleted.

---

## Testing Requirements

### Slice 1
- `test_runner_token_ttl.py`: token expiry uses config value; default is 1 year.
- `test_ghost_cleanup.py`: background task deletes ghosts; skips runners with
  active runs; `SELECT FOR UPDATE` prevents double-delete.
- `test_drain_mode.py`: draining runner is not dispatched to; undrain restores
  dispatch; drain with active runs does not interrupt them.
- `test_ssh_restart.py`: monkeypatched asyncssh; happy path + auth error +
  non-zero exit.

### Slice 2
- `test_aws_secret_encryption.py`: key stored encrypted; never returned in
  GET response; data migration encrypts existing rows.
- `test_label_dispatch.py`: exact-match routing; empty/null = any runner;
  mismatch queued count; mixed-label pool.

### Slice 3
- `test_run_history.py`: bucket aggregation; empty pool; days clamped at 30.
- Frontend: Vitest for sparkline SVG path generation edge cases (all-zero,
  single-spike, fetch error).

---

## Non-Goals (Explicitly Out of Scope)

- Vault-based AWS credential management (future follow-up).
- Label soft-preference / fallback routing.
- Per-runner Prometheus metrics export.
- Kubernetes pod status visibility beyond run list.
- Automatic token refresh protocol on the runner agent side.
