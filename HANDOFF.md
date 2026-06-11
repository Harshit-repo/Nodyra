# Noodle — Session Handoff & Compact Architecture

> **Purpose:** drop-in context for a fresh Claude session to continue work on
> Noodle. Captures architecture, current state, what's uncommitted, and where
> to pick up. See `plan.md` for the full milestone history and `docs/architecture.md`
> for deeper reference.

**Date:** 2026-05-27
**Branch:** `feat/orchestration-upgrades`
**Working dir:** `D:\noodle` (Windows 11, PowerShell + Bash via WSL/Git Bash)
**Framing rule:** Noodle's scope > n8n. Do not frame designs as "n8n-faithful".

---

## 1. What Noodle is

Self-hostable, **Python-native** workflow automation platform. Every node is a
plain Python function declared via the `@node` SDK. Users build workflows on a
React Flow canvas; runs execute in warm Python subprocesses bound to per-user
environments (uv-managed venvs).

**Differentiators vs n8n:** real Python nodes (not JS sandboxes), uploadable
`.py` code modules that auto-register as nodes, env-scoped dependencies, AST
starter-graph generation, artifacts as first-class, AI workflow drafts that
produce *editable* graphs (no hidden agent execution).

---

## 2. Repo layout

```
D:\noodle\
├── apps/
│   ├── api/        FastAPI backend + Alembic + SQLAlchemy
│   │   ├── app/{routers,services,models,schemas,security,config}.py
│   │   ├── alembic/versions/0001..0016_*.py
│   │   └── tests/test_*.py            (165 tests)
│   └── web/        React 18 + Vite + React Flow + TS
│       └── src/{editor,*Page.tsx,api.ts,types.ts,store.ts}
├── packages/
│   ├── core/       noodle.engine, noodle.sdk, noodle.models, noodle.artifacts
│   ├── nodes/      noodle_nodes.* — builtin + brand-icon HTTP nodes
│   └── runtime/    noodle_runtime — subprocess runner protocol
├── deploy/         docker-compose.yml + .env (gitignored)
├── plan.md         Long-form milestone history (M0–M8 + Slices 1–22)
└── HANDOFF.md      THIS FILE
```

`uv` workspace manages all Python packages. Frontend uses npm.

---

## 3. Architecture at a glance

### Data flow

```
User ──UI──▶ FastAPI (apps/api)
                │
                ├─ Triggers/Scheduler ──▶ runner.start_run
                │                              │
                │                              ▼
                │                       RuntimePool ──per-env──▶ subprocess
                │                              │                      │
                │                              ▼                      ▼
                │                       _RuntimeProcess         noodle_runtime
                │                              │                      │
                │                              └──────WebSocket◀──────┘
                ▼                                         events
            PostgreSQL / SQLite (dev.db)
```

### Key services (`apps/api/app/services/`)

| Service | Role |
|---|---|
| `runner.py` | Orchestrates a run: load graph, gather code modules, resolve credentials, dispatch via `runtime_pool`, persist `NodeRun`s + logs/timing/artifacts. Builds the per-run `SubworkflowMeta` (`subworkflows.meta_for_root_run`) — production-mode runs always force `published` sub-workflows via `use_published`. |
| `subworkflows.py` | A3 host resolver (`resolve_subworkflow`): child graph lookup (draft vs published from `call.use_published`), credential resolution, trigger seeding, child `Run` rows (`mode="subworkflow"`, `parent_run_id`), inline-vs-spawn decision. Cycle/depth semantics live in `noodle.engine.subworkflows`, not here. |
| `runtime_pool.py` | `_EnvPool(min_size, max_size)` per env. Three presets (Fixed/Elastic/Spawn-per-run) all driven by one rule: `release(): if min_size==0, close worker`. Reaper respects min floor. Global `max_concurrent_runs` semaphore at top-level dispatch only (subworkflows bypass to avoid deadlock). |
| `triggers.py` | DB-backed in-process scheduler. `_is_due()` consults workspace `app_timezone` + per-trigger `timezone` override. `dispatch_webhook(path, payload) -> (run_ids, any_path_matched)` — 401 when path matched but auth failed. |
| `credentials.py` + `redaction.py` | Encrypted-at-rest credential store, deterministic resolution (workflow → env → runner_pool → global). `_resolve_ref` returns whole dict when key=="*" (multi-field credentials). |
| `live_settings.py` | 5s TTL overlay of singleton `system_settings` row onto `config.settings`. Hot-reload for retention, output caps, reaper threshold. |
| `retention.py` | Age + per-workflow count prune; deletes artifacts on the way out. |
| `artifacts.py` | Local-FS artifact store; refs travel as JSON envelopes. |
| `venv.py` | uv-driven env builds; post-build RSS sample via psutil → `worker_rss_estimate_bytes`. |
| `starter_graph.py` | AST-walks uploaded modules → wired DAG with literal args as defaults. |
| `ai_builder.py` | Deterministic registry-safe AI graph drafts (no LLM yet in v1). |
| `credential_tests.py` | Read-only test endpoints per cred type. AWS requires explicit keys (no IAM role probing). |

### Engine (`packages/core/noodle/`)

- `engine.execute(graph, cache, targets)` — topo DAG, context-local log capture (no global `sys.stdout` mutation), retry backoff/jitter, optional `timeout_seconds` via `asyncio.to_thread + wait_for`.
- `GraphNode` carries optional `retry_wait_seconds`, `retry_backoff`, `timeout_seconds`, `always_output_data` (backward-compat).
- `sdk.discover_module_function_manifests` (AST, never executes) for previews; `register_module_functions` (exec) only inside runtime subprocess.
- Typed serialization envelopes for `DataFrame`, `datetime`, `Decimal`, `tuple`, `set`, `bytes` at WS/storage boundaries. **No pickle.**

### Runtime (`packages/runtime/`)

- `noodle_runtime/server.py` — stdio JSON-line protocol.
- Messages: `ready`, `register {modules}`, `run {graph, cache, workflow_modules}`, `run_started`, `node_started`, `node_finished`, `run_finished`.
- Lives inside the env's interpreter so `import pandas` resolves correctly.

### Nodes (`packages/nodes/noodle_nodes/`)

- `builtin.py` — triggers (manual/webhook/schedule), edit_fields, code, switch, sub-workflow.
- `integrations.py` — OpenAI Chat, Anthropic, Slack, GitHub, etc.
- `communication.py`, `saas.py`, `ai_extra.py`, `storage.py`, `cloud_devops.py` — HTTP/SDK wrappers per service.
- `system.py` — `execute_command` (cross-platform: auto/bash/sh/powershell/cmd).
- `transform_extra.py` — gzip, xml, html, csv, jq-lite, etc.
- `_creds.py` — `cred_single()` / `cred_multi()` helpers. Multi-field credentials carry `key="*"` and resolve to the whole dict.

---

## 4. Shipped slices (one line each)

1. Per-node logs (context-local capture) + timing + retry backoff/jitter + timeout.
2. Parallel runs per env via semaphore-gated warm pool. Subworkflows bypass cap.
3. **3a** DB-backed scheduler + real cron (`croniter`). **3b** Optional Celery Beat (Linux).
4. Deployments (workflow+schedule+default params+active) + retry-from-failed-node.
5. User code modules — AST preview + exec only at run time. Starter-graph generator.
6. `/executions` system-wide runs view with live WS streaming.
7. Run retention + per-output size cap + typed serialization envelopes.
8. *(not started)* Remote runners / worker pools.
9. Artifacts with local FS backend + retention cascade.
10. Credentials v2 — scoped (workflow→env→pool→global), encrypted, redacted, credential refs in graph JSON.
11. Versioned releases — `draft_graph` vs `workflow_versions`; deployments pin a version.
12. Error workflows — failure payload + recursion guard via `triggered_by_error_run_id`.
13. AI workflow builder — deterministic graph drafts, no hidden execution path.
14. UI polish — credential tests, latest-run chips, onboarding templates, Run-fresh action.
15. Login + RBAC (viewer<editor<admin<owner), first-user-becomes-owner setup flow.
16. Workspace + per-env settings surface (`system_settings` singleton + `live_settings`).
17. Elastic per-env pools (Fixed/Elastic/Spawn-per-run) + worker RSS estimate + soft warning.
18. *(designed only)* Restart API button (owner-only graceful SIGTERM).
19. Execute Command node (cross-platform shell).
20. Cron trigger timezone dropdown + `<TimezoneSelect />` using `Intl.supportedValuesOf('timeZone')`.
21. Webhook trigger auth layer (None / Basic / Header / Query) — 401 vs 404 disambiguation.
22. Multi-field credentials — single "Credentials" picker per node via `cred_single` / `cred_multi`.

Slices 8 (remote runners) and 18 (restart button) are the main unshipped pieces.

---

## 5. Current uncommitted state (2026-05-27)

Branch `feat/orchestration-upgrades` has **99 dirty files** spanning slices 17–22 plus polish. Last working session completed Slice 22 credential migration. **All tests green:**

- `uv run pytest packages/core/tests apps/api/tests packages/runtime/tests` → **165 passed, 1 skipped**
- `uv run pytest packages/nodes/tests` → **86 passed, 1 skipped**
- `uv run ruff check packages/nodes apps/api/app` → clean
- `apps/web` `npm run typecheck && npm run build` → clean

Migrations applied to `apps/api/dev.db`: through `0016_environment_runner_pool_max`.

**Untracked new files (representative):**
```
apps/api/alembic/versions/0013_production_slices.py
                          0014_user_profile_fields.py
                          0015_env_and_system_settings.py
                          0016_environment_runner_pool_max.py
apps/api/app/routers/system_settings.py
apps/api/app/security.py
apps/api/app/services/{ai_builder,credential_tests,credentials,live_settings,redaction}.py
apps/api/tests/test_{credentials_v2,releases_errors_ai,runtime_pool_elastic,settings_endpoints}.py
apps/web/src/{SecurityPage,SettingsPage,ToastProvider}.tsx
apps/web/src/editor/fields/        ← TimezoneSelect lives here
apps/web/src/theme.ts
packages/nodes/noodle_nodes/{_creds,ai_extra,cloud_devops,communication,saas,storage,system,transform_extra}.py
packages/nodes/tests/test_{execute_command,new_node_registration,transform_extra}.py
```

---

## 6. Pending work

**Immediate (when you next have user instruction):**
1. **Commit the slice 17–22 bundle.** User asked previously to "commit and restart docker"; session limit hit before commit. Suggested split: one commit per slice or one bundled commit with all of 17–22 — confirm with user.
2. **Restart Docker stack** (`deploy/docker-compose.yml`) so production picks up new code + migration 0016. `deploy/.env` must hold a 64-hex `INTERNAL_API_TOKEN` (gitignored).
3. Browser smoke tests for Slice 17 env modal (mode radio + RAM estimate), Slice 20 timezone dropdown, Slice 21 webhook auth, Slice 22 credential picker.

**Designed but not built:**
- **Slice 8** — Remote runners / worker pools (outbound-WS runner agent + dispatcher).
- **Slice 18** — Owner-only restart-API button (`POST /admin/restart` + health-poll overlay).

**Out-of-scope follow-ups noted in slice 17:**
- Hot-resize semaphore (avoid restart for pool changes).
- Per-env override of `max_concurrent_runs`.
- Per-workflow concurrency cap.
- Global-semaphore over-count fix (env-blocked runs holding global slots).

---

## 7. Conventions & gotchas

- **Migrations:** new column? new file in `apps/api/alembic/versions/` named `00XX_*.py`. SQLite `ADD COLUMN` is fine for nullable. Run `alembic upgrade head` against `apps/api/dev.db`.
- **Engine backward-compat:** any new `GraphNode` field MUST be optional with default — exported scripts and the runtime depend on it.
- **No pickle. Ever.** Use `serialization.py` envelopes for cross-process values.
- **Secrets at boundaries only.** Decrypt in `runner._execute_run` right before dispatch; everything downstream (events, logs, persisted output) flows through `redaction.py`.
- **Sub-workflows bypass the global concurrency cap.** Wrapping them deadlocks parents holding the only slot. (Unchanged by A3 — `dispatch_subworkflow` and the in-process child path both skip `global_slot()`.) Sub-workflow *semantics* — cycle detection, depth limits, inline-child execution, leaf extraction — live in `noodle.engine.subworkflows`; hosts (API, runtime subprocess, remote agent, exporter) supply a `SubworkflowRunner` resolver.
- **Sub-workflows respect the run's `use_published` flag** (carried in `SubworkflowMeta`/`SubworkflowCall`, set from the root run's mode). Editor manual runs propagate draft; webhook/schedule/deployment/error runs force published. Don't break this.
- **Frontend brand icons:** any node icon prefixed `brand:<slug>` resolves to `https://cdn.simpleicons.org/<slug>` via `NodeIcon.tsx`. Use real slugs.
- **Credentials in node params:** declare via `cred_single(type, key, label)` for single-field or `cred_multi(type, label, [fields])` for multi-field. Multi sets `key="*"`; the resolver returns the whole dict.
- **Webhook auth:** `dispatch_webhook` returns `(run_ids, any_path_matched)` — router maps `any_path_matched and not run_ids` → 401, no matches → 404.
- **Windows shell quirks:** PowerShell here. Use `$null`, `$env:VAR`, backtick for line continuation. Bash via `Bash` tool is also fine.
- **Auth surface:** `_REQUIRES_AUTHENTICATED = {user:manage, system:restart}` always demand a signed-in actor even when `auth_required=False`.

---

## 8. Common commands

```bash
# Backend
uv sync --all-packages
uv run pytest packages/core/tests apps/api/tests packages/runtime/tests
uv run pytest packages/nodes/tests
uv run ruff check packages/nodes apps/api/app
cd apps/api && uv run alembic upgrade head

# Frontend
cd apps/web && npm install
npm run dev          # vite dev server
npm run typecheck    # tsc --noEmit
npm run build        # production bundle

# Docker (deploy/.env must exist with INTERNAL_API_TOKEN)
cd deploy && docker compose up -d --build
docker compose logs -f api

# Dev API outside Docker
cd apps/api && uv run uvicorn app.main:app --reload
```

---

## 9. Where to look first

| Question | File |
|---|---|
| How does a run get dispatched? | `apps/api/app/services/runner.py` `_execute_run` |
| How are credentials resolved? | `apps/api/app/services/credentials.py` `resolve_credential_refs` + `_resolve_ref` |
| How does the scheduler decide to fire? | `apps/api/app/services/triggers.py` `_is_due` |
| How are env pools sized? | `apps/api/app/services/runtime_pool.py` `_resolve_pool_sizes` + `_EnvPool` |
| What does a graph node look like? | `packages/core/noodle/models.py` `GraphNode` |
| Where is the trigger-type list? | `apps/api/app/services/runner.py` `TRIGGER_TYPES` |
| How does a workflow get its graph? | `apps/api/app/services/subworkflows.py` `_load_workflow_graph` (respects `use_published` from the call) |
| How does the inspector show a credential field? | `apps/web/src/editor/NodeDetails.tsx` `CredentialParamField` |
| Where does the timezone dropdown render? | `apps/web/src/editor/fields/TimezoneSelect.tsx` (consumed in NDV) |

---

## 10. Open questions / decisions to make next

1. **Commit strategy for slices 17–22 bundle** — one commit per slice (clean history) vs one big "slices 17–22" commit (faster). User leaning toward bundle.
2. **Slice 18 vs Slice 8 priority** — restart button is a 1-day slice closing the "restart required" UX loop. Remote runners is multi-week. Recommend 18 first.
3. **AI builder upgrade** — current Slice 13 is deterministic registry-only. User may want real LLM planning (OpenAI/Anthropic) with server-side validation pass.
4. **OAuth2 connect flows** — Slice 14 left this as manual access-token only. Full browser flow is future work.
5. **Hot-reload pool sizing** — currently requires restart. Could become Slice 18.5 once restart button exists.

---

## 11. Things NOT to do

- Don't reintroduce inline `api_key`/`token`/`password` params — use `cred_single` / `cred_multi`.
- Don't read graphs as `workflow.draft_graph or latest.graph` in production paths — that's the Slice 11 sub-workflow leak. Use `subworkflows._load_workflow_graph`, which honors the call's `use_published` flag.
- Don't add a global `sys.stdout` redirect for log capture — use the engine's context-local stream installer.
- Don't bake `INTERNAL_API_TOKEN` into docker-compose. The `${VAR:?...}` form is intentional.
- Don't ship a new GraphNode field without a default — exported scripts will break.
- Don't add `--no-verify` / `--no-gpg-sign` to git commits unless user explicitly asks.
- Don't claim work is "done" without running pytest + ruff + tsc + build and showing the output.
