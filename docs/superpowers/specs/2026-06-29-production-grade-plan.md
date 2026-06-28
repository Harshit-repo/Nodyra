# Noodle Production-Grade Implementation Plan

**Date:** 2026-06-29
**Branch:** `fix/backend-production-readiness-p0-p1`
**Status:** Spec — awaiting user review before implementation
**References:** `NOODLE_PRODUCT_FUNCTIONALITY_AND_AI_STRATEGY_ASSESSMENT.md`, `NOODLE_ARCHITECTURE_ENGINE_ASSESSMENT.md`, `docs/audit-2026-06-24.md`

---

## 1. Current State

### 1.1 What's Already Fixed (19 of 22 audited P0/P1 issues)

This branch (`fix/backend-production-readiness-p0-p1`) has already resolved the vast majority of critical issues identified in the 2026-06-24 audit:

| Category | Fixed | Details |
|----------|-------|---------|
| **Engine** | E-01, E-03, E-04, E-05, E-06, E-07, E-08 | Sandbox hardened (comprehensive blocked modules — `os`, `socket`, `subprocess`, `urllib`, `pathlib`, `http`, `ctypes`, `multiprocessing`, `signal`, `sys`, `importlib`, `inspect` + 25 more). Cancellation path. DNS rebinding. venv SSRF. Cloud runner tokens. Prompt injection defaults. Remote sub-workflow cycle detection. |
| **API Auth** | B-02, B-03, B-04, B-05, B-06, B-07 | MCP org isolation. Cancel run org check. Unauthenticated list endpoints. Auth dependency flush. Remote run filter on restart. OAuth introspection cache + circuit breaker. |
| **Database** | D-01, D-02, D-03, D-04, D-05 | Dedup key UNIQUE constraint. Workflow versions UNIQUE. GitHub sync RLS on both tables. Encrypted webhook secret (DEK/KEK). |
| **Deployment** | V-01, V-02, V-05, V-09 | Production nginx Dockerfile. Baked secrets removed. Liveness + readiness probes. Rolling update strategy + terminationGracePeriodSeconds. |
| **Frontend** | F-02, F-03, T-02 | marked.parse async:false. Canvas drag AbortController. Canvas store tests. |
| **CI** | T-01 | Frontend tests run in CI. |

### 1.2 What's Still Broken (2 issues)

| ID | Issue | File | Fix |
|----|-------|------|-----|
| **E-02** | CodeExecToolAdapter uses raw `subprocess.Popen` instead of ProcessPoolExecutor | `packages/nodes/noodle_nodes/ai_v2/agent_tools.py:285` | Route through `PooledProcessIsolator.run()` like the Code node |
| **B-01** | `_load_run_and_workflow` uses `session.get()` bypassing org filter | `apps/api/app/routers/runs.py:279` | Replace with `select(Run).where(Run.id == run_id)` |

### 1.3 What's Missing (P1 — Not P0)

These are features that differentiate a compelling product from a functional prototype:

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 1 | **Durable execution checkpoints** — best-effort reconstruction exists but wrapped in `except: pass` | Server restart = lost progress | Large |
| 2 | **Python SDK + CLI** — no `pip install noodle-client` | Can't use from scripts/CI/terminal | Large |
| 3 | **Guided onboarding** — no tutorial for first-time users | < 30% feature discovery | Medium |
| 4 | **Multi-turn AI refinement** — single-turn generation only | Can't iterate conversationally | Medium |
| 5 | **Workflow test generation by AI** — no automated test data | No confidence in AI-generated workflows | Medium |
| 6 | **Standalone "explain workflow" endpoint** | Only available in generation context | Low |
| 7 | **Typed code node I/O** — Code node uses `Any` | No contract, no validation | Medium |
| 8 | **Node descriptions in palette** — no tooltip on hover | Must drag to discover | Low |
| 9 | **Expression autocomplete** — no help for `{{ }}` syntax | Syntax undiscoverable | Medium |
| 10 | **CI security scans blocking** — Postgres lane + pip-audit still advisory | Regressions can merge | Small |

---

## 2. Why a CLI? (Strategic Justification)

A CLI is not a P0 for beta. It IS a P1 for public launch. Here's the reasoning:

### 2.1 The Positioning Problem

Without a CLI, Noodle is a **web application with Python inside it**. With a CLI, Noodle is a **Python platform with a visual editor**. The difference is existential for the target persona (Python developers).

### 2.2 Concrete Use Cases Blocked Without CLI

| Use Case | Without CLI | With CLI |
|----------|------------|----------|
| CI/CD integration | Manual API curl with token management | `noodle run workflow-123 --data '{"user_id": 42}'` |
| Bulk operations | Click through UI for each workflow | `noodle export --all --format script > backup/` |
| Script automation | Can't script anything | `for wf in $(noodle list --tag prod); do noodle run $wf; done` |
| Local dev loop | Push code module through web UI | `noodle push my_nodes.py --watch` |
| GitOps | Manual publish in UI | `noodle deploy --from-git HEAD` in CI |
| First impression | "Sign up, load a web app, figure out canvas" | `pip install noodle-client && noodle create "Webhook → Slack"` |

### 2.3 Competitive Reality

- **n8n**: Has `n8n-node-dev` CLI for node development, no workflow CLI
- **Windmill**: Has `wmill` CLI — `wmill sync`, `wmill push`, `wmill run` 
- **Prefect**: Has `prefect` CLI — extensive
- **Airflow**: Has `airflow` CLI — extensive
- **Temporal**: Has `temporal` CLI — extensive

Every serious Python orchestration tool has a CLI. Noodle must too.

### 2.4 Recommendation

**Ship beta without CLI. Build CLI in weeks 3-4. Ship with public launch.**

---

## 3. Milestone Plan

### MS1: Beta-Ready (3 days — Week 1)

Fix the 2 remaining issues. Ship self-hosted beta.

#### Task 1.1: Fix E-02 — CodeExecToolAdapter Isolation

**File:** `packages/nodes/noodle_nodes/ai_v2/agent_tools.py`
**Current:** `CodeExecToolAdapter.invoke()` at line 285 uses `subprocess.Popen(argv, ...)` directly with AST validation but without ProcessPoolExecutor isolation.
**Required:** Route through same `PooledProcessIsolator.run()` boundary as the regular Code node.

**Implementation:**
```python
# In agent_tools.py:CodeExecToolAdapter.invoke()
# Replace subprocess.Popen with:
from noodle.process_isolation import get_isolator
isolator = get_isolator()  # returns the PooledProcessIsolator singleton
result = await isolator.run(code, env_vars={...}, timeout=...)
```

**Acceptance criteria:**
- CodeExecToolAdapter uses `PooledProcessIsolator`, not `subprocess.Popen`
- `_CodeValidator` AST checks still applied before execution
- Existing agent tool tests pass
- New test: `test_agent_code_tool_uses_process_isolator` — verify `subprocess.Popen` is NOT called; verify `PooledProcessIsolator` IS used

**Files touched:** `packages/nodes/noodle_nodes/ai_v2/agent_tools.py` (1 file)
**Estimated time:** 2 hours

#### Task 1.2: Fix B-01 — `_load_run_and_workflow` Org Scoping

**File:** `apps/api/app/routers/runs.py`
**Current:** Route guard `Depends(require_permission("workflow:run"))` exists at line 776 but the helper at line 279 still calls `session.get(Run, run_id)` which bypasses the ORM org filter.
**Required:** Use org-scoped `select()` query.

**Implementation:**
```python
# In runs.py:_load_run_and_workflow (line 279)
# Replace:
#   run = await session.get(Run, run_id)
# With:
run = await session.scalar(
    select(Run).where(Run.id == run_id)
)
```

**Acceptance criteria:**
- `_load_run_and_workflow` uses `select(Run).where(...)`, not `session.get(Run, ...)`
- Cross-org run ID returns 404 (not 403 — don't leak existence)
- All existing run endpoint tests pass
- New test: `test_debug_snapshot_cross_org_returns_404`

**Files touched:** `apps/api/app/routers/runs.py` (1 file)
**Estimated time:** 30 minutes

#### Task 1.3: CI Hardening

**File:** `.github/workflows/ci.yml`
**Changes:**
1. Flip Postgres lane `continue-on-error: false` (line 137)
2. Flip `pip-audit` to block on CRITICAL/HIGH CVEs
3. Fix any test failures that emerge from making these blocking

**Acceptance criteria:**
- All 3 CI lanes pass (Python SQLite, Python 3.14 compat, Postgres+Redis)
- All security scans pass
- All frontend tests pass
- CI is fully green and blocking

**Estimated time:** 2-4 hours (depending on latent failures)

#### Task 1.4: Helm Defaults Audit & Hardening

**Files:** `deploy/helm/noodle/`
**Changes:**
1. Add `resources.limits` to API + worker deployments (V-06)
2. Default `ingress.tls: true` with cert-manager annotation (V-07)
3. Move MinIO credentials to Kubernetes Secret or env vars (V-08)
4. Verify `terminationGracePeriodSeconds: 60` present on all deployments
5. Add `PodDisruptionBudget` when replicas > 1

**Acceptance criteria:**
- `helm template` produces valid manifests with all hardening
- `helm lint` passes
- Resource limits documented in values.yaml

**Estimated time:** 4 hours

#### MS1 Gate

- [ ] All 3 CI lanes green and blocking
- [ ] CodeExecToolAdapter uses ProcessPoolExecutor
- [ ] No `session.get()` bypasses in auth-critical paths
- [ ] Helm deploys with liveness probes, resource limits, rolling updates
- [ ] `docker compose up` succeeds with production web image
- [ ] All existing tests pass (373 engine + 115 API + frontend)
- [ ] Security scanning blocks on CRITICAL/HIGH

---

### MS2: Production-Grade Public Launch (4 weeks — Weeks 2-5)

#### Slice 2A: Durable Execution Checkpoints (Week 2)

**Problem:** Current `build_durable_execution_state()` in `run_resume.py` reconstructs cache from completed `NodeRun` rows, but it's best-effort — wrapped in `except Exception: pass` at runner.py:1440. If it fails silently, the run restarts from scratch, re-executing all nodes.

**Solution:** Real checkpoint persistence after every node completes.

**Implementation:**

1. **New DB column:** `ALTER TABLE runs ADD COLUMN checkpoint JSONB` — stores the serialized `node_outputs` dict + `completed_nodes` set after each node finishes.

2. **Checkpoint save** — In `_execute_run_impl`'s `on_event` callback (after `node_finished`), serialize current state:
```python
async def _save_checkpoint(run_id, node_outputs, completed_nodes):
    payload = {
        "node_outputs": _serialize_checkpoint(node_outputs),
        "completed_nodes": list(completed_nodes),
        "last_node_id": node_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    async with SessionLocal() as session:
        await session.execute(
            update(Run).where(Run.id == run_id).values(checkpoint=payload)
        )
        await session.commit()
```

3. **Checkpoint load** — In `_execute_queued_entry`, BEFORE engine execution, load checkpoint:
```python
checkpoint = await _load_checkpoint(run_id)
if checkpoint:
    cache = _deserialize_checkpoint(checkpoint["node_outputs"])
    # Pass as cache to engine.execute() — already-completed nodes skip
```

4. **Checkpoint clear** — On run terminal state (success/error/cancelled), clear checkpoint to free storage.

**Serialization safety:** Only JSON-serializable values stored in checkpoint. DatasetRef/ArtifactRef stored as references (just the key), not the data. DataFrames converted to preview rows (first 100).

**Acceptance criteria:**
- Kill server mid-workflow → restart → run resumes from last completed node (not from scratch)
- Checkpoint column cleared on terminal run states
- Checkpoint size bounded (max 1MB per run)
- Test: `test_durable_execution_resume` — execute 10-node workflow, kill after node 5, verify only nodes 6-10 execute on resume
- Test: `test_checkpoint_excludes_large_data` — verify DataFrames stored as refs, not inline

**Files touched:** `apps/api/app/models.py`, `apps/api/app/services/runner.py`, `apps/api/app/services/run_resume.py`, `apps/api/app/services/run_persistence.py`, `apps/api/alembic/versions/XXXX_checkpoint_column.py`
**Estimated time:** 4 days

#### Slice 2B: Python SDK + CLI (Weeks 2-3, parallel with 2A)

**Problem:** No way to interact with Noodle from Python code, terminal, or CI/CD.

**Solution:** `noodle-client` package on PyPI with typed API + `noodle` CLI.

**Package structure:**
```
packages/client/
├── pyproject.toml
├── noodle_client/
│   ├── __init__.py          # Public API exports
│   ├── client.py            # NoodleClient class (httpx-based)
│   ├── models.py            # Typed models (Workflow, Run, Credential, etc.)
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py          # Click/Typer CLI entrypoint
│   │   ├── workflows.py     # noodle workflow * commands
│   │   ├── runs.py          # noodle run * commands
│   │   ├── export_.py       # noodle export commands
│   │   ├── import_.py       # noodle import commands
│   │   └── auth.py          # noodle login/logout/whoami
│   └── _version.py
└── tests/
```

**API surface (MVP):**
```python
from noodle_client import NoodleClient

client = NoodleClient(base_url="https://noodle.example.com", token="...")

# Workflows
wf = client.workflows.create(name="My Workflow", folder_id="...")
client.workflows.list(status="active", limit=50)
client.workflows.get(workflow_id)
client.workflows.update(workflow_id, graph=...)
client.workflows.delete(workflow_id)
client.workflows.publish(workflow_id)

# Runs
run = client.runs.start(workflow_id, data={"key": "value"})
client.runs.get(run_id)
client.runs.cancel(run_id)
client.runs.list(workflow_id=workflow_id, status="failed")

# Export/Import
script = client.export_.as_script(workflow_id)
module = client.export_.as_module(workflow_id)
client.import_.from_module(module_source)

# Credentials
client.credentials.list()
client.credentials.create(name="API Key", type="api_key", data={"key": "..."})
```

**CLI surface (MVP):**
```bash
# Auth
noodle login https://noodle.example.com
noodle whoami

# Workflows
noodle workflow list
noodle workflow create "My Automation"
noodle workflow get <id>
noodle workflow delete <id>

# Runs
noodle run start <workflow-id> --data '{"key": "value"}'
noodle run list --workflow <id> --status failed
noodle run cancel <run-id>
noodle run logs <run-id>

# Export/Import
noodle export script <workflow-id> > my_wf.py
noodle export module <workflow-id> > my_wf.module.py
noodle import < my_wf.module.py

# Code modules
noodle push my_nodes.py --env production
```

**Acceptance criteria:**
- `pip install noodle-client` works from PyPI
- All client methods have typed return values (no `dict` escapes)
- CLI has `--help` on every command
- Client handles auth (token refresh, 401 → re-login prompt)
- Test: `test_client_full_cycle` — create workflow → start run → wait → get result → export → delete
- Test: CLI integration tests for each command

**Files created:** `packages/client/` (new package, ~15 files)
**Estimated time:** 2 weeks

#### Slice 2C: AI Improvements (Week 4)

**Problem:** AI builder is single-turn. Can't explain existing workflows. Can't generate tests. Can't iterate.

**Solution:** Three new capabilities, one new endpoint.

**Task 2C.1: Standalone Explain Endpoint**

```
POST /workflows/{id}/explain
→ Returns natural-language explanation of what the workflow does,
  what triggers it, what each node does, how data flows.
→ Uses the existing node catalog (ai_builder.py:_NODE_REGISTRY).
→ Does NOT require an LLM — falls back to template-based explanation.
```

**Files:** `apps/api/app/routers/workflows.py` (new route), `apps/api/app/services/ai_builder.py` (new `explain_workflow` function)
**Estimated time:** 1 day

**Task 2C.2: Multi-Turn AI Refinement**

```
POST /workflows/{id}/ai-draft
→ New mode: "refine"
→ Accepts conversation_history: list[{role, content}]
→ Accepts target_node_ids: list[str] (which nodes to focus on)
→ AI can reference nodes from previous turns by ID
→ Conversation context includes full node catalog + current graph state
```

**Files:** `apps/api/app/services/ai_builder.py`, `apps/api/app/routers/workflows.py`, `apps/api/app/schemas.py`
**Estimated time:** 4 days

**Task 2C.3: Workflow Test Generation**

```
POST /workflows/{id}/generate-tests
→ AI generates example input data + expected output assertions
→ Returns list of {name, input_data, expected_outputs, assertions}
→ User can run tests against current workflow version
→ Tests persist as workflow metadata (new tests column)
```

**Files:** `apps/api/app/routers/workflows.py` (new route), `apps/api/app/services/ai_builder.py` (new `generate_tests` function), `apps/api/app/models.py` (new `Workflow.tests` JSON column)
**Estimated time:** 4 days

**Acceptance criteria:**
- Explain endpoint returns useful description for any valid workflow (with or without LLM)
- Multi-turn refinement: can say "change the Slack node to notify #alerts channel" and AI modifies only that node
- Test generation produces runnable test data with assertions
- All three features have deterministic fallbacks (no LLM available)

#### Slice 2D: UX Production Polish (Week 5)

**Task 2D.1: Guided Onboarding**

Interactive overlay for first-time editor visits:
- Step 1: "This is your canvas. Drag nodes here."
- Step 2: "Press Tab to quick-add a node at your cursor."
- Step 3: "Connect nodes by dragging from output handles."
- Step 4: "Click Run to execute your workflow."
- Step 5: "Open the AI panel to generate workflows from text."

**Implementation:** New `OnboardingOverlay.tsx` component using a step-through tour pattern. Persists `onboarding_completed` in localStorage.

**Files:** `apps/web/src/editor/OnboardingOverlay.tsx`, `apps/web/src/editor/Canvas.tsx` (integration)
**Estimated time:** 3 days

**Task 2D.2: Node Descriptions in Palette**

Tooltip on hover showing: node name, description, input/output ports, category.

**Files:** `apps/web/src/editor/NodePalette.tsx`
**Estimated time:** 1 day

**Task 2D.3: Expression Autocomplete**

While typing `{{ }}` in parameter fields, show autocomplete dropdown with:
- `$json.<field>` — fields from the input JSON
- `$node["<id>"].<port>.<field>` — upstream node outputs
- `$workflow.<field>` — workflow-level variables
- `$env.<var>` — environment variables

**Files:** `apps/web/src/editor/fields/ExpressionAutocomplete.tsx` (new), parameter field components
**Estimated time:** 3 days

**Task 2D.4: Execution Timeline on Canvas**

Horizontal progress bar at bottom of canvas during execution showing:
- Node completion percentage (e.g., 7/12 nodes)
- Elapsed time
- Current node name with spinner
- Color-coded: green (success), red (error), blue (running), grey (pending)

**Files:** `apps/web/src/editor/ExecutionTimeline.tsx` (new), `apps/web/src/editor/Canvas.tsx` (integration)
**Estimated time:** 2 days

#### MS2 Gate

- [ ] Durable execution: kill server mid-workflow → resume from last checkpoint
- [ ] `pip install noodle-client` works; CLI has `--help` on every command
- [ ] Explain endpoint returns useful descriptions
- [ ] Multi-turn AI refinement works end-to-end
- [ ] AI can generate runnable test data for any workflow
- [ ] First-time user guided through 5 onboarding steps
- [ ] Node palette has descriptions on hover
- [ ] `{{ }}` autocomplete works in parameter fields
- [ ] Execution timeline visible during runs
- [ ] All new features have tests
- [ ] All existing tests still pass

---

### MS3: Differentiated Product (6 weeks — Weeks 6-11)

These features make Noodle genuinely unique in the market.

#### Slice 3A: MCP Client — External Tools as Nodes (Weeks 6-7)

**Problem:** Noodle's MCP server exposes workflows to AI tools. But Noodle can't consume external MCP tools as visual nodes.

**Solution:** Connect to any MCP server, discover its tools, and represent them as drag-and-drop nodes on the canvas.

```
MCP Server (e.g., filesystem, postgres, slack)
        │
        ▼
Noodle MCP Client ──► discover tools ──► generate NodeManifest per tool
        │
        ▼
Canvas nodes (typed ports from JSON Schema) ──► execute via MCP tool call
```

**Implementation:**
- New `MCPConnection` model — stores server URL, transport type, auth
- `mcp_client.py` service — connects to external MCP servers, discovers tools
- `mcp_node_generator.py` — converts MCP tool JSON Schema → `NodeManifest` with typed ports
- UI: "Add MCP Server" form → browse tools → drag onto canvas
- Execution: MCP tool call node invokes external MCP server at runtime

**Estimated time:** 2 weeks

#### Slice 3B: Typed Code Node I/O (Week 8)

**Problem:** Code node uses `Any` for all inputs and outputs. No contract, no validation at wiring time.

**Solution:** Let users declare Pydantic schemas inline on the Code node.

```python
# In the Code node UI, a new "Schema" tab:
input_schema = {
    "type": "object",
    "properties": {
        "user_id": {"type": "integer"},
        "name": {"type": "string"}
    },
    "required": ["user_id"]
}

output_schema = {
    "type": "object", 
    "properties": {
        "score": {"type": "number"},
        "category": {"type": "string", "enum": ["low", "medium", "high"]}
    }
}
```

**Implementation:**
- `GraphNode.params` gets `input_schema` and `output_schema` fields
- `_run_one_node` validates wired inputs against `input_schema` before execution
- `_run_one_node` validates output against `output_schema` after execution
- UI: Schema editor tab in NDV with JSON Schema builder

**Estimated time:** 1 week

#### Slice 3C: Visual Graph Diff for AI Changes (Week 9)

**Problem:** AI draft modal shows text summary. Users can't see what changed visually.

**Solution:** Side-by-side canvas rendering showing added (green), removed (red), changed (yellow) nodes and edges before accepting AI changes.

**Implementation:**
- Backend: `diff_workflow_graphs()` already exists in `diffWorkflowGraphs.ts`
- Frontend: `WorkflowDiffView.tsx` already renders diffs for version history
- New: Wire diff view into `AiDraftModal.tsx` as a "Visual Diff" tab
- Show node-by-node delta with accept/reject per change

**Estimated time:** 1 week

#### Slice 3D: AI-Generated Custom Nodes (Weeks 10-11)

**Problem:** If a node doesn't exist, AI can only use Code nodes (raw Python). No typed, reusable, port-based node is created.

**Solution:** AI generates a complete `@node`-decorated Python function with typed ports, which becomes a first-class reusable node.

```
User: "I need a node that calls the Resend API to send emails"
  │
  ▼
AI: Generates @node function with:
    - Typed inputs (to, subject, body, api_key)
    - Typed outputs (message_id, status)
    - Error handling
    - Documentation
  │
  ▼
Noodle: Registers as "Resend Email" node in palette
  │
  ▼
User: Drags onto canvas like any built-in node
```

**Implementation:**
- New `ai_builder.py` mode: `"generate_node"`
- AI receives: node registry context + user's description
- AI outputs: complete `@node`-decorated Python function
- `code_modules.py` infrastructure registers it as `user:ai_generated:<name>`
- Node appears in palette under "AI Generated" category
- User can edit the generated code before using

**Estimated time:** 2 weeks

#### MS3 Gate

- [ ] MCP client can connect to external MCP server and render tools as nodes
- [ ] Code node supports typed input/output schemas
- [ ] AI changes show visual before/after diff
- [ ] AI can generate a custom typed node from natural language description
- [ ] All new features have tests

---

### MS4: Enterprise (6+ weeks — Beyond scope of this spec)

- SAML/OIDC SSO
- KMS provider implementations (Vault, AWS KMS, GCP KMS, Azure Key Vault)
- Community node registry (review + publish pipeline)
- Multi-region execution dispatching
- Noodle Cloud (managed hosting)

*Detailed design deferred to a future spec.*

---

## 4. Testing Strategy

### 4.1 Per-Task Testing

Every task includes acceptance criteria with specific tests. Minimum:
- Unit test for the new/changed function
- Integration test for the end-to-end flow
- Existing test suite must remain green

### 4.2 Regression Gates

After each slice, run:
```bash
# Engine tests
cd packages/core && uv run pytest -x -q

# API tests (SQLite)
cd apps/api && uv run pytest -x -q -m "not postgres"

# API tests (Postgres) — in CI
cd apps/api && DATABASE_URL=postgresql://... uv run pytest -x -q

# Frontend tests
cd apps/web && npm test -- --run

# Frontend typecheck
cd apps/web && npm run typecheck

# Security scans
pip-audit
bandit -r packages/nodes/noodle_nodes packages/core/noodle packages/runner
```

### 4.3 Load / Stress Tests (MS2 Gate)

Before public launch:
- 50 concurrent workflow runs
- 1000-node loop workflow execution
- 500-credential list performance
- WebSocket streaming with 100 concurrent subscribers
- Server restart during 10 concurrent long-running workflows

---

## 5. Rollout Strategy

### 5.1 Beta (After MS1 — Week 1)

- **Audience:** 5-10 trusted developers (personal network, early adopters)
- **Deployment:** Self-hosted Docker Compose only
- **License:** Free lifetime for beta participants
- **Support:** Direct Discord access, weekly office hours
- **Goal:** Find UX friction, missing integrations, runtime bugs in real workflows

### 5.2 Public Launch (After MS2 — Week 6)

- **Audience:** Public. HN Show HN, Product Hunt, r/Python, r/selfhosted
- **Deployment:** Docker Compose + Helm + one-click cloud option (if feasible)
- **License:** Community (free) + Pro ($49/mo) — feature gating via existing licensing system
- **Support:** Discord community + GitHub Discussions + docs site
- **Goal:** 500+ active installs, 50+ Pro subscriptions

### 5.3 Post-Launch Iteration (MS3 — Weeks 6-11)

- Ship MS3 features incrementally (don't wait for all to complete)
- Prioritize based on beta feedback
- Publish case studies from beta users
- Build template library from real workflows

---

## 6. Acceptance Criteria (End-to-End)

### MS1 Complete When:

- [ ] All CI lanes green and blocking
- [ ] CodeExecToolAdapter uses ProcessPoolExecutor
- [ ] No `session.get()` org-filter bypasses in auth-critical paths  
- [ ] `docker compose up` serves production nginx web image
- [ ] Helm deploys with liveness probes, resource limits, rolling updates
- [ ] All 373 engine tests + 115 API tests + frontend tests pass
- [ ] Self-hosted beta can begin

### MS2 Complete When:

- [ ] Durable execution survives server restart mid-workflow
- [ ] `pip install noodle-client` works; `noodle --help` shows all commands
- [ ] AI explains any workflow, iterates conversationally, generates tests
- [ ] First-time user completes 5-step guided onboarding
- [ ] Expression autocomplete works in parameter fields
- [ ] Execution timeline shows progress during runs
- [ ] Public launch readiness

### MS3 Complete When:

- [ ] External MCP tools appear as canvas nodes
- [ ] Code node supports typed input/output schemas
- [ ] AI changes show visual diff
- [ ] AI generates custom typed nodes from description
- [ ] Noodle is uniquely differentiated in market

---

## 7. What We're NOT Doing

Explicitly descoped to prevent scope creep:

1. **400+ integration nodes** — Cover top 30 + MCP multiplier + AI code generation. Don't chase n8n's node count.
2. **Engine rewrite** — The engine is well-structured internally. Fix the runner orchestration, not the engine.
3. **Celery / Temporal / Prefect** — The DB-backed queue is the right design. Don't add operational dependencies.
4. **Microservices** — Monolith-with-worker is correct through 1000+ concurrent workflows.
5. **Mobile/responsive UI** — Desktop-first. Mobile is not a target persona.
6. **Real-time collaboration** — Single-user editor. Multi-user is enterprise (MS4+).
7. **Plugin marketplace** — `@node` SDK + code modules IS the plugin system. Community registry is MS4.

---

*Spec written 2026-06-29. References: NOODLE_PRODUCT_FUNCTIONALITY_AND_AI_STRATEGY_ASSESSMENT.md (updated), NOODLE_ARCHITECTURE_ENGINE_ASSESSMENT.md, docs/audit-2026-06-24.md. Verification of all 22 audited P0/P1 issues performed against current branch code.*
