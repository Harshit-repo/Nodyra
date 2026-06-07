# Polling & Webhook Triggers + File Read Nodes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add polling infrastructure to `ProviderTriggerSpec`, implement Slack and Stripe webhook triggers, add Google Sheets / Notion / RSS polling triggers, and add file-reading nodes (Text, CSV, JSON, XML) with browser-upload + server-path dual-source and `output_as_dataset` toggle.

**Architecture:** Extend `ProviderTriggerSpec` with an optional `poll` hook and `poll_interval_seconds`; the existing scheduler `_tick()` fires poll hooks for due subscriptions. Browser file uploads land at a new `POST /artifacts/upload` endpoint and are referenced by artifact_id via a new `file_upload` widget type in the node inspector.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, asyncio, React/TypeScript, pytest

> **Scope note:** This plan covers P0–P3 of the design spec. P4 (expiring subscription triggers: Google Drive, Sheets change-channel, Airtable webhook, Microsoft Graph) and P5 (IMAP, File Upload Trigger) require a follow-up plan after P4's subscription-renewal infrastructure is built.

---

## File Map

**New files:**
- `packages/nodes/noodle_nodes/integrations_v2/providers/slack/triggers.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/stripe/triggers.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/triggers.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/notion/triggers.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/rss/__init__.py`
- `packages/nodes/noodle_nodes/integrations_v2/providers/rss/triggers.py`
- `packages/nodes/noodle_nodes/file_nodes.py`
- `packages/nodes/tests/test_slack_trigger_v2.py`
- `packages/nodes/tests/test_stripe_trigger_v2.py`
- `packages/nodes/tests/test_google_sheets_new_row_trigger.py`
- `packages/nodes/tests/test_notion_trigger_v2.py`
- `packages/nodes/tests/test_rss_trigger.py`
- `packages/nodes/tests/test_file_nodes.py`
- `apps/api/tests/test_artifact_upload.py`

**Modified files:**
- `packages/nodes/noodle_nodes/integrations_v2/specs.py` — add poll types
- `packages/nodes/noodle_nodes/integrations_v2/providers/slack/__init__.py` — import triggers
- `packages/nodes/noodle_nodes/integrations_v2/providers/stripe/__init__.py` — import triggers
- `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/__init__.py` — import triggers
- `packages/nodes/noodle_nodes/integrations_v2/providers/notion/__init__.py` — import triggers
- `packages/nodes/noodle_nodes/__init__.py` — import rss + file_nodes
- `apps/api/app/services/triggers.py` — add polling pass
- `apps/api/app/routers/artifacts.py` — add upload endpoint
- `apps/web/src/editor/NodeDetails.tsx` — add file_upload widget
- `apps/web/src/api.ts` — add uploadArtifact()
- `docs/provider-coverage-matrix.md` — update rows

---

## Phase 0 — Foundation

### Task 1: Add poll types to `ProviderTriggerSpec`

**Files:**
- Modify: `packages/nodes/noodle_nodes/integrations_v2/specs.py`
- Test: `packages/nodes/tests/test_integrations_v2_registry.py`

- [ ] **Step 1: Write failing test**

```python
# In packages/nodes/tests/test_integrations_v2_registry.py — add at the bottom:

from noodle_nodes.integrations_v2.specs import (
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

def test_provider_trigger_spec_has_poll_fields() -> None:
    spec = ProviderTriggerSpec(
        node_id="test_poll_trigger",
        name="Test Poll Trigger",
        provider="test",
        resource="resource",
        event="poll",
        poll=lambda ctx: ProviderTriggerPollResult(events=[], cursor={}),
        poll_interval_seconds=60,
    )
    assert spec.poll is not None
    assert spec.poll_interval_seconds == 60
    assert spec.handle_event is None

def test_poll_context_and_result_are_dataclasses() -> None:
    ctx = ProviderTriggerPollContext(params={"key": "val"}, cursor={"last": "abc"})
    assert ctx.params == {"key": "val"}
    assert ctx.cursor == {"last": "abc"}
    result = ProviderTriggerPollResult(events=[{"a": 1}], cursor={"last": "xyz"})
    assert result.events == [{"a": 1}]
    assert result.cursor == {"last": "xyz"}
```

- [ ] **Step 2: Run test to verify it fails**

```
cd packages/nodes && python -m pytest tests/test_integrations_v2_registry.py::test_provider_trigger_spec_has_poll_fields -v
```

Expected: `ImportError` or `TypeError` — `ProviderTriggerPollContext` not defined.

- [ ] **Step 3: Implement — add poll types to `specs.py`**

After the `ProviderTriggerHandleEvent` line (line 157), insert:

```python
@dataclass(frozen=True)
class ProviderTriggerPollContext:
    """Input passed to a provider trigger's poll hook."""

    params: dict[str, Any]
    cursor: dict[str, Any]


@dataclass
class ProviderTriggerPollResult:
    """Return value from a provider trigger's poll hook."""

    events: list[dict[str, Any]]
    cursor: dict[str, Any]


ProviderTriggerPoll = Callable[[ProviderTriggerPollContext], ProviderTriggerPollResult]
```

Then add two fields to `ProviderTriggerSpec` after `handle_event`:

```python
    poll: ProviderTriggerPoll | None = None
    poll_interval_seconds: int = 300
```

Full updated `ProviderTriggerSpec` tail (lines 160–181 become):

```python
@dataclass(frozen=True)
class ProviderTriggerSpec:
    node_id: str
    name: str
    provider: str
    resource: str
    event: str
    category: str = "Triggers"
    version: str = "1.0.0"
    description: str = ""
    icon: str | None = None
    params: Sequence[OperationParamSpec] = field(default_factory=tuple)
    output_kind: PortDataKind = PortDataKind.main
    requirements: Sequence[str] = field(default_factory=tuple)
    activate: ProviderTriggerActivate | None = None
    deactivate: ProviderTriggerDeactivate | None = None
    handle_event: ProviderTriggerHandleEvent | None = None
    poll: ProviderTriggerPoll | None = None
    poll_interval_seconds: int = 300

    @property
    def trigger_key(self) -> str:
        return f"{self.provider}.{self.resource}.{self.event}"
```

- [ ] **Step 4: Run test to verify it passes**

```
cd packages/nodes && python -m pytest tests/test_integrations_v2_registry.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/specs.py packages/nodes/tests/test_integrations_v2_registry.py
git commit -m "feat(specs): add poll hook and poll_interval_seconds to ProviderTriggerSpec"
```

---

### Task 2: Add polling pass to the scheduler

**Files:**
- Modify: `apps/api/app/services/triggers.py`
- Test: `apps/api/tests/test_artifact_upload.py` *(polling tests go in a new file)*

Actually create: `apps/api/tests/test_polling_scheduler.py`

- [ ] **Step 1: Write failing test**

```python
# apps/api/tests/test_polling_scheduler.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.specs import (
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)


def _make_poll_spec(poll_fn):
    return ProviderTriggerSpec(
        node_id="test_poll",
        name="Test Poll",
        provider="test",
        resource="res",
        event="poll",
        poll=poll_fn,
        poll_interval_seconds=60,
    )


@pytest.mark.asyncio
async def test_poll_subscriptions_skips_non_due(monkeypatch):
    """Subscriptions whose next_poll_at is in the future must not fire."""
    from app.services.triggers import _poll_subscriptions

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    mock_sub = MagicMock()
    mock_sub.id = "sub1"
    mock_sub.status = "active"
    mock_sub.node_type = "test_poll"
    mock_sub.workflow_id = "wf1"
    mock_sub.node_id = "node1"
    mock_sub.config = {"next_poll_at": future}

    fired = []

    spec = _make_poll_spec(lambda ctx: (fired.append(1), ProviderTriggerPollResult(events=[], cursor={}))[1])

    with (
        patch("app.services.triggers._load_active_poll_subscriptions", new_callable=AsyncMock, return_value=[mock_sub]),
        patch("app.services.triggers.is_registered_provider_trigger", return_value=True),
        patch("app.services.triggers.get_registered_provider_trigger", return_value=MagicMock(spec=MagicMock(poll=spec.poll, poll_interval_seconds=60))),
    ):
        await _poll_subscriptions(datetime.now(UTC))

    assert fired == []


@pytest.mark.asyncio
async def test_poll_subscriptions_fires_overdue(monkeypatch):
    """Subscriptions past next_poll_at must have their poll hook called."""
    from app.services.triggers import _poll_subscriptions

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    mock_sub = MagicMock()
    mock_sub.id = "sub1"
    mock_sub.status = "active"
    mock_sub.node_type = "test_poll"
    mock_sub.workflow_id = "wf1"
    mock_sub.node_id = "node1"
    mock_sub.config = {"next_poll_at": past, "poll_cursor": {}}

    fired = []
    mock_registered = MagicMock()
    mock_registered.spec.poll = lambda ctx: (fired.append(ctx), ProviderTriggerPollResult(events=[], cursor={}))[1]
    mock_registered.spec.poll_interval_seconds = 60

    with (
        patch("app.services.triggers._load_active_poll_subscriptions", new_callable=AsyncMock, return_value=[mock_sub]),
        patch("app.services.triggers.is_registered_provider_trigger", return_value=True),
        patch("app.services.triggers.get_registered_provider_trigger", return_value=mock_registered),
        patch("app.services.triggers._execute_poll", new_callable=AsyncMock) as mock_execute,
    ):
        await _poll_subscriptions(datetime.now(UTC))

    mock_execute.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && python -m pytest tests/test_polling_scheduler.py -v
```

Expected: `ImportError` — `_poll_subscriptions` not defined.

- [ ] **Step 3: Implement polling pass in `triggers.py`**

At the top of `triggers.py`, extend the imports:

```python
# existing import:
from app.models import Deployment, Run, ScheduleState, Workflow, WorkflowVersion
# change to:
from app.models import (
    Deployment,
    ProviderTriggerSubscription,
    Run,
    ScheduleState,
    Workflow,
    WorkflowVersion,
)
```

Add below the existing imports (before `logger = ...`):

```python
from datetime import timedelta
```

Add these three functions anywhere before `_tick()`:

```python
async def _load_active_poll_subscriptions() -> list[ProviderTriggerSubscription]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(ProviderTriggerSubscription).where(
                ProviderTriggerSubscription.status == "active"
            )
        )
        return list(rows.all())


async def _execute_poll(
    sub: ProviderTriggerSubscription,
    now: datetime,
) -> None:
    from noodle_nodes.integrations_v2.registry import get_registered_provider_trigger
    from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext

    registered = get_registered_provider_trigger(sub.node_type)
    spec = registered.spec

    # Load workflow + node params + resolve credentials.
    async with SessionLocal() as session:
        workflow = await session.get(
            Workflow, sub.workflow_id, options=[selectinload(Workflow.versions)]
        )
        if workflow is None or not workflow.active or not workflow.versions:
            return
        version = workflow.versions[-1]
        graph = version.graph or {}
        node_params: dict = {}
        for node in graph.get("nodes", []):
            if node.get("id") == sub.node_id:
                node_params = node.get("params") or {}
                break
        from app.services.credentials import resolve_credential_refs
        resolved = await resolve_credential_refs(
            session,
            node_params,
            workflow_id=sub.workflow_id,
            environment_id=workflow.environment_id,
        )

    cursor = (sub.config or {}).get("poll_cursor") or {}
    ctx = ProviderTriggerPollContext(params=resolved, cursor=cursor)
    result = await asyncio.to_thread(spec.poll, ctx)

    next_poll_at = (now + timedelta(seconds=spec.poll_interval_seconds)).isoformat()

    # Persist updated cursor before dispatching so a crash during dispatch
    # doesn't re-process the same events on next tick.
    async with SessionLocal() as session:
        row = await session.get(ProviderTriggerSubscription, sub.id)
        if row is None:
            return
        row.config = {
            **(row.config or {}),
            "poll_cursor": result.cursor,
            "next_poll_at": next_poll_at,
        }
        if result.events:
            row.last_event_at = now
        await session.commit()

    if not result.events:
        return

    # Reload graph for dispatch (avoids holding session open during runs).
    async with SessionLocal() as session:
        workflow = await session.get(
            Workflow, sub.workflow_id, options=[selectinload(Workflow.versions)]
        )
        if workflow is None or not workflow.versions:
            return
        version = workflow.versions[-1]
        graph = version.graph or {}

    for event_payload in result.events:
        await start_run(
            sub.workflow_id,
            graph,
            version.version,
            workflow_version_id=version.id,
            mode="production",
            trigger_type="provider_trigger",
            trigger_node_id=sub.node_id,
            cache={sub.node_id: {"main": event_payload}},
        )


async def _poll_subscriptions(now: datetime) -> None:
    """Fire poll hooks for any active provider trigger subscriptions that are due."""
    from noodle_nodes.integrations_v2.registry import (
        get_registered_provider_trigger,
        is_registered_provider_trigger,
    )

    subs = await _load_active_poll_subscriptions()
    for sub in subs:
        if not is_registered_provider_trigger(sub.node_type):
            continue
        registered = get_registered_provider_trigger(sub.node_type)
        if registered.spec.poll is None:
            continue

        config = sub.config or {}
        next_poll_str = config.get("next_poll_at")
        if next_poll_str:
            try:
                next_poll = datetime.fromisoformat(next_poll_str)
                if next_poll.tzinfo is None:
                    next_poll = next_poll.replace(tzinfo=UTC)
                if now < next_poll:
                    continue
            except (ValueError, TypeError):
                pass

        try:
            await _execute_poll(sub, now)
        except Exception:
            logger.warning(
                "poll failed for subscription %s (%s)", sub.id, sub.node_type, exc_info=True
            )
            async with SessionLocal() as session:
                row = await session.get(ProviderTriggerSubscription, sub.id)
                if row is not None:
                    row.error = "Poll hook raised an exception — check logs"
                    await session.commit()
```

In `_tick()`, add a call to `_poll_subscriptions` at the very end (after the `for ... in due_deployment` loop):

```python
    # --- 3. Fire poll hooks for due provider trigger subscriptions.
    await _poll_subscriptions(now)
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_polling_scheduler.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/triggers.py apps/api/tests/test_polling_scheduler.py
git commit -m "feat(scheduler): add polling pass for ProviderTriggerSpec poll hooks"
```

---

### Task 3: Add `POST /artifacts/upload` endpoint

**Files:**
- Modify: `apps/api/app/routers/artifacts.py`
- Test: `apps/api/tests/test_artifact_upload.py`

- [ ] **Step 1: Write failing test**

```python
# apps/api/tests/test_artifact_upload.py
import io
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_artifact_returns_artifact_info(client: AsyncClient) -> None:
    content = b"col1,col2\nval1,val2\n"
    resp = await client.post(
        "/artifacts/upload",
        files={"file": ("test.csv", io.BytesIO(content), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test.csv"
    assert data["content_type"] == "text/csv"
    assert data["size_bytes"] == len(content)
    assert data["kind"] == "upload"
    assert "id" in data


@pytest.mark.asyncio
async def test_upload_artifact_rejects_missing_file(client: AsyncClient) -> None:
    resp = await client.post("/artifacts/upload")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_artifact_rejects_oversized_file(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_size_bytes", 10)
    content = b"x" * 100
    resp = await client.post(
        "/artifacts/upload",
        files={"file": ("big.bin", io.BytesIO(content), "application/octet-stream")},
    )
    assert resp.status_code == 413
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && python -m pytest tests/test_artifact_upload.py -v
```

Expected: `404` — endpoint does not exist yet.

- [ ] **Step 3: Add `max_upload_size_bytes` to settings if absent**

Check `apps/api/app/config.py` for the field. If absent, add:

```python
max_upload_size_bytes: int = 52_428_800  # 50 MB
```

- [ ] **Step 4: Implement upload endpoint in `artifacts.py`**

Add imports at top of file:

```python
import uuid
from fastapi import File, UploadFile
from app.config import settings
from app.models import Artifact
from app.services.artifact_backends import get_backend
```

Add after the existing `router` definition:

```python
@router.post("/artifacts/upload", response_model=ArtifactInfo)
async def upload_artifact(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_permission("artifacts:write")),
) -> ArtifactInfo:
    """Upload a file from the browser and store it as a run-less artifact."""
    content = await file.read()
    max_bytes = getattr(settings, "max_upload_size_bytes", 52_428_800)
    if len(content) > max_bytes:
        from fastapi import Response
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds maximum upload size of {max_bytes} bytes",
        )

    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    artifact_id = uuid.uuid4().hex

    backend = get_backend()
    storage_key = backend.write_bytes(artifact_id, content, content_type=content_type)

    row = Artifact(
        id=artifact_id,
        run_id=None,
        node_id=None,
        name=filename,
        kind="upload",
        content_type=content_type,
        size_bytes=len(content),
        storage_backend=backend.name,
        storage_key=storage_key,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _info(row)
```

- [ ] **Step 5: Run tests**

```
cd apps/api && python -m pytest tests/test_artifact_upload.py -v
```

Expected: All pass. If `require_permission` or `backend.write_bytes` signatures differ, adjust to match the existing patterns in the file.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/artifacts.py apps/api/app/config.py apps/api/tests/test_artifact_upload.py
git commit -m "feat(api): add POST /artifacts/upload endpoint for browser file uploads"
```

---

### Task 4: Add `file_upload` widget to the frontend

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/editor/NodeDetails.tsx`

- [ ] **Step 1: Add `uploadArtifact` to `api.ts`**

Find the `request` helper (line ~82). After the existing exported functions, add:

```typescript
export async function uploadArtifact(file: File): Promise<ArtifactInfo> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const body = new FormData();
  body.append("file", file);
  const resp = await fetch(`${BASE}/artifacts/upload`, { method: "POST", headers, body });
  if (resp.status === 401) {
    setToken(null);
    setUser(null);
    unauthorizedHandler?.();
    throw new Error("401 Unauthorized");
  }
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, `Upload failed: ${resp.status}`, detail);
  }
  return resp.json() as Promise<ArtifactInfo>;
}
```

- [ ] **Step 2: Add `file_upload` widget case in `NodeDetails.tsx`**

In `ParamField` (line ~1758), add the new widget case before the `spec.widget === "routes_table"` check (line ~1804):

```typescript
  if (spec.widget === "file_upload") {
    return (
      <FileUploadField
        value={String(value ?? "")}
        onChange={onChange}
      />
    );
  }
```

Add the `FileUploadField` component above `ParamField`:

```typescript
function FileUploadField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [filename, setFilename] = useState<string | null>(null);
  const { notify } = useToast();

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const info = await uploadArtifact(file);
      onChange(info.id);
      setFilename(info.name);
    } catch (err) {
      notify({ type: "error", message: String(err) });
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  return (
    <div className="file-upload-field">
      {value && (
        <div className="file-upload-current">
          <span className="file-upload-name">{filename ?? value}</span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => { onChange(""); setFilename(null); }}
          >
            ×
          </button>
        </div>
      )}
      <label className={`btn btn-sm ${busy ? "btn-disabled" : ""}`}>
        {busy ? "Uploading…" : value ? "Replace file" : "Choose file"}
        <input
          type="file"
          style={{ display: "none" }}
          disabled={busy}
          onChange={handleFile}
        />
      </label>
    </div>
  );
}
```

Add `uploadArtifact` to the `api` imports at the top of `NodeDetails.tsx`:

```typescript
import { api, uploadArtifact } from "../api";
```

- [ ] **Step 3: Verify TypeScript compiles**

```
cd apps/web && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(web): add file_upload widget and uploadArtifact API helper"
```

---

## Phase 1 — Webhook Triggers (Slack + Stripe)

### Task 5: Slack Event Trigger

**Files:**
- Create: `packages/nodes/noodle_nodes/integrations_v2/providers/slack/triggers.py`
- Modify: `packages/nodes/noodle_nodes/integrations_v2/providers/slack/__init__.py`
- Create: `packages/nodes/tests/test_slack_trigger_v2.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/nodes/tests/test_slack_trigger_v2.py
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest

import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.slack import triggers as slack_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerRequest


def _signed_headers(raw_body: bytes, signing_secret: str, timestamp: int | None = None) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    basestring = f"v0:{ts}:{raw_body.decode()}".encode()
    sig = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }


def test_slack_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "slack_event_trigger_v2" in manifests
    m = manifests["slack_event_trigger_v2"]
    assert m.name == "Slack Event Trigger"
    assert m.icon == "brand:slack"
    assert m.category == "Triggers"


def test_slack_trigger_url_challenge() -> None:
    raw = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    secret = "signing_secret"
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body={"type": "url_verification", "challenge": "abc123"},
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message"},
    )
    assert event.response_status == 200
    assert event.response_body == {"challenge": "abc123"}
    assert event.payload is None


def test_slack_trigger_rejects_bad_signature() -> None:
    raw = b'{"type":"event_callback","event":{"type":"message"}}'
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers={
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=badhash",
            },
            query={},
            body={"type": "event_callback"},
            raw_body=raw,
        ),
        {"signing_secret": "secret", "event_types": "message"},
    )
    assert event.response_status == 401
    assert event.payload is None


def test_slack_trigger_rejects_stale_timestamp() -> None:
    secret = "signing_secret"
    raw = b'{"type":"event_callback","event":{"type":"message"}}'
    stale_ts = int(time.time()) - 400  # older than 5 min tolerance
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret, timestamp=stale_ts),
            query={},
            body={"type": "event_callback"},
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message"},
    )
    assert event.response_status == 401
    assert event.payload is None


def test_slack_trigger_filters_unwanted_event_types() -> None:
    secret = "signing_secret"
    body = {"type": "event_callback", "event": {"type": "reaction_added"}, "event_id": "Ev1"}
    raw = json.dumps(body).encode()
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body=body,
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message"},
    )
    assert event.payload is None
    assert event.response_status == 200


def test_slack_trigger_dispatches_matching_event() -> None:
    secret = "signing_secret"
    body = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev42",
        "event": {"type": "message", "text": "hello", "user": "U1", "channel": "C1"},
    }
    raw = json.dumps(body).encode()
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body=body,
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message, reaction_added"},
    )
    assert event.response_status == 202
    assert event.dedupe_key == "slack:Ev42"
    assert event.payload is not None
    assert event.payload["event_type"] == "message"
    assert event.payload["team_id"] == "T123"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_slack_trigger_v2.py -v
```

Expected: `ImportError` — triggers module not found.

- [ ] **Step 3: Create `slack/triggers.py`**

```python
"""Slack provider trigger spec and lifecycle hooks."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
)

_TIMESTAMP_TOLERANCE_SECONDS = 300


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _verify_slack_signature(signing_secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
    h = _lower_headers(headers)
    timestamp_str = h.get("x-slack-request-timestamp", "")
    signature = h.get("x-slack-signature", "")
    try:
        ts = int(timestamp_str)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        return False
    basestring = f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _parse_event_types(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,\n]", raw) if p.strip()]


def handle_slack_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    signing_secret = str(params.get("signing_secret") or "").strip()
    if signing_secret and not _verify_slack_signature(signing_secret, request.raw_body, request.headers):
        return ProviderTriggerEvent(
            payload=None,
            response_body={"error": "Slack signature verification failed"},
            response_status=401,
        )

    body = request.body if isinstance(request.body, dict) else {}
    event_type = str(body.get("type") or "")

    # URL verification challenge — Slack sends this when setting up the event subscription.
    if event_type == "url_verification":
        return ProviderTriggerEvent(
            payload=None,
            response_body={"challenge": body.get("challenge", "")},
            response_status=200,
        )

    if event_type != "event_callback":
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "ignored"},
            response_status=200,
        )

    inner_event = body.get("event") or {}
    inner_type = str(inner_event.get("type") or "")
    allowed = _parse_event_types(params.get("event_types"))
    if allowed and inner_type not in allowed:
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "event type not subscribed"},
            response_status=200,
        )

    event_id = str(body.get("event_id") or "")
    return ProviderTriggerEvent(
        payload={
            "provider": "slack",
            "event_type": inner_type,
            "team_id": str(body.get("team_id") or ""),
            "event_id": event_id,
            "event": inner_event,
            "authorizations": body.get("authorizations"),
        },
        dedupe_key=f"slack:{event_id}" if event_id else None,
        response_body={"message": "accepted"},
        response_status=202,
    )


SLACK_EVENT_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="slack_event_trigger_v2",
    name="Slack Event Trigger",
    provider="slack",
    resource="event",
    event="callback",
    description=(
        "Start a workflow when a Slack event fires. "
        "Configure your Slack app's Event Subscriptions URL to point to the callback URL shown in this node."
    ),
    icon="brand:slack",
    params=(
        OperationParamSpec(
            name="signing_secret",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="generic",
                key="value",
                label="Signing secret",
                fields=["value"],
                multi=False,
            ),
            description="Slack app signing secret used to verify event payloads.",
        ),
        OperationParamSpec(
            name="event_types",
            default="message",
            placeholder="message, reaction_added, member_joined_channel",
            description="Comma-separated Slack event types to accept. Leave blank to accept all.",
        ),
    ),
    handle_event=lambda request, params: handle_slack_event(request, params),
)


register_provider_trigger(SLACK_EVENT_TRIGGER_SPEC)
```

- [ ] **Step 4: Update `slack/__init__.py`**

```python
"""Slack v2 provider nodes."""

from noodle_nodes.integrations_v2.providers.slack import operations as operations
from noodle_nodes.integrations_v2.providers.slack import triggers as triggers

__all__ = ["operations", "triggers"]
```

- [ ] **Step 5: Run tests**

```
cd packages/nodes && python -m pytest tests/test_slack_trigger_v2.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/providers/slack/triggers.py \
        packages/nodes/noodle_nodes/integrations_v2/providers/slack/__init__.py \
        packages/nodes/tests/test_slack_trigger_v2.py
git commit -m "feat(slack): add Slack event trigger with signing secret verification"
```

---

### Task 6: Stripe Event Trigger

**Files:**
- Create: `packages/nodes/noodle_nodes/integrations_v2/providers/stripe/triggers.py`
- Modify: `packages/nodes/noodle_nodes/integrations_v2/providers/stripe/__init__.py`
- Create: `packages/nodes/tests/test_stripe_trigger_v2.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/nodes/tests/test_stripe_trigger_v2.py
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.stripe import triggers as stripe_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerRequest


def _stripe_signature_header(raw_body: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    payload = f"{ts}.{raw_body.decode()}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_stripe_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "stripe_event_trigger_v2" in manifests
    m = manifests["stripe_event_trigger_v2"]
    assert m.name == "Stripe Event Trigger"
    assert m.icon == "brand:stripe"


def test_stripe_trigger_verifies_signature() -> None:
    secret = "whsec_test"
    body = {"id": "evt_1", "type": "payment_intent.succeeded", "data": {"object": {}}}
    raw = json.dumps(body).encode()
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": _stripe_signature_header(raw, secret)},
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret, "event_types": "payment_intent.succeeded"},
    )
    assert event.response_status == 202
    assert event.dedupe_key == "stripe:evt_1"
    assert event.payload is not None
    assert event.payload["event_type"] == "payment_intent.succeeded"


def test_stripe_trigger_rejects_bad_signature() -> None:
    raw = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": "t=123,v1=badhash"},
            query={},
            body={},
            raw_body=raw,
        ),
        {"webhook_secret": "whsec_test", "event_types": ""},
    )
    assert event.response_status == 401
    assert event.payload is None


def test_stripe_trigger_filters_event_types() -> None:
    secret = "whsec_test"
    body = {"id": "evt_2", "type": "customer.created", "data": {"object": {}}}
    raw = json.dumps(body).encode()
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": _stripe_signature_header(raw, secret)},
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret, "event_types": "payment_intent.succeeded"},
    )
    assert event.payload is None
    assert event.response_status == 200


def test_stripe_trigger_accepts_all_when_event_types_blank() -> None:
    secret = "whsec_test"
    body = {"id": "evt_3", "type": "anything.happened", "data": {"object": {}}}
    raw = json.dumps(body).encode()
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": _stripe_signature_header(raw, secret)},
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret, "event_types": ""},
    )
    assert event.response_status == 202
    assert event.payload is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_stripe_trigger_v2.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `stripe/triggers.py`**

```python
"""Stripe provider trigger spec and lifecycle hooks."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
)

_TIMESTAMP_TOLERANCE_SECONDS = 300


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _verify_stripe_signature(webhook_secret: str, raw_body: bytes, signature_header: str) -> bool:
    """Verify Stripe-Signature header per https://stripe.com/docs/webhooks/signatures"""
    parts: dict[str, list[str]] = {}
    for chunk in signature_header.split(","):
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            parts.setdefault(key.strip(), []).append(val.strip())
    try:
        ts = int((parts.get("t") or [""])[0])
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        return False
    signed_payload = f"{ts}.{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    provided = parts.get("v1") or []
    return any(hmac.compare_digest(expected, v) for v in provided)


def _parse_event_types(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,\n]", raw) if p.strip()]


def handle_stripe_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    webhook_secret = str(params.get("webhook_secret") or "").strip()
    headers = _lower_headers(request.headers)
    sig_header = headers.get("stripe-signature", "")

    if webhook_secret and not _verify_stripe_signature(webhook_secret, request.raw_body, sig_header):
        return ProviderTriggerEvent(
            payload=None,
            response_body={"error": "Stripe signature verification failed"},
            response_status=401,
        )

    body = request.body if isinstance(request.body, dict) else {}
    event_type = str(body.get("type") or "")
    allowed = _parse_event_types(params.get("event_types"))
    if allowed and event_type not in allowed:
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "event type not subscribed"},
            response_status=200,
        )

    event_id = str(body.get("id") or "")
    return ProviderTriggerEvent(
        payload={
            "provider": "stripe",
            "event_type": event_type,
            "event_id": event_id,
            "livemode": body.get("livemode", False),
            "data": body.get("data"),
            "body": body,
        },
        dedupe_key=f"stripe:{event_id}" if event_id else None,
        response_body={"received": True},
        response_status=202,
    )


STRIPE_EVENT_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="stripe_event_trigger_v2",
    name="Stripe Event Trigger",
    provider="stripe",
    resource="event",
    event="webhook",
    description=(
        "Start a workflow on Stripe webhook events. "
        "Create a webhook endpoint in your Stripe dashboard pointing to the callback URL shown in this node."
    ),
    icon="brand:stripe",
    params=(
        OperationParamSpec(
            name="webhook_secret",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="generic",
                key="value",
                label="Webhook signing secret",
                fields=["value"],
                multi=False,
            ),
            description="Stripe webhook signing secret (whsec_...) from the Stripe dashboard.",
        ),
        OperationParamSpec(
            name="event_types",
            default="",
            placeholder="payment_intent.succeeded, invoice.paid",
            description="Comma-separated Stripe event types to accept. Leave blank to accept all.",
        ),
    ),
    handle_event=lambda request, params: handle_stripe_event(request, params),
)


register_provider_trigger(STRIPE_EVENT_TRIGGER_SPEC)
```

- [ ] **Step 4: Update `stripe/__init__.py`**

```python
"""Stripe v2 provider nodes."""

from noodle_nodes.integrations_v2.providers.stripe import operations as operations
from noodle_nodes.integrations_v2.providers.stripe import triggers as triggers

__all__ = ["operations", "triggers"]
```

- [ ] **Step 5: Run tests**

```
cd packages/nodes && python -m pytest tests/test_stripe_trigger_v2.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/providers/stripe/triggers.py \
        packages/nodes/noodle_nodes/integrations_v2/providers/stripe/__init__.py \
        packages/nodes/tests/test_stripe_trigger_v2.py
git commit -m "feat(stripe): add Stripe event trigger with webhook signature verification"
```

---

## Phase 2 — Polling Triggers

### Task 7: Google Sheets New-Row Trigger (polling)

**Files:**
- Create: `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/triggers.py`
- Modify: `packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/__init__.py`
- Create: `packages/nodes/tests/test_google_sheets_new_row_trigger.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/nodes/tests/test_google_sheets_new_row_trigger.py
from __future__ import annotations
from unittest.mock import MagicMock, patch
import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.google_sheets import triggers as gs_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _mock_transport(return_value):
    t = MagicMock()
    t.request.return_value = return_value
    return t


def test_google_sheets_new_row_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "google_sheets_new_row_trigger_v2" in manifests
    m = manifests["google_sheets_new_row_trigger_v2"]
    assert m.name == "Google Sheets New Row"
    assert m.category == "Triggers"


def test_poll_first_run_establishes_cursor_no_events() -> None:
    """On first poll (empty cursor) no events should fire — just sets the baseline."""
    with patch(
        "noodle_nodes.integrations_v2.providers.google_sheets.triggers._transport"
    ) as mock_t:
        mock_t.return_value = _mock_transport(
            {"values": [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]}
        )
        result = gs_triggers.poll_new_rows(
            ProviderTriggerPollContext(
                params={
                    "credentials": {"access_token": "tok"},
                    "spreadsheet_id": "sheet1",
                    "sheet_name": "Sheet1",
                    "header_row": 1,
                },
                cursor={},  # empty — first run
            )
        )
    assert result.events == []
    assert result.cursor["last_row_index"] == 2  # 2 data rows (1-indexed after header)


def test_poll_detects_new_rows() -> None:
    """Rows beyond last_row_index should be returned as events."""
    with patch(
        "noodle_nodes.integrations_v2.providers.google_sheets.triggers._transport"
    ) as mock_t:
        mock_t.return_value = _mock_transport(
            {"values": [["Name", "Age"], ["Alice", "30"], ["Bob", "25"], ["Carol", "28"]]}
        )
        result = gs_triggers.poll_new_rows(
            ProviderTriggerPollContext(
                params={
                    "credentials": {"access_token": "tok"},
                    "spreadsheet_id": "sheet1",
                    "sheet_name": "Sheet1",
                    "header_row": 1,
                },
                cursor={"last_row_index": 2},  # previously saw 2 data rows
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["row"] == {"Name": "Carol", "Age": "28"}
    assert result.cursor["last_row_index"] == 3


def test_poll_no_new_rows_returns_empty() -> None:
    with patch(
        "noodle_nodes.integrations_v2.providers.google_sheets.triggers._transport"
    ) as mock_t:
        mock_t.return_value = _mock_transport(
            {"values": [["Name", "Age"], ["Alice", "30"]]}
        )
        result = gs_triggers.poll_new_rows(
            ProviderTriggerPollContext(
                params={
                    "credentials": {"access_token": "tok"},
                    "spreadsheet_id": "sheet1",
                    "sheet_name": "Sheet1",
                    "header_row": 1,
                },
                cursor={"last_row_index": 1},
            )
        )
    assert result.events == []
    assert result.cursor["last_row_index"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_google_sheets_new_row_trigger.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `google_sheets/triggers.py`**

```python
"""Google Sheets polling trigger — detects new rows appended to a sheet."""
from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

SHEETS_API_BASE = "https://sheets.googleapis.com"


def _transport(credentials: Any) -> ProviderTransport:
    creds = credentials if isinstance(credentials, dict) else {}
    token = str(creds.get("access_token") or creds.get("api_key") or "")
    if not token:
        raise ValueError("google_sheets_new_row_trigger_v2: credentials are required")
    return ProviderTransport(
        provider="google_sheets",
        base_url=SHEETS_API_BASE,
        default_headers={"Authorization": f"Bearer {token}"},
    )


def _sheet_range(sheet_name: str) -> str:
    safe = sheet_name.replace("'", "''") if sheet_name else "Sheet1"
    return f"'{safe}'"


def poll_new_rows(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    spreadsheet_id = str(params.get("spreadsheet_id") or "").strip()
    if not spreadsheet_id:
        raise ValueError("google_sheets_new_row_trigger_v2: spreadsheet_id is required")
    sheet_name = str(params.get("sheet_name") or "Sheet1").strip()
    try:
        header_row = max(1, int(params.get("header_row") or 1))
    except (ValueError, TypeError):
        header_row = 1

    transport = _transport(params.get("credentials"))
    response = transport.request(
        "GET",
        f"/v4/spreadsheets/{spreadsheet_id}/values/{_sheet_range(sheet_name)}",
        operation="get_sheet_values",
    )
    all_rows: list[list[str]] = response.get("values") or []

    if not all_rows:
        current_count = 0
        headers: list[str] = []
        data_rows: list[list[str]] = []
    else:
        headers = [str(h) for h in all_rows[header_row - 1]] if len(all_rows) >= header_row else []
        data_rows = all_rows[header_row:]
        current_count = len(data_rows)

    last_index = ctx.cursor.get("last_row_index")

    # First run: establish baseline, fire no events.
    if last_index is None:
        return ProviderTriggerPollResult(
            events=[],
            cursor={"last_row_index": current_count},
        )

    new_rows = data_rows[last_index:]
    events: list[dict[str, Any]] = []
    for i, raw_row in enumerate(new_rows):
        row_dict: dict[str, Any] = {}
        for col_idx, header in enumerate(headers):
            row_dict[header] = raw_row[col_idx] if col_idx < len(raw_row) else ""
        events.append({
            "provider": "google_sheets",
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
            "row_index": last_index + i + 1,
            "row": row_dict,
        })

    return ProviderTriggerPollResult(
        events=events,
        cursor={"last_row_index": current_count},
    )


GOOGLE_SHEETS_NEW_ROW_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="google_sheets_new_row_trigger_v2",
    name="Google Sheets New Row",
    provider="google_sheets",
    resource="sheet",
    event="new_row",
    description="Start a workflow when a new row is appended to a Google Sheet.",
    icon="brand:google-sheets",
    params=(
        OperationParamSpec(
            name="credentials",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="google_sheets_oauth2",
                key="*",
                label="Google Sheets account",
                fields=["access_token"],
                multi=True,
                test_service="google_sheets",
            ),
            description="Google Sheets OAuth credential.",
        ),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            description="The spreadsheet ID from the Google Sheets URL.",
        ),
        OperationParamSpec(
            name="sheet_name",
            default="Sheet1",
            description="Name of the tab/sheet to watch.",
        ),
        OperationParamSpec(
            name="header_row",
            type="number",
            default=1,
            description="Row number containing column headers (1-indexed).",
        ),
        OperationParamSpec(
            name="poll_interval",
            choices=["1", "5", "15", "30", "60"],
            default="5",
            description="How often to check for new rows (minutes).",
        ),
    ),
    poll=poll_new_rows,
    poll_interval_seconds=300,
)


register_provider_trigger(GOOGLE_SHEETS_NEW_ROW_TRIGGER_SPEC)
```

- [ ] **Step 4: Update `google_sheets/__init__.py`**

```python
"""Google Sheets v2 provider nodes."""

from noodle_nodes.integrations_v2.providers.google_sheets import operations as operations
from noodle_nodes.integrations_v2.providers.google_sheets import triggers as triggers

__all__ = ["operations", "triggers"]
```

- [ ] **Step 5: Run tests**

```
cd packages/nodes && python -m pytest tests/test_google_sheets_new_row_trigger.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/triggers.py \
        packages/nodes/noodle_nodes/integrations_v2/providers/google_sheets/__init__.py \
        packages/nodes/tests/test_google_sheets_new_row_trigger.py
git commit -m "feat(google-sheets): add polling new-row trigger"
```

---

### Task 8: Notion New Database Page Trigger (polling)

**Files:**
- Create: `packages/nodes/noodle_nodes/integrations_v2/providers/notion/triggers.py`
- Modify: `packages/nodes/noodle_nodes/integrations_v2/providers/notion/__init__.py`
- Create: `packages/nodes/tests/test_notion_trigger_v2.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/nodes/tests/test_notion_trigger_v2.py
from __future__ import annotations
from unittest.mock import MagicMock, patch
import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.notion import triggers as notion_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _mock_transport(return_value):
    t = MagicMock()
    t.request.return_value = return_value
    return t


def test_notion_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "notion_new_database_page_trigger_v2" in manifests
    m = manifests["notion_new_database_page_trigger_v2"]
    assert m.name == "Notion New Database Page"
    assert m.category == "Triggers"


def test_poll_first_run_no_events() -> None:
    """First run with empty cursor fires no events and sets baseline timestamp."""
    pages = [
        {"id": "page1", "created_time": "2024-01-01T10:00:00.000Z", "properties": {}},
        {"id": "page2", "created_time": "2024-01-01T11:00:00.000Z", "properties": {}},
    ]
    with patch(
        "noodle_nodes.integrations_v2.providers.notion.triggers._transport"
    ) as mock_t:
        mock_t.return_value = _mock_transport({"results": pages, "has_more": False})
        result = notion_triggers.poll_new_pages(
            ProviderTriggerPollContext(
                params={"credentials": {"token": "tok"}, "database_id": "db1"},
                cursor={},
            )
        )
    assert result.events == []
    assert result.cursor["last_created_time"] == "2024-01-01T11:00:00.000Z"
    assert set(result.cursor["seen_ids"]) == {"page1", "page2"}


def test_poll_returns_new_pages_after_cursor() -> None:
    new_pages = [
        {"id": "page3", "created_time": "2024-01-01T12:00:00.000Z", "properties": {"Name": {"title": [{"plain_text": "New Entry"}]}}},
    ]
    with patch(
        "noodle_nodes.integrations_v2.providers.notion.triggers._transport"
    ) as mock_t:
        mock_t.return_value = _mock_transport({"results": new_pages, "has_more": False})
        result = notion_triggers.poll_new_pages(
            ProviderTriggerPollContext(
                params={"credentials": {"token": "tok"}, "database_id": "db1"},
                cursor={
                    "last_created_time": "2024-01-01T11:00:00.000Z",
                    "seen_ids": ["page1", "page2"],
                },
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["page_id"] == "page3"
    assert result.cursor["last_created_time"] == "2024-01-01T12:00:00.000Z"
    assert "page3" in result.cursor["seen_ids"]


def test_poll_deduplicates_already_seen_ids() -> None:
    """Pages already in seen_ids must not be returned as events."""
    pages = [{"id": "page1", "created_time": "2024-01-01T10:00:00.000Z", "properties": {}}]
    with patch(
        "noodle_nodes.integrations_v2.providers.notion.triggers._transport"
    ) as mock_t:
        mock_t.return_value = _mock_transport({"results": pages, "has_more": False})
        result = notion_triggers.poll_new_pages(
            ProviderTriggerPollContext(
                params={"credentials": {"token": "tok"}, "database_id": "db1"},
                cursor={"last_created_time": "2024-01-01T09:00:00.000Z", "seen_ids": ["page1"]},
            )
        )
    assert result.events == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_notion_trigger_v2.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `notion/triggers.py`**

```python
"""Notion polling trigger — detects new pages in a database."""
from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

NOTION_API_BASE = "https://api.notion.com"
_MAX_SEEN_IDS = 500


def _transport(credentials: Any) -> ProviderTransport:
    creds = credentials if isinstance(credentials, dict) else {}
    token = str(creds.get("token") or creds.get("api_key") or "")
    if not token:
        raise ValueError("notion_new_database_page_trigger_v2: credentials are required")
    return ProviderTransport(
        provider="notion",
        base_url=NOTION_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
        },
    )


def poll_new_pages(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    database_id = str(params.get("database_id") or "").strip()
    if not database_id:
        raise ValueError("notion_new_database_page_trigger_v2: database_id is required")

    cursor = ctx.cursor
    last_created_time: str | None = cursor.get("last_created_time")
    seen_ids: list[str] = list(cursor.get("seen_ids") or [])

    transport = _transport(params.get("credentials"))

    query_body: dict[str, Any] = {
        "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        "page_size": 100,
    }
    if last_created_time:
        query_body["filter"] = {
            "timestamp": "created_time",
            "created_time": {"after": last_created_time},
        }

    response = transport.request(
        "POST",
        f"/v1/databases/{database_id}/query",
        operation="query_database_for_new_pages",
        json_body=query_body,
    )
    results: list[dict[str, Any]] = response.get("results") or []

    if not results:
        return ProviderTriggerPollResult(events=[], cursor=cursor)

    latest_time = last_created_time or ""
    events: list[dict[str, Any]] = []
    new_ids = list(seen_ids)

    for page in results:
        page_id = str(page.get("id") or "")
        created = str(page.get("created_time") or "")
        if page_id in seen_ids:
            continue
        if created > latest_time:
            latest_time = created
        events.append({
            "provider": "notion",
            "database_id": database_id,
            "page_id": page_id,
            "created_time": created,
            "properties": page.get("properties"),
            "page": page,
        })
        new_ids.append(page_id)

    # Keep only the last _MAX_SEEN_IDS to bound memory.
    new_ids = new_ids[-_MAX_SEEN_IDS:]

    # First run: no prior cursor — baseline only, no events.
    if last_created_time is None:
        return ProviderTriggerPollResult(
            events=[],
            cursor={"last_created_time": latest_time or "", "seen_ids": new_ids},
        )

    return ProviderTriggerPollResult(
        events=events,
        cursor={"last_created_time": latest_time, "seen_ids": new_ids},
    )


NOTION_NEW_PAGE_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="notion_new_database_page_trigger_v2",
    name="Notion New Database Page",
    provider="notion",
    resource="database",
    event="new_page",
    description="Start a workflow when a new page is created in a Notion database.",
    icon="brand:notion",
    params=(
        OperationParamSpec(
            name="credentials",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="notion",
                key="*",
                label="Notion integration token",
                fields=["token"],
                multi=True,
                test_service="notion",
            ),
            description="Notion integration token with access to the database.",
        ),
        OperationParamSpec(
            name="database_id",
            required=True,
            placeholder="8a7c2f0e1d3b4a5c9e6f7d2b",
            description="The Notion database ID to watch for new pages.",
        ),
        OperationParamSpec(
            name="poll_interval",
            choices=["1", "5", "15", "30", "60"],
            default="5",
            description="How often to check for new pages (minutes).",
        ),
    ),
    poll=poll_new_pages,
    poll_interval_seconds=300,
)


register_provider_trigger(NOTION_NEW_PAGE_TRIGGER_SPEC)
```

- [ ] **Step 4: Update `notion/__init__.py`**

```python
"""Notion v2 provider nodes."""

from noodle_nodes.integrations_v2.providers.notion import operations as operations
from noodle_nodes.integrations_v2.providers.notion import triggers as triggers

__all__ = ["operations", "triggers"]
```

- [ ] **Step 5: Run tests**

```
cd packages/nodes && python -m pytest tests/test_notion_trigger_v2.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/providers/notion/triggers.py \
        packages/nodes/noodle_nodes/integrations_v2/providers/notion/__init__.py \
        packages/nodes/tests/test_notion_trigger_v2.py
git commit -m "feat(notion): add polling new database page trigger"
```

---

### Task 9: RSS / Atom Feed Trigger (polling)

**Files:**
- Create: `packages/nodes/noodle_nodes/integrations_v2/providers/rss/__init__.py`
- Create: `packages/nodes/noodle_nodes/integrations_v2/providers/rss/triggers.py`
- Modify: `packages/nodes/noodle_nodes/__init__.py`
- Create: `packages/nodes/tests/test_rss_trigger.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/nodes/tests/test_rss_trigger.py
from __future__ import annotations
from unittest.mock import MagicMock, patch
import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.rss import triggers as rss_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext

RSS_FEED = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Item 1</title>
      <link>https://example.com/1</link>
      <guid>guid-1</guid>
      <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Item 2</title>
      <link>https://example.com/2</link>
      <guid>guid-2</guid>
      <pubDate>Mon, 01 Jan 2024 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <id>atom-1</id>
    <title>Atom Item 1</title>
    <link href="https://example.com/atom/1"/>
    <updated>2024-01-01T12:00:00Z</updated>
  </entry>
</feed>
"""


def _mock_response(text: str, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_rss_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "rss_feed_trigger" in manifests
    m = manifests["rss_feed_trigger"]
    assert m.name == "RSS / Atom Feed"
    assert m.category == "Triggers"


def test_rss_first_run_no_events() -> None:
    with patch("noodle_nodes.integrations_v2.providers.rss.triggers.requests") as mock_req:
        mock_req.get.return_value = _mock_response(RSS_FEED)
        result = rss_triggers.poll_rss_feed(
            ProviderTriggerPollContext(
                params={"feed_url": "https://example.com/feed.xml", "max_items": 10},
                cursor={},
            )
        )
    assert result.events == []
    assert "guid-1" in result.cursor["seen_guids"]
    assert "guid-2" in result.cursor["seen_guids"]


def test_rss_new_items_after_cursor() -> None:
    with patch("noodle_nodes.integrations_v2.providers.rss.triggers.requests") as mock_req:
        mock_req.get.return_value = _mock_response(RSS_FEED)
        result = rss_triggers.poll_rss_feed(
            ProviderTriggerPollContext(
                params={"feed_url": "https://example.com/feed.xml", "max_items": 10},
                cursor={"seen_guids": ["guid-1"]},
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["guid"] == "guid-2"
    assert result.events[0]["title"] == "Item 2"
    assert "guid-2" in result.cursor["seen_guids"]


def test_atom_feed_parses_entries() -> None:
    with patch("noodle_nodes.integrations_v2.providers.rss.triggers.requests") as mock_req:
        mock_req.get.return_value = _mock_response(ATOM_FEED)
        result = rss_triggers.poll_rss_feed(
            ProviderTriggerPollContext(
                params={"feed_url": "https://example.com/atom.xml", "max_items": 10},
                cursor={"seen_guids": []},
            )
        )
    # First run with empty (not None) cursor — should fire events.
    # (Empty list means "we know about zero items", different from first run with no cursor key.)
    assert len(result.events) == 1
    assert result.events[0]["guid"] == "atom-1"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_rss_trigger.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `rss/__init__.py`**

```python
"""RSS / Atom feed polling trigger."""

from noodle_nodes.integrations_v2.providers.rss import triggers as triggers

__all__ = ["triggers"]
```

- [ ] **Step 4: Create `rss/triggers.py`**

```python
"""RSS and Atom feed polling trigger — detects new feed items."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import requests as requests  # noqa: PLC0414 — named import so tests can monkeypatch

from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

_MAX_SEEN_GUIDS = 500
_ATOM_NS = "http://www.w3.org/2005/Atom"


def _parse_rss(root: ET.Element) -> list[dict[str, Any]]:
    items = []
    for item in root.findall(".//item"):
        guid = (item.findtext("guid") or item.findtext("link") or "").strip()
        items.append({
            "guid": guid,
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "pub_date": (item.findtext("pubDate") or "").strip(),
        })
    return items


def _parse_atom(root: ET.Element) -> list[dict[str, Any]]:
    items = []
    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        guid = (entry.findtext(f"{{{_ATOM_NS}}}id") or "").strip()
        link_el = entry.find(f"{{{_ATOM_NS}}}link")
        link = (link_el.get("href") or "") if link_el is not None else ""
        items.append({
            "guid": guid,
            "title": (entry.findtext(f"{{{_ATOM_NS}}}title") or "").strip(),
            "link": link.strip(),
            "description": (entry.findtext(f"{{{_ATOM_NS}}}summary") or "").strip(),
            "pub_date": (entry.findtext(f"{{{_ATOM_NS}}}updated") or "").strip(),
        })
    return items


def _fetch_items(feed_url: str) -> list[dict[str, Any]]:
    resp = requests.get(feed_url, timeout=15, headers={"User-Agent": "Noodle/1.0 feed-reader"})
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    tag = root.tag.lower()
    if "rss" in tag or root.find("channel") is not None:
        return _parse_rss(root)
    if "feed" in tag or f"{{{_ATOM_NS}}}" in root.tag:
        return _parse_atom(root)
    # Try both and return whichever finds items.
    rss = _parse_rss(root)
    return rss if rss else _parse_atom(root)


def poll_rss_feed(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    feed_url = str(params.get("feed_url") or "").strip()
    if not feed_url:
        raise ValueError("rss_feed_trigger: feed_url is required")
    try:
        max_items = int(params.get("max_items") or 10)
    except (ValueError, TypeError):
        max_items = 10

    cursor = ctx.cursor
    seen_guids: list[str] | None = cursor.get("seen_guids")  # None = truly first run

    items = _fetch_items(feed_url)

    if seen_guids is None:
        # First run: populate cursor, fire no events.
        guids = [it["guid"] for it in items if it["guid"]][-_MAX_SEEN_GUIDS:]
        return ProviderTriggerPollResult(events=[], cursor={"seen_guids": guids})

    seen_set = set(seen_guids)
    new_items = [it for it in items if it["guid"] and it["guid"] not in seen_set]
    new_items = new_items[:max_items]

    events = [
        {
            "provider": "rss",
            "feed_url": feed_url,
            "guid": it["guid"],
            "title": it["title"],
            "link": it["link"],
            "description": it["description"],
            "pub_date": it["pub_date"],
        }
        for it in new_items
    ]

    updated_guids = (seen_guids + [it["guid"] for it in new_items])[-_MAX_SEEN_GUIDS:]
    return ProviderTriggerPollResult(events=events, cursor={"seen_guids": updated_guids})


RSS_FEED_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="rss_feed_trigger",
    name="RSS / Atom Feed",
    provider="rss",
    resource="feed",
    event="new_item",
    description="Start a workflow when a new item appears in an RSS or Atom feed.",
    icon="rss",
    params=(
        OperationParamSpec(
            name="feed_url",
            required=True,
            placeholder="https://example.com/feed.xml",
            description="URL of the RSS or Atom feed to watch.",
        ),
        OperationParamSpec(
            name="max_items",
            type="number",
            default=10,
            description="Maximum new items to fire per poll (prevents flooding on first run).",
            advanced=True,
        ),
        OperationParamSpec(
            name="poll_interval",
            choices=["5", "15", "30", "60"],
            default="15",
            description="How often to check the feed (minutes).",
        ),
    ),
    requirements=("requests",),
    poll=poll_rss_feed,
    poll_interval_seconds=900,
)


register_provider_trigger(RSS_FEED_TRIGGER_SPEC)
```

- [ ] **Step 5: Register the RSS provider in `noodle_nodes/__init__.py`**

Add after the existing provider imports (line ~27):

```python
from noodle_nodes.integrations_v2.providers import rss as rss_v2
```

Add `"rss_v2"` to `__all__`.

- [ ] **Step 6: Run tests**

```
cd packages/nodes && python -m pytest tests/test_rss_trigger.py -v
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/providers/rss/ \
        packages/nodes/noodle_nodes/__init__.py \
        packages/nodes/tests/test_rss_trigger.py
git commit -m "feat(rss): add RSS/Atom feed polling trigger"
```

---

## Phase 3 — File Reading Nodes

### Task 10: `read_text_file` node

**Files:**
- Create: `packages/nodes/noodle_nodes/file_nodes.py`
- Modify: `packages/nodes/noodle_nodes/__init__.py`
- Create: `packages/nodes/tests/test_file_nodes.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/nodes/tests/test_file_nodes.py
from __future__ import annotations
import io
import pathlib
import pytest
import noodle_nodes  # noqa: F401
from noodle.sdk import registry


def test_read_text_file_node_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "read_text_file" in manifests
    m = manifests["read_text_file"]
    assert m.name == "Read Text File"
    assert m.category == "Files"


def test_read_text_file_from_path(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    result = registry.get("read_text_file").func(
        input=None, path=str(f), file="", encoding="utf-8"
    )
    assert result["text"] == "hello world"
    assert result["filename"] == "hello.txt"
    assert result["size_bytes"] == 11


def test_read_text_file_missing_path_raises() -> None:
    with pytest.raises(Exception, match="path or file"):
        registry.get("read_text_file").func(input=None, path="", file="", encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py::test_read_text_file_node_is_registered -v
```

Expected: `KeyError` — node not registered.

- [ ] **Step 3: Create `file_nodes.py` with `read_text_file`**

```python
"""File reading nodes — local path and browser-upload sources."""
from __future__ import annotations

import pathlib
from typing import Any

from noodle.sdk import node


@node(
    name="Read Text File",
    category="Files",
    description="Read a text file from a server path or browser-uploaded artifact.",
    icon="file-text",
)
def read_text_file(
    input: Any,
    path: str = "",
    file: str = "",
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Read plain text from a server path or an uploaded artifact (artifact_id)."""
    if file:
        # Browser-upload path: artifact_id stored as string param.
        from noodle.artifacts import LocalArtifactStore
        from app.config import settings
        store = LocalArtifactStore(settings.artifacts_dir)
        content_bytes = store.read_bytes(file)
        try:
            text = content_bytes.decode(encoding or "utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"read_text_file: cannot decode artifact with encoding {encoding!r}: {exc}") from exc
        return {"text": text, "filename": file, "size_bytes": len(content_bytes)}
    if path:
        p = pathlib.Path(path)
        text = p.read_text(encoding=encoding or "utf-8")
        return {"text": text, "filename": p.name, "size_bytes": p.stat().st_size}
    raise ValueError("read_text_file: either path or file (artifact_id) must be provided")
```

- [ ] **Step 4: Register in `__init__.py`**

Add to `noodle_nodes/__init__.py`:

```python
from noodle_nodes import file_nodes as file_nodes
```

Add `"file_nodes"` to `__all__`.

- [ ] **Step 5: Run tests**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py::test_read_text_file_node_is_registered tests/test_file_nodes.py::test_read_text_file_from_path tests/test_file_nodes.py::test_read_text_file_missing_path_raises -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py \
        packages/nodes/noodle_nodes/__init__.py \
        packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add read_text_file node with path and upload sources"
```

---

### Task 11: `read_csv_file` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Test: `packages/nodes/tests/test_file_nodes.py`

- [ ] **Step 1: Write failing tests** (append to `test_file_nodes.py`)

```python
def test_read_csv_file_node_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "read_csv_file" in manifests


def test_read_csv_file_returns_dataset_by_default(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
    result = registry.get("read_csv_file").func(
        input=None, path=str(f), file="", delimiter=",", has_header=True, output_as_dataset=True
    )
    # DatasetRef is a dict with artifact_id key.
    assert "artifact_id" in result or hasattr(result, "artifact_id")


def test_read_csv_file_raw_mode(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
    result = registry.get("read_csv_file").func(
        input=None, path=str(f), file="", delimiter=",", has_header=True, output_as_dataset=False
    )
    assert result["row_count"] == 2
    assert result["rows"][0] == {"name": "Alice", "age": "30"}
    assert result["filename"] == "data.csv"


def test_read_csv_file_missing_raises() -> None:
    with pytest.raises(Exception, match="path or file"):
        registry.get("read_csv_file").func(
            input=None, path="", file="", delimiter=",", has_header=True, output_as_dataset=False
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py::test_read_csv_file_node_is_registered -v
```

Expected: `KeyError`.

- [ ] **Step 3: Add `read_csv_file` to `file_nodes.py`**

```python
@node(
    name="Read CSV File",
    category="Files",
    description="Read a CSV file from a server path or uploaded artifact. Toggle output_as_dataset to get a DatasetRef.",
    icon="table",
)
def read_csv_file(
    input: Any,
    path: str = "",
    file: str = "",
    delimiter: str = ",",
    has_header: bool = True,
    output_as_dataset: bool = True,
) -> Any:
    import csv
    import io as _io

    if file:
        from noodle.artifacts import LocalArtifactStore
        from app.config import settings
        store = LocalArtifactStore(settings.artifacts_dir)
        raw = store.read_bytes(file)
        text = raw.decode("utf-8")
        filename = file
    elif path:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        filename = p.name
    else:
        raise ValueError("read_csv_file: either path or file (artifact_id) must be provided")

    reader = csv.reader(_io.StringIO(text), delimiter=delimiter or ",")
    all_rows = list(reader)
    if not all_rows:
        if output_as_dataset:
            from noodle_nodes.datasets import csv_parse
            return csv_parse(input=None, text=text, delimiter=delimiter, has_header=has_header)
        return {"rows": [], "filename": filename, "row_count": 0}

    if has_header:
        headers = all_rows[0]
        data_rows = all_rows[1:]
        dicts = [dict(zip(headers, row)) for row in data_rows]
    else:
        data_rows = all_rows
        dicts = [dict(enumerate(row)) for row in data_rows]

    if output_as_dataset:
        from noodle_nodes.datasets import csv_parse
        return csv_parse(input=None, text=text, delimiter=delimiter, has_header=has_header)

    return {"rows": dicts, "filename": filename, "row_count": len(dicts)}
```

- [ ] **Step 4: Run tests**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py -k "csv" -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add read_csv_file node with output_as_dataset toggle"
```

---

### Task 12: `read_json_file` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Test: `packages/nodes/tests/test_file_nodes.py`

- [ ] **Step 1: Write failing tests** (append to `test_file_nodes.py`)

```python
def test_read_json_file_raw_mode(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.json"
    f.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")
    result = registry.get("read_json_file").func(
        input=None, path=str(f), file="", output_as_dataset=False
    )
    assert result["data"] == [{"a": 1}, {"a": 2}]
    assert result["filename"] == "data.json"


def test_read_json_file_dataset_mode_array_of_objects(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.json"
    f.write_text('[{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]', encoding="utf-8")
    result = registry.get("read_json_file").func(
        input=None, path=str(f), file="", output_as_dataset=True
    )
    assert "artifact_id" in result or hasattr(result, "artifact_id")


def test_read_json_file_dataset_mode_non_array_raises(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}', encoding="utf-8")
    with pytest.raises(ValueError, match="array of objects"):
        registry.get("read_json_file").func(
            input=None, path=str(f), file="", output_as_dataset=True
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py::test_read_json_file_raw_mode -v
```

Expected: `KeyError`.

- [ ] **Step 3: Add `read_json_file` to `file_nodes.py`**

```python
@node(
    name="Read JSON File",
    category="Files",
    description="Read a JSON file from a server path or uploaded artifact. Enable output_as_dataset to convert an array of objects to a DatasetRef.",
    icon="braces",
)
def read_json_file(
    input: Any,
    path: str = "",
    file: str = "",
    output_as_dataset: bool = False,
) -> Any:
    import json as _json

    if file:
        from noodle.artifacts import LocalArtifactStore
        from app.config import settings
        store = LocalArtifactStore(settings.artifacts_dir)
        raw = store.read_bytes(file)
        text = raw.decode("utf-8")
        filename = file
    elif path:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        filename = p.name
    else:
        raise ValueError("read_json_file: either path or file (artifact_id) must be provided")

    data = _json.loads(text)

    if output_as_dataset:
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError(
                "read_json_file: output_as_dataset requires the JSON root to be an array of objects"
            )
        import csv as _csv
        import io as _io
        if not data:
            csv_text = ""
        else:
            headers = list(data[0].keys())
            buf = _io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)
            csv_text = buf.getvalue()
        from noodle_nodes.datasets import csv_parse
        return csv_parse(input=None, text=csv_text, delimiter=",", has_header=True)

    return {"data": data, "filename": filename}
```

- [ ] **Step 4: Run tests**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py -k "json" -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add read_json_file node with output_as_dataset toggle"
```

---

### Task 13: `read_xml_file` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Test: `packages/nodes/tests/test_file_nodes.py`

- [ ] **Step 1: Write failing tests** (append to `test_file_nodes.py`)

```python
def test_read_xml_file_raw_mode(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.xml"
    f.write_text("<root><item><name>Alice</name></item></root>", encoding="utf-8")
    result = registry.get("read_xml_file").func(
        input=None, path=str(f), file="", row_xpath="", output_as_dataset=False
    )
    assert isinstance(result["data"], dict)
    assert result["filename"] == "data.xml"


def test_read_xml_file_dataset_mode(tmp_path: pathlib.Path) -> None:
    pytest.importorskip("xmltodict")
    f = tmp_path / "data.xml"
    f.write_text(
        "<items><item><name>Alice</name><age>30</age></item>"
        "<item><name>Bob</name><age>25</age></item></items>",
        encoding="utf-8",
    )
    result = registry.get("read_xml_file").func(
        input=None, path=str(f), file="", row_xpath="item", output_as_dataset=True
    )
    assert "artifact_id" in result or hasattr(result, "artifact_id")


def test_read_xml_file_dataset_missing_xpath_raises(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.xml"
    f.write_text("<root/>", encoding="utf-8")
    with pytest.raises(ValueError, match="row_xpath"):
        registry.get("read_xml_file").func(
            input=None, path=str(f), file="", row_xpath="", output_as_dataset=True
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py::test_read_xml_file_raw_mode -v
```

Expected: `KeyError`.

- [ ] **Step 3: Add `read_xml_file` to `file_nodes.py`**

```python
@node(
    name="Read XML File",
    category="Files",
    description="Read an XML file from a server path or uploaded artifact. Provide row_xpath and enable output_as_dataset to extract repeated elements as a DatasetRef.",
    icon="code",
    requirements=("xmltodict",),
)
def read_xml_file(
    input: Any,
    path: str = "",
    file: str = "",
    row_xpath: str = "",
    output_as_dataset: bool = False,
) -> Any:
    import xml.etree.ElementTree as ET

    if file:
        from noodle.artifacts import LocalArtifactStore
        from app.config import settings
        store = LocalArtifactStore(settings.artifacts_dir)
        raw = store.read_bytes(file)
        text = raw.decode("utf-8")
        filename = file
    elif path:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        filename = p.name
    else:
        raise ValueError("read_xml_file: either path or file (artifact_id) must be provided")

    if output_as_dataset:
        if not row_xpath:
            raise ValueError("read_xml_file: row_xpath is required when output_as_dataset is true")
        import xmltodict
        parsed = xmltodict.parse(text)
        root = ET.fromstring(text)
        rows_els = root.findall(f".//{row_xpath}")
        rows: list[dict] = []
        for el in rows_els:
            row: dict = {}
            for child in el:
                row[child.tag] = (child.text or "").strip()
            # Also include attributes.
            for attr_name, attr_val in el.attrib.items():
                row[f"@{attr_name}"] = attr_val
            rows.append(row)
        import csv as _csv
        import io as _io
        if rows:
            headers = list(rows[0].keys())
            buf = _io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            csv_text = buf.getvalue()
        else:
            csv_text = ""
        from noodle_nodes.datasets import csv_parse
        return csv_parse(input=None, text=csv_text, delimiter=",", has_header=True)

    import xmltodict
    data = xmltodict.parse(text)
    return {"data": dict(data), "filename": filename}
```

- [ ] **Step 4: Run tests**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py -k "xml" -v
```

Expected: Pass (xml raw-mode test passes without xmltodict; dataset test is skipped if not installed).

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add read_xml_file node with xmltodict and output_as_dataset toggle"
```

---

### Task 14: Update coverage matrix and node manifest test

**Files:**
- Modify: `docs/provider-coverage-matrix.md`
- Modify: `apps/api/tests/test_nodes.py`

- [ ] **Step 1: Update `docs/provider-coverage-matrix.md`**

Update the Google Sheets row to replace `Planned` trigger with:
```
New-row polling trigger (google_sheets_new_row_trigger_v2)
```

Update the Slack row to replace `Planned` trigger with:
```
Event trigger (slack_event_trigger_v2)
```

Update the Stripe row to replace `Planned` trigger with:
```
Event/webhook trigger (stripe_event_trigger_v2)
```

Update the Notion row to replace `Planned` trigger with:
```
New database page polling trigger (notion_new_database_page_trigger_v2)
```

Add a new row for RSS:
```
| RSS / Atom | Feed polling trigger | N/A | None (public feeds) | Beta | Mocked poll tests | Built-in polling trigger; no credentials required. |
```

Add a row for file nodes:
```
| File Nodes | N/A | N/A | N/A | Beta | Unit tests with tmp_path | read_text_file, read_csv_file, read_json_file, read_xml_file; dual source (path + upload). |
```

- [ ] **Step 2: Add new node IDs to `test_nodes.py`**

In `apps/api/tests/test_nodes.py`, add the new IDs to the existing `<= ids` assertion:

```python
    assert {
        # ... existing IDs ...
        "slack_event_trigger_v2",
        "stripe_event_trigger_v2",
        "google_sheets_new_row_trigger_v2",
        "notion_new_database_page_trigger_v2",
        "rss_feed_trigger",
        "read_text_file",
        "read_csv_file",
        "read_json_file",
        "read_xml_file",
    } <= ids
```

- [ ] **Step 3: Run the full test suite**

```
cd packages/nodes && python -m pytest tests/ -v
cd apps/api && python -m pytest tests/ -v
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add docs/provider-coverage-matrix.md apps/api/tests/test_nodes.py
git commit -m "docs(matrix): update provider coverage with new triggers and file nodes"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Poll hook extension to ProviderTriggerSpec | Task 1 |
| Scheduler polling pass | Task 2 |
| POST /artifacts/upload endpoint | Task 3 |
| file_upload widget in frontend | Task 4 |
| Slack event trigger | Task 5 |
| Stripe event trigger | Task 6 |
| Google Sheets new-row trigger (polling) | Task 7 |
| Notion new-database-page trigger (polling) | Task 8 |
| RSS/Atom feed trigger (polling) | Task 9 |
| read_text_file node | Task 10 |
| read_csv_file with output_as_dataset | Task 11 |
| read_json_file with output_as_dataset | Task 12 |
| read_xml_file with row_xpath + output_as_dataset | Task 13 |
| Coverage matrix update | Task 14 |

**Not covered in this plan (requires follow-up):**
- P4: Google Drive file trigger, Google Sheets change trigger, Airtable record webhook trigger, Microsoft Graph notification trigger — all require subscription-renewal infrastructure (expires_at tracking, renewal pass in scheduler).
- P5: IMAP email trigger (needs `imap` credential type), Airtable polling fallback, File Upload Trigger built-in node.
