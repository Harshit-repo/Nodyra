# AI Draft Builder + Fix with AI Polish Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task only after user approval.

**Goal:** Refine Noodle's AI workflow draft builder and make "Fix with AI" produce targeted, actionable draft repairs instead of a generic replacement prompt.

**Architecture:** Keep the current Slice 13 contract: AI builder returns a normal editable `WorkflowGraph`, not hidden agent execution. Upgrade the backend planner into a real LLM-backed planner with deterministic fallback, strict server-side validation, registry-safe node IDs, credential-safe parameter handling, and current workflow/run context for Fix with AI. Upgrade the frontend modal into a clear preview/apply flow with context-aware Fix mode and safer application choices.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async, Noodle `WorkflowGraph`, React/Vite/TypeScript, Zustand editor store, pytest, `npm run build`.

---

## Current context from repo inspection

- Backend entrypoint: `apps/api/app/routers/workflows.py:257-279` exposes `POST /workflows/{workflow_id}/ai-draft` and delegates to `build_workflow_draft`.
- Backend planner: `apps/api/app/services/ai_builder.py` is deterministic and keyword-based. It currently builds simple graphs for GitHub/OpenAI/Anthropic/Slack/SMTP and falls back to `manual_trigger -> edit_fields`.
- API schema: `apps/api/app/schemas.py:534-545` has only `prompt` and `apply` request fields, and response only includes graph, assumptions, missing credentials, required packages, explanation.
- Frontend API: `apps/web/src/api.ts:123-130` exposes `aiWorkflowDraft(id, { prompt, apply })`.
- Frontend UX: `apps/web/src/EditorPage.tsx:288-349` previews/applies AI drafts and pre-fills Fix with AI with failure text; `EditorPage.tsx:962-1053` renders one generic AI draft modal.
- Existing tests: `apps/api/tests/test_releases_errors_ai.py:160-269` covers AI draft graph creation, credential attachment, and ambiguous credentials.
- Git working tree currently has many modified files, including `apps/api/app/schemas.py`, `apps/api/tests/test_releases_errors_ai.py`, `apps/web/src/EditorPage.tsx`, and `apps/web/src/api.ts`. Plan execution must avoid overwriting unrelated edits and preserve current line endings where practical.

## Product principles for this polish

1. AI output remains editable Noodle graph JSON.
2. No hidden workflow execution.
3. Draft preview is safe: user sees what will change before applying.
4. Fix with AI should repair the existing draft when possible, not blindly replace it.
5. Backend validates all graph output against known `WorkflowGraph` / existing node IDs before returning.
6. Missing credentials and assumptions are first-class UX items, not buried text.

---

## Proposed changes

### Backend contract

Extend `AiWorkflowDraftRequest` in `apps/api/app/schemas.py`:

```python
class AiWorkflowDraftRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    apply: bool = False
    mode: str = Field(default="draft", pattern="^(draft|fix)$")
    current_graph: WorkflowGraph | None = None
    failed_run_id: str | None = None
    failed_node_id: str | None = None
    error: str | None = Field(default=None, max_length=8000)
    fix_strategy: str = Field(default="minimal", pattern="^(minimal|replacement)$")
```

Extend `AiWorkflowDraftResponse`:

```python
class AiWorkflowDraftResponse(BaseModel):
    workflow_id: str
    graph: WorkflowGraph
    assumptions: list[str] = Field(default_factory=list)
    missing_credentials: list[str] = Field(default_factory=list)
    required_packages: list[str] = Field(default_factory=list)
    explanation: str = ""
    mode: str = "draft"
    change_summary: list[str] = Field(default_factory=list)
    confidence: str = "medium"  # low | medium | high
    focus_node_id: str | None = None
    planner: str = "llm"  # llm | deterministic_fallback
```

Frontend `AiWorkflowDraftResponse` / API types mirror these fields.

### Backend planner internals

Refactor `apps/api/app/services/ai_builder.py` into small helpers:

- `_workflow_context(session, workflow_id)` to load environment and current draft.
- `_known_capabilities()` or static capability map for currently supported nodes.
- `_llm_plan(...)` to call the configured model and request a strict JSON plan.
- `_deterministic_plan(...)` as a no-network fallback when no LLM config exists or LLM output is invalid.
- `_classify_prompt(prompt, mode, error)` only as deterministic fallback support.
- `_build_new_draft(...)` for create-from-prompt mode.
- `_build_fix_draft(...)` for fixing an existing graph.
- `_validate_graph(graph)` using `WorkflowGraph.model_validate` and a known node-id allowlist.
- `_with_credentials(...)` for credential auto-attach and missing list.
- `_layout(nodes, edges)` to keep output readable, using a consistent x/y grid.

### Real LLM planner

Add a provider abstraction in `apps/api/app/services/ai_builder.py` or a small adjacent module such as `apps/api/app/services/llm_planner.py`:

- Configuration source:
  - Prefer existing credential store if exactly one visible `openai` or `anthropic` credential exists for the workflow/environment.
  - Also support environment/config values for self-hosting, e.g. `NOODLE_AI_PROVIDER`, `NOODLE_AI_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` if aligned with `app.config` conventions.
  - If no provider is configured, return deterministic fallback output with `planner="deterministic_fallback"` and an assumption explaining that AI planning is not configured.
- Provider support:
  - OpenAI chat/completions or responses endpoint.
  - Anthropic messages endpoint.
  - Keep implementation dependency-light by using `httpx`/`requests` already available in the API environment, unless project dependencies indicate a preferred SDK.
- Prompt contract:
  - Send the LLM a compact registry of allowed node IDs, names, param names, credential fields, and input/output ports.
  - Include current workflow graph for fix mode.
  - Include failed run/node/error context for fix mode.
  - Demand strict JSON only: nodes, edges, params, assumptions, missing credentials, required packages, explanation, change summary, confidence, focus node.
  - Forbid secrets: the LLM must use placeholders or credential requirements, never raw credential values.
- Validation contract:
  - Parse JSON defensively.
  - Validate every node type against the allowed registry.
  - Validate `WorkflowGraph.model_validate`.
  - Normalize missing optional node fields through model defaults.
  - Strip/replace raw-looking secrets if the LLM emits them.
  - Re-run credential attachment server-side; never trust LLM credential refs.
  - If validation fails, attempt one repair prompt; if still invalid, fall back to deterministic output.

### Draft builder polish

The LLM planner should be primary. The deterministic builder remains the fallback and validation repair baseline. Improve deterministic coverage beyond current simple keyword flow:

- Trigger selection:
  - `webhook_trigger`: webhook, GitHub event, incoming event, Stripe/SaaS webhook.
  - `schedule_trigger`: every day/hour/week, cron, scheduled, recurring.
  - `manual_trigger`: explicit manual/test/ad hoc or fallback.
- Transform/logic:
  - `edit_fields`: reshape, map, extract, rename, add field.
  - `switch`: if/when condition, route, branch, filter.
  - `code`: parse/clean/custom Python when prompt asks for custom logic.
  - `http_request`: call/fetch/post/get API, webhook outbound.
- Integrations already present:
  - Slack, SMTP email, GitHub create issue/get repo, OpenAI, Anthropic, Airtable, Notion, Postgres/MySQL, S3, CSV/JSON helpers where appropriate.
- Parameter defaults should be helpful placeholders, not empty where a safe example is possible.
- Explanations should say why nodes were chosen and what needs user review.

### Fix with AI behavior

When `mode="fix"`, use `current_graph`, `failed_node_id`, `error`, and `fix_strategy` to produce either a minimal targeted repair or a larger replacement proposal:

- If `fix_strategy == "minimal"` and failed node exists:
  - Preserve all node IDs where possible so run state/selection remains understandable.
  - Modify only failed node params or insert a guard/prep node immediately upstream.
  - Return `focus_node_id` pointing to the failed or inserted node.
- If `fix_strategy == "replacement"`:
  - Allow the LLM to propose a cleaner replacement draft for the whole workflow.
  - Still preserve the original trigger and key external side-effect nodes where sensible.
  - Return change summary that clearly marks it as a broader rewrite.
  - Never auto-publish; user must preview and apply.
- Heuristic fixes:
  - Missing credential errors: do not fabricate secrets; set credential param to available credential ref if exactly one is visible, otherwise report missing credential and focus failed node.
  - JSON/KeyError/path errors: insert or update an `edit_fields` node before the failing node with safer field mapping assumptions.
  - HTTP 4xx/5xx or timeout: add/update `http_request` timeout/retry settings where supported and improve assumptions.
  - Code node syntax/runtime errors: preserve code but add a safer commented template only if the node is a `code` node and the error points to obvious missing input handling.
  - No clear fix: return current graph unchanged with low confidence, explanation, assumptions, and focus failed node.
- Minimal strategy must not replace the entire graph unless the current graph is empty or invalid.
- Replacement strategy may replace the graph, but must still pass server-side validation and present a clear preview.

### Frontend UX polish

In `apps/web/src/EditorPage.tsx`:

- Split AI modal state into mode-aware labels:
  - Draft mode title: "Build workflow with AI".
  - Fix mode title: "Fix failed run with AI".
- Add toolbar/canvas entry point for draft builder if not already visible enough (e.g. button near Save/Run: "AI Draft").
- For Fix with AI, send structured context instead of only text:
  - `mode: "fix"`
  - `current_graph: toGraph()`
  - `failed_node_id`
  - `error`
  - `failed_run_id: runId`
  - `fix_strategy: "minimal" | "replacement"`
- Preview panel additions:
  - Mode badge, confidence, focus node, change summary list.
  - Missing credentials with clear CTA text: "Open the affected node and choose credentials." (No secret entry in modal.)
  - Required packages list.
- Safer apply choices:
  - Draft mode button: "Replace draft on canvas".
  - Fix mode button: "Apply fix to draft".
  - Fix mode strategy control: "Minimal repair" vs "Propose replacement draft".
  - After applying, select/focus `focus_node_id` if present and trigger fit view.
- Preserve unsaved current graph handling:
  - Since `applyAiDraft` currently calls `api.updateWorkflow` and marks clean, keep that behavior but warn in modal text that applying replaces/updates the saved draft.

### Optional CSS polish

In `apps/web/src/index.css`, add small classes for:

- `.ai-preview-meta`
- `.ai-confidence`
- `.ai-change-summary`
- `.ai-preview-warning`

Keep styles consistent with existing `.ai-preview` and modal classes.

---

## Step-by-step implementation plan

### Task 1: Add backend API schema fields

**Objective:** Make the API capable of distinguishing create vs fix requests and returning richer preview metadata.

**Files:**
- Modify: `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_releases_errors_ai.py`

**Steps:**
1. Add `mode`, `current_graph`, `failed_run_id`, `failed_node_id`, `error`, and `fix_strategy` to `AiWorkflowDraftRequest`.
2. Add `mode`, `change_summary`, `confidence`, `focus_node_id`, and `planner` to `AiWorkflowDraftResponse`.
3. Keep defaults backward compatible so existing `{ prompt, apply }` calls still work.
4. Run targeted schema/API tests after later tasks.

### Task 2: Refactor AI builder into reusable helpers

**Objective:** Prepare `ai_builder.py` for richer draft and fix logic without changing behavior yet.

**Files:**
- Modify: `apps/api/app/services/ai_builder.py`

**Steps:**
1. Extract node/edge/layout helpers.
2. Extract credential lookup/attach helpers already present.
3. Add a small `DraftPlan` internal structure or plain helper return tuple for graph metadata.
4. Ensure existing tests still pass before adding new behavior.

### Task 3: Add real LLM planner with strict validation

**Objective:** Make AI Draft and Fix with AI use an actual LLM planner while keeping graph output safe and editable.

**Files:**
- Modify: `apps/api/app/services/ai_builder.py`
- Possibly create: `apps/api/app/services/llm_planner.py`
- Modify: `apps/api/app/config.py` if new config fields are needed
- Modify: `apps/api/tests/test_releases_errors_ai.py`

**Steps:**
1. Add provider/config resolution for OpenAI/Anthropic from credentials or environment/config.
2. Build compact node registry context from known built-in manifests or a curated static registry.
3. Implement strict JSON LLM call for draft/fix plans.
4. Validate/normalize the returned graph server-side.
5. Server-side attach credentials only after validation.
6. Add tests with monkeypatched fake LLM responses for valid output, invalid output fallback, and no-provider fallback.

### Task 4: Add richer deterministic fallback coverage

**Objective:** Make fallback prompts produce useful initial graphs when no LLM is configured or LLM output fails validation.

**Files:**
- Modify: `apps/api/app/services/ai_builder.py`
- Modify: `apps/api/tests/test_releases_errors_ai.py`

**Steps:**
1. Add prompt classification for trigger, transforms, actions, and integrations.
2. Add schedule/manual/webhook trigger selection.
3. Add HTTP/code/edit_fields/switch support where prompt indicates them.
4. Keep supported node IDs restricted to actual built-in node IDs inspected in `packages/nodes/noodle_nodes/*`.
5. Add tests for scheduled prompts, API-fetch prompts, and unknown safe fallback.

### Task 5: Implement Fix mode backend with minimal and replacement strategies

**Objective:** Make `mode="fix"` produce either targeted graph patches or broader replacement drafts from run failure context.

**Files:**
- Modify: `apps/api/app/services/ai_builder.py`
- Modify: `apps/api/app/routers/workflows.py`
- Modify: `apps/api/tests/test_releases_errors_ai.py`

**Steps:**
1. Update router to pass the full request body into `build_workflow_draft`, not just `prompt`.
2. Change `build_workflow_draft` signature to accept mode/current graph/failure fields/fix strategy.
3. If `mode == "fix"`, call `_build_fix_draft` with `fix_strategy`.
4. Implement LLM-backed minimal and replacement fix modes.
5. Implement fallback heuristics for missing credentials, code-node missing input guard, and generic unchanged-low-confidence fallback.
6. Add tests for minimal preservation, replacement graph proposal, missing credential handling, and unclear-error fallback.

### Task 6: Update frontend types and API client

**Objective:** Let the web app send/receive the richer AI contract.

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`

**Steps:**
1. Add `AiWorkflowDraftMode = "draft" | "fix"` and `AiFixStrategy = "minimal" | "replacement"` if useful.
2. Extend `AiWorkflowDraftResponse` fields.
3. Extend `api.aiWorkflowDraft` body type to include mode/current_graph/run/error/fix_strategy fields.
4. Preserve existing call sites.

### Task 7: Extract and polish AI modal / Fix with AI frontend behavior

**Objective:** Move AI draft UI out of the already-large editor file, then make AI draft and Fix with AI feel intentional and safe.

**Files:**
- Create: `apps/web/src/AiDraftModal.tsx`
- Modify: `apps/web/src/EditorPage.tsx`
- Modify: `apps/web/src/index.css` if needed

**Steps:**
1. Create `AiDraftModal.tsx` with props for open/close, mode, prompt, preview, busy state, fix strategy, and callback handlers.
2. Keep orchestration state in `EditorPage.tsx`: `aiMode` (`draft` or `fix`), `fixStrategy` (`minimal` or `replacement`), prompt, preview, busy state, and optional fix context.
3. Make draft-builder opener set `aiMode="draft"` and clear fix context.
4. Update `openAiFixFailedRun` to set `aiMode="fix"`, collect `runId`, `failedNodeId`, `failedError`, and current graph.
5. Update `previewAiDraft` request body with structured context and selected fix strategy.
6. Render mode-specific modal title, helper text, fix strategy toggle, preview metadata, change summary, confidence, planner, and focus node in `AiDraftModal.tsx`.
7. Update apply button text by mode.
8. After apply, select/focus the `focus_node_id` if present and fit view.

### Task 8: Add/adjust validation tests

**Objective:** Verify backend and frontend still build cleanly.

**Files:**
- Modify: `apps/api/tests/test_releases_errors_ai.py`
- Possibly add: small focused tests if a new file is cleaner, e.g. `apps/api/tests/test_ai_builder.py`

**Commands:**
- Backend targeted:
  - `uv run pytest apps/api/tests/test_releases_errors_ai.py -q`
- Backend broader smoke:
  - `uv run pytest apps/api/tests/test_workflows.py apps/api/tests/test_runs.py apps/api/tests/test_releases_errors_ai.py -q`
- Frontend:
  - `cd apps/web && npm run build`
- Lint/type where feasible:
  - `uv run ruff check apps/api/app/services/ai_builder.py apps/api/app/routers/workflows.py apps/api/app/schemas.py apps/api/tests/test_releases_errors_ai.py`

---

## Files likely to change

Backend:
- `apps/api/app/schemas.py`
- `apps/api/app/routers/workflows.py`
- `apps/api/app/services/ai_builder.py`
- `apps/api/tests/test_releases_errors_ai.py` or new `apps/api/tests/test_ai_builder.py`

Frontend:
- `apps/web/src/types.ts`
- `apps/web/src/api.ts`
- `apps/web/src/EditorPage.tsx`
- `apps/web/src/AiDraftModal.tsx` (new extracted modal component)
- `apps/web/src/index.css` (small style polish only)

No database migration should be required.

---

## Risks and mitigations

- **Working tree already has many modified files:** inspect diffs before editing each file and preserve existing unrelated changes. Avoid broad formatters that churn line endings.
- **LLM output can be invalid or unsafe:** validate against allowed node registry, strip secrets, retry repair once, then fall back deterministically.
- **Deterministic fallback can overpromise:** keep confidence/assumptions explicit and prefer safe placeholders over fabricated params.
- **Fix mode may accidentally replace user work:** default to minimal repairs; only use replacement strategy when user explicitly chooses it in the modal.
- **Credential safety:** never generate or expose credential values; only attach existing credential refs when exactly one visible credential matches.
- **Frontend file is large (`EditorPage.tsx`):** keep changes localized around AI modal state/functions/render, or consider extracting `AiDraftModal.tsx` only if this reduces risk.
- **No frontend test harness exists:** rely on TypeScript/Vite build plus manual browser smoke steps.

---

## Manual smoke checklist after implementation

1. Create empty workflow, open AI Draft, prompt: "When a GitHub issue is opened, summarize with OpenAI and post to Slack." Preview shows webhook, OpenAI, Slack, missing credentials, assumptions.
2. Apply draft. Canvas updates and saved draft has unpublished changes.
3. Prompt: "Every morning fetch an API and email me a summary." Preview includes schedule trigger and relevant action/transform nodes.
4. Run a workflow that fails on a node. Click Fix with AI. Modal opens in Fix mode with failure context.
5. Preview minimal fix. It preserves the existing graph and shows change summary/confidence/focus node.
6. Toggle replacement strategy and preview again. It may propose a broader replacement but clearly labels the change.
7. Apply fix. Canvas updates, affected node is selected/focused when applicable, and draft is saved.

---

## Open questions for approval

1. Real LLM planner is required. Deterministic behavior remains only as fallback if LLM config is absent or invalid output fails validation.
2. Fix with AI should support both strategies: minimal targeted repair and broader replacement draft proposal.
3. Component organization decision: either keep the modal JSX inside `EditorPage.tsx` for fewer files, or extract it into `apps/web/src/AiDraftModal.tsx` to make the already-large editor file easier to maintain. Default recommendation: extract only if the implementation starts making `EditorPage.tsx` noticeably harder to read; otherwise keep localized for lower risk.
