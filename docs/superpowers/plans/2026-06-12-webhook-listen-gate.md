# Webhook Listen Gate & Auth Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the test webhook URL behind an active server-side listen session so external callers cannot hit it without the editor's "Listen for test event" being active, enforce authentication on the test URL, and fix the production URL to return 404 for unmatched paths.

**Architecture:** Add an in-memory `_listening: dict[str, float]` dict (path → monotonic expire_at) in `webhooks.py` alongside the existing `_captured` dict. Two new endpoints start/stop the session. `capture_webhook` checks `_is_listening` before processing any request. Production `trigger_webhook` raises 404 when `result.any_match` is False.

**Tech Stack:** FastAPI, httpx AsyncClient (tests), TypeScript/React (frontend wiring)

---

## File Map

| File | Change |
|------|--------|
| `apps/api/app/routers/webhooks.py` | Add `_listening` dict + helpers + two new endpoints, gate `capture_webhook`, fix production 404 |
| `apps/api/tests/conftest.py` | Add autouse fixture to reset `_listening` between tests |
| `apps/api/tests/test_triggers.py` | Update 4 existing tests, add 4 new listen-gate tests |
| `apps/web/src/api.ts` | Add `startListen` and `stopListen` methods |
| `apps/web/src/EditorPage.tsx` | `startWebhookTestRun` + `stopWebhookListen` call the new API |
| `apps/web/src/editor/NodeDetails.tsx` | `WebhookPanel.listen()` + `stop()` call the new API |

---

## Task 1: Backend — listen session state + new endpoints

**Files:**
- Modify: `apps/api/app/routers/webhooks.py`

- [ ] **Step 1: Add the `_listening` dict, TTL constant, and three helper functions**

  In `webhooks.py`, after the `_captured` dict definition (line ~40), add:

  ```python
  WEBHOOK_LISTEN_TTL_SECONDS = 10 * 60  # 10 min — covers setup time in Postman etc.

  # path → monotonic expire_at. Set by the editor's Listen session; expires
  # automatically so a closed browser tab never leaves the URL permanently open.
  _listening: dict[str, float] = {}


  def _is_listening(path: str) -> bool:
      """Return True if there is an active, non-expired listen session for path."""
      expire_at = _listening.get(path)
      if expire_at is None:
          return False
      if time.monotonic() > expire_at:
          _listening.pop(path, None)
          return False
      return True


  def _start_listening(path: str) -> None:
      _listening[path] = time.monotonic() + WEBHOOK_LISTEN_TTL_SECONDS


  def _stop_listening(path: str) -> None:
      _listening.pop(path, None)
  ```

- [ ] **Step 2: Add `POST /webhook-test/{path}/listen` and `DELETE /webhook-test/{path}/listen`**

  Add these two endpoints after the existing `clear_webhook` DELETE endpoint (around line 193):

  ```python
  @router.post("/webhook-test/{path}/listen", status_code=200)
  async def start_listen_session(path: str) -> dict:
      """Register an active listen session so the test URL accepts incoming requests."""
      _start_listening(path)
      return {"listening": True, "ttl_seconds": WEBHOOK_LISTEN_TTL_SECONDS}


  @router.delete("/webhook-test/{path}/listen", status_code=204)
  async def stop_listen_session(path: str) -> None:
      """Clear the listen session; the test URL will return 404 until re-opened."""
      _stop_listening(path)
  ```

- [ ] **Step 3: Gate `capture_webhook` behind `_is_listening`**

  In `capture_webhook`, add the listen check as the first thing in the function body (before `_payload` and `_record_capture`):

  ```python
  @router.api_route("/webhook-test/{path}", methods=_METHODS)
  async def capture_webhook(path: str, request: Request) -> dict:
      """Editor test URL — only active while a listen session is registered."""
      if not _is_listening(path):
          raise HTTPException(
              status_code=404,
              detail=(
                  "No active listen session for this webhook path. "
                  "Click 'Listen for test event' in the editor first."
              ),
          )
      req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
      # ... rest of the function unchanged
  ```

---

## Task 2: Fix production URL to return 404 for unmatched paths

**Files:**
- Modify: `apps/api/app/routers/webhooks.py`

- [ ] **Step 1: Add `any_match` check inside `trigger_webhook`**

  In `trigger_webhook` (the production route), add the 404 raise right after the `reject_status` check, still inside the `with run_as_system():` block:

  ```python
  with run_as_system():
      result = await dispatch_webhook(
          path, payload, raw_body=raw_body,
          client_ip=request.client.host if request.client else None,
      )
      logger.info(
          "webhook prod path=%s matched=%s runs=%d req_id=%s",
          path, result.any_match, len(result.run_ids), req_id,
      )
      if result.reject_status is not None:
          raise HTTPException(
              result.reject_status, _REJECT_DETAIL[result.reject_status]
          )
      if not result.any_match:
          raise HTTPException(404, "No active workflow for this webhook path.")
      if result.sync is not None:
          shape = await wait_for_webhook_result(
              **result.sync,
              timeout=settings.webhook_response_timeout_seconds,
          )
          return _shaped_response(shape)
  if result.response is not None:
      return _shaped_response(result.response)
  if result.run_ids:
      message = "Workflow triggered"
  elif result.deduped:
      message = "Duplicate delivery acknowledged"
  else:
      message = "No active workflow for this path"
  return {
      "message": message,
      "path": path,
      "runs": result.run_ids,
      "x_request_id": req_id,
  }
  ```

  The `else` branch with `"No active workflow for this path"` is now unreachable but left intact for safety.

---

## Task 3: Reset `_listening` between tests in conftest

**Files:**
- Modify: `apps/api/tests/conftest.py`

- [ ] **Step 1: Add the autouse fixture**

  After the existing `_reset_run_dispatch_state` fixture in `conftest.py`, add:

  ```python
  @pytest.fixture(autouse=True)
  def _reset_webhook_listen_state():
      """Clear the in-memory listen session dict between tests.

      _listening is module-level state in the webhooks router, just like
      _captured. Without a reset, a test that calls start_listen_session for
      path X leaves that path open for subsequent tests.
      """
      import app.routers.webhooks as webhooks_module

      webhooks_module._listening.clear()
      webhooks_module._captured.clear()
      yield
      webhooks_module._listening.clear()
      webhooks_module._captured.clear()
  ```

  Note: `_captured` is also reset here — without this, a test that fires a request and stores a captured payload can pollute the next test's `lastWebhook` poll. The existing tests pass today only because each test uses a different path; resetting explicitly is safer.

---

## Task 4: Update existing tests broken by the production 404 change

**Files:**
- Modify: `apps/api/tests/test_triggers.py`

These four existing tests assert HTTP 200 for unmatched production paths. They must be updated.

- [ ] **Step 1: Update `test_webhook_with_no_active_workflow`** (around line 122)

  Change:
  ```python
  async def test_webhook_with_no_active_workflow(client: AsyncClient) -> None:
      response = (await client.post("/webhook/unknown", json={})).json()
      assert response["runs"] == []
  ```

  To:
  ```python
  async def test_webhook_with_no_active_workflow(client: AsyncClient) -> None:
      resp = await client.post("/webhook/unknown", json={})
      assert resp.status_code == 404
  ```

- [ ] **Step 2: Update `test_inactive_workflow_is_not_triggered`** (around line 127)

  Change the final two lines:
  ```python
  response = (await client.post("/webhook/idle", json={})).json()
  assert response["runs"] == []
  ```

  To:
  ```python
  resp = await client.post("/webhook/idle", json={})
  assert resp.status_code == 404
  ```

- [ ] **Step 3: Update `test_webhook_unknown_path_still_returns_200`** (around line 1259)

  Change the test name and body:
  ```python
  async def test_webhook_unknown_path_returns_404(client: AsyncClient) -> None:
      """Unknown production paths return 404 — callers learn the endpoint doesn't exist."""
      resp = await client.post("/webhook/nobody-listens", json={})
      assert resp.status_code == 404
  ```

- [ ] **Step 4: Update `test_webhook_role_default_serves_production_path`** (around line 1298)

  The old test hit an unknown path and asserted `!= 404`. With our change, unknown paths return 404 from our own logic — the same status code as a missing route — so the test can no longer distinguish "router mounted" from "router not mounted" via status code alone. Replace it with a test that creates an active workflow:

  ```python
  async def test_webhook_role_default_serves_production_path(client: AsyncClient) -> None:
      """Default role — production /webhook/* is mounted and fires active workflows."""
      workflow_id = (
          await client.post("/workflows", json={"name": "Probe"})
      ).json()["id"]
      await client.put(
          f"/workflows/{workflow_id}",
          json={"graph": _webhook_graph("probe-role"), "active": True},
      )
      await client.post(f"/workflows/{workflow_id}/publish", json={})
      resp = await client.post("/webhook/probe-role", json={})
      assert resp.status_code == 200
      assert len(resp.json()["runs"]) == 1
  ```

- [ ] **Step 5: Run the existing test suite to confirm only the four updated tests changed**

  ```
  cd apps/api
  python -m pytest tests/test_triggers.py -x -q 2>&1 | head -60
  ```

  Expected: all tests pass.

---

## Task 5: Add new tests for the listen gate and test-URL auth

**Files:**
- Modify: `apps/api/tests/test_triggers.py`

Add these tests at the end of the file (after the last test in the `# --- Task 8` block).

- [ ] **Step 1: Add the four listen-gate tests**

  ```python
  # --- Listen gate (test URL) --------------------------------------------------

  async def test_webhook_test_url_blocked_without_listen_session(
      client: AsyncClient,
  ) -> None:
      """Test URL returns 404 when no listen session is registered."""
      resp = await client.post("/webhook-test/my-path", json={"x": 1})
      assert resp.status_code == 404
      assert "listen" in resp.json()["detail"].lower()


  async def test_webhook_test_url_allowed_after_start_listen(
      client: AsyncClient,
  ) -> None:
      """Test URL accepts requests once the listen session is started."""
      await client.post("/webhook-test/my-path/listen")
      resp = await client.post("/webhook-test/my-path", json={"x": 1})
      assert resp.status_code == 200


  async def test_webhook_test_url_blocked_after_stop_listen(
      client: AsyncClient,
  ) -> None:
      """Test URL returns 404 after the listen session is explicitly stopped."""
      await client.post("/webhook-test/my-path/listen")
      await client.delete("/webhook-test/my-path/listen")
      resp = await client.post("/webhook-test/my-path", json={"x": 1})
      assert resp.status_code == 404


  async def test_webhook_test_url_still_captures_when_no_workflow_matches(
      client: AsyncClient,
  ) -> None:
      """When listening but no workflow matches the path, request is captured (200)
      so the editor can show it, but no run is fired."""
      await client.post("/webhook-test/unmatched-path/listen")
      resp = await client.post("/webhook-test/unmatched-path", json={"hello": "world"})
      assert resp.status_code == 200
      # The capture buffer now holds the request.
      captured = (await client.get("/webhook-test/unmatched-path/last")).json()
      assert captured["body"] == {"hello": "world"}


  async def test_webhook_test_url_enforces_auth_when_listening(
      client: AsyncClient,
  ) -> None:
      """Auth is enforced on the test URL when a listen session is active and
      the workflow draft has authentication configured."""
      import base64

      workflow_id = (
          await client.post("/workflows", json={"name": "AuthedTest"})
      ).json()["id"]
      graph = _webhook_graph_with_auth(
          "test-auth-path",
          {
              "auth_type": "basic",
              "auth_username": "alice",
              "auth_password": "wonderland",
          },
      )
      await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

      await client.post("/webhook-test/test-auth-path/listen")

      # No auth → 401
      resp = await client.post("/webhook-test/test-auth-path", json={})
      assert resp.status_code == 401

      # Correct auth → 200
      token = base64.b64encode(b"alice:wonderland").decode("ascii")
      resp = await client.post(
          "/webhook-test/test-auth-path",
          headers={"Authorization": f"Basic {token}"},
          json={"order": 1},
      )
      assert resp.status_code == 200
  ```

- [ ] **Step 2: Run the new tests to confirm they pass**

  ```
  cd apps/api
  python -m pytest tests/test_triggers.py -x -q -k "listen_gate or test_url" 2>&1 | tail -20
  ```

  Expected: all 5 new tests pass.

- [ ] **Step 3: Run the full trigger test suite**

  ```
  cd apps/api
  python -m pytest tests/test_triggers.py -q 2>&1 | tail -20
  ```

  Expected: all tests pass.

---

## Task 6: Frontend — add `startListen` and `stopListen` to `api.ts`

**Files:**
- Modify: `apps/web/src/api.ts`

- [ ] **Step 1: Add the two new methods after `clearWebhook`**

  Find the `clearWebhook` method in `api.ts` (around line 579):
  ```typescript
  clearWebhook: (path: string) =>
    request<void>(`/webhook-test/${encodeURIComponent(path)}/last`, {
      method: "DELETE",
    }),
  ```

  Add immediately after it:
  ```typescript
  startListen: (path: string) =>
    request<{ listening: boolean; ttl_seconds: number }>(
      `/webhook-test/${encodeURIComponent(path)}/listen`,
      { method: "POST" },
    ),
  stopListen: (path: string) =>
    request<void>(`/webhook-test/${encodeURIComponent(path)}/listen`, {
      method: "DELETE",
    }),
  ```

---

## Task 7: Frontend — wire `EditorPage.tsx`

**Files:**
- Modify: `apps/web/src/EditorPage.tsx`

The editor has two webhook-listen paths: `startWebhookTestRun` (triggered by Execute when trigger is webhook) and `stopWebhookListen` (called on success, error, and unmount).

- [ ] **Step 1: Start the listen session in `startWebhookTestRun`**

  Find the `startWebhookTestRun` function (around line 885). It currently calls `api.clearWebhook(path)` and then sets `webhookListen`. Add `api.startListen(path)` right after the `clearWebhook` call:

  ```typescript
  async function startWebhookTestRun(
    node: GraphNode,
    targets?: string[],
    cache?: RunCache,
  ): Promise<void> {
    if (!id || webhookListen) return;
    const path = String(node.params.path ?? "").trim() || "noodle";
    const url = `${window.location.origin}/api/webhook-test/${path}`;
    const runTargets =
      targets?.length === 1 && targets[0] === node.id ? undefined : targets;

    setMessage("");
    setNodeOutput(node.id, undefined);
    try {
      await api.clearWebhook(path);
      await api.startListen(path);
    } catch (err) {
      setMessage(String(err));
      return;
    }

    setWebhookListen({ nodeId: node.id, path, url, targets: runTargets });
    // ... rest unchanged
  ```

- [ ] **Step 2: Stop the listen session in `stopWebhookListen`**

  Find `stopWebhookListen` (around line 520). Add a fire-and-forget `stopListen` call. The function needs to know which path was listening — read it from the current `webhookListen` state before clearing:

  ```typescript
  function stopWebhookListen(): void {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    // Fire-and-forget — stop is best-effort; the server TTL is the fallback.
    const current = webhookListen;
    if (current) {
      void api.stopListen(current.path).catch(() => undefined);
    }
    setWebhookListen(null);
  }
  ```

  Wait — `stopWebhookListen` is a plain function defined inside the component, so `webhookListen` is in its closure. But React state reads inside a non-useCallback closure can be stale. Use a ref to track the current path:

  Actually, looking at the component, `stopWebhookListen` already reads `timerRef.current` (a ref). Add a `listenPathRef` ref:

  ```typescript
  const listenPathRef = useRef<string | null>(null);
  ```

  Set it when starting:
  ```typescript
  // In startWebhookTestRun, after startListen succeeds:
  listenPathRef.current = path;
  setWebhookListen({ nodeId: node.id, path, url, targets: runTargets });
  ```

  Clear it in stop:
  ```typescript
  function stopWebhookListen(): void {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (listenPathRef.current) {
      void api.stopListen(listenPathRef.current).catch(() => undefined);
      listenPathRef.current = null;
    }
    setWebhookListen(null);
  }
  ```

---

## Task 8: Frontend — wire `NodeDetails.tsx` WebhookPanel

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

The `WebhookPanel` component has its own `listen()` and `stop()` functions that independently poll the test URL.

- [ ] **Step 1: Start the listen session in `listen()`**

  Find `WebhookPanel` (around line 2330) and its `listen` function (around line 2360). Add `api.startListen(slug)` after `api.clearWebhook(slug)`:

  ```typescript
  async function listen(): Promise<void> {
    if (listening) return;
    setError("");
    setReceived(false);
    if (nodeId) setNodeOutput(nodeId, undefined);
    try {
      await api.clearWebhook(slug);
      await api.startListen(slug);
    } catch (err) {
      setError(String(err));
      return;
    }
    setListening(true);
    timerRef.current = window.setInterval(async () => {
      // ... unchanged
    }, 1300);
  }
  ```

- [ ] **Step 2: Stop the listen session in `stop()`**

  The `stop` function currently just clears the timer and sets `listening` to false. Add a fire-and-forget `stopListen`:

  ```typescript
  function stop(): void {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    void api.stopListen(slug).catch(() => undefined);
    setListening(false);
  }
  ```

  The `slug` variable is in the outer `WebhookPanel` scope and is always current (it's derived from props, not state), so no ref needed here.

---

## Task 9: Manual smoke test

- [ ] **Step 1: Start the dev server**

  ```
  cd apps/web && npm run dev
  ```

- [ ] **Step 2: Open the editor on any webhook workflow**

  Verify the test URL shows in the node inspector.

- [ ] **Step 3: Send a Postman request to the test URL WITHOUT clicking Listen**

  Expected: `404 No active listen session…`

- [ ] **Step 4: Click "Listen for test event"**

  Expected: editor shows "Listening for a test event…" banner.

- [ ] **Step 5: Send the Postman request again**

  Expected: `200`, editor shows the captured request in the Output panel, run fires.

- [ ] **Step 6: Test auth — set `auth_type = basic` on the webhook node, save**

  Click Listen, then send from Postman WITHOUT auth headers.
  Expected: `401 Webhook authentication failed.`

  Send WITH correct Basic auth.
  Expected: `200`, run fires.

- [ ] **Step 7: Test production URL with unknown path**

  ```
  curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/api/webhook/no-such-path
  ```

  Expected: `404`

- [ ] **Step 8: Commit**

  ```
  git add apps/api/app/routers/webhooks.py \
          apps/api/tests/conftest.py \
          apps/api/tests/test_triggers.py \
          apps/web/src/api.ts \
          apps/web/src/EditorPage.tsx \
          apps/web/src/editor/NodeDetails.tsx
  git commit -m "feat(webhooks): listen-gate test URL, enforce auth, 404 for unmatched production paths"
  ```
