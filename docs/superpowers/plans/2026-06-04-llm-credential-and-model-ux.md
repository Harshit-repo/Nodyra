# LLM Credential & Model-Selection UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make adding an LLM-provider credential ask only for the fields that provider needs, and turn the model picker into a live, free-text combobox backed by the provider's real catalogue.

**Architecture:** Provider→field presentation is frontend-driven config (`llmProviders.ts`) shared by both credential surfaces. A new backend dynamic-options loader (`llm_models`) fetches model catalogues; the existing `GET /nodes/dynamic-options/{loader_id}` endpoint is generalized + secured to decrypt a credential by id server-side. A new editable `LoadOptionsField` combobox consumes `load_options`. A stateless `POST /credentials/test-draft` enables "Test connection" before save.

**Tech Stack:** FastAPI + SQLAlchemy (async) + httpx (backend), React 18 + TypeScript + Zustand + Vitest (frontend), pytest (backend tests).

Spec: `docs/superpowers/specs/2026-06-04-llm-credential-and-model-ux-design.md`

---

## Notes for the implementer

- Backend tests run from repo root with the project venv: `.venv/Scripts/python.exe -m pytest <path> -q`.
- Frontend commands run from `apps/web`: `npx vitest run <path>`, `npm run typecheck`.
- `ParamSpec` already carries `load_options: str | None` and `depends_on: list[str]` in both `packages/core/noodle/models.py` and `apps/web/src/types.ts`, and `sdk.py` already threads them from node `meta`. **No core/sdk changes are needed.**
- Dynamic-option loaders are sync functions registered via `register_loader(id, fn)` in `packages/nodes/noodle_nodes/integrations_v2/dynamic_options.py`; `call_loader` normalises results to `[{value,label,description}]`.
- Reuse provider dispatch ideas from `apps/api/app/services/credential_tests.py::_test_llm_provider`.

---

## Task 1: `llm_models` / `embedding_models` dynamic-option loaders

**Files:**
- Create: `packages/nodes/noodle_nodes/ai_v2/model_options.py`
- Modify: `packages/nodes/noodle_nodes/ai_v2/__init__.py` (register on import)
- Test: `packages/nodes/tests/test_model_options.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/nodes/tests/test_model_options.py
import noodle_nodes.ai_v2.model_options as mo


def test_openrouter_models_parses_data_ids(monkeypatch):
    def fake_fetch(method, url, **kw):
        assert method == "GET"
        assert url == "https://openrouter.ai/api/v1/models"
        assert kw["headers"]["Authorization"] == "Bearer sk-or"
        return {"data": [{"id": "openai/gpt-4.1-mini"}, {"id": "anthropic/claude-3.7-sonnet"}]}

    monkeypatch.setattr(mo, "_fetch_json", fake_fetch)
    out = mo.llm_models(credentials={"provider": "openrouter", "api_key": "sk-or"})
    assert out == [
        {"value": "openai/gpt-4.1-mini", "label": "openai/gpt-4.1-mini", "description": ""},
        {"value": "anthropic/claude-3.7-sonnet", "label": "anthropic/claude-3.7-sonnet", "description": ""},
    ]


def test_ollama_models_use_tags_endpoint(monkeypatch):
    def fake_fetch(method, url, **kw):
        assert url == "http://localhost:11434/api/tags"
        return {"models": [{"name": "llama3.1"}, {"name": "qwen2.5"}]}

    monkeypatch.setattr(mo, "_fetch_json", fake_fetch)
    out = mo.llm_models(credentials={"provider": "ollama", "base_url": "http://localhost:11434/v1"})
    assert [o["value"] for o in out] == ["llama3.1", "qwen2.5"]


def test_unreachable_provider_falls_back_to_curated(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(mo, "_fetch_json", boom)
    out = mo.llm_models(credentials={"provider": "openai", "api_key": "sk"})
    assert out  # non-empty curated fallback
    assert all(set(o) == {"value", "label", "description"} for o in out)


def test_loader_is_registered():
    from noodle_nodes.integrations_v2.dynamic_options import list_loader_ids
    import noodle_nodes  # noqa: F401 - triggers registration
    assert "llm_models" in list_loader_ids()
    assert "embedding_models" in list_loader_ids()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest packages/nodes/tests/test_model_options.py -q`
Expected: FAIL — `ModuleNotFoundError: noodle_nodes.ai_v2.model_options`.

- [ ] **Step 3: Write the loader module**

```python
# packages/nodes/noodle_nodes/ai_v2/model_options.py
"""Dynamic-option loaders for LLM / embedding model dropdowns.

Each loader fetches the provider's live model catalogue when a credential is
supplied, and degrades to a small curated list on any error so the dropdown is
never empty (offline, no list API, bad key, etc.).
"""

from __future__ import annotations

from typing import Any

import httpx

from noodle_nodes.integrations_v2.dynamic_options import register_loader

CURATED_CHAT_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "o3-mini"],
    "openai_compatible": ["gpt-4.1-mini", "gpt-4o-mini"],
    "anthropic": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
    "openrouter": ["openai/gpt-4.1-mini", "anthropic/claude-3.7-sonnet"],
    "ollama": ["llama3.1", "qwen2.5", "gemma2"],
    "azure_openai": ["gpt-4o-mini", "gpt-4o"],
}

CURATED_EMBEDDING_MODELS: dict[str, list[str]] = {
    "openai": ["text-embedding-3-small", "text-embedding-3-large"],
    "openai_compatible": ["text-embedding-3-small"],
    "ollama": ["nomic-embed-text", "mxbai-embed-large"],
    "cohere": ["embed-english-v3.0", "embed-multilingual-v3.0"],
}

_PROVIDER_ALIASES = {
    "openai compatible": "openai_compatible",
    "open router": "openrouter",
    "open-router": "openrouter",
}


def _effective_provider(provider: str | None, credentials: dict[str, str]) -> str:
    value = (provider or credentials.get("provider") or "openai").strip().lower()
    return _PROVIDER_ALIASES.get(value, value)


def _fetch_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    with httpx.Client(timeout=10) as client:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        body = response.json()
    return body if isinstance(body, dict) else {}


def _auth_header(api_key: str) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


def _chat_models_for(provider: str, api_key: str, base_url: str) -> list[str]:
    if provider == "ollama":
        url = (base_url or "http://localhost:11434").rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        data = _fetch_json("GET", f"{url}/api/tags")
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    if provider == "openrouter":
        url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
        data = _fetch_json("GET", f"{url}/models", headers=_auth_header(api_key))
        return [m["id"] for m in data.get("data", []) if m.get("id")]
    if provider == "anthropic":
        data = _fetch_json(
            "GET",
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        return [m["id"] for m in data.get("data", []) if m.get("id")]
    # openai, openai_compatible, azure_openai → OpenAI-compatible /models
    url = (base_url or "https://api.openai.com/v1").rstrip("/")
    data = _fetch_json("GET", f"{url}/models", headers=_auth_header(api_key))
    return [m["id"] for m in data.get("data", []) if m.get("id")]


def _as_options(models: list[str]) -> list[dict[str, str]]:
    return [{"value": m, "label": m, "description": ""} for m in models]


def llm_models(
    credentials: Any = None,
    provider: str | None = None,
    base_url: str | None = None,
    **_kwargs: Any,
) -> list[dict[str, str]]:
    creds = {str(k): str(v) for k, v in (credentials or {}).items()} if isinstance(credentials, dict) else {}
    prov = _effective_provider(provider, creds)
    api_key = creds.get("api_key") or creds.get("token") or ""
    url = base_url or creds.get("base_url") or ""
    try:
        models = _chat_models_for(prov, api_key, url)
    except Exception:
        models = []
    if not models:
        models = CURATED_CHAT_MODELS.get(prov, CURATED_CHAT_MODELS["openai"])
    return _as_options(models)


def embedding_models(
    credentials: Any = None,
    provider: str | None = None,
    base_url: str | None = None,
    **_kwargs: Any,
) -> list[dict[str, str]]:
    creds = {str(k): str(v) for k, v in (credentials or {}).items()} if isinstance(credentials, dict) else {}
    prov = _effective_provider(provider, creds)
    api_key = creds.get("api_key") or creds.get("token") or ""
    url = base_url or creds.get("base_url") or ""
    try:
        if prov in {"openai", "openai_compatible", "ollama"}:
            models = [m for m in _chat_models_for(prov, api_key, url) if "embed" in m.lower()]
        else:
            models = []
    except Exception:
        models = []
    if not models:
        models = CURATED_EMBEDDING_MODELS.get(prov, CURATED_EMBEDDING_MODELS["openai"])
    return _as_options(models)


register_loader("llm_models", llm_models)
register_loader("embedding_models", embedding_models)
```

- [ ] **Step 4: Ensure the module is imported so registration runs**

Add to `packages/nodes/noodle_nodes/ai_v2/__init__.py` (append at end of file):

```python
from . import model_options  # noqa: F401 - registers llm_models/embedding_models loaders
```

If `ai_v2/__init__.py` does not exist or is not imported by `noodle_nodes`, instead add the same import line to the bottom of `packages/nodes/noodle_nodes/llm.py` (which is imported at package load). Verify with Step 5's `test_loader_is_registered`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest packages/nodes/tests/test_model_options.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/model_options.py packages/nodes/noodle_nodes/ai_v2/__init__.py packages/nodes/tests/test_model_options.py
git commit -m "feat(nodes): add llm_models/embedding_models dynamic-option loaders with curated fallback"
```

---

## Task 2: Generalize + secure the dynamic-options endpoint

**Files:**
- Modify: `apps/api/app/routers/nodes.py:149-189`
- Test: `apps/api/tests/test_nodes.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_nodes.py — add these tests
async def test_dynamic_options_decrypts_credential_by_id(client, monkeypatch):
    import noodle_nodes.ai_v2.model_options as mo

    captured = {}

    def fake_chat_models(provider, api_key, base_url):
        captured["provider"] = provider
        captured["api_key"] = api_key
        return ["gpt-4.1-mini"]

    monkeypatch.setattr(mo, "_chat_models_for", fake_chat_models)

    cred = (await client.post("/credentials", json={
        "name": "OpenAI", "type": "llm_provider", "scope": "global",
        "data": {"provider": "openai", "api_key": "sk-secret"},
    })).json()

    resp = await client.get(
        f"/nodes/dynamic-options/llm_models?credential_id={cred['id']}&provider=openai"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["options"] == [{"value": "gpt-4.1-mini", "label": "gpt-4.1-mini", "description": ""}]
    assert captured["api_key"] == "sk-secret"  # decrypted server-side


async def test_dynamic_options_rejects_out_of_scope_credential(client):
    wf = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]
    cred = (await client.post("/credentials", json={
        "name": "Scoped", "type": "llm_provider", "scope": "workflow",
        "workflow_id": wf, "data": {"provider": "openai", "api_key": "sk"},
    })).json()
    # No workflow_id in the query → credential is not visible → 403.
    resp = await client.get(f"/nodes/dynamic-options/llm_models?credential_id={cred['id']}")
    assert resp.status_code == 403


async def test_dynamic_options_unknown_loader_404(client):
    resp = await client.get("/nodes/dynamic-options/nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest apps/api/tests/test_nodes.py -q -k dynamic_options`
Expected: FAIL — current endpoint ignores `credential_id` / has no scope check (assertions or 200-vs-403 mismatch).

- [ ] **Step 3: Replace the endpoint**

Replace `apps/api/app/routers/nodes.py:149-189` with:

```python
@router.get(
    "/dynamic-options/{loader_id}",
    dependencies=[Depends(require_permission("credential:read"))],
)
async def get_dynamic_options(
    loader_id: str,
    request: Request,
    credential_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return dynamic option choices for a node parameter dropdown.

    ``loader_id`` matches a registered loader. All query params are forwarded to
    the loader as keyword arguments. When ``credential_id`` is supplied the
    credential is loaded, scope-checked, and decrypted server-side, and the
    plaintext dict is passed to the loader as ``credentials`` — secrets never
    travel in the query string.
    """
    import asyncio

    if loader_id not in list_loader_ids():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown dynamic option loader '{loader_id}'",
        )

    kwargs: dict = {
        key: value
        for key, value in request.query_params.items()
        if key != "credential_id"
    }

    if credential_id:
        cred = await session.get(Credential, credential_id)
        if cred is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
        if _scope_rank(
            cred,
            kwargs.get("workflow_id"),
            kwargs.get("environment_id"),
            kwargs.get("runner_pool_id"),
        ) < 0:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Credential is not visible for the supplied scope.",
            )
        kwargs["credentials"] = decrypt_credential(cred.encrypted_data, cred.encrypted_dek)

    try:
        options = await asyncio.to_thread(call_loader, loader_id, **kwargs)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Dynamic options loader '{loader_id}' failed: {exc}",
        ) from exc
    return {"loader_id": loader_id, "options": options}
```

- [ ] **Step 4: Add the imports at the top of `apps/api/app/routers/nodes.py`**

Ensure these imports exist (add any that are missing):

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential
from app.routers.credentials import _scope_rank
from app.security import require_permission
from app.services.crypto import decrypt_credential
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest apps/api/tests/test_nodes.py -q -k dynamic_options`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full API credential/nodes suites for regressions**

Run: `.venv/Scripts/python.exe -m pytest apps/api/tests/test_nodes.py apps/api/tests/test_credentials_v2.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/routers/nodes.py apps/api/tests/test_nodes.py
git commit -m "feat(api): generalize+secure dynamic-options endpoint (credential_id decrypt, scope check, no plaintext secrets)"
```

---

## Task 3: `POST /credentials/test-draft` (test before save)

**Files:**
- Modify: `apps/api/app/schemas.py` (add `CredentialTestDraftRequest`)
- Modify: `apps/api/app/routers/credentials.py` (add route)
- Test: `apps/api/tests/test_credentials_v2.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_credentials_v2.py — add
async def test_test_draft_runs_without_persisting(client, monkeypatch):
    calls: list[dict] = []

    async def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url})
        return {"ok": True, "message": "Connected", "details": {"status_code": 200}}

    monkeypatch.setattr("app.services.credential_tests._request", fake_request)

    before = (await client.get("/credentials")).json()
    resp = await client.post("/credentials/test-draft", json={
        "type": "llm_provider",
        "data": {"provider": "openrouter", "api_key": "sk-or-test"},
        "context": {},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    after = (await client.get("/credentials")).json()
    assert len(after) == len(before)  # nothing persisted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest apps/api/tests/test_credentials_v2.py -q -k test_draft`
Expected: FAIL — 404 (route does not exist).

- [ ] **Step 3: Add the request schema**

Add to `apps/api/app/schemas.py` after `CredentialTestRequest`:

```python
class CredentialTestDraftRequest(BaseModel):
    type: str
    data: dict[str, str] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Add the route**

Add to `apps/api/app/routers/credentials.py` (after the existing `test_credential` route). Also add `CredentialTestDraftRequest` to the `from app.schemas import (...)` block:

```python
@router.post(
    "/test-draft",
    response_model=CredentialTestResponse,
    dependencies=[Depends(require_permission("credential:test"))],
)
async def test_credential_draft(
    body: CredentialTestDraftRequest,
) -> CredentialTestResponse:
    """Test in-progress credential values before they are saved.

    Stateless: nothing is persisted. Used by the create-credential modals'
    "Test connection" button.
    """
    type_spec = get_credential_type(body.type)
    test_service = type_spec.test_service if type_spec and type_spec.test_service else body.type
    return await test_credential_connection(test_service, body.data, body.context)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest apps/api/tests/test_credentials_v2.py -q -k test_draft`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/credentials.py apps/api/tests/test_credentials_v2.py
git commit -m "feat(api): add stateless POST /credentials/test-draft for test-before-save"
```

---

## Task 4: Wire model params to the loaders

**Files:**
- Modify: `packages/nodes/noodle_nodes/llm.py` (model params on chat nodes)
- Modify: `packages/nodes/noodle_nodes/ai_v2/models.py` (model param)
- Test: `packages/nodes/tests/test_llm_nodes.py` (manifest assertion)

- [ ] **Step 1: Write the failing test**

```python
# packages/nodes/tests/test_llm_nodes.py — add
def test_ai_chat_model_param_uses_dynamic_loader():
    from noodle.sdk import registry
    import noodle_nodes  # noqa: F401
    manifest = next(m for m in registry.manifests() if m.id == "ai_chat")
    model = next(p for p in manifest.params if p.name == "model")
    assert model.load_options == "llm_models"
    assert "credentials" in model.depends_on
    assert model.choices  # curated fallback preserved
```

(If `registry.manifests()` is not the accessor used elsewhere in this test file, match the existing pattern in `test_llm_nodes.py` for fetching a manifest by id.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest packages/nodes/tests/test_llm_nodes.py -q -k dynamic_loader`
Expected: FAIL — `load_options` is `None`.

- [ ] **Step 3: Update the model params**

For each chat node `model` param in `packages/nodes/noodle_nodes/llm.py` (the `ai_chat`, `ai_chat_model`, and any other node whose `model` uses `CHAT_MODEL_CHOICES`), change:

```python
"model": {"choices": CHAT_MODEL_CHOICES, "placeholder": "gpt-4.1-mini"},
```

to:

```python
"model": {
    "choices": CHAT_MODEL_CHOICES,
    "placeholder": "gpt-4.1-mini",
    "load_options": "llm_models",
    "depends_on": ["credentials"],
},
```

In `packages/nodes/noodle_nodes/ai_v2/models.py`, apply the same `load_options`/`depends_on` additions to its `model` param. For embedding model params (if present in this file or `ai_v2/embeddings.py`), use `"load_options": "embedding_models"` instead.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest packages/nodes/tests/test_llm_nodes.py -q -k dynamic_loader`
Expected: PASS.

- [ ] **Step 5: Run the nodes suite for regressions**

Run: `.venv/Scripts/python.exe -m pytest packages/nodes/tests/test_llm_nodes.py packages/nodes/tests/test_ai_v2_nodes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/llm.py packages/nodes/noodle_nodes/ai_v2/models.py packages/nodes/tests/test_llm_nodes.py
git commit -m "feat(nodes): model params load live catalogue via llm_models loader (curated choices kept as fallback)"
```

---

## Task 5: Frontend provider-variant config (`llmProviders.ts`)

**Files:**
- Create: `apps/web/src/llmProviders.ts`
- Test: `apps/web/src/llmProviders.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/llmProviders.test.ts
import { describe, expect, it } from "vitest";
import {
  LLM_PROVIDER_VARIANTS,
  getLlmVariant,
  visibleCredentialFields,
} from "./llmProviders";

describe("llmProviders", () => {
  it("OpenRouter asks for api_key + base_url default, advanced site/app", () => {
    const v = getLlmVariant("openrouter");
    expect(v.apiKey).toBe("required");
    expect(v.baseUrlDefault).toBe("https://openrouter.ai/api/v1");
    expect(v.advancedFields).toEqual(["site_url", "app_name"]);
  });

  it("Ollama hides api_key and defaults the base url", () => {
    const v = getLlmVariant("ollama");
    expect(v.apiKey).toBe("hidden");
    expect(v.baseUrlDefault).toBe("http://localhost:11434");
    expect(visibleCredentialFields("ollama", false)).not.toContain("api_key");
    expect(visibleCredentialFields("ollama", false)).toContain("base_url");
  });

  it("Azure forces its advanced fields into the visible set", () => {
    const fields = visibleCredentialFields("azure_openai", false);
    expect(fields).toContain("azure_endpoint");
    expect(fields).toContain("deployment");
  });

  it("OpenAI default view is just provider + api_key", () => {
    expect(visibleCredentialFields("openai", false)).toEqual(["api_key"]);
  });

  it("falls back to an openai-shaped variant for unknown providers", () => {
    expect(getLlmVariant("totally-unknown").apiKey).toBe("required");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `apps/web`): `npx vitest run src/llmProviders.test.ts`
Expected: FAIL — cannot resolve `./llmProviders`.

- [ ] **Step 3: Write the config module**

```ts
// apps/web/src/llmProviders.ts

export type ApiKeyMode = "required" | "optional" | "hidden";

export interface LlmProviderVariant {
  value: string;
  label: string;
  apiKey: ApiKeyMode;
  baseUrlDefault?: string;
  /** Fields revealed under the "Advanced" disclosure for this provider. */
  advancedFields: string[];
  /** Whether this provider needs a base_url field shown at all. */
  showBaseUrl: boolean;
  /** Per-provider curated model fallback shown before/without a fetch. */
  curatedModels?: string[];
  docsUrl?: string;
}

export const LLM_PROVIDER_VARIANTS: LlmProviderVariant[] = [
  {
    value: "openai",
    label: "OpenAI",
    apiKey: "required",
    advancedFields: ["organization"],
    showBaseUrl: false,
    curatedModels: ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini"],
    docsUrl: "https://platform.openai.com/api-keys",
  },
  {
    value: "anthropic",
    label: "Anthropic",
    apiKey: "required",
    advancedFields: [],
    showBaseUrl: false,
    curatedModels: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
    docsUrl: "https://console.anthropic.com/settings/keys",
  },
  {
    value: "openrouter",
    label: "OpenRouter",
    apiKey: "required",
    baseUrlDefault: "https://openrouter.ai/api/v1",
    advancedFields: ["site_url", "app_name"],
    showBaseUrl: true,
    curatedModels: ["openai/gpt-4.1-mini", "anthropic/claude-3.7-sonnet"],
    docsUrl: "https://openrouter.ai/settings/keys",
  },
  {
    value: "openai_compatible",
    label: "OpenAI-compatible",
    apiKey: "optional",
    advancedFields: ["organization"],
    showBaseUrl: true,
    curatedModels: ["gpt-4.1-mini"],
  },
  {
    value: "ollama",
    label: "Ollama",
    apiKey: "hidden",
    baseUrlDefault: "http://localhost:11434",
    advancedFields: [],
    showBaseUrl: true,
    curatedModels: ["llama3.1", "qwen2.5", "gemma2"],
  },
  {
    value: "azure_openai",
    label: "Azure OpenAI",
    apiKey: "required",
    advancedFields: ["azure_endpoint", "azure_api_version", "deployment"],
    showBaseUrl: false,
    curatedModels: ["gpt-4o-mini", "gpt-4o"],
  },
];

const BY_VALUE = new Map(LLM_PROVIDER_VARIANTS.map((v) => [v.value, v]));

export function getLlmVariant(provider: string): LlmProviderVariant {
  return BY_VALUE.get(provider) ?? LLM_PROVIDER_VARIANTS[0];
}

/** Ordered list of credential field keys to show for a provider.
 *  When `showAdvanced` is false, advanced fields are omitted UNLESS the variant
 *  requires them (Azure), in which case they are always part of the core set. */
export function visibleCredentialFields(
  provider: string,
  showAdvanced: boolean,
): string[] {
  const v = getLlmVariant(provider);
  const fields: string[] = [];
  if (v.apiKey !== "hidden") fields.push("api_key");
  if (v.showBaseUrl) fields.push("base_url");
  const advancedRequired = v.value === "azure_openai";
  if (showAdvanced || advancedRequired) fields.push(...v.advancedFields);
  return fields;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `apps/web`): `npx vitest run src/llmProviders.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/llmProviders.ts apps/web/src/llmProviders.test.ts
git commit -m "feat(web): provider-variant config for LLM credentials (per-provider fields + base_url defaults)"
```

---

## Task 6: Provider-aware credential form + test-before-save

**Files:**
- Modify: `apps/web/src/api.ts` (add `testCredentialDraft`, `dynamicOptions`)
- Modify: `apps/web/src/CredentialsPage.tsx` (llm_provider form + Test button)
- Modify: `apps/web/src/editor/NodeDetails.tsx` (`CredentialCreateModal` provider-aware + Test button)

This task is UI wiring; it is verified by `npm run typecheck` + manual reasoning + the Task 5 unit tests that back the field logic. No new unit test file is required, but typecheck MUST stay clean.

- [ ] **Step 1: Add API client methods**

Add to the `api` object in `apps/web/src/api.ts` (near `testCredential`):

```ts
  testCredentialDraft: (body: {
    type: string;
    data: Record<string, string>;
    context?: Record<string, unknown>;
  }) =>
    request<CredentialTestResponse>("/credentials/test-draft", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  dynamicOptions: (
    loaderId: string,
    params: Record<string, string> = {},
  ) => {
    const qs = new URLSearchParams(params).toString();
    return request<{ loader_id: string; options: { value: string; label: string; description?: string }[] }>(
      `/nodes/dynamic-options/${loaderId}${qs ? `?${qs}` : ""}`,
    );
  },
```

- [ ] **Step 2: Make the Credentials-page `llm_provider` form provider-aware**

In `apps/web/src/CredentialsPage.tsx`, replace the flat 9-field `llm_provider` preset (`CredentialsPage.tsx:74-138`) so its `fields` contain only `provider` (the select) — the rest are derived at render time. Then, in `renderField`/the form body, when `preset.type === "llm_provider"`, render fields from `visibleCredentialFields(values.provider, showAdvanced)` plus a toggle that flips `showAdvanced`, and prefill `base_url` from `getLlmVariant(values.provider).baseUrlDefault` when the provider changes and the field is empty.

Concretely:
- Import: `import { getLlmVariant, visibleCredentialFields } from "./llmProviders";`
- Add modal state: `const [showAdvanced, setShowAdvanced] = useState(false);`
- When the provider value changes, set the base_url default if empty:

```ts
function onProviderChange(next: string) {
  const variant = getLlmVariant(next);
  setValues((cur) => ({
    ...cur,
    provider: next,
    base_url: cur.base_url?.trim() ? cur.base_url : (variant.baseUrlDefault ?? ""),
  }));
  setShowAdvanced(false);
}
```

- Render (inside the form, only for `preset.type === "llm_provider"`): the provider `<select>` (options from `LLM_PROVIDER_VARIANTS`), then map `visibleCredentialFields(values.provider, showAdvanced)` through the existing `renderField` using a small field lookup:

```ts
const LLM_FIELD_DEFS: Record<string, CredentialFormField> = {
  api_key: { key: "api_key", label: "API key", kind: "password", placeholder: "Paste provider key" },
  base_url: { key: "base_url", label: "Base URL", placeholder: "https://…" },
  organization: { key: "organization", label: "Organization", placeholder: "Optional OpenAI org" },
  site_url: { key: "site_url", label: "Site URL", placeholder: "Optional OpenRouter HTTP-Referer" },
  app_name: { key: "app_name", label: "App name", placeholder: "Optional OpenRouter X-Title" },
  azure_endpoint: { key: "azure_endpoint", label: "Azure endpoint", placeholder: "https://resource.openai.azure.com" },
  azure_api_version: { key: "azure_api_version", label: "Azure API version", placeholder: "2024-02-15-preview" },
  deployment: { key: "deployment", label: "Deployment", placeholder: "Azure deployment name" },
};
```

- Mark `api_key` required when `getLlmVariant(values.provider).apiKey === "required"`.
- Add an "Advanced" toggle button shown only when `getLlmVariant(values.provider).advancedFields.length > 0` and the provider is not Azure (Azure's are always visible). Toggling sets `showAdvanced`.

- [ ] **Step 3: Add "Test connection" to the Credentials-page create modal**

Add modal state `const [testResult, setTestResult] = useState<CredentialTestResponse | null>(null);` and a button next to Create:

```tsx
<button
  type="button"
  className="btn btn-ghost"
  disabled={busy}
  onClick={async () => {
    setTestResult(null);
    setError("");
    const data: Record<string, string> = {};
    for (const [k, val] of Object.entries(values)) {
      if (typeof val === "string" && val.trim()) data[k] = val.trim();
    }
    try {
      const res = await api.testCredentialDraft({ type: preset.type, data, context: {} });
      setTestResult(res);
      notify(res.ok ? "Credential connected." : res.message, res.ok ? "success" : "error");
    } catch (err) {
      notify(String(err), "error");
    }
  }}
>
  Test connection
</button>
```

Render `testResult.message` inline below the actions when present. Keep the button enabled for any preset that could have a test handler — it is safe to call for all types (the endpoint returns a clear "no test available" message otherwise).

- [ ] **Step 4: Make the node quick-add modal provider-aware**

In `apps/web/src/editor/NodeDetails.tsx`, `CredentialCreateModal` currently renders `fields.map(...)` flat. When `credType === "llm_provider"`, switch to the same provider-first rendering:
- Add `provider` to `fieldValues` state (default `"openai"`).
- Show the provider `<select>` (from `LLM_PROVIDER_VARIANTS`), then `visibleCredentialFields(fieldValues.provider, showAdvanced)`, prefilling `base_url` default on provider change (same `onProviderChange` logic).
- Keep the existing flat rendering for every non-`llm_provider` `credType`.
- Add a "Test connection" button calling `api.testCredentialDraft({ type: credType, data, context: {} })` with the trimmed `fieldValues`.

Import `{ getLlmVariant, visibleCredentialFields, LLM_PROVIDER_VARIANTS }` from `../llmProviders`.

- [ ] **Step 5: Typecheck**

Run (from `apps/web`): `npm run typecheck`
Expected: clean (exit 0).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/CredentialsPage.tsx apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(web): provider-aware LLM credential forms + Test-before-save on both surfaces"
```

---

## Task 7: `LoadOptionsField` model combobox

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (add `LoadOptionsField`, route `load_options` in `ParamField`)
- Test: `apps/web/src/editor/loadOptionsField.test.ts` (pure helpers)

- [ ] **Step 1: Write the failing test for the query-building helper**

```ts
// apps/web/src/editor/loadOptionsField.test.ts
import { describe, expect, it } from "vitest";
import { buildLoadOptionsParams, mergeOptions } from "./NodeDetails";

describe("buildLoadOptionsParams", () => {
  it("includes credential_id + provider/base_url from params, skips empties", () => {
    const out = buildLoadOptionsParams(
      { __noodle_credential__: true, id: "cred1", key: "*" },
      { provider: "ollama", base_url: "", model: "x" },
    );
    expect(out).toEqual({ credential_id: "cred1", provider: "ollama" });
  });

  it("returns provider-only when no credential is selected", () => {
    const out = buildLoadOptionsParams(null, { provider: "openai" });
    expect(out).toEqual({ provider: "openai" });
  });
});

describe("mergeOptions", () => {
  it("keeps a typed free-text value that is not in the fetched list", () => {
    const merged = mergeOptions(["gpt-4o", "gpt-4o-mini"], "my-custom-model");
    expect(merged[0]).toBe("my-custom-model");
    expect(merged).toContain("gpt-4o");
  });

  it("does not duplicate a current value already present", () => {
    const merged = mergeOptions(["gpt-4o"], "gpt-4o");
    expect(merged.filter((m) => m === "gpt-4o")).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `apps/web`): `npx vitest run src/editor/loadOptionsField.test.ts`
Expected: FAIL — exports not found.

- [ ] **Step 3: Add the pure helpers (exported) to `NodeDetails.tsx`**

```ts
// Near the other exported helpers in NodeDetails.tsx
export function buildLoadOptionsParams(
  credential: { id: string } | null,
  params: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (credential?.id) out.credential_id = credential.id;
  for (const key of ["provider", "base_url", "workflow_id"]) {
    const value = params[key];
    if (typeof value === "string" && value.trim()) out[key] = value.trim();
  }
  return out;
}

export function mergeOptions(fetched: string[], current: string): string[] {
  const list = [...fetched];
  if (current && !list.includes(current)) list.unshift(current);
  return list;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `apps/web`): `npx vitest run src/editor/loadOptionsField.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the `LoadOptionsField` component**

Add to `NodeDetails.tsx`. It is an editable combobox using a native `<datalist>` so free-text always works:

```tsx
function LoadOptionsField({
  spec,
  value,
  onChange,
  params,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  params: Record<string, unknown>;
}) {
  const current = String(value ?? "");
  const credential = (() => {
    for (const dep of spec.depends_on ?? []) {
      const v = params[dep];
      if (isCredentialRef(v)) return v;
    }
    return null;
  })();
  const curated = (spec.choices ?? []).map(String);
  const [fetched, setFetched] = useState<string[]>(curated);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const listId = `opts-${spec.name}`;

  async function fetchOptions(): Promise<void> {
    if (!spec.load_options) return;
    setLoading(true);
    setErr("");
    try {
      const res = await api.dynamicOptions(
        spec.load_options,
        buildLoadOptionsParams(credential, params),
      );
      setFetched(res.options.map((o) => o.value));
    } catch (e) {
      setErr("Couldn't load list — type a value or retry.");
    } finally {
      setLoading(false);
    }
  }

  // Auto-fetch once a credential is present.
  useEffect(() => {
    if (credential) void fetchOptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credential?.id]);

  const options = mergeOptions(fetched, current);

  return (
    <div className="load-options-field">
      <div className="load-options-row">
        <input
          className="field-input"
          list={listId}
          placeholder={spec.placeholder || "Select or type a value"}
          value={current}
          onChange={(e) => onChange(e.target.value)}
        />
        <datalist id={listId}>
          {options.map((opt) => (
            <option key={opt} value={opt} />
          ))}
        </datalist>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          title="Refresh list"
          disabled={loading}
          onClick={() => void fetchOptions()}
        >
          {loading ? "…" : "↻"}
        </button>
      </div>
      {err && <p className="field-desc">{err}</p>}
    </div>
  );
}
```

- [ ] **Step 6: Route `load_options` in `ParamField`**

In `ParamField` (NodeDetails.tsx), add this branch **before** the `spec.choices` branch so a dynamic field overrides the strict select, and thread `params` in. `ParamField` already receives `credentialContext` which is the node's `params` object (see `credentialContext={params}` at the call site) — reuse it:

```tsx
  if (spec.load_options) {
    return (
      <LoadOptionsField
        spec={spec}
        value={value}
        onChange={onChange}
        params={credentialContext ?? {}}
      />
    );
  }
```

- [ ] **Step 7: Typecheck + run the editor test suite**

Run (from `apps/web`): `npm run typecheck && npx vitest run src/editor`
Expected: clean typecheck; all editor tests PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx apps/web/src/editor/loadOptionsField.test.ts
git commit -m "feat(web): editable model combobox (LoadOptionsField) backed by dynamic-options + free text"
```

---

## Task 8: Full verification pass

- [ ] **Step 1: Backend suites**

Run: `.venv/Scripts/python.exe -m pytest apps/api/tests/test_credentials_v2.py apps/api/tests/test_nodes.py packages/nodes/tests/test_model_options.py packages/nodes/tests/test_llm_nodes.py -q`
Expected: all PASS.

- [ ] **Step 2: Frontend suite + typecheck + build**

Run (from `apps/web`): `npx vitest run && npm run typecheck && npm run build`
Expected: all green, build succeeds.

- [ ] **Step 3: Commit any incidental fixes**

```bash
git add -A && git commit -m "test: full verification pass for LLM credential/model UX" || echo "nothing to commit"
```

---

## Self-review (completed during planning)

- **Spec coverage:** Provider-aware fields → Tasks 5/6. Dynamic models → Tasks 1/2/4/7. Free-text fallback → Task 7 (`mergeOptions` + datalist). Test-before-save → Tasks 3/6. Secured endpoint → Task 2. Curated/offline fallback → Tasks 1 (backend) + 5/7 (frontend). Scope-UUID pickers correctly **excluded** (non-goal).
- **Type consistency:** `buildLoadOptionsParams`/`mergeOptions`/`getLlmVariant`/`visibleCredentialFields` names used identically across tasks. `dynamicOptions` return shape matches the endpoint's `{loader_id, options}`. `CredentialTestDraftRequest` fields (`type`,`data`,`context`) match the frontend `testCredentialDraft` body.
- **Placeholder scan:** No TBD/TODO; every code step shows complete code. Task 6 is intentionally UI-wiring verified by typecheck rather than a new unit test, with the field logic covered by Task 5's unit tests.
- **Known soft spots flagged inline:** Task 1 Step 4 (registration import location) and Task 4 Step 1 (manifest accessor name) include fallbacks to match existing patterns.
