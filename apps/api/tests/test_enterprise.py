from httpx import AsyncClient


async def test_register_login_and_me(client: AsyncClient) -> None:
    registered = (
        await client.post(
            "/auth/register",
            json={
                "name": "Dev User",
                "company": "Nodyra Labs",
                "email": "dev@nodyra.test",
                "password": "supersecret",
            },
        )
    ).json()
    assert registered["user"]["email"] == "dev@nodyra.test"
    assert registered["user"]["name"] == "Dev User"
    assert registered["user"]["company"] == "Nodyra Labs"
    assert registered["user"]["role"] == "owner"
    token = registered["token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "dev@nodyra.test"

    login = await client.post(
        "/auth/login",
        json={"email": "dev@nodyra.test", "password": "supersecret"},
    )
    assert login.status_code == 200


async def test_login_rejects_bad_password(client: AsyncClient) -> None:
    await client.post(
        "/auth/register",
        json={"email": "a@b.test", "password": "password123"},
    )
    resp = await client.post(
        "/auth/login", json={"email": "a@b.test", "password": "wrong-password"}
    )
    assert resp.status_code == 401


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_users_me_alias(client: AsyncClient) -> None:
    """``/users/me`` is a REST-conventional alias for ``/auth/me`` (QA fix)."""
    registered = (
        await client.post(
            "/auth/register",
            json={"email": "alias@nodyra.test", "password": "supersecret"},
        )
    ).json()
    token = registered["token"]
    resp = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "alias@nodyra.test"


async def test_login_rate_limited(client: AsyncClient) -> None:
    """The login endpoint blocks brute-force after auth_rate_limit_per_minute
    failures from the same client IP (QA fix)."""
    from app.config import settings as app_settings
    from app.services import rate_limit

    rate_limit.reset()
    app_settings.auth_rate_limit_enabled = True
    app_settings.auth_rate_limit_per_minute = 3
    try:
        for _ in range(3):
            resp = await client.post(
                "/auth/login",
                json={"email": "nope@nodyra.test", "password": "wrong"},
            )
            assert resp.status_code == 401
        resp = await client.post(
            "/auth/login",
            json={"email": "nope@nodyra.test", "password": "wrong"},
        )
        assert resp.status_code == 429
        assert "Too many" in resp.json()["detail"]
    finally:
        app_settings.auth_rate_limit_per_minute = 10
        rate_limit.reset()


async def test_credentials_never_expose_secret_values(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/credentials",
            json={
                "name": "GitHub Token",
                "type": "apiKey",
                "data": {"token": "ghp_supersecret"},
            },
        )
    ).json()
    assert created["keys"] == ["token"]
    assert "ghp_supersecret" not in str(created)

    listed = (await client.get("/credentials")).json()
    assert "ghp_supersecret" not in str(listed)


async def test_credential_delete(client: AsyncClient) -> None:
    cred_id = (
        await client.post(
            "/credentials", json={"name": "Temp", "data": {"k": "v"}}
        )
    ).json()["id"]
    assert (await client.delete(f"/credentials/{cred_id}")).status_code == 204


async def test_auth_required_endpoint(client: AsyncClient) -> None:
    initial = (await client.get("/auth/required")).json()
    assert initial["auth_required"] is False
    assert initial["signed_in"] is False
    assert initial["registration_open"] is True

    registered = (
        await client.post(
            "/auth/register",
            json={"email": "user@nodyra.test", "password": "supersecret"},
        )
    ).json()
    info = (
        await client.get(
            "/auth/required",
            headers={"Authorization": f"Bearer {registered['token']}"},
        )
    ).json()
    assert info["signed_in"] is True
    assert info["user"]["email"] == "user@nodyra.test"


async def test_auth_gating_blocks_when_required(client: AsyncClient) -> None:
    from app.config import settings as app_settings

    app_settings.auth_required = True
    try:
        # /auth and /health are still reachable so the user can sign in.
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/auth/required")).status_code == 200

        # Other endpoints reject anonymous requests.
        assert (await client.get("/workflows")).status_code == 401

        registered = (
            await client.post(
                "/auth/register",
                json={"email": "gate@nodyra.test", "password": "supersecret"},
            )
        ).json()
        headers = {"Authorization": f"Bearer {registered['token']}"}
        assert (
            await client.get("/workflows", headers=headers)
        ).status_code == 200

        # Bad token still fails.
        bad = await client.get(
            "/workflows", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert bad.status_code == 401
    finally:
        app_settings.auth_required = False


async def test_registration_closes_after_first_user(client: AsyncClient) -> None:
    first = await client.post(
        "/auth/register",
        json={
            "name": "Workspace Admin",
            "company": "Nodyra Labs",
            "email": "owner@nodyra.test",
            "password": "supersecret",
        },
    )
    assert first.status_code == 201
    assert first.json()["user"]["role"] == "owner"

    second = await client.post(
        "/auth/register",
        json={"email": "second@nodyra.test", "password": "supersecret"},
    )
    assert second.status_code == 403

    state = (await client.get("/auth/required")).json()
    assert state["registration_open"] is False


async def test_rbac_blocks_viewer_mutations_and_admin_only_secrets(
    client: AsyncClient,
) -> None:
    from app.config import settings as app_settings

    app_settings.auth_required = True
    try:
        owner = (
            await client.post(
                "/auth/register",
                json={
                    "name": "Workspace Admin",
                    "company": "Nodyra Labs",
                    "email": "owner2@nodyra.test",
                    "password": "supersecret",
                },
            )
        ).json()
        assert owner["user"]["role"] == "owner"
        owner_headers = {"Authorization": f"Bearer {owner['token']}"}

        viewer = (
            await client.post(
                "/auth/users",
                headers=owner_headers,
                json={
                    "name": "Viewer User",
                    "company": "Nodyra Labs",
                    "email": "viewer@nodyra.test",
                    "password": "supersecret",
                    "role": "viewer",
                },
            )
        ).json()
        editor = (
            await client.post(
                "/auth/users",
                headers=owner_headers,
                json={
                    "name": "Editor User",
                    "email": "editor@nodyra.test",
                    "password": "supersecret",
                    "role": "editor",
                },
            )
        ).json()
        assert viewer["role"] == "viewer"
        assert viewer["name"] == "Viewer User"
        assert viewer["company"] == "Nodyra Labs"
        assert editor["role"] == "editor"

        viewer_login = (
            await client.post(
                "/auth/login",
                json={"email": "viewer@nodyra.test", "password": "supersecret"},
            )
        ).json()
        editor_login = (
            await client.post(
                "/auth/login",
                json={"email": "editor@nodyra.test", "password": "supersecret"},
            )
        ).json()
        viewer_headers = {"Authorization": f"Bearer {viewer_login['token']}"}
        editor_headers = {"Authorization": f"Bearer {editor_login['token']}"}

        denied = await client.post(
            "/workflows", headers=viewer_headers, json={"name": "Nope"}
        )
        assert denied.status_code == 403

        created = await client.post(
            "/workflows", headers=editor_headers, json={"name": "Allowed"}
        )
        assert created.status_code == 201

        secret_denied = await client.post(
            "/credentials",
            headers=editor_headers,
            json={"name": "Token", "data": {"token": "secret"}},
        )
        assert secret_denied.status_code == 403

        secret_created = await client.post(
            "/credentials",
            headers=owner_headers,
            json={"name": "Token", "data": {"token": "secret"}},
        )
        assert secret_created.status_code == 201
    finally:
        app_settings.auth_required = False


async def test_audit_log_records_actions(client: AsyncClient) -> None:
    await client.post("/workflows", json={"name": "Audited Flow"})
    await client.post("/credentials", json={"name": "Audited Cred", "data": {}})

    events = (await client.get("/audit")).json()["items"]
    actions = {(e["action"], e["target_type"]) for e in events}
    assert ("create", "workflow") in actions
    assert ("create", "credential") in actions
