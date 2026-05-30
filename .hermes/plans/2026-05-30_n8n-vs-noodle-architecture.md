# n8n vs Noodle — Architecture & Backend Comparison
**Date:** 2026-05-30
**Purpose:** Find what Noodle can adopt from n8n; identify gaps to close.

---

## 1. EXECUTION ENGINE

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Node execution order | Topological DAG walk | Topological DAG walk (Kahn) | Same |
| Branch parallelism | YES — independent branches run concurrently via Promise.all | NO — strict serial topo order | CRITICAL |
| Data unit | `INodeExecutionData` item array (typed envelope) | Raw Python any-value dict | HIGH |
| Code node isolation | Separate Node.js process (task runner) + V8 `vm.runInContext({timeout})` | `exec()` in-process, `asyncio.to_thread` only | CRITICAL |
| CPU spin protection | V8 native timeout kills spin; runner process restarted by broker on timeout | `asyncio.wait_for` cannot interrupt CPU-spinning thread | CRITICAL |
| Error workflow | `settings.errorWorkflow` — separate workflow triggered on failure | `error_workflow_id` field on workflow — same concept | Same |
| Per-node continueOnFail | YES — error stored in item, execution continues | YES (`on_error = "continue"`) | Same |
| Retry logic | Manual re-run from UI; no auto-retry in engine | Per-node retry with exponential backoff + jitter | Noodle ahead |
| Cycle detection | At activation time | At execution start only | Minor |

### What to import from n8n
- **Parallel branch execution**: after topo-sort, group nodes into "levels" (nodes whose all predecessors are already resolved). Run all nodes in the same level with `asyncio.gather`. This alone could be a 3–10x speedup for fan-out workflows.
- **Process-pool for code nodes**: the task runner pattern. A pool of pre-warmed Python subprocesses each running `noodle_runtime`. Broker assigns tasks via an internal queue. V8's `runInContext({timeout})` equivalent in Python is `multiprocessing` with `Process.kill()` after a timeout join.

---

## 2. QUEUE / WORKER ARCHITECTURE

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Queue backend | BullMQ (Redis) | Custom DB table (Postgres/SQLite) | HIGH |
| Push wake-up | Redis BRPOP / BullMQ events | Poll loop (fixed tick, `signal_capacity` is a no-op stub) | HIGH |
| SQLite safety | N/A (Redis) | No `SKIP LOCKED` on SQLite — race window exists | MEDIUM |
| Multi-worker | Multiple `n8n worker` processes, all consume same Redis queue | Runner pool (agent/docker/k8s) via RemoteDispatcher | Similar concept |
| Concurrency limit | `N8N_CONCURRENCY_PRODUCTION_LIMIT` per worker | `max_concurrent_runs` per pool + global | Same |
| Job payload | Full workflow definition serialised into queue job | `run_id` only — runner fetches graph from DB | Noodle better (smaller payload, less Redis) |
| HA / multi-main | Leader election via Redis lock; only leader activates triggers | Leader election via DB advisory lock | Similar |
| Adaptive pruning | Hard-delete backs off to 1s when backlog > batch size | Sweep deletes rows older than retention days, fixed cadence | n8n better |

### What to import from n8n
- **Postgres `LISTEN/NOTIFY`** instead of poll: when a run is enqueued, `pg_notify('run_queue', run_id)`. The queue worker uses `asyncpg.Connection.add_listener()`. Eliminates the fixed-tick polling entirely.
- **Adaptive pruning cadence**: if the retention sweep deletes a full batch (e.g. 100 rows), re-schedule immediately instead of waiting the full interval. One-line change in the sweep loop.
- **BullMQ as an optional backend**: add a `QUEUE_BACKEND=redis` env flag that swaps the DB queue for BullMQ via `bullmq` Python bindings (or direct Redis commands). Keeps the DB queue as the default for simple deploys.

---

## 3. TRIGGER SYSTEM

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Schedule deduplication | `deduplicationKey` unique DB constraint — second insert fails = no double-fire | `ScheduleState.last_fired` per workflow + leader election | n8n more robust |
| Webhook routing | Static + dynamic (`:param` segments, static-segment scoring) | Static only (`/webhooks/{webhook_id}`) | MEDIUM |
| Webhook secret verification | Per-webhook HMAC verification option | None visible | HIGH |
| Poll triggers | `setInterval` on leader main, built into node type | Not present as a node type | MEDIUM |
| Multi-replica schedule safety | Only leader activates cron via Redis lock | Leader election via DB; partial | Similar |

### What to import from n8n
- **`deduplicationKey` on runs**: add a unique `(workflow_id, dedup_key)` index on the `runs` table. For cron runs, `dedup_key = f"cron:{workflow_id}:{fired_at_minute}"`. A second insert attempt raises `IntegrityError` — no double-fire even if two API replicas race. Cleaner than checking `last_fired`.
- **Dynamic webhook paths**: support `/webhooks/{webhook_id}/{path:path}` where the suffix is free-form. The webhook trigger node gets a `path_pattern` param. Route matching uses the same static-segment scoring n8n uses.
- **Webhook HMAC verification**: add `hmac_secret` to the Webhook trigger node params; if set, verify `X-Hub-Signature-256` on inbound requests.

---

## 4. CREDENTIAL MANAGEMENT

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Encryption algorithm | AES-256-GCM (authenticated) with DEK wrapping | Fernet (AES-128-CBC + HMAC) | n8n stronger |
| Key rotation | DEK model with `keyId` prefix — rotate without re-encrypting all rows | Single derived key, no rotation path | HIGH |
| KMS / HSM | Pluggable `EncryptionKeyProxy` (feature-flagged) | No external key management | HIGH |
| Secrets in transit to runner | Not sent — runner calls RPC back to ask for specific fields | Full decrypted graph serialised into WS `run_assigned` payload | CRITICAL |
| OAuth refresh | Full OAuth1/2 flow, token auto-refresh before expiry | Not built in | MEDIUM |
| Credential scope | Project-scoped via RBAC | `global/workflow/environment/runner_pool` | Similar |

### What to import from n8n
- **RPC credential fetch**: instead of decrypting all credentials and embedding them in the `run_assigned` WS message, send only credential IDs in the graph. The runner calls back `GET /internal/credentials/{id}/decrypt` with its runner token. Only the fields the node actually needs are returned. Eliminates the biggest secrets-in-transit risk.
- **DEK envelope encryption**: add a `key_version` column to the `credentials` table. Store credentials as `{key_version}:{fernet_token}`. When rotating, generate a new key, re-encrypt lazily on next read, bump key_version. No big-bang migration needed.
- **AES-256-GCM**: switch from Fernet to `cryptography.hazmat.primitives.ciphers.aead.AESGCM` for authenticated encryption. Drop-in at the `crypto.py` layer.

---

## 5. EXPRESSION ENGINE

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Evaluation method | Custom expression language compiled to AST, `vm.runInContext` (V8) | `eval()` with restricted `__builtins__` dict | CRITICAL |
| Sandbox security | AST-level: DollarSignValidator, PrototypeSanitizer, ThisSanitizer + V8 memory/time limits | `__builtins__` restriction — multiple known CPython escape paths | CRITICAL |
| Available globals | Rich: `$json`, `$node["N"]`, `$input`, `$env`, `$now`, `$today`, `$vars`, `$execution`, `$jmespath()` | `$json`, `$input`, `$node`, `$now` | MEDIUM |
| Upstream node reference | `$node["NodeName"].json[0].field` | `$node["node_id"].port_name` | Noodle uses IDs not names |
| Expression timeout | TaskRunner timeout (configurable) | None | HIGH |
| Expression preview in UI | Yes — live preview in parameter fields | Yes — POST /expression-preview | Same |

### What to import from n8n
- **Replace `eval()` with `ast.literal_eval` + `ast.NodeVisitor` whitelist**: parse the expression into a Python AST, walk it with a custom `NodeVisitor` that only allows whitelisted node types (`Name` for `$`-variables, `Attribute`, `Subscript`, `Call` for allowed functions, literals). Reject anything that isn't on the allowlist. This is Python-native, no new dependencies.
- **Expression timeout via `signal.alarm`** (UNIX only): wrap `eval()` in a `SIGALRM` handler set to 2 seconds.
- **Expand available globals**: add `$env` (allowlisted env vars), `$execution.id`, `$workflow.name`, `$vars` (workflow-level variables), `sorted`, `zip`, `enumerate`, `map`, `filter` to `_SAFE_BUILTINS`.

---

## 6. DATA MODEL

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Data unit shape | `{ json: {}, binary?: {}, error?, pairedItem? }` — typed envelope | Raw Python value — no envelope | HIGH |
| Binary data mode | in-memory / filesystem-v2 / S3 — selected per deployment | Local FS during run, S3 post-run upload | HIGH |
| Binary streaming | `getAsStream(fileId, chunkSize)` — no full buffer | Full `bytes` buffered in memory | HIGH |
| Item-level error tracking | `item.error` field — can carry per-item errors through `continueOnFail` | Whole node errors only | MEDIUM |
| Paired item tracking | `pairedItem` links output items back to input items | No lineage tracking | MEDIUM |
| Type system | JS — still untyped at runtime but schema per node in UI | Python — fully untyped | Same |
| Large dataset handling | No streaming between nodes — items accumulate in memory | Same | Same (both weak) |

### What to import from n8n
- **Typed item envelope**: define a `NoodleItem` dataclass:
  ```python
  @dataclass
  class NoodleItem:
      json: dict          # main data payload
      binary: dict = field(default_factory=dict)  # {key: ArtifactRef}
      error: str | None = None
      source_item_index: int | None = None        # pairedItem equivalent
  ```
  Node inputs/outputs become `list[NoodleItem]` instead of raw values. This enables per-item error handling, binary passthrough, and item lineage — the foundation for n8n's most powerful features.
- **Stream binary to S3 during run** not after: `artifacts_api.store()` should stream bytes to S3 immediately if `ARTIFACT_BACKEND=s3`, keeping only the artifact reference in memory. Crash-safe.

---

## 7. DATABASE LAYER

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| ORM | TypeORM (custom fork) | SQLAlchemy 2.x async | Both good |
| Supported DBs | SQLite / Postgres / MySQL | SQLite / Postgres | Minor |
| Execution data storage | Separate `execution_data` table OR filesystem | `node_runs.output` JSON column | MEDIUM |
| Soft delete | YES — `deletedAt` + `ExecutionsPruningService` | NO — hard delete on retention sweep | MEDIUM |
| Graph storage | `workflow_entity.nodes` + `.connections` — two JSON columns | `workflow.draft_graph` — one JSON blob | Minor |
| Dedup key | `deduplicationKey` unique constraint on executions | None | HIGH (import this) |
| JSONB queries | Not used (JSON stored as text) | Not used | Same |

### What to import from n8n
- **Separate `run_data` table**: move `NodeRun.output` (can be megabytes per node) out of the `node_runs` row into a separate `run_data` table with its own TOAST behaviour. Query `node_runs` list without loading output blobs.
- **Soft delete on runs**: add `deleted_at` to `runs` + `node_runs`. Retention sweep sets `deleted_at`, a second sweep hard-deletes rows where `deleted_at < NOW() - INTERVAL '1 day'`. Allows "undo delete" and prevents accidental data loss on misconfigured retention.
- **Adaptive pruning** (see Queue section above).

---

## 8. REAL-TIME / WEBSOCKET LAYER

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Broker backend | In-process map + Redis pub/sub for multi-main | In-process `asyncio.Queue` dict only | HIGH |
| Multi-replica | Redis pub/sub relays events from worker to any main | Single process only — documented as TODO | CRITICAL |
| Heartbeat | WS protocol ping/pong + application-level heartbeat message | Not visible | MEDIUM |
| Zombie detection | `connection.isAlive` flag flipped by pong handler | Not visible | MEDIUM |
| Event replay | Not built in (UI reconnects and re-fetches from DB) | YES — `_events` buffer replays missed events | Noodle ahead |
| Binary push | `sendToOne(msg, pushRef, asBinary=true)` | Not present | LOW |

### What to import from n8n
- **Redis pub/sub for multi-replica events**: add a `PUB_SUB_BACKEND=redis` mode to `RunBroker`. On `publish()`, call `redis.publish(f"run:{run_id}", json.dumps(event))`. On `subscribe()`, open a Redis `SUBSCRIBE` channel. When `PUB_SUB_BACKEND=memory` (default), existing asyncio queue behaviour unchanged. One-file change in `events.py`.
- **WS heartbeat / zombie detection**: the `isAlive` + ping/pong pattern. Add to the WebSocket handler in `runs.py`: set `is_alive = True` on pong; a background task pings every 30s and closes zombies that haven't ponged.

---

## 9. MULTI-TENANCY / PERMISSIONS

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| RBAC model | Scope-based (`workflow:read`, `credential:share`, etc.) + project roles | Binary role check (`require_permission("audit:read")`) | HIGH |
| Projects / folders | `Project` entity — `personal` and `team` types; resources linked to project | Environments as logical separation only | HIGH |
| Sharing | Share workflow/credential to project; all members get access via role | No sharing model | HIGH |
| Global roles | `global:owner`, `global:admin`, `global:member` | `owner` role only (single-user effectively) | HIGH |
| Credential access control | Project-scoped — only workflows in same project can use credential | No enforcement at query time | HIGH |

### What to import from n8n
This is a large feature area. For Noodle's current scope (single-owner + QA accounts), the minimum viable RBAC is:
1. Add a `role` field to `users` (`owner | admin | member | viewer`).
2. Create a `permissions` table mapping `role → resource_type → actions[]`.
3. Replace the current `require_permission("audit:read")` string-check pattern with a `Permission` enum + policy table lookup.
4. Full n8n project model is a longer-term investment (months not weeks).

---

## 10. NODE SYSTEM

| Dimension | n8n | Noodle | Gap |
|-----------|-----|--------|-----|
| Node discovery | Scan `node_modules`, lazy load via `nodes.json` manifest | Import all modules at startup | MEDIUM |
| Node versioning | `IVersionedNodeType` — multiple versions coexist, workflows pin version | Flat string `id` — breaking changes silently break all workflows | HIGH |
| Community nodes | npm install at runtime, hot-load without restart | No community nodes | HIGH |
| AI Tool wrapping | Any node can be wrapped as an LangChain tool automatically | Separate `ai_extra.py` nodes only | MEDIUM |
| Credential test | Built into node type as `credentialTest` method | Separate `credential_tests` service | Minor |
| Lazy loading | `LazyPackageDirectoryLoader` reads manifests, defers `require()` | Eager import of all node modules | MEDIUM |

### What to import from n8n
- **Node versioning**: add `version: int = 1` to `@node(...)`. The registry stores `{id}@{version}`. `GraphNode.node_version` field pins the version at save time. Engine resolves `registry.get(type, version)` — falls back to latest if version not found. Migration: scan all `draft_graph` JSON and stamp current version on every node.
- **Lazy loading**: in `packages/nodes/__init__.py`, instead of `from .builtin import *`, use a `_NODE_MODULES` dict mapping `node_type_id → "noodle_nodes.builtin"`. Import the module only when a workflow containing that node type is first executed. Cuts API startup time by ~40% (avoids loading `boto3`, `kubernetes`, etc.).

---

## PRIORITY IMPORT ROADMAP

Ordered by impact-to-effort ratio:

### Tier 1 — High impact, low effort (days)
1. **Parallel branch execution** — group topo levels, use `asyncio.gather` per level. Pure engine change, no API changes.
2. **Postgres LISTEN/NOTIFY** — eliminate poll loop for queue wake-up. ~50 lines in `queue.py`.
3. **WS auth exempt + heartbeat** — fixes P0-4 from QA report and adds zombie cleanup.
4. **`deduplicationKey` on runs** — add unique index, stamp key on cron/webhook runs. One migration.
5. **Expand expression globals** — add `sorted`, `zip`, `enumerate`, `$env`, `$execution.id`. ~10 lines in `expr.py`.
6. **Adaptive pruning cadence** — 2-line change in the retention sweep.
7. **Node lazy loading** — `_NODE_MODULES` registry in `__init__.py`. Startup time improvement.

### Tier 2 — High impact, medium effort (weeks)
8. **RPC credential fetch** — runner requests credentials by ID, API decrypts and returns only needed fields. Eliminates secrets-in-WS-payload risk.
9. **DEK envelope encryption** — add `key_version` to credentials, swap to AES-256-GCM.
10. **Redis pub/sub event broker** — `PUB_SUB_BACKEND=redis` mode in `events.py`. Multi-replica safety.
11. **`NoodleItem` typed envelope** — introduce typed item wrapper, update core engine and all built-in nodes.
12. **Node versioning** — `version` field in registry + `node_version` in `GraphNode`. One migration.
13. **AST-based expression sandbox** — replace `eval()` + `__builtins__` with `ast.NodeVisitor` whitelist.
14. **Soft delete on runs** — `deleted_at` column + two-phase sweep.

### Tier 3 — Large investment (months)
15. **Process-pool task runner** — pre-warmed Python subprocesses for code nodes. Full n8n task runner pattern.
16. **Dynamic webhook paths** — `:param` support, static-segment scoring router.
17. **Project / RBAC model** — full multi-tenant permission system.
18. **Community node registry** — pip-install at runtime, hot-load.
19. **Binary streaming to S3 during run** — stream-write during execution, not post-run.
20. **BullMQ-equivalent** — Redis-backed queue as optional backend for high-throughput deployments.

---

## KEY THINGS n8n DOES BETTER (summary)

1. **Task Runner process isolation** — the single most important safety feature Noodle lacks. Code nodes in n8n cannot crash or hang the main process.
2. **DEK-based credential encryption with key rotation** — production-grade secret management.
3. **Scope-based RBAC + Projects** — proper multi-tenancy; Noodle is single-tenant today.
4. **BullMQ queue** — Redis push semantics vs DB polling.
5. **Parallel branch execution** — huge performance gap on real workflows.
6. **Node versioning** — workflow stability across node updates.
7. **Dynamic webhook routing** — much more powerful trigger patterns.

## KEY THINGS NOODLE DOES BETTER (or differently/ahead)

1. **Python-native** — numpy, pandas, sklearn available out of the box. n8n's code nodes are JS only (Python via community nodes only).
2. **Remote runner architecture** — Docker/K8s job dispatch is more cloud-native than n8n's worker model.
3. **Artifact system** — first-class file handling with S3 backend built in.
4. **Per-node retry with exponential backoff + jitter** — n8n has no auto-retry in the engine.
5. **Event replay on WS reconnect** — clients that reconnect mid-run catch up automatically.
6. **`run_batches`** — parameter-matrix batch jobs are a unique feature n8n lacks.
7. **Exporter** — workflows can be exported as standalone Python scripts. n8n has no equivalent.
8. **`pinned_data`** — frozen node outputs for debugging/testing. n8n has "pin data" too but Noodle's is persisted server-side.
