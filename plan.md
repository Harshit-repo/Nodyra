# Noodle — Project Plan & Current State

> A self-hostable, Python-native workflow automation platform where every node
> is pure Python. This document is the single hand-off — it captures what's
> built, how it fits together, what's intentionally not done yet, and what to
> do next.

---

## 1. Vision

Build a Python-native workflow automation platform, end to end:

- Users build workflows on a drag-and-drop React Flow canvas.
- Every node is a plain Python function (typed I/O, declared in a tiny SDK).
- Workflows run on warm Python worker processes, not a container per
  execution.
- Per-workflow isolation comes from an **Environments** section: one global
  `uv`-managed venv plus user-created custom envs. A workflow is assigned an
  env; runs go to the runner subprocess bound to that env.
- Any workflow can be exported as a standalone `.py` file **or** packaged as a
  Docker image to run anywhere.
- Self-hostable first; multi-tenant SaaS is a later upgrade requiring real
  sandboxing.

The original milestone plan ran M0 → M8 and is delivered as the self-hostable
core. See §9 ("What ships today") and §10 ("Gaps & honest scope notes").

---

## 2. Architecture overview

```
                ┌──────────────┐
   Browser ───▶ │  Web (React) │  React Flow canvas + Workflows/Environments/
                │  + React     │  Credentials/Activity/Editor pages
                │  Flow        │
                └──────┬───────┘
                       │ REST + WebSocket  (via Vite proxy /api and /ws)
                ┌──────▼───────┐
                │  API         │  FastAPI. Workflow CRUD + versioning,
                │  (FastAPI)   │  nodes, environments, runs, triggers,
                │              │  webhooks, credentials, audit, auth,
                │              │  pinned data, export, metrics.
                └──┬────────┬──┘
         ┌────────┘        └────────┐
   ┌─────▼─────┐             ┌──────▼─────────────┐
   │ PostgreSQL│             │ In-process broker  │  asyncio pub/sub for live
   │  (SQLite  │             │ + scheduler loop   │  run events; in dev runs
   │   in dev) │             │ + run executor     │  in the API process
   └───────────┘             └────────────────────┘
                                       │
                                       ▼
                              ┌────────────────────┐
                              │ Noodle engine      │  packages/core/noodle
                              │ (DAG executor)     │  Same engine used inside
                              │                    │  exported .py scripts.
                              └────────────────────┘
```

**Pieces that exist as scaffolding for the production architecture but aren't
fully wired:**

- `apps/worker` — Celery + Beat skeleton, runs a `ping` task. The production
  path is Celery worker pool + Beat for cron triggers; for dev, both are
  in-process in the API.
- `packages/runtime` — placeholder for the env-runner subprocess server. The
  production path is a long-lived runner per environment that imports the
  engine + nodes from that venv; for dev, the API runs the engine directly in
  its own Python.

---

## 3. Tech stack

| Layer        | Choice |
|--------------|--------|
| Frontend     | React 18, TypeScript, Vite, **React Flow** (`@xyflow/react`), Zustand, React Router |
| API          | Python 3.12, **FastAPI**, Pydantic v2, SQLAlchemy 2.0 async, Alembic |
| Database     | PostgreSQL 16 (production); SQLite + aiosqlite (dev + tests) |
| Queue/broker | Redis (Celery) — scaffolded, not on dev critical path |
| Workers      | Celery (skeleton); in-process asyncio for dev |
| Environments | **uv**-managed venvs (`uv venv` + `uv pip install`) |
| Ship/export  | `.py` codegen + Docker image bundle (Dockerfile + requirements + script) |
| Auth         | Local accounts; PBKDF2-HMAC password hashing; HMAC-signed tokens |
| Secrets      | Fernet-encrypted credentials (`cryptography`) |
| Object store | S3-compatible (MinIO in docker-compose) — scaffolded |
| Observability| Prometheus text metrics, JSON health, system status |
| Deploy       | docker-compose (dev/single-node), Helm chart (Kubernetes) |
| Fonts        | Bricolage Grotesque (display), Hanken Grotesk (body), IBM Plex Mono |
| Theme        | Blue/black: warm-near-black bg, vivid blue accent (#4c9eff) |

---

## 4. Repository layout

```
D:\noodle\
  pyproject.toml          # uv workspace root
  .python-version         # 3.12
  README.md
  plan.md                 # ← this file

  apps/
    web/                  # React + React Flow editor (Vite)
      src/
        App.tsx           # routes
        main.tsx
        HomeHeader.tsx    # shared nav (Workflows / Envs / Credentials / Activity)
        Logo.tsx          # the wavy-noodle wordmark
        NodeIcon.tsx      # 34 SVG node-glyph icons
        api.ts
        categories.ts
        types.ts
        index.css         # tokens + base + Workflows/Envs/Creds/Activity styles
        editor.css        # editor-only styles
        WorkflowsPage.tsx
        EnvironmentsPage.tsx
        CredentialsPage.tsx
        ActivityPage.tsx
        EditorPage.tsx    # canvas + toolbar (Save/Run/Runs/Export/env/active)
        editor/
          store.ts        # zustand: graph, run state, pinned, ndvOpenId, ...
          Canvas.tsx      # ReactFlow wrapper
          NodeCard.tsx    # square icon tile + hover toolbar + status badges
          NodePalette.tsx
          NodeDetails.tsx # shared inspector body (params, run, pinned, webhook)
          Inspector.tsx   # right sidebar wrapper, resizable
          NodeDetailModal.tsx # NDV modal (double-click)

    api/                  # FastAPI server
      app/
        main.py           # lifespan: ensure global env, start scheduler
        config.py         # Settings via pydantic-settings
        db.py             # async engine + Base + get_session
        redis_client.py
        models.py         # ORM: Environment, Workflow, WorkflowVersion,
                          #      Run, NodeRun, User, Credential, AuditEvent,
                          #      PinnedData
        schemas.py        # Pydantic request/response
        routers/
          health.py       webhooks.py
          nodes.py        workflows.py
          environments.py runs.py
          export.py       auth.py
          credentials.py  audit.py
          ops.py          pinned.py
        services/
          venv.py         # uv venv build pipeline
          runner.py       # in-process execution + persist run + emit events
          events.py       # in-process pub/sub broker (WS source)
          triggers.py     # webhook dispatch + schedule tick loop
          crypto.py       # Fernet + PBKDF2 + HMAC tokens
          audit.py        # log_audit() helper
      alembic.ini
      alembic/
        env.py
        script.py.mako
        versions/
          0001_baseline.py
          0002_workflows.py
          0003_environments.py
          0004_runs.py
          0005_enterprise.py
          0006_pinned.py
      tests/              # 50+ API tests (httpx + sqlite + StaticPool)
        conftest.py       # patches venv/runner/triggers SessionLocal

    worker/               # Celery skeleton with a ping task
      worker/
        celery_app.py
        tasks.py
        config.py

  packages/
    core/                 # noodle (engine + SDK + models)
      noodle/
        __init__.py
        models.py         # Pydantic: ParamSpec, PortSpec, NodeManifest,
                          #          GraphNode, Edge, WorkflowGraph,
                          #          NodeRunResult, RunResult
        sdk.py            # @node decorator, NodeRegistry, manifest gen
        engine.py         # async DAG executor (event hook, cache, targets,
                          #                      override outputs, disabled,
                          #                      expression eval)
        expr.py           # {{ ... }} evaluator + _Attrible wrapper
      tests/              # engine + sdk + expr unit tests

    nodes/                # noodle_nodes — 46 built-in nodes (see §8)
      noodle_nodes/
        __init__.py
        builtin.py
      tests/

    runtime/              # placeholder (M4 productionization target)
    exporter/             # noodle_exporter: .py + Docker bundle codegen
      noodle_exporter/codegen.py

  deploy/
    docker-compose.yml    # postgres, redis, minio, api, worker, beat, web
    Dockerfile.python     # api + worker base image (uv)
    helm/noodle/          # Helm chart (api + worker + beat + web + ingress)

  docs/
    architecture.md
    deployment.md
    nodes.md
```

---

## 5. Data model

Six migrations have been applied; the dev SQLite at `apps/api/dev.db` is on
`0006_pinned`.

### `environments` (0003)
| col | type | notes |
|-----|------|-------|
| id | str pk | uuid4 hex |
| name | str(120) | |
| is_global | bool | exactly one row has true |
| python_version | str(16) | "3.12" default |
| packages | JSON | list of pip specs |
| status | str | pending / building / ready / error |
| status_detail | text | build log / error |
| created_at / updated_at | timestamptz | |

### `workflows` (0002, +environment_id in 0003)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| name | str(200) | |
| active | bool | drives webhook + schedule triggers |
| environment_id | str fk → environments | nullable |
| created_at / updated_at | timestamptz | |

### `workflow_versions` (0002)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| workflow_id | str fk CASCADE | |
| version | int | monotonic per workflow |
| graph | JSON | the WorkflowGraph dict |
| created_at | timestamptz | |

### `runs` (0004)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| workflow_id | str fk CASCADE | |
| workflow_version | int | |
| mode | str | manual / production |
| status | str | running / success / error |
| trigger_type | str | manual / webhook / schedule |
| started_at | timestamptz | |
| finished_at | timestamptz | nullable until done |

### `node_runs` (0004)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| run_id | str fk CASCADE | |
| node_id | str(120) | the graph node id |
| status | str | success / error / skipped |
| output | JSON | json-safe outputs dict |
| error | text | nullable |

### `users` (0005)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| email | str unique index | |
| password_hash | text | PBKDF2-HMAC-SHA256 200k rounds + salt |
| role | str | admin (only role used today) |
| created_at | timestamptz | |

### `credentials` (0005)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| name | str(120) | |
| type | str(40) | apiKey / httpAuth / oauth2 / generic |
| encrypted_data | text | Fernet of `json.dumps(data)` |
| created_at / updated_at | timestamptz | |

### `audit_events` (0005)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| action | str(40) | create / update / delete |
| target_type | str(40) | workflow / environment / credential |
| target_id | str(120) | |
| detail | text | |
| created_at | timestamptz | |

### `pinned_data` (0006)
| col | type | notes |
|-----|------|-------|
| id | str pk | |
| workflow_id | str fk CASCADE | |
| node_id | str(120) | |
| payload | JSON | engine outputs dict for that node |
| created_at / updated_at | timestamptz | |
| `uq_pinned_workflow_node` UNIQUE(workflow_id, node_id) | | |

---

## 6. API surface

All endpoints are unauthenticated today (see §10 — auth backend exists, gating
not yet enforced). Base under `/api` in the frontend (Vite proxy strips it).

### Health & ops
- `GET /health/live`
- `GET /health/ready` — checks Postgres + Redis
- `GET /system/status` — version, uptime, counts
- `GET /metrics` — Prometheus text

### Nodes
- `GET /nodes` → `NodeManifest[]` — palette source

### Workflows
- `GET /workflows` / `POST /workflows`
- `GET /workflows/{id}` / `PUT /workflows/{id}` / `DELETE /workflows/{id}`
- `GET /workflows/{id}/versions`
- Every save creates a new immutable WorkflowVersion (only when `graph` is
  provided in the PUT body); audit log records create/delete.

### Environments
- `GET /environments` / `POST /environments` / `GET /environments/{id}` /
  `DELETE /environments/{id}`
- `POST /environments/{id}/packages` body `{package}` — add + rebuild
- `DELETE /environments/{id}/packages/{package}` — remove + rebuild
- `POST /environments/{id}/rebuild`
- The "Global" env is auto-created in the lifespan startup if none exists.
- Builds shell out to `uv venv` + `uv pip install`; toggleable via
  `settings.enable_venv_builds` (off in tests).

### Runs
- `POST /workflows/{id}/run` body `{mode?, targets?}` → `{run_id}`
- `GET /workflows/{id}/runs` (last 50)
- `GET /runs/{id}`
- `WS /ws/runs/{run_id}` — live per-node events (broker replays buffered
  history on connect, so late subscribers get the full sequence)
- Dispatch seeds `cache` from `pinned_data` for the workflow before calling
  the engine.

### Webhooks
- `POST/GET/.../webhook-test/{path}` — capture for the editor's Listen panel
- `GET /webhook-test/{path}/last`
- `DELETE /webhook-test/{path}/last` — clear last capture (used by Listen)
- `POST/GET/.../webhook/{path}` — production trigger: finds active workflows
  whose graph starts with a matching webhook node, seeds the request into the
  trigger node's output via cache, dispatches a run.

### Export
- `GET /workflows/{id}/export.py` — standalone Python script
- `GET /workflows/{id}/export/docker` — zip with Dockerfile + workflow.py +
  requirements.txt + README.md

### Auth (backend present, not enforced)
- `POST /auth/register` `{email, password}` → `{token, user}`
- `POST /auth/login`
- `GET /auth/me` (requires `Authorization: Bearer <token>`)

### Credentials (encrypted at rest)
- `GET /credentials` — returns `{id, name, type, keys[], created_at,
  updated_at}` — never the values
- `POST /credentials` `{name, type, data: dict}` (data is encrypted)
- `PUT /credentials/{id}` `{name?, data?}`
- `DELETE /credentials/{id}`

### Audit
- `GET /audit` — last 100 events

### Pinned data
- `GET /workflows/{id}/pinned`
- `PUT /workflows/{id}/pinned/{node_id}` `{payload}` — upsert
- `DELETE /workflows/{id}/pinned/{node_id}`

---

## 7. Node SDK & engine

### Node model

A node is a plain Python function. The `@node` decorator builds a manifest
from its signature.

- **Input ports** — declared by `@node(inputs=[...])` (default `["input"]`).
  Function parameters whose names match are wired-data inputs. Triggers pass
  `inputs=[]`.
- **Config parameters** — every other function parameter. Edited in the
  inspector, never wired. Per-param UI metadata (choices, multiline,
  placeholder, description, **`key_value`**) goes in the decorator's `params`
  argument.
- **Outputs** — declared by `@node(outputs=[...])` (default `["main"]`).
  Multi-output nodes return a dict keyed by those names (omit a key to mark
  that branch as untaken).
- **`outputs_override` per GraphNode instance** — used by `switch` so the
  number of branches is dynamic per node.

Example:

```python
@node(
    name="HTTP Request",
    id="http_request",
    category="Transform",
    icon="globe",
    params={
        "url": {"placeholder": "https://api.example.com/data"},
        "method": {"choices": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "headers": {"description": "Add fields or switch to raw JSON.",
                    "key_value": True},
        "query":   {"key_value": True},
        "body":    {"key_value": True},
    },
)
def http_request(input=None, url="", method="GET",
                 headers=None, query=None, body=None):
    import requests
    response = requests.request(method, url, headers=headers or None,
                                params=query or None, json=body or None,
                                timeout=30)
    try:    return response.json()
    except ValueError: return response.text
```

### Engine features (`packages/core/noodle/engine.py`)

`async def execute(graph, registry, *, cache=None, targets=None, on_event=None)`

- **Topological DAG execution** — Kahn's algorithm; cycles raise `GraphError`.
- **Branching & skip propagation** — consumers of an untaken branch are
  marked skipped, and downstream of a skipped/errored node is skipped.
- **Partial execution via `cache`** — `{node_id: {output_name: value}}` skips
  re-execution and supplies the value. Used by pinned data and webhook
  request injection.
- **Targeted runs via `targets`** — execute only those nodes plus their
  ancestors not already cached. Used by the "Run from here" toolbar action.
- **`outputs_override`** on GraphNode for dynamic-output nodes (Switch).
- **Disabled passthrough** — a node with `disabled=True` is bypassed; the
  first wired input flows to the first output.
- **Live event hook** — `on_event(event)` is awaited with per-node
  `node_started` / `node_finished` events. Used by the in-process broker to
  drive WebSocket updates and per-node status badges on the canvas.
- **Expression evaluation** — before calling the node function, every string
  config param is walked and `{{ ... }}` blocks are evaluated against:
  - `$json`  — first wired input value
  - `$input` — `{port_name: value}` for every wired input
  - `$node`  — `{node_id: outputs_dict}` for every upstream node
  - `$now`   — UTC `datetime`
  Dotted access mirrors subscript access via an `_Attrible` wrapper, missing
  keys return None, errors become `[expr error: ...]` strings. Eval runs with
  a tiny safe-builtins allowlist (no `open`, no `__import__`, etc.).

### Built-in node library (`packages/nodes/noodle_nodes/`) — 46 nodes

**Triggers** (`inputs=[]`):
- `manual_trigger` (play) — emit a configured payload.
- `schedule_trigger` (clock) — interval (minutes/hours/days) + every + cron.
- `webhook_trigger` (webhook) — http_method, path, response_mode,
  response_code. The path drives both Test URL and Production URL.

**Logic:**
- `if` (branch) — outputs `["true", "false"]`. Operators: equals, not equals,
  contains, greater/less than, is empty / not empty / true.
- `switch` (switch) — **dynamic outputs**. `rules` is a key/value dict — each
  key becomes a branch handle, the value is matched against the input field.
  Always has a `fallback` output.
- `filter` (filter) — same operator set as If, filters a list.
- `merge` (merge) — `inputs=["input_a","input_b"]`; mode append/combine.

**Data:**
- `edit_fields` (pencil) — set/replace dict fields; `keep_only_set`.
- `sort` (sort) — by field, ascending/descending.
- `limit` (limit) — first/last N.
- `aggregate` (aggregate) — collect one field across all items.
- `remove_duplicates` (dedupe) — by field or whole item.
- `rename_keys` (tag) — `{old: new}` mapping.
- `join` (merge) — `separator.join(items)`.
- `split` (switch) — `str.split(separator, max_split)`.
- `length` (ruler) — `len(input)`.

**Transform:**
- `code` (code) — run Python; `input` in scope, assign to `output`.
- `http_request` (globe) — headers/query/body are KV-toggleable JSON.
- `datetime` (calendar) — current timestamp or strftime.
- `to_json` (braces) / `from_json` (import).
- `regex_extract` (regex) — `re.findall`.
- `regex_replace` (regex) — `re.sub`.
- `hash` (hash) — md5/sha1/sha256/sha512.
- `uuid` (tag) — uuid4 string.
- `base64_encode` (braces) / `base64_decode` (import).

**Utility:**
- `no_op` (dot).
- `wait` (pause) — sleeps up to 30 s.

**Integrations:**
- `slack_send_message` (message) — Slack chat.postMessage.
- `discord_send_message` (message) — Discord webhook message.
- `smtp_send_email` (mail) — send plain text email through SMTP/Gmail SMTP.
- `google_sheets_read` / `google_sheets_append` (sheet) — Sheets values API.
- `notion_create_page` (page) — create a Notion page/database item.
- `github_get_repo` / `github_create_issue` (github).
- `postgres_query` / `mysql_query` (database) — SQL query nodes; require
  `psycopg[binary]` / `PyMySQL` in the workflow environment.
- `s3_put_object` / `s3_get_object` (storage) — S3-compatible object storage;
  requires `boto3` in the workflow environment.
- `openai_chat` / `anthropic_message` (ai) — LLM API calls.
- `stripe_create_customer` (card).
- `airtable_list_records` / `airtable_create_record` (table).

34 SVG icons live in `apps/web/src/NodeIcon.tsx`. New nodes can reuse or add
to that set.

---

## 8. Frontend

### Pages (`apps/web/src`)

- **Workflows** (`/`) — card grid, create modal, hover-delete.
- **Environments** (`/environments`) — list/create custom envs, add/remove
  packages, rebuild, build-status pill, polls while building.
- **Credentials** (`/credentials`) — list cards with field names (never
  values), create modal with dynamic key/value rows, delete.
- **Activity** (`/activity`) — recent audit events.
- **Editor** (`/workflows/:id`) — see below.

### Editor (`EditorPage.tsx`)

Toolbar: home/logo → workflow name (inline-editable) → version + node count
| environment select → Active toggle → **Runs ▾** → **Export ▾** → ▶ Run →
Save (with dirty dot). Ctrl/⌘-S also saves.

Body: `[NodePalette] [ReactFlow Canvas] [Inspector]` inside
`ReactFlowProvider`. The inspector is **horizontally resizable** by dragging
its left edge (260 – 720 px).

Double-click any node → **NDV modal**. The modal header carries
the node title + Execute step / Disable / Delete / × close. Body reuses
`NodeDetails` with `showHeader={false}` to avoid duplicate titles.

### Node card (`NodeCard.tsx`)

A 72 × 72 rounded square tile with the icon centered, name below, category
color theme. State overlays:
- Run status pip top-right (running spinner / ✓ / ! / –) coloured by status.
- Disabled overlay (faded, ○ pip top-left).
- Pinned indicator (PIN badge bottom-right + accent ring) when pinned.
- Selected → accent ring + glow.

Floating hover toolbar above the tile:
- ▶ Run from here
- ⤢ Open NDV
- ◐ / ● Disable / Enable
- × Delete (with the "danger" hover state)

Output handles are derived per-instance from `data.outputsOverride ??
manifest.outputs` — Switch grows new handles as the user adds rules.

### Inspector content (`NodeDetails.tsx`)

Shared between the sidebar and the modal. Top-level sections:
- Header (icon + name + category + id + description) — skipped in modal.
- **Parameters** with the expression hint pinned to the top.
- **Last run** with the per-node output + "📌 Pin this output".
- **Pinned** (when present) with the frozen payload and Unpin.
- **Webhook URLs** (only on `webhook_trigger`): Test URL + Production URL
  with Copy + "Listen for test event" that clears the last capture and polls
  `/webhook-test/{path}/last` every 1.3 s until something arrives.

### Param fields (`NodeDetails.tsx`)

`ParamField` renders one of:
- `select` for `choices`,
- `field-toggle` for booleans,
- number input for integer/number,
- textarea for `multiline` strings, otherwise text input,
- `JsonField` for any/object/array,
- **`KeyValueField`** for params marked `key_value` — has a `Fields / Raw
  JSON` toggle. Fields mode is key/value rows; Raw JSON is a textarea. Switch
  either way preserves the data (round-trips between dict and JSON text).

### Editor store (`editor/store.ts`)

Zustand store. Key slices:
- **Graph**: `nodes`, `edges`, `manifests`, `manifestsById`, `selectedId`,
  `dirty`, `ndvOpenId`. `loadGraph` / `toGraph` translate to/from the API's
  WorkflowGraph (including `disabled` and `outputs_override`). The store also
  derives switch outputs from `params.rules` and prunes stale edges when a
  branch is removed.
- **Run state**: `runId`, `running`, `runStatus`, `runOutputs`, `runError`.
  `applyRunEvent` consumes WebSocket events; `applyRunInfo` loads a past run
  from the API.
- **Workflow context**: `workflowId`, `pinned` map. `setPinnedFor`.
- **Action plumbing**: `setRunHandler` lets the EditorPage register its
  `run()` so NodeCard's "Run from here" can dispatch without prop-drilling.

---

## 9. What ships today

The M0 → M8 plan is delivered as the self-hostable core, plus polish.

| Milestone | Status | What's in it |
|-----------|--------|--------------|
| **M0 Foundation** | ✅ | uv workspace, docker-compose dev stack, FastAPI skeleton, Alembic baseline, React app shell, CI scaffolding. |
| **M1 Node SDK + engine** | ✅ | `@node` decorator, manifest generation, async DAG engine with branching, partial execution, targeted runs, cycle detection. 46 built-ins. |
| **M2 Editor UI** | ✅ | React Flow canvas, palette, inspector, workflow CRUD + versioning. |
| **M3 Environments** | ✅ | `environments` table; `uv` build pipeline; UI to create envs, add/remove packages, rebuild; per-workflow env selector. |
| **M4 Execution & test mode** | ✅ (in-process) | Run/NodeRun tables; in-process executor; WebSocket live events; Run button; live node status badges; per-node output in inspector; Execute Step / run-from-here. |
| **M5 Triggers** | ✅ (in-process) | Webhook ingress dispatching real runs (request seeded via cache); in-process schedule loop. |
| **M6 Export & ship** | ✅ | `.py` codegen (round-trip-tested) + Docker bundle (zip download). |
| **M7 Enterprise** | ✅ (backend only) | Fernet-encrypted credentials (never expose values), audit log, local auth (register/login/JWT-like tokens). |
| **M8 Ops** | ✅ | Prometheus metrics, system status, Helm chart, architecture/deployment/nodes docs. |

### Polish features added on top of M0–M8

- **Square icon-tile node cards** with name below, status badges, hover
  toolbar (run / open / disable / delete).
- **NDV modal** on double-click — node-detail view.
- **Resizable inspector**.
- **Webhook Listen mode** — clears and polls for a real captured request.
- **Key/value editor with raw-JSON toggle** for HTTP Request headers / query
  / body (and any node that opts in via `key_value: True`).
- **Dynamic Switch branches** — outputs grow per instance; removed branches
  prune their edges.
- **Disabled-node passthrough** in the engine.
- **9 stdlib-only nodes** added: join, split, length, regex_extract,
  regex_replace, hash, uuid, base64_encode, base64_decode.
- **Expression evaluation** in string params with `$json`, `$node`, `$input`,
  `$now` aliases.
- **Pinned data** — UI pin/unpin per node; run dispatch seeds the engine
  cache from pinned rows.
- **Runs dropdown** in the toolbar — view recent runs, click to replay onto
  the canvas.
- **Theme** — switched from amber/brown to blue/black (vivid blue accent on
  warm-near-black, cool category colours).
- **Inspector deduplication** — node title only appears once (sidebar drops
  the generic "Inspector" header when a node is selected; NDV body drops its
  duplicate title via `showHeader={false}`).

### Tests

74 passing across:
- `packages/core/tests/` — engine, sdk, expressions
- `packages/nodes/tests/` — built-in node behaviour
- `packages/exporter/tests/` — round-trip script + docker bundle
- `apps/api/tests/` — health, nodes, workflows, environments, runs, triggers,
  export, credentials/auth/audit (enterprise), pinned, ops

Lint is `ruff` clean. Web `tsc` + `vite build` clean.

---

## 10. Gaps & honest scope notes

These are deliberate trade-offs to keep the dev loop simple and the autonomous
delivery on schedule. None of them are blockers for the demo; all of them are
the **next pass**.

### Execution backend
- **Subprocess runner is now implemented** as an opt-in backend. Set
  `USE_SUBPROCESS_RUNNER=true` and the API dispatches every run to a
  long-lived `noodle_runtime` subprocess per environment, spawned with that
  env's venv Python. The protocol is newline-delimited JSON over stdin /
  stdout (events carry the originating ``request_id``); the pool lives in
  `apps/api/app/services/runtime_pool.py`. Each subprocess is serialized via
  an `asyncio.Lock`, so concurrent runs to the same env queue while different
  envs execute in parallel. Venv builds now also install the noodle workspace
  packages (`packages/core`, `packages/nodes`, `packages/runtime`) so the
  subprocess can import the engine + the built-in node library.
- **Sub-workflow RPC works across the subprocess boundary.** When an
  `execute_workflow` node runs inside a subprocess, the runtime emits a
  `call_workflow` event on stdout; the pool catches it, invokes the host
  side `_call_sub_workflow` (with the host's `call_chain` contextvar set so
  cycle detection works across subprocess hops), and writes the result back
  to the subprocess via a `call_workflow_response` message. Concurrent
  callbacks run as tasks and a per-process write lock keeps stdin bytes
  from interleaving. Verified live with parent input 9 → sub-workflow
  doubled to 18 through the subprocess.
- **Celery worker is a skeleton.** The dispatch path (`services/runner.py`)
  is in-process via `asyncio.create_task`. Production should:
  1. Make the runner a Celery task,
  2. Workers maintain a warm runner subprocess pool keyed by env image (the
     pool implementation in `services/runtime_pool.py` is reusable),
  3. Dispatch a job to the right subprocess for the workflow's env.
- **Schedule triggers loop in-process** (asyncio sleep + tick every 30 s).
  Production target is Celery Beat.

### Auth & multi-tenancy
- **Auth gating is implemented** as an opt-in. `settings.auth_required`
  (env `AUTH_REQUIRED`) is `false` by default. When it's true, the FastAPI
  middleware in `app/main.py` rejects any request without a valid Bearer
  token, except for the always-open paths needed for sign-in and external
  triggers: `/auth/*`, `/health/*`, `/webhook/*`, `/`, `/metrics`,
  `/system/status`. The frontend bootstraps via `GET /auth/required`; when
  it sees `auth_required: true` and no token, it renders `LoginPage.tsx`
  (email + password, sign-in / register toggle) and stores the token in
  `localStorage`. `api.ts` attaches the token as `Authorization: Bearer`
  on every request and, on a 401, clears the token and re-renders the
  login screen. `HomeHeader` shows the signed-in user's email and a sign
  out button when a user is present.
- **Multi-tenancy is not implemented.** There is no `orgs` / `workspaces`
  table, and queries are global. The plan's Org → Workspace → Workflow
  hierarchy with per-row tenant scoping is the work.
- **RBAC is not enforced.** Users have a `role` column ("admin" by default)
  but no role checks exist.
- **SSO (OIDC/SAML)** via Authlib is not implemented.

### Hardening
- **OpenTelemetry tracing** — not started.
- **Rate limiting / per-tenant quotas** — not started.
- **Real sandboxing** (containers per run, gVisor) — explicitly deferred per
  the plan; needed only before multi-tenant SaaS.

### Editor polish that isn't done yet
- **Sub-workflows** — an "Execute Workflow" node that calls another workflow
  by id and returns its result. The runner already supports cache + targets;
  this is a node + small UI work.
- **Expression "fx" badges** on fields whose value contains `{{` — only the
  top-of-section hint exists today.
- **Hover preview / input-output split** in NodeCard — a side-by-side
  input/output panel; we only have the "Last run" section in the inspector
  today.
- **Sortable / draggable Switch branches** — keys are just dict order today.
- **Partial-execution visual hints** — when targets exclude part of the
  graph, the run nodes show status but the skipped nodes show nothing
  visible.

---

## 11. Roadmap — recommended next pass

Loosely ordered by impact / unblock-power:

1. **Real per-env subprocess execution.** Implement
   `packages/runtime/noodle_runtime/server.py` as a long-lived process that
   loads the engine + nodes from its venv and serves run requests over a
   socket. Worker pool owns one process per env image; dispatch routes by
   `workflow.environment_id`. This unlocks isolation and makes packages
   installed per-env actually available to nodes.
2. **Sub-workflows.** Add an `execute_workflow` built-in node:
   - Config params: `workflow_id` (choices from `/workflows`), `inputs` (KV).
   - At run time, the node looks up the target workflow's latest graph,
     calls `start_run` (or the engine directly, in-process for the same env
     to avoid recursion explosions), waits for the result, returns it.
   - Cycle detection across workflows is the gotcha — track the chain in the
     cache key.
3. **Auth gating + multi-tenancy.** Smallest credible step:
   - `settings.auth_required: bool = False` plus a `RequireUser` dependency.
   - Frontend: login page, token storage, attach `Authorization: Bearer …`.
   - Then add `orgs` + `memberships` tables and scope every workflow /
     environment / credential query by membership.
4. **Celery + Beat productionization.** Once #1 and #3 land, move the
   dispatch + scheduler off the API process so the API can horizontally
   scale.
5. **Editor UX polish:**
   - "fx" badge on string fields whose value contains `{{`.
   - Hover/input preview panel on selected node (NDV side-by-side).
   - Reorderable Switch branches (drag the KV rows).
   - "Partial execution" indicator showing which nodes were targeted.
6. **OpenTelemetry + rate limits + container-per-run isolation** — the M8
   hardening items, plus a Helm chart for HPA on the worker pool.

---

## 12. Local development

```sh
# 1. Install deps (uv workspace)
cd D:\noodle
uv sync --all-packages

# 2. Bring up infra (postgres / redis / minio) OR skip and use SQLite
docker compose -f deploy/docker-compose.yml up -d postgres redis minio

# 3. Migrate
#    Dev SQLite:
$env:DATABASE_URL="sqlite+aiosqlite:///./dev.db"
cd apps/api && uv run alembic upgrade head && cd ../..
#    Postgres:
DATABASE_URL=postgresql+asyncpg://noodle:noodle@localhost:5432/noodle \
  uv run alembic upgrade head -c apps/api/alembic.ini

# 4. Start API (dev)
cd apps/api
$env:DATABASE_URL="sqlite+aiosqlite:///./dev.db"
$env:CORS_ORIGINS="http://localhost:5173"
uv run uvicorn app.main:app --port 8000 --reload

# 5. Start web
cd apps/web
npm install
npm run dev   # http://localhost:5173

# 6. Run tests
cd D:\noodle
uv run ruff check .
uv run pytest

cd apps/web
npm run typecheck
npm run build
```

The dev API uses SQLite, no Redis required, no Celery worker required. The
schedule loop runs inside the API process; the webhook ingress and run
dispatch are all in-process.

The Vite dev proxy maps `/api` → `http://localhost:8000` and proxies `/ws`
with `ws: true` (set `VITE_API_PROXY` to override).

### Useful env vars (`app/config.py`)
| var | default | notes |
|-----|---------|-------|
| `DATABASE_URL` | `postgresql+asyncpg://noodle:noodle@localhost:5432/noodle` | use `sqlite+aiosqlite:///./dev.db` in dev |
| `REDIS_URL` | `redis://localhost:6379/0` | only used by `/health/ready` and Celery skeleton |
| `CORS_ORIGINS` | `http://localhost:5173` | comma-separated |
| `ENVS_DIR` | `./envs` | where `uv venv` writes per-env venvs |
| `ENABLE_VENV_BUILDS` | `true` | tests set this false |
| `RUN_SYNCHRONOUSLY` | `false` | tests set this true (deterministic, no polling) |
| `SECRET_KEY` | dev placeholder | encrypts credentials + signs tokens — change in prod |

---

## 13. Files most worth reading first

If you're picking this up cold, in this order:

1. `plan.md` (this file)
2. `packages/core/noodle/engine.py` — the execution model in 200 lines
3. `packages/core/noodle/expr.py` — the expression evaluator
4. `packages/core/noodle/sdk.py` — `@node` and the manifest generator
5. `packages/nodes/noodle_nodes/builtin.py` — every shipped node
6. `apps/api/app/services/runner.py` — how a run is dispatched and persisted
7. `apps/api/app/services/triggers.py` — webhook + schedule dispatch
8. `apps/api/app/routers/runs.py` — run/run-list/WS endpoints
9. `apps/web/src/editor/store.ts` — frontend single source of truth
10. `apps/web/src/editor/NodeDetails.tsx` — KV field, expression hint, pin UI
11. `apps/web/src/EditorPage.tsx` — toolbar + run wiring + WebSocket
12. `apps/web/src/editor/NodeCard.tsx` — square tile + status + toolbar
13. `apps/api/alembic/versions/0006_pinned.py` (and 0001 → 0006 in order) —
    the full schema evolution

Original M0–M8 design doc:
`C:\Users\harry\.claude\plans\supposre-i-want-to-dynamic-kay.md` (the
pre-implementation plan; this file supersedes it for current state).
