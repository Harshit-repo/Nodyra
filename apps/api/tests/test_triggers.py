from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.models import ScheduleState
from app.services import triggers


def _webhook_graph(path: str) -> dict:
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"path": path, "http_method": "POST"},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "proc",
                "type": "code",
                "params": {"code": "output = input['body']"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "hook",
                "source_output": "main",
                "target": "proc",
                "target_input": "input",
            }
        ],
    }


async def test_webhook_triggers_active_workflow(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Hooked"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph("orders"), "active": True},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = (await client.post("/webhook/orders", json={"order": 42})).json()
    assert len(response["runs"]) == 1

    run = (await client.get(f"/runs/{response['runs'][0]}")).json()
    assert run["status"] == "success"
    assert run["trigger_type"] == "webhook"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["proc"]["output"]["main"] == {"order": 42}


async def test_webhook_with_no_active_workflow(client: AsyncClient) -> None:
    response = (await client.post("/webhook/unknown", json={})).json()
    assert response["runs"] == []


async def test_inactive_workflow_is_not_triggered(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Off"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph("idle"), "active": False},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = (await client.post("/webhook/idle", json={})).json()
    assert response["runs"] == []


async def test_schedule_tick_fires_when_due(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Scheduled"})
    ).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {"interval": "minutes", "every": 1},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # first sighting starts the clock, no run
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

    # Rewind the persisted last_fired so the schedule is overdue.
    async with triggers.SessionLocal() as session:
        state = (
            await session.scalars(
                select(ScheduleState).where(
                    ScheduleState.workflow_id == workflow_id
                )
            )
        ).one()
        state.last_fired = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    await triggers._tick()  # now overdue — should fire

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"


async def test_schedule_tick_honours_cron(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Cron"})
    ).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {"cron": "* * * * *"},  # every minute
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # start the clock
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

    async with triggers.SessionLocal() as session:
        state = (
            await session.scalars(
                select(ScheduleState).where(
                    ScheduleState.workflow_id == workflow_id
                )
            )
        ).one()
        state.last_fired = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()

    await triggers._tick()  # a cron minute has elapsed — should fire
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"


def test_is_due_unknown_timezone_does_not_fire() -> None:
    """An invalid IANA name must not silently fall back to UTC."""
    triggers._logged_bad_tz.clear()
    last = datetime.now(UTC) - timedelta(hours=5)
    now = datetime.now(UTC)
    params = {"cron": "* * * * *", "tz": "Mars/Olympus_Mons"}
    assert triggers._is_due(params, last, now) is False
    # And the warning is flood-controlled — only the first invalid hit logs.
    assert "Mars/Olympus_Mons" in triggers._logged_bad_tz


# --- Webhook auth (Slice 21) -------------------------------------------------


def _webhook_graph_with_auth(path: str, auth_params: dict) -> dict:
    graph = _webhook_graph(path)
    graph["nodes"][0]["params"].update(auth_params)
    return graph


async def test_webhook_basic_auth_rejects_missing_credentials(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Basic"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "secured",
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = await client.post("/webhook/secured", json={})
    assert response.status_code == 401


async def test_webhook_basic_auth_accepts_valid_credentials(
    client: AsyncClient,
) -> None:
    import base64

    workflow_id = (
        await client.post("/workflows", json={"name": "Basic2"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "secured2",
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    token = base64.b64encode(b"alice:wonderland").decode("ascii")
    response = await client.post(
        "/webhook/secured2",
        headers={"Authorization": f"Basic {token}"},
        json={"order": 1},
    )
    body = response.json()
    assert response.status_code == 200
    assert len(body["runs"]) == 1


async def test_webhook_header_auth_checks_value(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Header"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "header-auth",
        {
            "auth_type": "header",
            "auth_header_name": "X-API-Key",
            "auth_header_value": "supersecret",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post(
        "/webhook/header-auth",
        headers={"X-API-Key": "nope"},
        json={},
    )
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/header-auth",
        headers={"X-API-Key": "supersecret"},
        json={},
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_query_auth_checks_value(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Query"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "query-auth",
        {
            "auth_type": "query",
            "auth_query_name": "token",
            "auth_query_value": "tokentokentoken",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post("/webhook/query-auth?token=wrong", json={})
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/query-auth?token=tokentokentoken", json={}
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_bearer_auth_checks_token(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Bearer"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "bearer-auth",
        {"auth_type": "bearer", "auth_bearer_token": "tok-abc-123"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post(
        "/webhook/bearer-auth", headers={"Authorization": "Bearer nope"}, json={}
    )
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/bearer-auth",
        headers={"Authorization": "Bearer tok-abc-123"},
        json={},
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


def _make_hs256_jwt(payload: dict, secret: str) -> str:
    import base64
    import hashlib
    import hmac
    import json as _json

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(_json.dumps(payload).encode())
    signing_input = f"{header}.{body}".encode()
    sig = b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


async def test_webhook_jwt_auth_verifies_hs256_and_exp(client: AsyncClient) -> None:
    import time

    secret = "jwt-shared-secret"
    workflow_id = (
        await client.post("/workflows", json={"name": "JWT"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "jwt-auth", {"auth_type": "jwt", "auth_jwt_secret": secret}
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    good = _make_hs256_jwt({"sub": "abc", "exp": int(time.time()) + 3600}, secret)
    forged = _make_hs256_jwt({"sub": "abc", "exp": int(time.time()) + 3600}, "wrong")
    expired = _make_hs256_jwt({"sub": "abc", "exp": int(time.time()) - 10}, secret)

    bad_sig = await client.post(
        "/webhook/jwt-auth", headers={"Authorization": f"Bearer {forged}"}, json={}
    )
    assert bad_sig.status_code == 401
    stale = await client.post(
        "/webhook/jwt-auth", headers={"Authorization": f"Bearer {expired}"}, json={}
    )
    assert stale.status_code == 401
    ok = await client.post(
        "/webhook/jwt-auth", headers={"Authorization": f"Bearer {good}"}, json={}
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_hmac_verification_checks_signature(
    client: AsyncClient,
) -> None:
    import hashlib
    import hmac

    secret = "whsec_test"
    workflow_id = (
        await client.post("/workflows", json={"name": "HMAC"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "hmac-hook",
        {
            "auth_type": "none",
            "hmac_verification": "on",
            "hmac_header": "X-Signature",
            "hmac_secret": secret,
            "hmac_algorithm": "sha256",
            "hmac_prefix": "sha256=",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    body = b'{"order": 42}'
    good_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    bad = await client.post(
        "/webhook/hmac-hook",
        headers={"X-Signature": "sha256=deadbeef", "Content-Type": "application/json"},
        content=body,
    )
    assert bad.status_code == 401
    ok = await client.post(
        "/webhook/hmac-hook",
        headers={"X-Signature": good_sig, "Content-Type": "application/json"},
        content=body,
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_ip_allowlist_rejects_outside_caller(
    client: AsyncClient,
) -> None:
    """A non-empty ip_allowlist rejects callers outside it with 403.

    The test transport's socket peer is 127.0.0.1, which is outside 10.0.0.0/8.
    """
    workflow_id = (
        await client.post("/workflows", json={"name": "IPDeny"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "ip-deny", {"ip_allowlist": "10.0.0.0/8"}
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/ip-deny", json={})
    assert resp.status_code == 403


async def test_webhook_ip_allowlist_accepts_listed_caller(
    client: AsyncClient,
) -> None:
    """A caller whose IP is inside the allowlist runs the workflow."""
    workflow_id = (
        await client.post("/workflows", json={"name": "IPAllow"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "ip-allow", {"ip_allowlist": "127.0.0.0/8, 10.0.0.0/8"}
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/ip-allow", json={"ok": 1})
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 1


async def test_webhook_ip_allowlist_honours_xff_only_when_trusted(
    client: AsyncClient,
) -> None:
    """X-Forwarded-For is consulted only when trust_proxy is on.

    With trust_proxy off (default) the spoofed XFF is ignored and the socket
    peer (127.0.0.1) decides — outside 203.0.113.0/24 → 403. With trust_proxy
    on, the forwarded client IP is honoured → 200.
    """
    untrusted_wf = (
        await client.post("/workflows", json={"name": "XFFUntrusted"})
    ).json()["id"]
    await client.put(
        f"/workflows/{untrusted_wf}",
        json={
            "graph": _webhook_graph_with_auth(
                "xff-untrusted", {"ip_allowlist": "203.0.113.0/24"}
            ),
            "active": True,
        },
    )
    await client.post(f"/workflows/{untrusted_wf}/publish", json={})
    spoofed = await client.post(
        "/webhook/xff-untrusted",
        headers={"X-Forwarded-For": "203.0.113.9"},
        json={},
    )
    assert spoofed.status_code == 403

    trusted_wf = (
        await client.post("/workflows", json={"name": "XFFTrusted"})
    ).json()["id"]
    await client.put(
        f"/workflows/{trusted_wf}",
        json={
            "graph": _webhook_graph_with_auth(
                "xff-trusted",
                {"ip_allowlist": "203.0.113.0/24", "trust_proxy": "on"},
            ),
            "active": True,
        },
    )
    await client.post(f"/workflows/{trusted_wf}/publish", json={})
    forwarded = await client.post(
        "/webhook/xff-trusted",
        headers={"X-Forwarded-For": "203.0.113.9, 10.1.1.1"},
        json={"ok": 1},
    )
    assert forwarded.status_code == 200
    assert len(forwarded.json()["runs"]) == 1


async def test_webhook_dedup_acks_repeat_without_second_run(
    client: AsyncClient,
) -> None:
    """With dedup on, a repeated key acks 200 but starts no second run.

    A distinct key runs normally; a repeat is idempotently dropped (200, no
    run) rather than rejected (401).
    """
    workflow_id = (
        await client.post("/workflows", json={"name": "Dedup"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "dedup-hook",
        {"dedup": "on", "dedup_key": "{{ $json.body['id'] }}"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    first = await client.post("/webhook/dedup-hook", json={"id": "evt-1"})
    assert first.status_code == 200
    assert len(first.json()["runs"]) == 1

    repeat = await client.post("/webhook/dedup-hook", json={"id": "evt-1"})
    assert repeat.status_code == 200
    assert repeat.json()["runs"] == []

    other = await client.post("/webhook/dedup-hook", json={"id": "evt-2"})
    assert other.status_code == 200
    assert len(other.json()["runs"]) == 1


async def test_webhook_response_data_no_body(client: AsyncClient) -> None:
    """response_data='No Body' returns the configured code and an empty body."""
    workflow_id = (
        await client.post("/workflows", json={"name": "NoBody"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "no-body",
        {"response_mode": "On Received", "response_data": "No Body",
         "response_code": 202},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/no-body", json={"x": 1})
    assert resp.status_code == 202
    assert resp.content == b""


async def test_webhook_response_data_all_entries_echoes_body(
    client: AsyncClient,
) -> None:
    """response_data='All Entries' echoes the received body verbatim."""
    workflow_id = (
        await client.post("/workflows", json={"name": "AllEntries"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "all-entries",
        {"response_mode": "On Received", "response_data": "All Entries"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    body = [{"id": "A1"}, {"id": "B7"}]
    resp = await client.post("/webhook/all-entries", json=body)
    assert resp.status_code == 200
    assert resp.json() == body


async def test_webhook_response_data_first_entry_json(
    client: AsyncClient,
) -> None:
    """response_data='First Entry JSON' returns the first item of the body."""
    workflow_id = (
        await client.post("/workflows", json={"name": "FirstEntry"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "first-entry",
        {"response_mode": "On Received", "response_data": "First Entry JSON"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post(
        "/webhook/first-entry", json=[{"id": "A1"}, {"id": "B7"}]
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": "A1"}


async def test_webhook_response_data_custom_body_and_headers(
    client: AsyncClient,
) -> None:
    """response_data='Custom' evaluates response_body + response_headers."""
    workflow_id = (
        await client.post("/workflows", json={"name": "Custom"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "custom-resp",
        {
            "response_mode": "On Received",
            "response_data": "Custom",
            "response_body": "{{ $json.body['name'] }}",
            "response_headers": {"X-Foo": "bar"},
            "response_code": 201,
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/custom-resp", json={"name": "alice"})
    assert resp.status_code == 201
    assert resp.json() == "alice"
    assert resp.headers.get("X-Foo") == "bar"


async def test_webhook_unknown_path_still_returns_200(client: AsyncClient) -> None:
    """Unknown paths return 200 with empty runs (existing behaviour).

    Only path matches that *exist but fail auth* return 401.
    """
    response = await client.post("/webhook/nobody-listens", json={})
    assert response.status_code == 200
    assert response.json()["runs"] == []


def test_is_due_different_timezones_fire_at_different_utc() -> None:
    """Same cron expression resolves to different UTC fire times per tz.

    Cron '0 9 * * *' fires daily at 09:00 local. NY (UTC-4 in May DST) fires
    at 13:00 UTC; Sydney (UTC+10) fires at 23:00 UTC. Anchor ``last`` and
    ``now`` so NY has crossed today's 09:00 but Sydney hasn't yet.
    """
    last = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)  # 08:00 NY / 22:00 Sydney
    now = datetime(2026, 5, 26, 14, 0, tzinfo=UTC)  # past 13:00 NY only

    sydney_due = triggers._is_due(
        {"cron": "0 9 * * *", "tz": "Australia/Sydney"}, last, now
    )
    ny_due = triggers._is_due(
        {"cron": "0 9 * * *", "tz": "America/New_York"}, last, now
    )
    assert ny_due is True
    assert sydney_due is False


# --- Task 8: webhook ingress role split --------------------------------------

def test_webhook_role_defaults_to_ingress() -> None:
    """The production-grade ingress posture is the default."""
    from app.config import Settings

    assert Settings().webhook_role == "ingress"


async def test_webhook_role_default_serves_production_path(client: AsyncClient) -> None:
    """Default role — production /webhook/* is reachable (not 404)."""
    resp = await client.post("/webhook/no-such-path", json={})
    # 200 is the "accepted, no matching trigger" reply; we just need NOT 404.
    assert resp.status_code != 404


async def test_webhook_role_disabled_blocks_production_but_keeps_test_paths(
    monkeypatch,
) -> None:
    """webhook_role=='disabled' — production /webhook/{path} is unmounted, but
    the editor capture paths /webhook-test/* stay available so the builder UX
    works on a control-plane-only replica."""
    import importlib

    from app import config as _config
    from app.config import Settings

    new_settings = Settings(webhook_role="disabled")
    monkeypatch.setattr(_config, "settings", new_settings)

    import app.main as _main
    reloaded = importlib.reload(_main)
    paths = {getattr(r, "path", "") for r in reloaded.app.routes}
    assert "/webhook/{path}" not in paths, (
        "production /webhook/{path} must not be mounted when role=disabled"
    )
    assert any(p.startswith("/webhook-test") for p in paths), (
        "editor /webhook-test/* paths must stay mounted when role=disabled"
    )

    # Restore default behaviour for subsequent tests in the session.
    monkeypatch.setattr(_config, "settings", Settings())
    importlib.reload(_main)


