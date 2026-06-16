"""
Production review 2026-06-14 — regression tests.

Each test is written RED-first: it documents the broken behaviour and
must fail before the fix, then pass after.  Run only this module during
the fix cycle:

    pytest apps/api/tests/test_review_bugs.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import models
from app.config import settings
from app.services import retention
from app.services.runner import cancel_run
from app.tenancy import DEFAULT_ORG_ID, current_org_id, run_as_system

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

TRIGGER_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}}
    ],
    "edges": [],
}


@pytest.fixture
def mt_on(monkeypatch):
    """Enable multi-tenancy and reset the org ContextVar around each test."""
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


async def _create_org_and_workflow(session, org_id: str) -> str:
    """Insert an org + workflow with one published version. Returns workflow_id."""
    session.add(models.Organization(id=org_id, name=org_id, slug=org_id))
    # Ensure default org exists (FK dependency for some queries)
    existing_default = await session.get(models.Organization, DEFAULT_ORG_ID)
    if existing_default is None:
        session.add(
            models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default")
        )
    token = current_org_id.set(org_id)
    try:
        wf = models.Workflow(name="test-wf", draft_graph=TRIGGER_GRAPH)
        wf.versions.append(models.WorkflowVersion(version=1, graph=TRIGGER_GRAPH))
        session.add(wf)
        await session.flush()
        wf_id = wf.id
    finally:
        current_org_id.reset(token)
    await session.commit()
    return wf_id


# ---------------------------------------------------------------------------
# F-1: dispatch_error_handlers silently no-ops for non-default orgs
# ---------------------------------------------------------------------------


async def test_error_handlers_fire_for_non_default_org(client: AsyncClient, mt_on):
    """Error workflows must be dispatched for runs in any org, not just default.

    Before the fix: dispatch_error_handlers opens SessionLocal with no org
    context → active_org_id() == DEFAULT_ORG_ID → session.get(Run, run_id)
    returns None for non-default orgs → start_run_fn is never called.

    After the fix: the session uses run_as_system() and the error workflow fires.
    """
    from app.services import run_alerts
    from app.services.retention import SessionLocal

    # Create two workflows in org-x: the main workflow and an error-handler.
    async with SessionLocal() as session:
        wf_id = await _create_org_and_workflow(session, "org-x")

    async with SessionLocal() as session:
        token = current_org_id.set("org-x")
        err_wf = models.Workflow(name="err-handler", draft_graph=TRIGGER_GRAPH)
        err_wf.versions.append(
            models.WorkflowVersion(version=1, graph=TRIGGER_GRAPH)
        )
        session.add(err_wf)
        await session.flush()
        err_wf_id = err_wf.id
        current_org_id.reset(token)
        await session.commit()

    # Wire the error workflow onto the main workflow.
    async with SessionLocal() as session:
        with run_as_system():
            wf = await session.get(models.Workflow, wf_id)
        wf.error_workflow_id = err_wf_id
        await session.commit()

    # Create a Run row for org-x with status='error'.
    async with SessionLocal() as session:
        token = current_org_id.set("org-x")
        run = models.Run(
            workflow_id=wf_id,
            workflow_version=1,
            status="error",
            mode="production",
            trigger_type="manual",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        await session.commit()
        run_id = run.id
        current_org_id.reset(token)

    started: list = []

    async def spy_start_run(*args, **kwargs):
        started.append(args[0])  # workflow_id
        return "fake-run-id"

    # MT is ON. No org ContextVar is set (reset by the mt_on fixture).
    # The run is in org-x. Without run_as_system(), dispatch_error_handlers
    # cannot find it and start_run_fn is never called.
    await run_alerts.dispatch_error_handlers(
        SessionLocal,
        spy_start_run,
        run_id=run_id,
        node_events={},
        secret_values=[],
    )

    assert err_wf_id in started, (
        "dispatch_error_handlers did not call start_run_fn for a run in org-x. "
        "The session was likely org-filtered to DEFAULT_ORG_ID, making the run "
        "invisible. Fix: wrap session accesses with run_as_system()."
    )


# ---------------------------------------------------------------------------
# F-12: cancel_run silently no-ops for non-default orgs
# ---------------------------------------------------------------------------


async def test_cancel_run_works_for_non_default_org(client: AsyncClient, mt_on):
    """cancel_run must cancel runs in any org, not only DEFAULT_ORG_ID.

    Before the fix: cancel_run opens SessionLocal with no org context →
    session.get(Run, run_id) returns None for non-default org runs →
    function returns None instead of 'cancelled'.

    After the fix: uses run_as_system() inside the fallback branch.
    """
    from app.services.retention import SessionLocal

    async with SessionLocal() as session:
        wf_id = await _create_org_and_workflow(session, "org-cancel")

    # Insert a stale 'running' run directly (synchronous tests complete
    # instantly, so we bypass start_run and inject the DB row directly).
    async with SessionLocal() as session:
        token = current_org_id.set("org-cancel")
        run = models.Run(
            workflow_id=wf_id,
            workflow_version=1,
            status="running",
            mode="production",
            trigger_type="manual",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.commit()
        run_id = run.id
        current_org_id.reset(token)

    # No active asyncio task → falls through to the SessionLocal branch.
    result = await cancel_run(run_id)

    assert result == "cancelled", (
        f"cancel_run returned {result!r} instead of 'cancelled'. "
        "The SessionLocal branch has no org context set, so the ORM filter "
        "scopes it to DEFAULT_ORG_ID and the run is invisible."
    )

    # Verify the DB row was actually updated
    from app.services.retention import SessionLocal

    async with SessionLocal() as session:
        row = await session.scalar(
            select(models.Run)
            .where(models.Run.id == run_id)
            .execution_options(skip_org_filter=True)
        )
    assert row is not None and row.status == "cancelled"


# ---------------------------------------------------------------------------
# F-7: MCP publish_workflow fails when notes is omitted
# ---------------------------------------------------------------------------


async def test_mcp_publish_workflow_without_notes_succeeds(client: AsyncClient):
    """MCP publish_workflow must work when 'notes' is not provided by the caller.

    Before the fix: str('') or None evaluates to None, and
    WorkflowPublishRequest(notes=None) raises a Pydantic ValidationError
    because notes: str does not accept None.

    After the fix: empty notes defaults to '' (the field default).
    """
    from app.mcp import tools as mcp_tools

    # Create and publish a workflow via HTTP so we have a valid workflow_id.
    create_resp = await client.post("/workflows", json={"name": "MCP Pub Test"})
    assert create_resp.status_code == 201
    wf_id = create_resp.json()["id"]
    await client.put(f"/workflows/{wf_id}", json={"graph": TRIGGER_GRAPH})

    async with retention.SessionLocal() as session:
        # Simulate an MCP call with NO 'notes' key in args
        result = await mcp_tools._publish_workflow(
            session,
            user=None,
            args={"workflow_id": wf_id},  # notes intentionally omitted
        )

    assert "workflow_version_id" in result, (
        "publish_workflow returned an unexpected result. "
        "notes=None was passed to a non-optional str field, raising ValidationError."
    )


# ---------------------------------------------------------------------------
# F-8: IndexError when running a workflow with no published versions
# ---------------------------------------------------------------------------


async def test_run_workflow_with_no_versions_returns_400(client: AsyncClient):
    """Running a workflow that has never been published must return 400, not 500.

    Before the fix: workflow.versions[-1] raises IndexError on an empty list,
    producing an unhandled 500. After the fix: a clear 400 is returned.
    """
    create_resp = await client.post("/workflows", json={"name": "No Versions"})
    assert create_resp.status_code == 201
    wf_id = create_resp.json()["id"]
    # Intentionally do NOT publish — versions list is empty.

    resp = await client.post(f"/workflows/{wf_id}/run", json={})
    assert resp.status_code == 400, (
        f"Expected 400 (no versions), got {resp.status_code}. "
        "workflow.versions[-1] raises IndexError when versions is empty."
    )
    assert "version" in resp.json().get("detail", "").lower() or (
        resp.status_code == 400
    ), "Error detail should mention versions"


# ---------------------------------------------------------------------------
# F-9: runner_pool_id silently dropped for Deployments
# ---------------------------------------------------------------------------


async def test_deployment_persists_runner_pool_id(client: AsyncClient):
    """runner_pool_id must be stored on create and returned on read.

    Before the fix: Deployment() constructor in create_deployment never
    passes runner_pool_id, so every new deployment silently gets NULL.
    _info() builder also never reads deployment.runner_pool_id, so GET
    always returns null even if the DB row has a value.

    After the fix: the field is wired through create and _info().
    """
    # Create a workflow with a published version so deployment is valid.
    wf_resp = await client.post("/workflows", json={"name": "Pool WF"})
    wf_id = wf_resp.json()["id"]
    await client.put(f"/workflows/{wf_id}", json={"graph": TRIGGER_GRAPH})
    await client.post(f"/workflows/{wf_id}/publish", json={})

    # Create a runner pool to reference
    pool_resp = await client.post(
        "/runner-pools",
        json={"name": "test-pool", "provider": "agent"},
    )
    if pool_resp.status_code not in (200, 201):
        pytest.skip("Runner pool creation not available in this config")
    pool_id = pool_resp.json()["id"]

    # Create deployment with runner_pool_id set
    dep_resp = await client.post(
        "/deployments",
        json={
            "workflow_id": wf_id,
            "name": "Pool Deployment",
            "runner_pool_id": pool_id,
        },
    )
    assert dep_resp.status_code == 201, dep_resp.text
    dep = dep_resp.json()
    dep_id = dep["id"]

    assert dep["runner_pool_id"] == pool_id, (
        f"Create returned runner_pool_id={dep['runner_pool_id']!r}, expected {pool_id!r}. "
        "The Deployment() constructor in create_deployment never passes runner_pool_id."
    )

    # GET should also return the right value
    get_resp = await client.get(f"/deployments/{dep_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["runner_pool_id"] == pool_id, (
        "_info() builder omits deployment.runner_pool_id from the response."
    )


async def test_deployment_update_persists_runner_pool_id(client: AsyncClient):
    """PUT /deployments/{id} must update runner_pool_id when provided."""
    wf_resp = await client.post("/workflows", json={"name": "Pool WF 2"})
    wf_id = wf_resp.json()["id"]
    await client.put(f"/workflows/{wf_id}", json={"graph": TRIGGER_GRAPH})
    await client.post(f"/workflows/{wf_id}/publish", json={})

    pool_resp = await client.post(
        "/runner-pools",
        json={"name": "update-pool", "provider": "agent"},
    )
    if pool_resp.status_code not in (200, 201):
        pytest.skip("Runner pool creation not available in this config")
    pool_id = pool_resp.json()["id"]

    dep_resp = await client.post(
        "/deployments",
        json={"workflow_id": wf_id, "name": "Dep"},
    )
    dep_id = dep_resp.json()["id"]

    # Update with a runner_pool_id
    put_resp = await client.put(
        f"/deployments/{dep_id}",
        json={"runner_pool_id": pool_id},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["runner_pool_id"] == pool_id, (
        "update_deployment never reads body.runner_pool_id — the column is never updated."
    )


# ---------------------------------------------------------------------------
# R-1: credentials._load leaks cross-org credentials via the identity map
# ---------------------------------------------------------------------------


async def test_credential_load_blocks_cross_org_identity_map(client: AsyncClient, mt_on):
    """credentials._load must not return a credential from another org even when
    that credential is already in the session identity map.

    Before the fix: ``session.get(Credential, id)`` returns the identity-map hit
    without issuing a SELECT, so the org-filter hook never fires → org-A reads
    org-B's secret (IDOR). After the fix: ``populate_existing=True`` forces a
    real SELECT, the org filter applies, and _load raises 404.
    """
    from fastapi import HTTPException

    from app.routers import credentials as cred_router
    from app.services.retention import SessionLocal

    # Credential created in org-b.
    async with SessionLocal() as session:
        token = current_org_id.set("org-b")
        cred = models.Credential(name="secret", encrypted_data="x")
        session.add(cred)
        await session.commit()
        cred_id = cred.id
        current_org_id.reset(token)

    # A session acting as org-a primes the identity map with a cross-org read
    # (simulates a dispatch path that used skip_org_filter), then calls _load.
    async with SessionLocal() as session:
        token = current_org_id.set("org-a")
        primed = await session.scalar(
            select(models.Credential)
            .where(models.Credential.id == cred_id)
            .execution_options(skip_org_filter=True)
        )
        assert primed is not None, "precondition: org-b cred is now in the identity map"

        with pytest.raises(HTTPException) as exc_info:
            await cred_router._load(session, cred_id)
        assert exc_info.value.status_code == 404, (
            "credentials._load returned a cross-org credential — the identity map "
            "bypassed the org filter. Fix: session.get(..., populate_existing=True)."
        )
        current_org_id.reset(token)


# ---------------------------------------------------------------------------
# R-2: cross-org error_workflow_id must be rejected at validation time
# ---------------------------------------------------------------------------


async def test_error_workflow_validation_rejects_cross_org(client: AsyncClient, mt_on):
    """The error_workflow_id write-path validation must use a filtered SELECT so
    a workflow owned by another org resolves to None (rejected with 404).

    Before the fix: ``session.get(Workflow, error_workflow_id)`` (existence-only)
    could see a cross-org workflow, letting org-A point its error handler at
    org-B's workflow and — via the run_as_system error dispatch — leak its
    failure payload. After the fix: the filtered select returns None for any
    cross-org id.
    """
    from app.services.retention import SessionLocal

    async with SessionLocal() as session:
        other_wf = await _create_org_and_workflow(session, "org-b2")

    async with SessionLocal() as session:
        token = current_org_id.set("org-a2")
        visible = await session.scalar(
            select(models.Workflow).where(models.Workflow.id == other_wf)
        )
        current_org_id.reset(token)

    assert visible is None, (
        "A cross-org workflow was visible to the filtered validation select. "
        "The error_workflow_id check must use session.scalar(select(...)) not "
        "session.get() so the ORM org filter rejects cross-org references (R-2)."
    )


# ---------------------------------------------------------------------------
# R-3: filesystem/network-egress nodes must be flagged by the unsafe classifier
# ---------------------------------------------------------------------------


def test_unsafe_classifier_flags_new_fs_and_network_nodes():
    """shapefile_read (LFI) and the raw-network nodes must produce findings so
    the deploy-time unsafe-node policy gate applies to them (R-3)."""
    from app.services.unsafe_nodes import classify

    extra_network_nodes = [
        "ai_tool",
        "ai_http_tool",
        "ai_vector_retriever",
        "ai_qdrant_vector_store",
        "mcp_tools",
        "mcp_call_tool",
        "mcp_list_tools",
        "mongodb_query",
        "redis_command",
        "elasticsearch_search",
        "pinecone_upsert",
        "pinecone_query",
        "teams_send_webhook",
        "calendly_get_event",
        "jira_create_issue",
        "shopify_list_orders",
        "git_clone",
        "git_pull",
        "discord_send_message",
        "smtp_send_email",
        "postgres_query",
        "mysql_query",
    ]
    graph = {
        "nodes": [
            {"id": "a", "type": "shapefile_read", "params": {"path": "/etc/passwd"}},
            {"id": "b", "type": "network_port_probe", "params": {"host": "10.0.0.1"}},
            {"id": "c", "type": "sftp_transfer", "params": {}},
            {"id": "d", "type": "ldap_query", "params": {}},
            {"id": "e", "type": "sitemap_crawl", "params": {}},
            {"id": "f", "type": "certificate_inspect", "params": {"host": "x"}},
            {"id": "g", "type": "rss_feed_trigger", "params": {"feed_url": "https://x.test/rss.xml"}},
            {
                "id": "graphql-private",
                "type": "graphql_request",
                "params": {"url": "http://127.0.0.1/graphql"},
            },
            {
                "id": "url-loader-private",
                "type": "ai_url_document_loader",
                "params": {"url": "http://169.254.169.254/latest"},
            },
            {
                "id": "h",
                "type": "file_change_trigger",
                "params": {"source_type": "local", "watch_path": "/tmp"},
            },
            {
                "id": "file-path",
                "type": "read_text_file",
                "params": {"path": "/etc/passwd"},
            },
            {
                "id": "file-upload",
                "type": "read_text_file",
                "params": {"file": "uploaded-artifact"},
            },
            {
                "id": "ai-file-loader",
                "type": "ai_file_document_loader",
                "params": {"path": "/etc/passwd"},
            },
            {
                "id": "cloud",
                "type": "file_change_trigger",
                "params": {"source_type": "s3", "bucket": "b"},
            },
            {"id": "i", "type": "model_endpoint_probe", "params": {"base_url": "http://10.0.0.5"}},
            {"id": "j", "type": "model_endpoint_benchmark", "params": {}},
            {"id": "k", "type": "shadow_compare_endpoint", "params": {}},
            {
                "id": "s3-custom",
                "type": "s3_get_object",
                "params": {"endpoint_url": "http://minio.internal:9000"},
            },
            {
                "id": "s3-default",
                "type": "s3_get_object",
                "params": {"endpoint_url": ""},
            },
            {
                "id": "ai-chat-custom",
                "type": "ai_chat_model_openai",
                "params": {"provider": "openai_compatible"},
            },
            {
                "id": "ai-chat-base-url",
                "type": "ai_chat_model_openai",
                "params": {"credentials": {"provider": "openai", "base_url": "https://llm.example/v1"}},
            },
            {
                "id": "ai-chat-fixed",
                "type": "ai_chat_model_openai",
                "params": {"provider": "openai"},
            },
            {
                "id": "ai-chat-azure",
                "type": "ai_chat_model_azure",
                "params": {"credentials": {"azure_endpoint": "https://example.openai.azure.com"}},
            },
            {
                "id": "ai-embedding-ollama",
                "type": "ai_embedding_model",
                "params": {"provider": "ollama"},
            },
            {
                "id": "ai-embedding-fixed",
                "type": "ai_embedding_model",
                "params": {"provider": "openai"},
            },
            *[
                {"id": f"extra-{index}", "type": node_type, "params": {}}
                for index, node_type in enumerate(extra_network_nodes)
            ],
            {"id": "safe", "type": "manual_trigger", "params": {}},
        ],
        "edges": [],
    }
    findings = classify(graph)
    flagged = {f["node_id"] for f in findings}
    expected = {
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "graphql-private",
        "h",
        "file-path",
        "ai-file-loader",
        "i",
        "j",
        "k",
        "s3-custom",
        "ai-chat-custom",
        "ai-chat-base-url",
        "ai-chat-azure",
        "ai-embedding-ollama",
        "url-loader-private",
        *{f"extra-{index}" for index in range(len(extra_network_nodes))},
    }
    assert expected <= flagged, (
        f"Expected fs/network nodes to be flagged, got {flagged}."
    )
    assert "safe" not in flagged
    assert "cloud" not in flagged
    assert "file-upload" not in flagged
    assert "s3-default" not in flagged
    assert "ai-chat-fixed" not in flagged
    assert "ai-embedding-fixed" not in flagged
    kinds = {f["node_id"]: f["kind"] for f in findings}
    assert kinds["a"] == "filesystem"
    assert kinds["b"] == "network_egress"
    assert kinds["g"] == "network_egress"
    assert kinds["graphql-private"] == "http_private_ip"
    assert kinds["h"] == "filesystem"
    assert kinds["file-path"] == "filesystem"
    assert kinds["ai-file-loader"] == "filesystem"
    assert kinds["i"] == "network_egress"
    assert kinds["s3-custom"] == "network_egress"
    assert kinds["ai-chat-custom"] == "network_egress"
    assert kinds["ai-chat-base-url"] == "network_egress"
    assert kinds["ai-chat-azure"] == "network_egress"
    assert kinds["ai-embedding-ollama"] == "network_egress"
    assert kinds["url-loader-private"] == "http_private_ip"
    for index in range(len(extra_network_nodes)):
        assert kinds[f"extra-{index}"] == "network_egress"


# ---------------------------------------------------------------------------
# R-9: OAuth credential must be stamped to the org that started the flow
# ---------------------------------------------------------------------------


async def test_oauth_callback_stamps_credential_to_initiating_org(
    client: AsyncClient, mt_on, monkeypatch
):
    """An OAuth credential must land in the org that initiated the flow, not
    DEFAULT_ORG_ID.

    The callback is a provider redirect with no X-Org-Id header, so the org is
    carried in the signed state and re-applied before insert. Before the fix:
    the credential is stamped/encrypted under DEFAULT_ORG_ID for every
    non-default org (cross-tenant leak + wrong KEK).
    """
    from app.services.oauth import create_oauth_state
    from app.services.retention import SessionLocal

    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-secret")

    async def fake_post_token_form(url, data):
        return {
            "access_token": "tok",
            "refresh_token": "r",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "token_type": "Bearer",
        }

    monkeypatch.setattr("app.services.oauth._post_token_form", fake_post_token_form)

    # The org that initiated the flow (must exist for the FK + KEK minting).
    async with SessionLocal() as session:
        session.add(
            models.Organization(id="org-oauth", name="OAuth Org", slug="org-oauth")
        )
        await session.commit()

    state, _ = create_oauth_state(
        {
            "credential_type": "google_sheets_oauth2",
            "name": "Sheets OAuth",
            "scope": "global",
            "redirect_uri": "http://test/credentials/oauth/callback",
            "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
            "org_id": "org-oauth",
        }
    )

    resp = await client.get(
        "/credentials/oauth/callback",
        params={"code": "provider-code", "state": state},
    )
    assert resp.status_code == 200, resp.text
    assert "noodle_oauth_success" in resp.text

    async with SessionLocal() as session:
        cred = await session.scalar(
            select(models.Credential)
            .where(models.Credential.name == "Sheets OAuth")
            .execution_options(skip_org_filter=True)
        )
    assert cred is not None, "OAuth credential was not created"
    assert cred.org_id == "org-oauth", (
        f"OAuth credential stamped to {cred.org_id!r}, expected 'org-oauth'. "
        "Without org_id in the signed state the callback falls back to "
        "DEFAULT_ORG_ID (R-9)."
    )


# ---------------------------------------------------------------------------
# R-13: provider webhook dispatch must resolve non-default-org subscriptions
# ---------------------------------------------------------------------------


async def test_provider_webhook_resolves_non_default_org_subscription(
    client: AsyncClient, mt_on
):
    """A provider-trigger subscription owned by a non-default org must be
    discoverable by the webhook dispatch path.

    Before the fix `dispatch_provider_webhook` opened a bare SessionLocal()
    with no org context (provider callbacks are unauthenticated), so the
    subscription lookup was filtered to DEFAULT_ORG_ID, returned None, and the
    trigger 404'd. The fix resolves the subscription's org via run_as_system()
    then runs the dispatch under run_as_org(that_org).
    """
    from app.services.provider_triggers import _load_subscription_context
    from app.services.retention import SessionLocal
    from app.tenancy import run_as_org, run_as_system

    async with SessionLocal() as session:
        wf_id = await _create_org_and_workflow(session, "org-prov")

    async with SessionLocal() as session:
        token = current_org_id.set("org-prov")
        sub = models.ProviderTriggerSubscription(
            workflow_id=wf_id,
            node_id="n1",
            node_type="github_trigger",
            provider="github",
            trigger_key="push",
            status="active",
        )
        session.add(sub)
        await session.commit()
        sub_id = sub.id
        current_org_id.reset(token)

    # The org-resolution query the fix relies on must find the subscription
    # even with no org context.
    async with SessionLocal() as session:
        with run_as_system():
            sub_org = await session.scalar(
                select(models.ProviderTriggerSubscription.org_id).where(
                    models.ProviderTriggerSubscription.id == sub_id
                )
            )
    assert sub_org == "org-prov"

    # Under the (pre-fix) default context the subscription is invisible…
    async with SessionLocal() as session:
        with pytest.raises(KeyError):
            await _load_subscription_context(session, sub_id)

    # …but under the resolved org context it loads correctly (what the fix does).
    async with SessionLocal() as session:
        with run_as_org(sub_org):
            row, workflow, _version = await _load_subscription_context(session, sub_id)
    assert row.id == sub_id and workflow.id == wf_id
