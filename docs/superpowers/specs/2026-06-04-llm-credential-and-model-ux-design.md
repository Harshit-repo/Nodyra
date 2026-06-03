# LLM credential & model-selection UX — design

**Date:** 2026-06-04
**Status:** Design approved; spec for review before writing the implementation plan.

## Goal

Make adding an LLM-provider credential and choosing a model feel like a modern
AI tool instead of a flat form full of fields you don't need:

1. **Provider-aware credential form.** Pick a provider → see only the fields that
   provider actually needs, with sensible `base_url` defaults prefilled.
   The default ask is **Provider + API key**; everything else is tucked behind an
   "Advanced" disclosure that auto-opens only when the chosen provider needs it
   (Ollama → base URL, Azure → endpoint/version/deployment).
2. **Dynamic model list.** Once a credential is selected, fetch the provider's
   real model catalogue (OpenAI `/v1/models`, OpenRouter `/api/v1/models`,
   Ollama `/api/tags`, Anthropic `/v1/models`, Azure deployments) instead of a
   hardcoded list.
3. **Free-text model fallback.** The model field is an editable combobox: pick a
   fetched model, or type any id. New/unlisted models always work.
4. **Test before save.** A "Test connection" button inside the create-credential
   modal validates the credential before it is persisted.

Applies to both credential surfaces: the dedicated **Credentials page**
(`CredentialsPage.tsx`) and the node inspector's **quick-add modal**
(`CredentialCreateModal` in `NodeDetails.tsx`).

## What exists today

- **Credential types:** `apps/api/app/services/credential_types.py` defines
  backend-owned `CredentialTypeSpec`s with flat `fields`. `llm_provider` is **not**
  among them — it is presented purely from frontend presets.
- **Credentials page:** `apps/web/src/CredentialsPage.tsx` holds a hardcoded
  `CREDENTIAL_PRESETS` list. The `llm_provider` preset declares **9 flat fields**
  (provider, api_key, base_url, site_url, app_name, organization, azure_endpoint,
  azure_api_version, deployment) shown all at once regardless of provider — which
  contradicts the modal's own copy: *"Noodle only asks for fields this credential
  type uses."* It can also `POST /credentials` only — no pre-save test.
- **Node quick-add modal:** `CredentialCreateModal` renders `meta.fields` flat
  (7 fields for AI Chat's `llm_provider`), labelled via `CRED_FIELD_LABELS`.
- **Model selection:** `packages/nodes/noodle_nodes/llm.py` exposes
  `CHAT_MODEL_CHOICES` (and `EMBEDDING_*`, `VISION_*`) as a param `choices` list.
  In `ParamField`, a `choices` array renders a **strict `<select>`** — no custom
  entry, no live catalogue.
- **Dynamic options (half-wired):**
  - `packages/nodes/noodle_nodes/integrations_v2/dynamic_options.py` — a
    `register_loader`/`call_loader` registry returning `[{value,label,description}]`.
  - `GET /nodes/dynamic-options/{loader_id}` (`apps/api/app/routers/nodes.py`) —
    **no auth dependency**, accepts a **plaintext** `credentials` JSON string in the
    query, and hardcodes `spreadsheet_id`/`sheet_name` params. Built for Sheets.
  - `ParamSpec.load_options` exists in `types.ts` but **`ParamField` never reads
    it** — the frontend has no dynamic-options widget.
- **Credential test:** `POST /credentials/{id}/test` (`credentials.py`) requires a
  **persisted** credential, does a scope-rank check, decrypts server-side, and
  calls `test_credential_connection` (`services/credential_tests.py`). There is no
  stateless/draft variant.
- **`credentialMatchesParam`** (just landed) already lets a partially-filled
  multi-field `llm_provider` credential match a node's picker.

## Non-goals (YAGNI)

- **Scope UUID pickers** (typing raw Workflow/Environment/Runner-pool IDs) — a
  real flaw, but separate from the LLM flow. Explicitly out of scope this pass.
- No change to how credentials are **stored** (still `{field: value}` dicts) or
  to runtime credential resolution.
- No caching layer for fetched models beyond an in-memory request; a manual
  **refresh** button is enough. (Loaders may apply a short server-side TTL if
  trivial, but it is not required.)
- No backend-owned provider-variant schema. Provider→visible-field mapping is
  **presentation** and lives in the frontend (see Decision A).
- No OAuth changes.

## Design

### Decision A — provider variants are frontend-driven

A new shared module `apps/web/src/llmProviders.ts` owns the only
provider-specific presentation knowledge:

```ts
interface LlmProviderVariant {
  value: string;                 // "openai" | "openrouter" | "ollama" | ...
  label: string;                 // "OpenAI", "OpenRouter", "Ollama", ...
  apiKey: "required" | "optional" | "hidden";
  baseUrlDefault?: string;       // prefilled, editable
  advancedFields: string[];      // shown under the "Advanced" disclosure
  curatedModels?: string[];      // offline / no-list-API fallback for the combobox
  docsUrl?: string;
}
```

(The loader id lives on the node param's `load_options`; the variant only carries
the per-provider **curated fallback** the combobox shows pre-fetch / offline.)

Rationale: the backend/runtime already accepts whatever subset of fields is
present, so a presentation-only map cannot drift from runtime behaviour, and it
avoids reshaping `CredentialTypeSpec`. Both credential surfaces import this one
map, so they stay consistent with each other.

Variants (initial):

| Provider | api_key | base_url default | Advanced |
|---|---|---|---|
| OpenAI | required | — | organization |
| Anthropic | required | — | — |
| OpenRouter | required | `https://openrouter.ai/api/v1` | site_url, app_name |
| OpenAI-compatible | optional | (user-entered) | organization |
| Ollama | hidden | `http://localhost:11434` | — |
| Azure OpenAI | required | — | azure_endpoint, azure_api_version, deployment |

### Decision B — provider-first credential form (shared component)

Extract a `LlmCredentialFields` component used by both modals:

1. **Provider** select (first field).
2. **API key** field — shown unless the variant says `hidden`; required marker
   from the variant.
3. **`base_url`** — shown when the variant has a default or is OpenAI-compatible;
   prefilled with `baseUrlDefault`, editable.
4. **Advanced** `<details>` disclosure containing `advancedFields`; auto-opened
   when the selected provider has required advanced fields (Azure) or the user
   has a saved non-default value (edit case).

For **non-LLM** credential types, the existing flat rendering is unchanged — this
component is only swapped in when `type === "llm_provider"`. The Credentials page
`llm_provider` preset is reduced to provider + api_key + base_url + the advanced
set, rendered through this component rather than 9 flat fields.

### Decision C — secured, generalized dynamic-options endpoint

Rework `GET /nodes/dynamic-options/{loader_id}`:

- Add `dependencies=[Depends(require_permission("credential:read"))]`.
- Accept `credential_id: str | None` and an arbitrary query passthrough
  (`request.query_params`) instead of the hardcoded `spreadsheet_id`/`sheet_name`
  and the plaintext `credentials` JSON. **Remove** the plaintext path.
- When `credential_id` is given: load it, run the same **scope-rank check** as
  `test_credential` (403 if not visible), decrypt server-side, and pass the
  resulting dict to the loader as `credentials=...`. Other query params
  (e.g. `provider`, `base_url`) are forwarded as loader kwargs.
- Loaders remain the registry in `dynamic_options.py`; the Sheets loader keeps
  working via the same forwarded kwargs.

This both enables model fetching and **removes the secret-in-query smell**.

### Decision D — model loaders

New module `packages/nodes/noodle_nodes/ai_v2/model_options.py` (or alongside the
llm node module) registering loaders:

- `llm_models(credentials, provider=None, base_url=None)` → dispatches by
  effective provider:
  - openai / openai-compatible → `GET {base}/v1/models`
  - openrouter → `GET {base}/api/v1/models`
  - ollama → `GET {base}/api/tags` (names → values)
  - anthropic → `GET /v1/models` (x-api-key)
  - azure_openai → list deployments
  - on any error / unknown → return the variant's curated list (loader degrades
    gracefully; the endpoint still 200s with the fallback).
- `embedding_models(...)` analogous (smaller; can reuse `llm_models` filtering).

Loaders reuse the HTTP patterns and provider dispatch already proven in
`services/credential_tests.py::_test_llm_provider` (kept DRY where practical).

### Decision E — `LoadOptionsField` combobox (frontend)

Teach `ParamField` to honour `load_options`:

- When `spec.load_options` is set, render a `LoadOptionsField` instead of the
  strict `<select>`.
- It is an **editable combobox** (text input + dropdown list, or `<input
  list=…>` datalist for simplicity): the user can type any value (free-text
  fallback) or choose a fetched/curated option.
- It fetches options from `GET /nodes/dynamic-options/{load_options}` passing the
  resolved `credential_id` (from the sibling param named in `spec.depends_on`,
  e.g. `credentials`) plus `provider`/`base_url` read from the node params.
- Fetch triggers: when a credential is present and on demand via a **refresh**
  button. Before any credential is set, it shows the variant's curated models so
  the field is never empty.
- Errors surface inline ("Couldn't load models — type one or retry") and never
  block typing.

### Decision F — node param wiring

In `llm.py` / `ai_v2/models.py`, the `model` params gain:

- `load_options: "llm_models"` (embeddings → `"embedding_models"`),
- `depends_on: ["credentials"]`,
- keep the existing `choices` array as the **curated fallback** the frontend uses
  pre-fetch / offline. Free-text is allowed because `LoadOptionsField` is an
  editable combobox regardless of `choices`.

This needs `load_options` / `depends_on` to be carried on `ParamSpec` from the
node `meta` (verify `sdk.py` threads them; add if missing) and mirrored in
`types.ts` (already present).

### Decision G — draft (pre-save) credential test

Add `POST /credentials/test-draft` (auth `credential:test`) accepting
`{type, data, context}`; it resolves `test_service` the same way as
`test_credential` and calls `test_credential_connection` **without persisting**.
Both modals get a "Test connection" button that calls it with the in-progress
field values, so users validate before saving. The existing per-id test endpoint
is unchanged.

## Data flow (model fetch)

```
NodeDetails model field (LoadOptionsField)
  → reads sibling `credentials` ref → credential_id; reads params.provider/base_url
  → GET /nodes/dynamic-options/llm_models?credential_id=…&provider=…&base_url=…
      → require_permission(credential:read) + scope-rank check
      → decrypt credential server-side
      → call_loader("llm_models", credentials={…}, provider=…, base_url=…)
          → provider HTTP call (or curated fallback on error)
  ← [{value,label,description}]   → combobox options (+ free text always allowed)
```

## Testing

**Backend (pytest):**
- `llm_models` loader: openai/openrouter/ollama/anthropic dispatch hits the right
  URL/headers (monkeypatched `_request`); unknown/erroring provider → curated
  fallback, still returns options.
- dynamic-options endpoint: `credential_id` decrypts and forwards; scope-rank
  mismatch → 403; missing permission → 403/401; unknown loader → 404; plaintext
  `credentials` query path is gone.
- `POST /credentials/test-draft`: runs the right tester without creating a row;
  redaction still applies.

**Frontend (vitest):**
- `llmProviders`: variant selection yields the expected visible/advanced fields
  and `base_url` default per provider (incl. Ollama hides api_key, Azure forces
  advanced open).
- `LoadOptionsField` logic (pure parts): builds the right query (credential_id +
  provider), merges fetched + curated, preserves free-text, surfaces fetch error
  without clearing the typed value.
- `tsc` + `vite build` clean.

## Files (summary)

| Area | File | Change |
|---|---|---|
| FE config | `apps/web/src/llmProviders.ts` *(new)* | provider variants + helpers + tests |
| FE form | `apps/web/src/CredentialsPage.tsx` | use `LlmCredentialFields`; test-before-save; reduce llm_provider preset |
| FE form | `apps/web/src/editor/NodeDetails.tsx` | provider-first quick-add modal; `LoadOptionsField`; honour `load_options`/`depends_on` |
| BE endpoint | `apps/api/app/routers/nodes.py` | generalize + secure dynamic-options |
| BE endpoint | `apps/api/app/routers/credentials.py` + `schemas.py` | `POST /credentials/test-draft` |
| BE loaders | `packages/nodes/noodle_nodes/ai_v2/model_options.py` *(new)* | `llm_models`, `embedding_models` |
| Node specs | `packages/nodes/noodle_nodes/llm.py`, `ai_v2/models.py` | model params: `load_options` + `depends_on` + curated fallback |
| Core | `packages/core/noodle/sdk.py`, `models.py` | thread `load_options`/`depends_on` onto `ParamSpec` if missing |

## Risks / notes

- Provider list endpoints differ in payload shape; loaders normalise to
  `{value,label}` and must degrade to curated lists so the field is never empty.
- The model field must remain usable with **no network** (curated + free text).
- Generalizing dynamic-options changes the Sheets call path — keep its kwargs
  working and covered by existing tests.
